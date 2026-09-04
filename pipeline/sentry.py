"""The sentry: a daemon that keeps engines alive on a hostile host.

Each tick:
  0. Probe the filesystem BEFORE acting. Outage -> log, sleep, do nothing else
     (never spend recovery budget into an outage).
  1. For every open run (journal says open, no run.close):
     a. Engine process gone + no deliberate-stop receipt  -> RELIGHT (max 3 per
        run per window). Deliberate stops are never auto-restarted.
     b. Engine alive but a worker dispatch is stalled (process alive, transcript
        mtime older than the stall threshold) -> kill the worker's process group
        once per phase; the engine sees the failure and retries with the stall
        text. Bounded: one recovery per phase per run, journaled as recover.stall.
  2. Sleep.

The sentry has no memory beyond the journal: relight counts and recoveries are
counted from journal rows, so a restarted sentry is not a fresh budget."""

import os
import signal
import time
from pathlib import Path

from . import engine, paths
from .journal import Journal, run_ids
from .util import FileLock, fs_probe, liveness, log, now_ts, pid_alive

TICK_S = int(os.environ.get("PIPELINE_SENTRY_TICK_S", "60"))
STALL_S = int(os.environ.get("PIPELINE_STALL_S", "900"))
RELIGHT_MAX = int(os.environ.get("PIPELINE_RELIGHT_MAX", "3"))
RELIGHT_WINDOW_S = int(os.environ.get("PIPELINE_RELIGHT_WINDOW_S", str(6 * 3600)))
ENGINE_GRACE_S = int(os.environ.get("PIPELINE_ENGINE_GRACE_S", "30"))  # newly launched engines get a grace period


def tick(now: float = None) -> dict:
    """One sentry pass. Returns a summary dict (the suite pins it)."""
    now = now or now_ts()
    summary = {"probe": True, "relit": [], "recovered": [], "skipped_stopped": [], "outage": False}
    if not fs_probe(paths.state_dir()):
        summary["probe"] = False
        summary["outage"] = True
        log("sentry: filesystem probe FAILED; standing down this tick")
        return summary
    for rid in run_ids():
        try:
            _tend(rid, now, summary)
        except Exception as e:  # one bad run must not kill the sentry
            log(f"sentry: error tending {rid}: {e!r}")
    return summary


def _tend(rid: str, now: float, summary: dict):
    j = Journal(rid)
    rows = j.rows()
    st = j.state()
    rdir = paths.run_dir(rid)
    if (rdir / "STOPPED").exists() or st["stopped"]:
        summary["skipped_stopped"].append(rid)  # deliberate: the machine is asking for judgment
        return
    if st["closed"] or not st["open"]:
        return
    engine_pid = st.get("engine_pid")
    if not pid_alive(engine_pid):
        # grace: the launcher may not have written run.open yet, or relight just happened
        last_t = max((r.get("t", 0) for r in rows if r["event"] in ("run.open", "run.resume", "relight")), default=0)
        if ENGINE_GRACE_S > 0 and now_ts() - last_t < ENGINE_GRACE_S:
            return
        relights = [r for r in rows if r["event"] == "relight" and now - r.get("t", 0) < RELIGHT_WINDOW_S]
        if len(relights) >= RELIGHT_MAX:
            log(f"sentry: {rid} engine dead but relight budget exhausted ({len(relights)}/{RELIGHT_MAX} in window)")
            if not any(r["event"] == "relight.exhausted" for r in rows):
                j.write("relight.exhausted", by="sentry", count=len(relights))
            return
        log(f"sentry: relighting {rid} (engine pid {engine_pid} gone)")
        new_pid = engine.relaunch(rid, by="sentry")
        summary["relit"].append((rid, new_pid))
        return
    # engine alive: look for stalled workers
    for d in st["open_dispatches"]:
        phase = d.get("phase")
        if st["recoveries"].get(phase, 0) >= 1:
            continue  # bounded: once per phase
        lv = liveness(d.get("pid"), Path(d.get("transcript") or "/nonexistent"), STALL_S)
        if lv["alive"] and lv["stalled"]:
            log(f"sentry: {rid} dispatch {d['id']} phase {phase} stalled (transcript age {lv['age']}); killing pgid {d['pid']}")
            try:
                os.killpg(int(d["pid"]), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            j.write("recover.stall", phase=phase, by="sentry", dispatch=d["id"], action="killpg", transcript_age=lv["age"])
            summary["recovered"].append((rid, d["id"]))


def main_loop():
    paths.ensure_layout()
    lock = FileLock(paths.state_dir() / "sentry.lock")
    if not lock.acquire():
        log("sentry already running")
        return 0
    log(f"sentry up: tick={TICK_S}s stall={STALL_S}s relight_max={RELIGHT_MAX}/{RELIGHT_WINDOW_S}s")
    stop = False

    def _sig(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    while not stop:
        s = tick()
        try:
            (paths.state_dir() / "sentry.heartbeat").write_text(f"{now_ts()}\n{s}\n")
        except OSError:
            pass
        for _ in range(TICK_S):
            if stop:
                break
            time.sleep(1)
    lock.release()
    return 0
