#!/usr/bin/env python3
"""Checks for phase 6 of plan 004: EFFORT grammar.

Default (no args): EFFORT: max must raise PlanError.
--positive: EFFORT: low must parse and be readable.

Exit 0 on success, 1 on failure, with a one-line reason.
"""
import sys

sys.path.insert(0, "/root/pipeline")
from pipeline import plan  # noqa: E402


def main() -> int:
    positive = "--positive" in sys.argv
    text = (
        "## Phase 1: a (implementer)\nEFFORT: %s\n"
        % ("low" if positive else "max")
    )
    try:
        p = plan.parse_text(text)
    except plan.PlanError:
        if positive:
            print("FAIL: EFFORT: low rejected")
            return 1
        print("ok: EFFORT: max rejected")
        return 0
    if positive:
        print("ok: EFFORT: low parsed as", p.by_number(1).effort)
        return 0 if p.by_number(1).effort == "low" else 1
    print("FAIL: EFFORT: max accepted")
    return 1


if __name__ == "__main__":
    sys.exit(main())
