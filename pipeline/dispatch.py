"""Dispatch: run one headless worker session as a disposable worker.

Each seat's model family picks its CLI (roles.cli_of):

  claude  `claude -p --output-format stream-json --verbose --agent pl-<role>
           --model <model> --effort <effort> --permission-mode dontAsk
           --allowed-tools <grants> --disallowed-tools <denies> ...`
  codex   `codex exec --json --model <model> -c model_reasoning_effort=<effort>
           --sandbox read-only | --dangerously-bypass-approvals-and-sandbox
           --cd <cwd> -o <last-message> -`
  agy     `agy --output-format stream-json --model <model> --effort <effort>
           --dangerously-skip-permissions --settings <deny rules> -p=<prompt>`

Claude and Codex read the brief on stdin; agy takes it attached to -p (it never reads
stdin). Codex and agy get the contract and role prompt ahead of the brief, since they
have no agent files. Sealed Codex seats run in the read-only sandbox; unsealed ones run
outside it (workspace-write makes .git read-only) under the generated execpolicy rules. The JSON event stream is written to a transcript file whose
mtime is the liveness signal. Tokens and cost come from the Claude `result` event or
the Codex `turn.completed` events.

Headless Claude Code start-up is kept lean (measured: about 2.4s -> 1.5s per launch on a
one-line prompt): non-essential traffic off, claude.ai connectors and every MCP server
outside --mcp-config off, no session persistence (the transcript file is the record),
and per-machine prompt sections moved out of the system prompt so every worker shares
one prompt-cache prefix.

PIPELINE_WORKER_BIN overrides both binaries (the suite uses a fake);
PIPELINE_CLAUDE_BIN / PIPELINE_CODEX_BIN override one."""

import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path

from . import roles as roles_mod
from .util import now_iso, now_ts
from .usage import UsageAccumulator

DEFAULT_BINS = {"claude": "claude", "codex": "codex", "agy": "agy"}
AGY_INLINE_MAX = 100_000  # bytes; Linux caps one argv string at 128 KiB

# Environment for headless Claude Code workers: skip start-up network round trips that
# a disposable worker never uses, and keep the operator's claude.ai connectors (mail,
# drive, calendar...) out of every worker's tool list.
CLAUDE_HEADLESS_ENV = {
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
    "DISABLE_AUTOUPDATER": "1",
}


def model_arg(model: str, effort: str = None) -> str:
    """Bare model id as the CLIs take it (a legacy 'provider/model#variant' is trimmed)."""
    return model.split("/", 1)[-1].split("#", 1)[0]


def build_argv(*, binary: str, cli: str, agent: str, model: str, effort: str, cwd: Path = None,
               out_dir: Path = None, permissions: dict = None, sealed: bool = False,
               prompt: str = None, settings: dict = None) -> list:
    if cli == "claude":
        argv = [binary, "-p", "--output-format", "stream-json", "--verbose",
                "--agent", agent, "--model", model_arg(model)]
        if effort:
            argv += ["--effort", effort]
        argv += ["--permission-mode", "dontAsk"]
        perms = permissions or {}
        if perms.get("allow"):
            argv += ["--allowed-tools", ",".join(perms["allow"])]
        if perms.get("deny"):
            argv += ["--disallowed-tools", ",".join(perms["deny"])]
        argv += ["--strict-mcp-config", "--no-session-persistence",
                 "--exclude-dynamic-system-prompt-sections"]
        return argv
    if cli == "codex":
        argv = [binary, "exec", "--json", "--model", model_arg(model)]
        if effort:
            argv += ["-c", f'model_reasoning_effort="{effort}"']
        if sealed:
            argv += ["--sandbox", "read-only"]
        else:
            argv += ["--dangerously-bypass-approvals-and-sandbox"]
        argv += ["--skip-git-repo-check", "--ephemeral"]
        if cwd is not None:
            argv += ["--cd", str(cwd)]
        if out_dir is not None:
            argv += ["-o", str(Path(out_dir) / "last-message.md")]
        argv.append("-")
        return argv
    if cli == "agy":
        argv = [binary, "--output-format", "stream-json", "--model", model_arg(model)]
        if effort:
            argv += ["--effort", effort]
        argv += ["--dangerously-skip-permissions", "--disable-slash-commands"]
        if settings:
            argv += ["--settings", json.dumps(settings)]
        argv.append("-p=" + (prompt or ""))
        return argv
    raise ValueError(f"unknown worker cli {cli!r}")


def worker_stdin(cli: str, role: str, brief: str) -> str:
    if cli in ("codex", "agy"):
        return roles_mod.inline_prompt(role).rstrip() + "\n\n---\n\n" + brief
    return brief


def agy_prompt(text: str, path: Path) -> str:
    """The -p prompt for agy: inline when it fits in one argv string, else a pointer to
    the saved prompt file (agy's file tools read it)."""
    if len(text.encode()) <= AGY_INLINE_MAX:
        return text
    return (f"Your full instructions are in {path}. Read the whole file first, then carry "
            f"them out exactly as written.")


_LIMIT_WORDS = re.compile(r"usage limit|rate limit|hit your limit|limit reached|quota|resource_exhausted", re.I)


def is_quota_error(text: str) -> bool:
    """True when a transcript shows the subscription refused the run for usage limits:
    a Claude rate_limit_event whose status is "rejected", or a Codex error/turn.failed
    event whose message names a usage or rate limit."""
    if not text:
        return False
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        t = ev.get("type")
        if t == "rate_limit_event" and (ev.get("rate_limit_info") or {}).get("status") == "rejected":
            return True
        if t in ("error", "turn.failed"):
            msg = ev.get("message") or (ev.get("error") or {}).get("message") or ""
            if _LIMIT_WORDS.search(str(msg)):
                return True
        # agy: an error event, or a result that did not succeed, naming a quota
        agy_ev = ev.get("event")
        if agy_ev == "error" or (agy_ev == "result" and (ev.get("result") or {}).get("status") != "SUCCESS"):
            if _LIMIT_WORDS.search(json.dumps(ev.get("error") or ev.get("result") or ev)):
                return True
    return False


def quota_reset(text: str):
    """When the usage limit resets, as the transcript states it (or None)."""
    for line in (text or "").splitlines():
        if '"rate_limit_event"' not in line:
            continue
        try:
            info = json.loads(line).get("rate_limit_info") or {}
        except (json.JSONDecodeError, AttributeError):
            continue
        if info.get("status") == "rejected" and info.get("resetsAt"):
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(info["resetsAt"])))
    m = re.search(r"(?:try again in|resets? (?:in|at))\s+[^.\"\\]+", text or "", re.I)
    return m.group(0) if m else None


def worker_bin(cli: str = "claude") -> str:
    return (os.environ.get("PIPELINE_WORKER_BIN")
            or os.environ.get(f"PIPELINE_{cli.upper()}_BIN")
            or DEFAULT_BINS[cli])


_DELIVERABLE_STRIP = '.,;:!?()[]{}"\'`*<>'


def parse_deliverables(cwd, final_text: str) -> list:
    """Extract deliverable paths the worker registered in its own closing words.

    Scans only the LAST non-empty block of `final_text` (blocks are separated by
    blank lines) -- the worker's sign-off, not its narration. Each whitespace
    token is stripped of surrounding punctuation and checked against the
    filesystem: it must resolve to an existing regular file under `cwd` (absolute
    paths outside `cwd`, directories, and plain chatter words never match since
    they don't resolve to a real file there). Returns a deduped, sorted list of
    cwd-relative path strings."""
    cwd = Path(cwd)
    if not final_text:
        return []
    blocks = [b for b in re.split(r"\n\s*\n", final_text.strip()) if b.strip()]
    if not blocks:
        return []
    last_block = blocks[-1]
    try:
        cwd_resolved = cwd.resolve()
    except OSError:
        cwd_resolved = cwd
    found = set()
    for tok in re.findall(r"\S+", last_block):
        cleaned = tok.strip(_DELIVERABLE_STRIP)
        if not cleaned:
            continue
        p = Path(cleaned)
        if p.is_absolute():
            try:
                resolved = p.resolve()
            except OSError:
                continue
            try:
                rel = resolved.relative_to(cwd_resolved)
            except ValueError:
                continue
        else:
            rel = Path(cleaned)
            resolved = cwd / rel
        if resolved.is_file():
            found.add(rel.as_posix())
    return sorted(found)


def build_brief(*, task: str, role: str, cwd: Path, exits: list = (), extras: dict = None,
                previous_failure: str = None, preamble: str = None, budget_s: int = None) -> str:
    """Task facts only. The contract and discipline live in the role prompt (agent file)."""
    parts = []
    parts.append(f"WORKING DIRECTORY: {cwd}")
    parts.append(f"ROLE: {role}")
    if budget_s is not None:
        parts.append(
            f"TIME BUDGET: {budget_s}s. SHIP BY: {int(budget_s * 0.8)}s.\n"
            "Investigate only what the task names. At SHIP BY, stop investigating: land what exists\n"
            "(files written, commands run once each), write what remains to NOTES.md, and stop.\n"
            "The engine re-runs the EXIT predicates as the authority after you finish."
        )
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
        self.outcome = kw.get("outcome")  # ok | failed | timeout | killed | outage | quota
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
        self.usage = kw.get("usage")  # New: independent usage with provenance
        self.deliverables = kw.get("deliverables")  # paths the worker registered, cwd-relative

    def as_row(self) -> dict:
        row = {
            "outcome": self.outcome, "exit_code": self.exit_code, "wall_s": round(self.wall_s, 2),
            "tokens": self.tokens, "cost": round(self.cost, 6), "model": self.model,
            "session_id": self.session_id, "error": self.error,
        }
        if self.usage is not None:
            row["usage"] = self.usage
        if self.deliverables is not None:
            row["deliverables"] = self.deliverables
        return row


def run_dispatch(*, brief: str, role: str, cwd: Path, out_dir: Path, timeout: int, env: dict = None,
                 model: str = None, effort: str = None, on_start=None, reg: dict = None) -> DispatchResult:
    """Blocking: runs one worker to completion (or timeout). `on_start(pid, transcript)`
    is called right after spawn so the caller can journal dispatch.start."""
    reg = reg or roles_mod.load()
    s = roles_mod.seat(role, reg)
    model = model or s["model_q"]
    effective_effort = effort if effort is not None else s["effort"]
    out_dir.mkdir(parents=True, exist_ok=True)
    transcript = out_dir / "transcript.jsonl"
    brief_path = out_dir / "brief.md"
    brief_path.write_text(brief)
    (out_dir / "meta.json").write_text(json.dumps({
        "role": role, "model": model, "cli": roles_mod.cli_of(model, reg), "cwd": str(cwd), "started": now_iso(), "timeout": timeout,
    }, indent=1))

    cli = roles_mod.cli_of(model, reg)
    perms = roles_mod.claude_permissions(role, reg) if cli == "claude" else None
    stdin_path = out_dir / "stdin.md"
    stdin_path.write_text(worker_stdin(cli, role, brief))
    argv = build_argv(binary=worker_bin(cli), cli=cli, agent=s["agent"], model=model, effort=effective_effort,
                      cwd=Path(cwd).resolve(), out_dir=out_dir.resolve(), permissions=perms,
                      sealed=bool(s.get("sealed")),
                      prompt=agy_prompt(stdin_path.read_text(), stdin_path.resolve()) if cli == "agy" else None,
                      settings=roles_mod.agy_settings(role, reg) if cli == "agy" else None)
    full_env = dict(os.environ)
    if cli == "claude":
        full_env.update(CLAUDE_HEADLESS_ENV)
    full_env.update(env or {})
    full_env["PIPELINE_ROLE"] = role
    full_env["PIPELINE_HEADLESS"] = "1"
    start = now_ts()
    result = DispatchResult(model=model, transcript=str(transcript))
    try:
        with open(transcript, "wb") as tf, open(os.devnull if cli == "agy" else stdin_path, "rb") as sf:
            proc = subprocess.Popen(argv, stdin=sf, stdout=tf, stderr=subprocess.STDOUT,
                                    cwd=str(cwd), env=full_env, start_new_session=True)
            result.pid = proc.pid
            if on_start:
                on_start(proc.pid, transcript)
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
    last = out_dir / "last-message.md"
    if not result.final_text and last.exists():
        result.final_text = last.read_text(errors="replace")
    result.usage = parsed.get("usage")  # New independent usage field
    result.deliverables = parse_deliverables(cwd, result.final_text)
    if result.outcome is None:
        if parsed["quota"] and result.exit_code != 0:
            # The subscription's usage limit refused the run: neither the model's fault
            # nor a machine failure. There is no fallback seat; the phase waits for a retry.
            result.outcome = "quota"
            reset = quota_reset(parsed["tail"])
            result.error = f"subscription usage limit; resets {reset or 'unknown'}; tail: {parsed['tail'][-400:]}"
        elif result.exit_code == 0 and not parsed["is_error"]:
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
    """Tokens, cost, session id, final text and executed shell commands from a worker
    transcript. Reads Claude Code stream-json, Codex --json, and legacy DevPass events."""
    tokens = {"input": 0, "output": 0, "reasoning": 0, "total": 0}
    empty = {"tokens": tokens, "cost": 0.0, "session_id": None, "final_text": "", "tail": "", "steps": 0,
             "quota": False, "usage": None, "is_error": False, "commands": []}
    try:
        raw = path.read_text(errors="replace")
    except OSError:
        return empty
    acc = UsageAccumulator()
    session_id = None
    texts = []
    commands = []
    is_error = False
    result_text = None
    agy_text = {}  # step_index -> text deltas
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        acc.feed_line(line)
        t = ev.get("type")
        session_id = (session_id or ev.get("session_id") or ev.get("thread_id") or ev.get("sessionID")
                      or ev.get("conversation_id"))
        agy_ev = ev.get("event")
        if agy_ev == "step_update":  # agy
            su = ev.get("step_update") or {}
            if su.get("step_type") == "agent_response" and su.get("text_delta"):
                agy_text.setdefault(su.get("step_index"), []).append(su["text_delta"])
            elif su.get("step_type") == "tool" and su.get("state") == "DONE" and su.get("tool_name") == "run_command":
                cmd = ((su.get("tool_info") or {}).get("parameters") or {}).get("CommandLine")
                if cmd:
                    commands.append(cmd)
            continue
        if agy_ev == "result":  # agy
            res = ev.get("result") or {}
            is_error = res.get("status") != "SUCCESS" or bool(res.get("finished_reason"))
            if isinstance(res.get("response"), str) and res["response"].strip():
                result_text = res["response"]
            continue
        if t == "assistant":  # Claude
            for c in (ev.get("message") or {}).get("content") or []:
                if c.get("type") == "text" and c.get("text"):
                    texts.append(c["text"])
                elif c.get("type") == "tool_use" and c.get("name") == "Bash":
                    cmd = (c.get("input") or {}).get("command")
                    if cmd:
                        commands.append(cmd)
        elif t == "result":  # Claude
            is_error = bool(ev.get("is_error")) or ev.get("subtype") not in (None, "success")
            if isinstance(ev.get("result"), str):
                result_text = ev["result"]
        elif t in ("item.completed", "item.started"):  # Codex
            item = ev.get("item") or {}
            it = item.get("type") or item.get("item_type")
            if t == "item.completed" and it in ("agent_message", "assistant_message") and item.get("text"):
                texts.append(item["text"])
            elif t == "item.started" and it == "command_execution" and item.get("command"):
                commands.append(item["command"])
        elif t == "turn.failed":  # Codex
            is_error = True
        elif t == "text":  # legacy
            texts.append((ev.get("part") or {}).get("text", ""))
        elif t == "tool_use":  # legacy
            part = ev.get("part") or {}
            if part.get("tool") == "bash":
                inp = (part.get("state") or {}).get("input") or {}
                if isinstance(inp, dict) and inp.get("command"):
                    commands.append(inp["command"])
    snap = acc.snapshot()
    tokens = {"input": snap["input"], "output": snap["output"], "reasoning": snap["reasoning"]}
    tokens["total"] = tokens["input"] + tokens["output"] + tokens["reasoning"]
    usage = {
        "provider_total": snap["provider_total"],
        "input_tokens": snap["input"],
        "output_tokens": snap["output"],
        "reasoning_tokens": snap["reasoning"],
        "cache_read_tokens": snap["cache_read"],
        "cache_write_tokens": snap["cache_write"],
        "cost_usd": snap["cost"],
        "steps_completed": snap["steps"],
        "missing_fields": snap["missing_fields"],
        "telemetry_complete": snap["telemetry_complete"],
    }
    if agy_text:
        texts.append("".join(agy_text[max(agy_text, key=lambda k: (k is not None, k or 0))]))
    final_text = result_text if result_text else (texts[-1] if texts else "")
    return {
        "tokens": tokens, "cost": snap["cost"], "session_id": session_id, "final_text": final_text,
        "tail": raw[-2000:], "steps": snap["steps"], "quota": is_quota_error(raw), "usage": usage,
        "is_error": is_error, "commands": commands,
    }
