"""The finisher pattern: workers die at ceremony (work done, commit/push missing).
A finisher dispatch verifies the tree state and lands the exact expected commit.

`finish --subject "<exact commit subject>" --paths a b --verify "<grep cmd>" [--push branch]`
launches a quick with an implementer brief whose EXIT predicates prove the commit
landed, so completion is refused until the ceremony is real."""

import shlex
from pathlib import Path

from .quick import launch_quick


def finisher_brief(*, subject: str, add_paths: list, verify: str = None, push: str = None, suite: str = None) -> tuple:
    paths_s = " ".join(shlex.quote(p) for p in add_paths)
    task = [
        "Land the exact expected commit. The work is already in the tree; do not redo it.",
        "1. `git status --porcelain` and confirm the staged/unstaged changes are limited to the paths below. "
        "If files outside the fence are modified, leave them alone (do not add, do not revert).",
        f"2. Stage ONLY: `git add -- {paths_s}`",
    ]
    if suite:
        task.append(f"3. Run the suite entrypoint: `{suite}`. If it fails, do NOT commit; write the failure to NOTES.md and stop.")
    task.append(f"4. Commit with exactly this subject: `git commit -m {shlex.quote(subject)}`")
    if push:
        task.append(f"5. Push only this branch: `git push origin {shlex.quote(push)}`")
    if verify:
        task.append(f"6. Run the verification: `{verify}` and include its output in your final lines.")
    task.append("Final lines: `git log -1 --format='%h %s'` output.")
    exits = [f"git log -1 --format=%s | grep -qxF {shlex.quote(subject)}"]
    for p in add_paths:
        exits.append(f"git diff --quiet HEAD -- {shlex.quote(p)}")  # nothing left unstaged under the fence
    if verify:
        exits.append(verify)
    if push:
        exits.append(f"git fetch -q origin {shlex.quote(push)} && git diff --quiet HEAD origin/{shlex.quote(push)}")
    return "\n".join(task), exits


def launch_finisher(*, cwd: Path, subject: str, add_paths: list, verify: str = None, push: str = None,
                    suite: str = None, conversation: str = None) -> dict:
    task, exits = finisher_brief(subject=subject, add_paths=add_paths, verify=verify, push=push, suite=suite)
    return launch_quick(task, role="implementer", cwd=cwd, exits=exits, timeout=1200, conversation=conversation)
