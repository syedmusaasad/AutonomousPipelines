# Pipeline terminal UI

`pipeline tui` is a read-only, journal-backed curses view for pipeline runs,
quick dispatches, registered deliverables, and one devpass-code conversation.

## Launch

```sh
bin/pipeline tui
bin/pipeline tui --conv SESSION_ID
bin/pipeline tui --all
```

Without `--conv`, the UI uses `PIPELINE_CONVERSATION` or the most recently
registered conversation.  `--all` expands the first three tabs beyond that
conversation.  The conversation tab remains attached to the selected session.

For an isolated headless capture, use a separate tmux socket:

```sh
env -u TMUX -u TMUX_PANE -u TMUX_TMPDIR \
  tmux -L pipeline-tui-probe new-session -d -s tui 'bin/pipeline tui --conv SESSION_ID'
```

## Tabs and keys

| Key | Action |
| --- | --- |
| `Tab` / `Shift-Tab` | Next / previous tab |
| `1`–`4` | Pipelines, Dispatches, Files, Conversation |
| `j` / `k` or arrows | Move a list cursor; scroll drill-down and conversation panes |
| `Enter` | Drill Pipelines → phases → attempt items → an item file |
| `Esc` | Go up one drill-down level; at a tab root it is a no-op |
| `a` | Toggle this conversation versus all sessions |
| `PageUp` / `PageDown` | Page the active scrollable pane |
| `Home` / `End` | Jump to the beginning / end of the active pane |
| `q` | Quit |

The status bar reports the attached session and the current mouse mode.  The
Pipelines tab contains plan runs; quick work belongs on Dispatches.  Files
shows only deliverables registered by dispatches, not arbitrary worktree files.

## Mouse and selection

Mouse capture starts in **pipeline** mode: wheel scrolling and click-drag
selection are handled by the UI.  Press `m` outside the conversation composer
to toggle to **native-terminal** mode, which disables UI mouse capture for your
terminal's normal selection behavior.  Press `m` again to return to pipeline
mode.  With a pipeline selection active, `Ctrl-C` emits an OSC52 clipboard
payload where the terminal supports it.

## Follow behavior

Every scrollable pane begins in `scroll:live` follow mode.  Incoming
conversation rows and growing content keep the viewport pinned to the last
wrapped row.  `PageUp`, `Home`, upward scroll, or wheel-up leaves follow and the
hint becomes `scroll@N`.  `End`, or scrolling back to the bottom edge, restores
`scroll:live`.  Follow is based on visual wrapped rows, so long streamed lines
remain correctly pinned.

## Conversation limits

The Conversation tab opens the devpass-code SQLite database read-only and
replays existing text, collapsed tool/reasoning summaries, and later streamed
or edited rows.  Large histories load in chunks with a progress line.  Typing
is visible in its composer, but the tab is read-only replay plus stream unless
a writer is explicitly attached; the normal CLI does not attach one, so Enter
does not send messages.
