#!/usr/bin/env python3
"""
Baseline measurement runner with checkpointing and aggregation.
Launches repair and researcher child runs through /root/pipeline/bin/run,
waits for completion, aggregates telemetry, and verifies immutable checks.
"""

import json
import subprocess
import sys
import time
import os
from pathlib import Path
from typing import Optional, Dict, Any, List
import hashlib
import re

PIPELINE_ROOT = Path("/root/pipeline")
PLANS_ROOT = PIPELINE_ROOT / "plans" / "005-token-efficiency"
CONV_ID = "ses_f957d9dc1ffepFpvyzZoBE3AmE"
TIMEOUT_SECS = 1400
POLL_INTERVAL_SECS = 10

# Fixture hashes from the manifest (immutable)
IMMUTABLE_CHECKS = {
    "repair": {
        "test_calc.py": "c0b779f34bcb4b56297f7db4426e1566ea3037488dcc8deeba39820d0c8212a9",
        "calc.py": "de1ddede504506088f340a08951f6b291623a3bb0761cb8c2241c7752ade2e24",
    },
    "researcher": {
        "answer_key.py": "a8705017707a9c5c638659898a6fed9f46aba5b0fb8f459f3635a387945b7086",
        "journal.jsonl": "9a0441c953f75a3f5f6da7487bb2e22f082b627732b79eba35b1a824cdae48b8",
    }
}

def sha256_file(path: Path) -> Optional[str]:
    """Compute SHA256 of a file."""
    if not path.exists():
        return None
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        h.update(f.read())
    return h.hexdigest()

def verify_immutable_fixtures() -> bool:
    """Verify that immutable test and checker files have not changed."""
    print("Verifying immutable fixtures...")
    
    repair_test = Path("/root/.system/projects/token-efficiency-reference/repair/fixtures/repair/tests/test_calc.py")
    repair_calc = Path("/root/.system/projects/token-efficiency-reference/repair/fixtures/repair/calc.py")
    researcher_checker = Path("/root/.system/projects/token-efficiency-reference/researcher/fixtures/answer_key.py")
    researcher_journal = Path("/root/.system/projects/token-efficiency-reference/researcher/fixtures/journal.jsonl")
    
    checks = {
        "repair/tests/test_calc.py": (repair_test, IMMUTABLE_CHECKS["repair"]["test_calc.py"]),
        "repair/calc.py": (repair_calc, IMMUTABLE_CHECKS["repair"]["calc.py"]),
        "researcher/answer_key.py": (researcher_checker, IMMUTABLE_CHECKS["researcher"]["answer_key.py"]),
        "researcher/journal.jsonl": (researcher_journal, IMMUTABLE_CHECKS["researcher"]["journal.jsonl"]),
    }
    
    all_ok = True
    for desc, (path, expected_hash) in checks.items():
        actual = sha256_file(path)
        if actual == expected_hash:
            print(f"  ✓ {desc}")
        else:
            print(f"  ✗ {desc} CHANGED!")
            print(f"    Expected: {expected_hash}")
            print(f"    Actual:   {actual}")
            all_ok = False
    
    return all_ok

def launch_child_run(workspace: str, plan_path: str) -> Optional[str]:
    """
    Launch a child run via /root/pipeline/bin/run.
    Returns the run_id if successful, None otherwise.
    """
    print(f"\nLaunching {workspace} run...")
    cmd = [
        str(PIPELINE_ROOT / "bin" / "run"),
        "--conv", CONV_ID,
        plan_path
    ]
    print(f"  Command: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        print(f"  Exit code: {result.returncode}")
        
        # Extract run_id from output (format: "Launched run: run_<id>")
        match = re.search(r"run_\d+", result.stdout + result.stderr)
        if match:
            run_id = match.group(0)
            print(f"  Run ID: {run_id}")
            return run_id
        else:
            print(f"  Failed to extract run_id from output")
            print(f"  stdout: {result.stdout[:500]}")
            print(f"  stderr: {result.stderr[:500]}")
            return None
    except subprocess.TimeoutExpired:
        print(f"  Launch timed out")
        return None
    except Exception as e:
        print(f"  Launch failed: {e}")
        return None

def get_journal_state(run_id: str) -> Dict[str, Any]:
    """Get the state of a run's journal."""
    cmd = ["python3", "-c", f"""
import json
from pathlib import Path
from pipeline.journal import Journal
j = Journal('{run_id}')
state = j.state()
print(json.dumps(state))
"""]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PIPELINE_ROOT), timeout=10)
        if result.returncode == 0:
            return json.loads(result.stdout)
        else:
            print(f"  Error getting state: {result.stderr}")
            return {}
    except Exception as e:
        print(f"  Error: {e}")
        return {}

def wait_for_completion(run_ids: List[str]) -> Dict[str, bool]:
    """
    Poll for run completion with bounded wait.
    Returns dict of {run_id: completed}.
    """
    start_time = time.time()
    completed = {run_id: False for run_id in run_ids}
    
    while time.time() - start_time < TIMEOUT_SECS:
        all_done = True
        for run_id in run_ids:
            if not completed[run_id]:
                state = get_journal_state(run_id)
                closed = state.get("closed") == "done"
                stopped = state.get("stopped", False)
                
                if closed and not stopped:
                    completed[run_id] = True
                    print(f"  {run_id}: completed (closed={closed}, stopped={stopped})")
                elif stopped:
                    print(f"  {run_id}: STOPPED early (error condition)")
                    completed[run_id] = False  # Mark as blocker
                    all_done = False
                else:
                    all_done = False
        
        if all_done:
            return completed
        
        elapsed = time.time() - start_time
        remaining = TIMEOUT_SECS - elapsed
        print(f"  Polling... ({int(elapsed)}s / {TIMEOUT_SECS}s)")
        time.sleep(POLL_INTERVAL_SECS)
    
    print(f"Timeout reached ({TIMEOUT_SECS}s)")
    return completed

def aggregate_telemetry(run_ids: List[str]) -> Dict[str, Any]:
    """
    Aggregate telemetry from child runs.
    Returns dict with token counts, costs, wall times, etc.
    """
    print("\nAggregating telemetry...")
    
    aggregate = {
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cache_read_tokens": 0,
        "total_cache_write_tokens": 0,
        "total_cost_usd": 0,
        "total_wall_secs": 0,
        "per_run": {},
        "unknown_usage": False
    }
    
    for run_id in run_ids:
        # Extract usage from journal
        cmd = ["python3", "-c", f"""
import json
from pathlib import Path
from pipeline.journal import Journal
j = Journal('{run_id}')
rows = list(j.rows())
total_input = total_output = total_cache_read = total_cache_write = 0
wall_secs = 0
cost_usd = 0.0
for row in rows:
    usage = row.get('usage', {{}})
    total_input += usage.get('input_tokens', 0)
    total_output += usage.get('output_tokens', 0)
    total_cache_read += usage.get('cache_read_tokens', 0)
    total_cache_write += usage.get('cache_write_tokens', 0)
    cost_usd += usage.get('cost_usd', 0.0)
    wall_secs += usage.get('wall_secs', 0)
print(json.dumps({{
    'input': total_input,
    'output': total_output,
    'cache_read': total_cache_read,
    'cache_write': total_cache_write,
    'cost_usd': cost_usd,
    'wall_secs': wall_secs
}}))
"""]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PIPELINE_ROOT), timeout=30)
            if result.returncode == 0:
                metrics = json.loads(result.stdout)
                aggregate["per_run"][run_id] = metrics
                aggregate["total_input_tokens"] += metrics.get("input", 0)
                aggregate["total_output_tokens"] += metrics.get("output", 0)
                aggregate["total_cache_read_tokens"] += metrics.get("cache_read", 0)
                aggregate["total_cache_write_tokens"] += metrics.get("cache_write", 0)
                aggregate["total_cost_usd"] += metrics.get("cost_usd", 0)
                aggregate["total_wall_secs"] += metrics.get("wall_secs", 0)
                print(f"  {run_id}: {metrics}")
            else:
                print(f"  {run_id}: Error extracting metrics: {result.stderr[:200]}")
                aggregate["unknown_usage"] = True
        except Exception as e:
            print(f"  {run_id}: Exception: {e}")
            aggregate["unknown_usage"] = True
    
    return aggregate

def main():
    print("=== Baseline Measurement Runner ===\n")
    
    # Load reference manifest
    manifest_path = PLANS_ROOT / "reference-manifest.json"
    with open(manifest_path) as f:
        manifest = json.load(f)
    
    # Verify immutable fixtures before any launches
    if not verify_immutable_fixtures():
        print("\n✗ Immutable fixture verification failed")
        sys.exit(1)
    
    print("\n✓ Immutable fixtures verified\n")
    
    # Get prepared plan paths
    repair_plan = manifest["workspaces"]["repair"]["plan_md"]
    researcher_plan = manifest["workspaces"]["researcher"]["plan_md"]
    
    print(f"Repair plan: {repair_plan}")
    print(f"Researcher plan: {researcher_plan}\n")
    
    # Load or initialize baseline launches checkpoint
    launches_file = PLANS_ROOT / "baseline-launches.json"
    if launches_file.exists():
        with open(launches_file) as f:
            launches = json.load(f)
        print(f"Loaded existing launches: {launches}")
    else:
        launches = {"repair": None, "researcher": None}
    
    # Launch repair if not already launched
    if launches["repair"] is None:
        repair_run_id = launch_child_run("repair", repair_plan)
        if repair_run_id:
            launches["repair"] = repair_run_id
            with open(launches_file, 'w') as f:
                json.dump(launches, f, indent=2)
            print(f"Saved launches: {launches}")
        else:
            print("\n✗ Failed to launch repair run")
            sys.exit(1)
    else:
        print(f"Using previously launched repair run: {launches['repair']}")
    
    # Wait for repair completion
    print(f"\nWaiting for repair run to complete...")
    repair_completed = wait_for_completion([launches["repair"]])
    
    if not repair_completed.get(launches["repair"]):
        print(f"\n✗ Repair run did not complete successfully")
        sys.exit(1)
    
    # Verify repair immutable checks
    print("\nVerifying repair fixtures after completion...")
    if not verify_immutable_fixtures():
        print("✗ Repair modified immutable fixtures")
        sys.exit(1)
    
    print("✓ Repair immutable fixtures unchanged")
    
    # Now launch researcher
    if launches["researcher"] is None:
        researcher_run_id = launch_child_run("researcher", researcher_plan)
        if researcher_run_id:
            launches["researcher"] = researcher_run_id
            with open(launches_file, 'w') as f:
                json.dump(launches, f, indent=2)
            print(f"Saved launches: {launches}")
        else:
            print("\n✗ Failed to launch researcher run")
            sys.exit(1)
    else:
        print(f"Using previously launched researcher run: {launches['researcher']}")
    
    # Wait for researcher completion
    print(f"\nWaiting for researcher run to complete...")
    researcher_completed = wait_for_completion([launches["researcher"]])
    
    if not researcher_completed.get(launches["researcher"]):
        print(f"\n✗ Researcher run did not complete successfully")
        sys.exit(1)
    
    # Verify researcher immutable checks
    print("\nVerifying researcher fixtures after completion...")
    if not verify_immutable_fixtures():
        print("✗ Researcher modified immutable fixtures")
        sys.exit(1)
    
    print("✓ Researcher immutable fixtures unchanged")
    
    # Aggregate telemetry
    all_run_ids = [launches["repair"], launches["researcher"]]
    telemetry = aggregate_telemetry(all_run_ids)
    
    # Build baseline report
    baseline_report = {
        "measurement_status": "complete",
        "run_ids": all_run_ids,
        "original_conversation": CONV_ID,
        "settings": manifest["frozen_settings"],
        "fixture_hashes": {
            "repair": manifest["workspaces"]["repair"]["fixtures_hash"],
            "researcher": manifest["workspaces"]["researcher"]["fixtures_hash"]
        },
        "telemetry": telemetry,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    }
    
    # Write baseline.json
    baseline_path = PLANS_ROOT / "baseline.json"
    with open(baseline_path, 'w') as f:
        json.dump(baseline_report, f, indent=2)
    
    print(f"\n✓ Baseline report written to {baseline_path}")
    
    # Write BASELINE.md
    md_path = PLANS_ROOT / "BASELINE.md"
    md_content = f"""# Token Efficiency Baseline Measurements

## Summary
Baseline measurements completed for token efficiency reference runs.

- **Status**: Complete
- **Conversation**: {CONV_ID}
- **Repair Run**: {launches["repair"]}
- **Researcher Run**: {launches["researcher"]}
- **Timestamp**: {baseline_report["timestamp"]}

## Telemetry

### Aggregate Metrics
- Total Input Tokens: {telemetry.get("total_input_tokens", "unknown")}
- Total Output Tokens: {telemetry.get("total_output_tokens", "unknown")}
- Total Cache Read Tokens: {telemetry.get("total_cache_read_tokens", "unknown")}
- Total Cache Write Tokens: {telemetry.get("total_cache_write_tokens", "unknown")}
- Total Cost: ${telemetry.get("total_cost_usd", 0):.4f}
- Total Wall Time: {telemetry.get("total_wall_secs", "unknown")}s

### Unknown Usage
- Cache token counts and costs treated as unknown when unavailable.

### Per-Run Metrics
"""
    for run_id, metrics in telemetry.get("per_run", {}).items():
        md_content += f"\n#### {run_id}\n"
        md_content += f"- Input Tokens: {metrics.get('input', 'unknown')}\n"
        md_content += f"- Output Tokens: {metrics.get('output', 'unknown')}\n"
        md_content += f"- Cache Read Tokens: {metrics.get('cache_read', 'unknown')}\n"
        md_content += f"- Cache Write Tokens: {metrics.get('cache_write', 'unknown')}\n"
        md_content += f"- Cost: ${metrics.get('cost_usd', 0):.4f}\n"
        md_content += f"- Wall Time: {metrics.get('wall_secs', 'unknown')}s\n"
    
    md_content += f"""
## Settings and Fixtures

### Frozen Role/Model Settings
```json
{json.dumps(manifest["frozen_settings"], indent=2)}
```

### Fixture Hashes
```json
{json.dumps(baseline_report["fixture_hashes"], indent=2)}
```

## Limitations

1. **Answer-Key Exposure**: The researcher answer checker is readable inside the prepared fixture, so this is a workflow baseline, not proof of blind research quality.
2. **Cache Token Attribution**: Cache token counts are provider-reported; legacy journal totals are not included as context length or savings.
3. **Unknown Usage**: If any child run lacks telemetry, those metrics are marked as unknown rather than estimated.

## Regeneration

To rerun these baseline measurements with the same conversation:

```bash
python3 {PLANS_ROOT}/run_baseline.py
```

Note: If measurements have already been launched for this exact conversation and plan combination, they will be reused instead of rerun.
"""
    
    with open(md_path, 'w') as f:
        f.write(md_content)
    
    print(f"✓ Baseline markdown written to {md_path}")
    
    print("\n=== Baseline Measurement Complete ===")
    print(f"Run IDs: {all_run_ids}")
    print(f"Total input tokens: {telemetry.get('total_input_tokens', 'unknown')}")
    print(f"Total output tokens: {telemetry.get('total_output_tokens', 'unknown')}")
    print(f"Total cost: ${telemetry.get('total_cost_usd', 0):.4f}")

if __name__ == "__main__":
    main()
