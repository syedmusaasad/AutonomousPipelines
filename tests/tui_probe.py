import base64
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
from unittest import mock
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


SERVER = "pipeline-tui-probe"
SESSION = "tui-probe"
FIXTURE = Path("/tmp/devpass-code/tui-probe-fixture")
CONV_DB = FIXTURE / "conversation.db"
PROBE_SESSION_ID = "ses_probe"
DONE_TRANSCRIPT_LINES = 500  # hundreds of lines: enough to make the item view genuinely scrollable


def fixup_env():
    # Detach from any parent tmux so the child uses OUR isolated socket.
    # (Refusing outright breaks when an operator runs the probe from inside a
    # tmux shell; what matters is the child not inheriting the parent socket.)
    for k in ("TMUX", "TMUX_PANE", "TMUX_TMPDIR"):
        os.environ.pop(k, None)
    shutil.rmtree(FIXTURE, ignore_errors=True)
    FIXTURE.mkdir(parents=True)
    os.environ["PIPELINE_ESTATE"] = str(FIXTURE)
    os.environ["HOME"] = str(FIXTURE / "home")
    (FIXTURE / "home" / ".system").mkdir(parents=True)


def build_fixture():
    from pipeline.journal import Journal
    from pipeline.registry import register

    runs = FIXTURE / "runs"
    runs.mkdir()
    pid = os.getpid()
    specs = ("r-run", "r-gate", "r-done")
    for run_id in specs:
        run_dir = runs / run_id
        run_dir.mkdir()
        journal = Journal(run_id, run_dir / "journal.jsonl")
        journal.write("run.open", plan="probe", cwd=str(FIXTURE), conversation="ses_probe", pid=pid)
        if run_id == "r-run":
            journal.write("phase.start", phase="1", role="worker", attempt=1)
            (run_dir / "transcript.jsonl").touch()
        elif run_id == "r-gate":
            journal.write("phase.wait", phase="1", sentinel="probe-gate")
        else:
            journal.write("phase.done", phase="1")
            journal.write("run.close", outcome="done")
            # a real dispatch's on-disk shape (phase-<key>/attempt-<n>/try-0/) with a
            # transcript.jsonl deep enough to be genuinely scrollable, so the "scroll"
            # assertion drills into REAL content, not a fixture shortcut. Phase key
            # MUST be numeric (App.try_drill_down sorts phase items via int(kv[0]),
            # matching every real run's phase-<N> numbering).
            item_dir = run_dir / "phase-1" / "attempt-1" / "try-0"
            item_dir.mkdir(parents=True)
            transcript_lines = [json.dumps({"line": i, "text": f"probe transcript line {i}"})
                                for i in range(DONE_TRANSCRIPT_LINES)]
            (item_dir / "transcript.jsonl").write_text("\n".join(transcript_lines) + "\n")
        register("plan", run_id, journal=journal.path, plan="probe", cwd=str(FIXTURE),
                 conversation="ses_probe", launcher_pid=pid, engine_pid=pid)
    _seed_conversation_db()


def _seed_conversation_db():
    """A minimal devpass-code-shaped sqlite DB (session/message/part) for the
    Conversation tab: one session matching the fixture's conversation id, with
    exactly one already-loaded text part. This is enough for
    ConversationState.open() to land in STATUS_LOADING -> STATUS_READY (one
    part, well under chunk_size) so the "stream" assertion's poll() path is
    live by the time it appends more rows behind the TUI's back."""
    conn = sqlite3.connect(str(CONV_DB))
    try:
        conn.execute("CREATE TABLE session (id text PRIMARY KEY)")
        conn.execute("""CREATE TABLE message (id text PRIMARY KEY, session_id text,
                     time_created integer, time_updated integer, data text)""")
        conn.execute("""CREATE TABLE part (id text PRIMARY KEY, message_id text, session_id text,
                     time_created integer, time_updated integer, data text)""")
        now_ms = int(time.time() * 1000)
        conn.execute("INSERT INTO session VALUES (?)", (PROBE_SESSION_ID,))
        conn.execute("INSERT INTO message VALUES (?,?,?,?,?)",
                     ("m-seed", PROBE_SESSION_ID, now_ms, now_ms, json.dumps({"role": "user"})))
        conn.execute("INSERT INTO part VALUES (?,?,?,?,?,?)",
                     ("p-seed", "m-seed", PROBE_SESSION_ID, now_ms, now_ms,
                      json.dumps({"type": "text", "text": "probe seed message"})))
        conn.commit()
    finally:
        conn.close()


def append_stream_part(text: str):
    """Append one assistant text part to the fixture conversation DB, as if a
    real session were streaming a reply -- entirely behind the TUI's back (a
    separate sqlite connection, no keys sent). time_updated is a fresh
    millisecond timestamp so ConversationState.poll() (time_updated > last
    seen) picks it up on the TUI's next data-poll tick."""
    conn = sqlite3.connect(str(CONV_DB))
    try:
        now_ms = int(time.time() * 1000)
        mid = f"m-stream-{now_ms}"
        pid = f"p-stream-{now_ms}"
        conn.execute("INSERT INTO message VALUES (?,?,?,?,?)",
                     (mid, PROBE_SESSION_ID, now_ms, now_ms, json.dumps({"role": "assistant"})))
        conn.execute("INSERT INTO part VALUES (?,?,?,?,?,?)",
                     (pid, mid, PROBE_SESSION_ID, now_ms, now_ms, json.dumps({"type": "text", "text": text})))
        conn.commit()
    finally:
        conn.close()


def capture(tmux_session, escape=False):
    flags = ["-p", "-e", "-t", tmux_session] if escape else ["-p", "-t", tmux_session]
    result = subprocess.run(["tmux", "-L", SERVER, "capture-pane", *flags],
                            text=True, capture_output=True, check=True)
    return result.stdout


def send_key(tmux_session, key):
    subprocess.run(["tmux", "-L", SERVER, "send-keys", "-t", tmux_session, key], check=True)


def pane_dead(tmux_session):
    result = subprocess.run(["tmux", "-L", SERVER, "list-panes", "-t", tmux_session,
                             "-F", "#{pane_dead}"], text=True, capture_output=True)
    return result.stdout.strip() == "1"


def tui_env(db_path=CONV_DB):
    env = os.environ.copy()
    env["PIPELINE_ESTATE"] = str(FIXTURE)
    env["HOME"] = str(FIXTURE / "home")
    env["PIPELINE_DEVPASS_DB"] = str(db_path)
    Path(env["HOME"] + "/.system").mkdir(parents=True, exist_ok=True)
    return env


def _kill_stale():
    """A stale session from a crashed prior run makes new-session fail with
    'duplicate session'. Kill it best-effort before boot; the fixture is
    regenerated anyway, so nothing is lost."""
    subprocess.run(["tmux", "-L", SERVER, "kill-session", "-t", SESSION],
                   capture_output=True)


def boot_tui(tmux_session, conv=PROBE_SESSION_ID, db_path=CONV_DB, settle=True):
    _kill_stale()
    env = tui_env(db_path)
    subprocess.run(["tmux", "-L", SERVER, "new-session", "-d", "-x", "120", "-y", "40",
                    "-s", tmux_session, f"pipeline tui --conv {conv}"],
                    env=env, check=True)
    if not settle:
        return ""
    previous = ""
    stable = 0
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        cap = capture(tmux_session)
        if cap == previous:
            stable += 1
            if stable >= 2:
                return cap
        else:
            stable = 0
            previous = cap
        time.sleep(0.25)
    return previous


def assert_boot(tmux_session, plain):
    missing = [run_id for run_id in ("r-run", "r-gate", "r-done") if run_id not in plain]
    if missing:
        print(plain)
        raise AssertionError("missing run ids: " + ", ".join(missing))

    # the active tab must be visually marked (reverse video on its label).
    esc_cap = capture(tmux_session, escape=True)
    first_line = esc_cap.splitlines()[0] if esc_cap.splitlines() else ""
    if "\x1b[7m" not in first_line and "\x1b[27m" not in first_line:
        print(esc_cap)
        raise AssertionError("active tab is not visually marked (no reverse-video escape "
                              "on the tab bar): " + repr(first_line[:200]))
    # Tab must cycle through all 4 tabs and land back on Pipelines (no dead end).
    tabs = ("Pipelines", "Dispatches", "Files", "Conversation")
    for _ in range(len(tabs)):
        send_key(tmux_session, "Tab")
        time.sleep(0.2)
    after_cycle = capture(tmux_session)
    if pane_dead(tmux_session):
        raise AssertionError("pane died after Tab cycling")
    if "r-run" not in after_cycle and "r-gate" not in after_cycle and "r-done" not in after_cycle:
        # back on Pipelines: fixture rows should be visible again
        print(after_cycle)
        raise AssertionError("Tab did not cycle back to Pipelines after a full loop")

    # Esc must never be a dead end: repeated Esc keeps the app alive and on-screen.
    for _ in range(3):
        send_key(tmux_session, "Escape")
        time.sleep(0.2)
    after_esc = capture(tmux_session)
    if pane_dead(tmux_session):
        print(after_esc)
        raise AssertionError("pane died after Esc (dead end)")
    if not after_esc.strip():
        raise AssertionError("blank pane after Esc (dead end)")


def run_boot():
    plain = boot_tui(SESSION)
    assert_boot(SESSION, plain)


# -- latency ---------------------------------------------------------------

LATENCY_KEYS = "bcdfghjkmnpqrstvwxyz"  # 20 distinct, unambiguous keystrokes
LATENCY_BUDGET_S = 0.15


def assert_latency(tmux_session):
    """Paste LATENCY_KEYS (20 keystrokes) as a single burst, then send nothing
    further. Each key's visible effect (its cumulative prefix appearing in the
    composer line) must show up within LATENCY_BUDGET_S of the paste -- proving
    the real curses event loop drains + repaints promptly, not batched behind a
    tick. Measured by timestamped capture-pane polls against the REAL app; no
    mocks. Failure prints the slowest per-key delta."""
    # land on the Conversation tab: it is the one tab with a composer, so a
    # burst of printable keystrokes has a guaranteed visible effect (the typed
    # buffer growing) rather than being consumed as navigation.
    send_key(tmux_session, "4")
    time.sleep(0.3)
    subprocess.run(["tmux", "-L", SERVER, "set-buffer", LATENCY_KEYS], check=True)
    t0 = time.monotonic()
    subprocess.run(["tmux", "-L", SERVER, "paste-buffer", "-t", tmux_session], check=True)

    deltas = {}  # prefix length -> seconds until first seen
    deadline = t0 + max(2.0, LATENCY_BUDGET_S * len(LATENCY_KEYS) + 1.0)
    seen_full = False
    while time.monotonic() < deadline:
        now = time.monotonic()
        cap = capture(tmux_session)
        for n in range(len(LATENCY_KEYS), 0, -1):
            prefix = LATENCY_KEYS[:n]
            if prefix not in deltas and prefix in cap:
                deltas[prefix] = now - t0
        if LATENCY_KEYS in deltas:
            seen_full = True
            break
        time.sleep(0.01)

    if pane_dead(tmux_session):
        raise AssertionError("pane died mid-burst")
    if not seen_full:
        print(capture(tmux_session))
        missing = [LATENCY_KEYS[:n] for n in range(1, len(LATENCY_KEYS) + 1) if LATENCY_KEYS[:n] not in deltas]
        raise AssertionError(f"full 20-key burst never appeared; missing prefixes up to: {missing[-1] if missing else '?'}")

    # per-key delta: the time between consecutive prefixes appearing (an
    # honest per-keystroke visible-effect latency, not just end-to-end).
    ordered = sorted(deltas.items(), key=lambda kv: len(kv[0]))
    prev_t = 0.0
    slowest = 0.0
    slowest_prefix = ""
    for prefix, t in ordered:
        d = t - prev_t
        if d > slowest:
            slowest = d
            slowest_prefix = prefix
        prev_t = t
    if slowest > LATENCY_BUDGET_S:
        print(capture(tmux_session))
        raise AssertionError(
            f"slowest keystroke-to-visible-effect delta was {slowest * 1000:.1f}ms "
            f"(budget {LATENCY_BUDGET_S * 1000:.0f}ms) landing on prefix {slowest_prefix!r}")


def run_latency():
    boot_tui(SESSION)
    assert_latency(SESSION)


# -- stream ------------------------------------------------------------------

STREAM_MARKER = "STREAM-APPEND-MARKER-7f3a"
STREAM_BUDGET_S = 3.0


def assert_stream(tmux_session):
    """Append a new assistant text part to the fixture conversation DB behind
    the TUI's back (a separate sqlite connection -- no tmux keys sent at all)
    and confirm the new content appears in timed capture-pane polls. Drives the
    REAL app's poll-on-data-tick path (ConversationState.poll(), driven by
    app.py's regular POLL_S refresh), not a mock."""
    send_key(tmux_session, "4")
    time.sleep(0.3)
    before = capture(tmux_session)
    if STREAM_MARKER in before:
        raise AssertionError("marker already present before the DB append (test setup bug)")

    append_stream_part(STREAM_MARKER)
    t0 = time.monotonic()
    seen_at = None
    while time.monotonic() - t0 < STREAM_BUDGET_S:
        cap = capture(tmux_session)
        if STREAM_MARKER in cap:
            seen_at = time.monotonic() - t0
            break
        time.sleep(0.02)

    if pane_dead(tmux_session):
        raise AssertionError("pane died while waiting for the streamed append")
    if seen_at is None:
        print(capture(tmux_session))
        raise AssertionError(
            f"streamed DB append never appeared within {STREAM_BUDGET_S}s of zero-input timed captures")


def run_stream():
    boot_tui(SESSION)
    assert_stream(SESSION)


# -- scroll (wheel/page-up-leaves-follow/end-re-enters-follow/pinned-tail) ----

def send_ctrl_c_raw(tmux_session):
    """Ctrl+C (byte 0x03) as a raw literal keystroke, via -H (hex) so tmux never
    tries to look it up as a named key -- send-keys "C-c" would risk being caught
    by a tmux binding instead of reaching the pane's raw input stream."""
    subprocess.run(["tmux", "-L", SERVER, "send-keys", "-t", tmux_session, "-H", "03"], check=True)


def drill_to_done_transcript(tmux_session):
    """From a fresh boot on the Pipelines tab: Down to the r-done row (fixture
    insertion order is not the on-screen order -- rows sort by opened_at), Enter
    on the run (-> phases), Enter on phase 1 (-> items), Enter on the one item
    (-> the item view, showing transcript.jsonl's real on-disk content)."""
    cap = capture(tmux_session)
    body = cap.splitlines()[2:]
    idx = next(i for i, line in enumerate(body) if "r-done" in line)
    for _ in range(idx):
        send_key(tmux_session, "Down")
        time.sleep(0.05)
    for _ in range(3):
        send_key(tmux_session, "Enter")
        time.sleep(0.3)


def assert_scroll(tmux_session):
    """Drill into the DONE fixture run's transcript.jsonl item view (hundreds of
    real on-disk lines -- no fixture shortcut), then exercise the scroll model's
    documented, already-unit-tested paths against the REAL curses app:

      1. PageUp must change the visible capture (leaves follow, moves the
         viewport up) -- a REAL TUI bug in the item-view scroll wiring if not,
         per the brief; fixed here (max 2 cycles) if it ever regresses.
      2. End must return to the tail AND re-enter follow (hint bar reads
         scroll:live again, capture matches the original bottom-of-file view).
      3. During a live stream append (a DB write behind the TUI's back, on the
         Conversation tab), the last WRAPPED row stays visible -- the pinned
         tail -- without any key sent at all.
    """
    drill_to_done_transcript(tmux_session)
    before = capture(tmux_session)
    if pane_dead(tmux_session):
        raise AssertionError("pane died while drilling into the transcript item view")
    if "transcript.jsonl" not in before:
        print(before)
        raise AssertionError("did not land in the transcript.jsonl item view after drilling down")
    if "scroll:live" not in before:
        print(before)
        raise AssertionError("item view did not boot in follow (scroll:live)")

    send_key(tmux_session, "PageUp")
    time.sleep(0.3)
    after_pgup = capture(tmux_session)
    if after_pgup == before:
        # REAL TUI BUG path per the brief: PageUp did not move the viewport at
        # all. Nothing further to try blind -- surface it loudly rather than
        # silently patching around an unknown root cause.
        print(before)
        raise AssertionError(
            "PageUp did not change the item-view capture: scroll wiring bug "
            "(pipeline/tui/scroll.py Viewport.page_up / app.py KEY_PGUP wiring)")
    if "scroll@" not in after_pgup:
        print(after_pgup)
        raise AssertionError("PageUp did not clear follow (hint bar still not scroll@N)")

    send_key(tmux_session, "End")
    time.sleep(0.3)
    after_end = capture(tmux_session)
    if pane_dead(tmux_session):
        raise AssertionError("pane died after End")
    if "scroll:live" not in after_end:
        print(after_end)
        raise AssertionError("End did not re-enter follow (hint bar not scroll:live)")
    if after_end != before:
        print("BEFORE:\n" + before)
        print("AFTER END:\n" + after_end)
        raise AssertionError("End did not restore the original bottom-of-file view")

    # pinned tail: a stream append lands behind the TUI's back on the
    # Conversation tab, and the last WRAPPED row must stay visible with zero
    # keys sent, exactly like assert_stream but asserting the SAME tail-pin
    # guarantee the item view's follow relies on.
    send_key(tmux_session, "4")
    time.sleep(0.3)
    marker = "PIN-TAIL-MARKER-9c2e-" + ("z" * 200)  # forces multi-row wrap
    append_stream_part(marker)
    tail_token = marker[-24:]  # the tail end of the last wrapped visual row
    t0 = time.monotonic()
    seen_at = None
    while time.monotonic() - t0 < STREAM_BUDGET_S:
        cap = capture(tmux_session)
        if tail_token in cap:
            seen_at = time.monotonic() - t0
            break
        time.sleep(0.02)
    if pane_dead(tmux_session):
        raise AssertionError("pane died while waiting for the pinned-tail append")
    if seen_at is None:
        print(capture(tmux_session))
        raise AssertionError(
            f"pinned tail (last wrapped row of a live stream append) never appeared "
            f"within {STREAM_BUDGET_S}s of zero-input timed captures")


def run_scroll():
    boot_tui(SESSION)
    assert_scroll(SESSION)


# -- replay (20k-part conversation must remain responsive) --------------------

GIANT_FIXTURE = FIXTURE / "giant"
GIANT_DB = GIANT_FIXTURE / "giant.db"
REPLAY_BUDGET_S = 3.0


def assert_replay(tmux_session):
    """Generate the fixed 20k-part/100k-line fixture, then make exactly two
    captures: one while the chunked loader reports progress and one after it has
    had the remaining replay budget to paint conversation content.  There is no
    capture-poll loop here: a slow replay is an app bug, not a probe retry."""
    subprocess.run([sys.executable, str(Path(__file__).parent / "fixtures" / "tui_giant_fixture.py"),
                    "--target", str(GIANT_FIXTURE)], check=True)
    started = time.monotonic()
    boot_tui(tmux_session, conv="ses_giant", db_path=GIANT_DB, settle=False)
    # Give curses one draw tick to create the loading state, then switch to the
    # attached conversation before taking the required immediate capture.
    time.sleep(0.10)
    send_key(tmux_session, "4")
    time.sleep(0.05)  # one paint after the tab key; this is not a capture poll
    progress_cap = capture(tmux_session)
    if "loading conversation:" not in progress_cap:
        print(progress_cap)
        raise AssertionError("giant replay never showed its loading progress line")

    remaining = REPLAY_BUDGET_S - (time.monotonic() - started)
    if remaining > 0:
        time.sleep(remaining)
    content_cap = capture(tmux_session)
    elapsed = time.monotonic() - started
    if pane_dead(tmux_session):
        raise AssertionError("pane died during giant replay")
    if "giant replay line " not in content_cap:
        print(content_cap)
        raise AssertionError("giant replay showed no conversation content rows")
    if elapsed > REPLAY_BUDGET_S + 0.15:
        raise AssertionError(f"giant replay exceeded {REPLAY_BUDGET_S}s budget ({elapsed:.2f}s)")


def run_replay():
    assert_replay(SESSION)


# -- palette (registered colors + visibly marked active tab) ------------------

def assert_palette(tmux_session):
    """The palette contract is an in-process assertion on the App palette, not
    fragile ANSI parsing.  The live capture only verifies that the marked tab and
    the app-owned status-bar identity are actually painted."""
    from pipeline.tui import app as tui_app

    registered = []
    with mock.patch.object(tui_app.palette_mod.curses, "has_colors", return_value=True), \
         mock.patch.object(tui_app.palette_mod.curses, "start_color"), \
         mock.patch.object(tui_app.palette_mod.curses, "use_default_colors"), \
         mock.patch.object(tui_app.palette_mod.curses, "init_pair",
                           side_effect=lambda number, fg, bg: registered.append((number, fg, bg))), \
         mock.patch.object(tui_app.palette_mod.curses, "color_pair", side_effect=lambda number: number):
        app = tui_app.App(PROBE_SESSION_ID)
        app.palette = tui_app.palette_mod.init_palette()
    if set(app.palette) != {"accent", "error"} or len(registered) != 2:
        raise AssertionError(f"expected exactly accent/error palette pairs, got {app.palette!r}, {registered!r}")
    if [pair[0] for pair in registered] != [1, 2]:
        raise AssertionError(f"unexpected palette pair registration: {registered!r}")

    plain = boot_tui(tmux_session)
    escaped = capture(tmux_session, escape=True)
    first_line = escaped.splitlines()[0] if escaped.splitlines() else ""
    if "agent=pl-interactive" not in plain:
        print(plain)
        raise AssertionError("palette capture is missing the app status-bar identity")
    if "\x1b[7m" not in first_line and "\x1b[4m" not in first_line:
        print(escaped)
        raise AssertionError("palette capture has no inverse/underline active-tab marker")


def run_palette():
    assert_palette(SESSION)


# -- osc52 (mouse-capture hint + drag-select + Ctrl+C clipboard escape) -------

def assert_osc52(tmux_session):
    """Verified at two levels, exactly as the brief spells out (a click-drag
    selection created via a synthetic curses mouse-press sequence sent headless
    is unreliable per the brief -- so this drives the REAL tty path instead):

      (a) unit boundary: pipeline.tui.mouse's Selection/osc52_payload are
          covered by tests/test_mouse.py, already part of the main suite
          (tests/run.py) -- not re-asserted here, just named per the brief.
      (b) live tty path: with mouse capture ON (the boot default), the hint
          bar names both modes; a raw SGR mouse press+release pair (the exact
          bytes a real terminal emits for a click-drag) drives the REAL
          App._handle_mouse -> Selection, then a raw Ctrl+C byte drives the
          REAL App._copy_selection -> mouse.osc52_payload -> sys.stdout.write.
          `tmux pipe-pane -O` captures the pane's raw output bytes (capture-pane
          only ever shows the rendered screen, never a BEL-terminated escape
          that never repaints anything) so the OSC52 escape sequence and its
          base64 payload shape are verified directly in the tty byte stream.
    """
    hint = capture(tmux_session)
    if "mouse:pipeline" not in hint or "native-terminal select" not in hint:
        print(hint)
        raise AssertionError("hint bar does not name both mouse modes with capture ON")

    drill_to_done_transcript(tmux_session)
    if pane_dead(tmux_session):
        raise AssertionError("pane died while drilling into the transcript item view")

    pipe_path = FIXTURE / "osc52_pipe.bin"
    subprocess.run(["tmux", "-L", SERVER, "pipe-pane", "-O", "-t", tmux_session,
                    f"cat > {pipe_path}"], check=True)
    try:
        # SGR mouse protocol (the modern encoding real terminals send): ESC [ <
        # Cb ; Cx ; Cy M for press, same suffixed 'm' for release. Cb=0 is the
        # left button; coordinates are 1-based. Row 3 (0-indexed screen row) /
        # cols 3..15 sits inside the item view's visible transcript text.
        press = "\x1b[<0;4;4M"
        release = "\x1b[<0;16;4m"
        subprocess.run(["tmux", "-L", SERVER, "send-keys", "-t", tmux_session, "-l", press], check=True)
        time.sleep(0.2)
        subprocess.run(["tmux", "-L", SERVER, "send-keys", "-t", tmux_session, "-l", release], check=True)
        time.sleep(0.3)
        send_ctrl_c_raw(tmux_session)
        time.sleep(0.5)
    finally:
        subprocess.run(["tmux", "-L", SERVER, "pipe-pane", "-t", tmux_session],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if pane_dead(tmux_session):
        raise AssertionError("pane died after the drag-select + Ctrl+C sequence")
    data = pipe_path.read_bytes() if pipe_path.exists() else b""
    match = re.search(rb"\x1b\]52;c;([A-Za-z0-9+/=]*)\x07", data)
    if match is None:
        raise AssertionError(
            "no OSC52 escape sequence (ESC ] 52 ; c ; <base64> BEL) in the pane's "
            "raw tty output after a drag-select + Ctrl+C")
    try:
        base64.b64decode(match.group(1))
    except Exception as e:
        raise AssertionError(f"OSC52 payload base64 did not decode: {e}")


def run_osc52():
    boot_tui(SESSION)
    assert_osc52(SESSION)


# name -> callable. Each phase of plan 011 adds more entries here; --only FILTER
# runs the subset whose name contains FILTER (same convention as tests/run.py).
ASSERTIONS = {
    "boot": run_boot,
    "latency": run_latency,
    "stream": run_stream,
    "scroll": run_scroll,
    "osc52": run_osc52,
    "replay": run_replay,
    "palette": run_palette,
}


def parse_args(argv):
    only = None
    i = 1
    while i < len(argv):
        if argv[i] == "--only" and i + 1 < len(argv):
            only = argv[i + 1]
            i += 2
        else:
            i += 1
    return only


def main(argv):
    only = parse_args(argv)
    names = [n for n in ASSERTIONS if not only or only in n]
    if not names:
        print(f"no assertion matches --only {only!r}; known: {', '.join(ASSERTIONS)}", file=sys.stderr)
        return 1
    fixup_env()
    build_fixture()
    failed = []
    try:
        for name in names:
            try:
                ASSERTIONS[name]()
                print(f"PASS: {name}")
            except Exception:
                failed.append(name)
                print(f"FAIL: {name}")
                traceback.print_exc()
        return 1 if failed else 0
    finally:
        subprocess.run(["tmux", "-L", SERVER, "kill-session", "-t", SESSION],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["tmux", "-L", SERVER, "kill-server"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception:
        traceback.print_exc()
        sys.exit(1)
