# Plan 013: ITERATE phase directive (Ralph-loop as a first-class primitive)
WORKDIR: /root/pipeline

DECISION iterate-grammar: phases opt in with `ITERATE: on` + required `CEILING:` (positive
int, hard iteration cap) + at least one `EXIT:`. Optional `PROGRESS: <shell predicate>`
(exit 0 = advanced). `ITERATE: <anything-but-on>` and `ITERATE` + `LANES` in one phase are
validation errors. Non-ITERATE phases behave exactly as before (acceptance: the full
existing suite must pass unchanged).
DECISION iterate-semantics: per iteration: (1) dispatch a FRESH session with the
UNCHANGED phase body - build_brief with previous_failure=None every time; the engine
never appends prior prompt or failure text. (2) run all EXITs. (3) all pass -> phase
done (REVIEW/SURFACE run once, only here). (4) else run PROGRESS predicate (cwd = plan
workdir) or the built-in signal. (5) progress -> reset consecutive-no-progress to 0;
else increment. (6) 2 consecutive no-progress -> deliberate stop ITERATE-STALLED
(stop reason string). (7) iteration count reaches CEILING with EXIT failing ->
deliberate stop ITERATE-CEILING. Stall wins ties (check stall first). (8) next iteration.
Attempt-budget interplay: ITERATE phases ignore ATTEMPTS (progressing iterations consume
no retry budget; phase.fail rows are NOT written for merely-failing EXITs in iterate mode
- the iteration journal row carries the state instead).
DECISION built-in-signal: when PROGRESS omitted: (a) count of unchecked `- [ ]` boxes in
a phase-local progress file (the brief tells workers to maintain it); if no such file
exists or has no boxes, (b) project-state fingerprint: deterministic hash of the git
tracked-file state (git status --porcelain + git diff of tracked files; falls back to a
mtime+size walk of the workdir excluding .pipeline/, engine output dirs, __pycache__,
*.log, brief/transcript/result files); persist prev-fingerprint in
<pipeline/phase-N/> artifact, updated only on detected progress, tolerant of first
invocation (first call = progress by definition, persist and exit 0).
DECISION durable-state: the engine writes each failing EXIT's captured output to
.pipeline/phase-<N>/exit-<i>.log inside the phase artifact dir under the estate run dir
(engine-side artifact, not the repo: use <rdir>/phase-<N>/iterate/exit-<iter>-<i>.log);
briefs direct workers to maintain a progress file in the repo. COST-CEILING is RESERVED:
do not implement; grammar parser must REJECT it with a clear "reserved for future" error.
DECISION journal: one `iterate.end` row per iteration: phase, iteration, exit_ok (all
EXITs pass?), progress (true/false/null=first), stall_count, wall_s, tokens, cost,
cumulative_tokens, cumulative_cost; cost unavailable recorded as null, never estimated.
status.py: iterative phase renders "iter N/CEILING stall=k $cum" and the waiting-list
banner for ceiling/stall stops names the operator action.

## Phase 1: grammar + validation (implementer)
TIMEOUT: 1200
EXIT: python3 plans/013-iterate/check_iterate_grammar.py
EXIT: python3 tests/run.py
try:
 p.parse_text('## Phase 1: x (implementer)\nITERATE: yes\nCEILING: 12\nEXIT: true\n')
except p.PlanError: ok=True
assert ok, 'ITERATE: yes must fail'"
try:
 p.parse_text('## Phase 1: x (implementer)\nITERATE: on\nCEILING: 12\nLANES: items\nEXIT: true\n')
except p.PlanError as e: ok='LANES' in str(e)
assert ok, 'ITERATE+LANES must fail'"
try:
 p.parse_text('## Phase 1: x (implementer)\nITERATE: on\nEXIT: true\n')
except p.PlanError as e: ok='CEILING' in str(e)
assert ok"
try:
 p.parse_text('## Phase 1: x (implementer)\nITERATE: on\nCEILING: 2\n')
except p.PlanError as e: ok='EXIT' in str(e)
assert ok"
try:
 p.parse_text('## Phase 1: x (implementer)\nCOST-CEILING: 5\nEXIT: true\n')
except p.PlanError as e: ok='reserved' in str(e).lower()
assert ok"
EXIT: git log -1 --format=%s | grep -qxF 'iterate: grammar, validation, reserved COST-CEILING'

pipeline/plan.py: add "ITERATE", "PROGRESS", "COST-CEILING" handling. ITERATE accepts only
"on" (PlanError otherwise); sets ph.iterate. COST-CEILING is a PlanError "reserved for a
future directive". _resolve: if ph.iterate: CEILING required (already have ceiling field
- reuse it), at least one EXIT required, LANES forbidden (PlanError naming both), PROGRESS
is a raw shell string on ph.iterate_progress. Gates + ITERATE also forbidden. Model the
dataclass fields. cmd_validate prints iterate phases with a flag. Tests: all the EXIT
assertions above plus a clean non-iterate plan unchanged. Run suite ONCE. Stage
pipeline/plan.py pipeline/cli.py tests/. Commit exactly:
`git commit -m 'iterate: grammar, validation, reserved COST-CEILING'`.
## Phase 2: the loop + built-in signal + journal (implementer)
TIMEOUT: 1800
EXIT: python3 tests/run.py
EXIT: python3 -c "import sys;sys.path.insert(0,'.');from pipeline import engine as e;assert callable(e.builtin_progress_signal) or callable(e._builtin_progress)"
EXIT: python3 tests/run.py iterate_ 2>&1 | grep -q ' 0 failed'

pipeline/engine.py _run_phase: if ph.iterate -> _run_iterate(plan, ph) instead of the
attempt loop. _run_iterate implements EXACTLY the 10-step semantics from the spec's
SEMANTICS section (see DECISION iterate-semantics): fresh brief each iteration
(previous_failure=None always), EXITs via existing _run_exits, PROGRESS via existing
run_predicate (cwd=plan.workdir) or builtin signal; stop reasons: "ITERATE-STALLED" /
"ITERATE-CEILING" as DeliberateStop reason=burned detail=... NO wait - use reason strings
in the stop detail; DeliberateStop reason enum gains nothing: pass reason="burned" with
detail prefixed "ITERATE-STALLED:" / "ITERATE-CEILING:" (STOP_REASONS is a fixed tuple;
do not extend it - the detail carries the distinct name, and status.py matches on the
detail prefix for the banner). Write failing EXIT outputs to
<rdir>/phase-<N>/iterate/exit-<iter>-<i>.log. Journal iterate.end rows per DECISION.
_build_builtin_progress_signal: returns a callable(state_path, workdir) -> bool; checkbox
count first (grep unchecked '- [ ]' in .pipeline/phase-N*/progress.md or tasks.md under
the workdir - first run persists count and returns True), else git-state fingerprint
(git status --porcelain + git diff; fallback walk excluding noise: .pipeline/,
__pycache__, *.log, brief.md, transcript.jsonl, result.json), persisted to
<rdir>/phase-<N>/iterate/fingerprint.txt, updated only on progress, first run = True.
Status.py: iterative phase line shows iter N/CEILING, stall count, cumulative cost; the
waiting-on-you entry for an ITERATE-STALLED/ITERATE-CEILING stop renders a distinct
operator-action banner (match the detail prefix). Tests with the fake worker
(FAKE: directives from the brief): a 2-iteration loop that passes (fake touches a file on
iteration 1 satisfying EXIT; iteration count 2, both journaled, REVIEW not run per
iteration), a ceiling stop (fake never satisfies EXIT but always progresses -> hits
CEILING -> deliberate stop with ITERATE-CEILING detail), a stall stop (fake makes no
change -> PROGRESS omitted so built-in signal: git fingerprint unchanged -> two
consecutive -> ITERATE-STALLED), PROGRESS predicate honored (exit 1 -> stall even with
file changes), fresh-brief proof (fake logs briefs; assert iteration 2's brief lacks
iteration 1's content / identical to iteration 1's body), EXIT logs on disk, journal row
fields, REVIEW once only (fake verdict PASS appears once even over 3 iterations),
non-iterate phases untouched (existing suite). Register all in the suite. Run suite ONCE.
Stage pipeline/engine.py pipeline/status.py tests/. Commit exactly:
`git commit -m 'iterate: fresh-session loop, progress signals, journal, banners'`.

## Phase 3: dogfood a real iteration (implementer)
TIMEOUT: 1500
EXIT: python3 tests/run.py
EXIT: git log -1 --format=%s | grep -qxF 'iterate: dogfood - migration loop green in 2 iterations'

Dogfood IN the repo: create tests/fixtures/iterate-demo/ with a tiny "migration": 4
numbered tasks.md items each naming a deterministic check, and a verify script that
passes only when all items done. Append a plan file tests/fixtures/iterate-demo/plan.md
with one ITERATE phase (CEILING: 4, the built-in checkbox signal, EXIT: the verify
script) and run it through engine.launch on the fixture dir in a test (fake-worker style:
the FAKE: directives make it deterministic - iteration 1 completes items 1-2, iteration 2
items 3-4, EXIT passes on iteration 2). Assert: exactly 2 dispatches, 2 iterate.end rows,
phase done, no REVIEW unless declared. This proves the primitive end-to-end. Also add the
directive to pipeline/plan.py's module docstring grammar list and a section to
docs/WRITING-PLANS.md (the discipline: iterative phases need a progress file named in the
brief; PROGRESS predicates own their comparison state; prefer low CEILING). Run suite ONCE.
Stage the fixture, docs, plan.py, tests/. Commit exactly:
`git commit -m 'iterate: dogfood - migration loop green in 2 iterations'`.

## Phase 4: publish (implementer)
TIMEOUT: 300
EXIT: git log -1 --format=%s | grep -qxF 'iterate: Ralph-loop directive published'
EXIT: git fetch -q origin main && git diff --quiet HEAD origin/main

Push all four commits: `git push origin main`. Final lines: commit hash + suite count.
