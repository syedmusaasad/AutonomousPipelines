"""Model seat changes happen ONLY through staged head-to-head trials with rubrics.

`pipeline trial <role> <model@effort>...  --tasks tasks.md --rubric rubric.md`
  1. Parses tasks.md (one task per `## ` heading) and runs each task for each arm
     (incumbent + all candidates), blinded as A, B, C...; the mapping {A,B,...} ->
     {incumbent,candidate,...} is chosen randomly and saved to `trial/mapping.json`
     in the plan preamble. Task phases write to `trial/task<i>/<arm>/`.
  2. Dispatches a sealed reviewer on a THIRD family to score each arm against the
     rubric, writing `scores.json` ({task: {A: n, B: n, ...}}). The scorer sees
     only A/B/C labels — no model names, no incumbent/candidate.
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


def trial_plan(role: str, candidates, tasks: list, rubric: str, workdir: Path, reg: dict, repeats: dict = None) -> str:
    """A trial is itself a plan: arms (incumbent + all candidates), blinded as A, B, C...;
    each task x arm x repeat is one phase with MODEL: and EFFORT: pinned; mapping.json saved in the
    trial dir; a single scorer phase on a family not used by any arm.

    candidates: a single arm string (model or model@effort) or a list of arm strings.
    repeats: {arm_label: n} or None (defaults to 1 per arm).
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

    # Find scorer: reviewer on a family not used by any arm
    arm_families = {roles_mod.family_of(mq, reg) for mq, _ in shuffled_arms}
    scorer = next(
        r for r in roles_mod.REVIEWERS
        if roles_mod.family_of(reg["roles"][r]["model"], reg) not in arm_families
    )

    mapping_json = json.dumps(arm_map)
    cand_list = ", ".join(candidates)
    lines = [
        f"# trial: {role} incumbent={seat['model']} candidates={cand_list}",
        f"WORKDIR: {workdir}", "",
        f"DECISION trial-{role}: candidates [{cand_list}] nominated; trial decides.",
        f"DECISION arm-mapping: {mapping_json}", "",
    ]

    n = 0
    for i, (name, task) in enumerate(tasks, 1):
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
                phase_lines += ["", f"Write your deliverable under `trial/task{i}/{label}/` only.", "", task, ""]
                lines += phase_lines

    n += 1
    deps = ", ".join(str(k) for k in range(1, n))
    lines += [
        f"## Phase {n}: score ({scorer})",
        f"AFTER: {deps}",
        "EXIT: test -s trial/scores.json && python3 -c \"import json;json.load(open('trial/scores.json'))\"",
        "",
        "Score each task's arms against the rubric below. Write `trial/scores.json` as",
        '{"<task>": {"A": <0-10>, "B": <0-10>, ...}}.', "Do not edit anything else.", "",
        "### Rubric", rubric, "",
    ]
    return "\n".join(lines)


def select(arms: dict, band: float) -> dict:
    """Select the best model from a set of arms using the band-then-wall-then-cost rule.

    arms: {model: {"quality": mean, "wall_s": mean per dispatch, "cost": mean per dispatch}}
    band: the quality tolerance band (e.g. 1.5 for standard)

    Returns:
        {"chosen": model, "band_kept": [models], "ranking": [models in order], "reasons": {model: {...}}}
    """
    if not arms:
        raise ValueError("arms must be non-empty")

    best_quality = max(v["quality"] for v in arms.values())
    threshold = best_quality - band

    band_kept = [m for m, v in arms.items() if v["quality"] >= threshold]
    # Sort band members by wall_s then cost
    ranking = sorted(band_kept, key=lambda m: (arms[m]["wall_s"], arms[m]["cost"]))
    chosen = ranking[0]

    reasons = {}
    for m, v in arms.items():
        if v["quality"] < threshold:
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

    return {
        "chosen": chosen,
        "band_kept": band_kept,
        "ranking": ranking,
        "reasons": reasons,
    }


def select_reviewers(arms_by_model: dict, band: float, families: dict) -> list:
    """Select the best set of three reviewers on three distinct families.

    arms_by_model: {model: {"quality": mean, "wall_s": mean, "cost": mean}}
    band: quality tolerance band
    families: {model: family_string}

    Strategy: maximise count within band, then minimise summed wall_s, then summed cost.
    All three must be on distinct families.

    Returns: list of 3 model names.
    """
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

    scores_path = trial_dir / "trial" / "scores.json"
    scores = json.loads(scores_path.read_text())

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
    for d in journal_state.get("dispatches", {}).values():
        model = d.get("model")
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
        }

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
    sel = select(arms_for_select, band)

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
        "selection": sel,
        "reasons": reasons,
        "decided_at": now_iso(),
        "tolerance": TOLERANCE,
        "band": band,
    }


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
    history_entry = {"model": model, "at": now_iso(), "trial": verdict}
    if effort is not None:
        history_entry["effort"] = effort
    reg["roles"][role].setdefault("history", []).append(history_entry)
    roles_mod.validate(reg)
    path.write_text(json.dumps(reg, indent=2) + "\n")
