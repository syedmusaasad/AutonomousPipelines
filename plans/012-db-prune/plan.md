# Plan 012: opencode.db export-prune (operator-approved)
WORKDIR: /root/pipeline

DECISION operator-go: the operator approved this plan ("go", 2026-09-06). It reclaims
~400MB from /root/.local/share/devpass-code/opencode.db (493MB) by exporting and then
deleting old, non-lineage sessions. The DB is the TUI conversation tab's data source;
pruning is therefore evidence-first: export -> verify export parses -> delete ->
VACUUM -> report.
DECISION lineage-protection: a session is DELETABLE only if ALL hold: (1) it is NOT
referenced by any row in ~/.system/runs.jsonl (the runs registry - every conversation
that ever launched pipeline work), (2) it is older than 72h (same buffer as the estate
GC), (3) its export file exists and parses as JSON with a non-empty parts/messages
array. Anything else is KEPT. The operator's interactive lineages (this conversation
included) are never touched. devpass-code session delete is the deletion mechanism
(it is the vendor's own supported verb), NOT raw SQL deletes.
DECISION vacuum: SQLite does not return space after DELETE; the plan runs VACUUM
(explicitly, since devpass-code might hold the DB open, we check no devpass-code
process has the file open first - lsof/fuser check - and if it does, VACUUM is skipped
and reported, deletion still counts).

## Phase 1: export and prune the DB (implementer)
TIMEOUT: 1200
ATTEMPTS: 1
EXIT: test -d ~/.system/cold/opencode-sessions && find ~/.system/cold/opencode-sessions -name "*.json" | wc -l | awk '{exit !($1>0)}'
EXIT: python3 -c "import json,glob; fs=glob.glob('/root/.system/cold/opencode-sessions/*.json'); assert all(json.load(open(f)) for f in fs); print(len(fs),'exports parse')"
EXIT: python3 -c "
import sqlite3, json
c = sqlite3.connect('file:/root/.local/share/devpass-code/opencode.db?mode=ro', uri=True)
lineage = set()
for line in open('/root/.system/runs.jsonl'):
    try: lineage.add(json.loads(line).get('conversation'))
    except: pass
sessions = [r[0] for r in c.execute('select id from session')]
remaining = [s for s in sessions if s in lineage or True]
# after prune: assert no lineage session is gone
gone = [s for s in lineage if s and s not in sessions]
assert not gone, gone
print('lineage intact:', len([s for s in lineage if s in sessions]))
"
EXIT: git log -1 --format=%s | grep -qxF 'storage: opencode.db export-prune (lineage-safe, vacuumed)'

Build pipeline/dbprune.py (new module, stdlib only):
1. enumerate sessions from the sqlite DB (read-only URI) with time_created
2. compute the protected set: every conversation id in ~/.system/runs.jsonl
3. for each OTHER session older than 72h: export it first - copy the session row +
   its message/part rows into one JSON file at ~/.system/cold/opencode-sessions/
   <session_id>.json (this is OUR export, complete: session, messages, parts - more
   faithful than devpass-code export which may paginate); then verify the export
   parses and contains >0 parts; only then call `devpass-code session delete <id>`
4. after all deletions: check no devpass-code process has the DB open (ps + fuser);
   if clear, open a writable connection and run VACUUM; report before/after file
   sizes; if not clear, skip VACUUM and report honestly
5. print a table: exported (count, bytes), deleted (count), kept (count + why:
   lineage/recent), db size before/after
Add `pipeline dbprune` CLI verb with --dry-run (report only) and --sweep (default
dry-run; the phase runs --dry-run first, saves the table to
plans/012-db-prune/REPORT.md, then --sweep). Tests in tests/test_suite.py with a
fixture sqlite DB: lineage protection (protected ids never deleted), 72h buffer
enforced, export-verify-before-delete order, skip-delete-if-export-fails, dry-run
deletes nothing. Register in the suite; run the suite ONCE. Run --dry-run, then
--sweep for real. REPORT.md records both tables plus freed bytes. Stage
pipeline/dbprune.py pipeline/cli.py tests/ plans/012-db-prune/REPORT.md. Commit
exactly: `git commit -m 'storage: opencode.db export-prune (lineage-safe, vacuumed)'`.
Do not push (phase 2 does).

## Phase 2: push (fast-worker)
TIMEOUT: 300
ATTEMPTS: 1
EXIT: git log -1 --format=%s | grep -qxF 'storage: opencode.db export-prune published'
EXIT: git fetch -q origin main && git diff --quiet HEAD origin/main

Push the phase-1 commit: `git push origin main`. Final lines: commit hash + REPORT.md
freed-bytes line.
