# Verification Report: Token-Efficiency Accounting Corrections

## Summary
All three verification checks **PASSED** with returncode 0.

## Executed Commands

1. **test_review_provenance**: `python3 -m unittest discover -s tests -p test_review_provenance.py`
   - Return code: 0
   - Elapsed: 18.45s
   - Tests run: 10 (all passed)
   - Log: `test_review_provenance.log`

2. **run_harness**: `python3 tests/run.py`
   - Return code: 0
   - Elapsed: 44.91s
   - Tests run: 104 (all passed), ratchet floor 104
   - Log: `run_harness.log`

3. **check_agents**: `bin/pipeline check-agents`
   - Return code: 0
   - Elapsed: 0.09s
   - Output: "agents match registry"
   - Log: `check_agents.log`

## Results

**ALL CHECKS PASSED**: True

Total elapsed: 63.46s (well within 360s limit)

## Code Inspection vs. Tested Behavior

### Key Observation
The test suite (104 tests including review provenance and usage accounting) passed completely, indicating the implementation meets the test specification. However, code inspection reveals that five specification-level issues recorded in the prior reviewer assessment remain unaddressed:

1. **dispatch.py:196** - `parse_transcript()` still loads full transcript via `read_text()` before splitting into lines (line 196). The brief required line-by-line iteration to avoid whole-file loading. The iterative loop follows (line 209+) but the initial load constraint is violated.

2. **dispatch.py:240** - The `usage` payload includes `provider_total`, `input_tokens`, `output_tokens`, `reasoning_tokens`, `cache_read_tokens`, `cache_write_tokens`, `cost_usd`, `steps_completed`, `missing_fields`, and `telemetry_complete`, but lacks an explicit `provenance` field required for dispatch.end rows to carry usage provenance metadata.

3. **accounting-recovery/extract.py:177** - Output schema does not include retained EXIT/check or cross-review verdict evidence requested by phase brief.

4. **accounting-recovery/baseline-accounting.json:7** - `excluded_overhead` field references workload baseline run IDs (repair/researcher) rather than setup/recovery/coordinator overhead run IDs.

5. **accounting-recovery/check.py:146** - Validation logic does not assert retained EXIT/cross-review evidence or successful run.close outcomes; checks only event presence and basic structure.

### Test Isolation
REVIEW_OUT isolation is confirmed present in tests/harness.py (lines 45, 54-55), where REVIEW_OUT, ITEM, LANE_OUT, LANE, PHASE, and RUN are explicitly scrubbed from os.environ during setUp and restored in cleanup.

## Source Fingerprint
`3056737ef8688079144838ce26dcf31f0be4a27230598f394abccdce39762699`

## Unresolved Findings
All five prior reviewer blocking findings remain unresolved in the tested code:
- `parse_transcript` whole-file loading pattern
- `usage` object lacks explicit provenance field
- Extract output missing EXIT/verdict evidence
- Baseline accounting wrong overhead exclusion refs
- Validator missing EXIT/review evidence checks

## Verdict
**Test execution: PASS**
**Code inspection: FIVE UNRESOLVED BRIEF-CRITICAL FINDINGS**

The test suite validates the implementation against current specifications, but the specification-level accounting gaps identified in the prior review remain. These are not test coverage issues but deliberate reviewer findings about incomplete accounting auditing and transcript loading optimization that the reviewed work chose not to address.
