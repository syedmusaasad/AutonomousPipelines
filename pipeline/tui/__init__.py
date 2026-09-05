"""pipeline.tui: a read-only, journal-backed terminal UI.

The TUI owns no state of its own. Everything it shows is read fresh, every poll,
from: per-run journal.jsonl (via pipeline.journal), the runs registry
(~/.system/runs.jsonl, via pipeline.registry), worker result/brief/transcript files
on disk, /proc for liveness (via pipeline.util), and -- for the Conversation tab --
the devpass-code sqlite DB, opened read-only. It never writes anywhere except its
own OSC52 clipboard payload (phase 3)."""
