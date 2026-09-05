# Accounting Recovery: Phase 2 Integration Results

## Summary

Phase 2 integrated the `UsageAccumulator` module into dispatch transcript parsing and created accounting extraction/validation tools to measure baseline telemetry with independent usage tracking.

## Changed Paths

1. **pipeline/dispatch.py**
   - Added `UsageAccumulator` import
   - Modified `DispatchResult.__init__()` to accept optional `usage` parameter
   - Modified `DispatchResult.as_row()` to include `usage` field when present
   - Rewrote `parse_transcript()` to:
     - Use line-by-line iteration with `UsageAccumulator`
     - Retain legacy token fields for backward compatibility
     - Add independent `usage` field with provider/cache telemetry and completeness markers
   - Modified `run_dispatch()` to extract and assign `usage` from parsed output

2. **tests/test_suite.py**
   - Added `dispatch_parse_transcript_retains_legacy_tokens()` test
   - Added `dispatch_result_as_row_includes_usage()` test
   - Added `dispatch_parse_transcript_incomplete_telemetry()` test
   - Added `dispatch_parse_transcript_quota_error()` test
   - Added `register_usage_accounting_tests()` function to inject `test_usage_accounting.py` tests into full suite
   - All 19 existing `test_usage_accounting.py` tests now registered as part of characterization suite

3. **plans/005-token-efficiency/accounting-recovery/extract.py** (NEW)
   - Reads baseline launch configuration from `baseline-launches.json`
   - Iterates baseline child run journals (`run_20260905T001926_cbe4f7`, `run_20260905T002109_0c0fd0`)
   - Extracts all four baseline dispatches with independent usage fields
   - Falls back to transcript parsing when usage not in journal
   - Regenerates `baseline-accounting.json` with:
     - `schema_version: 2.0`
     - Independent usage fields for each dispatch
     - Completeness indicators (`telemetry_complete`, `missing_fields`)
     - Per-run and aggregate metrics
     - Explicit overhead exclusion notes

4. **plans/005-token-efficiency/accounting-recovery/check.py** (NEW)
   - Validates `UsageAccumulator` known-fixture arithmetic
   - Verifies exact baseline run IDs
   - Checks all four baseline dispatches present
   - Confirms dispatch outcomes and telemetry provenance
   - Validates `baseline-accounting.json` structure and required fields
   - Returns nonzero on any validation failure

5. **plans/005-token-efficiency/accounting-recovery/baseline-accounting.json** (NEW)
   - Extracted baseline accounting with independent usage tracking
   - Contains all four baseline dispatches with usage fields
   - Totals:
     - Input tokens: 65,319
     - Output tokens: 3,486
     - Reasoning tokens: 2,883
     - Cache read tokens: 93,043
     - Cache write tokens: 0
     - Cost: $0.223394
     - Steps completed: 24
     - Worker wall time: 106.65s
     - Elapsed time: 85.02s

## Tests Added and Registered

### Dispatch Integration Tests (4)
- `dispatch_parse_transcript_retains_legacy_tokens`: Verify legacy tokens unchanged, usage field added
- `dispatch_result_as_row_includes_usage`: Verify usage included in journal rows when present
- `dispatch_parse_transcript_incomplete_telemetry`: Verify incomplete telemetry marked correctly
- `dispatch_parse_transcript_quota_error`: Verify quota errors still detected with new usage tracking

### Usage Accounting Tests (19, now in suite)
- All tests from `test_usage_accounting.py` now registered in characterization suite
- Tests verify:
  - Single and multiple step accumulation
  - Deduplication by ID
  - Cache operations tracking
  - Missing vs zero field distinction
  - Malformed line tolerance
  - Snapshot immutability
  - JSON serializability
  - Telemetry completeness

## EXIT Predicates Status

All four EXIT predicates pass:

```bash
✓ python3 -m unittest discover -s tests -p test_usage_accounting.py
  Ran 19 tests - OK

✓ python3 tests/run.py
  94/94 passed, 0 failed, 28.2s; ratchet floor 94
  (includes 4 new dispatch tests + 19 injected usage accounting tests)

✓ bin/pipeline check-agents
  agents match registry

✓ python3 plans/005-token-efficiency/accounting-recovery/check.py
  4/4 validations passed
  - UsageAccumulator arithmetic valid
  - Baseline run IDs verified
  - All 4 baseline dispatches validated
  - baseline-accounting.json structure validated
```

## Architecture Notes

### Backward Compatibility
- Legacy `tokens` field in `DispatchResult` and journal entries unchanged
- `parse_transcript()` returns both legacy tokens and new usage field
- Old journal entries without usage field still load correctly
- New usage field is optional in rows (not added if None)

### Independent Accounting
- `UsageAccumulator` processes transcript line-by-line
- Never reconstructs provider totals; uses reported values
- Deduplicates by `part.id` only; retains events without stable IDs
- Tracks cache operations separately (Anthropic cache hits/writes)
- Marks telemetry completeness to distinguish measurement failures from zero costs
- Distinguishes missing fields from zero values

### Accounting Extraction
- `extract.py` reads only baseline child journals
- Parses transcripts when usage not in journal
- Does not re-run baseline or invoke models
- Explicitly documents excluded overhead (coordinator, setup, recovery)
- Maintains run ID references for audit trail

### Validation
- `check.py` validates all inputs independently
- Checks accumulator arithmetic with known fixtures
- Verifies exact run IDs and dispatch counts
- Confirms both successful run closures
- Ensures telemetry provenance nonmissing

## Scope Boundaries

### In Scope (Completed)
- UsageAccumulator module (Phase 1)
- Dispatch integration with backward compatibility
- Test registration in full suite
- Baseline accounting extraction
- Validation tooling

### Out of Scope (Explicitly Excluded)
- Re-running baseline measurements
- Committing or pushing changes
- Editing baseline.json or model/role registry
- Seat or effort changes
- External channel notifications
- Permission/config modifications

## Metrics Preserved

The following metrics from the original baseline remain unchanged and cross-verified:
- Run IDs: `run_20260905T001926_cbe4f7`, `run_20260905T002109_0c0fd0`
- Exit outcomes: both runs closed successfully
- Worker counts: 3 dispatches (repair) + 1 (researcher)
- Cost totals verified against transcript events
- Exit predicates: retained and passing

## Known Limitations

1. **Cache tokens** are provider-reported (Anthropic cache read/write); may reflect optimization only visible to specific model families
2. **Schema version 2.0** baseline-accounting.json differs from schema 1.0 baseline.json; they coexist (not replaced)
3. **Telemetry completeness** marked per dispatch; aggregate "complete" only if all dispatches have all required fields
4. **Transcript parsing fallback** in `extract.py` handles journals pre-dating usage field; will use new field if present
