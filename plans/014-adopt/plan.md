# Plan 014: ADOPT — session handoff as a first-class verb
WORKDIR: /root/pipeline

DECISION adopt-verb: `pipeline adopt <run-dir-or-run-id>` re-keys a run's registry row
to the CALLER's conversation id (resolved identically to launch-time registration:
registry.current_conversation() — one resolution path, no second identity scheme),
appends a receipt journal event, and changes nothing else. Atomic: registry rewrite and
receipt happen together; a failure between them is a bug, tested against.
DECISION adopt-scope: scoping only. No restart/pause/phase/gate/budget mutation — the
run's execution state is byte-identical (acceptance test proves it). Whole-run
granularity. Repeat adoption allowed (A->B, B->C): each appends its own receipt; the
journal reads as a chain of custody top-to-bottom. Refusals, each with a one-line message
and nonzero exit: (1) run not in registry; (2) caller already owns it — an idempotence
guard reported as a no-op refusal, not an error state; (3) ambiguous prefix — list
candidates, change nothing.
DECISION registry-shape: the runs registry is append-only JSONL today; adopt REWRITES
one row's conversation field in place (the registry is an index, not a journal — the
journal receipt is the audit trail; a rewritten index row plus a journal receipt is the
designed shape). The rewrite is atomic via the existing append_jsonl-style storm armor:
read all rows, rewrite the row, write the whole file to a temp then os.replace.
DECISION discipline: A session owns what it launched PLUS what was explicitly adopted.
Pasted output stays context. `status --mine` and TUI scoping show adopted runs
immediately (they read the registry); the TUI's Files/Pipelines rows gain nothing new —
scoping already keys off the registry conversation field, so adoption flows through
without UI changes beyond an optional marker (status shows adoption receipts when
present: "adopted from <short-id>" — sourced from the journal, rendered in the waiting
line or phase meta, minimal).

## Phase 1: the verb (implementer)
TIMEOUT: 900
ATTEMPTS: 1
EXIT: python3 plans/014-adopt/check_adopt.py
EXIT: python3 tests/run.py 2>&1 | grep -q ' 0 failed'
EXIT: git log -1 --format=%s | grep -qxF 'adopt: session handoff verb (re-key, receipt, refusals)'

pipeline/registry.py: add
- resolve_run(prefix) -> (run_id, row) | None | "ambiguous:[ids]" : exact id match first,
  then unique prefix match over all_rows(); multiple prefix hits -> ambiguous list.
- adopt(run_id, new_conversation=None) -> dict result {action: "adopted"|"refused",
  reason?, from?, to?}: resolves caller via new_conversation or current_conversation();
  refusals per DECISION adopt-verb (not-found / already-owned / ambiguous listed by
  caller-side resolution); on adopt: rewrite the row's conversation field atomically
  (temp+replace under storm armor), then append the receipt to the run's journal via
  pipeline.journal.Journal.write("adopted", by=<new>, from=<old>, at handled by the
  journal's ts). Return the result for CLI rendering.
pipeline/cli.py: `adopt <run>` subcommand: prints one line for success ("adopted
<run> from <old-short> -> <new-short>") or the refusal message; exit 0 on adopt, exit 0
on already-owned refusal BUT print "no-op: already yours" (idempotence is not an
error), exit 3 not-found, exit 4 ambiguous (listing candidates), exit 2 usage.
Journal event name: "adopted" (fields by, from).
tests (in tests/test_suite.py, fixture estate via tests/harness.Estate):
- adopt_rekeys_row_and_appends_receipt: register run to conv A, adopt with conv B ->
  registry row says B, journal has adopted receipt with both ids, and a SECOND adoption
  by conv C appends a second receipt (chain reads A->B then B->C top-to-bottom).
- adopt_refusals: missing run (exit 3 + message), ambiguous prefix lists candidates and
  changes nothing, already-owned is a no-op refusal (registry unchanged, no second
  receipt appended).
- adopt_does_not_touch_execution_state: snapshot the run dir (file list + mtimes +
  journal rows before), adopt, snapshot after: only the journal grew by exactly one
  adopted row; every other file byte-identical, phases/gates/attempts state unchanged
  (derive via Journal.state and compare relevant fields).
- status_shows_adopted_run_in_scope: status.all_reports(conv B) includes the adopted
  run; all_runs view unchanged (same set before/after).
Write plans/014-adopt/check_adopt.py: a checker script (my standing rule: no inline
python -c EXITs) exercising the four acceptance groups end-to-end on a fixture estate
and exiting 0/1 with a clear report. Run the suite ONCE. Stage pipeline/registry.py
pipeline/cli.py tests/ plans/014-adopt/check_adopt.py. Commit exactly:
`git commit -m 'adopt: session handoff verb (re-key, receipt, refusals)'`.

## Phase 2: dogfood the handoff + publish (fast-worker)
TIMEOUT: 600
ATTEMPTS: 1
EXIT: test -s plans/014-adopt/DOGFOOD.md
EXIT: git log -1 --format=%s | grep -qxF 'adopt: handoff dogfooded and published'

Dogfood: pick a real stopped run (e.g. run_20260904T060316_431609), adopt it from this
conversation (PIPELINE_CONVERSATION or --conv), capture the one-line output and the
`status --mine` before/after showing the run appearing in scope, into
plans/014-adopt/DOGFOOD.md. Then return it to its original conversation by adopting
back (second receipt = the chain proof, captured in DOGFOOD.md). Push origin main with
BOTH commits; final lines: the two commit hashes + pushed confirmation.
