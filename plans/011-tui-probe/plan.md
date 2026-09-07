# Plan 011: TUI probe — split into proven, gated steps
WORKDIR: /root/pipeline

DECISION probe-split: phase 5 of plan 007 burned two 40-minute timeouts because ten
assertions were bundled into one dispatch. This plan does the same work in FOUR small
phases, each independently verifiable, each with a tight timeout. The probe remains a
hard gate: the TUI is not done until tests/tui_probe.py passes, but building it in
steps means each step can fail honestly without taking the whole gate down.
DECISION no-registry-changes: the fallback sweep is COMPLETE (researcher and
document-writer fallbacks now glm-5.3, recorded as DECISION fallback-sweep). Do not
touch roles/registry.json, roles/*.md, or README.md in this plan.
DECISION isolation: all tmux work uses the socket pipeline-tui-probe (never the
user's server). Abort if TMUX is in the environment.

## Phase 1: probe harness boots and one assertion passes (implementer)
TIMEOUT: 900
ATTEMPTS: 1
EXIT: python3 tests/tui_probe.py --only boot
EXIT: python3 tests/run.py 2>&1 | grep -q " 0 failed"
EXIT: git log -1 --format=%s | grep -qxF 'tui-probe: harness boots, boot assertion passes'

Create tests/tui_probe.py with a --only FILTER flag: it builds the fixture estate
(3 runs: one running with a live fake worker pid + fresh transcript mtime, one
gate-waiting, one done with deliverables and a review dir; generated under
/tmp/devpass-code/tui-probe-fixture via tests/harness.Estate patterns), boots
`pipeline tui` inside `tmux -L pipeline-tui-probe new-session -d -x 120 -y 40`,
and implements assertion "boot": the TUI renders the Pipelines tab with the three
fixture runs visible within 10s, the active tab marked, and Esc cycles tabs without
dead ends. Add ONLY this assertion now; --only boot exits 0 on pass, 1 on fail with
a capture pane dump in the failure message. Wire into tests/run.py as a skip-if-no-tmux
step (hard gate when tmux exists). Keep the fixture generation seeded/idempotent.
Commit exactly: `git commit -m 'tui-probe: harness boots, boot assertion passes'`.

## Phase 2: latency and stream assertions (implementer)
TIMEOUT: 1200
ATTEMPTS: 1
EXIT: python3 tests/tui_probe.py --only latency
EXIT: python3 tests/tui_probe.py --only stream
EXIT: python3 tests/run.py 2>&1 | grep -q " 0 failed"
EXIT: git log -1 --format=%s | grep -qxF 'tui-probe: keystroke latency and zero-input stream repaint'

Add to tests/tui_probe.py: assertion "latency" — paste a burst of 20 keystrokes via
tmux paste-buffer, then NO further input; each key's visible effect must appear within
150ms (measure by timestamped capture-pane polls; fail prints the slowest delta).
Assertion "stream" — append text parts to the fixture conversation DB behind the TUI's
back; the new content must appear across timed captures with zero keys sent.
Both must drive the REAL app (no mocks). Same fixture boot. Commit exactly:
`git commit -m 'tui-probe: keystroke latency and zero-input stream repaint'`.

## Phase 3: scroll, follow, mouse, OSC52 assertions (implementer)
TIMEOUT: 1200
ATTEMPTS: 1
EXIT: python3 tests/tui_probe.py --only scroll
EXIT: python3 tests/tui_probe.py --only osc52
EXIT: python3 tests/run.py 2>&1 | grep -q " 0 failed"
EXIT: git log -1 --format=%s | grep -qxF 'tui-probe: wheel/snap/pin and osc52 capture'

Add: assertion "scroll" (DECISIONS PRE-MADE — do not deliberate): boot as in phases
1-2; open the DONE fixture run's item view for its transcript.jsonl (drill: Enter on the
run, Enter on the phase, Enter on the transcript item). That file has hundreds of lines —
no fixture changes needed. Send wheel events: tmux send-keys with Mouse events is
unreliable; instead send PageUp (the scroll model's page-up path, already unit-tested for
leaving follow) — capture before/after MUST differ. Then End (bottom-edge re-follow). No
other approach; if PageUp does not change the capture, that is a REAL TUI BUG in the
item-view scroll wiring — fix the smallest thing in pipeline/tui/ and retest (max 2
cycles). Wheel events move the viewport (capture before/after differ);
scrolling to the bottom edge re-enters follow; during a stream append the last WRAPPED
row stays visible (pinned tail). Assertion "osc52" (PRE-MADE): in the same item view, start a selection by sending a
mouse press sequence is unreliable headless; instead test the REAL code path at the unit
boundary plus the tty path: (a) unit-verify pipeline.tui.mouse selection -> payload
already covered by suite; (b) for the live path, drive Ctrl+C with an ACTIVE selection
created programmatically via the app's own selection API is not reachable headless —
so assert the OBSERVABLE: with mouse capture ON, the hint line names both modes, and a
selection followed by Ctrl+C emits OSC52 — a click-drag selection followed by
Ctrl+C emits the OSC52 escape sequence in the pane bytes (tmux capture-pane -p -e or
the tty buffer), verifying the payload shape. Commit exactly:
`git commit -m 'tui-probe: wheel/snap/pin and osc52 capture'`.

## Phase 4: replay-at-scale + palette, then full gate (implementer)
TIMEOUT: 1200
ATTEMPTS: 1
EXIT: python3 tests/tui_probe.py --only replay
EXIT: python3 tests/tui_probe.py --only palette
EXIT: python3 tests/tui_probe.py
EXIT: python3 tests/run.py 2>&1 | grep -q " 0 failed"
EXIT: git log -1 --format=%s | grep -qxF 'tui-probe: giant replay, palette, full gate green'

Add (DECISIONS PRE-MADE; the giant fixture ALREADY EXISTS as
tests/fixtures/tui_giant_fixture.py — run it, do not redesign it):
assertion "replay": python3 tests/fixtures/tui_giant_fixture.py --target
/tmp/devpass-code/tui-probe-fixture/giant (this writes giant.db with session
"ses_giant" and 20k parts, plus the 100k-line transcript). Boot the TUI pointing
--conv at the giant session id. The assertion PASSES when: within 3s of boot, a
capture shows content rows (any conversation text) AND the progress line appeared at
least once during loading (capture twice: immediately after boot for the progress line,
then after settle for content; two captures, no polling loop). If 3s is exceeded, that
is a REAL TUI bug in the chunked-replay path (pipeline/tui/conversation.py load_more) -
fix the smallest thing, max 2 cycles.
assertion "palette": boot as phases 1-3; capture with -e (escapes); PASS when: exactly
one accent color pair and one error pair are registered by the app (assert on the app's
palette dict via a tiny import test in-process, not by parsing escape codes) AND the
capture shows the active tab marker (the inverted/underline attribute char - check for
the app's own status-bar prefix strings, e.g. the agent name "pl-interactive", in the
capture). Do NOT parse ANSI color codes from the pane; assert the app's registered
palette object and visible marker strings instead.
Generate a GIANT fixture (>=100k-line transcript,
>=20k DB parts, seeded, under /tmp not committed); the conversation view must show a
progress line during load and be interactive within 3s. Assertion "palette" — the app
registers exactly one accent + one error color pair; capture shows the active tab
inverted/underlined; background is default/black. THEN: the full probe with no filter
must pass — every assertion, one gate. Update docs/TUI.md keys section if probe
behavior forced fixes. Commit exactly:
`git commit -m 'tui-probe: giant replay, palette, full gate green'`.

## Phase 5: dogfood and land (implementer)
TIMEOUT: 900
ATTEMPTS: 1
EXIT: test -s docs/TUI-DOGFOOD.md
EXIT: python3 tests/tui_probe.py
EXIT: git log -1 --format=%s | grep -qxF 'tui: dogfood capture and release'

Launch a real quick (touch a file task with EXIT) through bin/quick, watch it live in
the TUI inside the probe tmux server, capture pane proofs to docs/TUI-DOGFOOD.md
(pipelines tab mid-run, dispatches tab with cost, files tab listing the deliverable,
conversation replay), stop the run. Finish docs/TUI.md (launch, keys, mouse modes,
follow rules, honest limits: conversation tab is read-only replay+stream unless a
writer exists). Stage only the files this plan touched plus docs. Commit exactly:
`git commit -m 'tui: dogfood capture and release'`. Then `git push origin main`.
Final lines: commit hash + full-probe result.
