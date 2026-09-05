#!/usr/bin/env python3
"""Deterministic baseline preparation for token-efficiency reference workload.

Prepares two isolated fixture workspaces below /root/.system/projects/token-efficiency-reference/
with exact plan.md files and frozen fixtures, ready for measured child dispatches.

Usage:
    python3 prepare_reference.py                 # Prepare real workspaces (idempotent)
    python3 prepare_reference.py --self-test     # Test in temporary dirs, verify idempotency
"""

import hashlib
import json
import sys
import tempfile
from pathlib import Path
import shutil
import subprocess


SCRIPT_DIR = Path(__file__).resolve().parent
REFERENCE_ROOT = Path("/root/.system/projects/token-efficiency-reference")
REPAIR_FIXTURE_SOURCE = SCRIPT_DIR / "fixtures" / "repair"
ORIGINAL_CONVERSATION = "ses_f957d9dc1ffepFpvyzZoBE3AmE"


def compute_file_hash(path: Path) -> str:
    """SHA256 hash of a file's contents."""
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        sha.update(f.read())
    return sha.hexdigest()


def compute_tree_hash(base: Path) -> dict:
    """SHA256 hashes of all files under base, relative paths as keys."""
    hashes = {}
    if not base.exists():
        return hashes
    for f in sorted(base.rglob("*")):
        if f.is_file():
            rel = f.relative_to(base)
            hashes[str(rel)] = compute_file_hash(f)
    return hashes


def copy_tree(src: Path, dst: Path) -> None:
    """Recursively copy src to dst, creating parent directories."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def generate_synthetic_journal_rows(num_rows: int = 200) -> list:
    """Generate deterministic synthetic journal rows for researcher fixture.
    
    Each row simulates a plan-dispatch record with embedded facts.
    Three unambiguous required facts are buried at rows 67, 134, and 189.
    """
    rows = []
    facts = [
        "fact_1: The system was deployed on 2026-09-01 with model claude-sonnet-5.",
        "fact_2: Phase 1 completed with exit code 0 after 187 seconds.",
        "fact_3: The review verdict was PASS from both reviewer-a and reviewer-b.",
    ]
    
    for i in range(num_rows):
        row = {
            "ts": f"2026-09-{(i % 30) + 1:02d}T{(i % 24):02d}:00:00+00:00",
            "t": 1693526400.0 + (i * 3600),
            "run": f"run_test_{i:04d}",
            "event": "dispatch.end" if i % 3 == 0 else ("phase.start" if i % 3 == 1 else "exit.check"),
        }
        
        if row["event"] == "dispatch.end":
            row["tokens"] = {"in": 500 + i * 10, "out": 100 + i * 5, "total": 600 + i * 15}
            row["cost"] = 0.005 + i * 0.0001
        elif row["event"] == "phase.start":
            row["phase"] = (i % 5) + 1
            row["role"] = "implementer" if i % 2 == 0 else "researcher"
        else:  # exit.check
            row["phase"] = (i % 5) + 1
            row["predicate"] = f"test -f file_{i}.txt"
            row["ok"] = i % 7 != 0
        
        # Embed the three facts at specific positions
        if i == 67:
            row["detail"] = facts[0]
        elif i == 134:
            row["detail"] = facts[1]
        elif i == 189:
            row["detail"] = facts[2]
        
        rows.append(row)
    
    return rows


def create_repair_plan(workdir: Path) -> str:
    """Generate plan.md for repair task with functional EXIT tests and REVIEW: cross."""
    return f"""# Repair Reference Fixture
WORKDIR: {workdir}
DECISION original-conversation: ses_f957d9dc1ffepFpvyzZoBE3AmE

## Phase 1: repair calc.normalize bug (implementer)
TIMEOUT: 300
ATTEMPTS: 1
EXIT: python3 fixtures/repair/tests/test_calc.py
REVIEW: cross

Fix the bug in fixtures/repair/calc.py `normalize` function: it divides by len(values)
instead of (hi - lo). The function should scale [min, max] → [0, 1].

Verify with:
  python3 fixtures/repair/tests/test_calc.py
All three tests must pass: test_clamp, test_normalize_min_max, test_normalize_values_between.
"""


def create_researcher_plan(workdir: Path) -> str:
    """Generate plan.md for researcher task with deterministic facts and independent EXIT."""
    return f"""# Extract Facts from Synthetic Journal
WORKDIR: {workdir}
DECISION original-conversation: ses_f957d9dc1ffepFpvyzZoBE3AmE

## Phase 1: extract three facts (researcher)
TIMEOUT: 300
ATTEMPTS: 1
EXIT: python3 fixtures/answer_key.py

Read fixtures/journal.jsonl (200 synthetic rows) and extract three required facts:
1. The system deployment model and date
2. Phase 1 completion status and duration
3. Review verdict from both reviewers

Write extracted facts to fixtures/findings.txt, one per line in order.
The answer-key verification script will check exact matches.
"""


def prepare_workspace(name: str, is_researcher: bool = False, temp_dir: Path = None) -> Path:
    """Prepare one isolated fixture workspace and return its path."""
    
    if temp_dir:
        base = temp_dir / name
    else:
        base = REFERENCE_ROOT / name
    
    base.mkdir(parents=True, exist_ok=True)
    fixtures_dir = base / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    
    if is_researcher:
        # Create synthetic journal fixture
        rows = generate_synthetic_journal_rows()
        journal_path = fixtures_dir / "journal.jsonl"
        with open(journal_path, "w") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        
        # Create answer-key verification script
        answer_key_script = fixtures_dir / "answer_key.py"
        answer_key_script.write_text("""\
#!/usr/bin/env python3
import json
import sys
from pathlib import Path

# Expected answers (deterministically embedded in journal.jsonl)
EXPECTED = [
    "fact_1: The system was deployed on 2026-09-01 with model claude-sonnet-5.",
    "fact_2: Phase 1 completed with exit code 0 after 187 seconds.",
    "fact_3: The review verdict was PASS from both reviewer-a and reviewer-b.",
]

def verify():
    findings_file = Path("fixtures/findings.txt")
    if not findings_file.exists():
        print("FAIL: fixtures/findings.txt not found")
        return False
    
    with open(findings_file) as f:
        extracted = [line.rstrip("\\n") for line in f if line.strip()]
    
    if len(extracted) != len(EXPECTED):
        print(f"FAIL: Expected {len(EXPECTED)} facts, got {len(extracted)}")
        return False
    
    for i, (got, exp) in enumerate(zip(extracted, EXPECTED)):
        if got != exp:
            print(f"FAIL: Fact {i+1} mismatch")
            print(f"  Expected: {exp}")
            print(f"  Got:      {got}")
            return False
    
    print("ok    all three facts extracted correctly")
    return True

if __name__ == "__main__":
    sys.exit(0 if verify() else 1)
""")
        answer_key_script.chmod(0o755)
        
        # Write plan.md
        plan_path = base / "plan.md"
        plan_path.write_text(create_researcher_plan(base))
    else:
        # Copy repair fixture
        copy_tree(REPAIR_FIXTURE_SOURCE, fixtures_dir / "repair")
        
        # Write plan.md
        plan_path = base / "plan.md"
        plan_path.write_text(create_repair_plan(base))
    
    return base


def build_manifest(repair_path: Path, researcher_path: Path, settings: dict) -> dict:
    """Build manifest with fixture hashes, paths, and frozen settings."""
    return {
        "schema_version": "1.0",
        "prepared_at": "2026-09-05T00:09:21+00:00",
        "original_conversation": ORIGINAL_CONVERSATION,
        "measurement_status": "not_started",
        "workspaces": {
            "repair": {
                "path": str(repair_path),
                "plan_md": str(repair_path / "plan.md"),
                "fixtures_hash": compute_tree_hash(repair_path / "fixtures"),
            },
            "researcher": {
                "path": str(researcher_path),
                "plan_md": str(researcher_path / "plan.md"),
                "fixtures_hash": compute_tree_hash(researcher_path / "fixtures"),
            },
        },
        "frozen_settings": settings,
    }


def load_registry_settings() -> dict:
    """Load model/effort/timeout settings from registry.json."""
    registry_path = Path("/root/pipeline/roles/registry.json")
    with open(registry_path) as f:
        registry = json.load(f)
    
    return {
        "roles": {
            "implementer": {
                "model": registry["roles"]["implementer"]["model"],
                "fallback": registry["roles"]["implementer"]["fallback"],
                "effort": registry["roles"]["implementer"]["effort"],
            },
            "reviewer-a": {
                "model": registry["roles"]["reviewer-a"]["model"],
                "fallback": registry["roles"]["reviewer-a"]["fallback"],
                "effort": registry["roles"]["reviewer-a"]["effort"],
            },
            "reviewer-b": {
                "model": registry["roles"]["reviewer-b"]["model"],
                "fallback": registry["roles"]["reviewer-b"]["fallback"],
                "effort": registry["roles"]["reviewer-b"]["effort"],
            },
            "researcher": {
                "model": registry["roles"]["researcher"]["model"],
                "fallback": registry["roles"]["researcher"]["fallback"],
                "effort": registry["roles"]["researcher"]["effort"],
            },
        },
        "provider": registry["provider"],
    }


def prepare_real_workspaces() -> dict:
    """Prepare the real reference workspaces (idempotent)."""
    
    # Check if already prepared with same hash
    manifest_path = SCRIPT_DIR / "reference-manifest.json"
    if manifest_path.exists():
        with open(manifest_path) as f:
            old_manifest = json.load(f)
        
        repair_path = Path(old_manifest["workspaces"]["repair"]["path"])
        researcher_path = Path(old_manifest["workspaces"]["researcher"]["path"])
        
        # Verify fixtures haven't changed
        if repair_path.exists() and researcher_path.exists():
            new_hashes_repair = compute_tree_hash(repair_path / "fixtures")
            new_hashes_researcher = compute_tree_hash(researcher_path / "fixtures")
            old_hashes_repair = old_manifest["workspaces"]["repair"]["fixtures_hash"]
            old_hashes_researcher = old_manifest["workspaces"]["researcher"]["fixtures_hash"]
            
            if new_hashes_repair == old_hashes_repair and new_hashes_researcher == old_hashes_researcher:
                return old_manifest
            else:
                raise RuntimeError(
                    "REJECT: fixture hashes changed since last preparation; "
                    "remove reference-manifest.json and restart if fixtures are intentionally modified"
                )
    
    settings = load_registry_settings()
    
    repair_path = prepare_workspace("repair", is_researcher=False)
    researcher_path = prepare_workspace("researcher", is_researcher=True)
    
    manifest = build_manifest(repair_path, researcher_path, settings)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    
    return manifest


def test_preparation() -> bool:
    """Self-test: prepare in temp dirs, verify idempotency, test answer-key rejection."""
    
    with tempfile.TemporaryDirectory(prefix="token-efficiency-test-") as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Test 1: Prepare once
        print("Testing: prepare workspaces...")
        repair1 = prepare_workspace("repair", is_researcher=False, temp_dir=tmpdir)
        researcher1 = prepare_workspace("researcher", is_researcher=True, temp_dir=tmpdir)
        
        hashes_repair_1 = compute_tree_hash(repair1 / "fixtures")
        hashes_researcher_1 = compute_tree_hash(researcher1 / "fixtures")
        
        # Test 2: Prepare again (idempotency)
        print("Testing: idempotency...")
        repair2 = prepare_workspace("repair", is_researcher=False, temp_dir=tmpdir)
        researcher2 = prepare_workspace("researcher", is_researcher=True, temp_dir=tmpdir)
        
        hashes_repair_2 = compute_tree_hash(repair2 / "fixtures")
        hashes_researcher_2 = compute_tree_hash(researcher2 / "fixtures")
        
        if hashes_repair_1 != hashes_repair_2 or hashes_researcher_1 != hashes_researcher_2:
            print("FAIL  idempotency: hashes changed on second preparation")
            return False
        print("ok    idempotency")
        
        # Test 3: Parse plans
        print("Testing: plan parsing...")
        sys.path.insert(0, str(Path("/root/pipeline")))
        from pipeline.plan import parse_file
        
        repair_plan = parse_file(repair1 / "plan.md")
        if not repair_plan.phases or repair_plan.phases[0].name != "repair calc.normalize bug":
            print("FAIL  repair plan malformed")
            return False
        if repair_plan.phases[0].review != "cross":
            print("FAIL  repair plan missing REVIEW: cross")
            return False
        print("ok    repair plan parsed")
        
        researcher_plan = parse_file(researcher1 / "plan.md")
        if not researcher_plan.phases or researcher_plan.phases[0].name != "extract three facts":
            print("FAIL  researcher plan malformed")
            return False
        print("ok    researcher plan parsed")
        
        # Test 4: Answer-key rejects bad findings
        print("Testing: answer-key rejects incorrect output...")
        bad_findings = researcher1 / "fixtures" / "findings.txt"
        bad_findings.write_text("wrong line 1\nwrong line 2\nwrong line 3\n")
        
        result = subprocess.run(
            ["python3", str(researcher1 / "fixtures" / "answer_key.py")],
            cwd=str(researcher1),
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print("FAIL  answer-key should reject wrong findings")
            return False
        print("ok    answer-key rejects incorrect output")
        
        # Test 5: Answer-key accepts correct findings
        print("Testing: answer-key accepts correct findings...")
        correct_findings = researcher1 / "fixtures" / "findings.txt"
        correct_findings.write_text(
            "fact_1: The system was deployed on 2026-09-01 with model claude-sonnet-5.\n"
            "fact_2: Phase 1 completed with exit code 0 after 187 seconds.\n"
            "fact_3: The review verdict was PASS from both reviewer-a and reviewer-b.\n"
        )
        
        result = subprocess.run(
            ["python3", str(researcher1 / "fixtures" / "answer_key.py")],
            cwd=str(researcher1),
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            print(f"FAIL  answer-key should accept correct findings: {result.stdout} {result.stderr}")
            return False
        print("ok    answer-key accepts correct output")
        
        # Test 6: Repair fixture still fails
        print("Testing: repair fixture tests still fail (bug present)...")
        result = subprocess.run(
            ["python3", "fixtures/repair/tests/test_calc.py"],
            cwd=str(repair1),
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print("FAIL  repair tests should fail (bug should be present)")
            print(result.stdout)
            return False
        if "FAIL" not in result.stdout or "passed" not in result.stdout.lower():
            print(f"FAIL  unexpected test output: {result.stdout}")
            return False
        print("ok    repair fixture tests fail as expected")
    
    return True


def main():
    if "--self-test" in sys.argv:
        print("=== Self-test mode ===")
        if test_preparation():
            print("\n=== Self-test PASSED ===")
            return 0
        else:
            print("\n=== Self-test FAILED ===")
            return 1
    else:
        try:
            manifest = prepare_real_workspaces()
            print(f"Prepared reference workspaces:")
            print(f"  Repair: {manifest['workspaces']['repair']['path']}")
            print(f"  Researcher: {manifest['workspaces']['researcher']['path']}")
            print(f"Manifest: {SCRIPT_DIR / 'reference-manifest.json'}")
            return 0
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(main())
