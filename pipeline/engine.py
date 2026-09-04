"""The engine: turns a plan into verified, journaled work.

Loop:
  1. Re-read the plan (append-aware: new phases are picked up without relaunch).
  2. Derive state from the journal (resume is idempotent: done phases stay done).
  3. Start every phase whose AFTER deps are done, up to the concurrency ceiling.
  4. Each phase runs in a thread: dispatch -> EXIT (engine-run) -> SURFACE ->
     REVIEW: cross. Any failing step feeds the next attempt with the failure text.
  5. Gate phases dispatch nothing; the engine waits for the operator's sentinel.
  6. Deliberate stops (burned attempts, failed gate, blocking review, config
     mismatch) write run.stop and exit non-zero. The sentry never relights those.

State is never kept in memory that the journal does not also have."""

import glob as globmod
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import dispatch as dsp
from . import paths, plan as planmod, registry, roles as roles_mod, surface as surfmod
from .journal import Journal
from .util import FileLock, fs_probe, log, new_id, now_ts, pid_alive, with_storm_armor, read_json

POLL_S = float(os.environ.get("PIPELINE_POLL_S", "5"))
GATE_POLL_S = float(os.environ.get("PIPELINE_GATE_POLL_S", "10"))
MAX_CONCURRENT_PHASES = int(os.environ.get("PIPELINE_MAX_PHASES", "3"))
EXIT_TIMEOUT_S = int(os.environ.get("PIPELINE_EXIT_TIMEOUT_S", "600"))
REVIEW_TIMEOUT_S = int(os.environ.get("PIPELINE_REVIEW_TIMEOUT_S", "900"))
STOP_REASONS = ("burned", "gate_failed", "review_blocking", "config_mismatch", "plan_invalid")


class DeliberateStop(Exception):
    def __init__(self, reason: str, detail: str = ""):
        assert reason in STOP_REASONS
        self.reason, self.detail = reason, detail
        super().__init__(f"{reason}: {detail}")


class Engine:
    def __init__(self, run_id: str, plan_path: Path):
        self.run_id = run_id
        self.plan_path = Path(plan_path).resolve()
        self.rdir = paths.run_dir(run_id)
        self.journal = Journal(run_id)
        self.reg = roles_mod.load()
        self.lock = FileLock(self.rdir / "engine.lock")
        self._stop = threading.Event()
        self._phase_lock = threading.Lock()
        self._running = set()

    # ---- lifecycle ---------------------------------------------------------

    def start(self) -> int:
        self.rdir.mkdir(parents=True, exist_ok=True)
        if not self.lock.acquire():
            log(f"engine for {self.run_id} already running; exiting")
            return 0
        (self.rdir / "engine.pid").write_text(str(os.getpid()))
        signal.signal(signal.SIGTERM, lambda *_: self._stop.set())
        try:
            return self._main()
        finally:
            self.lock.release()

    def _main(self) -> int:
        st = self.journal.state()
        try:
            plan = self._load_plan()
        except planmod.PlanError as e:
            self._deliberate_stop("plan_invalid", str(e))
            return 2
        if not st["phases"] and not st["open"] and st["closed"] is None:
            self.journal.write("run.open", plan=str(self.plan_path), cwd=str(plan.workdir), pid=os.getpid(),
                               conversation=registry.current_conversation(), phases=[p.number for p in plan.phases])
        else:
            if st["stopped"] and not self._stop_cleared():
                # A deliberate stop is the machine asking for judgment. Only an explicit
                # `pipeline resume` (run.resume with cleared=true) may continue.
                log(f"run {self.run_id} deliberately stopped ({st['stopped']}); refusing to continue")
                return 3
            if st["closed"]:
                log(f"run {self.run_id} already closed ({st['closed']}); nothing to do")
                return 0
            self.journal.write("run.resume", pid=os.getpid())
            self._mark_orphans(st)
        try:
            self._check_config(plan)
            return self._loop(plan)
        except DeliberateStop as e:
            self._deliberate_stop(e.reason, e.detail)
            return 2

    def _stop_cleared(self) -> bool:
        rows = self.journal.rows()
        last_stop = max((i for i, r in enumerate(rows) if r["event"] == "run.stop"), default=-1)
        return any(r["event"] == "run.resume" and r.get("cleared") for r in rows[last_stop + 1:])

    def _deliberate_stop(self, reason: str, detail: str):
        log(f"DELIBERATE STOP [{reason}]: {detail}")
        self.journal.write("run.stop", reason=reason, detail=detail[:2000])
        self.journal.write("run.close", outcome="stopped")
        # receipt file: the sentry checks this before relighting
        (self.rdir / "STOPPED").write_text(f"{reason}\n{detail}\n")

    def _mark_orphans(self, st: dict):
        """On resume, dispatches left open by a dead engine are closed as 'killed'."""
        for d in st["open_dispatches"]:
            if not pid_alive(d.get("pid")):
                self.journal.write("dispatch.end", id=d["id"], outcome="killed", wall_s=None, tokens={},
                                   cost=0.0, model=d.get("model"), note="orphaned by engine death")

    def _check_config(self, plan):
        drift = roles_mod.drift(reg=self.reg)
        if drift and os.environ.get("PIPELINE_WORKER_BIN") is None:
            raise DeliberateStop("config_mismatch", f"generated agent files drifted: {drift}; run `pipeline render-agents`")
        try:
            planmod.validate_roles(plan, set(self.reg["roles"]))
        except planmod.PlanError as e:
            raise DeliberateStop("plan_invalid", str(e))

    def _load_plan(self):
        plan = planmod.parse_file(self.plan_path)
        planmod.validate_roles(plan, set(self.reg["roles"]) | {planmod.GATE_ROLE})
        return plan

    # ---- scheduler -----------------------------------------------------------

    def _loop(self, plan) -> int:
        pool = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_PHASES)
        futures = {}
        failed = {}
        while not self._stop.is_set():
            try:
                plan = self._load_plan()  # append-aware
            except planmod.PlanError as e:
                if futures:
                    log(f"plan became invalid while phases run; finishing them: {e}")
                else:
                    raise DeliberateStop("plan_invalid", str(e))
            st = self.journal.state()
            done = {int(k) for k, v in st["phases"].items() if v["status"] == "done"}
            # reap futures
            for n, f in list(futures.items()):
                if f.done():
                    del futures[n]
                    exc = f.exception()
                    if isinstance(exc, DeliberateStop):
                        for other in futures.values():
                            other.cancel()
                        raise exc
                    if exc:
                        failed[n] = repr(exc)
                        self.journal.write("phase.fail", phase=str(n), reason=f"engine error: {exc!r}", attempt=0)
                        raise DeliberateStop("burned", f"phase {n} engine error: {exc!r}")
            # schedule
            for ph in plan.phases:
                if ph.number in done or ph.number in futures or ph.number in failed:
                    continue
                if not set(ph.after) <= done:
                    continue
                if len(futures) >= MAX_CONCURRENT_PHASES:
                    break
                futures[ph.number] = pool.submit(self._run_phase, plan, ph)
            all_done = all(p.number in done for p in plan.phases)
            if all_done and not futures:
                self.journal.write("run.close", outcome="done")
                pool.shutdown(wait=False)
                return 0
            if not futures and not all_done:
                # nothing runnable and nothing running: dependency deadlock or all remaining failed
                pending = [p.number for p in plan.phases if p.number not in done]
                raise DeliberateStop("plan_invalid", f"no runnable phases; pending {pending}")
            time.sleep(POLL_S)
        pool.shutdown(wait=False)
        log("engine stopping on signal")
        return 1

    # ---- phases ------------------------------------------------------------

    def _phase_dir(self, ph) -> Path:
        d = self.rdir / f"phase-{ph.number}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _run_phase(self, plan, ph):
        if ph.is_gate:
            return self._run_gate(plan, ph)
        st = self.journal.state().get("phases", {}).get(ph.key, {})
        # Only attempts that actually FAILED count against the budget. A phase left
        # "running" by a dead engine or an aborted run never got its verdict; it is
        # re-run as the same attempt number, not charged twice.
        prior_attempts = st.get("attempts", 0) if st.get("status") == "failed" else max(0, st.get("attempts", 0) - 1) if st.get("status") == "running" else 0
        failure_text = st.get("last_reason")
        for attempt in range(prior_attempts + 1, ph.attempts + 1):
            self.journal.write("phase.start", phase=ph.key, role=ph.role, attempt=attempt, name=ph.name)
            try:
                if ph.lanes:
                    self._run_lanes(plan, ph, attempt, failure_text)  # EXIT runs per lane inside
                else:
                    self._run_single(plan, ph, attempt, failure_text)
                    self._run_exits(plan, ph, env=None)
                self._run_surfaces(plan, ph, attempt)
                self._run_review(plan, ph)
            except PhaseFailure as e:
                failure_text = str(e)
                self.journal.write("phase.fail", phase=ph.key, reason=failure_text[:4000], attempt=attempt)
                continue
            self.journal.write("phase.done", phase=ph.key)
            return
        raise DeliberateStop("burned", f"phase {ph.number} ({ph.name}) burned {ph.attempts} attempts; last: {failure_text}")

    def _run_gate(self, plan, ph):
        sentinel = Path(ph.gate)
        if not sentinel.is_absolute():
            sentinel = plan.dir / sentinel
        self.journal.write("phase.wait", phase=ph.key, sentinel=str(sentinel), name=ph.name)
        while not self._stop.is_set():
            if sentinel.exists():
                content = sentinel.read_text(errors="replace").strip().lower()
                if content.startswith("no") or content.startswith("reject") or content.startswith("fail"):
                    raise DeliberateStop("gate_failed", f"phase {ph.number} sentinel {sentinel} says: {content[:200]}")
                self.journal.write("phase.done", phase=ph.key, sentinel=str(sentinel), content=content[:200])
                return
            time.sleep(GATE_POLL_S)
        raise PhaseFailure("engine stopped while waiting at gate")

    def _dispatch(self, plan, ph, *, attempt: int, brief: str, out_dir: Path, env: dict = None, role: str = None,
                  lane: int = None, item: str = None, timeout: int = None, cwd: Path = None) -> dsp.DispatchResult:
        role = role or ph.role
        did = new_id("d")
        s = roles_mod.seat(role, self.reg)
        models = [s["model_q"], s["fallback_q"]]
        if ph.model and role == ph.role:
            models = [roles_mod.qualified(ph.model, self.reg)]  # pinned (trials): no fallback, that would taint the arm
        # Determine effort: ph.effort overrides the seat's effort when set (for trial arms and EFFORT: directive)
        effort = ph.effort if (ph.effort is not None and role == ph.role) else None
        last = None
        for i, model in enumerate(models):
            def on_start(pid, transcript, model=model):
                self.journal.write("dispatch.start", id=did, phase=ph.key, role=role, model=model, pid=pid,
                                   transcript=str(transcript), attempt=attempt, lane=lane, item=item,
                                   fallback=(i > 0))
            res = dsp.run_dispatch(brief=brief, role=role, cwd=cwd or plan.workdir, out_dir=out_dir / f"try-{i}",
                                   timeout=timeout or ph.timeout, env=env, model=model, effort=effort,
                                   on_start=on_start, reg=self.reg)
            self.journal.write("dispatch.end", id=did, phase=ph.key, role=role, **res.as_row())
            last = res
            if res.outcome == "ok":
                return res
            if res.outcome in ("timeout", "outage", "killed"):
                break  # the task's or the host's problem, not the model's; don't burn the fallback
            if res.tokens.get("total", 0) > 0 and res.pid is not None:
                break  # the model ran and the task failed: that is a retry, not a seat change
            # the seat never completed a step (launch failure, provider error): fallback family once
            log(f"dispatch {did} on {model} never completed a step ({res.error}); trying fallback")
        return last

    def _run_single(self, plan, ph, attempt, failure_text):
        pdir = self._phase_dir(ph) / f"attempt-{attempt}"
        brief = dsp.build_brief(task=ph.brief, role=ph.role, cwd=plan.workdir, exits=ph.exits,
                                preamble=plan.preamble, previous_failure=failure_text,
                                extras={"PHASE": f"{ph.number}: {ph.name}", "RUN": self.run_id})
        res = self._dispatch(plan, ph, attempt=attempt, brief=brief, out_dir=pdir)
        if res.outcome != "ok":
            raise PhaseFailure(f"worker outcome {res.outcome}: {res.error or ''}\n--- worker tail ---\n{res.final_text[-1500:]}")

    def _run_lanes(self, plan, ph, attempt, failure_text):
        items_path = Path(ph.lanes)
        if not items_path.is_absolute():
            items_path = plan.dir / items_path
        if not items_path.exists():
            raise DeliberateStop("plan_invalid", f"phase {ph.number}: LANES file {items_path} missing")
        items = [ln.strip() for ln in items_path.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
        pdir = self._phase_dir(ph) / f"attempt-{attempt}"
        lanes_root = self._phase_dir(ph) / "lanes"
        st = self.journal.state()
        done_lanes = {int(r["lane"]) for r in self.journal.rows() if r["event"] == "lane.done" and r["phase"] == ph.key}
        failures = {}

        def one(idx, item):
            if idx in done_lanes:
                return
            lane_out = lanes_root / f"lane-{idx}"
            lane_out.mkdir(parents=True, exist_ok=True)
            env = {"ITEM": item, "LANE": str(idx), "LANE_OUT": str(lane_out)}
            brief = dsp.build_brief(task=ph.brief, role=ph.role, cwd=plan.workdir, exits=ph.exits, preamble=plan.preamble,
                                    previous_failure=failure_text if failure_text and f"lane {idx}" in failure_text else None,
                                    extras={"PHASE": f"{ph.number}: {ph.name}", "RUN": self.run_id, "ITEM": item,
                                            "LANE": idx, "LANE_OUT": str(lane_out)})
            res = self._dispatch(plan, ph, attempt=attempt, brief=brief, out_dir=pdir / f"lane-{idx}", env=env,
                                 lane=idx, item=item)
            if res.outcome != "ok":
                failures[idx] = f"lane {idx} item {item!r}: worker {res.outcome}: {res.error or ''}"
                return
            try:
                self._run_exits(plan, ph, env=env, lane=idx)
            except PhaseFailure as e:
                failures[idx] = f"lane {idx} item {item!r}: {e}"
                return
            self.journal.write("lane.done", phase=ph.key, lane=idx, item=item)

        with ThreadPoolExecutor(max_workers=ph.ceiling) as ex:
            list(ex.map(lambda t: one(*t), enumerate(items)))
        if failures:
            raise PhaseFailure("\n".join(failures[k] for k in sorted(failures)))

    # ---- verification --------------------------------------------------------

    def _run_exits(self, plan, ph, env: dict, lane=None):
        """EXIT predicates run BY THE ENGINE, in the plan workdir, with $ITEM/$LANE_OUT
        exported for lanes. The phase cannot complete while any fails."""
        fails = []
        for pred in ph.exits:
            ok, out = run_predicate(pred, cwd=plan.workdir, env=env, timeout=EXIT_TIMEOUT_S)
            self.journal.write("exit.check", phase=ph.key, predicate=pred, ok=ok, output=out[-800:], lane=lane)
            if not ok:
                fails.append(f"EXIT failed: `{pred}`\n{out[-1500:]}")
        if fails:
            raise PhaseFailure("\n\n".join(fails))

    def _run_surfaces(self, plan, ph, attempt):
        if not ph.surfaces:
            return
        results = self._score_surfaces(plan, ph)
        if all(r["pass"] for r in results):
            return
        # exactly one rewrite dispatch, carrying the failing metrics and quoted lines
        report = surfmod.failure_report(results)
        files = sorted({r["file"] for r in results if not r["pass"]})
        brief = dsp.build_brief(
            task=f"Rewrite these files so they pass the register standard. Fix the quoted offending lines and the "
                 f"failing metrics first; keep the content and structure.\nFiles: {', '.join(files)}\n"
                 f"Registers: {', '.join(sorted({r['surface'] for r in results}))} (see registers/<surface>.json rules)\n\n"
                 f"### Original phase brief\n{ph.brief}",
            role="document-writer", cwd=plan.workdir, exits=ph.exits, preamble=plan.preamble,
            previous_failure="SURFACE scoring failed.\n" + report,
            extras={"PHASE": f"{ph.number}: {ph.name} (surface rewrite)", "RUN": self.run_id})
        pdir = self._phase_dir(ph) / f"attempt-{attempt}" / "surface-rewrite"
        res = self._dispatch(plan, ph, attempt=attempt, brief=brief, out_dir=pdir, role="document-writer")
        if res.outcome != "ok":
            raise PhaseFailure(f"surface rewrite worker {res.outcome}: {res.error}")
        results = self._score_surfaces(plan, ph, rewrite=True)
        if not all(r["pass"] for r in results):
            raise PhaseFailure("SURFACE still failing after one rewrite:\n" + surfmod.failure_report(results))

    def _score_surfaces(self, plan, ph, rewrite=False) -> list:
        results = []
        for pattern, surf in ph.surfaces:
            matches = sorted(globmod.glob(str(plan.workdir / pattern), recursive=True))
            if not matches:
                r = {"pass": False, "file": pattern, "surface": surf, "metrics": {"files_matched": {"value": 0, "limit": 1, "pass": False, "unit": ">="}}, "offending": []}
                results.append(r)
                self.journal.write("surface.score", phase=ph.key, file=pattern, surface=surf, metrics=r["metrics"], **{"pass": False}, rewrite=rewrite)
                continue
            for f in matches:
                r = surfmod.score_file(Path(f), surf)
                results.append(r)
                self.journal.write("surface.score", phase=ph.key, file=f, surface=surf,
                                   metrics={k: v["value"] for k, v in r["metrics"].items()},
                                   failing=[k for k, v in r["metrics"].items() if not v["pass"]],
                                   offending=r["offending"][:8], rewrite=rewrite, **{"pass": r["pass"]})
        return results

    def _run_review(self, plan, ph):
        if ph.review != "cross":
            return
        a, b = roles_mod.cross_review_pair(self.reg)
        rdir = self._phase_dir(ph) / "review"
        verdicts = {}

        def review(role):
            out = rdir / role
            out.mkdir(parents=True, exist_ok=True)
            brief = dsp.build_brief(
                task=f"Review the work of phase {ph.number} ({ph.name}) against its brief and EXIT predicates.\n\n"
                     f"### Phase brief\n{ph.brief}\n\nWrite $REVIEW_OUT/review.md; the first line must be "
                     f"`VERDICT: PASS|CONCERNS|BLOCKING`.",
                role=role, cwd=plan.workdir, exits=ph.exits, preamble=plan.preamble,
                extras={"PHASE": f"{ph.number}: {ph.name} (review)", "RUN": self.run_id, "REVIEW_OUT": str(out)})
            res = self._dispatch(plan, ph, attempt=0, brief=brief, out_dir=out / "dispatch", env={"REVIEW_OUT": str(out)},
                                 role=role, timeout=REVIEW_TIMEOUT_S)
            verdict = read_verdict(out / "review.md")
            if res.outcome != "ok" and verdict is None:
                verdict = "UNAVAILABLE"
            verdicts[role] = (verdict, res.model)
            self.journal.write("review.verdict", phase=ph.key, reviewer=role, model=res.model, verdict=verdict,
                               review=str(out / "review.md"))

        with ThreadPoolExecutor(max_workers=2) as ex:
            list(ex.map(review, (a, b)))
        fams = {roles_mod.family_of(m, self.reg) for _, m in verdicts.values() if m}
        if len(fams) < 2:
            raise PhaseFailure(f"cross review did not span two families: {verdicts}")
        vs = {v for v, _ in verdicts.values()}
        if vs == {"BLOCKING"}:
            raise DeliberateStop("review_blocking", f"phase {ph.number}: both reviewers BLOCKING: {verdicts}")
        if "UNAVAILABLE" in vs or None in vs:
            raise PhaseFailure(f"a reviewer produced no verdict: {verdicts}")


class PhaseFailure(Exception):
    pass


def read_verdict(path: Path):
    try:
        first = path.read_text(errors="replace").strip().splitlines()[0]
    except (OSError, IndexError):
        return None
    if first.upper().startswith("VERDICT:"):
        v = first.split(":", 1)[1].strip().upper().split()[0] if first.split(":", 1)[1].strip() else ""
        return v if v in ("PASS", "CONCERNS", "BLOCKING") else None
    return None


def run_predicate(pred: str, *, cwd: Path, env: dict = None, timeout: int = 600):
    full = dict(os.environ)
    full.update(env or {})
    try:
        p = subprocess.run(["bash", "-o", "pipefail", "-c", pred], cwd=str(cwd), env=full, capture_output=True,
                           text=True, timeout=timeout)
        out = (p.stdout + p.stderr)
        return p.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, f"predicate timed out after {timeout}s"
    except OSError as e:
        return False, f"predicate could not run: {e}"


# ---- launching -----------------------------------------------------------------

def launch(plan_path: Path, *, run_id: str = None, conversation: str = None, kind: str = "plan") -> dict:
    """Validate the plan, register the run, and launch a detached engine. Returns the
    registry row. Never runs the engine in the foreground."""
    from .util import launch_detached
    plan_path = Path(plan_path).resolve()
    plan = planmod.parse_file(plan_path)
    reg = roles_mod.load()
    planmod.validate_roles(plan, set(reg["roles"]) | {planmod.GATE_ROLE})
    paths.ensure_layout()
    if not fs_probe(paths.runs_dir()):
        raise RuntimeError(f"estate filesystem not writable: {paths.runs_dir()}")
    run_id = run_id or new_id("run")
    rdir = paths.run_dir(run_id)
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "plan.path").write_text(str(plan_path))
    pid = launch_detached([sys.executable, "-m", "pipeline.cli", "engine", run_id, str(plan_path)],
                          log_path=rdir / "engine.log", cwd=str(plan.workdir),
                          env={"PIPELINE_CONVERSATION": conversation or registry.current_conversation()})
    row = registry.register(kind, run_id, journal=paths.journal_path(run_id), plan=str(plan_path),
                            cwd=str(plan.workdir), conversation=conversation, engine_pid=pid)
    return row


def relaunch(run_id: str, *, by: str = "sentry", cleared: bool = False) -> int:
    """Relight a dead engine for an open run. Idempotent: the engine resumes from the
    journal. If cleared=True the operator explicitly lifts a deliberate stop."""
    from .util import launch_detached
    rdir = paths.run_dir(run_id)
    plan_path = Path((rdir / "plan.path").read_text().strip())
    j = Journal(run_id)
    st = j.state()
    old = st.get("engine_pid")
    if cleared:
        stopped = rdir / "STOPPED"
        if stopped.exists():
            stopped.unlink()
        # remove closure so the engine treats the run as open again
        j.write("run.resume", pid=None, cleared=True, by=by)
    pid = launch_detached([sys.executable, "-m", "pipeline.cli", "engine", run_id, str(plan_path)],
                          log_path=rdir / "engine.log", cwd=str(plan_path.parent),
                          env={"PIPELINE_CONVERSATION": st.get("conversation") or registry.UNKNOWN})
    j.write("relight", by=by, old_pid=old, new_pid=pid)
    return pid
