# Wrap-up: bounded correctness fixes and a supervised-use handoff
WORKDIR: /root/pipeline

DECISION allowance-preservation: the operator reports 73% Astra allowance used and 51% monthly allowance used, and asks to finish while retaining allowance for development. These are operator-reported values, not live telemetry. Stop expanding scope. No new model research, probes, candidate comparisons, feature projects or infrastructure redesign. Two coding dispatches, at most two cross-review dispatches, then one approval gate before publication. No automatic retries or timeout extensions.
DECISION practical-handoff: deliver a verified supervised-use release, not a claim of globally optimal models, frontier-model equivalence, minimum tokens or hardened unattended operation. Existing model/effort settings remain provisional and unchanged; the operator can manually use a premium model outside the pipeline. Model changes still require the trial policy. Do not spend the remaining allowance chasing seat optimization.
DECISION scope-reset: the operator clarified that the deliverable is the original AutonomousPipelines framework adapted to non-premium models selected for task quality, runtime and token use. Accounting research and new evaluation infrastructure are not separate product goals. Finish only correctness fixes already listed here, and include a concrete role-by-role recommendation from existing evidence in the handoff. No additional research dispatches or benchmark campaigns. Do not present existing interim defaults as measured winners or omit the model recommendation behind further infrastructure work.
DECISION preserved-evidence: earlier stopped runs remain stopped. Do not resume them, rerun the baseline or change historical journals. The accounting baseline already exists. Close this work with a truthful status of fixed versus deferred items.

Existing source edits: pipeline/dispatch.py, pipeline/usage.py, tests/harness.py, tests/test_suite.py, tests/ratchet.json, tests/test_review_provenance.py, tests/test_usage_accounting.py. Respect any additional user-owned edits. Raw files such as aa_output.txt, codex.txt, models.txt, test.py, generate_premium.py are outside the release scope; do not delete or publish them. No direct devpass-code launches, child plans, tests against live models, new tool installations or permission changes. Test subprocesses using the established fake-worker harness are allowed. No token/credential files may be inspected or copied.

## Phase 1: close the false-review-acceptance bug (fast-worker)
TIMEOUT: 600
ATTEMPTS: 1
EXIT: python3 -m unittest discover -s tests -p test_review_provenance.py
EXIT: python3 tests/run.py
EXIT: bin/pipeline check-agents

Read only pipeline/engine.py _run_review and related dispatch return interface, tests/harness.py, tests/test_review_provenance.py and narrowly related test_suite.py cases initially. Defect: `if res.outcome != "ok" and verdict is None` still allows a PASS artifact from a timed-out reviewer. A prior test named timeout-with-preexisting-verdict catches an exception but never asserts verdict rejection. Passing that test did not fix the defect.

Make failed/timed-out/killed reviewer dispatches unconditionally ineligible; their verdict is UNAVAILABLE regardless of files left behind. Use a fresh per-attempt reviewer output directory and journal artifact path/hash associated with the successful attempt; preserve old files as evidence, never as a verdict for a new attempt. Preserve successful PASS/CONCERNS/BLOCKING semantics, the two-distinct-family requirement, and the existing both-BLOCKING rule. Ensure test subprocesses scrub inherited real REVIEW_OUT and restore environment afterward. Do not claim this implements an OS sandbox.

Replace vacuous test bodies with stronger assertions, not fewer tests: use mocks or the fake worker to exercise `_run_review` directly without long sleeps; timeout-with-PASS, failed-with-PASS, old-attempt artifact, successful fresh verdict, and nested test environment leaving an external review sentinel unchanged. Do not swallow errors or skip cases. Register coverage in the full suite and retain the ratchet.

Write plans/006-wrap-up/REVIEW-FIX.md (<=30 lines) with confirmed cause, changed paths, actual verification results and limitations. Run each required command once and retain full failure logs; do not repeat a green suite for reassurance. Engine EXIT will verify independently. Allowed edits: pipeline/engine.py review lifecycle only, tests/harness.py, tests/test_review_provenance.py, relevant test_suite.py and ratchet additions, REVIEW-FIX.md. No new features, registry edits, commits or pushes. Stop with saved evidence if blocked; do not launch a recovery of your own.

## Phase 2: finish the five accounting corrections and handoff (fast-worker)
TIMEOUT: 900
ATTEMPTS: 1
REVIEW: cross
EXIT: python3 tests/run.py
EXIT: python3 -m unittest discover -s tests -p test_usage_accounting.py
EXIT: bin/pipeline check-agents
EXIT: python3 plans/005-token-efficiency/accounting-recovery/check.py
EXIT: test -s docs/HANDOFF.md && test -s plans/006-wrap-up/RELEASE.md

Read the five blocking findings at /root/.system/runs/run_20260905T013230_eb40aa/phase-2/review/reviewer-b/review.md, REVIEW-FIX.md, pipeline/dispatch.py parse_transcript, pipeline/usage.py, plans/005-token-efficiency/accounting-recovery/extract.py and check.py. Do not read full transcripts into context or explore unrelated modules.

Fix only those five findings:
1. Stream transcript lines from the file instead of read_text plus splitlines. Keep only the last deliverable and a bounded diagnostic tail; retain legacy parsing/diagnostic/quota behavior.
2. Add explicit usage provenance (schema version, source event type, provider-reported versus legacy-derived fields) alongside completeness and independent token/cache fields. Do not change historical journal values or invent missing telemetry.
3. Extract and retain baseline run.close, EXIT outcomes and each actual reviewer verdict/family/dispatch outcome in accounting data, with precise source run and artifact references. Model/review success requires the original successful dispatch, not merely a file.
4. Fix overhead membership: workload IDs are run_20260905T001926_cbe4f7 and run_20260905T002109_0c0fd0; setup/recovery/coordinator IDs include q_20260905T000921_8f6edf and q_20260905T001550_854481. Label development/failed-attempt overhead separately. Never exclude workload IDs as overhead or hide its cost.
5. Strengthen check.py and registered negative tests to reject missing provenance, bad/missing run.close, failed EXIT, missing/unavailable review, wrong reviewer families and incorrect overhead membership. Use copies/synthetic records, never mutate live journals.

Regenerate accounting data from existing records only; no baseline reruns or model calls. Distinguish wall-clock completion from summed parallel worker time. Preserve the original baseline.json, fixtures and answer checkers. Allowed edits: pipeline/dispatch.py, pipeline/usage.py only where necessary, accounting-recovery/extract.py and check.py, corrected generated accounting data/ACCOUNTING.md, relevant tests and ratchet additions. Record raw full test output outside the public release; run the suite once locally, then rely on engine EXIT.

Write docs/HANDOFF.md and plans/006-wrap-up/RELEASE.md with exact test commands and results, current model/effort lineup labelled provisional, quick-versus-plan decision examples, open stop receipts, and a short safe starting workflow for a small project. Roblox guidance should reference docs/ROBLOX-WORKFLOW.md; no new Roblox tooling/agent. Include the model research as documentary evidence with its limitations, not optimality or benchmark-winner claims.

The handoff must distinguish CURRENT configuration from a RECOMMENDED lineup. Read the existing docs/model-selection/RECOMMENDATION.md and recommendation-evidence.json only; make no new fetches or model calls. Give one recommended primary, cross-family standard-tier fallback and effort per role, with concise evidence/confidence and any missing task-specific evidence. Favor task-adequate quality and runtime/token economy, not model price or name alone; fast-worker remains low effort. Explicitly distinguish recommendations from registry changes, which still require the existing trial path. Where tier/identity or evidence is unresolved, name that blocker rather than invent a winner. Relate recommendations to the original quick/plan/EXIT/journal/review architecture; do not invent additional agents or a new selection framework. Existing reports' unsupported numerical claims must not be upgraded to verified evidence.

Clearly defer, without implementing: complete OS-level reviewer/egress isolation; fail-closed eligibility for unknown models and MODEL overrides; trial blinding/preamble leak; sticky-bootstrap authorization and nested conversation attribution; dispatch token caps, scoped evidence packets and measured efficiency savings. These limit suitability to supervised use. Missing fixes must not be described as resolved. Correct docs/READINESS.md to this evidence-backed status and remove suggestions to disable accounting or weaken acceptance to claim readiness. Do not expand into those deferred changes.

Write an explicit tested release-file manifest in RELEASE.md covering intended pre-existing changes plus this plan's changes. No raw tool dumps, credentials, local absolute-secret-bearing configs, benchmark workspaces or unrelated untracked files. Both reviewers should inspect only the listed files, five finding resolutions and new negative tests, expanding context if needed. A timed-out/missing review is failure, not approval. No commit/push here.

## Phase 3: approve the supervised release (gate)
GATE: /root/pipeline/plans/006-wrap-up/.approve-release

Publication approval is separate from finishing code. Operator reviews docs/HANDOFF.md and both final review artifacts before writing yes. Do not write this sentinel from a worker.

## Phase 4: publish exactly the verified release (fast-worker)
TIMEOUT: 300
ATTEMPTS: 1
EXIT: git log -1 --format=%s | grep -qxF 'fix: verify review outcomes and preserve accounting evidence'
EXIT: git diff --quiet HEAD -- pipeline/engine.py pipeline/dispatch.py pipeline/usage.py tests/harness.py tests/test_suite.py tests/ratchet.json tests/test_review_provenance.py tests/test_usage_accounting.py docs/HANDOFF.md docs/READINESS.md
EXIT: test "$(git rev-parse HEAD)" = "$(git ls-remote origin refs/heads/main | cut -f1)"

After the approval gate only, inspect git status, git diff and git log --oneline -10, then stage only the RELEASE.md manifest of files verified in this run. Exclude aa_output.txt, codex.txt, models.txt, test.py, generate_premium.py, raw logs, secrets and gate sentinels. Stop on unexpected changes or uncertain secret content; do not remove unrelated files. Use existing authentication without printing credential files.

Commit subject exactly: `fix: verify review outcomes and preserve accounting evidence`. The pre-commit hook and python3 tests/run.py entrypoint remain mandatory; no bypass flags, amendments or forced updates. Push only `git push origin main` to the existing syedmusaasad/AutonomousPipelines remote. Reject an unexpected remote URL. Final deliverable: commit hash, suite result and https://github.com/syedmusaasad/AutonomousPipelines. No extra work after publication.
