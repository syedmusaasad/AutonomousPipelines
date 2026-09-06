# GC backlog — 2026-09-06

Command run first: `~/.system/bin/pipeline gc --dry-run`

| result | count | estimated bytes |
|---|---:|---:|
| eligible | 0 | 0 |
| kept | 61 | 0 |
| recoverable total | — | 0 |

| keep reason | count |
|---|---:|
| closed but within the 72-hour buffer | 59 |
| open/running or dead-engine awaiting relight | 2 |

The dry-run table contained 37 quicks and 24 plans. No run qualified: the
oldest closed run was about 60 hours old, below the 72-hour buffer. The current
lineage (`run_20260906T163914_244032`) was kept. Journals and the runs registry
were not candidates. `opencode.db` and `tool-output` were not inspected or
swept.

## Sweep result

Command: `~/.system/bin/pipeline gc --sweep --buffer-hours 72`

| measurement | bytes |
|---|---:|
| `du -sb ~/.system/runs` before | 32,616,729 |
| `du -sb ~/.system/runs` after | 32,616,729 |
| actual freed | **0** |

Sweep reported `0 run(s), deleted 0 path(s), ~0 bytes freed` and exited 0.
Manifest: `~/.system/logs/gc-manifest-20260906T165431Z.jsonl`.
