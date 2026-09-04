"""The journal is the single source of truth.

One append-only JSONL per run: <estate>/runs/<run_id>/journal.jsonl. Every row has
`ts`, `run`, `event`. Dispatch rows carry phase, role, model, wall-clock, tokens,
outcome. Status is DERIVED from rows (see `derive_state`); nothing here stores a
mutable "current status" that a corpse could leave lying.

Event vocabulary (the characterization suite pins this):
  run.open        plan, cwd, conversation, engine pid
  run.resume      engine pid (idempotent relaunch)
  run.close       outcome: done | failed | stopped
  run.stop        deliberate stop: reason (burned|gate_failed|review_blocking|config_mismatch|plan_invalid)
  phase.start     phase, role, attempt
  phase.done      phase
  phase.fail      phase, reason, attempt
  phase.wait      phase, gate sentinel path (gate phases)
  dispatch.start  dispatch id, phase, role, model, pid, transcript, lane/item
  dispatch.end    dispatch id, outcome, wall_s, tokens{in,out,total}, cost
  exit.check      phase, predicate, ok, output (truncated)
  review.verdict  phase, reviewer role, model, verdict (PASS|CONCERNS|BLOCKING)
  surface.score   phase, file, surface, metrics, pass
  recover.stall   phase, by (sentry), action
  relight         by (sentry), old pid, new pid
"""

import os
from pathlib import Path

from . import paths
from .util import append_jsonl, now_iso, now_ts, read_jsonl

TERMINAL_DISPATCH = {"ok", "failed", "timeout", "killed", "stalled", "outage"}


class Journal:
    def __init__(self, run_id: str, path: Path = None):
        self.run_id = run_id
        self.path = path or paths.journal_path(run_id)

    def write(self, event: str, **fields) -> dict:
        row = {"ts": now_iso(), "t": round(now_ts(), 3), "run": self.run_id, "event": event}
        row.update(fields)
        append_jsonl(self.path, row)
        return row

    def rows(self) -> list:
        return read_jsonl(self.path)

    def state(self) -> dict:
        return derive_state(self.rows())


def derive_state(rows: list) -> dict:
    """Fold the journal into a state snapshot. Pure function; suite pins it."""
    st = {
        "open": False,
        "closed": None,  # outcome or None
        "stopped": None,  # deliberate stop reason or None
        "plan": None,
        "cwd": None,
        "conversation": None,
        "engine_pid": None,
        "phases": {},  # phase -> {status, attempts, role, last_reason, waiting_on}
        "dispatches": {},  # id -> row-ish
        "relights": 0,
        "recoveries": {},  # phase -> count
        "tokens_total": 0,
        "cost_total": 0.0,
    }
    for r in rows:
        ev = r.get("event")
        if ev == "run.open":
            st.update(open=True, plan=r.get("plan"), cwd=r.get("cwd"), conversation=r.get("conversation"), engine_pid=r.get("pid"))
            st["opened_at"] = r.get("t")
        elif ev == "run.resume":
            if r.get("pid"):
                st["engine_pid"] = r.get("pid")
            if r.get("cleared"):
                # operator lifted a deliberate stop: the run is open again and burned
                # phases get a fresh attempt budget (judgment was applied)
                st["open"], st["closed"], st["stopped"] = True, None, None
                for p in st["phases"].values():
                    if p.get("status") == "failed":
                        p["attempts"] = 0
        elif ev == "run.close":
            st["open"] = False
            st["closed"] = r.get("outcome")
            st["closed_at"] = r.get("t")
        elif ev == "run.stop":
            st["stopped"] = r.get("reason")
            st["stop_detail"] = r.get("detail")
        elif ev == "phase.start":
            p = st["phases"].setdefault(r["phase"], {"attempts": 0, "waiting_on": None})
            p.update(status="running", role=r.get("role"), attempts=r.get("attempt", p["attempts"] + 1), started_at=r.get("t"))
        elif ev == "phase.done":
            p = st["phases"].setdefault(r["phase"], {"attempts": 0, "waiting_on": None})
            p.update(status="done", done_at=r.get("t"))
        elif ev == "phase.fail":
            p = st["phases"].setdefault(r["phase"], {"attempts": 0, "waiting_on": None})
            p.update(status="failed", last_reason=r.get("reason"), failed_at=r.get("t"))
        elif ev == "phase.wait":
            p = st["phases"].setdefault(r["phase"], {"attempts": 0})
            p.update(status="waiting", waiting_on=r.get("sentinel"), role="gate")
        elif ev == "dispatch.start":
            st["dispatches"][r["id"]] = {
                "id": r["id"], "phase": r.get("phase"), "role": r.get("role"), "model": r.get("model"),
                "pid": r.get("pid"), "transcript": r.get("transcript"), "started_t": r.get("t"),
                "outcome": None, "lane": r.get("lane"), "item": r.get("item"), "attempt": r.get("attempt"),
            }
        elif ev == "dispatch.end":
            d = st["dispatches"].setdefault(r["id"], {"id": r["id"]})
            d.update(outcome=r.get("outcome"), wall_s=r.get("wall_s"), tokens=r.get("tokens") or {}, cost=r.get("cost") or 0.0,
                     ended_t=r.get("t"), model=r.get("model", d.get("model")))
            st["tokens_total"] += (r.get("tokens") or {}).get("total", 0) or 0
            st["cost_total"] += r.get("cost") or 0.0
        elif ev == "relight":
            st["relights"] += 1
            st["engine_pid"] = r.get("new_pid")
        elif ev == "recover.stall":
            st["recoveries"][r["phase"]] = st["recoveries"].get(r["phase"], 0) + 1
    st["open_dispatches"] = [d for d in st["dispatches"].values() if d.get("outcome") is None]
    return st


def run_ids() -> list:
    d = paths.runs_dir()
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if (p / "journal.jsonl").exists())
