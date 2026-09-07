"""Runs registry: ~/.system/runs.jsonl keys every run (plan runs and quick dispatches)
to the CONVERSATION that launched it. The conversation id comes from
$PIPELINE_CONVERSATION (injected into agent shells by the devpass-code plugin) and is
stable across resumes of the same session. Any client can scope work by session.

Custody rule: a session owns only what it launched. Pasting another session's output
into this one does not transfer custody; only rows whose `conversation` matches do."""

import fcntl
import json
import os
from pathlib import Path

from . import paths
from .journal import Journal
from .util import append_jsonl, now_iso, now_ts, read_jsonl, with_storm_armor

UNKNOWN = "unattributed"


def current_conversation() -> str:
    return os.environ.get("PIPELINE_CONVERSATION") or UNKNOWN


def register(kind: str, run_id: str, *, journal: Path, plan: str = None, cwd: str = None,
             conversation: str = None, launcher_pid: int = None, engine_pid: int = None, extra: dict = None) -> dict:
    row = {
        "ts": now_iso(), "t": round(now_ts(), 3), "kind": kind, "run": run_id,
        "conversation": conversation or current_conversation(),
        "journal": str(journal), "plan": plan, "cwd": cwd,
        "launcher_pid": launcher_pid or os.getpid(), "engine_pid": engine_pid,
    }
    if extra:
        row.update(extra)
    path = paths.runs_registry_path()
    lock_path = path.with_name(path.name + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            append_jsonl(path, row)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    return row


def all_rows() -> list:
    return read_jsonl(paths.runs_registry_path())


def for_conversation(conversation: str) -> list:
    return [r for r in all_rows() if r.get("conversation") == conversation]


def lookup(run_id: str):
    for r in all_rows():
        if r.get("run") == run_id:
            return r
    return None


def resolve_run(prefix: str):
    """Resolve an exact run id before accepting a unique run-id prefix."""
    rows = all_rows()
    for row in rows:
        if row.get("run") == prefix:
            return prefix, row
    matches = [(row.get("run"), row) for row in rows
               if isinstance(row.get("run"), str) and row["run"].startswith(prefix)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return "ambiguous:" + ",".join(run_id for run_id, _ in matches)
    return None


def _write_rows(path: Path, rows: list) -> None:
    """Replace the registry atomically with storm-armor retries."""
    text = "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows)

    def write():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    with_storm_armor(write, what=f"rewrite {path}")


def adopt(run_id: str, new_conversation: str = None) -> dict:
    """Transfer registry custody and append the corresponding journal receipt."""
    target = new_conversation or current_conversation()
    prefix = Path(run_id).name if "/" in run_id else run_id
    resolved = resolve_run(prefix)
    if resolved is None:
        return {"action": "refused", "reason": "not-found"}
    if isinstance(resolved, str):
        return {"action": "refused", "reason": "ambiguous",
                "candidates": resolved.removeprefix("ambiguous:").split(",")}

    resolved_id, found = resolved
    old = found.get("conversation")
    if old == target:
        return {"action": "refused", "reason": "already-owned", "from": old, "to": target,
                "run": resolved_id}

    path = paths.runs_registry_path()
    lock_path = path.with_name(path.name + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            # Re-read while serialized with other adoptions, avoiding a stale row.
            rows = all_rows()
            exact = next((row for row in rows if row.get("run") == resolved_id), None)
            if exact is None:
                return {"action": "refused", "reason": "not-found"}
            old = exact.get("conversation")
            if old == target:
                return {"action": "refused", "reason": "already-owned", "from": old, "to": target,
                        "run": resolved_id}
            rewritten = [dict(row, conversation=target) if row is exact else row for row in rows]
            _write_rows(path, rewritten)
            try:
                Journal(resolved_id).write("adopted", by=target, **{"from": old})
            except Exception:
                _write_rows(path, rows)
                raise
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    return {"action": "adopted", "from": old, "to": target, "run": resolved_id}
