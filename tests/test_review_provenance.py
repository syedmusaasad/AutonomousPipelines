"""Test suite for review provenance and isolation.

Tests verify that:
1. Review verdicts are properly created and not inherited from prior test context
2. A failed reviewer dispatch is never trusted, even with a leftover VERDICT file
3. The sealed (read-only) Codex reviewer's final message becomes the sealed record
4. Directory separation is maintained between reviews
5. Nested test suites don't pollute external review files via inherited REVIEW_OUT
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.harness import Estate
from pipeline import journal as jmod
from pipeline import roles as roles_mod
from pipeline import util


def run_plan(E, text, sub="p", timeout=60, run_id=None):
    """Helper from test_suite.py"""
    p = E.plan(text, sub=sub)
    rid = run_id or util.new_id("t")
    (E.estate / "runs" / rid).mkdir(parents=True, exist_ok=True)
    (E.estate / "runs" / rid / "plan.path").write_text(str(p))
    r = E.engine_fg(rid, p, timeout=timeout)
    return rid, p, r, jmod.Journal(rid).state()


class TestReviewProvenance(unittest.TestCase):
    """Review provenance and isolation tests (one sealed Codex reviewer)."""

    def _review_path(self, E, rid, phase=1, name="reviewer"):
        return Path(E.estate) / "runs" / rid / f"phase-{phase}/review/{name}/review.md"

    def test_review_dispatch_success_creates_verdict(self):
        """Successful reviewer dispatch yields one verdict; the engine writes the record."""
        with Estate() as E:
            rid, p, r, st = run_plan(E, """## Phase 1: work (implementer)
REVIEW: on
FAKE: touch out
FAKE: verdict CONCERNS
""")
            self.assertEqual(st["closed"], "done", r.stderr)
            v = [x for x in jmod.Journal(rid).rows() if x["event"] == "review.verdict"]
            self.assertEqual(len(v), 1, v)
            self.assertEqual((v[0]["verdict"], v[0]["reviewer"]), ("CONCERNS", "reviewer"))
            record = self._review_path(E, rid)
            self.assertTrue(record.exists(), record)
            self.assertTrue(record.read_text().startswith("VERDICT: CONCERNS"))

    def test_timeout_with_unavailable_verdict(self):
        """A reviewer that times out yields UNAVAILABLE, never a verdict from a stale file."""
        with Estate() as E:
            os.environ["PIPELINE_REVIEW_TIMEOUT_S"] = "1"
            try:
                import importlib
                from pipeline import engine
                importlib.reload(engine)
                # pre-seed a stale PASS record where the reviewer will write
                rid = util.new_id("t")
                stale = self._review_path(E, rid)
                stale.parent.mkdir(parents=True, exist_ok=True)
                stale.write_text("VERDICT: PASS\nstale\n")
                rid, p, r, st = run_plan(E, """## Phase 1: work (implementer)
REVIEW: on
ATTEMPTS: 1
FAKE: touch out
FAKE: sleep 3
""", run_id=rid)
            finally:
                os.environ.pop("PIPELINE_REVIEW_TIMEOUT_S", None)
                importlib.reload(engine)
            v = [x for x in jmod.Journal(rid).rows() if x["event"] == "review.verdict"]
            self.assertTrue(v and all(x["verdict"] == "UNAVAILABLE" for x in v), v)
            self.assertEqual(st["stopped"], "burned")

    def test_failed_dispatch_unavailable_verdict(self):
        """A failed reviewer dispatch yields UNAVAILABLE even if it wrote a record."""
        with Estate() as E:
            reg = roles_mod.load()
            rv = roles_mod.seat("reviewer", reg)
            rid, p, r, st = run_plan(E, f"""## Phase 1: work (implementer)
REVIEW: on
ATTEMPTS: 1
FAKE: touch out
FAKE: fail-on-model {rv['model']}
""")
            v = [x for x in jmod.Journal(rid).rows() if x["event"] == "review.verdict"]
            self.assertEqual([x["verdict"] for x in v], ["UNAVAILABLE"], v)
            self.assertEqual(st["stopped"], "burned")

    def test_review_directory_separation(self):
        """Two phases' reviews must be in separate directories."""
        with Estate() as E:
            rid, p, r, st = run_plan(E, """## Phase 1: one (implementer)
REVIEW: on
FAKE: touch out1
FAKE: verdict PASS

## Phase 2: two (implementer)
REVIEW: on
FAKE: touch out2
FAKE: verdict CONCERNS
""")
            self.assertEqual(st["closed"], "done", r.stderr)
            one, two = self._review_path(E, rid, 1), self._review_path(E, rid, 2)
            self.assertTrue(one.exists() and two.exists())
            self.assertNotEqual(one.parent, two.parent)
            self.assertTrue(one.read_text().startswith("VERDICT: PASS"))
            self.assertTrue(two.read_text().startswith("VERDICT: CONCERNS"))

    def test_review_out_not_inherited_between_tests(self):
        """REVIEW_OUT variable should not leak between runs."""
        with Estate() as E:
            rid1, p1, r1, st1 = run_plan(E, """## Phase 1: work (implementer)
REVIEW: on
FAKE: touch out
FAKE: verdict PASS
""", sub="sub1")
            self.assertEqual(st1["closed"], "done")
            self.assertIsNone(os.environ.get("REVIEW_OUT"), "REVIEW_OUT leaked between tests")
            rid2, p2, r2, st2 = run_plan(E, """## Phase 1: work (implementer)
REVIEW: on
FAKE: touch out2
FAKE: verdict CONCERNS
""", sub="sub2")
            self.assertEqual(st2["closed"], "done")
            v1 = {x["verdict"] for x in jmod.Journal(rid1).rows() if x["event"] == "review.verdict"}
            v2 = {x["verdict"] for x in jmod.Journal(rid2).rows() if x["event"] == "review.verdict"}
            self.assertEqual(v1, {"PASS"})
            self.assertEqual(v2, {"CONCERNS"})

    def test_review_verdict_dispatch_path_isolation(self):
        """Review, revision and re-review dispatches each get their own directory."""
        with Estate() as E:
            rid, p, r, st = run_plan(E, """## Phase 1: work (implementer)
REVIEW: on
FAKE: touch out
FAKE: verdict-seq BLOCKING,PASS
""")
            self.assertEqual(st["closed"], "done", r.stderr)
            base = Path(E.estate) / "runs" / rid / "phase-1/review"
            for d in (base / "reviewer/dispatch/try-0", base / "revision/try-0", base / "reviewer-rereview/dispatch/try-0"):
                self.assertTrue((d / "transcript.jsonl").exists(), d)
                self.assertTrue((d / "result.json").exists(), d)
            self.assertTrue((base / "reviewer/review.md").read_text().startswith("VERDICT: BLOCKING"))
            self.assertTrue((base / "reviewer-rereview/review.md").read_text().startswith("VERDICT: PASS"))

    def test_both_reviewers_blocking_stops_phase(self):
        """BLOCKING, one revision, BLOCKING again stops the phase."""
        with Estate() as E:
            rid, p, r, st = run_plan(E, """## Phase 1: work (implementer)
REVIEW: on
FAKE: touch out
FAKE: verdict BLOCKING
""")
            self.assertEqual(st["stopped"], "review_blocking", st)
            v = [x for x in jmod.Journal(rid).rows() if x["event"] == "review.verdict"]
            self.assertEqual([x["verdict"] for x in v], ["BLOCKING", "BLOCKING"])

    def test_one_reviewer_blocking_allows_completion(self):
        """BLOCKING cleared by the revision lets the phase complete."""
        with Estate() as E:
            rid, p, r, st = run_plan(E, """## Phase 1: work (implementer)
REVIEW: on
FAKE: touch out
FAKE: verdict-seq BLOCKING,PASS
""")
            self.assertEqual(st["closed"], "done", st)
            v = [x["verdict"] for x in jmod.Journal(rid).rows() if x["event"] == "review.verdict"]
            self.assertEqual(v, ["BLOCKING", "PASS"])

    def test_review_spans_two_model_families(self):
        """The reviewer sits on a different family (and CLI) than the work it reviews."""
        with Estate() as E:
            rid, p, r, st = run_plan(E, """## Phase 1: work (implementer)
REVIEW: on
FAKE: touch out
FAKE: verdict PASS
""")
            self.assertEqual(st["closed"], "done")
            reg = roles_mod.load()
            rows = jmod.Journal(rid).rows()
            v = [x for x in rows if x["event"] == "review.verdict"]
            worker = [x for x in rows if x["event"] == "dispatch.start" and x["role"] == "implementer"][0]
            self.assertNotEqual(roles_mod.family_of(v[0]["model"], reg), roles_mod.family_of(worker["model"], reg))
            self.assertEqual(roles_mod.cli_of(v[0]["model"], reg), "codex")

    def test_fake_worker_logs_review_out_context(self):
        """The reviewer dispatch carries an absolute REVIEW_OUT and runs on codex."""
        with Estate() as E:
            rid, p, r, st = run_plan(E, """## Phase 1: work (implementer)
REVIEW: on
FAKE: touch out
FAKE: verdict PASS
""")
            self.assertEqual(st["closed"], "done")
            reviewer_calls = [c for c in E.fake_calls() if c.get("agent") == "pl-reviewer"]
            self.assertEqual(len(reviewer_calls), 1, reviewer_calls)
            call = reviewer_calls[0]
            self.assertTrue(call.get("REVIEW_OUT", "").startswith("/"), call)
            self.assertEqual(call["cli"], "codex")
            self.assertIn("--sandbox", call["argv"])
            self.assertEqual(call["argv"][call["argv"].index("--sandbox") + 1], "read-only")


if __name__ == "__main__":
    unittest.main()
