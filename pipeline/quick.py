"""quick: one detached dispatch for small work. `echo "task" | quick -r <role>`.

A quick is a one-phase run with no EXIT (unless -x given) so it shares the engine,
journal, and registry; nothing is a special case the sentry cannot see."""

import sys
from pathlib import Path

from . import engine, paths, registry
from . import plan as planmod
from .util import new_id


def launch_quick(task: str, *, role: str, cwd: Path, exits: list = (), timeout: int = 1800,
                 conversation: str = None, review: bool = False, landing: bool = False) -> dict:
    paths.ensure_layout()
    run_id = new_id("q")
    qdir = paths.quick_dir() / run_id
    qdir.mkdir(parents=True, exist_ok=True)
    first = (task.strip().splitlines() or [""])[0][:80]
    lines = [f"# quick: {first}", f"WORKDIR: {Path(cwd).resolve()}", "",
             f"## Phase 1: quick ({role})", f"TIMEOUT: {timeout}", "ATTEMPTS: 2"]
    if landing:
        lines.append("LANDING: on")
    lines += [f"EXIT: {e}" for e in exits]
    if review:
        lines.append("REVIEW: on")
    # The brief is written as one escaped line so no brief line can be read as a phase
    # heading or directive; the parser hands the worker the original text byte for byte.
    lines += [planmod.encode_brief(task), ""]
    plan_path = qdir / "plan.md"
    plan_path.write_text("\n".join(lines))
    row = engine.launch(plan_path, run_id=run_id, conversation=conversation, kind="quick")
    return row
