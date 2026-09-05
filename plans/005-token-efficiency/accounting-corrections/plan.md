# Correct failed review acceptance and the accounting findings
WORKDIR: /root/pipeline

DECISION approved-quality-repair: this continues already-approved accounting work. The run run_20260905T013230_eb40aa closed done, but Codex returned BLOCKING and the other reviewer timed out with a review artifact whose entire rationale was "fake". That is not trustworthy acceptance. Preserve its journal and review files unchanged. No commits, pushes, model/effort/grant changes, baseline reruns, model-selection trials or new probes.

## Phase 1: review provenance and test isolation (fast-worker)
TIMEOUT: 600
ATTEMPTS: 1
EXIT: python3 tests/run.py
EXIT: bin/pipeline check-agents
EXIT: python3 -m unittest discover -s tests -p test_review_provenance.py

Read first pipeline/engine.py _run_review/_dispatch, tests/harness.py, tests/fake-devpass-code and reviewer fixtures in tests/test_suite.py. Evidence: /root/.system/runs/run_20260905T013230_eb40aa/journal.jsonl and phase-2/review/reviewer-a/review.md and reviewer-b/review.md. Extract only relevant transcript tool events via scripts if needed; do not ingest full transcripts. Determine and document whether a nested test inherited REVIEW_OUT and overwrote the real review file; this is a hypothesis until confirmed.

Changes allowed: engine.py review lifecycle only; tests/harness.py isolation; tests/fake-devpass-code narrowly for guard/test support; tests/test_review_provenance.py, targeted test_suite.py and ratchet additions; plan-local REVIEW-INTEGRITY.md. No registry, model, effort or permission edits.

Require successful reviewer dispatch completion before a verdict is eligible: a timeout/killed/failed dispatch with any pre-existing PASS file is UNAVAILABLE, never PASS. Give each reviewer dispatch attempt a fresh own directory and capture artifact path/hash with that attempt; do not reuse prior retries' files. Existing both-BLOCKING stops and one-BLOCKING/one-successful-verdict rules stay unchanged. A missing/failed review stops rather than silently completes the phase. Preserve full findings as evidence; no fabricated verdicts.

Test harness must scrub inherited live-run REVIEW_OUT, ITEM, LANE_OUT, PHASE/RUN and worker/session variables as applicable inside its isolated context and restore the caller environment afterwards. Fake worker must never write a real review dir from a nested suite. Add deterministic tests for timeout-with-PASS, failed-with-PASS, stale verdict after relaunch, successful valid verdict, directory separation, and nested fake suite with inherited REVIEW_OUT leaving an external sentinel review file byte-identical. Register new tests in the full suite; no live AI tests. Full suite and drift guard must pass. REVIEW-INTEGRITY.md states confirmed cause or remaining uncertainty, changed files and tests. No commit or push.

## Phase 2: fix all five accounting findings and re-review (fast-worker)
TIMEOUT: 600
ATTEMPTS: 1
REVIEW: cross
EXIT: python3 tests/run.py
EXIT: python3 -m unittest discover -s tests -p test_review_provenance.py
EXIT: bin/pipeline check-agents
EXIT: python3 plans/005-token-efficiency/accounting-recovery/check.py

Read the blocking findings at /root/.system/runs/run_20260905T013230_eb40aa/phase-2/review/reviewer-b/review.md, pipeline/dispatch.py parse_transcript, pipeline/usage.py, accounting-recovery/extract.py and check.py, baseline-launches.json and baseline-accounting.json. Initial edits limited to those accounting sources, relevant tests/ratchet, ACCOUNTING.md and corrected generated accounting data. Do not alter original baseline input/checkers, models, trial pools, or historical journals.

Resolve each finding explicitly:
1. Iterate transcript lines; remove read_text/full splitlines loading. Keep bounded diagnostics/final text, legacy result semantics and quota detection. Add a regression test rejecting read_text usage for transcript paths.
2. Add explicit usage provenance/version/source fields to parsed usage and dispatch.end alongside completeness. Preserve provider totals and cache without synthetic double counting.
3. Regenerated accounting must retain successful run.close, EXIT outcomes and both actual cross-family verdicts from each applicable baseline child; references to journal rows/artifacts for independent verification. Include per-run elapsed duration separately from sum of worker durations.
4. excluded_overhead must reference actual setup/recovery/coordinator run IDs (q_20260905T000921_8f6edf, q_20260905T001550_854481 and original stopped work as separately labelled development), never workload IDs run_20260905T001926_cbe4f7 or run_20260905T002109_0c0fd0. Show overhead separately, do not hide it or add it to workload costs.
5. Strengthen check.py to fail on missing/bad run.close, failed EXIT, absent/unavailable/timed-out or wrong-family review evidence, incorrect overhead membership and missing provenance. Add tampered-copy negative tests; never edit live journals for tests.

Run extract.py to regenerate only from recorded baseline evidence. Update ACCOUNTING.md with corrected claims, source IDs, limits and tests; retain original baseline.json. The full suite must still include all prior checks. Do not create a success receipt from your own prose. The engine will run EXIT and attach cross review; reviewers read this finding list and the exact changed files, starting with REVIEW-INTEGRITY.md, dispatch.py, extract.py, check.py and targeted new tests. No model tests or baseline reruns, commits or pushes.
