#!/usr/bin/env python3
"""Verification driver: runs three required checks with timeout and logging."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
import hashlib

WORKDIR = Path("/root/pipeline")
VERIF_DIR = WORKDIR / "plans/005-token-efficiency/accounting-corrections/verification"
RESULTS_FILE = VERIF_DIR / "results.json"

# Commands to run: (name, command_list, timeout_s, log_file_name)
CHECKS = [
    ("test_review_provenance", ["python3", "-m", "unittest", "discover", "-s", "tests", "-p", "test_review_provenance.py"], 120, "test_review_provenance.log"),
    ("run_harness", ["python3", "tests/run.py"], 120, "run_harness.log"),
    ("check_agents", ["bin/pipeline", "check-agents"], 120, "check_agents.log"),
]


def compute_source_fingerprint():
    """Compute SHA256 of git diff to detect source changes."""
    try:
        result = subprocess.run(
            ["git", "diff", "--no-ext-diff"],
            cwd=str(WORKDIR),
            capture_output=True,
            text=True,
            timeout=10
        )
        content = result.stdout.encode('utf-8')
        return hashlib.sha256(content).hexdigest()
    except Exception as e:
        return f"error: {e}"


def load_prior_results():
    """Load prior results if they exist."""
    if RESULTS_FILE.exists():
        try:
            return json.loads(RESULTS_FILE.read_text())
        except Exception:
            pass
    return None


def run_check(name, cmd, timeout_s, log_file):
    """Run a single check command with timeout."""
    log_path = VERIF_DIR / log_file
    
    t0 = time.time()
    try:
        result = subprocess.run(
            cmd,
            cwd=str(WORKDIR),
            capture_output=True,
            text=True,
            timeout=timeout_s
        )
        elapsed = time.time() - t0
        
        # Write full output to log
        log_content = f"Command: {' '.join(cmd)}\n"
        log_content += f"Return code: {result.returncode}\n"
        log_content += f"Elapsed: {elapsed:.2f}s\n"
        log_content += f"\n--- STDOUT ---\n{result.stdout}\n"
        log_content += f"\n--- STDERR ---\n{result.stderr}\n"
        
        log_path.write_text(log_content)
        
        return {
            "name": name,
            "command": " ".join(cmd),
            "returncode": result.returncode,
            "elapsed_s": round(elapsed, 2),
            "log_path": str(log_path),
            "timeout_hit": False
        }
    except subprocess.TimeoutExpired as e:
        elapsed = time.time() - t0
        log_content = f"Command: {' '.join(cmd)}\n"
        log_content += f"TIMEOUT after {timeout_s}s\n"
        log_content += f"Elapsed: {elapsed:.2f}s\n"
        
        log_path.write_text(log_content)
        
        return {
            "name": name,
            "command": " ".join(cmd),
            "returncode": -1,
            "elapsed_s": round(elapsed, 2),
            "log_path": str(log_path),
            "timeout_hit": True,
            "timeout_s": timeout_s
        }
    except Exception as e:
        log_path.write_text(f"Error running command: {e}\n")
        return {
            "name": name,
            "command": " ".join(cmd),
            "returncode": -2,
            "elapsed_s": 0,
            "log_path": str(log_path),
            "error": str(e)
        }


def main():
    VERIF_DIR.mkdir(parents=True, exist_ok=True)
    
    # Compute source fingerprint
    source_fp = compute_source_fingerprint()
    
    # Load prior results if available
    prior = load_prior_results()
    
    checks = []
    total_start = time.time()
    
    # Run each check
    for name, cmd, timeout_s, log_file in CHECKS:
        result = run_check(name, cmd, timeout_s, log_file)
        checks.append(result)
        print(f"✓ {name}: rc={result['returncode']} elapsed={result['elapsed_s']}s")
    
    total_elapsed = time.time() - total_start
    
    # Determine if all passed
    all_passed = len(checks) == 3 and all(c.get("returncode") == 0 for c in checks)
    
    # Read five findings from prior review
    unresolved_findings = [
        "pipeline/dispatch.py:196 parse_transcript loads full transcript before splitting (should iterate line-by-line)",
        "pipeline/dispatch.py:240 usage payload lacks explicit provenance field",
        "plans/005-token-efficiency/accounting-recovery/extract.py:177 schema missing EXIT/check/verdict evidence",
        "plans/005-token-efficiency/accounting-recovery/baseline-accounting.json:7 excluded_overhead refs wrong run IDs",
        "plans/005-token-efficiency/accounting-recovery/check.py:146 validation missing EXIT/cross-review evidence checks"
    ]
    
    # Build results
    results = {
        "checks": checks,
        "all_passed": all_passed,
        "total_elapsed_s": round(total_elapsed, 2),
        "source_fingerprint": source_fp,
        "unresolved_findings": unresolved_findings
    }
    
    # Write results
    RESULTS_FILE.write_text(json.dumps(results, indent=2))
    print(f"\nResults saved to {RESULTS_FILE}")
    print(f"All passed: {all_passed}")
    print(f"Source fingerprint: {source_fp}")
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
