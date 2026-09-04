"""Shared machinery: storm-armored writes, liveness, detached launch, ids, time."""

import fcntl
import json
import os
import secrets
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Storm armor: a filesystem blip costs a wait, not a death. Backoff schedule in
# seconds; the total is ~15 minutes. Tests shrink it via PIPELINE_BACKOFF_SCALE.
BACKOFF_SCHEDULE = (5, 15, 30, 60, 120, 240, 420)


def _scale() -> float:
    try:
        return float(os.environ.get("PIPELINE_BACKOFF_SCALE", "1"))
    except ValueError:
        return 1.0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_ts() -> float:
    return time.time()


def new_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{secrets.token_hex(3)}"


def log(msg: str, *, stream=None) -> None:
    stream = stream or sys.stderr
    try:
        stream.write(f"[{now_iso()}] {msg}\n")
        stream.flush()
    except Exception:
        pass


def with_storm_armor(fn, *, what: str = "fs-op", schedule=BACKOFF_SCHEDULE):
    """Run fn(); on OSError retry with minutes-scale backoff. Raises after schedule
    exhausted. Never swallows non-OSError exceptions."""
    last = None
    for i, delay in enumerate(list(schedule) + [None]):
        try:
            return fn()
        except OSError as e:  # includes IOError, PermissionError, ENOSPC, EIO, stale NFS
            last = e
            if delay is None:
                break
            log(f"storm-armor: {what} failed ({e}); retry {i + 1}/{len(schedule)} in {delay * _scale():.1f}s")
            time.sleep(delay * _scale())
    raise RuntimeError(f"storm-armor exhausted for {what}: {last}")


def fs_probe(path: Path) -> bool:
    """Probe the filesystem BEFORE acting: can we stat, create, write, read, and remove
    a temp file under path? Never raises."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".probe-{os.getpid()}-{secrets.token_hex(2)}"
        probe.write_text("ok")
        ok = probe.read_text() == "ok"
        probe.unlink()
        return ok
    except Exception:
        return False


def atomic_write(path: Path, text: str) -> None:
    def _do():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
        tmp.write_text(text)
        os.replace(tmp, path)

    with_storm_armor(_do, what=f"atomic_write {path}")


def append_jsonl(path: Path, row: dict) -> None:
    """Append one JSON row under an exclusive lock, with storm armor."""
    line = json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n"

    def _do():
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    with_storm_armor(_do, what=f"append {path}")


def read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # A torn tail line from a crash mid-write: skip, never die.
                continue
    return rows


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def pid_alive(pid) -> bool:
    if not pid:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # Zombies show as alive to kill(0); check /proc state.
    try:
        with open(f"/proc/{pid}/stat") as f:
            state = f.read().split(")")[-1].split()[0]
        return state != "Z"
    except OSError:
        return True


def mtime_or_none(path: Path):
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def liveness(pid, transcript: Path, stall_after: float) -> dict:
    """Liveness is process-existence plus transcript mtime; never the journal's own claim.
    Returns {alive, stalled, age} where age is seconds since the transcript last moved."""
    alive = pid_alive(pid)
    mt = mtime_or_none(transcript)
    age = (time.time() - mt) if mt is not None else None
    stalled = alive and (age is None or age > stall_after)
    return {"alive": alive, "stalled": stalled, "age": age}


def launch_detached(argv: list, *, log_path: Path, cwd=None, env=None) -> int:
    """Launch fully detached: new session (setsid), stdin from /dev/null, stdout/err to
    log. Equivalent to `( setsid nohup cmd >log 2>&1 & )`. Returns the child pid."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    with open(log_path, "ab") as lf, open(os.devnull, "rb") as devnull:
        p = subprocess.Popen(
            argv,
            stdin=devnull,
            stdout=lf,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            env=full_env,
            start_new_session=True,  # setsid
            close_fds=True,
        )
    return p.pid


def shell_join(argv) -> str:
    return " ".join(shlex.quote(a) for a in argv)


class FileLock:
    """Exclusive advisory lock on a lock file. Non-blocking acquire -> False if held."""

    def __init__(self, path: Path):
        self.path = path
        self.fh = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.close()
            self.fh = None
            return False
        self.fh.seek(0)
        self.fh.truncate()
        self.fh.write(str(os.getpid()))
        self.fh.flush()
        return True

    def release(self) -> None:
        if self.fh:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
            self.fh.close()
            self.fh = None
