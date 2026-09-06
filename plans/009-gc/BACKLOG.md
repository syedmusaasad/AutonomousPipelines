# GC backlog — 2026-09-06

Command run: `pipeline gc --dry-run`

```text
gc dry-run: 0 eligible, 65 kept, ~0 bytes recoverable (buffer=72.0h). Nothing deleted; pass --sweep to delete.
```

Counts: **65 runs kept-recent; 0 runs eligible**. The whole estate is younger
than the 72-hour buffer, so the sweeper correctly refused to touch anything.

**nothing eligible under 72h buffer — first real sweep will occur once runs age past the buffer**

No deletions were made. The Google Drive remote is configured; phases 5–6
landed. `opencode.db` remains untouched.
