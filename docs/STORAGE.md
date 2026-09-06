# Storage Architecture and Garbage Collection Policy

This document defines the storage footprint, retention guarantees, garbage collection (GC) policies, and operator maintenance interfaces for AutonomousPipelines and the host environment.

---

## 1. Where Bytes Actually Are (Measured Storage Footprint)

A disk audit across the pipeline host reveals the following distribution of storage consumption:

| Subsystem / Location | Measured Size | Nature of Data | Pipeline GC Scope |
|---|---|---|---|
| **Estate Runs** (`~/.system/runs/`) | **~38 MB** | Run directories containing journals and execution artifacts | **Artifacts swept; journals kept** |
| ↳ *Journals* (`*/journal.jsonl`) | ~740 KB | Append-only event stream (system of record) | **NEVER deleted** (permanent) |
| ↳ *Artifacts* (`brief.md`, `transcript.jsonl`, `result.json`, `review/`) | ~32 MB | Worker logs, full prompt transcripts, intermediate verdicts | **Swept after 72h buffer** |
| **Estate Quick Scratches** (`~/.system/quick/`) | ~312 KB | Temporary workspace directories for single-dispatch tasks | **Swept after 72h buffer** |
| **Estate Registry & State** (`~/.system/runs.jsonl`, `~/.system/state/`) | ~64 KB | Conversation lineage mapping and benchmark states | **NEVER deleted** |
| **Operator Workspaces** (`~/.system/projects/`) | ~20 MB | Operator plan working trees and checkout copies | **NEVER deleted** (operator custody) |
| **System Binaries & Tools** (`~/.system/bin/`) | ~144 MB | Toolchain binaries (`rclone`, `rojo`, `selene`, `stylua`, `lune`) | Static infrastructure |
| **Devpass-Code Database** (`~/.local/share/devpass-code/opencode.db`) | **493 MB** (up to ~507 MB) | SQLite conversation store, prompt history, TUI state | **NEVER deleted** (operator's source) |
| **Devpass-Code Tool Outputs** (`~/.local/share/devpass-code/tool-output/`) | **37 MB** | Cached stdout/stderr buffers from worker tool calls | Documented 30-day retention |
| **Devpass-Code Logs** (`~/.local/share/devpass-code/log/`) | **13 MB** | ACP server, session, and daemon stderr/stdout logs | Documented 30-day retention |
| **Devpass-Code Snapshots** (`~/.local/share/devpass-code/snapshot/`) | ~3.7 MB | Workspace shadow snapshots | Managed by devpass-code |

### Honest Statement on Journals
Journals are kept forever because they are the system of record and cost ~20 KB/run. Across 40 to 60 historical runs, journals total only ~740 KB. Even after 1,000 completed pipeline executions, the journal stream will consume less than 25 MB. Compact JSONL append-only logs preserve lineage, execution facts, token accounting, and replay integrity indefinitely without creating storage pressure. In contrast, non-journal artifacts account for >85% of estate run volume (~32 MB out of ~38 MB).

---

## 2. Pipeline GC Policy and Buffer

The garbage collector (`pipeline/gc.py`, invoked via `pipeline gc`) sweeps expired run artifacts while strictly defending integrity and lineage.

### The Journal is the Authority
A run or quick dispatch is eligible for sweeping **only when both criteria are met**:
1. **Closed Status**: The journal parses completely and its final event row is `run.close` (with outcome `done` or `stopped`).
2. **Buffer Expiration**: The closed timestamp (`closed_at`) is older than the configured buffer (default: **72 hours**).

### Eligibility Rules
- **ELIGIBLE_CLOSED**: Journal terminates in `run.close` AND `(now - closed_at) >= buffer_s` AND the run is not part of the active conversation lineage. Artifacts are eligible for removal.
- **KEEP_RUNNING**: Journal lacks `run.close`, has `closed_at: null`, or is currently active. These are never swept.
- **KEEP_RECENT**: Journal is closed, but `(now - closed_at) < buffer_s`. Retained to give operators inspection headroom.
- **Current Lineage Protected**: Runs recorded in `~/.system/runs.jsonl` matching the current conversation session (`registry.current_conversation()`) are kept regardless of age.
- **Gate-Waiting & Dead Engines Protected**: Runs waiting on sentinels or whose engine processes died without a clean `run.close` are never touched; dead engines may be relit by the sentry, and their diagnostic evidence must survive.
- **Torn Tail Protection**: Any journal failing validation or showing unexpected trailing events post-close is retained.

### What the Pipeline Will Delete
When `pipeline gc --sweep` executes against eligible runs:
- `transcript.jsonl` (full LLM tool-by-tool chat transcripts)
- `brief.md` (rendered phase instructions)
- `result.json` (per-phase exit records)
- Review directories: `review/` and `review-two/`
- Worker output dumps and temporary diff files
- Quick run companion scratch directories: `<estate>/quick/<run_id>/`

### What the Pipeline NEVER Deletes
The sweeper contains no code paths capable of deleting:
- `journal.jsonl` (preserved in `<estate>/runs/<run_id>/journal.jsonl`)
- `STOPPED` receipts (unconditional evidence of deliberate stops)
- `engine.lock` files
- `~/.system/runs.jsonl` (lineage registry)
- Estate directory layout (`runs/`, `quick/`, `state/`, `logs/`)
- Anything under `plans/` or repository files
- Workspaces under `~/.system/projects/`
- `opencode.db` or any SQLite files

### Pre-Sweep Manifest and Storm Armor
Before unlinking any artifact, `sweep()` writes a complete manifest of every file scheduled for deletion to `<estate>/logs/gc-manifest-<timestamp>.jsonl`. File removals are wrapped in `with_storm_armor` to absorb transient filesystem stalls (EIO/ENOSPC/EAGAIN) without abandoning the sweep.

---

## 3. Comparison with Existing Art

Generic file-retention tools (`tmpreaper`, `systemd-tmpfiles`) operate purely on file access or modification timestamps (`mtime`/`atime`). 
- **The Data-Loss Trap**: An age-only sweeper would delete artifacts from long-running gate-waiting plans, dead-engine runs awaiting sentry relight, or active conversation lineages simply because their brief files were created days ago.
- **The Bespoke Solution**: `pipeline gc` understands journal state derivation, closure invariants, and conversation lineage. The journal-aware verifier is the difference between a safe GC and an unrecoverable data-loss bug.

---

## 4. devpass-code Side: opencode.db Retention and Operator Knobs

The devpass-code database at `~/.local/share/devpass-code/opencode.db` (493 MB) is the single source of truth for the interactive conversation tab, prompt history, agent configuration, and session replays in the TUI.

### Retention Boundary: Documented, Not Automated
1. **Pipeline Hands Off `opencode.db`**: The pipeline sweeper never opens, alters, truncates, or deletes `opencode.db`. Automated deletion of the interactive conversation database would destroy the operator's active TUI context and audit trail.
2. **Ephemeral Directory Retention**: 
   - `tool-output/` (37 MB) and `log/` (13 MB) contain ephemeral execution output.
   - Recommended retention policy: Files older than **30 days** in `tool-output/` and `log/` can be safely pruned using age-based retention buffers, as they are not needed for session replay once historical dispatches have completed.
3. **Database Maintenance is the Operator's Call**: Cleaning, pruning, or shrinking `opencode.db` belongs exclusively to the operator through devpass-code's native session management interfaces.

### Operator Knobs for Session and Database Management

The operator can inspect, prune, and reclaim space from `opencode.db` using the following commands:

- **List existing sessions**:
  ```bash
  devpass-code session list
  ```
  Displays active and historical session IDs, titles, and update timestamps.

- **Delete specific old or completed sessions**:
  ```bash
  devpass-code session delete <sessionID>
  ```
  Removes conversation rows, message blocks, and part histories associated with the session.

- **Check database path and integrity**:
  ```bash
  devpass-code db path
  devpass-code db "PRAGMA integrity_check;"
  ```

- **Reclaim freed space (vacuuming SQLite)**:
  SQLite retains allocated pages after rows are deleted until a vacuum is run:
  ```bash
  devpass-code db "VACUUM;"
  ```

- **Inspect database table space usage**:
  ```bash
  devpass-code db "SELECT name, pgsize FROM dbstat ORDER BY pgsize DESC LIMIT 10;"
  ```

---

## 5. Daily Automation and Systemd Timer

Storage monitoring runs via systemd user services:
- **`systemd/pipeline-gc.service`**: Executes `~/.system/bin/pipeline gc --dry-run` by default. It generates reports without modifying any files.
- **`systemd/pipeline-gc.timer`**: Triggers daily (`OnCalendar=daily`, `Persistent=true`).
- **Operator Sweep Opt-In**: The default service is report-only. To enable automatic sweeping of runs older than 72 hours, the operator opts in by setting `Environment="PIPELINE_GC_SWEEP=1"` and replacing `--dry-run` with `--sweep` in the user unit.

---

## 6. Cloud Archive Tier (Extension)

For long-term retention beyond the local SSD estate, GC is extended via `pipeline/cloudtier.py` using `rclone` (Google Drive primary free tier):
1. **Export**: Eligible run artifacts are bundled into `<estate>/cold/<run>.tar.gz` (excluding `journal.jsonl` and `STOPPED`).
2. **Upload**: The archive is copied to the configured remote (`remote:pipeline-cold/<run>.tar.gz`).
3. **Verify Checksum**: Local SHA-256 is validated against the remote hashsum (`rclone hashsum sha256`).
4. **Manifest & Local Delete**: Only after byte-identical verification passes is the archive manifest recorded and local artifact files swept.
