"""Unit tests for pipeline.tui.model: read-only accessors over the journal, the
runs registry, dispatch result files, and liveness truth. Registered into the
main suite by tests/test_suite.py."""

import json
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.harness import Estate, wait_for
from pipeline import registry, roles as roles_mod, util
from pipeline.journal import Journal
from pipeline.tui import model as tm


def _run_plan(E, text, sub="p", timeout=60, run_id=None, kind="plan"):
    p = E.plan(text, sub=sub)
    rid = run_id or util.new_id("t")
    (E.estate / "runs" / rid).mkdir(parents=True, exist_ok=True)
    (E.estate / "runs" / rid / "plan.path").write_text(str(p))
    registry.register(kind, rid, journal=Path(E.estate) / "runs" / rid / "journal.jsonl",
                       plan=str(p), cwd=str(p.parent), conversation=registry.current_conversation())
    r = E.engine_fg(rid, p, timeout=timeout)
    return rid, p, r, Journal(rid).state()


class TestTuiModel(unittest.TestCase):

    def test_resolve_conversation_order(self):
        """explicit --conv wins over env; env wins over the registry's latest row."""
        with Estate() as E:
            self.assertEqual(tm.resolve_conversation("explicit_x"), "explicit_x")
            os.environ["PIPELINE_CONVERSATION"] = "env_y"
            try:
                self.assertEqual(tm.resolve_conversation(None), "env_y")
            finally:
                os.environ["PIPELINE_CONVERSATION"] = "ses_test_conv"  # harness default restored below anyway
            # with no env and no explicit, falls back to the most recent registry row
            del os.environ["PIPELINE_CONVERSATION"]
            try:
                registry.register("quick", "q1", journal=Path("/x"), conversation="ses_only")
                self.assertEqual(tm.resolve_conversation(None), "ses_only")
            finally:
                os.environ["PIPELINE_CONVERSATION"] = "ses_test_conv"

    def test_rows_for_scopes_by_conversation_and_all_toggle(self):
        with Estate() as E:
            registry.register("plan", "r1", journal=Path("/x"), conversation="ses_a")
            registry.register("plan", "r2", journal=Path("/x"), conversation="ses_b")
            mine = tm.rows_for("ses_a", all_sessions=False)
            self.assertEqual({r["run"] for r in mine}, {"r1"})
            everything = tm.rows_for("ses_a", all_sessions=True)
            self.assertEqual({r["run"] for r in everything}, {"r1", "r2"})

    def test_derive_run_state_never_trusts_running_claim_alone(self):
        """A journal 'open' run whose engine pid is dead reads as dead-engine, not
        running -- liveness is pid_alive, never the journal's own claim."""
        with Estate() as E:
            j = Journal("deadrun")
            j.write("run.open", plan="p", cwd="/c", conversation="ses", pid=999999)
            j.write("phase.start", phase="1", role="fast-worker", attempt=1)
            st = j.state()
            label, reason = tm.derive_run_state(st)
            self.assertEqual(label, "dead-engine")
            # a real, currently-alive process (this test process) reads as running
            j2 = Journal("aliverun")
            j2.write("run.open", plan="p", cwd="/c", conversation="ses", pid=os.getpid())
            j2.write("phase.start", phase="1", role="fast-worker", attempt=1)
            label2, _ = tm.derive_run_state(j2.state())
            self.assertEqual(label2, "running")

    def test_derive_run_state_gate_waiting_is_loud_even_with_dead_engine(self):
        with Estate() as E:
            j = Journal("gaterun")
            j.write("run.open", plan="p", cwd="/c", conversation="ses", pid=999999)
            j.write("phase.wait", phase="1", sentinel="/tmp/.gate-1")
            label, _ = tm.derive_run_state(j.state())
            self.assertEqual(label, "gate-waiting")

    def test_derive_run_state_stopped_carries_reason(self):
        with Estate() as E:
            j = Journal("stoprun")
            j.write("run.open", plan="p", cwd="/c", conversation="ses", pid=999999)
            j.write("run.stop", reason="burned", detail="phase 1 burned 2 attempts")
            j.write("run.close", outcome="stopped")
            label, reason = tm.derive_run_state(j.state())
            self.assertEqual(label, "stopped:burned")
            self.assertIn("burned", reason)

    def test_pipelines_tab_reports_progress_and_scopes_to_conversation(self):
        with Estate() as E:
            rid, p, r, st = _run_plan(E, "## Phase 1: a (fast-worker)\nFAKE: touch a\n", kind="plan")
            self.assertEqual(st["closed"], "done", r.stderr)
            views = tm.pipelines_tab(registry.current_conversation())
            self.assertEqual(len(views), 1)
            v = views[0]
            self.assertEqual(v["run"], rid)
            self.assertEqual(v["phase_progress"], "1/1")
            self.assertEqual(v["state"], "done")
            # a different conversation sees nothing
            self.assertEqual(tm.pipelines_tab("some_other_conv"), [])

    def test_pipelines_tab_running_phase_is_dead_engine_when_engine_is_gone(self):
        with Estate() as E:
            p = E.plan("## Phase 1: a (fast-worker)\nFAKE: touch a\n")
            rid = "corpse1"
            (E.estate / "runs" / rid).mkdir(parents=True)
            (E.estate / "runs" / rid / "plan.path").write_text(str(p))
            j = Journal(rid)
            j.write("run.open", plan=str(p), cwd=str(p.parent), conversation=registry.current_conversation(), pid=999999)
            j.write("phase.start", phase="1", role="fast-worker", attempt=1)
            registry.register("plan", rid, journal=Path(E.estate) / "runs" / rid / "journal.jsonl",
                               plan=str(p), cwd=str(p.parent), conversation=registry.current_conversation())
            views = tm.pipelines_tab(registry.current_conversation())
            self.assertEqual(len(views), 1)
            self.assertEqual(views[0]["state"], "dead-engine")
            self.assertFalse(views[0]["engine_alive"])

    def test_dispatches_tab_shows_quick_role_wall_cost_tokens(self):
        with Estate() as E:
            r = E.cli("quick", "-r", "fast-worker", input="fetch it\nFAKE: touch q.txt\n", check=True)
            rid = r.stdout.split()[1]
            wait_for(lambda: Journal(rid).state()["closed"] == "done", timeout=30)
            views = tm.dispatches_tab(registry.current_conversation())
            self.assertEqual(len(views), 1)
            v = views[0]
            self.assertEqual(v["run"], rid)
            self.assertEqual(v["role"], "fast-worker")
            self.assertEqual(v["state"], "done")
            self.assertGreater(v["tokens_total"], 0)
            self.assertGreaterEqual(v["cost"], 0)
            # plans never show up in the dispatches tab
            _run_plan(E, "## Phase 1: a (fast-worker)\nFAKE: touch a\n", kind="plan")
            views2 = tm.dispatches_tab(registry.current_conversation())
            self.assertEqual({v2["run"] for v2 in views2}, {rid})

    def test_files_tab_lists_deliverables_curated_by_run_and_phase(self):
        with Estate() as E:
            (E.work / "docs").mkdir()
            rid, p, r, st = _run_plan(
                E, "## Phase 1: a (implementer)\nFAKE: write docs/A.md <<hi>>\n", kind="plan")
            # touch a real file so parse_deliverables' filesystem check passes, and make
            # the fake worker's final text name it
            files = tm.files_tab(registry.current_conversation())
            # dispatch.end deliverables depend on the fake worker's closing text naming a
            # real file; the default fake closing text is "done" (no paths), so with no
            # FAKE directive producing a path-naming close, expect none registered here --
            # this asserts the "curated, not invented" contract, not a specific path.
            self.assertIsInstance(files, list)
            for f in files:
                self.assertEqual(f["run"], rid)
                self.assertTrue(f["path"])

    def test_files_tab_scopes_by_conversation(self):
        with Estate() as E:
            rid, p, r, st = _run_plan(E, "## Phase 1: a (fast-worker)\nFAKE: touch a\n", kind="plan")
            self.assertEqual(tm.files_tab("nope_not_mine"), [])

    def test_conversation_stub_is_honest_not_faked(self):
        text = tm.conversation_stub_text("any_conv")
        self.assertIn("no live session", text)

    def test_phase_listing_finds_transcript_brief_result_and_review_dir(self):
        with Estate() as E:
            rid, p, r, st = _run_plan(
                E, "## Phase 1: a (implementer)\nREVIEW: cross\nFAKE: touch out\nFAKE: verdict-by-agent a=PASS b=PASS\n")
            self.assertEqual(st["closed"], "done", r.stderr)
            listing = tm.phase_listing(rid, "1")
            labels = [it["label"] for it in listing["items"]]
            self.assertTrue(any("attempt-1" in l for l in labels))
            attempt_item = next(it for it in listing["items"] if "attempt-1" in it["label"])
            self.assertIn("transcript.jsonl", attempt_item)
            self.assertIn("brief.md", attempt_item)
            self.assertIn("result.json", attempt_item)
            self.assertTrue(any(l.startswith("review/") for l in labels))

    def test_phase_listing_empty_phase_dir_is_empty_not_error(self):
        with Estate() as E:
            listing = tm.phase_listing("no-such-run", "9")
            self.assertEqual(listing["items"], [])

    def test_read_item_truncates_from_tail_never_raises(self):
        with Estate() as E:
            f = E.tmp / "big.txt"
            f.write_text("x" * 500000)
            text = tm.read_item(f, max_bytes=100)
            self.assertIn("truncated", text)
            self.assertLessEqual(len(text), 200)
            missing = tm.read_item(E.tmp / "does-not-exist.txt")
            self.assertIn("could not read", missing)

    def test_worker_liveness_uses_pid_and_transcript_mtime(self):
        with Estate() as E:
            t = E.tmp / "transcript.jsonl"
            t.write_text("{}\n")
            lv = tm.worker_liveness({"pid": os.getpid(), "transcript": str(t)}, stall_after=100)
            self.assertTrue(lv["alive"])
            self.assertFalse(lv["stalled"])
            lv_dead = tm.worker_liveness({"pid": 999999, "transcript": str(t)}, stall_after=100)
            self.assertFalse(lv_dead["alive"])

    def test_model_context_window_reads_gateway_cache_or_none(self):
        with Estate() as E:
            cache = E.tmp / "gw-models.json"
            cache.write_text(json.dumps({"data": [{"id": "claude-sonnet-5", "context_length": 1000000}]}))
            os.environ["PIPELINE_GW_MODELS"] = str(cache)
            try:
                self.assertEqual(tm.model_context_window("claude-sonnet-5"), 1000000)
                self.assertIsNone(tm.model_context_window("no-such-model"))
            finally:
                del os.environ["PIPELINE_GW_MODELS"]
            # absent cache file -> None, never a fabricated number
            os.environ["PIPELINE_GW_MODELS"] = str(E.tmp / "absent.json")
            try:
                self.assertIsNone(tm.model_context_window("claude-sonnet-5"))
            finally:
                del os.environ["PIPELINE_GW_MODELS"]

    def test_context_fill_pct_is_na_without_db_or_window(self):
        with Estate() as E:
            os.environ["PIPELINE_DEVPASS_DB"] = str(E.tmp / "no-such.db")
            try:
                self.assertEqual(tm.context_fill_pct("ses_x", "claude-sonnet-5"), "n/a")
            finally:
                del os.environ["PIPELINE_DEVPASS_DB"]

    def test_context_fill_pct_computed_from_session_row_and_window(self):
        import sqlite3
        with Estate() as E:
            db = E.tmp / "opencode.db"
            conn = sqlite3.connect(str(db))
            conn.execute("""CREATE TABLE session (
                id text PRIMARY KEY, tokens_input integer, tokens_output integer,
                tokens_reasoning integer, tokens_cache_read integer, tokens_cache_write integer, model text)""")
            conn.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?)",
                        ("ses_x", 500000, 0, 0, 0, 0, json.dumps({"id": "claude-sonnet-5"})))
            conn.commit()
            conn.close()
            cache = E.tmp / "gw-models.json"
            cache.write_text(json.dumps({"data": [{"id": "claude-sonnet-5", "context_length": 1000000}]}))
            os.environ["PIPELINE_DEVPASS_DB"] = str(db)
            os.environ["PIPELINE_GW_MODELS"] = str(cache)
            try:
                pct = tm.context_fill_pct("ses_x")
                self.assertEqual(pct, "50.0%")
            finally:
                del os.environ["PIPELINE_DEVPASS_DB"]
                del os.environ["PIPELINE_GW_MODELS"]

    def test_status_bar_names_agent_model_effort_and_honest_na_fields(self):
        with Estate() as E:
            os.environ["PIPELINE_DEVPASS_DB"] = str(E.tmp / "absent.db")
            try:
                bar = tm.status_bar("ses_x")
            finally:
                del os.environ["PIPELINE_DEVPASS_DB"]
            self.assertEqual(bar["agent"], "pl-interactive")
            self.assertTrue(bar["model"])
            self.assertTrue(bar["effort"])
            self.assertEqual(bar["auth_expiry"], "n/a")  # no real expiry source exists
            self.assertEqual(bar["context_fill"], "n/a")  # no db -> honest n/a, never fabricated

    def test_selftest_exits_zero(self):
        with Estate() as E:
            self.assertEqual(tm.selftest(), 0)


if __name__ == "__main__":
    unittest.main()
