# Storage Architecture and Garbage Collection Policy

AutonomousPipelines permanently preserves append-only journal streams while purging expired execution artifacts after a 72-hour verification buffer.

---

## 1. Where Bytes Actually Are (Measured Storage Footprint)

A disk audit across the pipeline host measured the following storage distribution:

| Subsystem / Location | Measured Size | Nature of Data | Pipeline GC Scope |
|---|---|---|---|
| **Estate Runs** (`~/.system/runs/`) | **~38 MB** | Run directories containing journals and execution artifacts | Sweeps artifacts; keeps journals |
| ↳ *Journals* (`*/journal.jsonl`) | ~740 KB | Append-only event stream (system of record) | Retains permanently |
| ↳ *Artifacts* (`brief.md`, `transcript.jsonl`, `result.json`, `review/`) | ~32 MB | Worker logs, full prompt transcripts, intermediate verdicts | Sweeps after 72h buffer |
| **Estate Quick Scratches** (`~/.system/quick/`) | ~312 KB | Temporary workspace directories for single-dispatch tasks | Sweeps after 72h buffer |
| **Estate Registry & State** (`~/.system/runs.jsonl`, `~/.system/state/`) | ~64 KB | Conversation lineage mapping and benchmark states | Retains permanently |
| **Operator Workspaces** (`~/.system/projects/`) | ~20 MB | Operator plan working trees and checkout copies | Retains permanently |
| **System Binaries & Tools** (`~/.system/bin/`) | ~144 MB | Toolchain binaries (`rclone`, `rojo`, `selene`, `stylua`, `lune`) | Static infrastructure |
| **Devpass-Code Database** (`~/.local/share/devpass-code/opencode.db`) | **493 MB** (up to ~507 MB) | SQLite conversation store, prompt history, TUI state | Retains permanently (operator custody) |
| **Devpass-Code Tool Outputs** (`~/.local/share/devpass-code/tool-output/`) | **37 MB** | Cached stdout/stderr buffers from worker tool calls | 30-day retention buffer |
| **Devpass-Code Logs** (`~/.local/share/devpass-code/log/`) | **13 MB** | ACP server, session, and daemon stderr/stdout logs | 30-day retention buffer |
| **Devpass-Code Snapshots** (`~/.local/share/devpass-code/snapshot/`) | ~3.7 MB | Workspace shadow snapshots | Managed by devpass-code |

### Honest Statement on Journals

Journals remain forever because they represent the system of record and cost ~20 KB per run. Across 40 to 60 historical runs, journals total only ~740 KB. Even after 1,000 completed pipeline executions, the journal stream consumes less than 25 MB. Compact JSONL append-only logs preserve lineage, execution facts, token accounting, and replay integrity indefinitely. In contrast, non-journal artifacts account for more than 85% of estate run volume (~32 MB out of ~38 MB).

---

## 2. Pipeline GC Policy and Buffer

The garbage collector (`pipeline/gc.py`, invoked via `pipeline gc`) sweeps expired run artifacts while defending data integrity and lineage.

### The Journal is the Authority

A run or quick dispatch qualifies for sweeping only when it meets two strict conditions:
1. **Closed Status**: The journal parses completely and its final event row records `run.close` with outcome `done` or `stopped`.
2. **Buffer Expiration**: The closed timestamp (`closed_at`) is older than the configured buffer (default: 72 hours).

### Eligibility Rules

- **ELIGIBLE_CLOSED**: The journal terminates in `run.close`, `(now - closed_at) >= buffer_s`, and the run sits outside active conversation lineage. The sweeper purges these artifacts.
- **KEEP_RUNNING**: The journal lacks `run.close`, records `closed_at: null`, or still runs. The engine retains active runs.
- **KEEP_RECENT**: The journal closed recently with `(now - closed_at) < buffer_s`. The engine retains these runs for operator inspection.
- **Current Lineage Protected**: Runs recorded in `~/.system/runs.jsonl` matching the active conversation session remain safe regardless of age.
- **Gate-Waiting and Dead Engines Protected**: Runs waiting on sentinels or dead engines lack a clean `run.close`. The engine protects their diagnostic evidence for sentry recovery.
- **Torn Tail Protection**: Any journal failing validation or showing unexpected trailing events post-close remains intact.

### What the Pipeline Will Delete

When `pipeline gc --sweep` executes against eligible runs, it removes non-journal artifacts:
- The sweeper deletes `transcript.jsonl` containing full chat transcripts.
- The sweeper deletes `brief.md` containing rendered phase instructions.
- The sweeper deletes `result.json` containing per-phase exit records.
- The sweeper deletes review directories under `review/` and `review-two/`.
- The sweeper deletes worker output dumps and temporary diff files.
- The sweeper deletes quick run companion scratch directories in `<estate>/quick/<run_id>/`.

### What the Pipeline NEVER Deletes

The sweeper preserves critical records and contains no code paths that remove:
- Preserved journal files at `<estate>/runs/<run_id>/journal.jsonl`.
- Unconditional `STOPPED` receipts documenting deliberate stops.
- Active lock files named `engine.lock`.
- Lineage registry records in `~/.system/runs.jsonl`.
- Estate directory trees including `runs/`, `quick/`, `state/`, and `logs/`.
- Repository tree files and plan specifications under `plans/`.
- Operator working trees located in `~/.system/projects/`.
- Database files including `opencode.db` and SQLite structures.

### Pre-Sweep Manifest and Storm Armor

Before unlinking any artifact, `sweep()` writes a complete manifest of every file scheduled for deletion to `<estate>/logs/gc-manifest-<timestamp>.jsonl`. File removals run inside `with_storm_armor` to absorb transient filesystem stalls such as EIO, ENOSPC, or EAGAIN without aborting the sweep.

---

## 3. Comparison with Existing Art

Generic file-retention tools such as `tmpreaper` and `systemd-tmpfiles` inspect file timestamps (`mtime` and `atime`).

- **The Data-Loss Trap**: An age-only sweeper deletes artifacts from long-running gate-waiting plans, dead-engine runs awaiting sentry relight, or active conversation lineages because their brief files show older timestamps.
- **The Bespoke Solution**: The `pipeline gc` command reads journal state, closure invariants, and conversation lineage. Journal-aware verification prevents catastrophic data loss.

---

## 4. devpass-code Side: opencode.db Retention and Operator Knobs

The database at `~/.local/share/devpass-code/opencode.db` (493 MB) provides the single source of truth for the conversation tab, prompt history, agent configuration, and session replays.

### Retention Boundary: Documented, Not Automated

1. **Pipeline Hands Off `opencode.db`**: The pipeline sweeper never opens, alters, truncates, or deletes `opencode.db`. Deleting the conversation database would destroy the operator's active TUI context and session history.
2. **Ephemeral Directory Retention**: The `tool-output/` directory (37 MB) and `log/` directory (13 MB) store ephemeral execution buffers. Operators can prune files older than 30 days in `tool-output/` and `log/` using standard retention buffers because finished dispatches no longer require them.
3. **Database Maintenance Belongs to the Operator**: Cleaning, pruning, or shrinking `opencode.db` requires devpass-code native session commands.

### Operator Knobs for Session and Database Management

The operator inspects, prunes, and reclaims space from `opencode.db` using standard CLI tools:

- List existing sessions:
  ```bash
  devpass-code session list
  ```
  This command displays session identifiers, titles, and update timestamps.

- Delete specific historical sessions:
  ```bash
  devpass-code session delete <sessionID>
  ```
  This command removes conversation rows, message blocks, and history parts for the target session.

- Verify database integrity:
  ```bash
  devpass-code db path
  devpass-code db "PRAGMA integrity_check;"
  ```
  This command prints the database path and confirms SQLite file integrity.

- Reclaim freed storage pages:
  ```bash
  devpass-code db "VACUUM;"
  ```
  SQLite holds allocated disk pages until the operator runs vacuum.

- Inspect table space allocation:
  ```bash
  devpass-code db "SELECT name, pgsize FROM dbstat ORDER BY pgsize DESC LIMIT 10;"
  ```
  This query displays the top ten database tables by size.

---

## 5. Daily Automation and Systemd Timer

Systemd user services manage scheduled storage reporting:
- The service unit `systemd/pipeline-gc.service` executes `~/.system/bin/pipeline gc --dry-run` to report disk usage without modifying files.
- The timer unit `systemd/pipeline-gc.timer` triggers daily runs via `OnCalendar=daily` with `Persistent=true`.
- Operator Sweep Opt-In: The default service only reports storage. To enable automatic sweeps of runs older than 72 hours, the operator sets `Environment="PIPELINE_GC_SWEEP=1"` and replaces `--dry-run` with `--sweep` in the user unit.

---

## 6. Cloud Archive Tier (Extension)

For long-term retention beyond the local SSD estate, `pipeline/cloudtier.py` extends GC using `rclone` with Google Drive as the primary free tier:
1. **Export**: The pipeline bundles eligible run artifacts while omitting `journal.jsonl` and `STOPPED` receipts.
2. **Upload**: The pipeline copies the cold bundle to `remote:pipeline-cold/<run>.tar.gz`.
3. **Verify Checksum**: The pipeline validates the local SHA-256 hash against the remote checksum from `rclone hashsum sha256`.
4. **Manifest and Local Delete**: The pipeline records the cloud manifest entry and deletes local run artifact files only after checksum verification passes.

### Local cold tier

`/mnt/HC_Volume_106815039/pipeline-cold` is the local cold tier. It is not the estate: the sticky estate remains at `/root/.system`. GC writes each verified archive to this volume first, retains it there for fast retrieval, and also uploads the same archive to Drive as the off-site copy. Use `pipeline cold-get <run-id>` to print the local archive path; if it is absent, the command prints the recorded Drive hint and exits 4.

The volume is a single device and is not backed up itself. Drive remains the redundancy for exactly that reason. If the volume cannot be probed, cloud export stages its tar in `/tmp` and still completes the verified Drive archive rather than failing the export.
