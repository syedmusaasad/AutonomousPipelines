"""Status answers FROM the journal. Liveness is process-existence plus transcript
mtime, never the journal's own claim."""

import os
import re
import time
from pathlib import Path

from . import paths, registry
from .journal import Journal, run_ids
from .util import liveness, mtime_or_none, pid_alive, read_json

STALL_AFTER_S = int(os.environ.get("PIPELINE_STALL_S", "900"))

RESET_HINT_RE = re.compile(r"Resets? in [^.\"\\]+", re.IGNORECASE)


def run_report(run_id: str) -> dict:
    j = Journal(run_id)
    st = j.state()
    rdir = paths.run_dir(run_id)
    engine_alive = pid_alive(st.get("engine_pid"))
    stopped_receipt = (rdir / "STOPPED").exists()
    workers = []
    for d in st["open_dispatches"]:
        lv = liveness(d.get("pid"), Path(d["transcript"]) if d.get("transcript") else Path("/nonexistent"), STALL_AFTER_S)
        workers.append({**d, **lv})
    # a "running" phase whose engine is dead is a corpse, whatever the journal says
    phases = {}
    for k, p in st["phases"].items():
        q = dict(p)
        if q.get("status") == "running" and not engine_alive:
            q["status"] = "corpse"
        phases[k] = q
    if st["closed"]:
        verdict = st["closed"]
        if st.get("stopped"):
            verdict = f"stopped:{st['stopped']}"
    elif st["open"]:
        verdict = "running" if engine_alive else "dead-engine"
    else:
        verdict = "unknown"
    waiting = [f"phase {k}: write sentinel {p['waiting_on']}" for k, p in phases.items() if p.get("status") == "waiting"]
    if st.get("stopped") and st["closed"]:
        waiting.append(f"deliberate stop [{st['stopped']}]: {(st.get('stop_detail') or '')[:200]}  (resume with `pipeline resume {run_id}` after judging)")
    # dispatch.end rows, not the collapsed per-id state: a quota hit followed by a
    # successful quota_fallback retry reuses the dispatch id, so the derived state's
    # single "outcome" per id would lose the quota event.
    quota_rows = [r for r in j.rows() if r.get("event") == "dispatch.end" and r.get("outcome") == "quota"]
    if quota_rows:
        models = sorted({r.get("model") for r in quota_rows if r.get("model")})
        reset = None
        for r in quota_rows:
            m = RESET_HINT_RE.search(r.get("error") or "")
            if m:
                reset = m.group(0)
                break
        waiting.append(f"premium allowance hit on {', '.join(models)}; resets {reset or 'unknown'}")
    return {
        "run": run_id, "verdict": verdict, "plan": st.get("plan"), "conversation": st.get("conversation"),
        "engine_pid": st.get("engine_pid"), "engine_alive": engine_alive, "stopped_receipt": stopped_receipt,
        "phases": phases, "workers": workers, "waiting_on_operator": waiting,
        "tokens_total": st["tokens_total"], "cost_total": round(st["cost_total"], 4), "relights": st["relights"],
        "opened_at": st.get("opened_at"), "closed_at": st.get("closed_at"),
    }


def all_reports(conversation: str = None) -> list:
    ids = run_ids()
    if conversation:
        mine = {r["run"] for r in registry.for_conversation(conversation)}
        ids = [i for i in ids if i in mine]
    return [run_report(i) for i in ids]


def render(reports: list, *, scope: str) -> str:
    """Status discipline: first sentence states the outcome; then in-flight; then an
    explicit waiting-on-you list (an empty one is stated)."""
    inflight = [r for r in reports if r["verdict"] in ("running", "dead-engine")]
    waiting = [w for r in reports for w in (f"[{r['run']}] {x}" for x in r["waiting_on_operator"])]
    done = [r for r in reports if r["verdict"] == "done"]
    stopped = [r for r in reports if r["verdict"].startswith("stopped") or r["verdict"] == "failed"]
    if not reports:
        first = f"No runs recorded for {scope}."
    elif inflight:
        first = f"{len(inflight)} run(s) in flight, {len(done)} done, {len(stopped)} stopped ({scope})."
    elif stopped:
        first = f"Nothing in flight; {len(stopped)} run(s) stopped and need judgment, {len(done)} done ({scope})."
    else:
        first = f"Nothing in flight; all {len(done)} run(s) done ({scope})."
    lines = [first, "", "In flight:"]
    if not inflight:
        lines.append("  (none)")
    for r in inflight:
        lines.append(f"  {r['run']}  {r['verdict']}  engine_pid={r['engine_pid']} alive={r['engine_alive']}  plan={r['plan']}")
        for k, p in sorted(r["phases"].items(), key=lambda kv: int(kv[0])):
            lines.append(f"    phase {k}: {p.get('status')} role={p.get('role')} attempts={p.get('attempts')}")
        for w in r["workers"]:
            age = f"{int(w['age'])}s" if w.get("age") is not None else "n/a"
            state = "stalled" if w["stalled"] else ("alive" if w["alive"] else "dead")
            lines.append(f"    worker {w['id']} phase={w['phase']} role={w['role']} model={w['model']} pid={w['pid']} {state} transcript_age={age}")
    if stopped:
        lines += ["", "Stopped (need judgment):"]
        for r in stopped:
            lines.append(f"  {r['run']}  {r['verdict']}  plan={r['plan']}")
    lines += ["", "Waiting on you:"]
    if not waiting:
        lines.append("  nothing")
    for w in waiting:
        lines.append(f"  - {w}")
    return "\n".join(lines) + "\n"
