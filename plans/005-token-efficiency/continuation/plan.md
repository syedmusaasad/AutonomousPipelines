# Plan 005 continuation: implement against the recorded baseline
WORKDIR: /root/pipeline

DECISION efficiency-approved: the operator's "i approve both" approved token-efficiency changes and matched verification before seat-selection trials. This continues that existing scope, with smaller steps; no new approval or model changes are implied.
DECISION recovery-custody: run_20260904T234020_e16dc5 remains deliberately stopped for a scope violation. Do not resume it. Recovery q_20260905T000921_8f6edf and measurement coordinator q_20260905T001550_854481 completed through the journal. Baseline child runs are run_20260905T001926_cbe4f7 (repair plus cross review) and run_20260905T002109_0c0fd0 (researcher). Preserve and use their evidence; do not rerun the baseline.
DECISION frozen-comparison: role/model/effort, task intent, fixture inputs and acceptance assertions remain frozen for before/after. Standard-tier policy remains in effect. No commits, pushes, new model probes, seat-selection trials, permission changes or other projects' runs are authorized here.

Raw baseline records: /root/pipeline/plans/005-token-efficiency/baseline.json, BASELINE.md, reference-manifest.json, baseline-launches.json. That report's 106.65 seconds is the sum of worker durations, not elapsed completion time: reviewers ran concurrently. Its token count uses legacy accounting and omits cache fields. Correct those labels and extract recorded telemetry by script; do not treat incorrect labels as trustworthy measurements or change the underlying journals.

## Phase 1: accurate usage and bounded run summaries (implementer)
TIMEOUT: 900
ATTEMPTS: 2
EXIT: python3 tests/run.py
EXIT: bin/pipeline check-agents
EXIT: python3 plans/005-token-efficiency/check_efficiency.py accounting

Read initially: pipeline/dispatch.py parse_transcript, pipeline/bench.py, pipeline/journal.py derive_state, pipeline/registry.py, pipeline/status.py, pipeline/cli.py; the baseline JSON and existing run_baseline.py; tests/run.py and targeted accounting tests. Use script-extracted fields from the two named child journals/transcripts, not raw transcripts in model context. Output this bounded slice first; no reference-framework redesign.

Implement a shared incremental usage-event parser, reused by dispatch and reporting. Preserve provider total separately from input/output/reasoning/cache read/write, including absent/unknown fields. Do not assume cross-provider fields can always be summed without overlap. Preserve legacy journal values and provenance; no history rewriting. Ensure partial lines, malformed events and duplicate completed-step event IDs cannot double count or reset accumulated usage. Capture each attempt/fallback once, including failures; retain API-reported cost separately from any price inference.

Add exact run/conversation scoping to bench and a deterministic `pipeline summarize-run <id>` with bounded default output (target at most 30 lines): run/phase state, verified outcome, process and transcript liveness, attempt/fallback/budget overhead, EXIT failures, and references to full artifact files. Redact credential-like material and headers; explicit truncation, no hidden failures. Summaries are read-only and do not use an LLM. Report worker-time sums separately from run elapsed time; exclude gates from worker-produced outcome denominators. Tests must cover source attribution and missing usage.

Re-extract the two baseline child runs by script into baseline-accounting.json; correct BASELINE.md while retaining the original baseline.json. Include cache fields, provider totals, elapsed vs worker time, all four child dispatches and their existing correctness/review results. Report setup/coordination costs separately so they do not disappear into a false savings claim. No new model calls in this phase.

Add check_efficiency.py accounting to independently assert known fixture arithmetic, bounded summary behavior, retained outcome evidence and source IDs. Extend existing characterization coverage rather than lowering it. Allowed edits: pipeline/usage.py (new), dispatch.py, journal.py, bench.py, status.py, registry.py, cli.py; relevant tests and ratchet additions; files under plans/005-token-efficiency/. No fixture/checker/task/model/effort changes. Record touched paths and acceptance results in ACCOUNTING.md. No commit or push.

## Phase 2: scoped evidence, compact output and spending protection (implementer)
TIMEOUT: 1800
ATTEMPTS: 2
REVIEW: cross
EXIT: python3 tests/run.py
EXIT: bin/pipeline check-agents
EXIT: python3 plans/005-token-efficiency/check_efficiency.py implementation

Read initially: ACCOUNTING.md, pipeline/usage.py, pipeline/dispatch.py, engine.py (_dispatch, _run_review, _run_phase), sentry.py, journal.py, roles.py, tests/run.py; roles/CONTRACT.md, interactive.md, reviewer-a.md, reviewer-b.md, reviewer-c.md, and registry.json. Use relevant ranges and follow dependencies when needed. No changes to model choices, effort, grants, mandatory checks, or baseline evidence.

Implement only the remaining approved mechanisms:
1. Persist a phase/attempt pre-work snapshot and a review packet: task intent, changed-file manifest, bounded diff, EXIT results, references to full evidence. Include new, deleted, staged, unstaged and committed changes; never produce a falsely empty diff after a worker commits. For non-git workspaces use a scoped file snapshot. Persist snapshot/packet references across resume; identify pre-existing unrelated changes. Reviewer can follow relevant full files; truncation must not hide findings. Preserve cross-family pair selection and the blocking rule.
2. Concise contract/interactive/reviewer prompts: named initial evidence, avoid redundant unchanged reads, prefer script summaries for raw JSONL, concise deliverables. Do not forbid necessary whole-file/large-context reads or remove worker discipline. Briefs should not repeat the same evidence and contract. Compact suite presentation runs the identical test set, saves full output, includes complete failure information or precise file references, and preserves nonzero exit status. No grep/tail-based success claims, test deletion or bypass; existing hooks and engine EXIT remain authoritative.
3. Finite non-interactive per-role token_budget with measured rationale in docs/TOKEN-EFFICIENCY.md. Initial estimates from the approved plan: fast/lane 40000, implementer/frontend 250000, reviewers 120000; calibrate before first enabling based on baseline's correctly labelled provider usage. Other worker roles need explicit finite budgets. Leave the operator's manual interactive session unaffected. Apply budgets at each reported step using phase-1 parsing; record effective limit, observed usage, unknown telemetry and possible one-step overshoot. Kill only the correctly identified worker process group on breach, preserve partial evidence and journal budget_exceeded. It must be a deliberate stop with no automatic fallback/retry/relight loop, not a host outage or quality success. Do not auto-raise a cap to land work.

Add tests for review evidence after commit/new files/pre-existing changes/resume, available full context and unchanged cross-family blocking; budget boundary/partial telemetry/process-group termination/no retry or sentry restart; unchanged suite discovery/failure propagation and preserved headless instructions. Add check_efficiency.py implementation with these deterministic checks. Regenerate agents through bin/pipeline render-agents, never hand-edit generated files. Capture registry model/effort/grant fingerprints before changing only budget policy and assert them unchanged afterwards.

Allowed edits: engine.py, dispatch.py, usage.py, journal.py, sentry.py, roles.py, bench.py, status.py, cli.py; CONTRACT.md, interactive.md and reviewer prompts; registry.json budget fields only; tests/run.py, test_suite.py, fake-devpass-code, focused new tests and ratchet additions; docs/TOKEN-EFFICIENCY.md and plan-local acceptance files. No changes to trial.py, selection pools, prepared baseline tasks/checkers or permissions. Record the evidence paths for this phase's cross review in IMPLEMENTATION.md. No commit or push.

## Phase 3: matched after measurement and honest comparison (fast-worker)
TIMEOUT: 1800
ATTEMPTS: 1
EXIT: python3 tests/run.py
EXIT: bin/pipeline check-agents
EXIT: python3 plans/005-token-efficiency/check_efficiency.py comparison

Read ACCOUNTING.md, IMPLEMENTATION.md, baseline-accounting.json, reference-manifest.json, run_baseline.py and the original fixture source only as needed. Preserve the two baseline runs and repaired/output workspaces. Create fresh after-condition workspaces below /root/.system/projects/token-efficiency-reference/after/ from original fixture inputs, not the already repaired baseline workspaces. Prepared plan differences may only change absolute workdir locations; role/model/effort, requested outputs and acceptance checks stay identical. Archive fresh hashes before any launch, and independently check immutable hashes after completion.

Launch exactly two after child plans sequentially through /root/pipeline/bin/run --conv ses_f957d9dc1ffepFpvyzZoBE3AmE: repair with REVIEW: cross, then researcher. Expected measured workers: four, matching baseline. Record child IDs immediately in after-launches.json; reconcile registry paths before launching so a restart never duplicates runs. Never call devpass-code directly or dispatch reviewers yourself. Child timeout is 300 seconds per prepared worker; bounded coordinator wait 1400 seconds. Existing journal policy handles failures; do not restart, bypass a budget, drop checks, edit answers or change a model to get a result. No seat-selection trials.

Aggregate recorded telemetry and outcomes by script into comparison.json and RESULTS.md. Separate provider total, input/output/reasoning/cache, dollars, parallel worker-time sums and elapsed completion time, including failed attempts and coordination overhead. Equal-quality assertions, unchanged fixtures/settings and review obligations are mandatory; failed/truncated jobs are not savings. A readable answer key remains a disclosed limitation for the researcher fixture, not proof of blind research quality. Keep comparison sample-size/cache/host-load limitations explicit. Only claim token/cost/runtime savings where observed; an inconclusive or regressed comparison returns nonzero from check_efficiency.py comparison and requires judgment, not automatic retries.

Generate a scoped, public-safe benchmark report with regeneration commands and HANDOFF.md giving child IDs, verification results, touched files, limitations and whether the efficiency changes are ready for use in later model trials. Raw transcripts stay in the estate. No commit, push, model selection or new worker launches beyond these two child plans.
