#!/usr/bin/env python3
"""End-to-end acceptance checks for the session-handoff adopt verb."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))

from harness import Estate  # noqa: E402
from pipeline import registry, status  # noqa: E402
from pipeline.journal import Journal  # noqa: E402


def check_chain() -> None:
    with Estate() as estate:
        run = "adopt-check-chain"
        registry.register("plan", run, journal=estate.estate / "runs" / run / "journal.jsonl", conversation="ses_A")
        assert registry.adopt(run, "ses_B")["action"] == "adopted"
        assert registry.adopt(str(estate.estate / "runs" / run), "ses_C")["action"] == "adopted"
        assert registry.lookup(run)["conversation"] == "ses_C"
        rows = [row for row in Journal(run).rows() if row["event"] == "adopted"]
        assert [(row["from"], row["by"]) for row in rows] == [("ses_A", "ses_B"), ("ses_B", "ses_C")]


def check_refusals() -> None:
    with Estate() as estate:
        missing = estate.cli("adopt", "absent")
        assert missing.returncode == 3 and "not found" in missing.stdout
        for run in ("same-one", "same-two"):
            registry.register("plan", run, journal=Path("/j"), conversation="ses_A")
        before = registry.all_rows()
        ambiguous = estate.cli("adopt", "same-")
        assert ambiguous.returncode == 4 and "same-one" in ambiguous.stdout and registry.all_rows() == before
        registry.register("plan", "mine", journal=estate.estate / "runs" / "mine" / "journal.jsonl",
                          conversation="ses_test_conv")
        no_op = estate.cli("adopt", "mine")
        assert no_op.returncode == 0 and no_op.stdout.strip() == "no-op: already yours"
        assert not Journal("mine").rows()


def check_execution_unchanged() -> None:
    with Estate() as estate:
        run = "adopt-check-state"
        rdir = estate.estate / "runs" / run
        (rdir / "phase-1").mkdir(parents=True)
        artifact = rdir / "phase-1" / "artifact"
        artifact.write_text("unchanged\n")
        registry.register("plan", run, journal=rdir / "journal.jsonl", conversation="ses_A")
        journal = Journal(run)
        journal.write("run.open", plan="p", cwd="c", conversation="ses_A", pid=1)
        journal.write("phase.start", phase="1", role="implementer", attempt=1)
        journal.write("phase.done", phase="1")
        journal.write("run.close", outcome="done")
        rows, state = journal.rows(), journal.state()
        bytes_and_mtime = (artifact.read_bytes(), artifact.stat().st_mtime_ns)
        registry.adopt(run, "ses_B")
        assert (artifact.read_bytes(), artifact.stat().st_mtime_ns) == bytes_and_mtime
        assert journal.rows()[:-1] == rows and journal.rows()[-1]["event"] == "adopted"
        assert journal.state() == state


def check_status_scope() -> None:
    with Estate() as estate:
        run = "adopt-check-scope"
        registry.register("plan", run, journal=estate.estate / "runs" / run / "journal.jsonl", conversation="ses_A")
        Journal(run).write("run.open", plan="p", cwd="c", conversation="ses_A", pid=999999)
        all_before = {report["run"] for report in status.all_reports()}
        registry.adopt(run, "ses_B")
        assert run in {report["run"] for report in status.all_reports("ses_B")}
        assert {report["run"] for report in status.all_reports()} == all_before


def main() -> int:
    checks = (("chain", check_chain), ("refusals", check_refusals),
              ("execution state", check_execution_unchanged), ("status scope", check_status_scope))
    try:
        for label, check in checks:
            check()
            print(f"ok: {label}")
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("adopt acceptance: 4 groups passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
