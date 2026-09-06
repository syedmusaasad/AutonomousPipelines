import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


SERVER = "pipeline-tui-probe"
SESSION = "tui-probe"
FIXTURE = Path("/tmp/devpass-code/tui-probe-fixture")


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


def boot_tui(tmux_session):
    env = os.environ.copy()
    env["PIPELINE_ESTATE"] = str(FIXTURE)
    env["HOME"] = str(FIXTURE / "home")
    Path(env["HOME"] + "/.system").mkdir(parents=True, exist_ok=True)
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


# name -> callable. Each phase of plan 011 adds more entries here; --only FILTER
# runs the subset whose name contains FILTER (same convention as tests/run.py).
ASSERTIONS = {
    "boot": run_boot,
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
