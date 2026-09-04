"""Runs registry: ~/.system/runs.jsonl keys every run (plan runs and quick dispatches)
to the CONVERSATION that launched it. The conversation id comes from
$PIPELINE_CONVERSATION (injected into agent shells by the devpass-code plugin) and is
stable across resumes of the same session. Any client can scope work by session.

Custody rule: a session owns only what it launched. Pasting another session's output
into this one does not transfer custody; only rows whose `conversation` matches do."""

import os
from pathlib import Path

from . import paths
from .util import append_jsonl, now_iso, now_ts, read_jsonl

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
    append_jsonl(paths.runs_registry_path(), row)
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
