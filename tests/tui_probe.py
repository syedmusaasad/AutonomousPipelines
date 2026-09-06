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
        result = subprocess.run(["tmux", "-L", SERVER, "capture-pane", "-p", "-t", tmux_session],
                                text=True, capture_output=True, check=True)
        capture = result.stdout
        if capture == previous:
            stable += 1
            if stable >= 2:
                return capture
        else:
            stable = 0
            previous = capture
        time.sleep(0.25)
    return previous


def assert_boot(capture):
    missing = [run_id for run_id in ("r-run", "r-gate", "r-done") if run_id not in capture]
    if missing:
        print(capture)
        raise AssertionError("missing run ids: " + ", ".join(missing))


def main():
    fixup_env()
    build_fixture()
    try:
        assert_boot(boot_tui(SESSION))
        print("PASS: boot")
        return 0
    finally:
        subprocess.run(["tmux", "-L", SERVER, "kill-session", "-t", SESSION],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["tmux", "-L", SERVER, "kill-server"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
