# Plan 007: terminal UI for the pipeline (read-only, journal-backed)
WORKDIR: /root/pipeline

DECISION tui-stack: no Rust toolchain exists on this host; the TUI is Python 3 stdlib
curses (no new dependencies), one binary entry `pipeline tui` plus launcher `bin/pit`.
The TUI owns no state: it reads per-run journal.jsonl, ~/.system/runs.jsonl, worker
result files, /proc for liveness, and the devpass-code sqlite DB (read-only URI) for
the conversation tab. It never writes anywhere except its own OSC52 clipboard payload.
DECISION conversation-attach: the Conversation tab renders the live interactive session
by reading the devpass-code DB (session/part/message tables, read-only file: URI),
polling by time_updated for streamed replies. Tool-call parts collapse to one line each.
If the DB is absent or empty for the attached session, the tab says so plainly instead of
faking content. tmux capture is used only by the probe, not by the TUI.
DECISION deliverable-registration: the Files tab shows only what workers registered.
The engine (not the TUI) extracts deliverables: dispatch.py already saves result.json;
add a `deliverables` field parsed from the worker's final stdout lines (absolute or
cwd-relative paths that exist on disk), and journal it on dispatch.end. Chat text is
never harvested.
DECISION scoping: tabs scope to the attached conversation id from the runs registry
(PIPELINE_CONVERSATION env, else --conv, else the most recent session row). `a` toggles
all-sessions. Adoption mid-life is automatic: the registry is re-read on every poll.
DECISION probe-is-the-gate: tests/tui_probe.py drives a real TUI inside an isolated
tmux server (`tmux -L pipeline-tui-probe`). No TUI change lands while the probe fails;
the characterization suite runs the probe so the ratchet enforces it.

## Phase 1: register deliverables at dispatch (implementer)
TIMEOUT: 1200
EXIT: python3 tests/run.py
EXIT: python3 -c "from pipeline.dispatch import parse_deliverables; r=parse_deliverables('/root/pipeline', 'wrote /root/pipeline/docs/A.md and notes/ok.md'); assert r==['docs/A.md','notes/ok.md'], r"
EXIT: git log -1 --format=%s | grep -qxF 'dispatch: register worker deliverables'

In pipeline/dispatch.py add parse_deliverables(cwd, final_text): scan only the LAST
non-empty block of the worker's final text; take tokens that resolve to existing files
under cwd (absolute made relative); return a deduped, sorted list. Store as
`deliverables` in DispatchResult.as_row() and hence in dispatch.end journal rows and
result.json. Old rows without the field read as no-deliverables. Add focused tests:
path extraction, non-existent paths ignored, chat-chatter never matched, dedup,
back-compat of as_row. Register in tests/test_suite.py; run the suite ONCE. No TUI code
yet. Stage only pipeline/dispatch.py tests/test_suite.py tests/ratchet.json. Commit
exactly: `git commit -m 'dispatch: register worker deliverables'`. Do not push.

## Phase 2: TUI skeleton — tabs, scoping, truth, drill-down (implementer)
TIMEOUT: 2400
EXIT: python3 tests/run.py
EXIT: python3 -c "import pipeline.tui.app as a; assert callable(a.main) and callable(a.run_app)"
EXIT: python3 -m pipeline.tui.model --selftest
EXIT: git log -1 --format=%s | grep -qxF 'tui: four tabs, session scoping, journal truth'

Create pipeline/tui/: model.py (readers only: registry rows, journal state via
pipeline.journal, liveness via pipeline.util pid_alive+mtime, deliverables from
dispatch.end rows; --selftest prints a summary and exits 0), app.py (curses event loop),
views for the four tabs. `pipeline tui [--conv ID] [--all]` subcommand and bin/pit
launcher (chmod +x) that execs `pipeline tui "$@"`.

Event loop contract (hard requirements, tested by the probe in phase 5):
- select/poll over stdin AND data; on wake drain ALL pending keystrokes exhaustively.
- data arrival (journal mtime, DB poll every 500ms) repaints without any input.
- every keystroke is applied before the next repaint; no batching behind a tick.

Tabs: Pipelines (plans this conversation launched: name, phase progress n/m, state from
journal + liveness truth — running only if engine pid alive; gate-waiting loud; stopped
with reason), Dispatches (quicks: role, state, wall, cost, tokens), Files (deliverables
from dispatch.end rows, curated by origin run/phase), Conversation (stub this phase:
shows 'no live session' honestly). Drill-down: Enter opens phases -> phase listing
(transcript.jsonl path, brief.md, result.json, review dir) -> item view; Esc walks back;
never a dead end (Esc at top stays on tab). Status bar: agent name (pl-interactive),
model+effort from roles/registry.json, context-fill % estimated from the live session's
latest part tokens vs the model's context window (from the gateway models cache at
/tmp/devpass-code/gw-models.json if present, else 'n/a'), session id, auth-expiry 'n/a'
unless a real expiry exists, mouse mode. Liveness NEVER trusts a journal 'running'
claim alone. Add unit tests for model.py (fixture estate via tests/harness.Estate) and
register them. Run the suite ONCE. Stage only the new tui files, cli.py, bin/, tests.
Commit exactly: `git commit -m 'tui: four tabs, session scoping, journal truth'`.

## Phase 3: conversation tab, scroll/follow, mouse, OSC52 (implementer)
TIMEOUT: 2400
EXIT: python3 tests/run.py
EXIT: python3 -m pipeline.tui.model --selftest
EXIT: git log -1 --format=%s | grep -qxF 'tui: conversation, follow-tail, mouse, osc52'

Conversation tab: read-only sqlite (file:...?mode=ro) session/part/message rows for the
attached session; render turn history; tool parts as one collapsed line; text parts
streamed; poll by time_updated. Replay of a finished session is instant at any size:
chunked parse with a progress line during load; the UI never blocks on a full-file read.

Scroll model (shared by EVERY view): one viewport object with offset/limit over visual
rows; visual rows are computed with wrapping at the current width so follow-tail pins
the LAST WRAPPED ROW. Sending a message (only possible in conversation-attach mode when
a writer exists; otherwise typing shows a hint) snaps to live and exits history; live
mode keeps the tail pinned while streaming; scrolling to the bottom edge re-enters live.
Up/PgUp/Home leave live; any scroll position is visible in the hint line.

Mouse: curses mousemask all; wheel scrolls; click-drag selects with visible highlight;
Ctrl+C copies the selection via OSC52 (raw escape, works over SSH); one key `m` toggles
native-terminal selection (mouse capture off) and the hint line names both. Wheel
moves the viewport in every tab. Unit-test the pure parts (wrap calc, follow rules,
selection buffer, OSC52 payload shape). Run the suite ONCE. Commit exactly:
`git commit -m 'tui: conversation, follow-tail, mouse, osc52'`.

## Phase 4: session-manager front door (fast-worker)
TIMEOUT: 900
EXIT: python3 tests/run.py
EXIT: bash -n bin/pipesess && bin/pipesess --help | grep -q 'attach'
EXIT: git log -1 --format=%s | grep -qxF 'tui: pipesess session manager'

bin/pipesess (bash -> `pipeline sess` subcommand): verbs list (sessions from the
registry with run counts and state), attach (spawn `pit` with --conv, refusing to nest:
if PIPELINE_TUI_ATTACHED or a pit process already owns the terminal, exit 3 with a
clear message), kill (SIGTERM an engine by run id, after a journal stop like
`pipeline stop`), resume (delegates to `pipeline resume`). No new state files: it
reads the registry and journals only. Tests: verb parsing, nesting refusal (env var),
list scoping. Run the suite ONCE. Commit exactly:
`git commit -m 'tui: pipesess session manager'`.

## Phase 5: the probe — a hard gate (implementer)
TIMEOUT: 2400
EXIT: python3 tests/tui_probe.py
EXIT: python3 tests/run.py
EXIT: git log -1 --format=%s | grep -qxF 'tui: probe gate (tmux, latency, follow, osc52)'

tests/tui_probe.py: boots the TUI inside `tmux -L pipeline-tui-probe new-session -d`
(isolated socket; NEVER the user's server; abort if TMUX env would nest). Fixtures in
tests/fixtures/tui/: a fake estate with 3 runs (one running with a fake worker pid and
fresh transcript mtime, one gate-waiting, one done with deliverables and a review), a
conversation DB fixture (turns, tool parts, a long wrapping reply), and a GIANT replay
case (>=100k-line transcript + >=20k DB parts) generated deterministically by the probe
on first run (seeded, so it is reproducible; NOT committed if >2MB — regenerate instead).
Assertions, each a hard failure with a clear message:
1. keystroke burst: send 20 keys in one tmux paste, then NOTHING; each key's effect
   must be visible within 150ms (capture pane repeatedly; no follow-up event may be
   needed). 2. streamed reply: append text parts to the fixture DB; the new content must
   appear across timed captures with zero input sent. 3. wheel scrolls the viewport.
4. snap-on-send: history offset resets when a message is sent. 5. pinned tail: during
streaming, the last wrapped row stays visible even with a long wrapping reply.
6. replay-at-scale: the giant fixture opens with a progress line visible and content
interactive within 3s. 7. palette: background is terminal-default/black, exactly ONE
accent color pair + one error color pair registered. 8. active tab is inverted/underline
at capture time. 9. drag-selection emits OSC52 (capture bytes containing the sequence).
10. gate-waiting renders loudly (dedicated color + icon). Wire the probe into
tests/run.py (it may skip with a recorded reason ONLY if tmux is missing; otherwise it
is a hard gate). Run probe ONCE, suite ONCE. Commit exactly:
`git commit -m 'tui: probe gate (tmux, latency, follow, osc52)'`.

## Phase 6: dogfood and land (implementer)
TIMEOUT: 900
EXIT: python3 tests/tui_probe.py
EXIT: python3 tests/run.py
EXIT: git log -1 --format=%s | grep -qxF 'tui: dogfood capture and release'

Dogfood: launch a real quick (`echo probe > /tmp/devpass-code/tui-dogfood.txt` style
task with EXIT), watch it live in the TUI inside the probe tmux server, capture pane
proofs to docs/TUI-DOGFOOD.md (pane captures for: pipelines tab mid-run, dispatches tab
with cost, files tab listing the deliverable, conversation tab replay), then stop.
Write docs/TUI.md: launch (bin/pit, bin/pipesess attach), keys, mouse modes, the
follow rules, and the honest limits (conversation tab is read-only replay+stream unless
a writer exists). Stage only the TUI/plans/docs files named in phases 1-6 plus ratchet.
Commit exactly: `git commit -m 'tui: dogfood capture and release'`. Then push origin
main only this branch. Final lines: commit hash and probe result.

## Phase 7: finish conversation-tab wiring (implementer)
AFTER: 3
TIMEOUT: 1500
ATTEMPTS: 1
EXIT: python3 -c "import pipeline.tui.app, pipeline.tui.conversation, pipeline.tui.mouse, pipeline.tui.scroll; from pipeline.tui.app import main"
EXIT: python3 -m pipeline.tui.model --selftest
EXIT: git log -1 --format=%s | grep -qxF 'tui: conversation, follow-tail, mouse, osc52'

Phase 3 burned both attempts on timeouts, but left conversation.py, mouse.py, scroll.py
written (all parse and import). FINISH the integration; do not rewrite working modules:
1. Wire conversation.py into app.py and views.py: the Conversation tab renders fixture
   or live sessions via model.py's reader; tool parts collapse to one line; text parts
   stream; Nav's handle_key gives the conversation tab first crack for typing when a
   composer exists, skipping keys it does not own.
2. scroll.py is THE shared viewport for every view (conversation, listings, file view):
   one offset/limit model over visual rows; visual rows computed with wrapping at the
   current width; follow-tail pins the LAST WRAPPED ROW; sending a message exits history
   and snaps to live; scrolling to the bottom edge re-enters live.
3. mouse.py: wheel scrolls the active view; click-drag selects with visible highlight;
   Ctrl+C copies via OSC52 escape; one key toggles native-terminal selection; hint line
   names both modes.
4. Conversation replay must be chunked with a progress line; never a blocking full read.
Run the suite ONCE (it covers model.py; phase 5's probe will cover the live loop).
Add unit tests for the pure parts only: wrap computation, follow-state transitions,
selection buffer, OSC52 payload shape. Stage only pipeline/tui/, tests. Commit exactly:
`git commit -m 'tui: conversation, follow-tail, mouse, osc52'`. Do not push. Then phase 4
(pipesess) and phase 5 (probe) proceed unchanged.
