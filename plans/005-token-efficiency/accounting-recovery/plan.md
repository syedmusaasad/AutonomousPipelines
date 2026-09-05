# Accounting recovery: one module, then one integration
WORKDIR: /root/pipeline

DECISION continue-approved: the operator said "continue" after approving token-efficiency work. This bounded recovery narrows the existing accounting task; no new model trials, seat changes, commits, pushes, permission changes or background services are authorized.
DECISION preserve-baseline: retain the completed baseline runs run_20260905T001926_cbe4f7 and run_20260905T002109_0c0fd0. Do not repeat them. The original plan-005 run and its broad continuation remain stopped; this plan does not relaunch either.

## Phase 1: standalone streaming usage accumulator (fast-worker)
TIMEOUT: 420
ATTEMPTS: 1
EXIT: python3 -m unittest discover -s tests -p test_usage_accounting.py
EXIT: python3 -c "from pipeline.usage import UsageAccumulator; a=UsageAccumulator(); a.feed_line('{\"type\":\"step_finish\",\"part\":{\"id\":\"p1\",\"tokens\":{\"total\":150,\"input\":100,\"output\":20,\"reasoning\":5,\"cache\":{\"read\":25,\"write\":0}},\"cost\":0.1}}'); s=a.snapshot(); assert s['provider_total']==150 and s['input']==100 and s['cache_read']==25 and s['cost']==0.1"

Precondition: before editing, use one bounded local process (at most 120s) to verify Journal('run_20260905T010559_3a82bd').state() is stopped and its engine/current workers are gone. Otherwise stop without editing. Do not resume it.

Read only pipeline/dispatch.py parse_transcript and tests/harness.py initially. Deliver only pipeline/usage.py and tests/test_usage_accounting.py. Build the standalone module and its tests first; no production integration or unrelated reading in this phase. Use only stdlib. Do not call any model or delegate anything.

API: UsageAccumulator.feed_line(line: str) consumes one JSON event line. snapshot() returns independent cumulative values for provider_total, input, output, reasoning, cache_read, cache_write, cost, steps, missing_fields and telemetry_complete. Only completed step_finish events contribute usage. Keep provider totals as reported; never reconstruct them by assuming reasoning/cache overlap. Zero is different from missing. For partially reported fields retain known sums and missing counts so a consumer cannot silently treat incomplete sums as complete measurements. Empty streams are unknown, not a zero-cost successful dispatch.

Deduplicate step_finish events by part.id when provided, retain distinct events without stable IDs without inventing duplicate detection, ignore non-usage events, and tolerate malformed lines. Do not mark an incomplete partial line as consumed; the caller may retry it once completed. Snapshot mutation must not affect accumulator state. No whole-transcript loading needed for this API.

Add independent unittest cases for multiple steps, input/output/reasoning/cache kept separate from provider_total, absent/null/zero fields, absent cost, duplicate stable IDs, malformed/non-step lines, empty stream, snapshot immutability and JSON serialization. Run the exact unittest command in EXIT. Preserve any pre-existing file you did not author; report a collision instead of overwriting another worker's edits. No registry/model/effort changes, commits or pushes.

## Phase 2: integrate and correct baseline telemetry (fast-worker)
TIMEOUT: 600
ATTEMPTS: 1
REVIEW: cross
EXIT: python3 -m unittest discover -s tests -p test_usage_accounting.py
EXIT: python3 tests/run.py
EXIT: bin/pipeline check-agents
EXIT: python3 plans/005-token-efficiency/accounting-recovery/check.py

Read pipeline/usage.py, pipeline/dispatch.py parse_transcript and DispatchResult, the dispatch parsing tests in tests/test_suite.py, baseline.json, baseline-launches.json and run_baseline.py. Read relevant journal row formats as needed. Files allowed: pipeline/dispatch.py, tests/test_usage_accounting.py, tests/test_suite.py, tests/ratchet.json, and accounting-recovery/check.py, extract.py, ACCOUNTING.md plus plan-005 baseline-accounting.json. Do not edit the baseline JSON, original tasks/checkers, manifests, live registry, prompts, bench/status/CLI or model settings.

Use the new accumulator in parse_transcript while retaining existing callers and their legacy tokens semantics. Iterate line-by-line instead of loading the whole transcript. Add a usage field with explicit new provenance and completeness to parsed output, DispatchResult.as_row and hence new dispatch.end rows. Preserve quota detection, session/final deliverable parsing and a bounded diagnostic tail. Do not silently rewrite old journal values or rename existing outcome fields.

Add targeted tests showing the same transcript retains legacy token fields and exposes separate provider/cache telemetry; preserve quota/error behavior and final text. Register a test in the existing full-suite entrypoint that runs the new module tests so they cannot be skipped by the ratchet. Existing checks remain intact. Run the full suite and drift guard; report failures rather than deleting assertions.

Write extract.py to read only the two named baseline child journals/transcripts by script and regenerate baseline-accounting.json. Include all four baseline worker dispatches, independent usage fields and completeness, API dollars, sum of worker durations, each run's elapsed open-to-close duration, and existing EXIT/review outcomes. Do not call a model, run the baseline again or print full transcripts. Keep setup/recovery/coordinator cost outside workload totals and explicitly reference their run IDs as excluded overhead, not savings.

check.py independently validates accumulator known-fixture arithmetic, exact baseline run IDs and four dispatches, nonmissing telemetry provenance, both successful run closures and retained EXIT/cross-review evidence; errors return nonzero. ACCOUNTING.md states exact changed paths, commands, results, corrected metrics and limits. This is accounting readiness only, not completion of all efficiency work or selection of any model. No commit or push.
