# Plan 005: reduce avoidable tokens without losing verified work
WORKDIR: /root/pipeline

DECISION candidates-approved: the operator said "i approve both", approving plan 004's candidate/task sets and the existing phase-5 commit and push. Gate release is journaled separately for run_20260904T065718_4fea9b.
DECISION efficiency-before-trials: the same answer approved this token-efficiency work before model-selection trials. This plan runs controlled workflow measurements, not seat-selection trials, and does not change models or effort levels.
DECISION efficiency-quality: runtime and token efficiency matter, but savings must not come from skipped checks, hidden failures, weaker review, or depriving a large-context task of necessary evidence. Preserve the non-premium policy for every automatic dispatch.

Scope: token accounting, compact evidence, scoped review inputs, bounded worker spending, and a matched before/after measurement. No changes to other projects or their runs. No model/effort selection, changes to permissions, deployment, or publication in this plan. No commits or pushes: verified files and reports are the deliverable; publication is a separate ceremony. The $50 model-trial allowance is not authorization to run those trials here.
Execution bound: at most eight measured child worker dispatches total (four baseline, four after), plus this plan's workers and its declared cross review. Child runs must use quick/run and inherit this conversation. No direct devpass-code probes. Save checkpoints so a parent retry cannot repeat measured runs. Report all incurred cost, retries, and missing measurements.

## Phase 1: baseline and frozen reference workload (implementer)
TIMEOUT: 1800
ATTEMPTS: 1
EXIT: python3 -c "from pipeline.journal import Journal; s=Journal('run_20260904T065718_4fea9b').state(); assert s['closed']=='done' and not s['stopped'], 'plan 004 has not landed'"
EXIT: python3 plans/005-token-efficiency/check.py baseline
EXIT: test -s plans/005-token-efficiency/BASELINE.md

Predecessor: run_20260904T065718_4fea9b. Before writing project files, poll its Journal.state() in one bounded shell process for at most 600 seconds; proceed only when closed=done and its engine is gone. If it stops or times out, exit with the journal reason, without modifying anything else or resuming it. Plan 004 must complete its commit/push before this plan touches the shared tree. Other sessions' runs are outside scope.

Read first: pipeline/dispatch.py (parse_transcript, run_dispatch), pipeline/journal.py (derive_state), pipeline/bench.py, pipeline/registry.py, pipeline/engine.py (_run_review, _dispatch), tests/run.py, tests/ratchet.json, roles/CONTRACT.md, roles/registry.json, and plans/001-ci-and-bench/plan.md for context only. Read targeted related code as needed; do not ingest raw transcripts into the model. Aggregate them using a script.

Write only plans/005-token-efficiency/ in this phase:
- check.py: deterministic acceptance CLI with baseline, implementation, and comparison subcommands. Return nonzero for missing evidence or failed checks; distinguish inconclusive measurements from demonstrated savings. It must be callable from every EXIT here without shell quoting tricks.
- reference.py and fixtures/: reproducible baseline/after runner. Freeze fixture hashes, task text, output assertions, role/model/effort/config fingerprints, and checks before the baseline. Use fresh isolated workspaces under /root/.system/projects/token-efficiency-reference/, not the live repo. No credentials, real deployment, push, or actual historic plan replay in fixtures.
- baseline.json and BASELINE.md: evidence and limitations with regeneration commands. Include a frozen source-tree revision/diff fingerprint and before-prompt contents needed for a fair comparison, excluding secrets.

Measure with two child plans per condition, launched through /root/pipeline/bin/run: (1) one small implementer repair with deterministic functional tests and REVIEW: cross (one worker plus two engine-dispatched reviewers); (2) one researcher task extracting specified facts from synthetic journal/plan fixtures (one worker), checked by an answer key. Each child has ATTEMPTS: 1 and bounded timeout. Do not dispatch reviewers by hand. The same fixture and task wording must work before and after; allow the researcher either raw fixture access or the new summarizer without changing requested facts. Keep fixture size modest, with a few facts deliberately deep in the input. Never label this a 1M-context benchmark.

The runner must record launched child IDs before waiting, checkpoint progress, detect intentional stops/dead processes from journal plus process checks, and not repeat successful calls on retry. Use current registered non-premium models and effort, frozen across conditions. Exact same checks and cross-review requirement in both conditions. Existing review seal and fallback family rules remain in force.

Extract aggregate input/output/reasoning tokens, provider total, cache read/write when exposed, dollars, wall, tool calls, artifact/test outcomes and reviewer findings by script. Cached/replayed input is not new context length and is not proof of wasted reading. Unknown usage or cache accounting remains unknown, not zero. Source own-run totals via exact registry conversation/run IDs, never a plan basename heuristic. Historical journals may provide comparison context but cannot replace the matched baseline or justify claims about other sessions' costs.

No production changes in phase 1. Preserve the baseline and its accepted functional/review assertions for phases 2 and 3.

## Phase 2: efficient evidence and bounded dispatches (implementer)
TIMEOUT: 2400
ATTEMPTS: 2
REVIEW: cross
EXIT: python3 tests/run.py
EXIT: bin/pipeline check-agents
EXIT: python3 plans/005-token-efficiency/check.py implementation

Read first: phase-1 BASELINE.md and baseline.json summaries; pipeline/dispatch.py, engine.py, journal.py, bench.py, status.py, sentry.py, cli.py, roles.py; roles/CONTRACT.md, roles/interactive.md, roles/reviewer-a.md, roles/reviewer-b.md, roles/reviewer-c.md, roles/registry.json; tests/run.py and targeted tests in tests/test_suite.py. Follow named source references when correctness requires it.

Implement the smallest changes that address these measured mechanisms:
1. Shared, streaming usage accounting: preserve provider totals and input/output/reasoning/cache fields without double-counting reasoning or cache. Count every attempt/fallback exactly once, including failed and budget-exhausted work, and keep compatibility with old rows. Distinguish cumulative dispatch usage from context-window size and measured dollars from inferred prices. Record partial/missing telemetry explicitly. Extend bench to show input/output/cache splits, role/run/phase totals, tokens and cost per verified worker-produced outcome, and retry/fallback overhead. Gate completions are not landed worker outcomes. Add exact --mine/--conv/--run scoping without silently attributing another session's runs.
2. pipeline summarize-run <id>: deterministic bounded default digest (target <=30 lines) from the journal, with current process/transcript liveness, EXIT failures, totals, and artifact paths. Full evidence stays on disk with exact file/line references. A truncated summary says what was omitted; no credentials or raw HTTP headers. No AI summarization of JSONL. Keep status and summarize-run read-only.
3. Scoped cross-review: persist a phase/attempt baseline before work begins, then prepare a read-only evidence packet containing changed-file manifest, a bounded diff, original intent, and EXIT results. Include staged, unstaged, new, deleted and committed work; do not accidentally review an empty HEAD diff after a commit. Keep complete diff/artifact paths available, explicitly flag truncation, and let reviewers follow relevant context. Account for retries/resume and pre-existing unrelated modifications rather than silently attributing them to the phase. Persist packet paths in the journal; no unlogged LLM dispatches. Both reviewers must still run on different families after successful EXIT, with the existing blocking rule.
4. Prompt and output discipline: use concise task-scoped evidence once; avoid duplicated contract/preamble or repeated unchanged readbacks. Update CONTRACT.md and the interactive/reviewer prompts with targeted initial reads, summaries for journals, references for evidence, and concise final deliverables. This is not a ban on necessary whole-file reads, re-reading changed files, or large-context tasks. Provide optional compact suite output (success summary, complete failure details and saved full log) that runs the same tests, preserves failure exit status and still enforces the ratchet. Never replace test status with a tail/grep pipeline or reduce coverage to lower tokens. Avoid needless repeat verification only when the exact input tree and command are unchanged; engine EXIT and existing hooks remain authoritative.
5. Per-role non-interactive token_budget: calibrate finite limits from the baseline and record rationale (initial estimates: fast/lane 40000, implementer/frontend 250000, reviewer 120000; adjust estimates before enabling if baseline evidence requires it). Do not impose a hidden budget on the operator's manual interactive session. Enforce incrementally at reported step usage, persist usage and a dedicated budget_exceeded outcome, terminate the correct worker process group, and deliberately stop without a fallback/retry/relight loop that resets the budget. Explain usage-reporting granularity and possible one-step overshoot; never claim an exact provider billing cutoff. Treat budget exhaustion as a policy stop, not a model or host failure. No automatic cap increases to get work through. Policy field additions do not authorize any model/effort/grant changes.

Allowed production edits: the pipeline modules listed above, roles/CONTRACT.md, roles/interactive.md, roles/reviewer-a.md, roles/reviewer-b.md, roles/reviewer-c.md, roles/registry.json for token budgets only, tests/run.py, tests/test_suite.py, tests/fake-devpass-code, tests/ratchet.json, and new focused pipeline/tests modules if needed. Generated agents must be regenerated through bin/pipeline render-agents, not hand-edited. Preserve standard-tier primary/fallback/quota_fallback policy and existing model/effort seats.

Add characterization tests proving usage accounting for multiple steps, cache and legacy rows, failed/fallback accounting, bounded secret-safe summaries and exact conversation scoping, diff packets including new/committed changes and resume, retained reviewer context and distinct-family review, budget process termination/no automatic retry or sentry restart, compact suite failure propagation and unchanged required-test set. Update check.py implementation to exercise these interfaces and reject weakened baseline assertions.

Run the full suite with python3 tests/run.py; store its full log and use compact summaries only for communication. Update docs/TOKEN-EFFICIENCY.md and phase-local IMPLEMENTATION.md with interfaces, budget semantics and evidence. Do not claim savings yet. No commit or push.

## Phase 3: matched verification and measured report (implementer)
TIMEOUT: 1800
ATTEMPTS: 1
EXIT: python3 tests/run.py
EXIT: bin/pipeline check-agents
EXIT: python3 plans/005-token-efficiency/check.py comparison
EXIT: test -s plans/005-token-efficiency/RESULTS.md && test -s plans/005-token-efficiency/comparison.json

Read phase-local reference.py, check.py, baseline.json, IMPLEMENTATION.md and docs/TOKEN-EFFICIENCY.md. Use the frozen reference runner to launch only the four remaining after-condition worker dispatches through quick/run with journal IDs recorded. Do not rerun baseline, replay plan 001's commit/push, or run model-selection trials. Frozen model, effort, fixture facts, expected outputs and checks must match; changes to those invalidate rather than improve the comparison. Include warm/cold cache differences, fallback identities, host stalls and sample size as limitations.

Write comparison.json and RESULTS.md with per-workload and aggregate before/after input/output/cache/total tokens, dollars, wall, retries, deterministic correctness and cross-review results. Count all attempts in tokens per verified outcome; a killed/truncated/incorrect task is not an efficiency win. Require preserved correctness and review obligations. Report lower tokens only where measured, and flag runtime/cost regressions rather than claiming no regression from a single sample. If evidence does not establish the intended savings without a material regression, set the result to inconclusive or regressed, with check.py comparison returning nonzero so the run asks for judgment instead of starting trials.

Regenerate scoped public-safe benchmark output from the journal with exact regeneration commands and distinguish historical totals from this experiment. Keep raw artifacts outside the public repo and do not expose unrelated conversations or credentials. Write HANDOFF.md with changed files, acceptance results, child run IDs, remaining limitations and readiness for the later model trials. No model/effort changes, commits, pushes, new background daemons, or additional trial launches.
