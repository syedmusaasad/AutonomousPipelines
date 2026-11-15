"""Model seat changes happen ONLY through staged head-to-head trials with rubrics.

`pipeline trial <role> <model@effort>...  --tasks tasks.md --rubric rubric.md`
  1. Parses tasks.md (one task per `## ` heading) and runs each task for each arm
     (incumbent + all candidates), blinded as A, B, C...; the mapping {A,B,...} ->
     {incumbent,candidate,...} is chosen randomly and saved to `trial/mapping.json`
     only: the plan text, which every phase brief shares, names no model. Task phases
     write to `trial/task<i>/<arm>/`.
  2. Dispatches a sealed reviewer (the first whose family no arm uses) to score each arm against the
     rubric, writing `scores.json` ({task: {A: n, B: n, ...}}). When no reviewer is outside
     the arms' families (a mixed Claude/GPT trial), BOTH reviewers score every arm, each in its
     own phase, saving `scores-reviewer-a.json` and `scores-reviewer-b.json` (sealed seats cannot
     write: the phase's CAPTURE: has the engine save their JSON reply); decide() averages
     them per task and arm and records each reviewer's mean. The scorers see only A/B/C
     labels — no model names, no incumbent/candidate.
  3. Writes a verdict file via decide() + select(). The selection rule: keep arms
     whose mean rubric score is within the role's quality band of the best arm,
     then rank by wall clock, then cost. apply() changes the registry only when
     the verdict's chosen arm is the one being applied.

Arms are `model@effort` strings (e.g. `gpt-5.6-luna@low`). The effort part is
optional; if absent it defaults to the role's registry effort. `parse_arm(s)`
splits the string into `(model, effort)` with that default behaviour.

Vendor benchmarks nominate candidates; the trial decides."""

import json
import random
from pathlib import Path

from . import roles as roles_mod
from .util import now_iso

TOLERANCE = 0.10  # legacy: 10% regression on wall or tokens is the most a quality win may cost

BANDS = {"critical": 0.5, "standard": 1.5, "tolerant": 2.5}

_ARM_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Every arm brief carries this. Role prompts and the worker contract tell workers to commit
# their deliverable; a trial's output is scratch material and must never reach a branch.
NO_COMMIT_NOTICE = (
    "**Do NOT commit trial output.** Run no `git add`, `git commit`, `git push`, `git stash` or any other",
    "git command that writes: leave your files uncommitted on disk. This overrides any instruction in your",
    "role prompt or the worker contract to commit your deliverable or to report a commit hash: skip that",
    "step, and name the deliverable's path in your final message instead.",
)


class TrialError(ValueError):
    """A trial's inputs are unusable (e.g. a missing second reviewer's scores)."""


def scores_file(reviewer: str = None) -> str:
    """File name a scorer writes: `scores.json` (single scorer) or `scores-<reviewer>.json`."""
    return "scores.json" if reviewer is None else f"scores-{reviewer}.json"


def parse_arm(s: str, role: str = None, reg: dict = None) -> tuple:
    """Split an arm string 'model@effort' into (model, effort).

    If no '@' is present, effort defaults to the role's registry effort (if
    role and reg are provided), otherwise None.
    """
    if "@" in s:
        model, effort = s.rsplit("@", 1)
        return model, effort
    # No effort specified: use the role's registry effort if available
    if role is not None and reg is not None:
        effort = reg.get("roles", {}).get(role, {}).get("effort")
    else:
        effort = None
    return s, effort


def parse_tasks(text: str) -> list:
    tasks, cur, buf = [], None, []
    for ln in text.splitlines():
        if ln.startswith("## "):
            if cur:
                tasks.append((cur, "\n".join(buf).strip()))
            cur, buf = ln[3:].strip(), []
        elif cur:
            buf.append(ln)
    if cur:
        tasks.append((cur, "\n".join(buf).strip()))
    return tasks


def _demote_headings(text: str) -> str:
    """Make text safe to embed in a plan: a line starting with `## ` would be read by the plan
    parser as a phase heading, so it becomes a bold line instead."""
    return "\n".join(f"**{ln[3:].strip()}**" if ln.startswith("## ") else ln for ln in text.splitlines())


def trial_plan(role: str, candidates, tasks: list, rubric: str, workdir: Path, reg: dict, repeats: dict = None,
               arm_map_out: dict = None) -> str:
    """A trial is itself a plan: arms (incumbent + all candidates), blinded as A, B, C...;
    each task x arm x repeat is one phase with MODEL: and EFFORT: pinned; mapping.json saved in the
    trial dir; a single scorer phase on a family not used by any arm.

    candidates: a single arm string (model or model@effort) or a list of arm strings.
    repeats: {arm_label: n} or None (defaults to 1 per arm).
    arm_map_out: when given, filled with the label -> "model_q@effort" mapping. The plan text carries
    no mapping, model names or incumbent/candidate roles: every phase brief shares the plan preamble,
    and the scorers must see only the labels. The caller saves the mapping to trial/mapping.json.
    """
    if isinstance(candidates, str):
        candidates = [candidates]

    seat = roles_mod.seat(role, reg)
    incumbent_effort = seat["effort"]

    # Parse each candidate arm string into (model, effort)
    # Incumbent arm uses the seat's model and effort
    incumbent_arm_str = f"{seat['model_q']}@{incumbent_effort}"
    # Build arm_models as list of (model_q, effort) tuples
    arm_list = [(seat["model_q"], incumbent_effort)]  # incumbent
    for c in candidates:
        cmodel, ceffort = parse_arm(c, role=role, reg=reg)
        cmodel_q = roles_mod.qualified(cmodel, reg)
        if ceffort is None:
            ceffort = incumbent_effort
        arm_list.append((cmodel_q, ceffort))

    # shuffle arm assignment randomly
    arm_labels = list(_ARM_LABELS[:len(arm_list)])
    indices = list(range(len(arm_list)))
    random.shuffle(indices)
    shuffled_arms = [arm_list[i] for i in indices]

    # arm_map: label -> "model_q@effort"; identity_map: label -> "incumbent"|"candidate-N"
    arm_map = {}  # label -> "model_q@effort"
    identity_map = {}  # label -> identity
    for label, (model_q, effort) in zip(arm_labels, shuffled_arms):
        arm_map[label] = f"{model_q}@{effort}"
        if model_q == seat["model_q"] and effort == incumbent_effort:
            identity_map[label] = "incumbent"
        else:
            # find original index in arm_list (1-based among candidates)
            orig_idx = next(i for i, (mq, ef) in enumerate(arm_list) if mq == model_q and ef == effort)
            identity_map[label] = f"candidate-{orig_idx}"

    # Scorers: the first reviewer on a family no arm uses; when there is none (a mixed-family
    # trial), both reviewers score every arm, each in its own phase.
    arm_families = {roles_mod.family_of(mq, reg) for mq, _ in shuffled_arms}
    outside = next(
        (r for r in roles_mod.REVIEWERS
         if roles_mod.family_of(reg["roles"][r]["model"], reg) not in arm_families),
        None,
    )
    scorers = [(outside, scores_file())] if outside else [(r, scores_file(r)) for r in roles_mod.REVIEWERS]

    if arm_map_out is not None:
        arm_map_out.update(arm_map)
    # The preamble reaches every phase brief, scorers included: nothing in it may identify an arm.
    lines = [
        f"# trial: {role}",
        f"WORKDIR: {workdir}", "",
        f"DECISION trial-{role}: arms nominated; trial decides.", "",
    ]

    rubric = _demote_headings(rubric)
    n = 0
    for i, (name, task) in enumerate(tasks, 1):
        task = _demote_headings(task)
        for label in arm_labels:
            # repeats per arm
            rep_count = 1 if repeats is None else repeats.get(label, 1)
            for rep in range(rep_count):
                n += 1
                # For the first task, arms have no dependencies (start immediately).
                # For subsequent tasks, each arm depends on the same arm's phase from
                # the previous task (n - len(arm_labels) per repeat set).
                prev = n - len(arm_labels) * (rep_count if rep_count > 1 else 1)
                if i == 1:
                    after_clause = ""  # no AFTER directive needed for first task
                else:
                    after_clause = f"AFTER: {n - len(arm_labels)}"
                rep_suffix = f" rep{rep + 1}" if rep_count > 1 else ""
                model_q, effort = arm_map[label].rsplit("@", 1)
                phase_lines = [
                    f"## Phase {n}: task{i}-arm-{label}{rep_suffix} ({role})",
                    f"MODEL: {model_q}",
                    f"EFFORT: {effort}",
                ]
                if after_clause:
                    phase_lines.append(after_clause)
                out_dir = f"trial/task{i}/{label}"
                # EXIT: the arm's deliverable exists and is non-empty in its own output directory
                phase_lines.append(f'EXIT: test -n "$(find {out_dir} -type f -size +0c 2>/dev/null | head -n 1)"')
                phase_lines += ["", f"Write your deliverable under `{out_dir}/` only.", "",
                                *NO_COMMIT_NOTICE, "",
                                f"**Task {i}: {name}**", "", task, ""]
                lines += phase_lines

    arm_phases = n
    # The scorers get the same task text the arms got (labels only, no arm identity), so they can
    # judge whether each answer addresses what was asked.
    task_specs = []
    for i, (name, task) in enumerate(tasks, 1):
        task_specs += [f"**task{i}: {name}**", "", _demote_headings(task), ""]
    deps = ", ".join(str(k) for k in range(1, arm_phases + 1))
    for scorer, fname in scorers:
        n += 1
        lines += [
            f"## Phase {n}: score ({scorer})",
            f"AFTER: {deps}",
            f"CAPTURE: trial/{fname}",
            f"EXIT: test -s trial/{fname} && python3 -c \"import json;json.load(open('trial/{fname}'))\"",
            "",
            "Score each task's arms against the rubric below. You cannot write files: your final message",
            "must be only this JSON object, and the engine saves it as `trial/" + fname + "`:",
            '{"task1": {"A": <0-10>, "B": <0-10>, ...}, "task2": {...}}: one key per task below, named',
            "task1, task2, ..., and one score for every arm label on every task.",
            "The arms are known only by their labels A, B, C...; read each arm's deliverable in its",
            "trial/task<i>/<label>/ folder. You may also open the web sources and repository files an answer",
            "cites, or that a task names, to check claims. Never open trial/mapping.json, plan.md or the",
            "journal, and do not guess which arm is which.", "",
            "### Rubric", rubric, "",
            "### Tasks", *task_specs,
        ]
    return "\n".join(lines)


def select(arms: dict, band: float, reg: dict = None) -> dict:
    """Select the best model from a set of arms using the band-then-wall-then-cost rule.

    arms: {model: {"quality": mean, "wall_s": mean per dispatch, "cost": mean per dispatch}}
    band: the quality tolerance band (e.g. 1.5 for standard)
    reg: registry dict (optional). When given, arms whose model is tagged premium in
    reg["models"] are excluded BEFORE banding — a premium arm can never be chosen, no
    matter how it scores. Best-quality (the band's ceiling) is computed over the
    non-premium arms only. Premium arms that were scored still appear in `reasons`,
    marked "excluded: premium" so the report can show them. `quota_fallback` is the
    best (wall then cost) in-band non-premium arm other than `chosen` (or `chosen`
    itself if it is the only in-band non-premium arm); None when reg is absent or
    every arm is premium.

    Returns:
        {"chosen": model, "band_kept": [models], "ranking": [models in order],
         "reasons": {model: {...}}, "quota_fallback": model or None}
    """
    if not arms:
        raise ValueError("arms must be non-empty")

    if reg is not None:
        premium_arms = {m for m in arms if roles_mod.is_premium(_arm_model(m), reg)}
    else:
        premium_arms = set()

    eligible = {m: v for m, v in arms.items() if m not in premium_arms}
    if not eligible:
        raise ValueError("every arm is premium-tier; no eligible arm to select")

    best_quality = max(v["quality"] for v in eligible.values())
    threshold = best_quality - band

    band_kept = [m for m, v in eligible.items() if v["quality"] >= threshold]
    # Sort band members by wall_s then cost
    ranking = sorted(band_kept, key=lambda m: (arms[m]["wall_s"], arms[m]["cost"]))
    chosen = ranking[0]

    reasons = {}
    for m, v in arms.items():
        if m in premium_arms:
            reasons[m] = {
                "excluded": True,
                "reason": "excluded: premium",
                "quality": v["quality"],
                "wall_s": v["wall_s"],
                "cost": v["cost"],
            }
        elif v["quality"] < threshold:
            reasons[m] = {
                "excluded": True,
                "reason": f"quality {v['quality']:.3f} below threshold {threshold:.3f} (best={best_quality:.3f}, band={band})",
                "quality": v["quality"],
                "wall_s": v["wall_s"],
                "cost": v["cost"],
            }
        else:
            reasons[m] = {
                "excluded": False,
                "in_band": True,
                "quality": v["quality"],
                "wall_s": v["wall_s"],
                "cost": v["cost"],
                "rank": ranking.index(m) + 1,
            }

    quota_fallback = None
    if reg is not None:
        for m in ranking:
            if m != chosen:
                quota_fallback = m
                break
        else:
            quota_fallback = chosen if ranking else None

    return {
        "chosen": chosen,
        "band_kept": band_kept,
        "ranking": ranking,
        "reasons": reasons,
        "quota_fallback": quota_fallback,
    }


def _arm_model(arm: str) -> str:
    """Strip an optional '@effort' suffix from an arm string, leaving 'model' or
    'provider/model'."""
    return arm.rsplit("@", 1)[0] if "@" in arm else arm


def select_reviewers(arms_by_model: dict, band: float, families: dict, reg: dict = None) -> list:
    """Select the best set of three reviewers on three distinct families.

    arms_by_model: {model: {"quality": mean, "wall_s": mean, "cost": mean}}
    band: quality tolerance band
    families: {model: family_string}
    reg: registry dict (optional). When given, arms whose model is tagged premium in
    reg["models"] are excluded BEFORE banding — a premium arm can never be seated as
    a reviewer, no matter how it scores.

    Strategy: maximise count within band, then minimise summed wall_s, then summed cost.
    All three must be on distinct families.

    Returns: list of 3 model names.
    """
    if reg is not None:
        arms_by_model = {m: v for m, v in arms_by_model.items() if not roles_mod.is_premium(_arm_model(m), reg)}
    if len(arms_by_model) < 3:
        raise ValueError(f"need at least 3 arms, got {len(arms_by_model)}")

    best_quality = max(v["quality"] for v in arms_by_model.values())
    threshold = best_quality - band
    in_band = {m for m, v in arms_by_model.items() if v["quality"] >= threshold}

    models = list(arms_by_model.keys())
    n = len(models)

    best_set = None
    best_score = None  # (neg_in_band_count, sum_wall, sum_cost) - lower is better

    # Enumerate all triples with distinct families
    for i in range(n):
        for j in range(i + 1, n):
            for k in range(i + 2, n):
                if j == k:
                    continue
                triple = [models[i], models[j], models[k]]
                fams = {families.get(m, "unknown:" + m) for m in triple}
                if len(fams) < 3:
                    continue  # not 3 distinct families
                in_band_count = sum(1 for m in triple if m in in_band)
                sum_wall = sum(arms_by_model[m]["wall_s"] for m in triple)
                sum_cost = sum(arms_by_model[m]["cost"] for m in triple)
                score = (-in_band_count, sum_wall, sum_cost)
                if best_score is None or score < best_score:
                    best_score = score
                    best_set = triple

    if best_set is None:
        raise ValueError("no triple of models with three distinct families found")

    return best_set


def decide(trial_dir: Path, journal_state: dict, *, incumbent_q: str = None, candidate_q: str = None, role: str = None, reg: dict = None) -> dict:
    """Three axes from measured numbers: quality (rubric scores), wall, cost.

    Multi-arm mode: reads trial/mapping.json (arm -> "model_q@effort"), computes per-arm
    quality/wall/cost means, calls select() with the role's band, returns a verdict
    with "chosen" = the winning arm string (model_q@effort).

    Legacy 2-arm mode (incumbent_q + candidate_q provided): behaves like the old
    decide() but also sets "chosen" based on the band selection.

    Uses cost in dollars from dispatch rows (cost field); tokens are reported alongside.
    """
    mapping_path = trial_dir / "trial" / "mapping.json"
    if mapping_path.exists():
        arm_map = json.loads(mapping_path.read_text())  # label -> "model_q@effort" or model_q (legacy)
    else:
        arm_map = _infer_mapping(trial_dir)

    per_reviewer = _load_scores(trial_dir / "trial")
    tasks = _check_coverage(per_reviewer, arm_map, _plan_tasks(trial_dir / "plan.md"))
    scores = _average_scores(per_reviewer, tasks, sorted(arm_map))

    # Build per-arm quality, wall, cost, tokens from dispatches
    # arm_map: label -> "model_q@effort" (new) or model_q (legacy)
    # scores: {task: {label: score}}
    arm_quality: dict = {label: [] for label in arm_map}
    arm_wall: dict = {label: [] for label in arm_map}
    arm_cost: dict = {label: [] for label in arm_map}
    arm_tokens: dict = {label: [] for label in arm_map}

    # Collect scores per arm
    for task_scores in scores.values():
        for label, score in task_scores.items():
            if label in arm_quality:
                arm_quality[label].append(score)

    # Collect wall/cost/tokens per arm from dispatches
    # The arm_map value may be "model_q@effort" or just "model_q" (legacy)
    # Match dispatches by model (stripping @effort suffix if present)
    def arm_model(arm_val: str) -> str:
        """Extract just the model part from an arm string (strip @effort if present)."""
        return arm_val.rsplit("@", 1)[0] if "@" in arm_val else arm_val

    model_to_label = {arm_model(v): k for k, v in arm_map.items()}
    # Arms may share a model at different efforts, and scorer phases may run an arm's model:
    # when the trial's plan.md names the arm phases, match dispatches by phase, not by model.
    phase_to_label = _arm_phases(trial_dir / "plan.md")
    for d in journal_state.get("dispatches", {}).values():
        model = d.get("model")
        if phase_to_label:
            label = phase_to_label.get(str(d.get("phase")))
            if label not in arm_map:
                label = None
        else:
            label = model_to_label.get(model)
        if label and d.get("outcome") is not None:
            arm_wall[label].append(d.get("wall_s") or 0.0)
            arm_cost[label].append(d.get("cost") or 0.0)
            arm_tokens[label].append((d.get("tokens") or {}).get("total", 0) or 0)

    def mean(lst):
        return sum(lst) / len(lst) if lst else 0.0

    # Build arms dict keyed by the full arm string (model_q@effort or model_q for legacy)
    arms_by_arm = {}
    for label, arm_val in arm_map.items():
        arms_by_arm[arm_val] = {
            "quality": mean(arm_quality[label]),
            "wall_s": mean(arm_wall[label]),
            "cost": mean(arm_cost[label]),
            "tokens": mean(arm_tokens[label]),
            "tokens_total": int(sum(arm_tokens[label])),
        }
        if len(per_reviewer) > 1:
            arms_by_arm[arm_val]["reviewer_quality"] = {
                r: mean([ts[label] for ts in sc.values() if label in ts]) for r, sc in per_reviewer.items()}

    # Determine band
    if role is not None and reg is not None:
        role_def = reg.get("roles", {}).get(role, {})
        band_name = role_def.get("band", "standard")
        band = BANDS.get(band_name, BANDS["standard"])
    else:
        # Legacy: use standard band
        band = BANDS["standard"]

    # Run selection
    arms_for_select = {a: {"quality": v["quality"], "wall_s": v["wall_s"], "cost": v["cost"]}
                       for a, v in arms_by_arm.items()}
    sel = select(arms_for_select, band, reg=reg)

    # Legacy compatibility: 2-arm mode with incumbent_q / candidate_q
    # In legacy mode, arm_map values are plain model_q strings (no @effort)
    chosen_arm = sel["chosen"]
    if incumbent_q is not None:
        decision = "candidate" if chosen_arm != incumbent_q else "incumbent"
    else:
        decision = chosen_arm  # full arm string

    # Build token/wall aggregates per identity for legacy compat
    tok = {"total": int(sum(v["tokens"] for v in arms_by_arm.values()))}
    wall = {"total": sum(v["wall_s"] for v in arms_by_arm.values())}

    # Legacy per-identity aggregates
    if incumbent_q is not None and candidate_q is not None:
        inc_label = [l for l, v in arm_map.items() if arm_model(v) == incumbent_q or v == incumbent_q]
        cand_label = [l for l, v in arm_map.items() if arm_model(v) == candidate_q or v == candidate_q]
        q_legacy = {
            "incumbent": mean(arm_quality[inc_label[0]]) if inc_label else 0,
            "candidate": mean(arm_quality[cand_label[0]]) if cand_label else 0,
        }
        tok_legacy = {
            "incumbent": int(mean(arm_tokens[inc_label[0]])) if inc_label else 0,
            "candidate": int(mean(arm_tokens[cand_label[0]])) if cand_label else 0,
        }
        wall_legacy = {
            "incumbent": mean(arm_wall[inc_label[0]]) if inc_label else 0.0,
            "candidate": mean(arm_wall[cand_label[0]]) if cand_label else 0.0,
        }
        # Legacy reasons (re-compute for backward compat)
        reasons = []
        def regress(m):
            return m["incumbent"] > 0 and (m["candidate"] - m["incumbent"]) / m["incumbent"] > TOLERANCE
        q_tot_inc = sum(arm_quality[inc_label[0]]) if inc_label else 0
        q_tot_cand = sum(arm_quality[cand_label[0]]) if cand_label else 0
        if q_tot_cand <= q_tot_inc:
            reasons.append(f"quality not higher ({q_tot_cand} vs {q_tot_inc})")
        if regress(tok_legacy):
            reasons.append(f"tokens regressed >{TOLERANCE:.0%} ({tok_legacy['candidate']} vs {tok_legacy['incumbent']})")
        if regress(wall_legacy):
            reasons.append(f"wall regressed >{TOLERANCE:.0%} ({wall_legacy['candidate']:.0f}s vs {wall_legacy['incumbent']:.0f}s)")
        # Override decision for legacy test compat
        decision = "candidate" if not reasons else "incumbent"
        chosen_arm = candidate_q if decision == "candidate" else incumbent_q
    else:
        reasons = []
        q_legacy = None
        tok_legacy = None
        wall_legacy = None

    return {
        "chosen": chosen_arm,
        "decision": decision,
        "quality": q_legacy if q_legacy is not None else {a: v["quality"] for a, v in arms_by_arm.items()},
        "tokens": tok_legacy if tok_legacy is not None else {a: v["tokens"] for a, v in arms_by_arm.items()},
        "wall_s": wall_legacy if wall_legacy is not None else {a: v["wall_s"] for a, v in arms_by_arm.items()},
        "arms": arms_by_arm,
        "reviewer_quality": ({r: {arm_map[l]: mean([ts[l] for ts in sc.values() if l in ts]) for l in arm_map}
                              for r, sc in per_reviewer.items()} if len(per_reviewer) > 1 else None),
        "selection": sel,
        "reasons": reasons,
        "decided_at": now_iso(),
        "tolerance": TOLERANCE,
        "band": band,
    }


def _load_scores(trial_out: Path) -> dict:
    """{reviewer: {task: {label: score}}}. Two-reviewer files (scores-reviewer-a/-b.json) are
    required together: a missing one is an error, never a silent single score. With neither,
    the single-scorer scores.json is read under the key "scorer"."""
    pair = {r: trial_out / scores_file(r) for r in roles_mod.REVIEWERS}
    present = {r: p for r, p in pair.items() if p.exists()}
    if present:
        missing = [p.name for r, p in pair.items() if r not in present]
        if missing:
            raise TrialError(f"mixed-family trial needs both reviewers' scores; missing {', '.join(missing)}")
        return {r: _read_json(p) for r, p in present.items()}
    return {"scorer": _read_json(trial_out / scores_file())}


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except ValueError as e:
        raise TrialError(f"{path.name}: not valid JSON ({e})")


def _plan_tasks(plan_path: Path) -> list:
    """The task keys (`task1`, `task2`...) the trial plan's arm phases cover; [] without a plan."""
    import re
    if not plan_path.exists():
        return []
    found = {int(m.group(1)) for m in re.finditer(r"^## Phase \d+: task(\d+)-arm-[A-Z]\b", plan_path.read_text(), re.M)}
    return [f"task{i}" for i in sorted(found)]


def _check_coverage(per_reviewer: dict, arm_map: dict, expected_tasks: list) -> list:
    """Exact validation of every scorer's file, in one place, before any averaging: the set of task
    keys must equal the expected task set, and for every task the set of arm keys must equal the
    mapping's arm labels. Missing and unexpected keys are both rejected, as is any score that is not
    a number in 0..10 (booleans, strings, NaN and infinity included). The expected tasks come from
    the trial plan; without one, the first scorer's tasks, which every other scorer must then match
    exactly. Returns the expected task list, the only tasks averaging may visit."""
    if not isinstance(arm_map, dict) or not arm_map:
        raise TrialError("no arm mapping: nothing to score")
    if len(set(arm_map.values())) != len(arm_map):
        raise TrialError(f"arm mapping names the same arm twice: {arm_map!r}")
    labels = set(arm_map)
    tasks = list(expected_tasks) or sorted(next(iter(per_reviewer.values())) if isinstance(
        next(iter(per_reviewer.values())), dict) else [])
    if not tasks:
        raise TrialError("no scores: the scorer returned no tasks")
    want = set(tasks)
    for reviewer, sc in per_reviewer.items():
        if not isinstance(sc, dict):
            raise TrialError(f"{reviewer}: scores are not a JSON object")
        if set(sc) != want:
            raise TrialError(f"{reviewer}: task keys differ from the expected tasks: "
                             f"unexpected {sorted(set(sc) - want)}, missing {sorted(want - set(sc))}")
        for task in tasks:
            row = sc[task]
            if not isinstance(row, dict):
                raise TrialError(f"{reviewer}: task {task!r} not scored")
            if set(row) != labels:
                raise TrialError(f"{reviewer}: task {task!r} arm keys differ from the mapping: "
                                 f"unexpected {sorted(set(row) - labels)}, missing {sorted(labels - set(row))}")
            for label in sorted(labels):
                v = row[label]
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 10:
                    raise TrialError(f"{reviewer}: task {task!r} arm {label}: missing or invalid score {v!r}")
    return tasks


def _average_scores(per_reviewer: dict, tasks: list, labels: list) -> dict:
    """Average the reviewers' scores per task and arm, visiting only the expected tasks and arms
    (already validated by _check_coverage), never whatever keys a file carried. One scorer passes
    through as is."""
    sets = list(per_reviewer.values())
    return {task: {label: sum(sc[task][label] for sc in sets) / len(sets) for label in labels}
            for task in tasks}


def _arm_phases(plan_path: Path) -> dict:
    """{phase number (str): arm label} for the arm phases named `taskN-arm-X` in a trial plan.md."""
    import re
    if not plan_path.exists():
        return {}
    return {m.group(1): m.group(2) for m in
            re.finditer(r"^## Phase (\d+): task\d+-arm-([A-Z])\b", plan_path.read_text(), re.M)}


def extract_arm_map(plan_text: str) -> dict:
    """Extract the arm->model_q mapping from a trial plan's DECISION line."""
    for line in plan_text.splitlines():
        if line.startswith("DECISION arm-mapping:"):
            payload = line[len("DECISION arm-mapping:"):].strip()
            return json.loads(payload)
    return {"A": "incumbent", "B": "candidate"}


def _infer_mapping(trial_dir: Path) -> dict:
    """Try to read the arm mapping from the plan DECISION line if mapping.json absent."""
    plan_path = trial_dir / "plan.md"
    if plan_path.exists():
        return extract_arm_map(plan_path.read_text())
    # Default fallback
    return {"A": "incumbent", "B": "candidate"}


def apply(role: str, arm: str, verdict: dict, registry_path: Path = None) -> None:
    """Change the seat only when the trial verdict's chosen arm matches the arm arg.

    arm may be 'model' or 'model@effort'. If effort is present it is written to the
    registry alongside the model. Fallback family must still differ; the registry
    validator enforces it.
    """
    chosen = verdict.get("chosen")
    # Parse model and effort from the arm string
    if "@" in arm:
        model, effort = arm.rsplit("@", 1)
    else:
        model, effort = arm, None

    path = registry_path or roles_mod.REGISTRY
    reg = json.loads(path.read_text())
    model_q = roles_mod.qualified(model, reg)

    if roles_mod.is_premium(model, reg):
        raise roles_mod.RegistryError(
            f"apply refuses premium-tier arm {arm!r} (DECISION no-premium-seats)"
        )

    # Match chosen: could be "model_q@effort", "model_q", or "model"
    def chosen_matches(chosen_val: str, mq: str, m: str, ef) -> bool:
        if chosen_val == mq or chosen_val == m:
            return True
        # Also match "model_q@effort" or "model@effort"
        if ef is not None:
            if chosen_val == f"{mq}@{ef}" or chosen_val == f"{m}@{ef}":
                return True
        return False

    if not chosen_matches(chosen, model_q, model, effort):
        raise roles_mod.RegistryError(
            f"trial chose {chosen!r}, not {arm!r}; refusing to apply"
        )
    reg["roles"][role]["model"] = model
    if effort is not None:
        reg["roles"][role]["effort"] = effort
    # Seats have no fallback models: a selection's quota_fallback is reported, never seated.
    history_entry = {"model": model, "at": now_iso(), "trial": verdict}
    if effort is not None:
        history_entry["effort"] = effort
    reg["roles"][role].setdefault("history", []).append(history_entry)
    roles_mod.validate(reg)
    path.write_text(json.dumps(reg, indent=2) + "\n")
