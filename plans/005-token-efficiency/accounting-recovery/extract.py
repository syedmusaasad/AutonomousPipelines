#!/usr/bin/env python3
"""
Extract usage accounting from baseline runs.

Reads only the two named baseline child journals/transcripts and regenerates 
baseline-accounting.json with independent usage fields, completeness indicators,
and cross-review evidence.
"""

import json
import sys
from pathlib import Path
from typing import Dict, Any, List

# Import required modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from pipeline.dispatch import parse_transcript
from pipeline.journal import Journal


def extract_baseline_runs() -> Dict[str, Any]:
    """
    Extract telemetry from the two baseline runs.
    
    Returns a dict with all four baseline dispatches, independent usage fields,
    and completeness indicators.
    """
    
    # Load baseline launches to get run IDs
    baseline_launches = Path(__file__).parent.parent / "baseline-launches.json"
    if not baseline_launches.exists():
        raise FileNotFoundError(f"baseline-launches.json not found at {baseline_launches}")
    
    with open(baseline_launches) as f:
        launches = json.load(f)
    
    repair_run = launches.get("repair")
    researcher_run = launches.get("researcher")
    
    if not repair_run or not researcher_run:
        raise ValueError("baseline-launches.json missing repair or researcher run ID")
    
    # Expected run IDs from baseline.json
    expected_runs = ["run_20260905T001926_cbe4f7", "run_20260905T002109_0c0fd0"]
    actual_runs = [repair_run, researcher_run]
    
    if actual_runs != expected_runs:
        raise ValueError(f"Run IDs mismatch. Expected {expected_runs}, got {actual_runs}")
    
    # Extract dispatch data from journals
    all_dispatches = []
    per_run = {}
    
    for run_id in [repair_run, researcher_run]:
        journal = Journal(run_id)
        rows = list(journal.rows())
        
        # Extract dispatch.end rows for this run, and transcripts
        run_dispatches = []
        run_wall_secs = 0.0
        run_cost = 0.0
        run_usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost_usd": 0.0,
            "steps_completed": 0,
            "missing_fields": 0,
            "telemetry_complete": True,  # Only true if ALL dispatches have complete telemetry
        }
        
        # Get run timing from journal
        run_opened_at = None
        run_closed_at = None
        for row in rows:
            if row.get("event") == "run.open":
                run_opened_at = row.get("t")
            elif row.get("event") == "run.close":
                run_closed_at = row.get("t")
        
        run_elapsed_secs = 0.0
        if run_opened_at is not None and run_closed_at is not None:
            run_elapsed_secs = run_closed_at - run_opened_at
        
        # Extract dispatch data
        dispatch_transcripts = {}  # Map dispatch_id to transcript path
        for row in rows:
            if row.get("event") == "dispatch.start":
                dispatch_transcripts[row.get("id")] = row.get("transcript")
        
        for row in rows:
            if row.get("event") == "dispatch.end":
                dispatch_id = row.get("id")
                outcome = row.get("outcome")
                wall_s = row.get("wall_s", 0)
                tokens = row.get("tokens", {})
                cost = row.get("cost", 0)
                usage_from_journal = row.get("usage")
                
                # If usage is not in journal, try to extract from transcript
                usage = None
                if usage_from_journal:
                    usage = usage_from_journal
                else:
                    # Try to parse transcript if available
                    transcript_path = dispatch_transcripts.get(dispatch_id)
                    if transcript_path and Path(transcript_path).exists():
                        try:
                            parsed = parse_transcript(Path(transcript_path))
                            usage = parsed.get("usage")
                        except Exception as e:
                            print(f"  Warning: could not parse transcript for {dispatch_id}: {e}", file=sys.stderr)
                
                dispatch_data = {
                    "run_id": run_id,
                    "dispatch_id": dispatch_id,
                    "role": row.get("role"),
                    "model": row.get("model"),
                    "outcome": outcome,
                    "wall_secs": wall_s,
                    "tokens": tokens,
                    "cost": cost,
                }
                
                # Add usage if present (either from journal or parsed from transcript)
                if usage:
                    dispatch_data["usage"] = usage
                
                run_dispatches.append(dispatch_data)
                all_dispatches.append(dispatch_data)
                
                if wall_s:
                    run_wall_secs += wall_s
                if cost:
                    run_cost += cost
                
                # Accumulate usage if available
                if usage:
                    run_usage["input_tokens"] += usage.get("input_tokens", 0)
                    run_usage["output_tokens"] += usage.get("output_tokens", 0)
                    run_usage["reasoning_tokens"] += usage.get("reasoning_tokens", 0)
                    run_usage["cache_read_tokens"] += usage.get("cache_read_tokens", 0)
                    run_usage["cache_write_tokens"] += usage.get("cache_write_tokens", 0)
                    run_usage["cost_usd"] += usage.get("cost_usd", 0)
                    run_usage["steps_completed"] += usage.get("steps_completed", 0)
                    
                    # telemetry_complete is only true if all have complete telemetry
                    if not usage.get("telemetry_complete", False):
                        run_usage["telemetry_complete"] = False
                    
                    # Track missing fields (max across dispatches)
                    run_usage["missing_fields"] = max(
                        run_usage["missing_fields"],
                        usage.get("missing_fields", 0)
                    )
        
        per_run[run_id] = {
            "dispatches": run_dispatches,
            "wall_secs": run_wall_secs,
            "elapsed_secs": run_elapsed_secs,
            "cost_usd": run_cost,
            "usage": run_usage,
        }
    
    # Calculate totals
    total_input = sum(r["usage"]["input_tokens"] for r in per_run.values())
    total_output = sum(r["usage"]["output_tokens"] for r in per_run.values())
    total_reasoning = sum(r["usage"]["reasoning_tokens"] for r in per_run.values())
    total_cache_read = sum(r["usage"]["cache_read_tokens"] for r in per_run.values())
    total_cache_write = sum(r["usage"]["cache_write_tokens"] for r in per_run.values())
    total_cost = sum(r["usage"]["cost_usd"] for r in per_run.values())
    total_steps = sum(r["usage"]["steps_completed"] for r in per_run.values())
    
    return {
        "schema_version": "2.0",
        "run_ids": [repair_run, researcher_run],
        "excluded_overhead": {
            "description": "Setup/recovery/coordinator costs are not included in workload totals",
            "repair_run": repair_run,
            "researcher_run": researcher_run,
        },
        "totals": {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "reasoning_tokens": total_reasoning,
            "cache_read_tokens": total_cache_read,
            "cache_write_tokens": total_cache_write,
            "cost_usd": total_cost,
            "steps_completed": total_steps,
            "worker_wall_secs": sum(r["wall_secs"] for r in per_run.values()),
            "elapsed_secs": sum(r["elapsed_secs"] for r in per_run.values()),
        },
        "per_run": per_run,
        "per_dispatch": all_dispatches,
    }


def main():
    """Extract baseline accounting and write to file."""
    try:
        accounting = extract_baseline_runs()
        
        # Write output
        output_path = Path(__file__).parent / "baseline-accounting.json"
        with open(output_path, "w") as f:
            json.dump(accounting, f, indent=2)
        
        print(f"✓ Extracted baseline accounting to {output_path}")
        print(f"  Run IDs: {accounting['run_ids']}")
        print(f"  Total dispatches: {len(accounting['per_dispatch'])}")
        print(f"  Total input tokens: {accounting['totals']['input_tokens']}")
        print(f"  Total output tokens: {accounting['totals']['output_tokens']}")
        print(f"  Total cost: ${accounting['totals']['cost_usd']:.6f}")
        
        return 0
    except Exception as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
