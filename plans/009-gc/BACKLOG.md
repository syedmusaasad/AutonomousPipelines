# GC backlog — 2026-09-06

Command: `~/.system/bin/pipeline gc --dry-run`

```text
gc dry-run: 0 eligible, 62 kept, ~0 bytes recoverable (buffer=72.0h). Nothing deleted; pass --sweep to delete.
```

The table reported all 62 estate runs/quicks as kept: recent closed runs were
inside the 72-hour buffer, while open/dead-engine and current-lineage runs were
also retained. `opencode.db`, `tool-output/`, and `log/` were not swept.

## Sweep result

Command: `~/.system/bin/pipeline gc --sweep --buffer-hours 72`

- Runs swept: 0
- Paths deleted: 0
- Reported bytes freed: 0
- Estate runs bytes before: 32,909,832 ( `du -sb ~/.system/runs` )
- Estate runs bytes after: 32,909,832 ( `du -sb ~/.system/runs` )
- Honest net freed: **0 bytes**
- GC manifest: `~/.system/logs/gc-manifest-20260906T212045Z.jsonl` (empty, because no paths were eligible)

No eligible backlog remains.
