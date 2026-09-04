"""quick: one detached dispatch for small work. `echo "task" | quick -r <role>`.

A quick is a one-phase run with no EXIT (unless -x given) so it shares the engine,
journal, and registry; nothing is a special case the sentry cannot see."""

import sys
from pathlib import Path

from . import engine, paths, registry
from .util import new_id


def launch_quick(task: str, *, role: str, cwd: Path, exits: list = (), timeout: int = 1800,
                 conversation: str = None, review: bool = False) -> dict:
    paths.ensure_layout()
    run_id = new_id("q")
    qdir = paths.quick_dir() / run_id
    qdir.mkdir(parents=True, exist_ok=True)
    lines = [f"# quick: {task.strip().splitlines()[0][:80]}", f"WORKDIR: {Path(cwd).resolve()}", "",
             f"## Phase 1: quick ({role})", f"TIMEOUT: {timeout}", "ATTEMPTS: 2"]
    lines += [f"EXIT: {e}" for e in exits]
    if review:
        lines.append("REVIEW: cross")
    lines += ["", task.strip(), ""]
    plan_path = qdir / "plan.md"
    plan_path.write_text("\n".join(lines))
    row = engine.launch(plan_path, run_id=run_id, conversation=conversation, kind="quick")
    return row
