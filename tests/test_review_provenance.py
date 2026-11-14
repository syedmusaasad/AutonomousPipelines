"""Test suite for review provenance and isolation.

Tests verify that:
1. Review verdicts are properly created and not inherited from prior test context
2. A failed reviewer dispatch is never trusted, even with a leftover VERDICT file
3. A sealed reviewer's final message becomes its sealed record (two reviewers, one per CLI)
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
    """Review provenance and isolation tests (two sealed reviewers: Claude Code + Codex)."""

    def _review_path(self, E, rid, phase=1, name="reviewer-a"):
        return Path(E.estate) / "runs" / rid / f"phase-{phase}/review/{name}/review.md"

    def test_review_dispatch_success_creates_verdict(self):
        """Successful dispatch yields one verdict per reviewer; the engine writes the record."""
        with Estate() as E:
            rid, p, r, st = run_plan(E, """## Phase 1: work (implementer)
REVIEW: on
FAKE: touch out
FAKE: verdict CONCERNS
""")
            self.assertEqual(st["closed"], "done", r.stderr)
            v = [x for x in jmod.Journal(rid).rows() if x["event"] == "review.verdict"]
            self.assertEqual(sorted((x["reviewer"], x["verdict"]) for x in v),
                             [("reviewer-a", "CONCERNS"), ("reviewer-b", "CONCERNS")], v)
            for name in ("reviewer-a", "reviewer-b"):
                record = self._review_path(E, rid, name=name)
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
                for name in ("reviewer-a", "reviewer-b"):
                    stale = self._review_path(E, rid, name=name)
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
            rv = roles_mod.seat("reviewer-b", reg)
            rid, p, r, st = run_plan(E, f"""## Phase 1: work (implementer)
REVIEW: on
ATTEMPTS: 1
FAKE: touch out
FAKE: verdict PASS
FAKE: fail-on-model {rv['model']}
""")
            v = [x for x in jmod.Journal(rid).rows() if x["event"] == "review.verdict"]
            self.assertEqual({x["reviewer"]: x["verdict"] for x in v},
                             {"reviewer-a": "PASS", "reviewer-b": "UNAVAILABLE"}, v)
            self.assertEqual(st["stopped"], "burned")

    def test_review_directory_separation(self):
        """Two phases' reviews, and the two reviewers within one, are in separate directories."""
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
            seen = set()
            for phase, verdict in ((1, "PASS"), (2, "CONCERNS")):
                for name in ("reviewer-a", "reviewer-b"):
                    rec = self._review_path(E, rid, phase, name)
                    self.assertTrue(rec.exists(), rec)
                    self.assertTrue(rec.read_text().startswith(f"VERDICT: {verdict}"))
                    seen.add(rec.parent)
            self.assertEqual(len(seen), 4)

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
        """Review, revision and re-review dispatches each get their own directory, per reviewer."""
        with Estate() as E:
            rid, p, r, st = run_plan(E, """## Phase 1: work (implementer)
REVIEW: cross
FAKE: touch out
FAKE: verdict-by-agent a=BLOCKING,PASS b=PASS
""")
            self.assertEqual(st["closed"], "done", r.stderr)
            base = Path(E.estate) / "runs" / rid / "phase-1/review"
            dirs = ["revision", "reviewer-a", "reviewer-b", "reviewer-a-rereview", "reviewer-b-rereview"]
            for d in dirs:
                sub = base / d / ("try-0" if d == "revision" else "dispatch/try-0")
                self.assertTrue((sub / "transcript.jsonl").exists(), sub)
                self.assertTrue((sub / "result.json").exists(), sub)
            self.assertTrue((base / "reviewer-a/review.md").read_text().startswith("VERDICT: BLOCKING"))
            self.assertTrue((base / "reviewer-b/review.md").read_text().startswith("VERDICT: PASS"))
            self.assertTrue((base / "reviewer-a-rereview/review.md").read_text().startswith("VERDICT: PASS"))
            self.assertTrue((base / "reviewer-b-rereview/review.md").read_text().startswith("VERDICT: PASS"))
            seals = json.loads((base / "seals.json").read_text())
            self.assertEqual(len(seals), 4)

    def test_both_reviewers_blocking_stops_phase(self):
        """BLOCKING from both, one revision, BLOCKING again stops the phase."""
        with Estate() as E:
            rid, p, r, st = run_plan(E, """## Phase 1: work (implementer)
REVIEW: cross
FAKE: touch out
FAKE: verdict BLOCKING
""")
            self.assertEqual(st["stopped"], "review_blocking", st)
            v = [x for x in jmod.Journal(rid).rows() if x["event"] == "review.verdict"]
            self.assertEqual([x["verdict"] for x in v], ["BLOCKING"] * 4)

    def test_one_reviewer_blocking_cleared_by_revision_completes(self):
        """One BLOCKING, cleared by the revision, lets the phase complete."""
        with Estate() as E:
            rid, p, r, st = run_plan(E, """## Phase 1: work (implementer)
REVIEW: cross
FAKE: touch out
FAKE: verdict-by-agent a=PASS b=BLOCKING,CONCERNS
""")
            self.assertEqual(st["closed"], "done", st)
            v = [(x["reviewer"], x["verdict"]) for x in jmod.Journal(rid).rows() if x["event"] == "review.verdict"]
            self.assertEqual(sorted(v[:2]), [("reviewer-a", "PASS"), ("reviewer-b", "BLOCKING")])
            self.assertEqual(sorted(v[2:]), [("reviewer-a", "PASS"), ("reviewer-b", "CONCERNS")])

    def test_review_spans_two_model_families(self):
        """The reviewers span two families and CLIs; one is always outside the author's family."""
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
            fams = {x["reviewer"]: roles_mod.family_of(x["model"], reg) for x in v}
            self.assertEqual(len(set(fams.values())), 2, fams)
            self.assertIn(roles_mod.family_of(worker["model"], reg), fams.values())  # one shares, one does not
            self.assertTrue(any(f != roles_mod.family_of(worker["model"], reg) for f in fams.values()))
            clis = {x["reviewer"]: roles_mod.cli_of(x["model"], reg) for x in v}
            self.assertEqual(clis, {"reviewer-a": "claude", "reviewer-b": "codex"})

    def test_fake_worker_logs_review_out_context(self):
        """Each reviewer dispatch carries its own absolute REVIEW_OUT, on its own CLI."""
        with Estate() as E:
            rid, p, r, st = run_plan(E, """## Phase 1: work (implementer)
REVIEW: cross
FAKE: touch out
FAKE: verdict PASS
""")
            self.assertEqual(st["closed"], "done")
            calls = {c["agent"]: c for c in E.fake_calls() if c.get("agent", "").startswith("pl-reviewer")}
            self.assertEqual(set(calls), {"pl-reviewer-a", "pl-reviewer-b"})
            for c in calls.values():
                self.assertTrue(c.get("REVIEW_OUT", "").startswith("/"), c)
            self.assertNotEqual(calls["pl-reviewer-a"]["REVIEW_OUT"], calls["pl-reviewer-b"]["REVIEW_OUT"])
            self.assertEqual((calls["pl-reviewer-a"]["cli"], calls["pl-reviewer-b"]["cli"]), ("claude", "codex"))
            b_argv = calls["pl-reviewer-b"]["argv"]
            self.assertEqual(b_argv[b_argv.index("--sandbox") + 1], "read-only")
            self.assertNotIn("--sandbox", calls["pl-reviewer-a"]["argv"])


if __name__ == "__main__":
    unittest.main()
