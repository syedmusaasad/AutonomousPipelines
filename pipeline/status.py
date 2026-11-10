"""Status answers FROM the journal. Liveness is process-existence plus transcript
mtime, never the journal's own claim."""

import os
import re
import time
from pathlib import Path

from . import paths, registry
from .journal import Journal, run_ids
from .util import live_engine_pid, liveness, mtime_or_none, pid_alive, read_json

STALL_AFTER_S = int(os.environ.get("PIPELINE_STALL_S", "900"))

RESET_HINT_RE = re.compile(r"(?<=resets )(?!unknown)[^;]+|Resets? in [^.\"\\]+", re.IGNORECASE)


def run_report(run_id: str) -> dict:
    j = Journal(run_id)
    st = j.state()
    rdir = paths.run_dir(run_id)
    # the lock holder is the truth; the journal's pid can be a launch that lost the lock
    live = live_engine_pid(rdir, run_id)
    if live:
        st["engine_pid"] = live
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
    dispatch_rows = [
        {"id": d["id"], "phase": d.get("phase"), "role": d.get("role"), "model": d.get("model"),
         "wall_s": d.get("wall_s"), "tokens": (d.get("tokens") or {}).get("total"), "cost": d.get("cost")}
        for d in st["dispatches"].values() if d.get("outcome")
    ]
    waiting = [f"phase {k}: write sentinel {p['waiting_on']}" for k, p in phases.items() if p.get("status") == "waiting"]
    if st.get("stopped") and st["closed"]:
        detail = st.get("stop_detail") or ""
        if detail.startswith("ITERATE-STALLED:"):
            waiting.append(f"ITERATE-STALLED: {detail[len('ITERATE-STALLED:'):].strip()[:200]}  "
                            f"(the loop made no progress for consecutive iterations; inspect the iterate/ "
                            f"logs, fix the phase brief or PROGRESS predicate, then `pipeline resume {run_id}`)")
        elif detail.startswith("ITERATE-CEILING:"):
            waiting.append(f"ITERATE-CEILING: {detail[len('ITERATE-CEILING:'):].strip()[:200]}  "
                            f"(the loop exhausted its CEILING without EXIT passing; raise CEILING or fix the phase, "
                            f"then `pipeline resume {run_id}`)")
        else:
            waiting.append(f"deliberate stop [{st['stopped']}]: {detail[:200]}  (resume with `pipeline resume {run_id}` after judging)")
    # dispatch.end rows, not the collapsed per-id state: older journals reuse one
    # dispatch id across a quota hit and its fallback retry.
    quota_rows = [r for r in j.rows() if r.get("event") == "dispatch.end" and r.get("outcome") == "quota"]
    if quota_rows:
        models = sorted({r.get("model") for r in quota_rows if r.get("model")})
        reset = None
        for r in quota_rows:
            m = RESET_HINT_RE.search(r.get("error") or "")
            if m:
                reset = m.group(0)
                break
        waiting.append(f"usage limit hit on {', '.join(models)}; resets {reset or 'unknown'}")
    return {
        "run": run_id, "verdict": verdict, "plan": st.get("plan"), "conversation": st.get("conversation"),
        "engine_pid": st.get("engine_pid"), "engine_alive": engine_alive, "stopped_receipt": stopped_receipt,
        "phases": phases, "workers": workers, "dispatch_rows": dispatch_rows, "waiting_on_operator": waiting,
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
            it = p.get("iterate")
            if it:
                lines.append(f"    phase {k}: {p.get('status')} role={p.get('role')} "
                             f"iter {it.get('iteration')}/{it.get('ceiling')} stall={it.get('stall_count')} "
                             f"${round(it.get('cumulative_cost') or 0, 4)}")
            else:
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


# ---- inspect: everything about one run, in one read-only report ----------------

_EVENT_KEYS = ("phase", "role", "model", "outcome", "wall_s", "ok", "verdict", "reviewer", "reason", "detail",
               "by", "old_pid", "new_pid", "pid", "sentinel", "attempt", "note")


def _hms(t) -> str:
    return time.strftime("%m-%d %H:%M:%S", time.gmtime(t)) if t else "?"


def inspect(run_id: str, events: int = 15) -> str:
    """The engine, its workers, phases, reviews, sentry relights and the latest journal
    events for one run. Read-only. This is the interactive seat's eyes on the engine."""
    rdir = paths.run_dir(run_id)
    if not (rdir / "journal.jsonl").exists():
        raise FileNotFoundError(f"no such run: {run_id}")
    j = Journal(run_id)
    rows = j.rows()
    rep = run_report(run_id)
    st = j.state()
    now = time.time()
    out = [f"{run_id}  {rep['verdict']}  plan={rep['plan']}"]
    if st.get("stopped"):
        out.append(f"  stop: [{st['stopped']}] {(st.get('stop_detail') or '')[:400]}")
        out.append(f"  resume with: pipeline resume {run_id}   (after fixing the cause)")

    # engine: the lock holder is the truth
    live = live_engine_pid(rdir, run_id)
    journal_pid = st.get("engine_pid")
    if live:
        line = f"  engine: pid={live} ALIVE (holds the run lock)"
        if journal_pid and journal_pid != live:
            line += f"; journal names pid={journal_pid}, a launch that lost the lock (harmless)"
    elif st["closed"]:
        line = f"  engine: none (run closed: {st['closed']})"
    else:
        line = (f"  engine: DEAD (journal pid={journal_pid} is gone, nobody holds the lock). Live workers below are "
                f"unsupervised; `pipeline resume {run_id}` relights the engine.")
    out.append(line)
    relights = [r for r in rows if r["event"] == "relight" and not r.get("note")]
    if relights:
        last = relights[-1]
        out.append(f"  relights: {len(relights)} total, last {_hms(last.get('t'))} by {last.get('by')}"
                   + ("; sentry relight budget exhausted" if any(r["event"] == "relight.exhausted" for r in rows) else ""))
    log = rdir / "engine.log"
    if log.exists():
        tail = log.read_text(errors="replace").strip().splitlines()[-4:]
        out += ["  engine.log tail:"] + [f"    {l[:200]}" for l in tail]

    out.append("  phases:")
    for k, p in sorted(rep["phases"].items(), key=lambda kv: int(kv[0])):
        line = f"    {k}: {p.get('status')} role={p.get('role')} attempts={p.get('attempts')}"
        if p.get("status") == "waiting":
            line += f"  waiting on sentinel {p.get('waiting_on')}"
        if p.get("status") in ("failed", "corpse") and p.get("last_reason"):
            line += f"  last failure: {p['last_reason'][:240]!r}"
        out.append(line)

    out.append("  workers in flight:" if rep["workers"] else "  workers in flight: none")
    for w in rep["workers"]:
        state = "stalled" if w["stalled"] else ("alive" if w["alive"] else "DEAD")
        age = f"{int(w['age'])}s" if w.get("age") is not None else "n/a"
        ran = f"{int(now - w['started_t'])}s" if w.get("started_t") else "?"
        out.append(f"    {w['id']} phase={w['phase']} role={w['role']} model={w['model']} pid={w['pid']} {state} "
                   f"running={ran} transcript_age={age}")
        out.append(f"      transcript: {w.get('transcript')}")

    verdicts = [r for r in rows if r["event"] == "review.verdict"]
    if verdicts:
        out.append("  reviews:")
        out += [f"    {_hms(r.get('t'))} phase={r.get('phase')} {r.get('verdict')} by {r.get('model')}  {r.get('review')}"
                for r in verdicts[-6:]]
    if rep["waiting_on_operator"]:
        out.append("  waiting on operator:")
        out += [f"    - {w[:300]}" for w in rep["waiting_on_operator"]]
    out.append(f"  tokens={rep['tokens_total']} cost=${rep['cost_total']}  journal: {rdir / 'journal.jsonl'}")
    out.append(f"  last {min(events, len(rows))} journal events:")
    for r in rows[-events:]:
        fields = " ".join(f"{k}={str(r[k])[:90]}" for k in _EVENT_KEYS if r.get(k) is not None)
        out.append(f"    {_hms(r.get('t'))} {r['event']} {fields}".rstrip())
    return "\n".join(out) + "\n"
