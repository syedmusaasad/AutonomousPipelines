"""Conversation tab: read-only reader over the devpass-code sqlite DB (session/
message/part tables), opened `file:...?mode=ro` so the TUI can never write into
someone else's live session. Replay of a finished session must be instant at any
size, so loading is CHUNKED (never one big read) with a progress line while it
runs; once the backlog is loaded, new/edited rows are picked up by polling
`time_updated` -- this is also how a streamed reply (a text part whose content
grows token by token) shows up as it grows, not just when it finishes.

No curses import here: this stays a pure-ish data module (the one real I/O is the
sqlite connection) so the rendering rules are unit-testable against a throwaway
fixture DB, exactly like tests/harness.Estate does for the journal.
"""

import json
import sqlite3
from pathlib import Path

STATUS_ABSENT = "absent"   # DB file missing, or the session id isn't in it
STATUS_EMPTY = "empty"     # session exists but has zero parts
STATUS_LOADING = "loading"  # chunked backlog load in progress
STATUS_READY = "ready"     # backlog loaded; now polling time_updated for changes

# part types that render as a one-line collapsed summary
COLLAPSED_TYPES = ("tool", "reasoning")
# part types that are pure internal bookkeeping -- never shown
SILENT_TYPES = ("step-start", "step-finish", "patch", "snapshot")


def db_uri(path: Path) -> str:
    return f"file:{path}?mode=ro"


def open_db(path: Path):
    """A read-only connection, or None if the file plainly doesn't exist (never
    raises for that expected case; only lets connection-level surprises through
    to the caller as a status, via the caller catching sqlite3.Error)."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(db_uri(path), uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _parse_json(raw) -> dict:
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except (TypeError, ValueError):
        return {}


def role_header(role: str) -> str:
    return {"user": "\u00bb you", "assistant": "\u00ab assistant"}.get(role, f"[{role}]")


def _truncate(s: str, n: int = 90) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 3] + "..."


def render_tool_line(data: dict) -> str:
    tool = data.get("tool") or "tool"
    state = data.get("state") or {}
    status = state.get("status") or "?"
    inp = state.get("input") or {}
    detail = ""
    for key in ("command", "filePath", "pattern", "path"):
        if key in inp:
            detail = inp[key]
            break
    else:
        if inp:
            detail = next(iter(inp.values()))
    return f"  [tool:{status}] {tool} {_truncate(detail)}".rstrip()


def render_reasoning_line(data: dict) -> str:
    return f"  (thinking: {_truncate(data.get('text') or '')})"


def render_part_lines(part_type: str, data: dict) -> list:
    """The visual lines one part contributes to the turn history. Tool and
    reasoning parts collapse to exactly one line each; text parts render their full
    (possibly multi-line, possibly still-growing) text verbatim so streaming shows
    up as more lines/characters on the next poll; everything else is silent."""
    if part_type == "tool":
        return [render_tool_line(data)]
    if part_type == "reasoning":
        return [render_reasoning_line(data)]
    if part_type == "text":
        text = data.get("text") or ""
        return text.split("\n") if text else [""]
    return []


class ConversationState:
    """One attached session's turn history, loaded from the devpass-code DB.

    Lifecycle: open() classifies the session (absent/empty/loading). While
    status == loading, call load_more() repeatedly (each call reads one chunk and
    returns True if more remains) -- the caller (app.py) does this across several
    draw ticks so the UI is never blocked on a full read. Once status flips to
    ready, call poll() on the regular data-poll tick to pick up streamed/late rows.
    """

    def __init__(self, session_id: str, db_path, chunk_size: int = 500):
        self.session_id = session_id
        self.db_path = db_path
        self.chunk_size = max(1, chunk_size)
        self.conn = None
        self.status = STATUS_ABSENT
        self.total_count = 0
        self.loaded_count = 0
        self.error = None
        self.turns = []             # ordered list of {message_id, role, part_ids: []}
        self._turn_index = {}       # message_id -> index into self.turns
        self.parts = {}             # part_id -> {"lines": [...], "time_updated": int}
        self._offset = 0
        self._last_update_ts = 0

    # -- lifecycle -----------------------------------------------------------

    def open(self):
        try:
            self.conn = open_db(self.db_path)
        except sqlite3.Error as e:
            self.conn = None
            self.error = str(e)
        if self.conn is None:
            self.status = STATUS_ABSENT
            return self
        try:
            row = self.conn.execute("select 1 from session where id = ?", (self.session_id,)).fetchone()
            if row is None:
                self.status = STATUS_ABSENT
                return self
            self.total_count = self.conn.execute(
                "select count(*) from part where session_id = ?", (self.session_id,)).fetchone()[0]
        except sqlite3.Error as e:
            self.error = str(e)
            self.status = STATUS_ABSENT
            return self
        self.status = STATUS_EMPTY if self.total_count == 0 else STATUS_LOADING
        return self

    def progress_text(self) -> str:
        if self.status != STATUS_LOADING:
            return ""
        return f"loading conversation: {self.loaded_count}/{self.total_count} parts..."

    def banner(self) -> str:
        """The plain, honest line shown instead of faked content when there is
        nothing (yet) to render."""
        if self.status == STATUS_ABSENT:
            return f"no conversation database found for session {self.session_id} (no live session attached)"
        if self.status == STATUS_EMPTY:
            return f"session {self.session_id} has no messages yet"
        return ""

    # -- ingest ---------------------------------------------------------------

    def _ensure_turn(self, message_id: str, role: str) -> dict:
        idx = self._turn_index.get(message_id)
        if idx is None:
            self._turn_index[message_id] = len(self.turns)
            self.turns.append({"message_id": message_id, "role": role, "part_ids": []})
            return self.turns[-1]
        return self.turns[idx]

    def _ingest_row(self, part_id, message_id, role, part_type, data, time_updated):
        is_new = part_id not in self.parts
        self.parts[part_id] = {"lines": render_part_lines(part_type, data), "time_updated": time_updated or 0}
        if is_new:
            self._ensure_turn(message_id, role)["part_ids"].append(part_id)
        if time_updated and time_updated > self._last_update_ts:
            self._last_update_ts = time_updated

    def _ingest_rows(self, rows):
        n = 0
        for r in rows:
            mdata = _parse_json(r["mdata"])
            pdata = _parse_json(r["data"])
            self._ingest_row(r["id"], r["message_id"], mdata.get("role", "?"),
                              pdata.get("type", "?"), pdata, r["time_updated"])
            n += 1
        return n

    # -- chunked backlog load --------------------------------------------------

    def load_more(self) -> bool:
        """Load exactly one chunk. Returns True while more of the backlog remains
        (caller should call again next tick); False once fully loaded (status is
        then STATUS_READY)."""
        if self.status != STATUS_LOADING:
            return False
        rows = self.conn.execute(
            "select p.id as id, p.message_id as message_id, p.time_created as time_created, "
            "p.time_updated as time_updated, p.data as data, m.data as mdata "
            "from part p join message m on m.id = p.message_id "
            "where p.session_id = ? order by p.time_created, p.id limit ? offset ?",
            (self.session_id, self.chunk_size, self._offset)).fetchall()
        n = self._ingest_rows(rows)
        self._offset += n
        self.loaded_count += n
        if n < self.chunk_size:
            self.status = STATUS_READY
            return False
        return True

    # -- streamed / late updates -----------------------------------------------

    def poll(self):
        """Pick up rows created or updated since the last thing we saw. Only makes
        sense once the backlog is fully loaded."""
        if self.conn is None or self.status != STATUS_READY:
            return
        try:
            rows = self.conn.execute(
                "select p.id as id, p.message_id as message_id, p.time_created as time_created, "
                "p.time_updated as time_updated, p.data as data, m.data as mdata "
                "from part p join message m on m.id = p.message_id "
                "where p.session_id = ? and p.time_updated > ? order by p.time_created, p.id",
                (self.session_id, self._last_update_ts)).fetchall()
        except sqlite3.Error:
            return
        self._ingest_rows(rows)

    # -- render -----------------------------------------------------------------

    def lines(self) -> list:
        """The full logical-line turn history, in order: a role header per turn,
        followed by that turn's part lines, with a blank separator between turns.
        This is what scroll.wrap_lines() turns into visual rows for a Viewport."""
        out = []
        for turn in self.turns:
            out.append(role_header(turn["role"]))
            for pid in turn["part_ids"]:
                out.extend(self.parts[pid]["lines"] or [])
            out.append("")
        return out

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except sqlite3.Error:
                pass
            self.conn = None


# ---------------------------------------------------------------- compose / writer

class Composer:
    """Input buffer for the conversation tab's compose line. Typing is always
    captured (so the hint can show what would be sent), but a submit only actually
    sends when a writer is wired up -- this TUI is read-only by design (DECISION
    tui-stack/conversation-attach), so `writer` is None unless something explicitly
    attaches one. `sent_marker` increments on every real send so callers (the
    viewport) can detect "a message just went out" and snap to live."""

    def __init__(self, writer=None):
        self.writer = writer
        self.buffer = ""
        self.sent_marker = 0

    def has_writer(self) -> bool:
        return self.writer is not None

    def type_char(self, ch: str):
        self.buffer += ch

    def backspace(self):
        self.buffer = self.buffer[:-1]

    def clear(self):
        self.buffer = ""

    def hint(self) -> str:
        if self.has_writer():
            return "Enter: send"
        return "read-only: no writer attached for this session (typing is not sent)"

    def submit(self):
        """Send `buffer` if possible. Returns the sent text and resets the buffer
        (bumping sent_marker) when a writer exists and the buffer is non-empty;
        otherwise returns None and leaves the buffer untouched."""
        text = self.buffer.strip()
        if not text or not self.has_writer():
            return None
        self.writer.send(text)
        self.buffer = ""
        self.sent_marker += 1
        return text
