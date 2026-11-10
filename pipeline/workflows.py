"""Materialized workflow templates and plan checking.

pipeline materialize <workflow> <out.md> renders a named template into a plan file.
pipeline plan-check <plan.md> validates grammar, role names, that every phase header
in the file parses (header count vs parser output), required EXIT presence per
workflow, and TIMEOUT bounds. run calls plan-check at launch and refuses a failing
plan."""

import re
from pathlib import Path

from . import plan as planmod
from . import roles as roles_mod

WORKFLOWS = {
    "quick-fix": """# quick-fix: one verified change
WORKDIR: {workdir}

## Phase 1: fix (implementer)
TIMEOUT: 1200
EXIT: {exit_predicate}

{brief}
""",
    "feature": """# feature: implement, review, gate
WORKDIR: {workdir}

## Phase 1: implement (implementer)
TIMEOUT: 1800
EXIT: {exit_predicate}

{brief}

## Phase 2: review (implementer)
AFTER: 1
REVIEW: on
TIMEOUT: 1800
EXIT: {exit_predicate}

## Phase 3: approve (gate)
GATE: {workdir}/.gate-approve
""",
    "release": """# release: land behind a gate
WORKDIR: {workdir}

## Phase 1: land (implementer)
TIMEOUT: 1200
EXIT: {exit_predicate}

{brief}

## Phase 2: approve (gate)
GATE: {workdir}/.gate-release
""",
}

REQUIRED_EXITS = {"quick-fix": 1, "feature": 1, "release": 1}
TIMEOUT_BOUNDS = (30, 7200)


def materialize(workflow: str, out_path: Path, *, workdir: str = ".", brief: str = "", exit_predicate: str = "true") -> Path:
    if workflow not in WORKFLOWS:
        raise planmod.PlanError(f"unknown workflow {workflow!r}; known: {sorted(WORKFLOWS)}")
    text = WORKFLOWS[workflow].format(workdir=workdir, brief=brief or "Do the work.", exit_predicate=exit_predicate)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text)
    return out_path


def check_plan(path: Path) -> list:
    """Return a list of failure strings; empty means the plan passes."""
    path = Path(path)
    failures = []
    text = path.read_text()
    header_count = len(re.findall(r"^##\s*Phase\s+\d+", text, flags=re.M))
    try:
        pl = planmod.parse_file(path)
    except planmod.PlanError as e:
        msg = str(e)
        m = re.search(r"line (\d+): malformed phase heading", msg)
        if m:
            ln = int(m.group(1))
            near = text.splitlines()[ln - 1] if ln <= len(text.splitlines()) else ""
            num = re.search(r"Phase\s+(\d+)", near)
            name = num.group(1) if num else str(ln)
            return [f"plan-check: phase {name} header malformed or dropped: {msg}"]
        return [f"grammar: {e}"]
    try:
        roles_mod.load()
        known = set(roles_mod.load()["roles"]) | {planmod.GATE_ROLE}
        planmod.validate_roles(pl, known)
    except (planmod.PlanError, roles_mod.RegistryError) as e:
        failures.append(f"roles: {e}")
    if len(pl.phases) != header_count:
        named = [p.number for p in pl.phases]
        dropped = sorted(set(range(1, header_count + 1)) - set(named))
        failures.append(f"phase headers in file ({header_count}) != parsed ({len(pl.phases)}); dropped: {dropped}")
    workflow = plan_workflow(text)
    if workflow and workflow in REQUIRED_EXITS:
        with_exit = [p for p in pl.phases if p.exits]
        if len(with_exit) < REQUIRED_EXITS[workflow]:
            failures.append(f"workflow {workflow} requires at least {REQUIRED_EXITS[workflow]} phase with EXIT lines")
    is_quick = text.lstrip().startswith("# quick:")
    for p in pl.phases:
        if not is_quick and not p.is_gate and not p.exits and not p.lanes:
            failures.append(f"phase {p.number}: no EXIT lines")
        if not (TIMEOUT_BOUNDS[0] <= p.timeout <= TIMEOUT_BOUNDS[1]):
            failures.append(f"phase {p.number}: TIMEOUT {p.timeout} outside bounds {TIMEOUT_BOUNDS}")
    for p in pl.phases:
        if p.is_gate and p.gate:
            sentinel = p.gate_sentinel(pl.dir)
            if not sentinel.parent.is_dir():
                failures.append(
                    f"phase {p.number}: GATE {p.gate} resolves to {sentinel}, "
                    f"whose parent directory {sentinel.parent} does not exist; a relative GATE is relative to "
                    f"the plan file's directory ({pl.dir}), not WORKDIR; use an absolute path or fix the relative one")
    return failures


def plan_workflow(text: str):
    m = re.match(r"^#\s+(\S+):", text.strip())
    if not m:
        return None
    first = m.group(1).lower()
    return first if first in WORKFLOWS else None
