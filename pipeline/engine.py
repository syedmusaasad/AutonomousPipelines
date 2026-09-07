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
import hashlib
import os
import re
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
ITERATE_STALL_LIMIT = 2  # consecutive no-progress iterations before ITERATE-STALLED


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
        if ph.iterate:
            return self._run_iterate(plan, ph)
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

    def _run_iterate(self, plan, ph):
        """Ralph-loop: fresh session per iteration, no prompt/failure carryover. See
        DECISION iterate-semantics in plans/013-iterate/plan.md for the 10 numbered
        steps this implements exactly."""
        pdir = self._phase_dir(ph)
        iterate_dir = pdir / "iterate"
        iterate_dir.mkdir(parents=True, exist_ok=True)
        prior = [r for r in self.journal.rows() if r.get("event") == "iterate.end" and r.get("phase") == ph.key]
        start_iter = len(prior) + 1
        stall_count = prior[-1]["stall_count"] if prior else 0
        cum_tokens = prior[-1].get("cumulative_tokens", 0) if prior else 0
        cum_cost = prior[-1].get("cumulative_cost", 0.0) if prior else 0.0
        if not prior:
            self.journal.write("phase.start", phase=ph.key, role=ph.role, attempt=1, name=ph.name)
        progress_check = self._make_progress_check(plan, ph, iterate_dir)
        for iteration in range(start_iter, ph.ceiling + 1):
            t0 = time.time()
            # (1) fresh session, unchanged phase body: previous_failure=None, always.
            brief = dsp.build_brief(task=ph.brief, role=ph.role, cwd=plan.workdir, exits=ph.exits,
                                    preamble=plan.preamble, previous_failure=None,
                                    extras={"PHASE": f"{ph.number}: {ph.name}", "RUN": self.run_id,
                                            "ITERATE_PROGRESS_FILE": f".pipeline/phase-{ph.number}/progress.md "
                                                                     "(maintain unchecked - [ ] boxes here)"},
                                    budget_s=ph.timeout)
            res = self._dispatch(plan, ph, attempt=iteration, brief=brief, out_dir=pdir / f"iter-{iteration}")
            # (2) run all EXITs (engine-run, same as the attempt loop).
            try:
                self._run_exits(plan, ph, env=None, iteration=iteration, failure_log_dir=iterate_dir)
                exit_ok = True
            except PhaseFailure:
                exit_ok = False
            wall_s = time.time() - t0
            tokens = res.tokens.get("total", 0) or 0
            cost = res.cost
            cum_tokens += tokens
            cum_cost = None if cum_cost is None or cost is None else cum_cost + cost
            if exit_ok:
                # (3) all pass -> phase done; REVIEW/SURFACE run once, only here.
                self.journal.write("iterate.end", phase=ph.key, iteration=iteration, exit_ok=True, progress=None,
                                    stall_count=stall_count, wall_s=round(wall_s, 2), tokens=tokens,
                                    cost=round(cost, 6) if cost is not None else None,
                                    cumulative_tokens=cum_tokens,
                                    cumulative_cost=round(cum_cost, 6) if cum_cost is not None else None,
                                    ceiling=ph.ceiling)
                try:
                    self._run_surfaces(plan, ph, iteration)
                    self._run_review(plan, ph)
                except PhaseFailure as e:
                    raise DeliberateStop("burned", f"phase {ph.number} ({ph.name}) iterate: EXIT passed at iteration "
                                                    f"{iteration} but REVIEW/SURFACE failed: {e}")
                self.journal.write("phase.done", phase=ph.key)
                return
            # (4) PROGRESS predicate (cwd=plan.workdir) or the built-in signal.
            progressed, is_first = progress_check()
            # (5) progress -> reset consecutive-no-progress to 0; else increment.
            if progressed:
                stall_count = 0
            else:
                stall_count += 1
            self.journal.write("iterate.end", phase=ph.key, iteration=iteration, exit_ok=False,
                                progress=(None if is_first else progressed), stall_count=stall_count,
                                wall_s=round(wall_s, 2), tokens=tokens, cost=round(cost, 6) if cost is not None else None,
                                cumulative_tokens=cum_tokens,
                                cumulative_cost=round(cum_cost, 6) if cum_cost is not None else None, ceiling=ph.ceiling)
            # (6) 2 consecutive no-progress -> deliberate stop ITERATE-STALLED. Stall wins ties.
            if stall_count >= ITERATE_STALL_LIMIT:
                raise DeliberateStop("burned", f"ITERATE-STALLED: phase {ph.number} ({ph.name}) made no progress "
                                                f"for {stall_count} consecutive iterations (iteration {iteration}/{ph.ceiling})")
            # (7) iteration count reaches CEILING with EXIT failing -> deliberate stop ITERATE-CEILING.
            if iteration >= ph.ceiling:
                raise DeliberateStop("burned", f"ITERATE-CEILING: phase {ph.number} ({ph.name}) reached "
                                                f"CEILING={ph.ceiling} without EXIT passing")
            # (8) next iteration.
        raise DeliberateStop("burned", f"ITERATE-CEILING: phase {ph.number} ({ph.name}) reached "
                                        f"CEILING={ph.ceiling} without EXIT passing")

    def _make_progress_check(self, plan, ph, iterate_dir):
        """Returns callable() -> (progressed: bool, is_first: bool). is_first is only
        ever True for the built-in signal's very first invocation (no PROGRESS override,
        no persisted state yet); a custom PROGRESS predicate always yields a real
        true/false, iteration 1 included."""
        if ph.iterate_progress:
            pred = ph.iterate_progress

            def check():
                ok, _ = run_predicate(pred, cwd=plan.workdir, timeout=EXIT_TIMEOUT_S)
                return ok, False
            return check

        signal = _build_builtin_progress_signal()

        def check():
            marker = iterate_dir / "progress-signal-started"
            is_first = not marker.exists()
            progressed = signal(iterate_dir, plan.workdir)
            if is_first:
                marker.touch()
            return progressed, is_first
        return check

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
        pinned = bool(ph.model and role == ph.role)
        # Determine effort: ph.effort overrides the seat's effort when set (for trial arms and EFFORT: directive)
        effort_override = ph.effort if (ph.effort is not None and role == ph.role) else None

        def run_one(model: str, eff, *, try_idx: int, family_fallback: bool, quota_fallback: bool) -> dsp.DispatchResult:
            def on_start(pid, transcript, model=model):
                self.journal.write("dispatch.start", id=did, phase=ph.key, role=role, model=model, pid=pid,
                                   transcript=str(transcript), attempt=attempt, lane=lane, item=item,
                                   fallback=family_fallback, quota_fallback=quota_fallback)
            res = dsp.run_dispatch(brief=brief, role=role, cwd=cwd or plan.workdir, out_dir=out_dir / f"try-{try_idx}",
                                   timeout=timeout or ph.timeout, env=env, model=model, effort=eff,
                                   on_start=on_start, reg=self.reg)
            self.journal.write("dispatch.end", id=did, phase=ph.key, role=role, **res.as_row())
            return res

        if pinned:
            # MODEL: pinned (trials): no fallback at all, quota or otherwise -- either
            # would taint the arm being measured.
            model = roles_mod.qualified(ph.model, self.reg)
            return run_one(model, effort_override, try_idx=0, family_fallback=False, quota_fallback=False)

        res = run_one(s["model_q"], effort_override, try_idx=0, family_fallback=False, quota_fallback=False)
        if res.outcome == "ok":
            return res
        if res.outcome == "quota":
            # The premium-tier weekly allowance is exhausted, not the model's or the
            # task's fault: fall to the seat's quota_fallback (never the family fallback,
            # and this alone never burns a phase attempt -- if it succeeds we're done).
            qf_model_q = s.get("quota_fallback_model_q")
            if not qf_model_q:
                return res
            qf_effort = effort_override or s.get("quota_fallback_effort")
            log(f"dispatch {did} on {s['model_q']} hit premium-tier quota; trying quota_fallback {qf_model_q}")
            return run_one(qf_model_q, qf_effort, try_idx=1, family_fallback=False, quota_fallback=True)
        if res.outcome in ("timeout", "outage", "killed"):
            return res  # the task's or the host's problem, not the model's; don't burn the fallback
        if res.tokens.get("total", 0) > 0 and res.pid is not None:
            return res  # the model ran and the task failed: that is a retry, not a seat change
        # the seat never completed a step (launch failure, provider error): fallback family once
        log(f"dispatch {did} on {s['model_q']} never completed a step ({res.error}); trying fallback")
        return run_one(s["fallback_q"], effort_override, try_idx=1, family_fallback=True, quota_fallback=False)

    def _run_single(self, plan, ph, attempt, failure_text):
        pdir = self._phase_dir(ph) / f"attempt-{attempt}"
        brief = dsp.build_brief(task=ph.brief, role=ph.role, cwd=plan.workdir, exits=ph.exits,
                                preamble=plan.preamble, previous_failure=failure_text,
                                extras={"PHASE": f"{ph.number}: {ph.name}", "RUN": self.run_id},
                                budget_s=ph.timeout)
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
                                            "LANE": idx, "LANE_OUT": str(lane_out)},
                                    budget_s=ph.timeout)
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

    def _run_exits(self, plan, ph, env: dict, lane=None, *, iteration=None, failure_log_dir: Path = None):
        """EXIT predicates run BY THE ENGINE, in the plan workdir, with $ITEM/$LANE_OUT
        exported for lanes. The phase cannot complete while any fails."""
        fails = []
        for i, pred in enumerate(ph.exits):
            ok, out = run_predicate(pred, cwd=plan.workdir, env=env, timeout=EXIT_TIMEOUT_S)
            self.journal.write("exit.check", phase=ph.key, predicate=pred, ok=ok, output=out[-800:], lane=lane,
                               **({"iteration": iteration} if iteration is not None else {}))
            if not ok:
                if failure_log_dir is not None:
                    (failure_log_dir / f"exit-{iteration}-{i}.log").write_text(out)
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
            # Failed/timed-out/killed reviewer dispatches are unconditionally ineligible:
            # their verdict is UNAVAILABLE regardless of files left behind.
            if res.outcome != "ok":
                 verdict = "UNAVAILABLE"
            else:
                 verdict = read_verdict(out / "review.md")
                 if verdict is None:
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


_NOISE_DIRS = {".pipeline", "__pycache__", ".git"}
_NOISE_SUFFIXES = (".log",)
_NOISE_NAMES = {"brief.md", "transcript.jsonl", "result.json"}


def _count_unchecked_boxes(workdir: Path):
    """Count `- [ ]` boxes across every .pipeline/phase-N*/progress.md or tasks.md
    under workdir. Returns None if no such file exists or none has any boxes at all
    (both checked and unchecked): a file with all boxes checked is a real 0, but a
    project that never wrote the file has no built-in-signal opinion."""
    files = sorted(globmod.glob(str(workdir / ".pipeline" / "phase-*" / "progress.md")))
    files += sorted(globmod.glob(str(workdir / ".pipeline" / "phase-*" / "tasks.md")))
    if not files:
        return None
    any_box = False
    unchecked = 0
    for f in files:
        try:
            text = Path(f).read_text(errors="replace")
        except OSError:
            continue
        if re.search(r"^\s*[-*]\s*\[[ xX]\]", text, re.M):
            any_box = True
        unchecked += len(re.findall(r"^\s*[-*]\s*\[ \]", text, re.M))
    return unchecked if any_box else None


def _git_tracked_state_fingerprint(workdir: Path) -> str:
    """git status --porcelain + git diff of tracked files, hashed. Falls back to a
    deterministic mtime+size walk (excluding engine/estate noise) when workdir is
    not inside a git repo."""
    try:
        status = subprocess.run(["git", "status", "--porcelain"], cwd=str(workdir), capture_output=True,
                                text=True, timeout=30)
        diff = subprocess.run(["git", "diff"], cwd=str(workdir), capture_output=True, text=True, timeout=60)
        if status.returncode == 0 and diff.returncode == 0:
            h = hashlib.sha256()
            h.update(status.stdout.encode())
            h.update(diff.stdout.encode())
            return h.hexdigest()
    except (OSError, subprocess.SubprocessError):
        pass
    h = hashlib.sha256()
    for root, dirs, files in os.walk(workdir):
        dirs[:] = sorted(d for d in dirs if d not in _NOISE_DIRS)
        for name in sorted(files):
            if name in _NOISE_NAMES or name.endswith(_NOISE_SUFFIXES):
                continue
            p = Path(root) / name
            try:
                st = p.stat()
            except OSError:
                continue
            rel = str(p.relative_to(workdir))
            h.update(f"{rel}:{st.st_mtime_ns}:{st.st_size}\n".encode())
    return h.hexdigest()


def builtin_progress_signal(state_dir: Path, workdir: Path) -> bool:
    """The built-in PROGRESS signal used when a phase omits PROGRESS. Checkbox count
    first (a phase-local progress file the brief tells workers to maintain); else a
    project-state fingerprint (git tracked-file state, or a noise-excluding mtime+size
    walk as fallback). State persists in `state_dir`, updated only when progress is
    detected. Tolerant of the first call ever: no prior state means progress by
    definition (persisted, returns True)."""
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    count = _count_unchecked_boxes(Path(workdir))
    if count is not None:
        f = state_dir / "checkbox_count.txt"
        prev = f.read_text().strip() if f.exists() else None
        if prev is None:
            f.write_text(str(count))
            return True
        if count < int(prev):
            f.write_text(str(count))
            return True
        return False
    fp = _git_tracked_state_fingerprint(Path(workdir))
    f = state_dir / "fingerprint.txt"
    prev = f.read_text().strip() if f.exists() else None
    if prev is None:
        f.write_text(fp)
        return True
    if fp != prev:
        f.write_text(fp)
        return True
    return False


def _build_builtin_progress_signal():
    """Build the default PROGRESS predicate with its durable state supplied by callers."""
    return builtin_progress_signal


# Kept as a named builder-compatible alias for callers from the iterate rollout.
_builtin_progress = builtin_progress_signal


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
    for w in planmod.deliberation_warnings(plan):
        log(w)
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
