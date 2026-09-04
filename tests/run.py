#!/usr/bin/env python3
"""Suite runner. Runs every test, appends a row to <estate>/state/suite.jsonl (the bench
reads it), and enforces the ratchet: the ledger floor only rises."""

import json
import os
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import test_suite  # noqa: E402


def main(argv):
    only = [a for a in argv[1:] if not a.startswith("-")]
    tests = [t for t in test_suite.TESTS if not only or any(o in t.__name__ for o in only)]
    passed, failed = 0, []
    t0 = time.time()
    for t in tests:
        s = time.time()
        try:
            t()
            passed += 1
            print(f"ok    {t.__name__} ({time.time() - s:.1f}s)", flush=True)
        except Exception:
            failed.append(t.__name__)
            print(f"FAIL  {t.__name__} ({time.time() - s:.1f}s)", flush=True)
            traceback.print_exc()
    wall = round(time.time() - t0, 1)
    ledger_p = HERE / "ratchet.json"
    ledger = json.loads(ledger_p.read_text())
    print(f"\n{passed}/{len(tests)} passed, {len(failed)} failed, {wall}s; ratchet floor {ledger['min_tests']}")
    if not only:
        from pipeline import paths
        from pipeline.util import append_jsonl, now_iso
        try:
            append_jsonl(paths.state_dir() / "suite.jsonl", {"ts": now_iso(), "total": len(tests), "passed": passed,
                                                                "failed": len(failed), "wall_s": wall, "floor": ledger["min_tests"],
                                                                "failed_names": failed})
        except Exception as e:
            print(f"(could not log suite result: {e})")
        if passed == len(tests) and len(tests) > ledger["min_tests"]:
            ledger["min_tests"] = len(tests)
            ledger["required"] = sorted({*ledger["required"], *(t.__name__ for t in tests)})
            ledger_p.write_text(json.dumps(ledger, indent=1) + "\n")
            print(f"ratchet raised to {len(tests)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
