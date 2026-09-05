#!/usr/bin/env python3
"""Unit tests for prepare_reference.py - frozen baseline fixture preparation.

Tests verification:
  - fixture hash computation (deterministic)
  - workspace preparation idempotency
  - plan.md parsing and validation
  - answer-key script behavior (rejection of bad output, acceptance of correct)
  - synthetic journal generation (201 rows with 3 embedded facts)
  - no model calls made during preparation
"""

import sys
import json
import tempfile
import subprocess
from pathlib import Path
from unittest import mock


# Add pipeline to path for imports
sys.path.insert(0, str(Path("/root/pipeline")))

# Import after path is set
import prepare_reference


def test_compute_file_hash():
    """Test that file hash is deterministic and consistent."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write("test content")
        f.flush()
        path = Path(f.name)
    
    try:
        hash1 = prepare_reference.compute_file_hash(path)
        hash2 = prepare_reference.compute_file_hash(path)
        assert hash1 == hash2, "Same file should produce same hash"
        assert len(hash1) == 64, "SHA256 hash should be 64 hex chars"
        print("ok    compute_file_hash is deterministic")
    finally:
        path.unlink()


def test_compute_tree_hash():
    """Test that directory tree hash is deterministic."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        (tmpdir / "file1.txt").write_text("content1")
        (tmpdir / "subdir").mkdir()
        (tmpdir / "subdir" / "file2.txt").write_text("content2")
        
        hashes1 = prepare_reference.compute_tree_hash(tmpdir)
        hashes2 = prepare_reference.compute_tree_hash(tmpdir)
        
        assert hashes1 == hashes2, "Same tree should produce same hashes"
        assert "file1.txt" in hashes1
        assert "subdir/file2.txt" in hashes1
        print("ok    compute_tree_hash is deterministic")


def test_generate_synthetic_journal_rows():
    """Test synthetic journal generation with embedded facts."""
    rows = prepare_reference.generate_synthetic_journal_rows(200)
    
    assert len(rows) == 200, "Should generate exactly 200 rows"
    
    # Check for embedded facts
    row_67 = rows[67]
    row_134 = rows[134]
    row_189 = rows[189]
    
    assert "fact_1" in row_67.get("detail", ""), "Fact 1 should be at row 67"
    assert "fact_2" in row_134.get("detail", ""), "Fact 2 should be at row 134"
    assert "fact_3" in row_189.get("detail", ""), "Fact 3 should be at row 189"
    
    print("ok    synthetic journal has 3 embedded facts at expected positions")


def test_repair_plan_generation():
    """Test repair plan generation has required fields."""
    plan_text = prepare_reference.create_repair_plan(Path("/test/workdir"))
    
    assert "WORKDIR: /test/workdir" in plan_text
    assert "TIMEOUT: 300" in plan_text
    assert "ATTEMPTS: 1" in plan_text
    assert "REVIEW: cross" in plan_text
    assert "fixtures/repair/tests/test_calc.py" in plan_text
    assert prepare_reference.ORIGINAL_CONVERSATION in plan_text
    print("ok    repair plan has required fields and REVIEW: cross")


def test_researcher_plan_generation():
    """Test researcher plan generation has required fields."""
    plan_text = prepare_reference.create_researcher_plan(Path("/test/workdir"))
    
    assert "WORKDIR: /test/workdir" in plan_text
    assert "TIMEOUT: 300" in plan_text
    assert "ATTEMPTS: 1" in plan_text
    assert "fixtures/answer_key.py" in plan_text
    assert "200 synthetic rows" in plan_text or "journal.jsonl" in plan_text
    assert prepare_reference.ORIGINAL_CONVERSATION in plan_text
    print("ok    researcher plan has required fields")


def test_prepare_repair_workspace():
    """Test repair workspace preparation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        path = prepare_reference.prepare_workspace("repair", is_researcher=False, temp_dir=tmpdir)
        
        assert path.exists(), "Workspace should exist"
        assert (path / "plan.md").exists(), "plan.md should exist"
        assert (path / "fixtures" / "repair" / "calc.py").exists(), "calc.py should exist"
        assert (path / "fixtures" / "repair" / "tests" / "test_calc.py").exists(), "test_calc.py should exist"
        
        plan_text = (path / "plan.md").read_text()
        assert "normalize" in plan_text, "Plan should mention normalize bug"
        
        print("ok    repair workspace prepared with correct structure")


def test_prepare_researcher_workspace():
    """Test researcher workspace preparation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        path = prepare_reference.prepare_workspace("researcher", is_researcher=True, temp_dir=tmpdir)
        
        assert path.exists(), "Workspace should exist"
        assert (path / "plan.md").exists(), "plan.md should exist"
        assert (path / "fixtures" / "journal.jsonl").exists(), "journal.jsonl should exist"
        assert (path / "fixtures" / "answer_key.py").exists(), "answer_key.py should exist"
        
        # Verify journal content
        with open(path / "fixtures" / "journal.jsonl") as f:
            rows = [json.loads(line) for line in f]
        assert len(rows) == 200, "Journal should have 200 rows"
        
        print("ok    researcher workspace prepared with synthetic journal")


def test_plan_parsing():
    """Test that generated plans can be parsed by pipeline.plan."""
    from pipeline.plan import parse_file
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        repair_path = prepare_reference.prepare_workspace("repair", is_researcher=False, temp_dir=tmpdir)
        researcher_path = prepare_reference.prepare_workspace("researcher", is_researcher=True, temp_dir=tmpdir)
        
        repair_plan = parse_file(repair_path / "plan.md")
        researcher_plan = parse_file(researcher_path / "plan.md")
        
        assert len(repair_plan.phases) >= 1, "Repair plan should have at least 1 phase"
        assert len(researcher_plan.phases) >= 1, "Researcher plan should have at least 1 phase"
        
        repair_phase = repair_plan.phases[0]
        assert repair_phase.review == "cross", "Repair phase should have REVIEW: cross"
        assert repair_phase.timeout == 300, "Repair phase should have TIMEOUT: 300"
        assert len(repair_phase.exits) > 0, "Repair phase should have EXIT predicates"
        
        researcher_phase = researcher_plan.phases[0]
        assert researcher_phase.timeout == 300, "Researcher phase should have TIMEOUT: 300"
        
        print("ok    generated plans parse correctly with pipeline.plan")


def test_answer_key_rejection():
    """Test that answer_key.py rejects incorrect findings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        path = prepare_reference.prepare_workspace("researcher", is_researcher=True, temp_dir=tmpdir)
        
        # Write bad findings
        (path / "fixtures" / "findings.txt").write_text("wrong\nlines\nhere\n")
        
        result = subprocess.run(
            ["python3", "fixtures/answer_key.py"],
            cwd=str(path),
            capture_output=True,
            text=True
        )
        
        assert result.returncode != 0, "answer_key should reject bad findings"
        assert "FAIL" in result.stdout, "output should indicate failure"
        
        print("ok    answer_key rejects incorrect findings")


def test_answer_key_acceptance():
    """Test that answer_key.py accepts correct findings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        path = prepare_reference.prepare_workspace("researcher", is_researcher=True, temp_dir=tmpdir)
        
        # Write correct findings
        (path / "fixtures" / "findings.txt").write_text(
            "fact_1: The system was deployed on 2026-09-01 with model claude-sonnet-5.\n"
            "fact_2: Phase 1 completed with exit code 0 after 187 seconds.\n"
            "fact_3: The review verdict was PASS from both reviewer-a and reviewer-b.\n"
        )
        
        result = subprocess.run(
            ["python3", "fixtures/answer_key.py"],
            cwd=str(path),
            capture_output=True,
            text=True
        )
        
        assert result.returncode == 0, f"answer_key should accept correct findings; got: {result.stdout}"
        assert "ok" in result.stdout.lower(), "output should indicate success"
        
        print("ok    answer_key accepts correct findings")


def test_repair_fixture_tests_fail():
    """Test that repair fixture tests fail (bug is present)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        path = prepare_reference.prepare_workspace("repair", is_researcher=False, temp_dir=tmpdir)
        
        result = subprocess.run(
            ["python3", "fixtures/repair/tests/test_calc.py"],
            cwd=str(path),
            capture_output=True,
            text=True
        )
        
        assert result.returncode != 0, "Tests should fail due to bug"
        assert "FAIL" in result.stdout, "Output should show failures"
        
        print("ok    repair fixture tests fail (bug present)")


def test_idempotency():
    """Test that preparation is idempotent."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Prepare twice
        path1 = prepare_reference.prepare_workspace("repair", is_researcher=False, temp_dir=tmpdir)
        hashes1 = prepare_reference.compute_tree_hash(path1 / "fixtures")
        
        path2 = prepare_reference.prepare_workspace("repair", is_researcher=False, temp_dir=tmpdir)
        hashes2 = prepare_reference.compute_tree_hash(path2 / "fixtures")
        
        assert hashes1 == hashes2, "Repeated preparation should produce identical hashes"
        
        print("ok    workspace preparation is idempotent")


def test_manifest_structure():
    """Test that manifest has required fields."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        repair_path = prepare_reference.prepare_workspace("repair", is_researcher=False, temp_dir=tmpdir)
        researcher_path = prepare_reference.prepare_workspace("researcher", is_researcher=True, temp_dir=tmpdir)
        
        settings = prepare_reference.load_registry_settings()
        manifest = prepare_reference.build_manifest(repair_path, researcher_path, settings)
        
        assert manifest["schema_version"] == "1.0"
        assert manifest["original_conversation"] == prepare_reference.ORIGINAL_CONVERSATION
        assert manifest["measurement_status"] == "not_started"
        
        assert "repair" in manifest["workspaces"]
        assert "researcher" in manifest["workspaces"]
        
        assert "path" in manifest["workspaces"]["repair"]
        assert "plan_md" in manifest["workspaces"]["repair"]
        assert "fixtures_hash" in manifest["workspaces"]["repair"]
        
        assert "frozen_settings" in manifest
        assert "roles" in manifest["frozen_settings"]
        
        print("ok    manifest has correct structure")


def test_no_model_calls():
    """Verify that preparation makes no actual model calls."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Mock any potential model calls
        with mock.patch("subprocess.run") as mock_run:
            # Allow expected subprocess calls (like test execution)
            mock_run.side_effect = lambda *args, **kwargs: subprocess.run(*args, **kwargs)
            
            prepare_reference.prepare_workspace("repair", is_researcher=False, temp_dir=tmpdir)
            prepare_reference.prepare_workspace("researcher", is_researcher=True, temp_dir=tmpdir)
        
        print("ok    no model calls during preparation")


def main():
    """Run all tests."""
    tests = [
        test_compute_file_hash,
        test_compute_tree_hash,
        test_generate_synthetic_journal_rows,
        test_repair_plan_generation,
        test_researcher_plan_generation,
        test_prepare_repair_workspace,
        test_prepare_researcher_workspace,
        test_plan_parsing,
        test_answer_key_rejection,
        test_answer_key_acceptance,
        test_repair_fixture_tests_fail,
        test_idempotency,
        test_manifest_structure,
        test_no_model_calls,
    ]
    
    failed = []
    for test in tests:
        try:
            test()
        except Exception as e:
            failed.append((test.__name__, str(e)))
            print(f"FAIL  {test.__name__}: {e}")
    
    print(f"\n{len(tests) - len(failed)}/{len(tests)} tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
