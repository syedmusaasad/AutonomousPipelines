"""Model layer: read-only accessors over the journal, the runs registry, dispatch
result files, /proc liveness, and (for the status bar) the gateway's model-window
cache. Nothing here writes state; every call re-reads from disk/registry/proc so a
poll always reflects the world as it now stands.

Liveness discipline: a journal "running" claim is NEVER trusted alone. Every state
computed here re-checks pid_alive(engine_pid) via pipeline.util, exactly as
pipeline.status does, so a dead engine's "running" phase reads as dead-engine, not
running.
"""

import json
import os
import sqlite3
from pathlib import Path

from .. import paths, plan as planmod, registry, roles as roles_mod
from ..journal import Journal
from ..util import pid_alive, liveness as util_liveness, read_json

CONVERSATION_STUB_TEXT = "no live session (conversation tab lands in phase 3)"


def conversation_db_path() -> Path:
    """Where the Conversation tab reads session/message/part rows from -- same file
    the status bar's session_tokens() already reads (devpass_db_path), kept as its
    own name here since the two readers are conceptually independent."""
    return devpass_db_path()


# ---------------------------------------------------------------- conversation scoping

def resolve_conversation(explicit: str = None) -> str:
    """Order: --conv (explicit) > $PIPELINE_CONVERSATION > most recent registry row's
    conversation. Never guesses beyond what the registry actually recorded."""
    if explicit:
        return explicit
    env = os.environ.get("PIPELINE_CONVERSATION")
    if env:
        return env
    rows = registry.all_rows()
    if not rows:
        return registry.UNKNOWN
    latest = max(rows, key=lambda r: r.get("t", 0) or 0)
    return latest.get("conversation") or registry.UNKNOWN


def rows_for(conversation: str, all_sessions: bool = False) -> list:
    """Registry rows in scope: every row if all_sessions (the 'a' toggle), else only
    rows whose conversation matches (custody rule: a session owns only what it
    launched). Re-read fresh every call -- adoption mid-life is automatic."""
    if all_sessions:
        return registry.all_rows()
    return registry.for_conversation(conversation)


# ---------------------------------------------------------------- liveness truth

def engine_alive(state: dict) -> bool:
    return pid_alive(state.get("engine_pid"))


def derive_run_state(state: dict) -> tuple:
    """(state_label, reason) for one run's journal-derived state dict. Liveness is
    process-existence, never the journal's own "open"/"running" claim: a run left
    "open" by a dead engine reads as dead-engine, and a phase left "running" reads
    as gate-waiting or corpse depending on what it actually is."""
    alive = engine_alive(state)
    phases = state.get("phases", {})
    waiting = {k: p for k, p in phases.items() if p.get("status") == "waiting"}
    if state.get("closed"):
        if state.get("stopped"):
            return f"stopped:{state['stopped']}", state.get("stop_detail")
        return state["closed"], None
    if waiting:
        return "gate-waiting", None
    if state.get("open") and alive:
        return "running", None
    if state.get("open"):
        return "dead-engine", None
    return "unknown", None


# ---------------------------------------------------------------- Pipelines tab

def _plan_meta(plan_path):
    """Best-effort (title, total_phase_count) from the plan file; never raises."""
    if not plan_path:
        return None, None
    try:
        pl = planmod.parse_file(plan_path)
        return (pl.title or None), len(pl.phases)
    except Exception:
        return None, None


def pipeline_row_view(reg_row: dict) -> dict:
    run_id = reg_row["run"]
    st = Journal(run_id).state()
    alive = engine_alive(st)
    phases = st.get("phases", {})
    done_n = sum(1 for p in phases.values() if p.get("status") == "done")
    plan_path = st.get("plan") or reg_row.get("plan")
    title, total_n = _plan_meta(plan_path)
    if total_n is None:
        total_n = len(phases)
    name = title or (Path(plan_path).parent.name if plan_path else run_id)
    state_label, reason = derive_run_state(st)
    return {
        "run": run_id, "name": name, "phase_progress": f"{done_n}/{total_n}",
        "phases_done": done_n, "phases_total": total_n,
        "state": state_label, "reason": reason,
        "engine_pid": st.get("engine_pid"), "engine_alive": alive,
        "conversation": reg_row.get("conversation"), "plan": plan_path, "cwd": st.get("cwd"),
        "phases": phases, "opened_at": st.get("opened_at"), "closed_at": st.get("closed_at"),
    }


def pipelines_tab(conversation: str, all_sessions: bool = False) -> list:
    rows = [r for r in rows_for(conversation, all_sessions) if r.get("kind") == "plan"]
    views = [pipeline_row_view(r) for r in rows]
    views.sort(key=lambda v: v.get("opened_at") or 0, reverse=True)
    return views


# ---------------------------------------------------------------- Dispatches tab (quicks)

def dispatch_row_view(reg_row: dict) -> dict:
    run_id = reg_row["run"]
    st = Journal(run_id).state()
    alive = engine_alive(st)
    phase1 = st.get("phases", {}).get("1", {})
    dispatches = list(st.get("dispatches", {}).values())
    wall_s = sum(d.get("wall_s") or 0 for d in dispatches)
    cost = sum(d.get("cost") or 0 for d in dispatches)
    tokens_total = sum((d.get("tokens") or {}).get("total", 0) or 0 for d in dispatches)
    state_label, reason = derive_run_state(st)
    return {
        "run": run_id, "role": phase1.get("role"), "state": state_label, "reason": reason,
        "wall_s": round(wall_s, 1), "cost": round(cost, 6), "tokens_total": tokens_total,
        "engine_pid": st.get("engine_pid"), "engine_alive": alive,
        "conversation": reg_row.get("conversation"), "cwd": st.get("cwd"), "opened_at": st.get("opened_at"),
    }


def dispatches_tab(conversation: str, all_sessions: bool = False) -> list:
    rows = [r for r in rows_for(conversation, all_sessions) if r.get("kind") == "quick"]
    views = [dispatch_row_view(r) for r in rows]
    views.sort(key=lambda v: v.get("opened_at") or 0, reverse=True)
    return views


# ---------------------------------------------------------------- Files tab (deliverables)

def files_tab(conversation: str, all_sessions: bool = False) -> list:
    """Every deliverable a worker registered (dispatch.end rows carrying
    `deliverables`), curated by origin run/phase. Chat text is never harvested here
    -- that harvesting already happened once, in dispatch.py, at dispatch.end time."""
    rows = rows_for(conversation, all_sessions)
    out = []
    for reg_row in rows:
        run_id = reg_row["run"]
        j = Journal(run_id)
        st = j.state()
        cwd = st.get("cwd")
        for row in j.rows():
            if row.get("event") != "dispatch.end":
                continue
            deliverables = row.get("deliverables")
            if not deliverables:
                continue
            for rel in deliverables:
                abs_path = str(Path(cwd) / rel) if cwd else rel
                out.append({
                    "run": run_id, "phase": row.get("phase"), "role": row.get("role"),
                    "dispatch_id": row.get("id"), "path": rel, "abs_path": abs_path,
                    "exists": Path(abs_path).is_file(), "ts": row.get("ts"),
                    "conversation": reg_row.get("conversation"),
                })
    out.sort(key=lambda r: r.get("ts") or "", reverse=True)
    return out


# ---------------------------------------------------------------- Conversation tab (stub)

def conversation_stub_text(conversation: str) -> str:
    """Phase 2 stub: the tab tells the truth plainly instead of faking content. Real
    session/part rendering lands in phase 3 (DECISION conversation-attach)."""
    return CONVERSATION_STUB_TEXT


# ---------------------------------------------------------------- drill-down: phases -> items

def phase_listing(run_id: str, phase_key: str) -> dict:
    """Everything on disk under runs/<run_id>/phase-<phase_key>/: each attempt's
    transcript.jsonl / brief.md / result.json / meta.json, plus a review dir listing
    (one entry per reviewer) when the phase ran REVIEW: cross."""
    rdir = paths.run_dir(run_id)
    pdir = rdir / f"phase-{phase_key}"
    items = []
    if pdir.exists():
        for attempt_dir in sorted(pdir.glob("attempt-*")):
            subdirs = sorted(attempt_dir.glob("try-*")) + sorted(attempt_dir.glob("lane-*")) + \
                sorted(attempt_dir.glob("surface-rewrite"))
            if not subdirs and attempt_dir.is_dir():
                subdirs = [attempt_dir]
            for d in subdirs:
                entry = {"label": f"{attempt_dir.name}/{d.name}" if d != attempt_dir else attempt_dir.name,
                         "dir": str(d)}
                for fname in ("transcript.jsonl", "brief.md", "result.json", "meta.json"):
                    fp = d / fname
                    if fp.exists():
                        entry[fname] = str(fp)
                items.append(entry)
        review_dir = pdir / "review"
        if review_dir.exists():
            for rev in sorted(p for p in review_dir.iterdir() if p.is_dir()):
                md = rev / "review.md"
                entry = {"label": f"review/{rev.name}", "dir": str(rev)}
                if md.exists():
                    entry["review.md"] = str(md)
                items.append(entry)
    return {"phase": phase_key, "dir": str(pdir), "items": items}


def read_item(path, max_bytes: int = 200000) -> str:
    """Read a drilled-into file for the item view. Truncates from the tail (the most
    recent, most relevant content) rather than the head; never raises."""
    p = Path(path)
    try:
        text = p.read_text(errors="replace")
    except OSError as e:
        return f"(could not read {path}: {e})"
    if len(text) > max_bytes:
        text = "...(truncated, showing tail)...\n" + text[-max_bytes:]
    return text


# ---------------------------------------------------------------- worker liveness (drill-down)

def worker_liveness(dispatch_row: dict, stall_after: float = 900) -> dict:
    transcript = Path(dispatch_row["transcript"]) if dispatch_row.get("transcript") else Path("/nonexistent")
    return util_liveness(dispatch_row.get("pid"), transcript, stall_after)


# ---------------------------------------------------------------- status bar

def devpass_db_path() -> Path:
    override = os.environ.get("PIPELINE_DEVPASS_DB")
    if override:
        return Path(override)
    return Path.home() / ".local" / "share" / "devpass-code" / "opencode.db"


def gw_models_path() -> Path:
    return Path(os.environ.get("PIPELINE_GW_MODELS", "/tmp/devpass-code/gw-models.json"))


def model_context_window(model_bare: str):
    """Context window (tokens) for a bare model id from the gateway models cache, or
    None if the cache is absent or the model is not listed there."""
    data = read_json(gw_models_path(), default=None)
    if not data:
        return None
    for m in data.get("data", []):
        if m.get("id") == model_bare:
            cl = m.get("context_length")
            return cl if cl else None
    return None


def session_tokens(session_id: str):
    """Latest known token usage for a live session from the devpass-code DB (session
    row: cumulative tokens_input/output/reasoning/cache_read/cache_write), or None if
    the DB or the session row is absent."""
    db = devpass_db_path()
    if not session_id or not db.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            row = conn.execute(
                "select tokens_input, tokens_output, tokens_reasoning, tokens_cache_read, tokens_cache_write, model "
                "from session where id = ?", (session_id,)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if not row:
        return None
    ti, to, tr, tcr, tcw, model_json = row
    bare_model = None
    if model_json:
        try:
            bare_model = json.loads(model_json).get("id")
        except (json.JSONDecodeError, AttributeError):
            bare_model = None
    return {
        "input": ti or 0, "output": to or 0, "reasoning": tr or 0,
        "cache_read": tcr or 0, "cache_write": tcw or 0, "model": bare_model,
    }


def context_fill_pct(session_id: str, model_bare: str = None) -> str:
    """Context-fill % estimated from the live session's latest known tokens vs the
    model's context window (gateway models cache). 'n/a' whenever either input is
    missing -- never a fabricated number."""
    usage = session_tokens(session_id)
    if usage is None:
        return "n/a"
    model = model_bare or usage.get("model")
    if not model:
        return "n/a"
    window = model_context_window(model)
    if not window:
        return "n/a"
    used = usage["input"] + usage["output"] + usage["reasoning"] + usage["cache_read"] + usage["cache_write"]
    pct = 100.0 * used / window
    return f"{pct:.1f}%"


def status_bar(conversation: str, reg: dict = None, mouse_hint: str = None) -> dict:
    """agent name, model+effort, context-fill %, session id, auth-expiry, mouse mode.
    auth-expiry is 'n/a' unless a real expiry exists somewhere we can read (devpass-code
    keeps none today, so this always reads 'n/a' honestly). `mouse_hint` is supplied by
    the app (pipeline.tui.mouse.MouseMode().hint()) -- model.py has no curses/mouse
    knowledge of its own, so this stays 'n/a' when the caller doesn't pass one (e.g. the
    --selftest CLI path, which has no live mouse state)."""
    reg = reg or roles_mod.load()
    seat = roles_mod.seat("interactive", reg)
    return {
        "agent": seat["agent"], "model": seat["model_q"], "effort": seat["effort"],
        "context_fill": context_fill_pct(conversation, seat["model"]),
        "session_id": conversation, "auth_expiry": "n/a", "mouse_mode": mouse_hint or "n/a",
    }


# ---------------------------------------------------------------- --selftest

def selftest() -> int:
    conv = resolve_conversation()
    pls = pipelines_tab(conv, all_sessions=False)
    disp = dispatches_tab(conv, all_sessions=False)
    files = files_tab(conv, all_sessions=False)
    conv_text = conversation_stub_text(conv)
    try:
        bar = status_bar(conv)
    except Exception as e:  # registry may be absent in a bare checkout; still exit 0
        bar = {"agent": "n/a", "model": "n/a", "effort": "n/a", "context_fill": "n/a",
               "session_id": conv, "auth_expiry": "n/a", "mouse_mode": "n/a", "_error": str(e)}
    print(f"conversation: {conv}")
    print(f"pipelines: {len(pls)}")
    print(f"dispatches: {len(disp)}")
    print(f"files: {len(files)}")
    print(f"conversation-tab: {conv_text}")
    print(f"status-bar: agent={bar['agent']} model={bar['model']} effort={bar['effort']} "
          f"context_fill={bar['context_fill']} session={bar['session_id']} "
          f"auth_expiry={bar['auth_expiry']} mouse={bar['mouse_mode']}")
    return 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print("usage: python3 -m pipeline.tui.model --selftest", file=sys.stderr)
    sys.exit(1)
