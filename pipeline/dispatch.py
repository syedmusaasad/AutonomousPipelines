"""Dispatch: run one headless devpass-code session as a disposable worker.

The worker binary is `devpass-code run --format json --agent pl-<role> --dir <cwd>`.
Stdin carries the brief; the JSON event stream is written to a transcript file
whose mtime is the liveness signal. Tokens and cost are summed from step_finish
events. The brief is also saved next to the transcript so retries and finishers
can read what was asked.

PIPELINE_WORKER_BIN overrides the binary (the suite uses a fake)."""

import json
import os
import signal
import subprocess
import time
from pathlib import Path

from . import roles as roles_mod
from .util import now_iso, now_ts

DEFAULT_BIN = "devpass-code"


def worker_bin() -> str:
    return os.environ.get("PIPELINE_WORKER_BIN", DEFAULT_BIN)


def build_brief(*, task: str, role: str, cwd: Path, exits: list = (), extras: dict = None,
                previous_failure: str = None, preamble: str = None) -> str:
    """Task facts only. The contract and discipline live in the role prompt (agent file)."""
    parts = []
    parts.append(f"WORKING DIRECTORY: {cwd}")
    parts.append(f"ROLE: {role}")
    if extras:
        for k, v in extras.items():
            parts.append(f"{k}: {v}")
    if preamble:
        parts.append("\n## Plan context\n" + preamble.strip())
    parts.append("\n## Task\n" + task.strip())
    if exits:
        parts.append("\n## EXIT predicates (the engine runs these after you finish; all must pass)")
        for e in exits:
            parts.append(f"- `{e}`")
    if previous_failure:
        parts.append("\n## Previous attempt failed. Attack this failure first.\n" + previous_failure.strip())
    return "\n".join(parts).rstrip() + "\n"


class DispatchResult:
    def __init__(self, **kw):
        self.outcome = kw.get("outcome")  # ok | failed | timeout | killed | outage
        self.exit_code = kw.get("exit_code")
        self.wall_s = kw.get("wall_s", 0.0)
        self.tokens = kw.get("tokens", {"input": 0, "output": 0, "reasoning": 0, "total": 0})
        self.cost = kw.get("cost", 0.0)
        self.model = kw.get("model")
        self.session_id = kw.get("session_id")
        self.final_text = kw.get("final_text", "")
        self.error = kw.get("error")
        self.transcript = kw.get("transcript")
        self.pid = kw.get("pid")

    def as_row(self) -> dict:
        return {
            "outcome": self.outcome, "exit_code": self.exit_code, "wall_s": round(self.wall_s, 2),
            "tokens": self.tokens, "cost": round(self.cost, 6), "model": self.model,
            "session_id": self.session_id, "error": self.error,
        }


def run_dispatch(*, brief: str, role: str, cwd: Path, out_dir: Path, timeout: int, env: dict = None,
                 model: str = None, on_start=None, reg: dict = None) -> DispatchResult:
    """Blocking: runs one worker to completion (or timeout). `on_start(pid, transcript)`
    is called right after spawn so the caller can journal dispatch.start."""
    reg = reg or roles_mod.load()
    s = roles_mod.seat(role, reg)
    model = model or s["model_q"]
    out_dir.mkdir(parents=True, exist_ok=True)
    transcript = out_dir / "transcript.jsonl"
    brief_path = out_dir / "brief.md"
    brief_path.write_text(brief)
    (out_dir / "meta.json").write_text(json.dumps({
        "role": role, "model": model, "cwd": str(cwd), "started": now_iso(), "timeout": timeout,
    }, indent=1))

    argv = [worker_bin(), "run", "--format", "json", "--agent", s["agent"], "--model", model,
            "--variant", s["effort"], "--dir", str(cwd), "--auto"]
    full_env = dict(os.environ)
    full_env.update(env or {})
    full_env["PIPELINE_ROLE"] = role
    full_env["PIPELINE_HEADLESS"] = "1"
    start = now_ts()
    result = DispatchResult(model=model, transcript=str(transcript))
    try:
        with open(transcript, "wb") as tf:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=tf, stderr=subprocess.STDOUT,
                                    cwd=str(cwd), env=full_env, start_new_session=True)
            result.pid = proc.pid
            if on_start:
                on_start(proc.pid, transcript)
            try:
                proc.stdin.write(brief.encode())
                proc.stdin.close()
            except BrokenPipeError:
                pass
            try:
                code = proc.wait(timeout=timeout)
                result.exit_code = code
            except subprocess.TimeoutExpired:
                _kill_group(proc)
                result.exit_code = None
                result.outcome = "timeout"
    except FileNotFoundError as e:
        result.outcome = "failed"
        result.error = f"worker binary not found: {e}"
    except OSError as e:
        result.outcome = "outage"
        result.error = f"os error launching worker: {e}"
    result.wall_s = now_ts() - start
    parsed = parse_transcript(transcript)
    result.tokens = parsed["tokens"]
    result.cost = parsed["cost"]
    result.session_id = parsed["session_id"]
    result.final_text = parsed["final_text"]
    if result.outcome is None:
        if result.exit_code == 0:
            result.outcome = "ok"
        elif result.exit_code is not None and result.exit_code < 0:
            # killed by signal (sentry stall recovery, OOM, host): not the seat's fault
            result.outcome = "killed"
            result.error = f"worker killed by signal {-result.exit_code}; tail: {parsed['tail'][-600:]}"
        else:
            result.outcome = "failed"
            result.error = result.error or f"worker exit code {result.exit_code}; tail: {parsed['tail'][-600:]}"
    (out_dir / "result.json").write_text(json.dumps(result.as_row(), indent=1))
    return result


def _kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=10)
            return
        except subprocess.TimeoutExpired:
            pass
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
    except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
        pass


def parse_transcript(path: Path) -> dict:
    """Sum tokens/cost from step_finish events; collect final assistant text."""
    tokens = {"input": 0, "output": 0, "reasoning": 0, "total": 0}
    cost = 0.0
    session_id = None
    texts = []
    tail = ""
    try:
        raw = path.read_text(errors="replace")
    except OSError:
        return {"tokens": tokens, "cost": cost, "session_id": None, "final_text": "", "tail": ""}
    tail = raw[-2000:]
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        session_id = session_id or ev.get("sessionID")
        part = ev.get("part") or {}
        t = ev.get("type")
        if t == "step_finish":
            tk = part.get("tokens") or {}
            tokens["input"] += int(tk.get("input", 0) or 0)
            tokens["output"] += int(tk.get("output", 0) or 0)
            tokens["reasoning"] += int(tk.get("reasoning", 0) or 0)
            cost += float(part.get("cost", 0) or 0)
        elif t == "text":
            texts.append(part.get("text", ""))
    tokens["total"] = tokens["input"] + tokens["output"] + tokens["reasoning"]
    final_text = texts[-1] if texts else ""
    return {"tokens": tokens, "cost": cost, "session_id": session_id, "final_text": final_text, "tail": tail}
