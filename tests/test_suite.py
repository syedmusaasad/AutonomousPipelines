"""Characterization suite. Every behavior the spec names has a test here; the run.py
ratchet forbids the count going down."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import Estate, wait_for, REPO, FAKE  # noqa: E402

from pipeline import plan as planmod, roles as roles_mod, surface as surfmod, journal as jmod, util, registry, status, bench, sentry, engine, dispatch as dsp, finisher, trial  # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def run_plan(E, text, sub="p", timeout=60, run_id=None):
    p = E.plan(text, sub=sub)
    rid = run_id or util.new_id("t")
    (E.estate / "runs" / rid).mkdir(parents=True, exist_ok=True)
    (E.estate / "runs" / rid / "plan.path").write_text(str(p))
    r = E.engine_fg(rid, p, timeout=timeout)
    return rid, p, r, jmod.Journal(rid).state()


# ---------------------------------------------------------------- plan grammar

@test
def plan_parses_every_directive():
    text = """# T
WORKDIR: .
DECISION scope: keep it small

## Phase 1: build (implementer)
EXIT: test -f a
EXIT: test -f b
TIMEOUT: 30
ATTEMPTS: 3
do it

## Phase 2: fan (lane-worker)
AFTER: 1
LANES: items.txt
CEILING: 3
EXIT: test -f "$LANE_OUT/r"

## Phase 3: doc (document-writer)
AFTER: 1
SURFACE: *.md operator-doc
REVIEW: cross

## Phase 4: ship (gate)
AFTER: 2, 3
GATE: .go
"""
    pl = planmod.parse_text(text, Path("/x/plan.md"))
    assert [p.number for p in pl.phases] == [1, 2, 3, 4]
    p1 = pl.by_number(1)
    assert p1.exits == ["test -f a", "test -f b"] and p1.timeout == 30 and p1.attempts == 3 and p1.brief == "do it"
    assert p1.after == []
    p2 = pl.by_number(2)
    assert p2.lanes == "items.txt" and p2.ceiling == 3 and p2.after == [1]
    p3 = pl.by_number(3)
    assert p3.surfaces == [("*.md", "operator-doc")] and p3.review == "cross" and p3.after == [1]
    p4 = pl.by_number(4)
    assert p4.is_gate and p4.after == [2, 3] and p4.gate == ".go"
    assert planmod.topo_order(pl) == [1, 2, 3, 4]
    assert pl.decisions == ["DECISION scope: keep it small"]
    assert pl.workdir == Path("/x")


@test
def plan_default_is_strict_sequence():
    pl = planmod.parse_text("## Phase 1: a (implementer)\n\n## Phase 2: b (implementer)\n\n## Phase 3: c (implementer)\n")
    assert pl.by_number(2).after == [1] and pl.by_number(3).after == [2]


@test
def plan_rejects_bad_grammar():
    bad = [
        "## Phase 1: a (implementer)\n## Phase 1: b (implementer)\n",  # duplicate
        "## Phase 1: a (implementer)\nAFTER: 9\n",  # unknown dep
        "## Phase 1: a (implementer)\nREVIEW: single\n",  # only cross
        "## Phase 1: a (implementer)\nSURFACE: onlyglob\n",
        "## Phase 1: a (implementer)\nTIMEOUT: soon\n",
        "## Phase 1: g (gate)\nEXIT: true\n",  # gates take no EXIT
        "## Phase 1: a (implementer)\nGATE: x\n",  # GATE only on gates
        "## Phase 1: a (implementer)\nAFTER: 2\n## Phase 2: b (implementer)\nAFTER: 1\n",  # cycle
        "## Phase 1: a (implementer)\n## Broken heading\n",
        "## Phase 1: a (implementer)\nEXIT:\n",
    ]
    for b in bad:
        try:
            planmod.parse_text(b)
        except planmod.PlanError:
            continue
        raise AssertionError(f"accepted bad plan: {b!r}")


@test
def plan_unknown_role_is_refused():
    pl = planmod.parse_text("## Phase 1: a (wizard)\n")
    try:
        planmod.validate_roles(pl, {"implementer"})
    except planmod.PlanError:
        return
    raise AssertionError("unknown role accepted")


@test
def plan_gate_default_sentinel():
    pl = planmod.parse_text("## Phase 1: g (gate)\n", Path("/p/plan.md"))
    assert pl.by_number(1).gate == "/p/.gate-1"


# ---------------------------------------------------------------- registry

@test
def registry_validates_families_and_seals():
    reg = roles_mod.load()
    for name, r in reg["roles"].items():
        assert roles_mod.family_of(r["model"], reg) != roles_mod.family_of(r["fallback"], reg), name
        assert not r.get("external_post")
    for rv in roles_mod.REVIEWERS:
        assert reg["roles"][rv]["sealed"] and not reg["roles"][rv]["tools"]["edit"]
    a, b = roles_mod.cross_review_pair(reg)
    assert roles_mod.family_of(reg["roles"][a]["model"], reg) != roles_mod.family_of(reg["roles"][b]["model"], reg)
    import copy
    bad = copy.deepcopy(reg)
    bad["roles"]["implementer"]["fallback"] = bad["roles"]["implementer"]["model"]
    try:
        roles_mod.validate(bad)
    except roles_mod.RegistryError:
        pass
    else:
        raise AssertionError("same-family fallback accepted")
    bad = copy.deepcopy(reg)
    bad["roles"]["reviewer-a"]["tools"]["edit"] = True
    try:
        roles_mod.validate(bad)
    except roles_mod.RegistryError:
        pass
    else:
        raise AssertionError("reviewer with edit accepted")
    bad = copy.deepcopy(reg)
    bad["roles"]["fast-worker"]["external_post"] = True
    try:
        roles_mod.validate(bad)
    except roles_mod.RegistryError:
        pass
    else:
        raise AssertionError("external_post accepted")


@test
def registry_models_table_and_quota_fallback_never_premium():
    """roles["models"][model]["premium"] tags every candidate; every role's quota_fallback
    resolves to a model the table does not tag premium; validate() rejects one that does."""
    reg = roles_mod.load()
    assert "models" in reg and isinstance(reg["models"], dict) and reg["models"]
    for model, info in reg["models"].items():
        assert "premium" in info, model
        assert info["premium"] in (True, False, "unknown"), model
    for name, r in reg["roles"].items():
        assert "quota_fallback" in r, name
        qf_model, _ = roles_mod._split_arm(r["quota_fallback"])
        assert not roles_mod.is_premium(qf_model, reg), f"{name}: quota_fallback {r['quota_fallback']} is premium"
    import copy
    bad = copy.deepcopy(reg)
    # pick a confirmed-premium model and force it in as implementer's quota_fallback
    premium_model = next(m for m, i in bad["models"].items() if i["premium"] is True)
    bad["roles"]["implementer"]["quota_fallback"] = f"{premium_model}@high"
    try:
        roles_mod.validate(bad)
    except roles_mod.RegistryError:
        pass
    else:
        raise AssertionError("premium quota_fallback accepted")
    bad2 = copy.deepcopy(reg)
    del bad2["roles"]["implementer"]["quota_fallback"]
    try:
        roles_mod.validate(bad2)
    except roles_mod.RegistryError:
        pass
    else:
        raise AssertionError("missing quota_fallback accepted")


@test
def validate_rejects_premium_model_or_fallback_anywhere():
    """DECISION no-premium-seats: validate() rejects a registry where ANY role's model,
    fallback or quota_fallback is a model tagged premium in reg["models"]; the error
    names the role and field. Not just quota_fallback (already covered above) — model
    and fallback too."""
    import copy
    reg = roles_mod.load()
    premium_model = next(m for m, i in reg["models"].items() if i["premium"] is True)
    other_premium = next(m for m, i in reg["models"].items() if i["premium"] is True and m != premium_model)

    bad = copy.deepcopy(reg)
    bad["roles"]["implementer"]["model"] = premium_model
    try:
        roles_mod.validate(bad)
    except roles_mod.RegistryError as e:
        assert "implementer" in str(e) and "model" in str(e)
    else:
        raise AssertionError("premium primary model accepted")

    bad = copy.deepcopy(reg)
    # give fallback a distinct premium family so the family check doesn't fire first
    bad["roles"]["implementer"]["fallback"] = other_premium
    try:
        roles_mod.validate(bad)
    except roles_mod.RegistryError as e:
        assert "implementer" in str(e) and "fallback" in str(e)
    else:
        raise AssertionError("premium fallback accepted")

    # the registry on disk has no premium seat anywhere: model, fallback, quota_fallback
    prem = {m for m, i in reg["models"].items() if i["premium"] is True}
    for name, r in reg["roles"].items():
        for field in ("model", "fallback", "quota_fallback"):
            fmodel, _ = roles_mod._split_arm(r[field])
            assert fmodel not in prem, f"{name}.{field} = {r[field]!r} is premium-tier"
    assert reg["roles"]["interactive"]["model"] == "glm-5.3"


@test
def select_never_returns_premium_arm_even_when_it_scores_best():
    """select() never returns a premium arm as `chosen`, even when it has the best
    quality, wall and cost of all arms on offer."""
    reg = {"models": {
        "premZ": {"premium": True}, "stdY": {"premium": False}, "stdX": {"premium": False},
    }}
    arms = {
        "premZ": {"quality": 10.0, "wall_s": 1.0, "cost": 0.01},  # dominates on every axis
        "stdY": {"quality": 9.0, "wall_s": 5.0, "cost": 0.05},
        "stdX": {"quality": 8.7, "wall_s": 6.0, "cost": 0.06},
    }
    result = trial.select(arms, 1.5, reg=reg)
    assert result["chosen"] != "premZ"
    assert result["chosen"] == "stdY"
    assert all(m != "premZ" for m in result["band_kept"])
    assert all(m != "premZ" for m in result["ranking"])
    assert result["reasons"]["premZ"]["reason"] == "excluded: premium"


@test
def rendered_agents_deny_external_channels_and_questions():
    reg = roles_mod.load()
    for name, text in roles_mod.rendered_agents(reg).items():
        assert '"gh pr comment*": "deny"' in text, name
        assert '"curl -X POST*": "deny"' in text, name
        assert 'question: "deny"' in text, name
        assert "GENERATED" in text
    ra = roles_mod.render_agent("reviewer-a", reg)
    assert 'edit: "deny"' in ra and '"git push*": "deny"' in ra
    imp = roles_mod.render_agent("implementer", reg)
    assert 'edit: "allow"' in imp and "Headless worker contract" in imp and "never wait" in imp
    inter = roles_mod.render_agent("interactive", reg)
    assert "Headless worker contract" not in inter and "never does the work" in inter.lower() or "never do the work" in inter


@test
def drift_guard_detects_hand_edits():
    with Estate() as E:
        roles_mod.write_agents(E.agents)
        assert roles_mod.drift(E.agents) == []
        f = E.agents / "pl-implementer.md"
        f.write_text(f.read_text() + "\n# hand edit\n")
        assert roles_mod.drift(E.agents) == [str(f)]
        r = E.cli("check-agents")
        assert r.returncode == 1 and "DRIFT" in r.stdout
        E.cli("render-agents", check=True)
        assert E.cli("check-agents").returncode == 0


@test
def worker_contract_names_every_clause():
    c = roles_mod.worker_contract()
    for needle in ("non-interactive", "Never ask", "never wait", "filesystem is the oracle", "End stdout with the deliverable",
                   "engine runs EXIT", "refused", "Scope fences", "external channels", "Ceremony is exact"):
        assert needle in c, needle


# ---------------------------------------------------------------- journal / registry / util

@test
def journal_state_derivation():
    rows = [
        {"event": "run.open", "plan": "p", "cwd": "c", "conversation": "ses_1", "pid": 1, "t": 1},
        {"event": "phase.start", "phase": "1", "role": "implementer", "attempt": 1, "t": 2},
        {"event": "dispatch.start", "id": "d1", "phase": "1", "role": "implementer", "model": "m", "pid": 99, "transcript": "/t", "t": 2},
        {"event": "dispatch.end", "id": "d1", "outcome": "ok", "wall_s": 3.0, "tokens": {"total": 10}, "cost": 0.1, "t": 5},
        {"event": "phase.fail", "phase": "1", "reason": "EXIT failed", "attempt": 1, "t": 6},
        {"event": "phase.start", "phase": "1", "role": "implementer", "attempt": 2, "t": 7},
        {"event": "phase.done", "phase": "1", "t": 8},
        {"event": "relight", "by": "sentry", "old_pid": 1, "new_pid": 2, "t": 9},
        {"event": "run.close", "outcome": "done", "t": 10},
    ]
    st = jmod.derive_state(rows)
    assert st["closed"] == "done" and not st["open"] and st["phases"]["1"]["status"] == "done"
    assert st["phases"]["1"]["attempts"] == 2 and st["tokens_total"] == 10 and abs(st["cost_total"] - 0.1) < 1e-9
    assert st["relights"] == 1 and st["engine_pid"] == 2 and st["open_dispatches"] == []
    st2 = jmod.derive_state(rows[:3])
    assert st2["open"] and len(st2["open_dispatches"]) == 1 and st2["phases"]["1"]["status"] == "running"


@test
def journal_tolerates_torn_tail():
    with Estate() as E:
        j = jmod.Journal("r1")
        j.write("run.open", plan="p")
        with open(j.path, "a") as f:
            f.write('{"event": "phase.start", "pha')
        assert len(j.rows()) == 1 and j.state()["open"]


@test
def storm_armor_retries_with_backoff():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("EIO blip")
        return "ok"

    os.environ["PIPELINE_BACKOFF_SCALE"] = "0.001"
    try:
        assert util.with_storm_armor(flaky, what="t") == "ok" and calls["n"] == 3
        try:
            util.with_storm_armor(lambda: (_ for _ in ()).throw(OSError("down")), what="t", schedule=(0.001, 0.001))
        except RuntimeError as e:
            assert "exhausted" in str(e)
        else:
            raise AssertionError("no exhaustion")
    finally:
        os.environ.pop("PIPELINE_BACKOFF_SCALE", None)
    # non-OSError passes through untouched
    try:
        util.with_storm_armor(lambda: 1 / 0, what="t")
    except ZeroDivisionError:
        pass
    assert util.BACKOFF_SCHEDULE[-1] >= 300 and sum(util.BACKOFF_SCHEDULE) >= 600  # minutes-scale


@test
def liveness_is_process_plus_transcript_never_journal():
    with Estate() as E:
        t = E.tmp / "tr.jsonl"
        t.write_text("x")
        lv = util.liveness(os.getpid(), t, stall_after=100)
        assert lv["alive"] and not lv["stalled"]
        old = time.time() - 1000
        os.utime(t, (old, old))
        lv = util.liveness(os.getpid(), t, stall_after=100)
        assert lv["alive"] and lv["stalled"]
        lv = util.liveness(999999, t, stall_after=100)
        assert not lv["alive"] and not lv["stalled"]
        # journal says running, process is gone -> status says corpse
        j = jmod.Journal("corpse")
        j.write("run.open", plan="p", cwd="c", conversation="ses", pid=999999)
        j.write("phase.start", phase="1", role="implementer", attempt=1)
        rep = status.run_report("corpse")
        assert rep["verdict"] == "dead-engine" and rep["phases"]["1"]["status"] == "corpse"


@test
def fs_probe_reports_outage():
    with Estate() as E:
        assert util.fs_probe(E.estate / "state")
        assert not util.fs_probe(Path("/proc/nonexistent-dir/x"))


@test
def runs_registry_keys_to_conversation_and_scopes_custody():
    with Estate() as E:
        registry.register("plan", "runA", journal=Path("/j"), conversation="ses_A")
        registry.register("quick", "runB", journal=Path("/j"), conversation="ses_B")
        os.environ["PIPELINE_CONVERSATION"] = "ses_A"
        registry.register("plan", "runC", journal=Path("/j"))
        assert {r["run"] for r in registry.for_conversation("ses_A")} == {"runA", "runC"}
        assert {r["run"] for r in registry.for_conversation("ses_B")} == {"runB"}
        # pasted output from another session is context, never custody
        for rid in ("runA", "runB", "runC"):
            jmod.Journal(rid).write("run.open", plan="p", cwd="c", conversation="x", pid=1)
        mine = status.all_reports("ses_A")
        assert {r["run"] for r in mine} == {"runA", "runC"}
        assert registry.lookup("runB")["conversation"] == "ses_B"


# ---------------------------------------------------------------- engine core

@test
def engine_runs_phase_and_engine_runs_exit():
    with Estate() as E:
        rid, p, r, st = run_plan(E, """## Phase 1: make (implementer)
EXIT: test -f made.txt
FAKE: touch made.txt
""")
        assert r.returncode == 0, r.stderr
        assert st["closed"] == "done" and st["phases"]["1"]["status"] == "done"
        rows = jmod.Journal(rid).rows()
        ex = [x for x in rows if x["event"] == "exit.check"]
        assert ex and ex[0]["ok"] and ex[0]["predicate"] == "test -f made.txt"
        d = [x for x in rows if x["event"] == "dispatch.end"]
        assert d[0]["tokens"]["total"] == 1234 and d[0]["outcome"] == "ok" and d[0]["wall_s"] >= 0
        calls = E.fake_calls()
        assert calls[0]["agent"] == "pl-implementer" and calls[0]["auto"]
        assert "EXIT predicates" in calls[0]["brief"] and "test -f made.txt" in calls[0]["brief"]


@test
def exit_failure_feeds_retry_with_failure_text_then_burns():
    with Estate() as E:
        rid, p, r, st = run_plan(E, """## Phase 1: make (implementer)
EXIT: test -f made.txt && echo present || { echo "made.txt MISSING from $(pwd)"; false; }
FAKE: touch-if-retry made.txt
""")
        assert st["closed"] == "done"
        calls = E.fake_calls()
        assert len(calls) == 2 and not calls[0]["retry"] and calls[1]["retry"]
        assert "made.txt MISSING" in calls[1]["brief"] and "EXIT failed" in calls[1]["brief"]
        # never passes -> burned -> deliberate stop, receipt written
        rid, p, r, st = run_plan(E, """## Phase 1: never (implementer)
EXIT: false
FAKE: touch x
""", sub="q")
        assert r.returncode == 2 and st["closed"] == "stopped" and st["stopped"] == "burned"
        assert (E.estate / "runs" / rid / "STOPPED").exists()
        assert st["phases"]["1"]["attempts"] == 2


@test
def worker_failure_also_retries():
    with Estate() as E:
        rid, p, r, st = run_plan(E, """## Phase 1: flaky (fast-worker)
FAKE: fail-unless-retry
""")
        assert st["closed"] == "done"
        calls = E.fake_calls()
        assert len(calls) == 2 and "worker outcome failed" in calls[1]["brief"]


@test
def after_runs_independent_phases_concurrently():
    with Estate() as E:
        rid, p, r, st = run_plan(E, """## Phase 1: a (fast-worker)
FAKE: sleep 1.0
FAKE: touch a

## Phase 2: b (fast-worker)
AFTER: 1
FAKE: sleep 1.0
FAKE: touch b

## Phase 3: c (fast-worker)
AFTER: 1
FAKE: sleep 1.0
FAKE: touch c

## Phase 4: d (fast-worker)
AFTER: 2, 3
EXIT: test -f a -a -f b -a -f c
FAKE: touch d
""")
        assert st["closed"] == "done", r.stderr
        ph = st["phases"]
        # 2 and 3 overlap; 4 starts after both end
        assert ph["3"]["started_at"] < ph["2"]["done_at"] and ph["2"]["started_at"] < ph["3"]["done_at"]
        assert ph["4"]["started_at"] >= max(ph["2"]["done_at"], ph["3"]["done_at"])
        assert ph["2"]["started_at"] >= ph["1"]["done_at"]


@test
def sequence_default_never_overlaps():
    with Estate() as E:
        rid, p, r, st = run_plan(E, """## Phase 1: a (fast-worker)
FAKE: sleep 0.5

## Phase 2: b (fast-worker)
FAKE: sleep 0.5
""")
        ph = st["phases"]
        assert ph["2"]["started_at"] >= ph["1"]["done_at"]


@test
def timeout_kills_worker_and_counts_as_attempt():
    with Estate() as E:
        rid, p, r, st = run_plan(E, """## Phase 1: hang (fast-worker)
TIMEOUT: 1
ATTEMPTS: 1
FAKE: hang
""", timeout=40)
        assert st["closed"] == "stopped" and st["stopped"] == "burned"
        d = [x for x in jmod.Journal(rid).rows() if x["event"] == "dispatch.end"]
        assert d[0]["outcome"] == "timeout"
        assert not util.pid_alive([x for x in jmod.Journal(rid).rows() if x["event"] == "dispatch.start"][0]["pid"])


@test
def lanes_fan_out_with_ceiling_and_per_lane_exit():
    with Estate() as E:
        items = E.work / "p" / "items.txt"
        items.parent.mkdir(parents=True, exist_ok=True)
        items.write_text("alpha\nbeta\n# comment\ngamma\ndelta\n")
        rid, p, r, st = run_plan(E, """## Phase 1: fan (lane-worker)
LANES: items.txt
CEILING: 2
EXIT: test -f "$LANE_OUT/r.txt" && grep -q "$ITEM" "$LANE_OUT/r.txt"
FAKE: sleep 0.6
FAKE: write $LANE_OUT/r.txt <<item=$ITEM lane=$LANE>>
""", timeout=60)
        assert st["closed"] == "done", r.stderr
        calls = E.fake_calls()
        assert sorted(c["ITEM"] for c in calls) == ["alpha", "beta", "delta", "gamma"]
        assert all(c["LANE_OUT"] and c["LANE"] is not None for c in calls)
        ds = [x for x in jmod.Journal(rid).rows() if x["event"] in ("dispatch.start", "dispatch.end")]
        starts = sorted(x["t"] for x in ds if x["event"] == "dispatch.start")
        ends = sorted(x["t"] for x in ds if x["event"] == "dispatch.end")
        # ceiling 2: the third start waits for the first end
        assert starts[2] >= ends[0] - 0.05
        ex = [x for x in jmod.Journal(rid).rows() if x["event"] == "exit.check"]
        assert len(ex) == 4 and all(x["ok"] for x in ex) and {x["lane"] for x in ex} == {0, 1, 2, 3}
        # lanes have ITEM/LANE_OUT in the brief
        assert "LANE_OUT:" in calls[0]["brief"] and "ITEM:" in calls[0]["brief"]


@test
def lane_failure_retries_only_failed_lanes():
    with Estate() as E:
        items = E.work / "p" / "items.txt"
        items.parent.mkdir(parents=True, exist_ok=True)
        items.write_text("good\nbad\n")
        rid, p, r, st = run_plan(E, """## Phase 1: fan (lane-worker)
LANES: items.txt
EXIT: test -f "$LANE_OUT/ok"
FAKE: touch $LANE_OUT/ok-good-only-$ITEM
FAKE: touch-if-retry $LANE_OUT/ok
""", timeout=60)
        # first pass: both lanes fail EXIT (no `ok`); retry: both get `ok`. Total dispatches 4.
        assert st["closed"] == "done"
        calls = E.fake_calls()
        assert len(calls) == 4
        # lane.done rows recorded so a resume skips them
        assert len([x for x in jmod.Journal(rid).rows() if x["event"] == "lane.done"]) == 2


@test
def gate_waits_for_sentinel_and_records_waiting():
    with Estate() as E:
        p = E.plan("""## Phase 1: go (gate)
GATE: .go

## Phase 2: after (fast-worker)
EXIT: test -f after
FAKE: touch after
""")
        rid = "gate1"
        (E.estate / "runs" / rid).mkdir(parents=True)
        (E.estate / "runs" / rid / "plan.path").write_text(str(p))
        proc = subprocess.Popen([sys.executable, "-m", "pipeline.cli", "engine", rid, str(p)], env=os.environ, cwd=str(E.work),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        wait_for(lambda: jmod.Journal(rid).state()["phases"].get("1", {}).get("status") == "waiting")
        rep = status.run_report(rid)
        assert rep["waiting_on_operator"] and ".go" in rep["waiting_on_operator"][0]
        text = status.render([rep], scope="t")
        assert "Waiting on you:" in text and ".go" in text and text.splitlines()[0].endswith("(t).")
        time.sleep(0.5)
        assert jmod.Journal(rid).state()["phases"].get("2") is None  # nothing dispatched behind the gate
        (p.parent / ".go").write_text("yes")
        proc.wait(timeout=30)
        st = jmod.Journal(rid).state()
        assert st["closed"] == "done" and st["phases"]["2"]["status"] == "done"
        assert not E.fake_calls()[0]["agent"].endswith("gate")  # gate dispatched nothing


@test
def gate_rejection_is_deliberate_stop():
    with Estate() as E:
        p = E.plan("## Phase 1: go (gate)\nGATE: .go\n")
        (p.parent / ".go").write_text("no: not yet")
        rid, p, r, st = run_plan(E, p.read_text())
        assert st["stopped"] == "gate_failed" and st["closed"] == "stopped"


@test
def cross_review_two_families_blocking_stops():
    with Estate() as E:
        rid, p, r, st = run_plan(E, """## Phase 1: work (implementer)
REVIEW: cross
FAKE: touch out
FAKE: verdict-by-agent a=PASS b=CONCERNS
""")
        assert st["closed"] == "done", r.stderr
        rows = jmod.Journal(rid).rows()
        v = [x for x in rows if x["event"] == "review.verdict"]
        assert len(v) == 2 and {x["verdict"] for x in v} == {"PASS", "CONCERNS"}
        reg = roles_mod.load()
        assert len({roles_mod.family_of(x["model"], reg) for x in v}) == 2
        reviewers = [c for c in E.fake_calls() if c["agent"].startswith("pl-reviewer")]
        assert all(c["REVIEW_OUT"] for c in reviewers)
        # both BLOCKING -> deliberate stop
        rid, p, r, st = run_plan(E, """## Phase 1: work (implementer)
REVIEW: cross
FAKE: touch out
FAKE: verdict-by-agent a=BLOCKING b=BLOCKING
""", sub="q")
        assert st["stopped"] == "review_blocking"
        # one BLOCKING is not a stop
        rid, p, r, st = run_plan(E, """## Phase 1: work (implementer)
REVIEW: cross
FAKE: touch out
FAKE: verdict-by-agent a=BLOCKING b=PASS
""", sub="s")
        assert st["closed"] == "done"


@test
def review_runs_only_after_exit_passes():
    with Estate() as E:
        rid, p, r, st = run_plan(E, """## Phase 1: work (implementer)
ATTEMPTS: 1
EXIT: false
REVIEW: cross
FAKE: verdict PASS
""")
        assert st["stopped"] == "burned"
        assert not [c for c in E.fake_calls() if c["agent"].startswith("pl-reviewer")]


@test
def surface_scoring_and_one_rewrite_with_specifics():
    with Estate() as E:
        bad = ("In this document we will explore the pipeline!\n\n"
               "It is perhaps worth noting that the system, which was designed by the team over a period of several months during which many decisions were made and revisited and reconsidered, is powerful and can seamlessly leverage cutting-edge models.\n"
               "Maybe it is arguably somewhat good. It seems fine, kind of.\n")
        rid, p, r, st = run_plan(E, f"""## Phase 1: doc (document-writer)
SURFACE: README.md operator-doc
FAKE: write README.md <<{bad.strip().replace(chr(10), chr(92) + 'n')}>>
FAKE: fix-surface README.md
""")
        assert st["closed"] == "done", r.stderr
        calls = E.fake_calls()
        assert len(calls) == 2
        rewrite = calls[1]["brief"]
        assert "SURFACE scoring failed" in rewrite and "Failing metrics" in rewrite and "Offending lines" in rewrite
        assert "forbidden phrase 'seamlessly'" in rewrite and "exclamations" in rewrite and "preamble opener" in rewrite
        assert "limit" in rewrite  # metrics carry value vs limit
        rows = [x for x in jmod.Journal(rid).rows() if x["event"] == "surface.score"]
        assert rows[0]["pass"] is False and rows[-1]["pass"] is True and rows[-1]["rewrite"] is True
        # honest failure after one rewrite that does not fix it
        rid, p, r, st = run_plan(E, f"""## Phase 1: doc (document-writer)
ATTEMPTS: 1
SURFACE: README.md operator-doc
FAKE: write README.md <<{bad.strip().replace(chr(10), chr(92) + 'n')}>>
""", sub="q")
        assert st["stopped"] == "burned" and "SURFACE still failing after one rewrite" in st["stop_detail"]
        assert len([c for c in E.fake_calls() if "surface rewrite" in c["brief"]]) == 2  # exactly one per attempt


@test
def surface_scorer_metrics():
    reg = surfmod.load_register("operator-doc")
    good = "The engine lands work from plans. Each phase names one role. EXIT predicates run after the worker stops. " * 4
    res = surfmod.score(good, reg)
    assert res["pass"], res
    bad = surfmod.score("Welcome! This document will seamlessly explore things. It was written by us.", reg)
    assert not bad["pass"]
    failing = {k for k, v in bad["metrics"].items() if not v["pass"]}
    assert {"exclamations", "forbidden_phrases", "first_sentence_states_outcome", "words"} <= failing
    rep = surfmod.failure_report([{**bad, "file": "f.md", "surface": "operator-doc"}])
    assert "f.md" in rep and "exclamations: 1 (limit <= 0)" in rep and "L1:" in rep


@test
def surface_missing_file_fails_honestly():
    with Estate() as E:
        rid, p, r, st = run_plan(E, """## Phase 1: doc (document-writer)
ATTEMPTS: 1
SURFACE: nothing-*.md operator-doc
FAKE: touch other.txt
""")
        assert st["stopped"] == "burned"


@test
def plan_is_append_aware():
    with Estate() as E:
        p = E.plan("""## Phase 1: slow (fast-worker)
FAKE: sleep 1.5
FAKE: touch one
""")
        rid = "append1"
        (E.estate / "runs" / rid).mkdir(parents=True)
        (E.estate / "runs" / rid / "plan.path").write_text(str(p))
        proc = subprocess.Popen([sys.executable, "-m", "pipeline.cli", "engine", rid, str(p)], env=os.environ, cwd=str(E.work),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        wait_for(lambda: jmod.Journal(rid).state()["phases"].get("1"))
        with open(p, "a") as f:
            f.write("\n## Phase 2: added (fast-worker)\nEXIT: test -f one\nFAKE: touch two\n")
        proc.wait(timeout=30)
        st = jmod.Journal(rid).state()
        assert st["closed"] == "done" and st["phases"]["2"]["status"] == "done"
        assert (p.parent / "two").exists()


@test
def engine_resumes_idempotently_from_journal():
    with Estate() as E:
        p = E.plan("""## Phase 1: a (fast-worker)
FAKE: touch a

## Phase 2: b (fast-worker)
FAKE: touch b
""")
        rid = "resume1"
        (E.estate / "runs" / rid).mkdir(parents=True)
        (E.estate / "runs" / rid / "plan.path").write_text(str(p))
        j = jmod.Journal(rid)
        # simulate an engine that died after phase 1 with phase 2's dispatch open
        j.write("run.open", plan=str(p), cwd=str(p.parent), conversation="ses", pid=999999)
        j.write("phase.start", phase="1", role="fast-worker", attempt=1)
        j.write("phase.done", phase="1")
        j.write("phase.start", phase="2", role="fast-worker", attempt=1)
        j.write("dispatch.start", id="dX", phase="2", role="fast-worker", model="m", pid=999999, transcript="/none")
        r = E.engine_fg(rid, p)
        st = j.state()
        assert st["closed"] == "done"
        calls = E.fake_calls()
        assert len(calls) == 1 and not (p.parent / "a").exists() and (p.parent / "b").exists()  # phase 1 not redone
        rows = j.rows()
        assert any(x["event"] == "run.resume" for x in rows)
        assert [x for x in rows if x["event"] == "dispatch.end" and x["id"] == "dX"][0]["outcome"] == "killed"
        # relaunch on a closed run is a no-op
        r = E.engine_fg(rid, p)
        assert r.returncode == 0 and len(E.fake_calls()) == 1


@test
def engine_refuses_to_continue_a_deliberate_stop_until_cleared():
    with Estate() as E:
        rid, p, r, st = run_plan(E, """## Phase 1: never (fast-worker)
ATTEMPTS: 1
EXIT: test -f later
""")
        assert st["stopped"] == "burned"
        r = E.engine_fg(rid, p)
        assert r.returncode == 3 and len(E.fake_calls()) == 1
        (p.parent / "later").write_text("x")
        pid = engine.relaunch(rid, by="operator", cleared=True)
        wait_for(lambda: jmod.Journal(rid).state()["closed"] == "done", timeout=30)
        assert not (E.estate / "runs" / rid / "STOPPED").exists()


@test
def interrupted_attempt_is_not_charged_against_budget():
    with Estate() as E:
        p = E.plan("## Phase 1: a (fast-worker)\nATTEMPTS: 1\nEXIT: test -f a\nFAKE: touch a\n")
        rid = "interrupted1"
        (E.estate / "runs" / rid).mkdir(parents=True)
        (E.estate / "runs" / rid / "plan.path").write_text(str(p))
        j = jmod.Journal(rid)
        # an engine died right after phase.start: no dispatch, no verdict
        j.write("run.open", plan=str(p), cwd=str(p.parent), conversation="ses", pid=999999)
        j.write("phase.start", phase="1", role="fast-worker", attempt=1)
        r = E.engine_fg(rid, p)
        st = j.state()
        assert st["closed"] == "done", r.stderr  # ATTEMPTS: 1 still had its one real attempt
        assert len(E.fake_calls()) == 1
        # and after a deliberate stop is cleared, a phase left running gets a fresh budget too
        rid2 = "interrupted2"
        (E.estate / "runs" / rid2).mkdir(parents=True)
        (E.estate / "runs" / rid2 / "plan.path").write_text(str(p))
        j2 = jmod.Journal(rid2)
        j2.write("run.open", plan=str(p), cwd=str(p.parent), conversation="ses", pid=999999)
        j2.write("phase.start", phase="1", role="fast-worker", attempt=1)
        j2.write("run.stop", reason="plan_invalid", detail="x")
        j2.write("run.close", outcome="stopped")
        j2.write("run.resume", pid=None, cleared=True, by="operator")
        assert j2.state()["phases"]["1"]["attempts"] == 0


@test
def engine_lock_prevents_double_engines():
    with Estate() as E:
        p = E.plan("## Phase 1: a (fast-worker)\nFAKE: sleep 1.5\n")
        rid = "lock1"
        (E.estate / "runs" / rid).mkdir(parents=True)
        (E.estate / "runs" / rid / "plan.path").write_text(str(p))
        a = subprocess.Popen([sys.executable, "-m", "pipeline.cli", "engine", rid, str(p)], env=os.environ, cwd=str(E.work), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        wait_for(lambda: jmod.Journal(rid).state()["open"])
        b = E.engine_fg(rid, p)
        assert "already running" in b.stderr
        a.wait(timeout=30)
        assert len(E.fake_calls()) == 1


@test
def fallback_model_is_on_another_family_and_used_on_failure():
    with Estate() as E:
        reg = roles_mod.load()
        s = roles_mod.seat("implementer", reg)
        rid, p, r, st = run_plan(E, f"""## Phase 1: a (implementer)
FAKE: fail-on-model {s['model']}
FAKE: touch a
""")
        assert st["closed"] == "done", r.stderr
        calls = E.fake_calls()
        assert calls[0]["model"] == s["model_q"] and calls[1]["model"] == s["fallback_q"]
        assert s["family"] != s["fallback_family"]
        ds = [x for x in jmod.Journal(rid).rows() if x["event"] == "dispatch.start"]
        assert ds[1]["fallback"] is True


@test
def quota_outcome_falls_to_quota_fallback_not_family_fallback():
    """A 402 premium-tier quota transcript sends the retry to the seat's
    quota_fallback (never the family fallback), and a quota hit alone does not
    count as a phase attempt failure: if the fallback succeeds, the phase lands."""
    with Estate() as E:
        reg = roles_mod.load()
        s = roles_mod.seat("implementer", reg)
        rid, p, r, st = run_plan(E, f"""## Phase 1: a (implementer)
FAKE: quota-on-model {s['model']}
FAKE: touch a
""")
        assert st["closed"] == "done", r.stderr
        calls = E.fake_calls()
        assert calls[0]["model"] == s["model_q"]
        assert calls[1]["model"] == s["quota_fallback_model_q"]
        assert calls[1]["model"] != s["fallback_q"]
        ds = [x for x in jmod.Journal(rid).rows() if x["event"] == "dispatch.start"]
        assert ds[0].get("quota_fallback") in (False, None) and ds[1]["quota_fallback"] is True
        assert ds[1]["fallback"] is False
        de = [x for x in jmod.Journal(rid).rows() if x["event"] == "dispatch.end"]
        assert de[0]["outcome"] == "quota" and de[1]["outcome"] == "ok"
        # only one phase attempt was journaled: the quota hit did not burn one on its own
        assert st["phases"]["1"]["attempts"] == 1


@test
def brief_carries_task_facts_and_previous_failure_only():
    b = dsp.build_brief(task="Edit a.py; git add a.py only; commit 'fix: a'", role="implementer", cwd=Path("/w"), exits=["pytest -q"],
                        previous_failure="EXIT failed: pytest -q\n1 failed", preamble="ctx")
    assert "WORKING DIRECTORY: /w" in b and "git add a.py only" in b and "`pytest -q`" in b and "Attack this failure first" in b
    assert "Never ask" not in b  # discipline lives in the role prompt, not the brief


@test
def dispatch_transcript_parsing_sums_tokens():
    with Estate() as E:
        t = E.tmp / "t.jsonl"
        t.write_text("\n".join([
            json.dumps({"type": "step_finish", "sessionID": "s1", "part": {"tokens": {"input": 10, "output": 5, "reasoning": 1}, "cost": 0.5}}),
            "garbage line",
            json.dumps({"type": "text", "part": {"text": "final deliverable"}}),
            json.dumps({"type": "step_finish", "sessionID": "s1", "part": {"tokens": {"input": 20, "output": 5, "reasoning": 0}, "cost": 0.25}}),
        ]))
        p = dsp.parse_transcript(t)
        assert p["tokens"] == {"input": 30, "output": 10, "reasoning": 1, "total": 41} and abs(p["cost"] - 0.75) < 1e-9
        assert p["session_id"] == "s1" and p["final_text"] == "final deliverable"


@test
def quota_error_signature_detected_from_402_and_premium_wording():
    """is_quota_error requires BOTH statusCode 402 AND 'premium-tier' or 'allowance' in
    the text; either alone is not enough."""
    hit = json.dumps({"error": {"data": {"statusCode": 402, "message": "You\u2019ve used your weekly allowance for premium-tier models"}}})
    assert dsp.is_quota_error(hit)
    hit2 = json.dumps({"error": {"data": {"statusCode": 402, "message": "Redeem a Reset Pass; use any standard model. premium-tier caps apply."}}})
    assert dsp.is_quota_error(hit2)
    assert not dsp.is_quota_error("")
    assert not dsp.is_quota_error(json.dumps({"statusCode": 500, "message": "premium-tier allowance exceeded"}))  # wrong code
    assert not dsp.is_quota_error(json.dumps({"statusCode": 402, "message": "rate limited, try later"}))  # no signature words
    with Estate() as E:
        t = E.tmp / "quota.jsonl"
        t.write_text(json.dumps({"type": "error", "part": {"error": {"data": {
            "message": "You've used your weekly allowance for premium-tier models on the max plan.",
            "statusCode": 402}}}}))
        p = dsp.parse_transcript(t)
        assert p["quota"] is True and p["steps"] == 0


@test
def dispatch_outcome_is_quota_when_402_and_no_step_completed():
    with Estate() as E:
        reg = roles_mod.load()
        s = roles_mod.seat("implementer", reg)
        out_dir = E.tmp / "d1"
        res = dsp.run_dispatch(brief="FAKE: quota\n", role="implementer", cwd=E.work, out_dir=out_dir,
                               timeout=10, model=s["model_q"])
        assert res.outcome == "quota", res.as_row()
        assert res.tokens["total"] == 0


# ---------------------------------------------------------------- launchers

@test
def run_launches_detached_and_registers_to_conversation():
    with Estate() as E:
        p = E.plan("## Phase 1: a (fast-worker)\nEXIT: test -f a\nFAKE: touch a\n")
        os.environ["PIPELINE_CONVERSATION"] = "ses_launch"
        r = E.cli("run", str(p), check=True)
        rid = r.stdout.split()[1]
        row = registry.lookup(rid)
        assert row["conversation"] == "ses_launch" and row["kind"] == "plan"
        wait_for(lambda: jmod.Journal(rid).state()["closed"] == "done", timeout=30)
        st = jmod.Journal(rid).state()
        assert st["conversation"] == "ses_launch"
        # detached: engine's session id != launcher's (setsid) -- verified via /proc while alive is racy; check pid differs and log exists
        assert (E.estate / "runs" / rid / "engine.log").exists()
        out = E.cli("conv", "ses_launch").stdout
        assert rid in out or "done" in out.splitlines()[0]
        assert "Waiting on you:" in out


@test
def quick_is_one_dispatch_with_role_and_optional_exit():
    with Estate() as E:
        r = E.cli("quick", "-r", "fast-worker", "-x", "test -f q.txt", input="fetch the thing\nFAKE: touch q.txt\n", check=True)
        rid = r.stdout.split()[1]
        assert rid.startswith("q_")
        wait_for(lambda: jmod.Journal(rid).state()["closed"] == "done", timeout=30)
        calls = E.fake_calls()
        assert len(calls) == 1 and calls[0]["agent"] == "pl-fast-worker" and calls[0]["cwd"] == str(E.work)
        assert registry.lookup(rid)["kind"] == "quick"
        r = E.cli("quick", "-r", "fast-worker", input="")
        assert r.returncode == 2
        r = E.cli("quick", "-r", "nope", input="x")
        assert r.returncode != 0


@test
def finisher_lands_exact_commit_and_exit_proves_it():
    with Estate() as E:
        repo = E.work
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init"], cwd=repo, check=True)
        (repo / "a.txt").write_text("work done\n")
        (repo / "stray.txt").write_text("outside fence\n")
        task, exits = finisher.finisher_brief(subject="feat: land a", add_paths=["a.txt"], verify="grep -q 'work done' a.txt")
        assert "git add -- a.txt" in task and "feat: land a" in task
        assert exits[0].startswith("git log -1 --format=%s | grep -qxF") and "grep -q 'work done' a.txt" in exits
        # first run: fake does not commit -> EXIT fails -> retry -> still fails -> burned (honest)
        os.environ["GIT_AUTHOR_NAME"] = os.environ["GIT_COMMITTER_NAME"] = "t"
        os.environ["GIT_AUTHOR_EMAIL"] = os.environ["GIT_COMMITTER_EMAIL"] = "t@t"
        r = E.cli("finish", "--subject", "feat: land a", "--paths", "a.txt", "--verify", "grep -q 'work done' a.txt", check=True)
        rid = r.stdout.split()[2]
        wait_for(lambda: jmod.Journal(rid).state()["closed"], timeout=40)
        assert jmod.Journal(rid).state()["stopped"] == "burned"
        # now simulate the finisher doing the ceremony, then EXIT proves it
        subprocess.run(["git", "add", "--", "a.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "feat: land a"], cwd=repo, check=True)
        for e in exits:
            ok, out = engine.run_predicate(e, cwd=repo)
            assert ok, (e, out)
        assert "stray.txt" in subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout


# ---------------------------------------------------------------- sentry

@test
def sentry_relights_dead_engine_max_three_and_skips_deliberate_stops():
    with Estate() as E:
        p = E.plan("## Phase 1: a (fast-worker)\nEXIT: test -f go\nFAKE: touch a\n")
        rid = "dead1"
        rdir = E.estate / "runs" / rid
        rdir.mkdir(parents=True)
        (rdir / "plan.path").write_text(str(p))
        j = jmod.Journal(rid)
        j.write("run.open", plan=str(p), cwd=str(p.parent), conversation="ses", pid=999999)
        j.write("phase.start", phase="1", role="fast-worker", attempt=1)
        s = sentry.tick()
        assert s["probe"] and len(s["relit"]) == 1
        new_pid = s["relit"][0][1]
        wait_for(lambda: j.state()["closed"] is not None, timeout=30)  # relit engine runs, burns (EXIT false) -> deliberate stop
        assert j.state()["stopped"] == "burned" and (rdir / "STOPPED").exists()
        s = sentry.tick()
        assert s["relit"] == [] and rid in s["skipped_stopped"]  # never auto-restart a deliberate stop
        # budget: 3 relights per window
        rid = "dead2"
        rdir = E.estate / "runs" / rid
        rdir.mkdir(parents=True)
        (rdir / "plan.path").write_text(str(E.plan("## Phase 1: g (gate)\n", sub="g")))
        j = jmod.Journal(rid)
        j.write("run.open", plan="x", cwd=str(p.parent), conversation="ses", pid=999999)
        for i in range(3):
            j.write("relight", by="sentry", old_pid=1, new_pid=999999)
        s = sentry.tick()
        assert s["relit"] == []
        assert any(r["event"] == "relight.exhausted" for r in j.rows())


@test
def sentry_stands_down_on_fs_outage():
    with Estate() as E:
        os.environ["PIPELINE_ESTATE"] = "/proc/no-such-estate"
        try:
            s = sentry.tick()
        finally:
            os.environ["PIPELINE_ESTATE"] = str(E.estate)
        assert s["outage"] and not s["probe"] and s["relit"] == []


@test
def sentry_recovers_stalled_worker_once_per_phase():
    with Estate() as E:
        p = E.plan("## Phase 1: hang (fast-worker)\nTIMEOUT: 60\nATTEMPTS: 1\nFAKE: hang\n")
        rid = "stall1"
        (E.estate / "runs" / rid).mkdir(parents=True)
        (E.estate / "runs" / rid / "plan.path").write_text(str(p))
        proc = subprocess.Popen([sys.executable, "-m", "pipeline.cli", "engine", rid, str(p)], env=os.environ, cwd=str(E.work), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        j = jmod.Journal(rid)
        wait_for(lambda: j.state()["open_dispatches"])
        d = j.state()["open_dispatches"][0]
        time.sleep(2.5)  # exceed PIPELINE_STALL_S=2 with no transcript movement
        s = sentry.tick()
        assert s["recovered"] == [(rid, d["id"])]
        proc.wait(timeout=30)
        st = j.state()
        assert st["stopped"] == "burned" and st["recoveries"] == {"1": 1}
        assert [x for x in j.rows() if x["event"] == "dispatch.end"][0]["outcome"] == "killed"
        assert "killed by signal" in st["phases"]["1"]["last_reason"]  # the retry prompt carries the stall text
        s = sentry.tick()
        assert s["recovered"] == []


@test
def sentry_never_restarts_config_mismatch_or_review_blocking():
    with Estate() as E:
        for reason in ("config_mismatch", "review_blocking", "gate_failed", "burned"):
            rid = "stop-" + reason
            rdir = E.estate / "runs" / rid
            rdir.mkdir(parents=True)
            (rdir / "plan.path").write_text("/x")
            j = jmod.Journal(rid)
            j.write("run.open", plan="x", cwd="/", conversation="s", pid=999999)
            j.write("run.stop", reason=reason, detail="d")
            j.write("run.close", outcome="stopped")
        s = sentry.tick()
        assert s["relit"] == []


@test
def config_mismatch_is_deliberate_stop():
    with Estate() as E:
        roles_mod.write_agents(E.agents)
        f = E.agents / "pl-fast-worker.md"
        f.write_text(f.read_text() + "\n# drift\n")
        p = E.plan("## Phase 1: a (fast-worker)\nFAKE: touch a\n")
        rid = "cfg1"
        (E.estate / "runs" / rid).mkdir(parents=True)
        del os.environ["PIPELINE_WORKER_BIN"]  # the drift check is skipped under the fake worker; force it
        try:
            r = E.engine_fg(rid, p)
        finally:
            os.environ["PIPELINE_WORKER_BIN"] = str(FAKE)
        st = jmod.Journal(rid).state()
        assert st["stopped"] == "config_mismatch" and r.returncode == 2


# ---------------------------------------------------------------- status / bench / trial

@test
def status_first_sentence_and_waiting_list_always_present():
    text = status.render([], scope="conversation ses_x")
    assert text.splitlines()[0] == "No runs recorded for conversation ses_x."
    assert "Waiting on you:\n  nothing" in text
    with Estate() as E:
        rid, p, r, st = run_plan(E, "## Phase 1: a (fast-worker)\nFAKE: touch a\n")
        rep = status.run_report(rid)
        assert rep["verdict"] == "done" and rep["tokens_total"] == 1234
        text = status.render([rep], scope="all runs")
        assert text.startswith("Nothing in flight; all 1 run(s) done")
        assert "Waiting on you:\n  nothing" in text
        rid2, p2, r2, st2 = run_plan(E, "## Phase 1: a (fast-worker)\nATTEMPTS: 1\nEXIT: false\n", sub="q")
        reps = status.all_reports()
        text = status.render(reps, scope="all runs")
        assert text.startswith("Nothing in flight; 1 run(s) stopped and need judgment")
        assert "deliberate stop [burned]" in text
        out = E.cli("status", "--json").stdout
        assert json.loads(out)[0]["run"] in (rid, rid2)


@test
def status_names_premium_allowance_hit_once_in_waiting_list():
    with Estate() as E:
        reg = roles_mod.load()
        s = roles_mod.seat("implementer", reg)
        rid, p, r, st = run_plan(E, f"""## Phase 1: a (implementer)
FAKE: quota-on-model {s['model']}
FAKE: touch a
""")
        assert st["closed"] == "done", r.stderr
        rep = status.run_report(rid)
        hits = [w for w in rep["waiting_on_operator"] if "premium allowance hit" in w]
        assert len(hits) == 1, rep["waiting_on_operator"]
        assert s["model_q"] in hits[0] and "resets" in hits[0]
        text = status.render([rep], scope="all runs")
        assert text.count("premium allowance hit") == 1


@test
def bench_shows_premium_quota_bucket_and_per_model_hits():
    with Estate() as E:
        reg = roles_mod.load()
        s = roles_mod.seat("implementer", reg)
        rid, p, r, st = run_plan(E, f"""## Phase 1: a (implementer)
FAKE: quota-on-model {s['model']}
FAKE: touch a
""")
        assert st["closed"] == "done", r.stderr
        data = bench.collect()
        text = bench.render(data)
        assert "premium quota hits" in text
        assert "Premium quota hits by model" in text
        assert f"| {s['model_q']} |" in text.split("Premium quota hits by model")[1]


@test
def bench_is_generated_from_journal_with_regeneration_commands():
    with Estate() as E:
        run_plan(E, "## Phase 1: a (fast-worker)\nFAKE: touch a\n")
        run_plan(E, "## Phase 1: a (implementer)\nATTEMPTS: 1\nEXIT: false\n", sub="q")
        rid, p, r, st = run_plan(E, "## Phase 1: hang (fast-worker)\nTIMEOUT: 1\nATTEMPTS: 1\nFAKE: hang\n", sub="s", timeout=40)
        (E.estate / "state").mkdir(exist_ok=True)
        util.append_jsonl(E.estate / "state" / "suite.jsonl", {"ts": "now", "total": 40, "passed": 40, "failed": 0, "wall_s": 12.3, "floor": 40})
        data = bench.collect()
        text = bench.render(data)
        assert "dispatches: 3" in text and "ok: 2 (66.7%)" in text
        assert "machine failures (failed/timeout): 1" in text and "host-outage stalls" in text
        assert "tokens per landed outcome" in text and "| fast-worker |" in text and "| implementer |" in text
        assert "pipeline bench > BENCHMARKS.md" in text and "pipeline suite" in text
        assert "tests: 40 passed: 40" in text
        # no asserted numbers: every numeric line is derived; an empty estate says n/a
        import shutil
        shutil.rmtree(E.estate / "runs")
        text = bench.render(bench.collect())
        assert "dispatches: 0" in text and "n/a" in text


@test
def trial_is_a_plan_with_pinned_models_third_family_scorer_and_three_axis_decision():
    reg = roles_mod.load()
    tasks = trial.parse_tasks("## t1\ndo one\n## t2\ndo two\n")
    text = trial.trial_plan("implementer", ["gemini-3.1-pro-preview"], tasks, "score clarity 0-10", Path("/w"), reg)
    pl = planmod.parse_text(text, Path("/w/plan.md"))
    seat = roles_mod.seat("implementer", reg)
    cand_q = "llmgateway-devpass/gemini-3.1-pro-preview"
    # Phases 1 and 2 are task1-arm-A and task1-arm-B; models assigned randomly to arms
    arm_models = {pl.by_number(1).model, pl.by_number(2).model}
    assert arm_models == {seat["model_q"], cand_q}, f"arm models {arm_models} != expected pair"
    # Phases should also have EFFORT: pinned
    assert pl.by_number(1).effort is not None, "arm phase should have EFFORT pinned"
    assert pl.by_number(3).after == [1] and pl.by_number(4).after == [2]
    scorer = pl.by_number(5)
    fam = roles_mod.family_of(reg["roles"][scorer.role]["model"], reg)
    assert fam not in (seat["family"], "google") and scorer.after == [1, 2, 3, 4] and scorer.exits
    assert pl.decisions and "nominated; trial decides" in pl.decisions[0]
    # Blinding: scorer phase brief must not name models or reveal arm identities
    scorer_text = text.split(f"## Phase {scorer.number}:")[1]
    assert "incumbent" not in scorer_text, "scorer phase reveals 'incumbent'"
    assert "candidate" not in scorer_text, "scorer phase reveals 'candidate'"
    assert seat["model_q"].split("/")[-1] not in scorer_text, "scorer phase reveals incumbent model name"
    assert "gemini-3.1-pro-preview" not in scorer_text, "scorer phase reveals candidate model name"
    # Arm mapping is in a DECISION line with label -> "model_q@effort"
    arm_map = trial.extract_arm_map(text)
    assert set(arm_map.keys()) == {"A", "B"}
    # arm_map values are "model_q@effort" strings; check models are present
    arm_map_models = {v.rsplit("@", 1)[0] if "@" in v else v for v in arm_map.values()}
    assert arm_map_models == {seat["model_q"], cand_q}, f"arm_map models {arm_map_models} unexpected"
    with Estate() as E:
        (E.work / "trial").mkdir()
        # Scores use A/B keys; decide() translates via mapping.json
        # Use fake model names "inc" and "cand" for dispatch testing (legacy plain-model mapping)
        fake_map = {k: ("inc" if v.rsplit("@", 1)[0] == seat["model_q"] else "cand") for k, v in arm_map.items()}
        inc_arm = [k for k, v in fake_map.items() if v == "inc"][0]
        cand_arm = [k for k, v in fake_map.items() if v == "cand"][0]
        scores = {"t1": {inc_arm: 6, cand_arm: 8}, "t2": {inc_arm: 7, cand_arm: 8}}
        (E.work / "trial" / "scores.json").write_text(json.dumps(scores))
        (E.work / "trial" / "mapping.json").write_text(json.dumps(fake_map))
        st = {"dispatches": {
            "a": {"model": "inc", "outcome": "ok", "tokens": {"total": 1000}, "wall_s": 100, "cost": 0.05},
            "b": {"model": "cand", "outcome": "ok", "tokens": {"total": 1050}, "wall_s": 105, "cost": 0.06},
        }}
        v = trial.decide(E.work, st, incumbent_q="inc", candidate_q="cand")
        assert v["decision"] == "candidate" and v["reasons"] == []
        st["dispatches"]["b"]["tokens"]["total"] = 2000
        v = trial.decide(E.work, st, incumbent_q="inc", candidate_q="cand")
        assert v["decision"] == "incumbent" and any("tokens regressed" in r for r in v["reasons"])  # no axis eats another
        # apply refuses a lost trial and never writes the registry
        import shutil
        regp = E.tmp / "registry.json"
        shutil.copy(roles_mod.REGISTRY, regp)
        before = regp.read_text()
        try:
            trial.apply("implementer", "gemini-3.1-pro-preview", v, registry_path=regp)
        except roles_mod.RegistryError:
            pass
        else:
            raise AssertionError("applied a lost trial")
        assert regp.read_text() == before
        v["decision"], v["reasons"], v["chosen"] = "candidate", [], "llmgateway-devpass/gemini-3.1-pro-preview"
        trial.apply("implementer", "gemini-3.1-pro-preview", v, registry_path=regp)
        assert json.loads(regp.read_text())["roles"]["implementer"]["model"] == "gemini-3.1-pro-preview"


@test
def engine_pins_model_without_fallback_for_trial_arms():
    with Estate() as E:
        rid, p, r, st = run_plan(E, """## Phase 1: arm (implementer)
MODEL: llmgateway-devpass/gpt-5.4
ATTEMPTS: 1
FAKE: fail-on-model gpt-5.4
""")
        calls = E.fake_calls()
        assert len(calls) == 1 and calls[0]["model"] == "llmgateway-devpass/gpt-5.4" and st["stopped"] == "burned"


# ---------------------------------------------------------------- resilience / bootstrap

@test
def launch_detached_survives_parent_death():
    with Estate() as E:
        marker = E.tmp / "alive"
        script = f"import time,os; time.sleep(0.8); open({str(marker)!r},'w').write(str(os.getsid(0)))"
        parent = subprocess.Popen([sys.executable, "-c", f"""
import sys; sys.path.insert(0, {str(REPO)!r})
from pipeline.util import launch_detached
from pathlib import Path
print(launch_detached([sys.executable, '-c', {script!r}], log_path=Path({str(E.tmp / 'l.log')!r})), flush=True)
import os; os._exit(0)
"""], stdout=subprocess.PIPE, text=True)
        child_pid = int(parent.stdout.readline())
        parent.wait()
        wait_for(lambda: marker.exists(), timeout=10)
        assert int(marker.read_text()) == child_pid  # its own session leader (setsid)


@test
def bootstrap_is_sticky():
    with Estate() as E:
        home = E.tmp / "home"
        home.mkdir()
        env = {**os.environ, "HOME": str(home), "PATH": os.environ["PATH"]}
        env.pop("PIPELINE_ESTATE")
        env.pop("PIPELINE_AGENTS_DIR")
        r = subprocess.run(["bash", str(REPO / "bootstrap.sh"), f"--estate={E.tmp / 'estateA'}", "--no-sentry"], env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr + r.stdout
        assert (home / ".system" / "estate").read_text().strip() == str(E.tmp / "estateA")
        assert (home / ".system" / "bin" / "quick").exists() and (home / ".system" / "runs.jsonl").exists()
        assert (home / ".config" / "devpass-code" / "agent" / "pl-implementer.md").exists()
        assert (home / ".config" / "devpass-code" / "plugin" / "pipeline-conversation.js").exists()
        r = subprocess.run(["bash", str(REPO / "bootstrap.sh"), f"--estate={E.tmp / 'estateB'}", "--no-sentry"], env=env, capture_output=True, text=True)
        assert r.returncode == 3 and "sticky" in r.stderr
        assert (home / ".system" / "estate").read_text().strip() == str(E.tmp / "estateA")
        r = subprocess.run(["bash", str(REPO / "bootstrap.sh"), "--no-sentry"], env=env, capture_output=True, text=True)
        assert r.returncode == 0 and "sticky at" in r.stdout


@test
def no_bypass_flags_anywhere():
    import re
    src = "".join(p.read_text() for p in (REPO / "pipeline").glob("*.py")) + (REPO / "bootstrap.sh").read_text() + "".join(p.read_text() for p in (REPO / "bin").iterdir())
    for flag in ("--skip-exit", "--no-verify", "--force", "skip_exit", "SKIP_EXIT", "PIPELINE_SKIP", "--bypass", "--no-review", "--no-gate"):
        assert flag not in src, flag


@test
def select_band_keeps_faster_drops_much_worse():
    """Band rule: keeps arms within band of best, ranks by wall then cost; ties broken correctly."""
    arms = {
        "best": {"quality": 8.0, "wall_s": 20.0, "cost": 1.0},
        "faster_in_band": {"quality": 7.0, "wall_s": 10.0, "cost": 0.5},  # within 1.5 of 8.0, faster
        "too_bad": {"quality": 5.0, "wall_s": 5.0, "cost": 0.2},  # 3.0 below best, outside 1.5 band
    }
    result = trial.select(arms, 1.5)
    assert result["chosen"] == "faster_in_band", f"expected faster_in_band, got {result['chosen']}"
    assert set(result["band_kept"]) == {"best", "faster_in_band"}
    assert "too_bad" not in result["band_kept"]
    assert result["reasons"]["too_bad"]["excluded"] is True
    assert result["reasons"]["faster_in_band"]["excluded"] is False

    # Ties in wall broken by cost
    arms2 = {
        "a": {"quality": 9.0, "wall_s": 10.0, "cost": 2.0},
        "b": {"quality": 9.0, "wall_s": 10.0, "cost": 1.0},  # same wall, lower cost
        "c": {"quality": 9.0, "wall_s": 5.0, "cost": 3.0},   # faster wall, wins
    }
    result2 = trial.select(arms2, 1.5)
    assert result2["chosen"] == "c"
    assert result2["ranking"][0] == "c"
    assert result2["ranking"][1] == "b"  # wall tied with a, but lower cost
    assert result2["ranking"][2] == "a"

    # All numbers present in reasons
    for m in arms:
        assert "quality" in result["reasons"][m]
        assert "wall_s" in result["reasons"][m]
        assert "cost" in result["reasons"][m]


@test
def select_returns_best_in_band_non_premium_quota_fallback():
    """DECISION no-premium-seats: select(reg=...) excludes premium arms BEFORE banding —
    a premium arm can never be chosen even when it scores best. It still shows up in
    `reasons`, marked excluded: premium. `quota_fallback` is the next-best in-band
    non-premium arm; None (well, absent) when reg is absent, and select() raises when
    every arm is premium."""
    reg = {"models": {
        "provA": {"premium": True}, "provB": {"premium": False}, "provC": {"premium": False},
    }}
    arms = {
        "provA": {"quality": 8.0, "wall_s": 5.0, "cost": 0.5},   # best quality, but premium
        "provB": {"quality": 7.0, "wall_s": 10.0, "cost": 0.3},  # in band, standard, slower
        "provC": {"quality": 6.8, "wall_s": 8.0, "cost": 0.2},   # in band, standard, faster than B
    }
    result = trial.select(arms, 1.5, reg=reg)
    assert result["chosen"] == "provC"  # premium excluded before banding; fastest standard arm wins
    assert "provA" not in result["band_kept"]
    assert result["reasons"]["provA"]["excluded"] is True
    assert result["reasons"]["provA"]["reason"] == "excluded: premium"
    assert result["quota_fallback"] == "provB"  # next-best in-band non-premium arm after chosen
    # without reg, no premium filtering happens; premium arm can win, quota_fallback absent
    result_no_reg = trial.select(arms, 1.5)
    assert result_no_reg["chosen"] == "provA"
    assert result_no_reg.get("quota_fallback") is None
    # every arm premium -> nothing eligible to select
    reg_all_premium = {"models": {"provA": {"premium": True}, "provB": {"premium": True}, "provC": {"premium": True}}}
    try:
        trial.select(arms, 1.5, reg=reg_all_premium)
    except ValueError:
        pass
    else:
        raise AssertionError("select should refuse when every arm is premium")


@test
def select_reviewers_enforces_distinct_families():
    """select_reviewers must return 3 models on 3 distinct families; maximise in-band."""
    arms = {
        "claude-a": {"quality": 8.0, "wall_s": 10.0, "cost": 0.5},
        "gpt-b": {"quality": 7.5, "wall_s": 5.0, "cost": 0.3},
        "gemini-c": {"quality": 7.0, "wall_s": 8.0, "cost": 0.4},
        "deepseek-d": {"quality": 3.0, "wall_s": 3.0, "cost": 0.2},  # out of band
        "another-gpt": {"quality": 7.8, "wall_s": 4.0, "cost": 0.3},  # openai family, can't pair with gpt-b
    }
    families = {
        "claude-a": "anthropic",
        "gpt-b": "openai",
        "gemini-c": "google",
        "deepseek-d": "deepseek",
        "another-gpt": "openai",  # same family as gpt-b -> can't be in same triple
    }
    result = trial.select_reviewers(arms, 1.5, families)
    assert len(result) == 3
    fam_set = {families[m] for m in result}
    assert len(fam_set) == 3, f"not 3 distinct families: {fam_set} for {result}"
    # All 3 in-band candidates should be selected (claude, gpt-b or another-gpt, gemini)
    in_band_result = [m for m in result if arms[m]["quality"] >= 8.0 - 1.5]
    assert len(in_band_result) >= 2, f"expected at least 2 in-band in result, got {in_band_result}"
    # deepseek-d must not appear since there's a better triple that avoids it
    assert "deepseek-d" not in result, "should avoid out-of-band deepseek-d when better triple available"

    # With only 3 models on 3 distinct families, must return all 3
    arms3 = {
        "m1": {"quality": 7.0, "wall_s": 10.0, "cost": 0.5},
        "m2": {"quality": 6.0, "wall_s": 5.0, "cost": 0.3},
        "m3": {"quality": 5.0, "wall_s": 8.0, "cost": 0.4},
    }
    fam3 = {"m1": "anthropic", "m2": "openai", "m3": "google"}
    r3 = trial.select_reviewers(arms3, 1.5, fam3)
    assert set(r3) == {"m1", "m2", "m3"}

    # Fewer than 3 distinct families raises
    arms_bad = {"m1": {"quality": 7.0, "wall_s": 10.0, "cost": 0.5},
                "m2": {"quality": 6.0, "wall_s": 5.0, "cost": 0.3},
                "m3": {"quality": 5.0, "wall_s": 8.0, "cost": 0.4}}
    fam_bad = {"m1": "anthropic", "m2": "anthropic", "m3": "anthropic"}
    try:
        trial.select_reviewers(arms_bad, 1.5, fam_bad)
        raise AssertionError("should have raised ValueError for no distinct-family triple")
    except ValueError:
        pass


@test
def registry_bands_present():
    """Every role has a band field; bands are the expected values per role type."""
    reg = roles_mod.load()
    roles = reg["roles"]
    critical_roles = {"reviewer-a", "reviewer-b", "reviewer-c", "researcher"}
    standard_roles = {"implementer", "document-writer", "interactive", "frontend-worker"}
    tolerant_roles = {"fast-worker", "lane-worker"}
    for role_name, role_def in roles.items():
        assert "band" in role_def, f"role {role_name} missing band field"
        band = role_def["band"]
        assert band in trial.BANDS, f"role {role_name} has unknown band {band!r}"
        if role_name in critical_roles:
            assert band == "critical", f"expected critical for {role_name}, got {band}"
        elif role_name in standard_roles:
            assert band == "standard", f"expected standard for {role_name}, got {band}"
        elif role_name in tolerant_roles:
            assert band == "tolerant", f"expected tolerant for {role_name}, got {band}"
    # Specific spot-checks from the spec
    assert roles["implementer"]["band"] == "standard"
    assert roles["fast-worker"]["band"] == "tolerant"
    assert roles["reviewer-a"]["band"] == "critical"
    assert roles["frontend-worker"]["band"] == "standard"


@test
def effort_parses_and_rejects_bad_values():
    """EFFORT: low|medium|high is accepted; anything else raises PlanError."""
    for good in ("low", "medium", "high"):
        pl = planmod.parse_text(f"## Phase 1: a (implementer)\nEFFORT: {good}\n")
        assert pl.by_number(1).effort == good, f"expected effort={good!r}"
    # Bad value
    try:
        planmod.parse_text("## Phase 1: a (implementer)\nEFFORT: max\n")
    except planmod.PlanError:
        pass
    else:
        raise AssertionError("EFFORT: max should have raised PlanError")
    # Absent effort defaults to None
    pl2 = planmod.parse_text("## Phase 1: a (implementer)\n")
    assert pl2.by_number(1).effort is None


@test
def engine_passes_effort_to_worker():
    """When EFFORT: is set, the engine passes --variant with that value to the worker."""
    with Estate() as E:
        rid, p, r, st = run_plan(E, """## Phase 1: work (implementer)
EFFORT: low
FAKE: touch effort_test
""")
        assert st["closed"] == "done", r.stderr
        calls = E.fake_calls()
        assert calls[0]["variant"] == "low", f"expected variant=low, got {calls[0].get('variant')}"


@test
def parse_arm_splits_model_and_effort():
    """parse_arm('model@effort') -> ('model', 'effort'); bare model -> (model, None or reg effort)."""
    assert trial.parse_arm("gpt-5.6-luna@low") == ("gpt-5.6-luna", "low")
    assert trial.parse_arm("gpt-5.6-luna@medium") == ("gpt-5.6-luna", "medium")
    assert trial.parse_arm("gpt-5.6-luna@high") == ("gpt-5.6-luna", "high")
    # With provider prefix
    assert trial.parse_arm("provider/gpt-5.6-luna@low") == ("provider/gpt-5.6-luna", "low")
    # Bare model without effort: no role/reg -> effort is None
    model, effort = trial.parse_arm("gpt-5.6-luna")
    assert model == "gpt-5.6-luna" and effort is None


@test
def trial_apply_writes_effort():
    """apply() writes both model and effort to the registry when the arm includes @effort."""
    import shutil
    reg = roles_mod.load()
    regp = Path("/tmp/devpass-code/test_registry.json")
    regp.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(roles_mod.REGISTRY, regp)
    # Build a fake verdict with chosen = "llmgateway-devpass/gemini-3.1-pro-preview@low"
    chosen_arm = "llmgateway-devpass/gemini-3.1-pro-preview@low"
    verdict = {
        "chosen": chosen_arm,
        "decision": "candidate",
        "reasons": [],
        "decided_at": "now",
        "tolerance": 0.1,
        "band": 1.5,
        "arms": {},
        "selection": {},
        "quality": {},
        "tokens": {},
        "wall_s": {},
    }
    trial.apply("implementer", "gemini-3.1-pro-preview@low", verdict, registry_path=regp)
    after = json.loads(regp.read_text())
    assert after["roles"]["implementer"]["model"] == "gemini-3.1-pro-preview"
    assert after["roles"]["implementer"]["effort"] == "low"
    h = after["roles"]["implementer"]["history"][-1]
    assert h["model"] == "gemini-3.1-pro-preview" and h.get("effort") == "low"
    # apply refuses mismatched arm
    verdict2 = {**verdict, "chosen": "llmgateway-devpass/gemini-3.1-pro-preview@high"}
    try:
        trial.apply("implementer", "gemini-3.1-pro-preview@low", verdict2, registry_path=regp)
    except roles_mod.RegistryError:
        pass
    else:
        raise AssertionError("apply should refuse mismatched arm")


@test
def trial_apply_writes_quota_fallback_from_selection():
    """apply() writes verdict["selection"]["quota_fallback"] to the role's quota_fallback."""
    import shutil
    regp = Path("/tmp/devpass-code/test_registry_qf.json")
    regp.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(roles_mod.REGISTRY, regp)
    chosen_arm = "llmgateway-devpass/gemini-3.1-pro-preview@low"
    verdict = {
        "chosen": chosen_arm, "decision": "candidate", "reasons": [], "decided_at": "now",
        "tolerance": 0.1, "band": 1.5, "arms": {}, "quality": {}, "tokens": {}, "wall_s": {},
        "selection": {"quota_fallback": "llmgateway-devpass/gpt-5.6-luna@medium"},
    }
    trial.apply("implementer", "gemini-3.1-pro-preview@low", verdict, registry_path=regp)
    after = json.loads(regp.read_text())
    assert after["roles"]["implementer"]["quota_fallback"] == "gpt-5.6-luna@medium"
    h = after["roles"]["implementer"]["history"][-1]
    assert h.get("quota_fallback") == "gpt-5.6-luna@medium"


@test
def ratchet_ledger_never_goes_down():
    ledger = json.loads((REPO / "tests" / "ratchet.json").read_text())
    assert len(TESTS) >= ledger["min_tests"], f"{len(TESTS)} tests < ledger floor {ledger['min_tests']}: gates only ratchet"
    for name in ledger["required"]:
        assert name in {t.__name__ for t in TESTS}, f"required test {name} removed"
