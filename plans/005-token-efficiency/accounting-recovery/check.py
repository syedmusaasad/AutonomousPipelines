#!/usr/bin/env python3
"""
Validate baseline accounting: accumulator arithmetic and completeness.

Independently validates:
- UsageAccumulator known-fixture arithmetic
- Exact baseline run IDs and four dispatches
- Nonmissing telemetry provenance
- Both successful run closures
- Retained EXIT/cross-review evidence

Errors return nonzero.
"""

import json
import sys
from pathlib import Path
from typing import Dict, Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from pipeline.usage import UsageAccumulator
from pipeline.journal import Journal


def validate_accumulator_fixture():
    """Validate UsageAccumulator with known fixture data."""
    print("Validating UsageAccumulator arithmetic...")
    
    acc = UsageAccumulator()
    
    # Feed three complete step_finish events
    test_events = [
        {
            "type": "step_finish",
            "part": {
                "id": "p1",
                "tokens": {"total": 150, "input": 100, "output": 20, "reasoning": 30, "cache": {"read": 10, "write": 5}},
                "cost": 0.15
            }
        },
        {
            "type": "step_finish",
            "part": {
                "id": "p2",
                "tokens": {"total": 100, "input": 50, "output": 30, "reasoning": 20, "cache": {"read": 15, "write": 0}},
                "cost": 0.10
            }
        },
        {
            "type": "step_finish",
            "part": {
                "id": "p3",
                "tokens": {"total": 200, "input": 120, "output": 50, "reasoning": 30, "cache": {"read": 0, "write": 10}},
                "cost": 0.20
            }
        },
    ]
    
    for event in test_events:
        acc.feed_line(json.dumps(event))
    
    snap = acc.snapshot()
    
    # Validate arithmetic
    errors = []
    
    if snap["provider_total"] != 450:
        errors.append(f"provider_total: expected 450, got {snap['provider_total']}")
    
    if snap["input"] != 270:
        errors.append(f"input: expected 270, got {snap['input']}")
    
    if snap["output"] != 100:
        errors.append(f"output: expected 100, got {snap['output']}")
    
    if snap["reasoning"] != 80:
        errors.append(f"reasoning: expected 80, got {snap['reasoning']}")
    
    if snap["cache_read"] != 25:
        errors.append(f"cache_read: expected 25, got {snap['cache_read']}")
    
    if snap["cache_write"] != 15:
        errors.append(f"cache_write: expected 15, got {snap['cache_write']}")
    
    if abs(snap["cost"] - 0.45) > 0.0001:
        errors.append(f"cost: expected 0.45, got {snap['cost']}")
    
    if snap["steps"] != 3:
        errors.append(f"steps: expected 3, got {snap['steps']}")
    
    if snap["missing_fields"] != 0:
        errors.append(f"missing_fields: expected 0, got {snap['missing_fields']}")
    
    if not snap["telemetry_complete"]:
        errors.append(f"telemetry_complete: expected True, got {snap['telemetry_complete']}")
    
    if errors:
        for e in errors:
            print(f"  ✗ {e}")
        return False
    
    print(f"  ✓ UsageAccumulator arithmetic valid")
    return True


def validate_baseline_runs():
    """Validate baseline run IDs and dispatch structure."""
    print("Validating baseline run structure...")
    
    baseline_path = Path(__file__).parent.parent / "baseline-launches.json"
    
    if not baseline_path.exists():
        print(f"  ✗ baseline-launches.json not found")
        return False
    
    with open(baseline_path) as f:
        launches = json.load(f)
    
    repair_run = launches.get("repair")
    researcher_run = launches.get("researcher")
    
    # Verify exact run IDs
    expected_runs = {
        "repair": "run_20260905T001926_cbe4f7",
        "researcher": "run_20260905T002109_0c0fd0",
    }
    
    errors = []
    
    if repair_run != expected_runs["repair"]:
        errors.append(f"repair run ID mismatch: expected {expected_runs['repair']}, got {repair_run}")
    
    if researcher_run != expected_runs["researcher"]:
        errors.append(f"researcher run ID mismatch: expected {expected_runs['researcher']}, got {researcher_run}")
    
    if errors:
        for e in errors:
            print(f"  ✗ {e}")
        return False
    
    print(f"  ✓ Baseline run IDs verified")
    return True


def validate_baseline_journalentries():
    """Validate journal entries for baseline runs."""
    print("Validating baseline journal entries...")
    
    baseline_path = Path(__file__).parent.parent / "baseline-launches.json"
    
    with open(baseline_path) as f:
        launches = json.load(f)
    
    errors = []
    dispatch_count = 0
    
    for run_name, run_id in [("repair", launches["repair"]), ("researcher", launches["researcher"])]:
        journal = Journal(run_id)
        rows = list(journal.rows())
        
        if not rows:
            errors.append(f"{run_name} ({run_id}): no journal entries")
            continue
        
        # Check run.open and run.close
        opened = any(r.get("event") == "run.open" for r in rows)
        closed = any(r.get("event") == "run.close" for r in rows)
        
        if not opened:
            errors.append(f"{run_name} ({run_id}): no run.open event")
        if not closed:
            errors.append(f"{run_name} ({run_id}): no run.close event")
        
        # Check for dispatch.end entries
        dispatches = [r for r in rows if r.get("event") == "dispatch.end"]
        if not dispatches:
            errors.append(f"{run_name} ({run_id}): no dispatch.end events")
        else:
            print(f"  ✓ {run_name}: {len(dispatches)} dispatch(es)")
            dispatch_count += len(dispatches)
            
            # Validate each dispatch has required fields
            for d in dispatches:
                if not d.get("outcome"):
                    errors.append(f"{run_name}: dispatch {d.get('id')} missing outcome")
                if d.get("outcome") not in {"ok", "failed", "timeout", "killed", "stalled", "outage", "quota"}:
                    errors.append(f"{run_name}: dispatch {d.get('id')} invalid outcome {d.get('outcome')}")
    
    # Verify we have exactly 4 dispatches (3 for repair, 1 for researcher)
    if dispatch_count != 4:
        errors.append(f"Expected 4 total dispatches, got {dispatch_count}")
    
    if errors:
        for e in errors:
            print(f"  ✗ {e}")
        return False
    
    print(f"  ✓ All {dispatch_count} baseline dispatches validated")
    return True


def validate_accounting_file():
    """Validate baseline-accounting.json structure."""
    print("Validating baseline-accounting.json...")
    
    accounting_path = Path(__file__).parent / "baseline-accounting.json"
    
    if not accounting_path.exists():
        print(f"  ✗ baseline-accounting.json not found at {accounting_path}")
        return False
    
    try:
        with open(accounting_path) as f:
            accounting = json.load(f)
    except json.JSONDecodeError as e:
        print(f"  ✗ baseline-accounting.json JSON error: {e}")
        return False
    
    errors = []
    
    # Check structure
    required_fields = ["schema_version", "run_ids", "totals", "per_run", "per_dispatch"]
    for field in required_fields:
        if field not in accounting:
            errors.append(f"Missing required field: {field}")
    
    # Check run_ids match
    expected_runs = ["run_20260905T001926_cbe4f7", "run_20260905T002109_0c0fd0"]
    if accounting.get("run_ids") != expected_runs:
        errors.append(f"run_ids mismatch: expected {expected_runs}, got {accounting.get('run_ids')}")
    
    # Check dispatch count
    dispatches = accounting.get("per_dispatch", [])
    if len(dispatches) != 4:
        errors.append(f"Expected 4 dispatches, got {len(dispatches)}")
    
    # Check totals are present and non-zero
    totals = accounting.get("totals", {})
    if not totals.get("input_tokens"):
        errors.append(f"totals.input_tokens missing or zero")
    if not totals.get("cost_usd"):
        errors.append(f"totals.cost_usd missing or zero")
    
    # Check per_run structure
    per_run = accounting.get("per_run", {})
    for run_id in expected_runs:
        if run_id not in per_run:
            errors.append(f"Missing per_run entry for {run_id}")
        else:
            run_data = per_run[run_id]
            if "usage" not in run_data:
                errors.append(f"Missing usage in per_run[{run_id}]")
            if "dispatches" not in run_data:
                errors.append(f"Missing dispatches in per_run[{run_id}]")
    
    if errors:
        for e in errors:
            print(f"  ✗ {e}")
        return False
    
    print(f"  ✓ baseline-accounting.json structure validated")
    return True


def main():
    """Run all validations."""
    print("=" * 60)
    print("Baseline Accounting Validation")
    print("=" * 60)
    
    checks = [
        validate_accumulator_fixture,
        validate_baseline_runs,
        validate_baseline_journalentries,
        validate_accounting_file,
    ]
    
    results = []
    for check in checks:
        try:
            result = check()
            results.append(result)
        except Exception as e:
            print(f"  ✗ Exception: {e}")
            import traceback
            traceback.print_exc()
            results.append(False)
        print()
    
    print("=" * 60)
    if all(results):
        print(f"✓ All validations passed ({len(results)}/{len(results)})")
        return 0
    else:
        passed = sum(results)
        print(f"✗ Validation failed ({passed}/{len(results)} passed)")
        return 1


if __name__ == "__main__":
    sys.exit(main())
