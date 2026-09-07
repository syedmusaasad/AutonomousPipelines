import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


SERVER = "pipeline-tui-probe"
SESSION = "tui-probe"
FIXTURE = Path("/tmp/devpass-code/tui-probe-fixture")
CONV_DB = FIXTURE / "conversation.db"
PROBE_SESSION_ID = "ses_probe"


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
            journal.write("phase.start", phase="build", role="worker", attempt=1)
            (run_dir / "transcript.jsonl").touch()
        elif run_id == "r-gate":
            journal.write("phase.wait", phase="review", sentinel="probe-gate")
        else:
            journal.write("phase.done", phase="build")
            journal.write("run.close", outcome="done")
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


def tui_env():
    env = os.environ.copy()
    env["PIPELINE_ESTATE"] = str(FIXTURE)
    env["HOME"] = str(FIXTURE / "home")
    env["PIPELINE_DEVPASS_DB"] = str(CONV_DB)
    Path(env["HOME"] + "/.system").mkdir(parents=True, exist_ok=True)
    return env


def _kill_stale():
    """A stale session from a crashed prior run makes new-session fail with
    'duplicate session'. Kill it best-effort before boot; the fixture is
    regenerated anyway, so nothing is lost."""
    subprocess.run(["tmux", "-L", SERVER, "kill-session", "-t", SESSION],
                   capture_output=True)


def boot_tui(tmux_session):
    _kill_stale()
    env = tui_env()
    subprocess.run(["tmux", "-L", SERVER, "new-session", "-d", "-x", "120", "-y", "40",
                    "-s", tmux_session, "pipeline tui --conv ses_probe"],
                   env=env, check=True)
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


# name -> callable. Each phase of plan 011 adds more entries here; --only FILTER
# runs the subset whose name contains FILTER (same convention as tests/run.py).
ASSERTIONS = {
    "boot": run_boot,
    "latency": run_latency,
    "stream": run_stream,
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
