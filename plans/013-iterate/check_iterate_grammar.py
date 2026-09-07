#!/usr/bin/env python3
"""Acceptance checks for plan-013 phase 1 (grammar). Exit 0 = pass."""
import sys

sys.path.insert(0, "/root/pipeline")
from pipeline import plan as p


def main() -> int:
    # accepts on + fields
    pl = p.parse_text("## Phase 1: x (implementer)\nITERATE: on\nCEILING: 12\nEXIT: true\n")
    ph = pl.by_number(1)
    assert ph.iterate is True and ph.ceiling == 12 and ph.iterate_progress is None
    # rejects non-on
    try:
        p.parse_text("## Phase 1: x (implementer)\nITERATE: yes\nCEILING: 12\nEXIT: true\n")
        print("FAIL: ITERATE: yes accepted"); return 1
    except p.PlanError:
        pass
    # rejects ITERATE + LANES
    try:
        p.parse_text("## Phase 1: x (implementer)\nITERATE: on\nCEILING: 12\nLANES: items\nEXIT: true\n")
        print("FAIL: ITERATE+LANES accepted"); return 1
    except p.PlanError as e:
        assert "LANES" in str(e)
    # rejects missing CEILING
    try:
        p.parse_text("## Phase 1: x (implementer)\nITERATE: on\nEXIT: true\n")
        print("FAIL: missing CEILING accepted"); return 1
    except p.PlanError as e:
        assert "CEILING" in str(e)
    # rejects missing EXIT
    try:
        p.parse_text("## Phase 1: x (implementer)\nITERATE: on\nCEILING: 2\n")
        print("FAIL: missing EXIT accepted"); return 1
    except p.PlanError as e:
        assert "EXIT" in str(e)
    # COST-CEILING reserved
    try:
        p.parse_text("## Phase 1: x (implementer)\nCOST-CEILING: 5\nEXIT: true\n")
        print("FAIL: COST-CEILING accepted"); return 1
    except p.PlanError as e:
        assert "reserved" in str(e).lower()
    # PROGRESS captured
    pl2 = p.parse_text("## Phase 1: x (implementer)\nITERATE: on\nCEILING: 3\nPROGRESS: true\nEXIT: true\n")
    assert pl2.by_number(1).iterate_progress == "true"
    print("ok    iterate grammar checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
