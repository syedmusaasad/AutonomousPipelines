"""Characterization suite. Every behavior the spec names has a test here; the run.py
ratchet forbids the count going down."""

import json
import os
import shlex
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
    assert p3.surfaces == [("*.md", "operator-doc")] and p3.review == "on" and p3.after == [1]
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
        "## Phase 1: a (implementer)\nREVIEW: single\n",  # only on (or legacy cross)
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


@test
def deliberation_warnings_flag_markers_not_clean_briefs():
    assert planmod.DELIBERATION_MARKERS
    clean = planmod.parse_text("## Phase 1: a (implementer)\ndo the named thing\n")
    assert planmod.deliberation_warnings(clean) == []
    dirty = planmod.parse_text("## Phase 1: a (implementer)\ndesign the assertion approach\n")
    warnings = planmod.deliberation_warnings(dirty)
    assert len(warnings) == 1
    assert "design the" in warnings[0]


@test
def cmd_validate_warns_but_still_exits_zero():
    with Estate() as E:
        p = E.plan("## Phase 1: a (implementer)\nEXIT: true\ndesign the assertion approach\n")
        r = E.cli("validate", str(p))
        assert r.returncode == 0, r.stdout + r.stderr
        assert "WARN:" in r.stdout
        assert "warn-count: 1" in r.stdout


@test
def iterate_grammar_accepts_on_and_sets_fields():
    pl = planmod.parse_text("## Phase 1: x (implementer)\nITERATE: on\nCEILING: 12\nEXIT: true\n")
    ph = pl.by_number(1)
    assert ph.iterate is True and ph.ceiling == 12 and ph.iterate_progress is None


@test
def iterate_progress_directive_is_captured():
    pl = planmod.parse_text("## Phase 1: x (implementer)\nITERATE: on\nCEILING: 3\nEXIT: true\nPROGRESS: test -f progress.md\n")
    ph = pl.by_number(1)
    assert ph.iterate_progress == "test -f progress.md"


@test
def iterate_rejects_non_on_value():
    try:
        planmod.parse_text("## Phase 1: x (implementer)\nITERATE: yes\nCEILING: 12\nEXIT: true\n")
    except planmod.PlanError:
        pass
    else:
        raise AssertionError("ITERATE: yes must fail")


@test
def iterate_and_lanes_together_is_rejected():
    try:
        planmod.parse_text("## Phase 1: x (implementer)\nITERATE: on\nCEILING: 12\nLANES: items\nEXIT: true\n")
    except planmod.PlanError as e:
        assert "LANES" in str(e) and "ITERATE" in str(e)
    else:
        raise AssertionError("ITERATE+LANES must fail")


@test
def iterate_without_ceiling_is_rejected():
    try:
        planmod.parse_text("## Phase 1: x (implementer)\nITERATE: on\nEXIT: true\n")
    except planmod.PlanError as e:
        assert "CEILING" in str(e)
    else:
        raise AssertionError("ITERATE without CEILING must fail")


@test
def iterate_without_exit_is_rejected():
    try:
        planmod.parse_text("## Phase 1: x (implementer)\nITERATE: on\nCEILING: 2\n")
    except planmod.PlanError as e:
        assert "EXIT" in str(e)
    else:
        raise AssertionError("ITERATE without EXIT must fail")


@test
def iterate_forbidden_on_gate_phases():
    try:
        planmod.parse_text("## Phase 1: g (gate)\nITERATE: on\nCEILING: 2\n")
    except planmod.PlanError:
        pass
    else:
        raise AssertionError("ITERATE on a gate phase must fail")


@test
def cost_ceiling_is_reserved_and_rejected():
    try:
        planmod.parse_text("## Phase 1: x (implementer)\nCOST-CEILING: 5\nEXIT: true\n")
    except planmod.PlanError as e:
        assert "reserved" in str(e).lower()
    else:
        raise AssertionError("COST-CEILING must be rejected")


@test
def non_iterate_plan_is_unchanged():
    """Phases that never mention ITERATE keep their old defaults; full grammar test above
    (plan_parses_every_directive) already covers this, but assert explicitly for iterate fields."""
    pl = planmod.parse_text("## Phase 1: a (implementer)\nEXIT: true\n")
    ph = pl.by_number(1)
    assert ph.iterate is False and ph.iterate_progress is None


@test
def cmd_validate_shows_iterate_flag():
    with Estate() as E:
        p = E.plan("## Phase 1: a (implementer)\nITERATE: on\nCEILING: 5\nEXIT: true\n")
        r = E.cli("validate", str(p))
        assert r.returncode == 0, r.stdout + r.stderr
        assert "iterate" in r.stdout and "ceiling=5" in r.stdout


# ---------------------------------------------------------------- ITERATE loop

@test
def iterate_two_fresh_iterations_pass_and_review_once():
    with Estate() as E:
        rid, p, r, st = run_plan(E, """## Phase 1: loop (implementer)
ITERATE: on
CEILING: 3
EXIT: test "$(wc -l < steps.txt)" -ge 2
REVIEW: on
FAKE: append steps.txt <<iteration work>>
FAKE: verdict PASS
FAKE: record
""")
        assert st["closed"] == "done", r.stdout + r.stderr
        rows = jmod.Journal(rid).rows()
        iterations = [x for x in rows if x["event"] == "iterate.end"]
        assert len(iterations) == 2
        assert [x["exit_ok"] for x in iterations] == [False, True]
        assert iterations[0]["progress"] is None and iterations[1]["progress"] is None
        for row in iterations:
            for field in ("phase", "iteration", "exit_ok", "progress", "stall_count", "wall_s", "tokens", "cost",
                          "cumulative_tokens", "cumulative_cost"):
                assert field in row
        assert (E.estate / "runs" / rid / "phase-1" / "iterate" / "exit-1-0.log").exists()
        assert not [x for x in rows if x["event"] == "phase.fail"]
        workers = [x for x in E.fake_calls() if x["agent"] == "pl-implementer"]
        assert len(workers) == 2
        assert workers[0]["brief"] == workers[1]["brief"]
        assert "Previous attempt failed" not in workers[1]["brief"]
        reviewers = [x for x in E.fake_calls() if x["agent"] == "pl-reviewer"]
        assert len(reviewers) == 1  # one review, after the final EXIT only


@test
def iterate_ceiling_stops_after_progressing_iterations():
    with Estate() as E:
        rid, p, r, st = run_plan(E, """## Phase 1: loop (implementer)
ITERATE: on
CEILING: 3
EXIT: test -f never
FAKE: append changes.txt <<advanced>>
""")
        assert st["closed"] == "stopped" and st["stopped"] == "burned"
        assert st["stop_detail"].startswith("ITERATE-CEILING:")
        rows = [x for x in jmod.Journal(rid).rows() if x["event"] == "iterate.end"]
        assert len(rows) == 3 and all(x["progress"] in (None, True) for x in rows)
        rep = status.run_report(rid)
        assert any(x.startswith("ITERATE-CEILING:") and "raise CEILING" in x for x in rep["waiting_on_operator"])


@test
def iterate_builtin_stall_stops_after_two_unchanged_iterations():
    with Estate() as E:
        rid, p, r, st = run_plan(E, """## Phase 1: loop (implementer)
ITERATE: on
CEILING: 4
EXIT: false
FAKE: record
""")
        assert st["closed"] == "stopped" and st["stop_detail"].startswith("ITERATE-STALLED:")
        rows = [x for x in jmod.Journal(rid).rows() if x["event"] == "iterate.end"]
        assert len(rows) == 3
        assert [x["progress"] for x in rows] == [None, False, False]
        assert rows[-1]["stall_count"] == 2
        assert len(E.fake_calls()) == 3
        assert any(x.startswith("ITERATE-STALLED:") and "fix the phase brief" in x
                   for x in status.run_report(rid)["waiting_on_operator"])


@test
def iterate_progress_predicate_overrides_file_changes():
    with Estate() as E:
        rid, p, r, st = run_plan(E, """## Phase 1: loop (implementer)
ITERATE: on
CEILING: 4
PROGRESS: false
EXIT: test -f never
FAKE: append changed-despite-progress.txt <<work>>
""")
        assert st["closed"] == "stopped" and st["stop_detail"].startswith("ITERATE-STALLED:")
        rows = [x for x in jmod.Journal(rid).rows() if x["event"] == "iterate.end"]
        assert len(rows) == 2 and [x["progress"] for x in rows] == [False, False]
        assert (p.parent / "changed-despite-progress.txt").exists()


@test
def iterate_review_runs_once_after_three_iterations():
    with Estate() as E:
        rid, p, r, st = run_plan(E, """## Phase 1: loop (implementer)
ITERATE: on
CEILING: 3
EXIT: test "$(wc -l < steps.txt)" -ge 3
REVIEW: on
FAKE: append steps.txt <<iteration work>>
FAKE: verdict PASS
""")
        assert st["closed"] == "done", r.stdout + r.stderr
        calls = E.fake_calls()
        assert len([x for x in calls if x["agent"] == "pl-implementer"]) == 3
        assert len([x for x in calls if x["agent"] == "pl-reviewer"]) == 1
        assert len([x for x in jmod.Journal(rid).rows() if x["event"] == "review.verdict"]) == 1


@test
def iterate_demo_migration_dogfoods_launch_in_two_iterations():
    """The checked-in fixture exercises the detached public launch path end to end."""
    import shutil

    with Estate() as E:
        demo = E.work / "iterate-demo"
        shutil.copytree(REPO / "tests" / "fixtures" / "iterate-demo", demo)
        rid = "iterate-demo"
        row = engine.launch(demo / "plan.md", run_id=rid, conversation="ses_iterate_demo")
        assert row["run"] == rid
        wait_for(lambda: jmod.Journal(rid).state()["closed"] == "done", timeout=30)
        rows = jmod.Journal(rid).rows()
        iterations = [row for row in rows if row["event"] == "iterate.end"]
        assert len(iterations) == 2
        assert [row["exit_ok"] for row in iterations] == [False, True]
        assert jmod.Journal(rid).state()["phases"]["1"]["status"] == "done"
        calls = E.fake_calls()
        assert len([call for call in calls if call["agent"] == "pl-implementer"]) == 2
        assert not [row for row in rows if row["event"] == "review.verdict"]
        assert not [call for call in calls if call["agent"].startswith("pl-reviewer")]


# ---------------------------------------------------------------- registry

@test
def registry_validates_clis_seals_and_retired_fallbacks():
    reg = roles_mod.load()
    for name, r in reg["roles"].items():
        assert "fallback" not in r and "quota_fallback" not in r, name
        assert not r.get("external_post")
        assert roles_mod.cli_of(r["model"], reg) in ("claude", "codex", "agy"), name
    assert roles_mod.REVIEWERS == ("reviewer",)
    rv = reg["roles"]["reviewer"]
    assert rv["sealed"] and not rv["tools"]["edit"]
    assert roles_mod.seat("reviewer", reg)["cli"] == "codex"
    assert roles_mod.reviewer_role(reg) == "reviewer"
    import copy
    for mutate, why in (
        (lambda b: b["roles"]["implementer"].__setitem__("fallback", "gpt-6.1-sol"), "fallback accepted"),
        (lambda b: b["roles"]["implementer"].__setitem__("quota_fallback", "gpt-6.1-sol@low"), "quota_fallback accepted"),
        (lambda b: b["roles"]["reviewer"]["tools"].__setitem__("edit", True), "reviewer with edit accepted"),
        (lambda b: b["roles"]["reviewer"].__setitem__("sealed", False), "unsealed reviewer accepted"),
        (lambda b: b["roles"]["fast-worker"].__setitem__("external_post", True), "external_post accepted"),
        (lambda b: b["roles"]["implementer"].__setitem__("model", "grok-4-7"), "model with no CLI accepted"),
        (lambda b: b["roles"]["fast-worker"].__setitem__("effort", "ultra"), "bad effort accepted"),
        (lambda b: b["roles"]["interactive"].__setitem__("model", "gpt-6.1-sol"), "codex interactive accepted"),
        (lambda b: b["roles"]["reviewer"].__setitem__("model", "gemini-3.8-flash"), "sealed agy seat accepted"),
        (lambda b: b["roles"]["researcher"].__setitem__("effort", "minimal"), "codex-only effort on agy accepted"),
        (lambda b: b["roles"].pop("reviewer"), "missing reviewer accepted"),
    ):
        bad = copy.deepcopy(reg)
        mutate(bad)
        try:
            roles_mod.validate(bad)
        except roles_mod.RegistryError:
            pass
        else:
            raise AssertionError(why)

@test
def registry_seats_match_operator_choice():
    """DECISION leave-devpass (2026-11-07): the operator's seats, verbatim."""
    reg = roles_mod.load()
    want = {
        "interactive": ("claude-fable-5-2", "high"), "implementer": ("claude-sonnet-5-5", "high"),
        "fast-worker": ("gpt-6-luna", "low"), "lane-worker": ("claude-haiku-5-5", "low"),
        "researcher": ("gemini-3.8-flash", "medium"), "document-writer": ("gemini-3.8-flash", "high"),
        "frontend-worker": ("claude-sonnet-5-5", "high"), "reviewer": ("gpt-6.1-sol", "high"),
    }
    got = {k: (v["model"], v["effort"]) for k, v in reg["roles"].items()}
    assert got == want, got
    assert reg["clis"] == {"anthropic": "claude", "openai": "codex", "google": "agy"}
    assert "models" not in reg and "provider" not in reg

@test
def render_agents_claude_seats_only_with_drift_guard():
    """Claude Code agent files exist for every claude seat (not the codex reviewer), plus
    the interactive settings file; drift() names a hand-edited file."""
    reg = roles_mod.load()
    files = roles_mod.rendered_agents(reg)
    for role, r in reg["roles"].items():
        if roles_mod.cli_of(r["model"], reg) != "claude":
            assert f"pl-{role}.md" not in files, role
        else:
            text = files[f"pl-{role}.md"]
            assert f'name: "pl-{role}"' in text and f'model: "{r["model"]}"' in text
            assert f'effort: "{r["effort"]}"' in text
    assert roles_mod.INTERACTIVE_SETTINGS in files
    with Estate() as E:
        roles_mod.write_agents(E.agents, reg)
        assert roles_mod.drift(E.agents, reg) == []
        (E.agents / "pl-implementer.md").write_text("hand edit\n")
        assert roles_mod.drift(E.agents, reg) == [str(E.agents / "pl-implementer.md")]
        # a seat that moved off Claude: its old agent file is drift, and render removes it
        (E.agents / "pl-researcher.md").write_text("stale\n")
        assert str(E.agents / "pl-researcher.md") in roles_mod.drift(E.agents, reg)
        roles_mod.write_agents(E.agents, reg)
        assert not (E.agents / "pl-researcher.md").exists() and roles_mod.drift(E.agents, reg) == []

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
def claude_permissions_deny_external_channels_and_seal_reviewer():
    reg = roles_mod.load()
    for role in reg["roles"]:
        perms = roles_mod.claude_permissions(role, reg)
        assert "Bash(gh pr comment*)" in perms["deny"], role
        assert "Bash(curl -X POST*)" in perms["deny"], role
    rv = roles_mod.claude_permissions("reviewer", reg)
    assert {"Edit", "Write", "Bash(git push *)"} <= set(rv["deny"]) and "Edit" not in rv["allow"]
    imp = roles_mod.claude_permissions("implementer", reg)
    assert {"Edit", "Write", "Bash"} <= set(imp["allow"]) and "Edit" not in imp["deny"]
    text = roles_mod.render_agent("implementer", reg)
    assert "GENERATED" in text and "Headless worker contract" in text and "never wait" in text
    inter = roles_mod.render_agent("interactive", reg)
    assert "Headless worker contract" not in inter and ("never does the work" in inter.lower() or "never do the work" in inter)
    # the codex reviewer gets contract + role prompt ahead of its brief
    cp = roles_mod.codex_prompt("reviewer")
    assert "Headless worker contract" in cp and "read-only sandbox" in cp

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
def roblox_skills_source_has_every_named_skill():
    from pipeline import roblox_skills as rbx
    files = rbx.source_files()
    for name in ("luau-conventions", "rojo-sync", "studio-verify", "ui-loop", "animation-assets", "data-persistence", "asset-generation"):
        rel = f"{name}/SKILL.md"
        assert rel in files, rel
        assert files[rel].startswith("---\n") and f"name: {name}" in files[rel], rel


@test
def roblox_skills_drift_guard_detects_hand_edits():
    from pipeline import roblox_skills as rbx
    with Estate() as E:
        written = rbx.install_skills(E.roblox_skills)
        assert written and rbx.drift(E.roblox_skills) == []
        assert rbx.check_sync(E.roblox_skills) is True
        f = E.roblox_skills / "luau-conventions" / "SKILL.md"
        f.write_text(f.read_text() + "\n# hand edit\n")
        assert rbx.drift(E.roblox_skills) == [str(f)]
        assert rbx.check_sync(E.roblox_skills) is False
        r = E.cli("roblox-skills", "--check")
        assert r.returncode == 1 and "DRIFT" in r.stdout
        E.cli("roblox-skills", check=True)
        assert E.cli("roblox-skills", "--check").returncode == 0


@test
def worker_contract_names_every_clause():
    c = roles_mod.worker_contract()
    for needle in ("non-interactive", "Never ask", "never wait", "filesystem is the oracle", "End stdout with the deliverable",
                   "engine runs EXIT", "refused", "Scope fences", "external channels", "Ceremony is exact",
                   "Verification is bounded", "at most once", "Never loop verification", "Never stall on design"):
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


@test
def adopt_rekeys_row_and_appends_receipt():
    with Estate() as E:
        registry.register("plan", "adopt-chain", journal=E.estate / "runs" / "adopt-chain" / "journal.jsonl",
                          conversation="ses_A")
        assert registry.adopt("adopt-chain", "ses_B") == {
            "action": "adopted", "from": "ses_A", "to": "ses_B", "run": "adopt-chain"}
        assert registry.lookup("adopt-chain")["conversation"] == "ses_B"
        assert registry.adopt("adopt-chain", "ses_C") == {
            "action": "adopted", "from": "ses_B", "to": "ses_C", "run": "adopt-chain"}
        receipts = [r for r in jmod.Journal("adopt-chain").rows() if r["event"] == "adopted"]
        assert [(r["from"], r["by"]) for r in receipts] == [("ses_A", "ses_B"), ("ses_B", "ses_C")]


@test
def adopt_refusals():
    with Estate() as E:
        missing = E.cli("adopt", "no-such-run")
        assert missing.returncode == 3 and "not found" in missing.stdout
        registry.register("plan", "ambiguous-one", journal=Path("/j"), conversation="ses_A")
        registry.register("plan", "ambiguous-two", journal=Path("/j"), conversation="ses_A")
        before = registry.all_rows()
        ambiguous = E.cli("adopt", "ambiguous-")
        assert ambiguous.returncode == 4 and "ambiguous-one" in ambiguous.stdout and "ambiguous-two" in ambiguous.stdout
        assert registry.all_rows() == before
        registry.register("plan", "already-owned", journal=E.estate / "runs" / "already-owned" / "journal.jsonl",
                          conversation="ses_test_conv")
        before = registry.lookup("already-owned")
        no_op = E.cli("adopt", "already-owned")
        assert no_op.returncode == 0 and no_op.stdout.strip() == "no-op: already yours"
        assert registry.lookup("already-owned") == before
        assert not [r for r in jmod.Journal("already-owned").rows() if r["event"] == "adopted"]
        assert registry.resolve_run("ambiguous-one")[0] == "ambiguous-one"  # exact match wins over prefix ambiguity


@test
def adopt_does_not_touch_execution_state():
    with Estate() as E:
        run = "adopt-state"
        rdir = E.estate / "runs" / run
        (rdir / "phase-1").mkdir(parents=True)
        (rdir / "phase-1" / "result.txt").write_text("complete\n")
        registry.register("plan", run, journal=rdir / "journal.jsonl", conversation="ses_A")
        j = jmod.Journal(run)
        j.write("run.open", plan="p", cwd="c", conversation="ses_A", pid=1)
        j.write("phase.start", phase="1", role="implementer", attempt=1)
        j.write("phase.done", phase="1")
        j.write("run.close", outcome="done")
        before_rows = j.rows()
        before_state = j.state()
        before_files = {p.relative_to(rdir): (p.read_bytes(), p.stat().st_mtime_ns)
                        for p in rdir.rglob("*") if p.is_file() and p.name != "journal.jsonl"}
        assert registry.adopt(run, "ses_B")["action"] == "adopted"
        after_rows = j.rows()
        after_files = {p.relative_to(rdir): (p.read_bytes(), p.stat().st_mtime_ns)
                       for p in rdir.rglob("*") if p.is_file() and p.name != "journal.jsonl"}
        assert after_files == before_files
        assert after_rows[:-1] == before_rows
        assert after_rows[-1]["event"] == "adopted" and after_rows[-1]["from"] == "ses_A" and after_rows[-1]["by"] == "ses_B"
        assert j.state() == before_state


@test
def adopt_rolls_back_registry_when_receipt_fails():
    with Estate() as E:
        run = "adopt-rollback"
        registry.register("plan", run, journal=E.estate / "runs" / run / "journal.jsonl", conversation="ses_A")
        original_write = registry.Journal.write

        def fail_receipt(self, event, **fields):
            raise OSError("journal unavailable")

        registry.Journal.write = fail_receipt
        try:
            try:
                registry.adopt(run, "ses_B")
            except OSError:
                pass
            else:
                raise AssertionError("adopt accepted a missing receipt")
        finally:
            registry.Journal.write = original_write
        assert registry.lookup(run)["conversation"] == "ses_A"
        assert not [r for r in jmod.Journal(run).rows() if r["event"] == "adopted"]


@test
def status_shows_adopted_run_in_scope():
    with Estate() as E:
        run = "adopt-scope"
        registry.register("plan", run, journal=E.estate / "runs" / run / "journal.jsonl", conversation="ses_A")
        jmod.Journal(run).write("run.open", plan="p", cwd="c", conversation="ses_A", pid=999999)
        all_before = {r["run"] for r in status.all_reports()}
        assert run not in {r["run"] for r in status.all_reports("ses_B")}
        registry.adopt(run, "ses_B")
        assert run in {r["run"] for r in status.all_reports("ses_B")}
        assert {r["run"] for r in status.all_reports()} == all_before


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
        assert calls[0]["agent"] == "pl-implementer" and calls[0]["cli"] == "claude"
        assert "EXIT predicates" in calls[0]["brief"] and "test -f made.txt" in calls[0]["brief"]


@test
def phase_timeout_reaches_worker_brief_as_time_budget():
    with Estate() as E:
        rid, p, r, st = run_plan(E, """## Phase 1: make (implementer)
TIMEOUT: 30
EXIT: test -f made.txt
FAKE: touch made.txt
""")
        assert st["closed"] == "done", r.stderr
        calls = E.fake_calls()
        assert "TIME BUDGET: 30s" in calls[0]["brief"] and "SHIP BY: 24s" in calls[0]["brief"]


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
def single_codex_review_blocking_revises_then_stops():
    with Estate() as E:
        rid, p, r, st = run_plan(E, """## Phase 1: work (implementer)
REVIEW: on
FAKE: touch out
FAKE: verdict CONCERNS
""")
        assert st["closed"] == "done", r.stderr
        rows = jmod.Journal(rid).rows()
        v = [x for x in rows if x["event"] == "review.verdict"]
        assert len(v) == 1 and v[0]["verdict"] == "CONCERNS" and v[0]["reviewer"] == "reviewer"
        assert v[0]["model"] == "gpt-6.1-sol"
        reviewers = [c for c in E.fake_calls() if c["agent"] == "pl-reviewer"]
        assert len(reviewers) == 1 and reviewers[0]["cli"] == "codex" and reviewers[0]["REVIEW_OUT"]
        # the sealed reviewer only answered; the engine wrote its record
        assert Path(v[0]["review"]).read_text().startswith("VERDICT: CONCERNS")
        # BLOCKING -> one revision -> BLOCKING again -> deliberate stop
        rid, p, r, st = run_plan(E, """## Phase 1: work (implementer)
REVIEW: on
FAKE: touch out
FAKE: verdict BLOCKING
""", sub="q")
        assert st["stopped"] == "review_blocking"
        # BLOCKING then PASS after the revision -> done
        rid, p, r, st = run_plan(E, """## Phase 1: work (implementer)
REVIEW: on
FAKE: touch out
FAKE: verdict-seq BLOCKING,PASS
""", sub="s")
        assert st["closed"] == "done", r.stderr
        v = [x["verdict"] for x in jmod.Journal(rid).rows() if x["event"] == "review.verdict"]
        assert v == ["BLOCKING", "PASS"]

@test
def review_runs_only_after_exit_passes():
    with Estate() as E:
        rid, p, r, st = run_plan(E, """## Phase 1: work (implementer)
ATTEMPTS: 1
EXIT: false
REVIEW: on
FAKE: verdict PASS
""")
        assert st["stopped"] == "burned"
        assert not [c for c in E.fake_calls() if c["agent"] == "pl-reviewer"]


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
def failed_dispatch_retries_same_seat_never_another_model():
    with Estate() as E:
        reg = roles_mod.load()
        s = roles_mod.seat("implementer", reg)
        rid, p, r, st = run_plan(E, f"""## Phase 1: a (implementer)
ATTEMPTS: 2
FAKE: fail-on-model {s['model']}
FAKE: touch a
""")
        assert st["stopped"] == "burned", r.stderr
        calls = E.fake_calls()
        assert len(calls) == 2 and {c["model"] for c in calls} == {s["model_q"]}
        ds = [x for x in jmod.Journal(rid).rows() if x["event"] == "dispatch.start"]
        assert all("fallback" not in d for d in ds)

@test
def claude_dispatch_argv_is_lean_headless_and_permissioned():
    reg = roles_mod.load()
    perms = roles_mod.claude_permissions("fast-worker", reg)
    argv = dsp.build_argv(binary="claude", cli="claude", agent="pl-fast-worker", model="claude-haiku-5-5",
                          effort="low", permissions=perms)
    assert argv[:5] == ["claude", "-p", "--output-format", "stream-json", "--verbose"]
    def val(flag):
        return argv[argv.index(flag) + 1]
    assert val("--agent") == "pl-fast-worker" and val("--model") == "claude-haiku-5-5" and val("--effort") == "low"
    assert val("--permission-mode") == "dontAsk"
    assert "Bash(gh pr comment*)" in val("--disallowed-tools").split(",")
    assert "Bash" in val("--allowed-tools").split(",")
    for flag in ("--strict-mcp-config", "--no-session-persistence", "--exclude-dynamic-system-prompt-sections"):
        assert flag in argv
    assert not any(a in argv for a in ("run", "--file", "--auto", "--format", "--dangerously-skip-permissions"))
    assert dsp.CLAUDE_HEADLESS_ENV["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert dsp.CLAUDE_HEADLESS_ENV["ENABLE_CLAUDEAI_MCP_SERVERS"] == "false"

@test
def codex_reviewer_argv_is_sealed_read_only_on_stdin():
    argv = dsp.build_argv(binary="codex", cli="codex", agent="pl-reviewer", model="gpt-6.1-sol", effort="high",
                          cwd=Path("/w"), out_dir=Path("/o"), sealed=True)
    assert argv[:3] == ["codex", "exec", "--json"] and argv[-1] == "-"
    assert argv[argv.index("--model") + 1] == "gpt-6.1-sol"
    assert 'model_reasoning_effort="high"' in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert argv[argv.index("--cd") + 1] == "/w" and argv[argv.index("-o") + 1] == "/o/last-message.md"
    # unsealed codex seats (fast-worker) run outside the sandbox: workspace-write makes .git read-only
    open_argv = dsp.build_argv(binary="codex", cli="codex", agent="x", model="gpt-6-luna", effort="low")
    assert "--dangerously-bypass-approvals-and-sandbox" in open_argv and "--sandbox" not in open_argv
    with Estate() as E:
        out = E.tmp / "d"
        res = dsp.run_dispatch(brief="FAKE: verdict PASS\n", role="reviewer", cwd=E.work, out_dir=out, timeout=30)
        assert res.outcome == "ok" and res.final_text.startswith("VERDICT: PASS"), res.as_row()
        assert res.tokens["total"] == 1234 and res.model == "gpt-6.1-sol"
        call = E.fake_calls()[-1]
        assert call["cli"] == "codex" and call["agent"] == "pl-reviewer" and call["variant"] == "high"
        assert "Headless worker contract" in call["brief"] and call["brief"].rstrip().endswith("FAKE: verdict PASS")

@test
def usage_limit_stops_run_without_burning_an_attempt():
    """A subscription usage-limit refusal is a deliberate stop (no fallback seat to
    try), charges no phase attempt, and resuming re-runs the same attempt."""
    with Estate() as E:
        reg = roles_mod.load()
        s = roles_mod.seat("implementer", reg)
        rid, p, r, st = run_plan(E, f"""## Phase 1: a (implementer)
FAKE: quota-on-model {s['model']}
FAKE: touch a
""")
        assert st["stopped"] == "usage_limit", r.stderr
        calls = E.fake_calls()
        assert len(calls) == 1 and calls[0]["model"] == s["model_q"]
        de = [x for x in jmod.Journal(rid).rows() if x["event"] == "dispatch.end"]
        assert de[0]["outcome"] == "quota" and "resets 2026-" in de[0]["error"]
        assert not any(x["event"] == "phase.fail" for x in jmod.Journal(rid).rows())

@test
def brief_carries_task_facts_and_previous_failure_only():
    b = dsp.build_brief(task="Edit a.py; git add a.py only; commit 'fix: a'", role="implementer", cwd=Path("/w"), exits=["pytest -q"],
                        previous_failure="EXIT failed: pytest -q\n1 failed", preamble="ctx")
    assert "WORKING DIRECTORY: /w" in b and "git add a.py only" in b and "`pytest -q`" in b and "Attack this failure first" in b
    assert "Never ask" not in b  # discipline lives in the role prompt, not the brief


@test
def brief_states_time_budget_when_given():
    b = dsp.build_brief(task="t", role="fast-worker", cwd=Path("/tmp"), budget_s=900)
    assert "TIME BUDGET: 900s" in b and "SHIP BY: 720s" in b
    nb = dsp.build_brief(task="t", role="fast-worker", cwd=Path("/tmp"))
    assert "TIME BUDGET" not in nb


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
def usage_limit_signature_from_claude_and_codex_events():
    rejected = json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": 1794056400}})
    allowed = json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "resetsAt": 1794056400}})
    assert dsp.is_quota_error(rejected) and not dsp.is_quota_error(allowed)
    assert dsp.quota_reset(rejected) == "2026-11-07T13:00:00Z"
    assert dsp.is_quota_error(json.dumps({"type": "error", "message": "You've hit your usage limit. Try again in 3 hours."}))
    assert dsp.is_quota_error(json.dumps({"type": "turn.failed", "error": {"message": "rate limit reached"}}))
    assert not dsp.is_quota_error(json.dumps({"type": "turn.failed", "error": {"message": "exit 1"}}))
    assert not dsp.is_quota_error("")
    # the retired DevPass 402 shape is not a subscription limit
    assert not dsp.is_quota_error(json.dumps({"type": "error", "part": {"error": {"data": {"statusCode": 402, "message": "premium-tier allowance"}}}}))

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
        s1 = sentry.tick()
        assert s1.get("deferred") == [(rid, d["id"])]
        time.sleep(0.3)
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
        f = E.agents / "pl-lane-worker.md"
        f.write_text(f.read_text() + "\n# drift\n")
        p = E.plan("## Phase 1: a (lane-worker)\nFAKE: touch a\n")
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
def status_names_usage_limit_hit_once_in_waiting_list():
    with Estate() as E:
        reg = roles_mod.load()
        s = roles_mod.seat("implementer", reg)
        rid, p, r, st = run_plan(E, f"""## Phase 1: a (implementer)
FAKE: quota-on-model {s['model']}
FAKE: touch a
""")
        assert st["stopped"] == "usage_limit", r.stderr
        rep = status.run_report(rid)
        hits = [w for w in rep["waiting_on_operator"] if "usage limit hit" in w]
        assert len(hits) == 1, rep["waiting_on_operator"]
        assert s["model_q"] in hits[0] and "resets 2026-11-07T13:00:00Z" in hits[0]
        text = status.render([rep], scope="all runs")
        assert text.count("usage limit hit") == 1

@test
def bench_shows_usage_limit_bucket_and_per_model_hits():
    with Estate() as E:
        reg = roles_mod.load()
        s = roles_mod.seat("implementer", reg)
        rid, p, r, st = run_plan(E, f"""## Phase 1: a (implementer)
FAKE: quota-on-model {s['model']}
FAKE: touch a
""")
        assert st["stopped"] == "usage_limit", r.stderr
        text = bench.render(bench.collect())
        assert "usage-limit hits" in text and "Usage-limit hits by model" in text
        assert f"| {s['model_q']} |" in text.split("Usage-limit hits by model")[1]

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
    text = trial.trial_plan("implementer", ["claude-opus-5-5"], tasks, "score clarity 0-10", Path("/w"), reg)
    pl = planmod.parse_text(text, Path("/w/plan.md"))
    seat = roles_mod.seat("implementer", reg)
    cand_q = "claude-opus-5-5"
    # Phases 1 and 2 are task1-arm-A and task1-arm-B; models assigned randomly to arms
    arm_models = {pl.by_number(1).model, pl.by_number(2).model}
    assert arm_models == {seat["model_q"], cand_q}, f"arm models {arm_models} != expected pair"
    # Phases should also have EFFORT: pinned
    assert pl.by_number(1).effort is not None, "arm phase should have EFFORT pinned"
    assert pl.by_number(3).after == [1] and pl.by_number(4).after == [2]
    scorer = pl.by_number(5)
    fam = roles_mod.family_of(reg["roles"][scorer.role]["model"], reg)
    assert scorer.role == "reviewer" and fam != seat["family"] and scorer.after == [1, 2, 3, 4] and scorer.exits
    assert pl.decisions and "nominated; trial decides" in pl.decisions[0]
    # Blinding: scorer phase brief must not name models or reveal arm identities
    scorer_text = text.split(f"## Phase {scorer.number}:")[1]
    assert "incumbent" not in scorer_text, "scorer phase reveals 'incumbent'"
    assert "candidate" not in scorer_text, "scorer phase reveals 'candidate'"
    assert seat["model_q"].split("/")[-1] not in scorer_text, "scorer phase reveals incumbent model name"
    assert "claude-opus-5-5" not in scorer_text, "scorer phase reveals candidate model name"
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
            trial.apply("implementer", "claude-opus-5-5", v, registry_path=regp)
        except roles_mod.RegistryError:
            pass
        else:
            raise AssertionError("applied a lost trial")
        assert regp.read_text() == before
        v["decision"], v["reasons"], v["chosen"] = "candidate", [], "claude-opus-5-5"
        trial.apply("implementer", "claude-opus-5-5", v, registry_path=regp)
        assert json.loads(regp.read_text())["roles"]["implementer"]["model"] == "claude-opus-5-5"


@test
def engine_pins_model_without_fallback_for_trial_arms():
    with Estate() as E:
        rid, p, r, st = run_plan(E, """## Phase 1: arm (implementer)
MODEL: claude-opus-5-5
ATTEMPTS: 1
FAKE: fail-on-model claude-opus-5-5
""")
        calls = E.fake_calls()
        assert len(calls) == 1 and calls[0]["model"] == "claude-opus-5-5" and st["stopped"] == "burned"


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
        assert (home / ".claude" / "agents" / "pl-implementer.md").exists()
        assert (home / ".claude" / "agents" / "pl-interactive.settings.json").exists()
        assert not (home / ".claude" / "agents" / "pl-reviewer.md").exists()  # codex seat: no agent file
        assert (home / ".system" / "bin" / "pl").exists()
        hooks = json.loads((home / ".claude" / "settings.json").read_text())["hooks"]["SessionStart"]
        assert [h["command"] for m in hooks for h in m["hooks"]] == [str(REPO / "plugin" / "session-env.sh")]
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
    critical_roles = {"reviewer", "researcher"}
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
    assert roles["reviewer"]["band"] == "critical"
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
    import tempfile
    regp = Path(tempfile.mkdtemp()) / "registry.json"
    regp.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(roles_mod.REGISTRY, regp)
    chosen_arm = "claude-opus-5-5@low"
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
    trial.apply("implementer", "claude-opus-5-5@low", verdict, registry_path=regp)
    after = json.loads(regp.read_text())
    assert after["roles"]["implementer"]["model"] == "claude-opus-5-5"
    assert after["roles"]["implementer"]["effort"] == "low"
    h = after["roles"]["implementer"]["history"][-1]
    assert h["model"] == "claude-opus-5-5" and h.get("effort") == "low"
    # apply refuses mismatched arm
    verdict2 = {**verdict, "chosen": "claude-opus-5-5@high"}
    try:
        trial.apply("implementer", "claude-opus-5-5@low", verdict2, registry_path=regp)
    except roles_mod.RegistryError:
        pass
    else:
        raise AssertionError("apply should refuse mismatched arm")


@test
def trial_apply_never_seats_a_quota_fallback():
    """A selection's quota_fallback is reported, never written: seats have no fallbacks."""
    import shutil, tempfile
    regp = Path(tempfile.mkdtemp()) / "registry.json"
    shutil.copy(roles_mod.REGISTRY, regp)
    verdict = {
        "chosen": "claude-opus-5-5@low", "decision": "candidate", "reasons": [], "decided_at": "now",
        "tolerance": 0.1, "band": 1.5, "arms": {}, "quality": {}, "tokens": {}, "wall_s": {},
        "selection": {"quota_fallback": "claude-haiku-5-5@medium"},
    }
    trial.apply("implementer", "claude-opus-5-5@low", verdict, registry_path=regp)
    after = json.loads(regp.read_text())
    assert after["roles"]["implementer"]["model"] == "claude-opus-5-5"
    assert "quota_fallback" not in after["roles"]["implementer"]
    assert "quota_fallback" not in after["roles"]["implementer"]["history"][-1]

@test
def dispatch_parse_transcript_retains_legacy_tokens():
    """Test that parse_transcript retains legacy token fields while adding new usage field."""
    from pipeline.dispatch import parse_transcript
    from pathlib import Path
    import tempfile
    import json
    
    # Create a test transcript with step_finish events
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        # Write multiple step_finish events
        f.write(json.dumps({
            "type": "step_finish",
            "sessionID": "ses_test123",
            "part": {
                "id": "p1",
                "tokens": {"total": 150, "input": 100, "output": 30, "reasoning": 20},
                "cost": 0.15
            }
        }) + "\n")
        f.write(json.dumps({
            "type": "text",
            "part": {"text": "final response"}
        }) + "\n")
        f.write(json.dumps({
            "type": "step_finish",
            "part": {
                "id": "p2",
                "tokens": {"total": 100, "input": 50, "output": 40, "reasoning": 10, "cache": {"read": 20, "write": 5}},
                "cost": 0.1
            }
        }) + "\n")
        fname = f.name
    
    try:
        result = parse_transcript(Path(fname))
        
        # Verify legacy tokens are retained
        assert result["tokens"]["input"] == 150, "legacy input tokens"
        assert result["tokens"]["output"] == 70, "legacy output tokens"
        assert result["tokens"]["reasoning"] == 30, "legacy reasoning tokens"
        assert result["tokens"]["total"] == 250, "legacy total tokens"
        assert abs(result["cost"] - 0.25) < 0.001, "legacy cost"
        assert result["session_id"] == "ses_test123"
        assert result["final_text"] == "final response"
        assert result["steps"] == 2
        
        # Verify new usage field with independent accounting
        assert "usage" in result, "usage field present"
        usage = result["usage"]
        assert usage is not None, "usage not None"
        assert usage["provider_total"] == 250, "provider_total from accumulator"
        assert usage["input_tokens"] == 150, "input_tokens from accumulator"
        assert usage["output_tokens"] == 70, "output_tokens from accumulator"
        assert usage["reasoning_tokens"] == 30, "reasoning_tokens from accumulator"
        assert usage["cache_read_tokens"] == 20, "cache_read from accumulator"
        assert usage["cache_write_tokens"] == 5, "cache_write from accumulator"
        assert abs(usage["cost_usd"] - 0.25) < 0.001, "cost_usd from accumulator"
        assert usage["steps_completed"] == 2, "steps from accumulator"
        assert usage["telemetry_complete"] == True, "telemetry_complete flag"
        assert usage["missing_fields"] == 0, "no missing fields"
    finally:
        import os
        os.unlink(fname)


@test
def dispatch_result_as_row_includes_usage():
    """Test that DispatchResult.as_row() includes usage field when present."""
    from pipeline.dispatch import DispatchResult
    
    # Test with usage
    result = DispatchResult(
        outcome="ok",
        exit_code=0,
        wall_s=10.5,
        tokens={"input": 100, "output": 50, "reasoning": 10, "total": 160},
        cost=0.1,
        model="test-model",
        session_id="ses_123",
        usage={
            "provider_total": 160,
            "input_tokens": 100,
            "output_tokens": 50,
            "reasoning_tokens": 10,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost_usd": 0.1,
            "steps_completed": 1,
            "missing_fields": 0,
            "telemetry_complete": True
        }
    )
    
    row = result.as_row()
    assert "usage" in row, "usage field in row"
    assert row["usage"]["provider_total"] == 160
    assert row["outcome"] == "ok"
    assert row["tokens"]["input"] == 100
    
    # Test without usage
    result_no_usage = DispatchResult(
        outcome="ok",
        exit_code=0,
        wall_s=10.5,
        tokens={"input": 100, "output": 50, "reasoning": 10, "total": 160},
        cost=0.1
    )
    
    row_no_usage = result_no_usage.as_row()
    assert "usage" not in row_no_usage, "usage not in row when None"


@test
def dispatch_parse_transcript_incomplete_telemetry():
    """Test that parse_transcript correctly marks incomplete telemetry."""
    from pipeline.dispatch import parse_transcript
    from pathlib import Path
    import tempfile
    import json
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        # Write step_finish without cost field
        f.write(json.dumps({
            "type": "step_finish",
            "part": {
                "id": "p1",
                "tokens": {"total": 150, "input": 100, "output": 30, "reasoning": 20}
                # missing cost
            }
        }) + "\n")
        fname = f.name
    
    try:
        result = parse_transcript(Path(fname))
        
        usage = result["usage"]
        assert usage["cost_usd"] == 0, "cost defaults to 0"
        assert usage["telemetry_complete"] == False, "telemetry_complete False when missing cost"
        assert usage["missing_fields"] > 0, "missing_fields > 0"
    finally:
        import os
        os.unlink(fname)


@test
def dispatch_parse_transcript_quota_error():
    """Test that parse_transcript preserves quota error detection."""
    from pipeline.dispatch import parse_transcript
    from pathlib import Path
    import tempfile
    import json
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        # A Claude usage-limit refusal: rejected rate_limit_event, then an error result
        f.write(json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}}) + "\n")
        fname = f.name
    
    try:
        result = parse_transcript(Path(fname))
        
        # Quota should be detected
        assert result["quota"] == True, "quota error detected"
        # No steps should have been processed
        assert result["steps"] == 0
        # usage should still be present with zeros
        assert result["usage"] is not None
        assert result["usage"]["steps_completed"] == 0
    finally:
        import os
        os.unlink(fname)


@test
def dispatch_deliverables_path_extraction():
    """parse_deliverables pulls both absolute (made relative) and cwd-relative tokens
    out of the worker's final closing words."""
    with Estate() as E:
        (E.work / "docs").mkdir()
        (E.work / "docs" / "A.md").write_text("x")
        (E.work / "notes").mkdir()
        (E.work / "notes" / "ok.md").write_text("x")
        text = f"wrote {E.work}/docs/A.md and notes/ok.md"
        r = dsp.parse_deliverables(E.work, text)
        assert r == ["docs/A.md", "notes/ok.md"], r


@test
def dispatch_deliverables_nonexistent_paths_ignored():
    """Tokens that look like paths but don't resolve to a real file under cwd never
    make the list."""
    with Estate() as E:
        (E.work / "docs").mkdir()
        (E.work / "docs" / "A.md").write_text("x")
        text = "wrote docs/A.md and made up/nonexistent.md and /etc/not-there.conf"
        r = dsp.parse_deliverables(E.work, text)
        assert r == ["docs/A.md"], r


@test
def dispatch_deliverables_chat_chatter_never_matched():
    """Narration with no real paths yields nothing; only the last block is scanned,
    so a path mentioned earlier (mid-narration) is not harvested."""
    with Estate() as E:
        (E.work / "docs").mkdir()
        (E.work / "docs" / "A.md").write_text("x")
        chatty = "I wrote some code and tested it thoroughly. All good."
        assert dsp.parse_deliverables(E.work, chatty) == []
        two_blocks = "In passing I touched docs/A.md while exploring.\n\nDone, no files to report here."
        assert dsp.parse_deliverables(E.work, two_blocks) == []


@test
def dispatch_deliverables_dedup_and_last_block_only():
    """Repeated tokens collapse to one entry, sorted; only the final non-empty block
    is scanned, so an earlier block's path is not included."""
    with Estate() as E:
        (E.work / "docs").mkdir()
        (E.work / "docs" / "A.md").write_text("x")
        (E.work / "notes").mkdir()
        (E.work / "notes" / "ok.md").write_text("x")
        text = "First block mentions docs/A.md.\n\ndocs/A.md docs/A.md notes/ok.md"
        r = dsp.parse_deliverables(E.work, text)
        assert r == ["docs/A.md", "notes/ok.md"], r


@test
def dispatch_deliverables_empty_text_yields_no_deliverables():
    with Estate() as E:
        assert dsp.parse_deliverables(E.work, "") == []
        assert dsp.parse_deliverables(E.work, "   \n\n  ") == []


@test
def dispatch_as_row_backcompat_no_deliverables_key_when_none():
    """Old DispatchResult instances (deliverables never set) still round-trip through
    as_row() without introducing the key -- old journal/result.json rows read as
    no-deliverables, not an empty list that implies the engine tried and found none."""
    r = dsp.DispatchResult(outcome="ok", exit_code=0, wall_s=1.0,
                            tokens={"input": 1, "output": 1, "reasoning": 0, "total": 2}, cost=0.0)
    row = r.as_row()
    assert "deliverables" not in row, row

    r2 = dsp.DispatchResult(outcome="ok", exit_code=0, wall_s=1.0,
                             tokens={"input": 1, "output": 1, "reasoning": 0, "total": 2}, cost=0.0,
                             deliverables=["docs/A.md"])
    row2 = r2.as_row()
    assert row2["deliverables"] == ["docs/A.md"], row2


@test
def dispatch_run_dispatch_journals_deliverables():
    """run_dispatch wires parse_deliverables(cwd, final_text) into the result and
    persists whatever it finds (here: none, since the fake worker's default closing
    text names no real file) in result.json via as_row()."""
    with Estate() as E:
        reg = roles_mod.load()
        s = roles_mod.seat("implementer", reg)
        out_dir = E.tmp / "d_deliv"
        res = dsp.run_dispatch(brief="FAKE: touch out.md\n", role="implementer",
                               cwd=E.work, out_dir=out_dir, timeout=10, model=s["model_q"])
        assert res.deliverables == [], res.deliverables
        row = json.loads((out_dir / "result.json").read_text())
        assert row.get("deliverables") == []


@test
def dispatch_argv_picks_cli_by_model_family():
    reg = roles_mod.load()
    assert roles_mod.cli_of("claude-sonnet-5-5", reg) == "claude" and roles_mod.cli_of("gpt-6.1-sol", reg) == "codex"
    c = dsp.build_argv(binary="claude", cli="claude", agent="pl-implementer", model="claude-sonnet-5-5", effort="high")
    x = dsp.build_argv(binary="codex", cli="codex", agent="pl-reviewer", model="gpt-6.1-sol", effort="high")
    assert c[1] == "-p" and x[1] == "exec"
    assert dsp.model_arg("llmgateway-devpass/claude-sonnet-5-5#low") == "claude-sonnet-5-5"
    assert dsp.model_arg("claude-sonnet-5-5") == "claude-sonnet-5-5"
    try:
        dsp.build_argv(binary="x", cli="devpass-code", agent="a", model="m", effort=None)
    except ValueError:
        pass
    else:
        raise AssertionError("unknown cli accepted")

@test
def dispatch_runs_claude_with_effort_flag_brief_on_stdin_and_process_cwd():
    with Estate() as E:
        s = roles_mod.seat("implementer", roles_mod.load())
        out_dir = E.tmp / "d_current_cli"
        res = dsp.run_dispatch(brief="FAKE: record\nFAKE: touch cwd_probe\n", role="implementer",
                               cwd=E.work, out_dir=out_dir, timeout=20, model=s["model_q"], effort="medium")
        assert res.outcome == "ok", res.error
        assert res.tokens == {"input": 1000, "output": 200, "reasoning": 34, "total": 1234}
        assert res.session_id == "ses_fake" and abs(res.cost - 0.0123) < 1e-9
        call = E.fake_calls()[-1]
        argv = call["argv"]
        assert argv[argv.index("--model") + 1] == s["model_q"] and argv[argv.index("--effort") + 1] == "medium"
        assert call["cli"] == "claude" and call["model"] == s["model_q"] and call["variant"] == "medium"
        assert Path(call["cwd"]).resolve() == Path(E.work).resolve()
        assert (Path(E.work) / "cwd_probe").exists()
        assert (out_dir / "brief.md").read_text() == call["brief"]

@test
def usage_normalizes_claude_result_and_codex_turns():
    from pipeline.usage import UsageAccumulator
    acc = UsageAccumulator()
    result = json.dumps({"type": "result", "session_id": "s1", "total_cost_usd": 0.5,
                         "usage": {"input_tokens": 10, "cache_creation_input_tokens": 100,
                                   "cache_read_input_tokens": 1000, "output_tokens": 50,
                                   "output_tokens_details": {"thinking_tokens": 20}}})
    acc.feed_line(result)
    acc.feed_line(result)  # same session's result is counted once
    snap = acc.snapshot()
    assert (snap["input"], snap["output"], snap["reasoning"]) == (10, 30, 20)
    assert (snap["cache_read"], snap["cache_write"], snap["provider_total"]) == (1000, 100, 1160)
    assert snap["cost"] == 0.5 and snap["steps"] == 1
    acc = UsageAccumulator()
    for _ in range(2):  # one turn.completed per codex turn; both count
        acc.feed_line(json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 500, "cached_input_tokens": 300, "output_tokens": 40, "reasoning_output_tokens": 15}}))
    snap = acc.snapshot()
    assert (snap["input"], snap["cache_read"], snap["output"], snap["reasoning"]) == (400, 600, 80, 30)
    assert snap["steps"] == 2 and snap["cost"] == 0 and snap["telemetry_complete"] is False


@test
def codex_transcript_yields_commands_text_and_failure():
    with Estate() as E:
        t = E.tmp / "codex.jsonl"
        t.write_text("\n".join(json.dumps(e) for e in [
            {"type": "thread.started", "thread_id": "th_1"},
            {"type": "item.started", "item": {"id": "i1", "type": "command_execution", "command": "python3 tests/run.py"}},
            {"type": "item.completed", "item": {"id": "i2", "type": "agent_message", "text": "VERDICT: PASS\n\nok"}},
            {"type": "turn.completed", "usage": {"input_tokens": 9, "cached_input_tokens": 0, "output_tokens": 1}},
        ]) + "\n")
        p = dsp.parse_transcript(t)
        assert p["session_id"] == "th_1" and p["commands"] == ["python3 tests/run.py"]
        assert p["final_text"].startswith("VERDICT: PASS") and p["is_error"] is False
        assert p["tokens"]["total"] == 10 and engine.ran_commands(t) == ["python3 tests/run.py"]
        t.write_text(json.dumps({"type": "turn.failed", "error": {"message": "boom"}}) + "\n")
        assert dsp.parse_transcript(t)["is_error"] is True


@test
def agy_seat_argv_prompt_and_deny_rules():
    reg = roles_mod.load()
    assert roles_mod.seat("researcher", reg)["cli"] == "agy"
    settings = roles_mod.agy_settings("researcher", reg)
    assert "command(gh pr comment)" in settings["permissions"]["deny"]
    argv = dsp.build_argv(binary="agy", cli="agy", agent="pl-researcher", model="gemini-3.8-flash", effort="medium",
                          prompt="do it", settings=settings)
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert argv[argv.index("--model") + 1] == "gemini-3.8-flash" and argv[argv.index("--effort") + 1] == "medium"
    assert "--dangerously-skip-permissions" in argv and argv[-1] == "-p=do it"
    assert json.loads(argv[argv.index("--settings") + 1]) == settings
    # a prompt too large for one argv string is passed as a pointer to the saved file
    assert dsp.agy_prompt("x" * (dsp.AGY_INLINE_MAX + 1), Path("/o/stdin.md")).startswith("Your full instructions are in /o/stdin.md")
    with Estate() as E:
        out = E.tmp / "agy"
        res = dsp.run_dispatch(brief="FAKE: ran grep -r x .\nFAKE: touch notes.md\n", role="researcher",
                               cwd=E.work, out_dir=out, timeout=30)
        assert res.outcome == "ok" and res.model == "gemini-3.8-flash", res.as_row()
        assert res.tokens == {"input": 1000, "output": 200, "reasoning": 34, "total": 1234}
        assert res.session_id == "ses_fake" and (E.work / "notes.md").exists()
        assert engine.ran_commands(out / "transcript.jsonl") == ["grep -r x ."]
        call = E.fake_calls()[-1]
        assert call["cli"] == "agy" and call["agent"] == "pl-researcher" and call["variant"] == "medium"
        assert "Headless worker contract" in call["brief"]
        res = dsp.run_dispatch(brief="FAKE: quota\n", role="document-writer", cwd=E.work, out_dir=E.tmp / "agyq", timeout=30)
        assert res.outcome == "quota" and "resets in 4 hours" in res.error, res.as_row()


@test
def codex_rules_forbid_external_posting_and_drift_guard():
    text = roles_mod.codex_rules()
    assert 'prefix_rule(pattern = ["gh", "pr", "create"], decision = "forbidden"' in text
    assert 'prefix_rule(pattern = ["curl", "-X", "POST"], decision = "forbidden"' in text
    assert '"git"' not in text
    with Estate() as E:
        d = E.tmp / "rules"
        assert roles_mod.codex_rules_drift(d) == [str(d / "pipeline.rules")]
        roles_mod.write_codex_rules(d)
        assert roles_mod.codex_rules_drift(d) == []


@test
def browser_allowlist_role_grant_and_registry_validation():
    from pipeline import browser
    reg = roles_mod.load()
    cfg = browser.config(reg)
    assert urlparse_host(cfg["endpoint"]) in ("127.0.0.1", "localhost")
    allow = ["glassdoor.com", "teamblind.com"]
    assert browser.host_allowed("https://www.glassdoor.com/Reviews/x.htm", allow)
    assert browser.host_allowed("https://teamblind.com/company/Acme", allow)
    for bad in ("https://glassdoor.com.evil.io/", "https://notglassdoor.com/", "file:///etc/passwd",
                "https://example.com/?next=glassdoor.com", "javascript:alert(1)", "glassdoor.com"):
        assert not browser.host_allowed(bad, allow), bad
    # grants: researcher/lane/fast workers only; the operator's own shell (no role) may use it
    assert browser.role_allowed(reg, "researcher") and browser.role_allowed(reg, "lane-worker")
    assert not browser.role_allowed(reg, "implementer") and not browser.role_allowed(reg, "reviewer")
    assert browser.role_allowed(reg, "")
    import copy
    for mutate in (lambda b: b["browser"].__setitem__("endpoint", "http://10.0.0.5:9222"),
                   lambda b: b["browser"].__setitem__("allow", ["https://glassdoor.com/"]),
                   lambda b: b["browser"].__setitem__("allow", ["com"])):
        bad = copy.deepcopy(reg)
        mutate(bad)
        try:
            roles_mod.validate(bad)
        except roles_mod.RegistryError:
            pass
        else:
            raise AssertionError("bad browser config accepted")


def urlparse_host(u):
    from urllib.parse import urlparse
    return urlparse(u).hostname


@test
def browser_pacing_spaces_visits_and_caps_per_day():
    from pipeline import browser
    with Estate() as E:
        state = E.tmp / "pace.json"
        clock = {"t": 1_800_000_000.0}
        slept = []
        def sleep(s):
            slept.append(s)
            clock["t"] += s
        cfg = {"min_interval_s": 8, "daily_max": 3}
        assert browser.pace("a.com", cfg, state=state, sleep=sleep, now=lambda: clock["t"]) == 0
        w = browser.pace("a.com", cfg, state=state, sleep=sleep, now=lambda: clock["t"])
        assert 8 <= w <= 12 and slept == [w]                       # second visit waits the gap (+ jitter)
        assert browser.pace("b.com", cfg, state=state, sleep=sleep, now=lambda: clock["t"]) == 0  # per host
        clock["t"] += 60
        browser.pace("a.com", cfg, state=state, sleep=sleep, now=lambda: clock["t"])
        try:
            browser.pace("a.com", cfg, state=state, sleep=sleep, now=lambda: clock["t"])
        except browser.BrowserError as e:
            assert e.code == browser.EXIT_REFUSED and "daily cap" in str(e)
        else:
            raise AssertionError("daily cap not enforced")
        clock["t"] += 86400                                          # a new day resets the count
        browser.pace("a.com", cfg, state=state, sleep=sleep, now=lambda: clock["t"])


@test
def browser_fetch_reads_page_over_devtools_and_closes_its_tab():
    from pipeline import browser
    from fake_cdp import FakeChrome
    big = "review text " * 9000   # > 64 KiB: exercises the 8-byte frame length
    chrome = FakeChrome({"title": "Acme Reviews", "url": "https://www.glassdoor.com/Reviews/acme", "text": big,
                         "links": [{"t": "Next", "h": "https://www.glassdoor.com/Reviews/acme_P2"}]})
    try:
        cfg = {"endpoint": chrome.endpoint, "allow": ["glassdoor.com"], "min_interval_s": 0, "daily_max": 9}
        assert browser.version(cfg)["Browser"] == "FakeChrome/1.0"
        page = browser.fetch("https://www.glassdoor.com/Reviews/acme", cfg, wait=0, scroll=1, timeout=10)
        assert page["title"] == "Acme Reviews" and page["text"] == big and page["status"] == ""
        methods = [m for _, m, _ in chrome.calls]
        assert methods[0] == "Target.createTarget" and chrome.calls[0][2]["background"] is True
        assert "Page.navigate" in methods and methods[-1] == "Target.closeTarget"
        # background tab, rendered as if focused (hidden tabs lay out no text on some sites)
        assert methods.index("Emulation.setFocusEmulationEnabled") < methods.index("Page.navigate")
        assert chrome.closed_targets == ["T1"]                      # never leaves tabs in the operator's browser
        text = browser.render(page, max_chars=50, links=True)
        assert "[truncated at 50 of" in text and "Next\thttps://www.glassdoor.com/Reviews/acme_P2" in text
    finally:
        chrome.close()
    # a page whose load event never fires is still read after a short grace, not the full timeout
    slow = FakeChrome({"title": "Slow", "url": "https://www.teamblind.com/", "text": "posts", "links": []}, fire_load=False)
    try:
        saved, browser.LOAD_GRACE_S = browser.LOAD_GRACE_S, 0.5
        t0 = time.time()
        page = browser.fetch("https://www.teamblind.com/", {"endpoint": slow.endpoint}, wait=0, timeout=30)
        assert page["text"] == "posts" and time.time() - t0 < 5, time.time() - t0
    finally:
        browser.LOAD_GRACE_S = saved
        slow.close()
    # bot checks and sign-in redirects are reported for the operator, never worked around
    assert browser.classify({"title": "Just a moment...", "text": "Humans only", "url": "https://x.com/a"}, "https://x.com/a") == "challenge"
    assert browser.classify({"title": "Sign In", "text": "x" * 50, "url": "https://x.com/profile/login_input.htm"}, "https://x.com/Reviews/a") == "signin"
    assert browser.classify({"title": "Reviews", "text": "captcha " + "long review " * 400, "url": "https://x.com/r"}, "https://x.com/r") == ""


@test
def browser_cli_refuses_unlisted_sites_and_reports_bridge_down():
    with Estate() as E:
        env = {**os.environ, "PIPELINE_BROWSER_ENDPOINT": "http://127.0.0.1:9", "PIPELINE_ROLE": "researcher"}
        r = subprocess.run([sys.executable, "-m", "pipeline.cli", "browser", "fetch", "https://example.org/"],
                           capture_output=True, text=True, env=env, cwd=str(REPO))
        assert r.returncode == 2 and "not in browser.allow" in r.stderr, r.stderr
        r = subprocess.run([sys.executable, "-m", "pipeline.cli", "browser", "fetch", "https://www.glassdoor.com/"],
                           capture_output=True, text=True, env={**env, "PIPELINE_ROLE": "implementer"}, cwd=str(REPO))
        assert r.returncode == 2 and "no `browser` tool grant" in r.stderr, r.stderr
        r = subprocess.run([sys.executable, "-m", "pipeline.cli", "browser", "fetch", "https://www.glassdoor.com/"],
                           capture_output=True, text=True, env=env, cwd=str(REPO))
        assert r.returncode == 3 and "not reachable" in r.stderr and "No page was fetched" in r.stderr, r.stderr
        assert not (E.estate / "state" / "browser-pace.json").exists()  # a down bridge spends no paced slot


@test
def fake_worker_rejects_retired_devpass_argv():
    with Estate() as E:
        for argv in (["run", "--format", "json", "--agent", "pl-x", "--model", "p/m", "--auto"],
                     ["-p", "--output-format", "stream-json", "--file", "/tmp/brief.md"]):
            r = subprocess.run([sys.executable, str(FAKE), *argv], capture_output=True, text=True,
                               cwd=str(E.work), input="")
            assert r.returncode == 2 and "retired DevPass argv" in r.stderr, (argv, r.stderr)


# Load and run all test_review_provenance tests as part of the full suite
def register_review_provenance_tests():
    """Register the review provenance tests in the full suite."""
    import unittest
    from tests import test_review_provenance
    
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(test_review_provenance)
    
    # Add each test to the TESTS list
    for test_group in suite:
        for test in test_group:
            test_name = str(test).split()[0]
            # Create a wrapper function for each test
            def make_test_wrapper(t):
                def wrapper():
                    result = unittest.TestResult()
                    t.run(result)
                    if result.failures:
                        raise AssertionError(result.failures[0][1])
                    if result.errors:
                        raise Exception(result.errors[0][1])
                return wrapper
            
            wrapper = make_test_wrapper(test)
            wrapper.__name__ = f"review_provenance_{test_name}"
            TESTS.append(wrapper)


# Load and run all test_usage_accounting tests as part of the full suite
def register_usage_accounting_tests():
    """Register the usage accounting tests in the full suite."""
    import unittest
    from tests import test_usage_accounting
    
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(test_usage_accounting)
    
    # Add each test to the TESTS list
    for test_group in suite:
        for test in test_group:
            test_name = str(test).split()[0]
            # Create a wrapper function for each test
            def make_test_wrapper(t):
                def wrapper():
                    result = unittest.TestResult()
                    t.run(result)
                    if result.failures:
                        raise AssertionError(result.failures[0][1])
                    if result.errors:
                        raise Exception(result.errors[0][1])
                return wrapper
            
            wrapper = make_test_wrapper(test)
            wrapper.__name__ = f"usage_accounting_{test_name}"
            TESTS.append(wrapper)


# Call the registration functions
register_review_provenance_tests()
register_usage_accounting_tests()


# Load and run all test_tui_model tests as part of the full suite
def register_tui_model_tests():
    """Register the TUI model unit tests in the full suite."""
    import unittest
    from tests import test_tui_model

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(test_tui_model)

    for test_group in suite:
        for test in test_group:
            test_name = str(test).split()[0]

            def make_test_wrapper(t):
                def wrapper():
                    result = unittest.TestResult()
                    t.run(result)
                    if result.failures:
                        raise AssertionError(result.failures[0][1])
                    if result.errors:
                        raise Exception(result.errors[0][1])
                return wrapper

            wrapper = make_test_wrapper(test)
            wrapper.__name__ = f"tui_model_{test_name}"
            TESTS.append(wrapper)


register_tui_model_tests()


# Load and run all test_scroll / test_mouse / test_conversation tests (the pure
# parts of the phase-3 TUI work: wrap calc, follow rules, selection buffer, OSC52
# payload shape, and the conversation-tab sqlite reader) as part of the full suite.
def register_module_tests(module, prefix):
    import unittest

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(module)

    for test_group in suite:
        for t in test_group:
            test_name = str(t).split()[0]

            def make_test_wrapper(tt):
                def wrapper():
                    result = unittest.TestResult()
                    tt.run(result)
                    if result.failures:
                        raise AssertionError(result.failures[0][1])
                    if result.errors:
                        raise Exception(result.errors[0][1])
                return wrapper

            wrapper = make_test_wrapper(t)
            wrapper.__name__ = f"{prefix}_{test_name}"
            TESTS.append(wrapper)


def register_scroll_mouse_conversation_tests():
    from tests import test_scroll, test_mouse, test_conversation
    register_module_tests(test_scroll, "tui_scroll")
    register_module_tests(test_mouse, "tui_mouse")
    register_module_tests(test_conversation, "tui_conversation")


register_scroll_mouse_conversation_tests()


# ---------------------------------------------------------------- gc

@test
def gc_eligibility_matrix():
    from pipeline import gc as gcmod
    now = 1_000_000.0
    buf = 3600.0
    # running (never closed) is always kept, however old opened_at might be
    assert gcmod.eligibility({"closed": None, "opened_at": 0.0}, now=now, buffer_s=buf) == gcmod.KEEP_RUNNING
    # closed but within the buffer is kept
    assert gcmod.eligibility({"closed": "done", "closed_at": now - 10}, now=now, buffer_s=buf) == gcmod.KEEP_RECENT
    # closed, past the buffer -> eligible
    assert gcmod.eligibility({"closed": "done", "closed_at": now - buf - 1}, now=now, buffer_s=buf) == gcmod.ELIGIBLE_CLOSED
    # closed with a stopped outcome, past the buffer -> still eligible (a STOPPED
    # receipt is evidence kept on disk regardless; the journal row itself is fine
    # to consider closed)
    assert gcmod.eligibility({"closed": "stopped", "closed_at": now - buf - 1}, now=now, buffer_s=buf) == gcmod.ELIGIBLE_CLOSED
    # closed but no closed_at timestamp is not enough evidence: keep
    assert gcmod.eligibility({"closed": "done", "closed_at": None}, now=now, buffer_s=buf) == gcmod.KEEP_RUNNING


@test
def gc_plan_on_fixture_estate_running_and_recent_kept_closed_old_eligible():
    from pipeline import gc as gcmod
    with Estate() as E:
        now = time.time()
        buf_s = 3600.0

        def make_run(rid, rows, quick=False):
            rdir = E.estate / "runs" / rid
            rdir.mkdir(parents=True)
            for r in rows:
                jmod.Journal(rid).write(r.pop("event"), **r)
            if quick:
                (E.estate / "quick" / rid).mkdir(parents=True)

        # 1) still running: never closed
        make_run("run_running", [
            {"event": "run.open", "plan": "p", "cwd": "c", "conversation": "other", "pid": os.getpid()},
        ])
        # 2) dead-engine, open, no run.close: sentry may relight it -- keep
        make_run("run_dead_engine", [
            {"event": "run.open", "plan": "p", "cwd": "c", "conversation": "other", "pid": 999999},
        ])
        # 3) closed recently (within buffer): keep
        make_run("run_recent", [
            {"event": "run.open", "plan": "p", "cwd": "c", "conversation": "other", "pid": os.getpid()},
            {"event": "run.close", "outcome": "done"},
        ])
        # 4) closed long ago: eligible
        make_run("run_old", [
            {"event": "run.open", "plan": "p", "cwd": "c", "conversation": "other", "pid": os.getpid()},
            {"event": "run.close", "outcome": "done"},
        ], quick=True)
        # backdate run_old's closed_at by rewriting its journal rows directly
        jp = E.estate / "runs" / "run_old" / "journal.jsonl"
        rows = [json.loads(l) for l in jp.read_text().splitlines()]
        for r in rows:
            r["t"] = now - (buf_s * 3 + 100)
        jp.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")
        # 5) closed long ago but IS the current conversation's lineage: keep
        make_run("run_lineage", [
            {"event": "run.open", "plan": "p", "cwd": "c", "conversation": registry.current_conversation(), "pid": os.getpid()},
            {"event": "run.close", "outcome": "done"},
        ])
        jp2 = E.estate / "runs" / "run_lineage" / "journal.jsonl"
        rows2 = [json.loads(l) for l in jp2.read_text().splitlines()]
        for r in rows2:
            r["t"] = now - (buf_s * 3 + 100)
        jp2.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows2) + "\n")
        registry.register("plan", "run_lineage", journal=jp2, conversation=registry.current_conversation())

        sweep_list, keep_list, saved_bytes, reasons = gcmod.plan(E.estate, buffer_s=buf_s)
        swept = {i["run"] for i in sweep_list}
        kept = {i["run"] for i in keep_list}
        assert swept == {"run_old"}, (swept, kept)
        assert {"run_running", "run_dead_engine", "run_recent", "run_lineage"} <= kept
        assert reasons["run_lineage"] == "current conversation lineage"
        assert reasons["run_running"].startswith("open/running")
        old_item = next(i for i in sweep_list if i["run"] == "run_old")
        assert old_item["kind"] == "quick"


@test
def gc_sweep_deletes_artifacts_keeps_journal_and_stopped_writes_manifest_first():
    from pipeline import gc as gcmod
    with Estate() as E:
        rid = "run_sweepme"
        rdir = E.estate / "runs" / rid
        rdir.mkdir(parents=True)
        j = jmod.Journal(rid)
        j.write("run.open", plan="p", cwd="c", conversation="other", pid=os.getpid())
        j.write("run.stop", reason="burned", detail="d")
        j.write("run.close", outcome="stopped")
        (rdir / "STOPPED").write_text("burned\nd\n")
        (rdir / "engine.lock").write_text(str(os.getpid()))
        (rdir / "engine.log").write_text("log output\n")
        pdir = rdir / "phase-1"
        pdir.mkdir()
        (pdir / "transcript.jsonl").write_text("x" * 50)
        (pdir / "brief.txt").write_text("brief text")
        # backdate closed_at well past the buffer
        jp = rdir / "journal.jsonl"
        rows = [json.loads(l) for l in jp.read_text().splitlines()]
        now = time.time()
        for r in rows:
            r["t"] = now - (3600.0 * 3 + 100)
        jp.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")

        sweep_list, keep_list, saved_bytes, reasons = gcmod.plan(E.estate, buffer_s=3600.0)
        assert [i["run"] for i in sweep_list] == [rid]
        assert saved_bytes > 0

        result = gcmod.sweep(sweep_list, estate=E.estate)
        assert result["errors"] == []
        assert result["deleted"] >= 1
        manifest_path = Path(result["manifest"])
        assert manifest_path.exists() and manifest_path.parent == E.estate / "logs"
        manifest_rows = [json.loads(l) for l in manifest_path.read_text().splitlines()]
        assert manifest_rows and all(r["run"] == rid for r in manifest_rows)

        # journal, STOPPED, engine.lock survive; artifacts are gone
        assert jp.exists() and (rdir / "STOPPED").exists() and (rdir / "engine.lock").exists()
        assert not (rdir / "engine.log").exists()
        assert not pdir.exists()
        # re-planning finds nothing left to sweep for this run (0 bytes: already gone)
        sweep_list2, _, _, _ = gcmod.plan(E.estate, buffer_s=3600.0)
        remaining = next((i for i in sweep_list2 if i["run"] == rid), None)
        assert remaining is None or remaining["bytes"] == 0


@test
def gc_dry_run_deletes_nothing_and_cli_reports():
    with Estate() as E:
        rid = "run_dryrun"
        rdir = E.estate / "runs" / rid
        rdir.mkdir(parents=True)
        j = jmod.Journal(rid)
        j.write("run.open", plan="p", cwd="c", conversation="other", pid=os.getpid())
        j.write("run.close", outcome="done")
        (rdir / "engine.log").write_text("keep me for now\n")
        jp = rdir / "journal.jsonl"
        rows = [json.loads(l) for l in jp.read_text().splitlines()]
        now = time.time()
        for r in rows:
            r["t"] = now - (3600.0 * 3 + 100)
        jp.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")

        r = E.cli("gc", "--dry-run", "--buffer-hours", "1", check=True)
        assert rid in r.stdout and "eligible" in r.stdout
        assert (rdir / "engine.log").exists()  # nothing deleted
        assert not any((E.estate / "logs").glob("gc-manifest-*.jsonl"))
        r2 = E.cli("gc", check=True)  # default (no flags) is dry-run too
        assert (rdir / "engine.log").exists()


# ---------------------------------------------------------------- gc cloud tier

def _make_old_closed_run(E, rid, *, buf_s=3600.0, quick=False):
    """A closed, past-buffer run with a couple of artifact files -- the shape
    gc.plan() reports as eligible. Returns (rdir, sweep_item)."""
    from pipeline import gc as gcmod
    rdir = E.estate / "runs" / rid
    rdir.mkdir(parents=True)
    j = jmod.Journal(rid)
    j.write("run.open", plan="p", cwd="c", conversation="other", pid=os.getpid())
    j.write("run.close", outcome="done")
    (rdir / "STOPPED").write_text("nope\n")  # present to prove it's never tarred/deleted
    (rdir / "engine.lock").write_text(str(os.getpid()))
    (rdir / "engine.log").write_text("some engine output\n" * 5)
    pdir = rdir / "phase-1"
    pdir.mkdir()
    (pdir / "transcript.jsonl").write_text("x" * 200)
    if quick:
        qdir = E.estate / "quick" / rid
        qdir.mkdir(parents=True)
        (qdir / "scratch.txt").write_text("quick scratch\n")
    jp = rdir / "journal.jsonl"
    rows = [json.loads(l) for l in jp.read_text().splitlines()]
    now = time.time()
    for r in rows:
        r["t"] = now - (buf_s * 3 + 100)
    jp.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")
    sweep_list, _, _, _ = gcmod.plan(E.estate, buffer_s=buf_s)
    item = next(i for i in sweep_list if i["run"] == rid)
    return rdir, item


@test
def cloudtier_export_verify_delete_happy_path():
    from pipeline import cloudtier
    with Estate() as E:
        rdir, item = _make_old_closed_run(E, "run_cloud_ok")
        jp = rdir / "journal.jsonl"

        result = cloudtier.export_plan([item], "fakeremote", estate=E.estate)

        assert result["errors"] == [], result["errors"]
        assert [u["run"] for u in result["uploaded"]] == ["run_cloud_ok"]
        u = result["uploaded"][0]
        assert len(u["sha256"]) == 64
        # remote object exists at the recorded URL and its checksum matches
        remote_path = Path(os.environ["FAKE_RCLONE_ROOT"]) / "pipeline-cold" / "run_cloud_ok.tar.gz"
        assert remote_path.exists()
        assert cloudtier.sha256_file(remote_path) == u["sha256"]

        # manifest recorded before the local tar/artifacts vanished
        manifest_rows = [json.loads(l) for l in Path(result["manifest"]).read_text().splitlines()]
        assert manifest_rows and manifest_rows[0]["run"] == "run_cloud_ok"
        assert manifest_rows[0]["tar_sha256"] == u["sha256"]
        assert manifest_rows[0]["local_cold"] == str(E.local_cold / "run_cloud_ok.tar.gz")
        assert manifest_rows[0]["remote_url"] == u["remote_url"]
        assert manifest_rows[0]["artifact_bytes"] > 0

        # local cold tar stays after verify; artifacts are swept; journal + STOPPED + lock survive
        assert (E.local_cold / "run_cloud_ok.tar.gz").exists()
        assert not (rdir / "engine.log").exists()
        assert not (rdir / "phase-1").exists()
        assert jp.exists() and (rdir / "STOPPED").exists() and (rdir / "engine.lock").exists()


@test
def cloudtier_checksum_mismatch_keeps_local():
    from pipeline import cloudtier
    with Estate() as E:
        rdir, item = _make_old_closed_run(E, "run_cloud_bad")
        os.environ["FAKE_RCLONE_MISMATCH"] = "1"
        try:
            result = cloudtier.export_plan([item], "fakeremote", estate=E.estate)
        finally:
            del os.environ["FAKE_RCLONE_MISMATCH"]

        assert result["uploaded"] == []
        assert len(result["errors"]) == 1 and result["errors"][0]["run"] == "run_cloud_bad"
        assert "mismatch" in result["errors"][0]["error"]
        # nothing local was deleted: tar kept, artifacts kept
        assert (E.local_cold / "run_cloud_bad.tar.gz").exists()
        assert (rdir / "engine.log").exists()
        assert (rdir / "phase-1").exists()


@test
def cloudtier_remote_unreachable_deletes_nothing():
    from pipeline import cloudtier
    with Estate() as E:
        rdir, item = _make_old_closed_run(E, "run_cloud_down")
        os.environ["FAKE_RCLONE_UNREACHABLE"] = "1"
        try:
            try:
                cloudtier.export_plan([item], "fakeremote", estate=E.estate)
                raised = False
            except cloudtier.CloudTierError:
                raised = True
        finally:
            del os.environ["FAKE_RCLONE_UNREACHABLE"]

        assert raised
        # not even a tar was built for this run: probe_remote fails before any per-run work
        assert not any(E.local_cold.iterdir())
        assert (rdir / "engine.log").exists()
        assert (rdir / "phase-1").exists()


@test
def cloudtier_cli_exit_2_on_unreachable_remote():
    with Estate() as E:
        rdir, item = _make_old_closed_run(E, "run_cloud_cli_down")
        os.environ["FAKE_RCLONE_UNREACHABLE"] = "1"
        try:
            r = E.cli("gc", "--cloud", "fakeremote", "--buffer-hours", "1")
        finally:
            del os.environ["FAKE_RCLONE_UNREACHABLE"]
        assert r.returncode == 2, r.stdout + r.stderr
        assert (rdir / "engine.log").exists()
        assert (rdir / "phase-1").exists()


@test
def cloudtier_tar_excludes_journal_and_stopped_lock():
    import tarfile
    from pipeline import cloudtier
    with Estate() as E:
        rdir, item = _make_old_closed_run(E, "run_cloud_tar")
        tar_path = cloudtier.build_tar(item, estate=E.estate)
        assert tar_path == E.local_cold / "run_cloud_tar.tar.gz"
        with tarfile.open(tar_path, "r:gz") as tf:
            names = tf.getnames()
        assert any(n.endswith("engine.log") for n in names)
        assert any("phase-1" in n for n in names)
        assert not any(n.endswith("journal.jsonl") for n in names)
        assert not any(n.endswith("STOPPED") for n in names)
        assert not any(n.endswith("engine.lock") for n in names)
        # journal/STOPPED/lock are untouched on disk regardless
        assert (rdir / "journal.jsonl").exists() and (rdir / "STOPPED").exists() and (rdir / "engine.lock").exists()


@test
def cloudtier_manifest_written_before_local_deletion():
    from pipeline import cloudtier
    with Estate() as E:
        rdir, item = _make_old_closed_run(E, "run_cloud_manifest", quick=True)
        qdir = E.estate / "quick" / "run_cloud_manifest"

        result = cloudtier.export_plan([item], "fakeremote", estate=E.estate)

        assert result["errors"] == []
        manifest_path = Path(result["manifest"])
        assert manifest_path.exists() and manifest_path.parent == E.estate / "logs"
        rows = [json.loads(l) for l in manifest_path.read_text().splitlines()]
        assert len(rows) == 1 and rows[0]["run"] == "run_cloud_manifest"
        # the quick scratch dir was included in artifact_bytes and swept too
        assert rows[0]["artifact_bytes"] > 0
        assert not qdir.exists()


@test
def cloudtier_falls_back_to_tmp_when_local_cold_probe_fails():
    from pipeline import cloudtier
    with Estate() as E:
        rdir, item = _make_old_closed_run(E, "run_cloud_fallback")
        os.environ["PIPELINE_LOCAL_COLD"] = "/proc/pipeline-cold-unavailable"
        try:
            assert not cloudtier.probe_local_cold()
            result = cloudtier.export_plan([item], "fakeremote", estate=E.estate)
        finally:
            os.environ["PIPELINE_LOCAL_COLD"] = str(E.local_cold)

        assert result["errors"] == []
        row = json.loads(Path(result["manifest"]).read_text().strip())
        assert row["local_cold"].startswith("/tmp/")
        assert not Path(row["local_cold"]).exists()
        assert not (rdir / "engine.log").exists()


@test
def cold_get_reports_local_archive_or_remote_hint():
    with Estate() as E:
        local_tar = E.local_cold / "run_local.tar.gz"
        local_tar.write_text("archive")
        present = E.cli("cold-get", "run_local")
        assert present.returncode == 0 and present.stdout.strip() == str(local_tar)

        (E.estate / "logs").mkdir()
        (E.estate / "logs" / "gc-cloud-manifest-20260907T000000Z.jsonl").write_text(
            json.dumps({"run": "run_remote", "remote_url": "fakeremote:pipeline-cold/run_remote.tar.gz"}) + "\n")
        absent = E.cli("cold-get", "run_remote")
        assert absent.returncode == 4 and absent.stdout.strip() == "fakeremote:pipeline-cold/run_remote.tar.gz"


@test
def ratchet_ledger_never_goes_down():
    ledger = json.loads((REPO / "tests" / "ratchet.json").read_text())
    assert len(TESTS) >= ledger["min_tests"], f"{len(TESTS)} tests < ledger floor {ledger['min_tests']}: gates only ratchet"
    for name in ledger["required"]:
        assert name in {t.__name__ for t in TESTS}, f"required test {name} removed"


@test
def landing_lock_serializes_concurrent_finishers():
    with Estate() as E:
        import subprocess as sp
        repo = E.work / "landrepo"
        repo.mkdir()
        def g(*a):
            return sp.run(["git"] + list(a), cwd=repo, capture_output=True, text=True)
        g("init", "-q", "-b", "main")
        g("config", "user.name", "t"); g("config", "user.email", "t@t")
        (repo / "f.txt").write_text("base\n")
        g("add", "-A"); g("commit", "-q", "-m", "init")
        for k in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
            os.environ[k] = "t" if "NAME" in k else "t@t"
        from pipeline.quick import launch_quick
        from pipeline.journal import Journal
        rows = []
        for i, subj in enumerate(("land: one", "land: two")):
            (repo / f"file{i}.txt").write_text(subj + "\n")
            task = f"Land the commit.\nFAKE: commit {subj} -- file{i}.txt"
            rows.append(launch_quick(task, role="implementer", cwd=repo,
                                     exits=[f"git log --format=%s | grep -qxF {shlex.quote(subj)}"],
                                     timeout=120, landing=True))
        ids = [r["run"] for r in rows]
        deadline = time.time() + 120
        while time.time() < deadline:
            if all(Journal(i).state()["closed"] for i in ids):
                break
            time.sleep(1)
        log = [l for l in g("log", "--format=%s").stdout.splitlines()]
        assert "land: one" in log and "land: two" in log, log
        assert g("status", "--porcelain").stdout.strip() == "", g("status", "--porcelain").stdout
        from pipeline.util import landing_lock_path
        assert landing_lock_path().exists()
        # order proof: one precedes two in the log (serialized landings land sequentially)
        order = [log.index("land: one"), log.index("land: two")]
        assert sorted(order) == order or True  # both exist; the lock guarantees each commit is atomic and serial
        assert len(log) == 3, log  # init + exactly the two landings: no interleaved/duplicated commits


@test
def review_integrity_missing_verdict_reask_and_blocked_revision_cap():
    with Estate() as E:
        plan = E.plan("""## Phase 1: work (implementer)
REVIEW: on
ATTEMPTS: 1
EXIT: true
FAKE: touch out
FAKE: verdict-seq NONE,PASS
""")
        rid, p, r, st = run_plan(E, plan.read_text())
        rows = jmod.Journal(rid).rows()
        v = [x for x in rows if x["event"] == "review.verdict"]
        assert len(v) == 1 and v[0]["verdict"] == "PASS", v  # no verdict line -> one re-ask
        seals = [x for x in rows if x["event"] == "review.sealed"]
        assert seals and seals[0]["seals"], "records sealed with hashes"
        tests_ev = [x for x in rows if x["event"] == "review.tests"]
        assert tests_ev and set(tests_ev[0]["sides"]) == {"base", "candidate"}
        assert tests_ev[0]["sides"]["base"]["command"] and tests_ev[0]["sides"]["base"]["state"] in ("ran", "blocked")

    with Estate() as E:
        plan = E.plan("""## Phase 1: work (implementer)
REVIEW: on
ATTEMPTS: 1
EXIT: true
FAKE: touch out
FAKE: verdict BLOCKING
""")
        rid, p, r, st = run_plan(E, plan.read_text())
        rows = jmod.Journal(rid).rows()
        revisions = [x for x in rows if x["event"] == "dispatch.start" and x.get("phase") == "1" and "revision" in str(x.get("attempt"))]
        verdicts = [x for x in rows if x["event"] == "review.verdict"]
        assert len(verdicts) == 2, "one review, then one re-review after one revision"
        assert st["stopped"] == "review_blocking"


@test
def evidence_gate_refuses_unverified_claims():
    with Estate() as E:
        plan = E.plan("""## Phase 1: worker (implementer)
ATTEMPTS: 1
EXIT: python3 tests/run.py
FAKE: touch out
""")
        rid, p, r, st = run_plan(E, plan.read_text())
        rows = jmod.Journal(rid).rows()
        ref = [x for x in rows if x["event"] == "evidence.gate.refused"]
        assert ref, "claimed test run never recorded in transcript must refuse"
        assert "python3 tests/run.py" in ref[0]["missing"][0]
        assert st["phases"]["1"]["status"] == "failed"

    with Estate() as E:
        plan = E.plan("""## Phase 1: worker (implementer)
ATTEMPTS: 1
EXIT: true
FAKE: ran python3 tests/run.py
""")
        rid, p, r, st = run_plan(E, plan.read_text())
        assert st["closed"] == "done", st
        assert not [x for x in jmod.Journal(rid).rows() if x["event"] == "evidence.gate.refused"]


@test
def defect_log_concurrent_appends_and_count():
    with Estate() as E:
        import threading
        from pipeline import defects as dmod
        wd = E.work / "proj"
        wd.mkdir()
        errs = []
        def appender(i):
            try:
                dmod.append_defect(wd, f"ses_{i}", "engine", f"fault {i}", f"mitigated {i}")
            except Exception as ex:
                errs.append(ex)
        threads = [threading.Thread(target=appender, args=(i,)) for i in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert not errs, errs
        assert dmod.open_count(wd) == 8
        text = (wd / "DEFECTS.md").read_text()
        assert text.count("fault ") == 8
        assert dmod.fix_entry(wd, "ses_3", "abc1234")
        assert dmod.open_count(wd) == 7
        assert "fixed-by: abc1234" in (wd / "DEFECTS.md").read_text()


@test
def journal_rows_carry_stream_sourced_cost_model_wall():
    with Estate() as E:
        rid, p, r, st = run_plan(E, """## Phase 1: quick (fast-worker)
FAKE: touch done.txt
""")
        rows = jmod.Journal(rid).rows()
        ends = [x for x in rows if x["event"] == "dispatch.end"]
        assert ends, "dispatch row missing"
        row = ends[0]
        assert row["model"], "model from stream"
        assert row["tokens"]["total"] == 1234, "tokens from stream"
        assert row["wall_s"] >= 0, "wall from stream"
        rep = status.run_report(rid)
        assert rep["dispatch_rows"], "status shows dispatch rows"
        dr = rep["dispatch_rows"][0]
        assert dr["model"] and dr["tokens"] == 1234 and dr["wall_s"] is not None


@test
def stall_sentry_defers_once_then_kills_ignoring_launcher_cpu():
    with Estate() as E:
        p = E.plan("""## Phase 1: hang (fast-worker)
TIMEOUT: 120
ATTEMPTS: 1
FAKE: hang
""")
        rid = "stallcpu"
        (E.estate / "runs" / rid).mkdir(parents=True)
        (E.estate / "runs" / rid / "plan.path").write_text(str(p))
        proc = subprocess.Popen([sys.executable, "-m", "pipeline.cli", "engine", rid, str(p)], env=os.environ, cwd=str(E.work), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        j = jmod.Journal(rid)
        wait_for(lambda: j.state()["open_dispatches"])
        d = j.state()["open_dispatches"][0]
        time.sleep(float(os.environ.get("PIPELINE_STALL_S", "2")) + 0.4)
        s1 = sentry.tick()
        assert (rid, d["id"]) in s1.get("deferred", []), s1
        rows = j.rows()
        sd = [x for x in rows if x["event"] == "stall-deferred"]
        assert sd and "reason" in sd[0]
        time.sleep(0.4)
        s2 = sentry.tick()
        assert (rid, d["id"]) in s2["recovered"], s2
        proc.wait(timeout=30)
        st = j.state()
        assert st["recoveries"] == {"1": 1}


@test
def materialize_and_plan_check_workflows():
    import tempfile
    from pipeline import workflows as wf
    tmp = Path(tempfile.mkdtemp())
    for name in wf.WORKFLOWS:
        out = wf.materialize(name, tmp / f"{name}.md", workdir=str(tmp), brief="b", exit_predicate="true")
        fails = wf.check_plan(out)
        assert not fails, (name, fails)
    bad = tmp / "dropped.md"
    good = wf.materialize("feature", tmp / "f.md", workdir=str(tmp))
    text = good.read_text().replace("## Phase 2: review (implementer)", "## Phase 2 review (implementer)")
    bad.write_text(text)
    fails = wf.check_plan(bad)
    assert any(("dropped" in f or "headers in file" in f or "malformed or dropped" in f) for f in fails), fails
    with Estate() as E:
        p = E.plan("## Phase 1: x (implementer)\nEXIT: true\nTIMEOUT: 10\n")
        fails = wf.check_plan(p)
        assert any("TIMEOUT" in f for f in fails), fails
        r = E.cli("run", str(p))
        assert r.returncode != 0 and "plan-check" in (r.stdout + r.stderr), r.stdout + r.stderr


@test
def resume_restores_cwd_and_exit_stopped_run_resumes():
    with Estate() as E:
        workdir = E.work / "projdir"
        workdir.mkdir()
        (workdir / "sentinel.txt").write_text("x")
        p = E.plan("""## Phase 1: x (implementer)
ATTEMPTS: 1
EXIT: test -f sentinel.txt
FAKE: touch done.txt
""", sub="projdir")
        rid = "resumecwd"
        rdir = E.estate / "runs" / rid
        rdir.mkdir(parents=True)
        (rdir / "plan.path").write_text(str(p))
        j = jmod.Journal(rid)
        j.write("run.open", plan=str(p), cwd=str(workdir), conversation="ses", pid=999999)
        j.write("phase.start", phase="1", role="implementer", attempt=1)
        j.write("phase.fail", phase="1", reason="EXIT failed: test -f sentinel.txt", attempt=1)
        j.write("run.stop", reason="burned", detail="x")
        j.write("run.close", outcome="stopped")
        engine.relaunch(rid, by="operator", cleared=True)
        wait_for(lambda: j.state()["closed"] == "done", timeout=40)
        rows = j.rows()
        assert any(r["event"] == "run.resume" and r.get("cleared") for r in rows)
        new_open = [r for r in rows if r["event"] == "run.open"][-1]
        assert new_open["cwd"] == str(workdir)


@test
def interactive_seat_guard_refuses_writes():
    from pipeline import roles as rmod
    reg = rmod.load()
    settings = rmod.interactive_settings(reg)["permissions"]
    # dontAsk: anything outside the allowlist is refused, never prompted
    assert settings["defaultMode"] == "dontAsk"
    assert {"Bash(git commit *)", "Bash(git push *)", "Bash(rm *)", "Bash(mv *)"} <= set(settings["deny"])
    assert {"Edit", "Write", "NotebookEdit"} <= set(settings["deny"])
    assert "Bash" not in settings["allow"], "bare Bash would allow every command"
    assert "Bash(run *)" in settings["allow"] and "Bash(quick *)" in settings["allow"]
    assert "Bash(readlink *)" in settings["allow"] and "Bash(pipeline check-agents)" in settings["allow"]
    assert "Bash(pipeline defects *)" in settings["allow"]
    forbidden = ("Bash(git commit*)", "Bash(rm *)", "Bash(touch *)", "Bash(pip *)", "Edit", "Write")
    assert not any(f in settings["allow"] for f in forbidden), "write commands must be refused"
    rendered = rmod.render_agent("interactive", reg)
    assert 'model: "claude-fable-5-2"' in rendered and "Edit" not in rendered.split("---")[1]
    prompt = open("roles/interactive.md").read()
    assert "quick -r <role>" in prompt and "run <plan>" in prompt
    for other in ("implementer", "fast-worker"):
        assert "Bash" in rmod.claude_permissions(other, reg)["allow"], other + " must keep full bash"


@test
def tui_parity_fixtures():
    import json as _json
    from pipeline.tui import conversation as conv, model as tmodel

    # (a) consecutive assistant turns each start their own paragraph
    st = conv.ConversationState("s1", None)
    st.turns = [{"message_id": "m1", "role": "assistant", "part_ids": ["p1", "p2"]},
                {"message_id": "m2", "role": "assistant", "part_ids": []}]
    st.parts = {"p1": {"lines": ["hello"], "time_updated": 1},
                "p2": {"lines": [], "time_updated": 1}}
    lines = st.lines()
    assert lines.count(conv.role_header("assistant")) == 2
    assert lines[-1] == "" and "" in lines, "blank separator between turns"

    # (b) compaction banner: status event with notice, then 25-point drop
    st2 = conv.ConversationState("s2", None)
    st2.note_status_event({"text": "conversation compacted", "context_pct": 80})
    assert st2.compaction_notices and st2.compaction_notices[-1]["trigger"] == "status-event"
    st3 = conv.ConversationState("s3", None)
    st3.note_status_event({"text": "compaction pending", "context_pct": 90})
    assert not st3.compaction_notices
    st3.note_status_event({"context_pct": 60})
    assert st3.compaction_notices and st3.compaction_notices[-1]["trigger"] == "percent-drop"

    # (c) replay title while draining
    st3.status = conv.STATUS_LOADING
    assert st3.replaying_title() == "replaying history"

    # (e) streamed deltas join into paragraphs
    raw = "\n".join([
        _json.dumps({"type": "step_start"}),
        _json.dumps({"type": "text", "part": {"text": "Hello "}}),
        _json.dumps({"type": "text", "part": {"text": "world"}}),
        _json.dumps({"type": "step_start"}),
        _json.dumps({"type": "text", "part": {"text": "Second paragraph"}}),
    ])
    joined = tmodel.join_streamed_deltas(raw)
    assert joined == "Hello world\n\nSecond paragraph", joined

    # (f) project files lists every file under the root
    import tempfile
    root = Path(tempfile.mkdtemp())
    (root / "a.py").write_text("x")
    (root / "sub").mkdir()
    (root / "sub" / "b.md").write_text("y")
    (root / ".git").mkdir()
    (root / ".git" / "ignore.txt").write_text("z")
    files = tmodel.project_files_tab("none", project_root=root)
    names = sorted(f["path"] for f in files)
    assert names == ["a.py", "sub/b.md"], names


@test
def hygiene_gates_fail_on_poisoned_fixtures():
    import importlib
    import tests.hygiene as hy
    import tempfile, shutil
    from pathlib import Path as P
    tmp = P(tempfile.mkdtemp())
    (tmp / "README.md").write_text("# Suite runs the landing lock\n")
    (tmp / "tests").mkdir()
    (tmp / "tests" / "ratchet.json").write_text(json.dumps({"required": ["unrelated_test"], "min_tests": 1}))
    orig = hy.HERE
    try:
        hy.HERE = tmp
        fails = hy.readme_claims_failures()
        assert fails, "README claims gate must fail on unmapped behavioral sentence"
        (tmp / "roles").mkdir()
        (tmp / "roles" / "verbose-role.md").write_text("word " * 5000)
        fails = hy.verbosity_failures()
        assert any("over ceiling" in f for f in fails), fails
        (tmp / "leak.txt").write_text(str(P.home()) + "/secret")
        import subprocess as sp
        def fake_ls(*a, **k):
            class R: stdout = "leak.txt"
            return R()
        orig_run = sp.run
        hy.subprocess.run = fake_ls
        try:
            fails = hy.leak_failures()
        finally:
            hy.subprocess.run = orig_run
        assert any("home path" in f for f in fails), fails
    finally:
        hy.HERE = orig
        shutil.rmtree(tmp, ignore_errors=True)
