# Plan 009: storage GC for the estate (buffer, verify, sweep)
WORKDIR: /root/pipeline

DECISION gc-policy: runs and quicks are kept whole until they are BOTH closed AND older
than a buffer (default 72h). The journal is the authority: a run is eligible only if its
journal's last row is run.close (done or stopped) and its closed_at is older than the
buffer. Runs referenced by the runs registry as the CURRENT conversation's lineage are
never swept. Gate-waiting / running / dead-engine runs are never swept (dead engines may
be relit; their evidence must survive). Nothing is deleted until a VERIFY step proves the
run's journal parses, ends with run.close, and is past the buffer; then and only then are
the non-journal artifacts (transcripts, briefs, result.json, review dirs) removed. The
journal file itself is KEPT (compact: our 38MB of runs is 740K of journals vs 32MB of
artifacts — journals are the source of truth and cheap; artifacts are the bulk).
DECISION gc-targets: (1) estate run/quick artifact dirs as above; (2) devpass-code
opencode.db is NOT ours to delete: the tool-output/ dir (37M) and log/ (13M) get the
same buffer-based sweep of files older than 30d; the DB itself (493M) is the
conversation tab's data source and stays untouched by us — its retention is the
operator's call via devpass-code's own session management, which we document but do not
automate. (3) plan workspaces under ~/.system/projects stay (they are operator work).
DECISION gc-shape: a `pipeline gc` command with --dry-run (default: report only),
--sweep (delete), --buffer-hours N (default 72). A daily systemd timer re-runs
`pipeline gc --report` and logs findings; sweeping happens ONLY from the timer with an
explicit --sweep flag configured by the operator in bootstrap (default timer does NOT
sweep; it reports. The operator opts into auto-sweep by setting PIPELINE_GC_SWEEP=1 in
the timer unit). Storm-armor every filesystem op. GC NEVER deletes: journals, the runs
registry, estate layout, STOPPED receipts (they are evidence), or anything under
plans/ in the repo.
DECISION cloud-tier: operator approved cloud storage integration; Google Drive is the
primary free tier (15GB), rclone v1.75.1 is the bridge (already installed at
~/.system/bin/rclone, verified working). Hetzner Object Storage is an explicit NOT-NOW
fallback. The tier extends GC, it does not replace it: eligible artifacts become
export -> upload -> VERIFY CHECKSUM -> local delete, with the cloud URL recorded in the
journal-adjacent manifest so lineage stays answerable. rclone crypt layer decision is
deferred to the operator at OAuth time (recommended on: it is a personal Drive).
DECISION existing-art: evaluated honestly — generic retention tools (tmpreaper,
systemd-tmpfiles) don't understand journal closure semantics or conversation lineage;
a bespoke reader is the right system here. tmpfiles-style age-only deletion would
delete a gate-waiting run's evidence. The journal-aware verifier is the difference
between a GC and a data-loss bug.

## Phase 1: the GC command (implementer)
TIMEOUT: 1500
ATTEMPTS: 1
EXIT: python3 tests/run.py
EXIT: ~/.system/bin/pipeline gc --help | grep -q dry-run
EXIT: python3 -c "from pipeline.gc import eligibility, ELIGIBLE_CLOSED, KEEP_RUNNING, KEEP_RECENT; s=eligibility({'closed':'done','closed_at':1.0}, now=1e12, buffer_s=100.0); assert s==ELIGIBLE_CLOSED"
EXIT: python3 -c "from pipeline.gc import eligibility, KEEP_RUNNING; assert eligibility({'closed':None}, now=1e12, buffer_s=100.0)==KEEP_RUNNING"
EXIT: git log -1 --format=%s | grep -qxF 'gc: journal-aware estate sweeper with 72h buffer'

pipeline/gc.py: eligibility(run_state, now, buffer_s) -> status enum (ELIGIBLE_CLOSED,
KEEP_RUNNING, KEEP_RECENT) — pure function; plan(estate, buffer_s) -> (sweep_list,
keep_list, saved_bytes_estimate, reasons) — walks runs_dir() and quick_dir(), folds
each journal via pipeline.journal.Journal.state(), checks the last row is run.close,
closed_at older than buffer, engine pid not alive (dead engines may relight: keep),
not in the current conversation lineage from pipeline.registry; NEVER touches journals,
receipts, registry. sweep(plan_items) deletes artifact files (everything in the run dir
except journal.jsonl, STOPPED, engine.lock) with per-file storm armor and a manifest of
what was deleted, written to <estate>/logs/gc-manifest-<ts>.jsonl BEFORE deletion
(recoverable record). CLI: `pipeline gc [--dry-run|--sweep] [--buffer-hours N]
[--quick-only]`, default dry-run prints a table (run, closed_at, age, eligible, bytes).
Exit 0 always unless a probe of the estate fails (then 2). Tests: eligibility matrix
(running kept, recent kept, closed+old eligible, dead-engine-eligible-but-kept-if-lineage),
plan on a fixture estate via tests/harness.Estate with fake old/new/running runs,
sweep deletes artifacts but keeps journal+STOPPED, manifest written before deletion,
dry-run deletes nothing. Register in suite; run ONCE. Stage pipeline/gc.py, pipeline/cli.py,
tests. Commit exactly: `git commit -m 'gc: journal-aware estate sweeper with 72h buffer'`.

## Phase 2: daily timer + bootstrap wiring (fast-worker)
TIMEOUT: 900
ATTEMPTS: 1
EXIT: bash -n systemd/pipeline-gc.service systemd/pipeline-gc.timer && systemctl --user enable pipeline-gc.timer 2>/dev/null || test -f systemd/pipeline-gc.timer
EXIT: python3 tests/run.py
EXIT: git log -1 --format=%s | grep -qxF 'gc: daily report timer, operator-opted sweep'

systemd/pipeline-gc.service (Type=oneshot, ExecStart=%h/.system/bin/pipeline gc --dry-run,
Environment=PIPELINE_GC_SWEEP off by default; a commented ExecStart line documents the
operator opt-in: replace --dry-run with --sweep to auto-delete) and pipeline-gc.timer
(OnCalendar=daily, Persistent=true). bootstrap.sh: install both units when systemd --user
exists alongside the sentry unit; print one line telling the operator how to opt into
auto-sweep. Also add a gc section to docs/READINESS.md's honest limits. Tests: unit files
parse (bash -n where applicable), bootstrap idempotence unaffected. Commit exactly:
`git commit -m 'gc: daily report timer, operator-opted sweep'`.

## Phase 3: sweep the backlog and record it (fast-worker)
TIMEOUT: 600
ATTEMPTS: 1
EXIT: ~/.system/bin/pipeline gc --dry-run | grep -q "eligible"
EXIT: ~/.system/bin/pipeline gc --sweep --buffer-hours 72 && echo swept
EXIT: test -s plans/009-gc/BACKLOG.md && grep -q "0 eligible" plans/009-gc/BACKLOG.md
EXIT: git log -1 --format=%s | grep -qxF 'gc: backlog swept, manifest recorded'

Run `pipeline gc --dry-run` first and save its table to plans/009-gc/BACKLOG.md (with
bytes saved estimate); then `pipeline gc --sweep --buffer-hours 72`. The current
conversation's lineage is auto-kept; the TUI and Roblox runs are recent so they stay.
Report freed bytes in BACKLOG.md honestly (du before/after). Do NOT sweep opencode.db
or tool-output (that is phase 4's decision, operator's call). Commit exactly:
`git commit -m 'gc: backlog swept, manifest recorded'` with plans/009-gc/BACKLOG.md only.

## Phase 4: devpass-code side, documented not automated (researcher)
TIMEOUT: 600
ATTEMPTS: 1
SURFACE: docs/STORAGE.md operator-doc
EXIT: test -s docs/STORAGE.md && grep -q "opencode.db" docs/STORAGE.md
EXIT: grep -q "tool-output" docs/STORAGE.md && grep -q "retention" docs/STORAGE.md
EXIT: git log -1 --format=%s | grep -qxF 'gc: storage policy documented'

docs/STORAGE.md: where bytes actually are (measured: estate runs 38M of which 740K
journals / 32M artifacts; opencode.db 493M — the conversation source; tool-output 37M;
log 13M), the GC policy and buffer, what the pipeline will and will not delete (never:
journals, registry, receipts, the DB), and the operator's own knobs for the DB
(devpass-code session management; we do not auto-delete the TUI's data source). Honest
statement: journals are kept forever because they are the system of record and cost
~20KB/run. Register doc in suite check if trivial. Commit exactly:
`git commit -m 'gc: storage policy documented'`.

## Phase 5: cloud tier — GC export-to-Drive (implementer)
TIMEOUT: 1500
ATTEMPTS: 1
EXIT: python3 tests/run.py
EXIT: python3 -c "from pipeline.cloudtier import export_plan, verify_upload; assert callable(export_plan) and callable(verify_upload)"
EXIT: git log -1 --format=%s | grep -qxF 'gc: cloud tier (rclone export-verify-delete)'

pipeline/cloudtier.py: export_plan(gc_items, remote) builds the cold bundle per eligible
run: tar the artifact files (NOT journal.jsonl/STOPPED) to <estate>/cold/<run>.tar.gz,
then rclone copy to remote:pipeline-cold/<run>.tar.gz, then verify_upload compares local
sha256 vs `rclone sha1sum`-equivalent (rclone hashsum sha256) of the remote object —
byte-identical proof, not upload-success-only. Only after verify passes does it call
gc.sweep() on that run's artifacts, then DELETES the local tar (the remote copy is the
archive). The manifest row (written before local deletion, per plan discipline) records
run id, tar sha256, remote URL, artifact byte count. CLI: `pipeline gc --cloud <remote>`
composes: plan -> export_plan -> sweep. If rclone or the remote is unreachable: exit 2
with a clear message, delete NOTHING, keep every local byte. Tests with a fake rclone
shim (PATH override, like the fake worker binary): export-verify-delete happy path,
checksum mismatch -> local files kept, remote unreachable -> nothing deleted, tar
excludes journal/STOPPED, manifest row written before deletion. Register in suite, run
ONCE. Stage pipeline/cloudtier.py pipeline/cli.py tests. Commit exactly:
`git commit -m 'gc: cloud tier (rclone export-verify-delete)'`.

## Phase 6: wire the remote + operator OAuth handoff (fast-worker)
TIMEOUT: 600
ATTEMPTS: 1
EXIT: test -f ~/.config/rclone/rclone.conf || test -f plans/009-gc/OAUTH-PENDING.md
EXIT: python3 tests/run.py
EXIT: git log -1 --format=%s | grep -qxF 'gc: drive remote wired into timer + docs'

Configure nothing destructive: write docs/CLOUD-TIER.md with the exact operator OAuth
steps (rclone config create pipeline-drive drive --headless=false, run it on the
MacBook or any browser machine, paste the token back; then `pipeline gc --cloud
pipeline-drive --dry-run` to see the plan). Update the gc systemd timer's documented
opt-in line to the cloud variant. If the operator has NOT completed OAuth by the time
this phase runs, write plans/009-gc/OAUTH-PENDING.md explaining the one-time step
(honest state, not a fake success). Tests: suite ONCE. Commit exactly:
`git commit -m 'gc: drive remote wired into timer + docs'`.

## Phase 7: record honest empty-backlog truth (fast-worker)
AFTER: 6
TIMEOUT: 300
ATTEMPTS: 1
EXIT: test -s plans/009-gc/BACKLOG.md && grep -q "nothing eligible" plans/009-gc/BACKLOG.md
EXIT: git log -1 --format=%s | grep -qxF 'gc: backlog report (nothing eligible under 72h buffer)'

Phase 3 burned because the whole estate is younger than the 72h buffer: the sweeper
correctly refused to touch anything, and the manifest was correctly absent. Rewrite
plans/009-gc/BACKLOG.md to say exactly that: run `pipeline gc --dry-run`, include its
counts (runs kept-recent vs eligible), state "nothing eligible under 72h buffer —
first real sweep will occur once runs age past the buffer", and note the Drive remote
is configured (phase 5-6 landed). No deletions. Commit exactly:
`git commit -m 'gc: backlog report (nothing eligible under 72h buffer)'` with
plans/009-gc/BACKLOG.md only.
