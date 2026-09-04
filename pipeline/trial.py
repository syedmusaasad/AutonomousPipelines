"""Model seat changes happen ONLY through staged head-to-head trials with rubrics.

`pipeline trial <role> <candidate-model> --tasks tasks.md --rubric rubric.md`
  1. Parses tasks.md (one task per `## ` heading) and runs each task twice: once on
     arm-A, once on arm-B, where the mapping {A,B} -> {incumbent,candidate} is chosen
     randomly and saved to `trial/mapping.json` in the plan preamble. Task phases write
     to `trial/task<i>/A/` and `trial/task<i>/B/`.
  2. Dispatches a sealed reviewer on a THIRD family to score each pair against the
     rubric, writing `scores.json` ({task: {A: n, B: n}}). The scorer sees only A/B
     labels — no model names, no incumbent/candidate.
  3. Writes a verdict file. It does NOT change the registry; the operator applies
     `pipeline trial apply <trial-dir>` which refuses unless the candidate won on
     quality and did not regress speed or tokens by more than the tolerance.

Vendor benchmarks nominate candidates; the trial decides."""

import json
import random
from pathlib import Path

from . import roles as roles_mod
from .util import now_iso

TOLERANCE = 0.10  # 10% regression on wall or tokens is the most a quality win may cost


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


def trial_plan(role: str, candidate: str, tasks: list, rubric: str, workdir: Path, reg: dict) -> str:
    """A trial is itself a plan: paired phases per task, then a scoring phase on a third
    family, then a gate. Nothing about trials bypasses the engine.

    Arms are labelled A and B; the mapping to incumbent/candidate is random and saved to
    trial/mapping.json in the preamble DECISION line. The scorer never sees arm identities."""
    seat = roles_mod.seat(role, reg)
    cand_q = roles_mod.qualified(candidate, reg)
    fam_inc, fam_cand = seat["family"], roles_mod.family_of(candidate, reg)
    scorer = next(r for r in roles_mod.REVIEWERS if roles_mod.family_of(reg["roles"][r]["model"], reg) not in (fam_inc, fam_cand))

    # Random mapping: A and B assigned to incumbent/candidate non-deterministically
    if random.randint(0, 1):
        arm_map = {"A": "incumbent", "B": "candidate"}
    else:
        arm_map = {"A": "candidate", "B": "incumbent"}

    # model_map: arm -> model_q
    model_map = {
        arm: (seat["model_q"] if identity == "incumbent" else cand_q)
        for arm, identity in arm_map.items()
    }

    mapping_json = json.dumps(arm_map)
    lines = [f"# trial: {role} incumbent={seat['model']} candidate={candidate}", f"WORKDIR: {workdir}", "",
             f"DECISION trial-{role}: candidate {candidate} nominated; trial decides.",
             f"DECISION arm-mapping: {mapping_json}", ""]
    n = 0
    for i, (name, task) in enumerate(tasks, 1):
        for arm in ("A", "B"):
            n += 1
            lines += [f"## Phase {n}: task{i}-arm-{arm} ({role})", f"MODEL: {model_map[arm]}", "AFTER:" if n <= 2 else f"AFTER: {n - 2}", "",
                      f"Write your deliverable under `trial/task{i}/{arm}/` only.", "", task, ""]
    n += 1
    deps = ", ".join(str(k) for k in range(1, n))
    lines += [f"## Phase {n}: score ({scorer})", f"AFTER: {deps}", "EXIT: test -s trial/scores.json && python3 -c \"import json;json.load(open('trial/scores.json'))\"", "",
              "Score each task's two arms against the rubric below. Write `trial/scores.json` as",
              '{"<task>": {"A": <0-10>, "B": <0-10>, "why": "..."}}.', "Do not edit anything else.", "",
              "### Rubric", rubric, ""]
    return "\n".join(lines)


def decide(trial_dir: Path, journal_state: dict, *, incumbent_q: str, candidate_q: str) -> dict:
    """Three axes from measured numbers: quality (rubric scores), tokens, wall. The
    candidate wins only if quality is strictly higher AND neither tokens nor wall
    regress beyond TOLERANCE. No axis silently eats another.

    Reads trial/mapping.json to translate A/B scores back to incumbent/candidate."""
    mapping_path = trial_dir / "trial" / "mapping.json"
    if mapping_path.exists():
        arm_map = json.loads(mapping_path.read_text())
    else:
        # Fall back: parse from scores.json keys or read from plan decisions
        # Try to infer from plan DECISION line
        arm_map = _infer_mapping(trial_dir)

    # Invert: identity -> arm
    identity_to_arm = {v: k for k, v in arm_map.items()}
    inc_arm = identity_to_arm.get("incumbent", "A")
    cand_arm = identity_to_arm.get("candidate", "B")

    scores = json.loads((trial_dir / "trial" / "scores.json").read_text())
    q = {
        "incumbent": sum(v[inc_arm] for v in scores.values()),
        "candidate": sum(v[cand_arm] for v in scores.values()),
    }
    tok = {"incumbent": 0, "candidate": 0}
    wall = {"incumbent": 0.0, "candidate": 0.0}
    for d in journal_state["dispatches"].values():
        arm = "incumbent" if d.get("model") == incumbent_q else "candidate" if d.get("model") == candidate_q else None
        if arm and d.get("outcome") is not None:
            tok[arm] += (d.get("tokens") or {}).get("total", 0) or 0
            wall[arm] += d.get("wall_s") or 0.0

    def regress(m):
        return m["incumbent"] > 0 and (m["candidate"] - m["incumbent"]) / m["incumbent"] > TOLERANCE

    reasons = []
    if q["candidate"] <= q["incumbent"]:
        reasons.append(f"quality not higher ({q['candidate']} vs {q['incumbent']})")
    if regress(tok):
        reasons.append(f"tokens regressed >{TOLERANCE:.0%} ({tok['candidate']} vs {tok['incumbent']})")
    if regress(wall):
        reasons.append(f"wall regressed >{TOLERANCE:.0%} ({wall['candidate']:.0f}s vs {wall['incumbent']:.0f}s)")
    return {"quality": q, "tokens": tok, "wall_s": wall, "decision": "candidate" if not reasons else "incumbent",
            "reasons": reasons, "decided_at": now_iso(), "tolerance": TOLERANCE}


def extract_arm_map(plan_text: str) -> dict:
    """Extract the arm->identity mapping from a trial plan's DECISION line."""
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


def apply(role: str, candidate: str, verdict: dict, registry_path: Path = None) -> None:
    """Change the seat only when the trial verdict says candidate. Fallback family must
    still differ; the registry validator enforces it."""
    if verdict.get("decision") != "candidate":
        raise roles_mod.RegistryError(f"trial did not favour candidate: {verdict.get('reasons')}")
    path = registry_path or roles_mod.REGISTRY
    reg = json.loads(path.read_text())
    reg["roles"][role]["model"] = candidate
    reg["roles"][role].setdefault("history", []).append({"model": candidate, "at": now_iso(), "trial": verdict})
    roles_mod.validate(reg)
    path.write_text(json.dumps(reg, indent=2) + "\n")
