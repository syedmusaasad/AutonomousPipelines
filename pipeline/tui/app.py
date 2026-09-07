"""pipeline.tui.app: the curses event loop and navigation state.

Split deliberately: `Nav` holds pure navigation/drill-down logic with no curses
import, so it is unit-testable without a terminal; `App` wraps a `Nav` with the
data cache (read fresh from `pipeline.tui.model` every poll) and drives curses.

Event loop contract (hard requirement, tested end-to-end by the phase-5 probe):
  - select/poll over stdin AND the data-poll interval; on wake, drain ALL pending
    keystrokes exhaustively before repainting.
  - data arrival (poll interval elapses) repaints without any input.
  - every keystroke is applied before the next repaint; no batching behind a tick.

Phase 3 adds: the Conversation tab (pipeline.tui.conversation), the shared
scroll.Viewport model used by every pane, and mouse support (wheel scroll,
click-drag selection, OSC52 copy, native/pipeline mouse-mode toggle).
"""

import argparse
import curses
import os
import select
import sys
import time

from . import model, views
from . import conversation as conv_mod
from . import mouse as mouse_mod
from . import palette as palette_mod
from . import scroll as scroll_mod

POLL_S = float(os.environ.get("PIPELINE_TUI_POLL_S", "0.5"))
LOAD_SPIN_S = 0.02  # select() timeout while a conversation backlog is chunk-loading,
                     # so the progress line advances fast without ever blocking on a
                     # full read in one shot.

TABS = ("Pipelines", "Dispatches", "Files", "Conversation")
CONVERSATION_TAB = TABS.index("Conversation")

KEY_QUIT = frozenset({ord("q"), ord("Q")})
KEY_UP = frozenset({curses.KEY_UP if hasattr(curses, "KEY_UP") else 259, ord("k")})
KEY_DOWN = frozenset({curses.KEY_DOWN if hasattr(curses, "KEY_DOWN") else 258, ord("j")})
KEY_ENTER = frozenset({10, 13, curses.KEY_ENTER if hasattr(curses, "KEY_ENTER") else 343})
KEY_ESC = 27
KEY_TAB = 9
KEY_BTAB = curses.KEY_BTAB if hasattr(curses, "KEY_BTAB") else 353
KEY_TOGGLE_ALL = frozenset({ord("a"), ord("A")})
KEY_TOGGLE_MOUSE = frozenset({ord("m"), ord("M")})
KEY_PGUP = curses.KEY_PPAGE if hasattr(curses, "KEY_PPAGE") else 339
KEY_PGDN = curses.KEY_NPAGE if hasattr(curses, "KEY_NPAGE") else 338
KEY_HOME = curses.KEY_HOME if hasattr(curses, "KEY_HOME") else 262
KEY_END = curses.KEY_END if hasattr(curses, "KEY_END") else 360
KEY_MOUSE = curses.KEY_MOUSE if hasattr(curses, "KEY_MOUSE") else 409
KEY_BACKSPACE = frozenset({curses.KEY_BACKSPACE if hasattr(curses, "KEY_BACKSPACE") else 263, 127, 8})
KEY_CTRL_C = 3
WHEEL_ROWS = 3  # visual rows moved per wheel tick
CONV_SCROLL_KEYS = KEY_UP | KEY_DOWN | frozenset({KEY_PGUP, KEY_PGDN, KEY_HOME, KEY_END})


def _is_typing_key(ch: int) -> bool:
    """A key the Conversation tab's composer owns when it has first crack: any
    printable character or backspace. Enter is handled separately (submit vs.
    drill-down/Nav depending on context). Arrows, Tab, Esc, page/home/end, and
    mouse events stay navigation/scroll and fall through to Nav/Viewport."""
    return (32 <= ch <= 126) or ch in KEY_BACKSPACE


class Nav:
    """Pure navigation state: current tab, per-tab selection cursor, and a
    drill-down stack. No curses dependency -- unit-testable headless."""

    def __init__(self):
        self.tab = 0
        self.selection = {i: 0 for i in range(len(TABS))}
        self.stack = []  # list of dicts describing the drill-down frame
        self.all_sessions = False
        self.quit = False

    # -- tab switching --------------------------------------------------

    def goto_tab(self, idx: int):
        self.tab = idx % len(TABS)
        self.stack = []

    def next_tab(self):
        self.goto_tab(self.tab + 1)

    def prev_tab(self):
        self.goto_tab(self.tab - 1)

    def toggle_all_sessions(self):
        self.all_sessions = not self.all_sessions
        self.stack = []

    # -- selection within the current list -------------------------------

    def cur_selection(self) -> int:
        if self.stack:
            return self.stack[-1].get("selection", 0)
        return self.selection.get(self.tab, 0)

    def set_selection(self, n: int):
        if self.stack:
            self.stack[-1]["selection"] = n
        else:
            self.selection[self.tab] = n

    def move(self, delta: int, list_len: int):
        if list_len <= 0:
            self.set_selection(0)
            return
        n = max(0, min(list_len - 1, self.cur_selection() + delta))
        self.set_selection(n)

    def clamp(self, list_len: int):
        if list_len <= 0:
            self.set_selection(0)
        else:
            self.set_selection(max(0, min(list_len - 1, self.cur_selection())))

    # -- drill-down: never a dead end; Esc at top stays on the tab -------

    def push(self, frame: dict):
        frame = dict(frame)
        frame.setdefault("selection", 0)
        self.stack.append(frame)

    def pop(self):
        if self.stack:
            self.stack.pop()
        # Esc with an empty stack is a no-op: stays on the tab, never exits.

    def depth(self) -> int:
        return len(self.stack)

    def top(self):
        return self.stack[-1] if self.stack else None

    # -- key handling ------------------------------------------------------

    def handle_key(self, ch: int, list_len: int) -> None:
        """Apply one keystroke to the navigation state. `list_len` is the length of
        whatever list is currently on screen (the tab's rows, or the drill-down
        frame's items) so Up/Down/Enter/clamp know their bounds."""
        if ch in KEY_QUIT:
            self.quit = True
        elif ch in KEY_UP:
            self.move(-1, list_len)
        elif ch in KEY_DOWN:
            self.move(1, list_len)
        elif ch in KEY_ENTER:
            self.on_enter(list_len)
        elif ch == KEY_ESC:
            self.pop()
        elif ch == KEY_TAB:
            self.next_tab()
        elif ch == KEY_BTAB:
            self.prev_tab()
        elif ch in KEY_TOGGLE_ALL:
            self.toggle_all_sessions()
        elif ord("1") <= ch <= ord(str(len(TABS))):
            self.goto_tab(ch - ord("1"))

    def on_enter(self, list_len: int):
        """Drill-down is wired by App (it knows what the current list actually
        contains); Nav just tracks that Enter happened via the stack push App does
        after calling this. Kept as a hook for readability/testability."""
        pass


class App:
    def __init__(self, conv: str, all_sessions: bool = False, writer=None):
        self.conv = conv
        self.nav = Nav()
        self.nav.all_sessions = all_sessions
        self.data = {}
        self.last_refresh = 0.0

        # -- shared scroll state: one Viewport per scrollable pane. List tabs and
        # drill-down frames get a Viewport lazily (keyed by identity below); the
        # Conversation tab gets one fixed Viewport since it is a single pane.
        self._list_viewports = {}
        self.conv_viewport = scroll_mod.Viewport()

        # -- conversation tab state
        self.conv_state = None
        self.composer = conv_mod.Composer(writer=writer)
        self._conv_session = None

        # -- color: exactly one accent pair + one error pair (pipeline.tui.palette),
        # registered once real curses is up in run() below; {} (both attrs degrade
        # to 0/A_NORMAL) until then, so views.py never needs a None check.
        self.palette = {"accent": 0, "error": 0}

        # -- mouse state
        self.mouse_mode = mouse_mod.MouseMode(captured=True)
        self.selection = mouse_mod.Selection()
        self._last_rows = []   # exact text painted this frame, one string per screen row
        self._last_w = 80
        self._last_body_h = 1
        self._clipboard_msg = ""

    # -- viewport lookup ----------------------------------------------------

    def list_viewport(self, key) -> scroll_mod.Viewport:
        """A stable Viewport for a given list/frame. `key` is the tab index for a
        top-level tab, or the drill-down frame dict itself (identity-keyed via
        id()) so pushing/popping the stack doesn't lose or leak scroll state."""
        ident = key if isinstance(key, int) else id(key)
        vp = self._list_viewports.get(ident)
        if vp is None:
            vp = scroll_mod.Viewport()
            self._list_viewports[ident] = vp
        return vp

    def active_viewport(self) -> scroll_mod.Viewport:
        frame = self.nav.top()
        if frame is not None:
            return self.list_viewport(frame)
        if self.nav.tab == CONVERSATION_TAB:
            return self.conv_viewport
        return self.list_viewport(self.nav.tab)

    # -- data -------------------------------------------------------------

    def refresh_data(self):
        conv, all_s = self.conv, self.nav.all_sessions
        self.data["pipelines"] = model.pipelines_tab(conv, all_s)
        self.data["dispatches"] = model.dispatches_tab(conv, all_s)
        self.data["files"] = model.files_tab(conv, all_s)
        self.data["conversation_text"] = model.conversation_stub_text(conv)
        try:
            self.data["status_bar"] = model.status_bar(conv, mouse_hint=self.mouse_mode.hint())
        except Exception as e:
            self.data["status_bar"] = {
                "agent": "n/a", "model": "n/a", "effort": "n/a", "context_fill": "n/a",
                "session_id": conv, "auth_expiry": "n/a", "mouse_mode": "n/a", "_error": str(e),
            }
        self.last_refresh = time.monotonic()
        self._ensure_conversation_attached()

    # -- conversation tab lifecycle ------------------------------------------

    def _ensure_conversation_attached(self):
        """(Re)open the conversation reader if the attached session changed, or
        open it for the first time. Cheap: only touches the DB when the session
        id actually differs from what we already have open."""
        if self._conv_session == self.conv and self.conv_state is not None:
            return
        if self.conv_state is not None:
            self.conv_state.close()
        self._conv_session = self.conv
        self.conv_state = conv_mod.ConversationState(self.conv, model.conversation_db_path())
        self.conv_state.open()
        self.conv_viewport.follow = True

    def conversation_tick(self):
        """Called every loop iteration: advance the chunked backlog load one chunk
        at a time (never a full blocking read), or poll for streamed/late rows
        once loaded. Returns True if it did meaningful work this tick (used to
        pick a fast spin interval while loading, so the progress line advances
        smoothly)."""
        cs = self.conv_state
        if cs is None:
            return False
        if cs.status == conv_mod.STATUS_LOADING:
            cs.load_more()
            self.conv_viewport.sync_follow(len(scroll_mod.wrap_lines(cs.lines(), self._last_w)), self._last_body_h)
            return True
        if cs.status == conv_mod.STATUS_READY:
            cs.poll()
        return False

    def current_tab_rows(self) -> list:
        key = ("pipelines", "dispatches", "files", "conversation_text")[self.nav.tab]
        val = self.data.get(key)
        if self.nav.tab == CONVERSATION_TAB:  # conversation is text, not a row list
            return []
        return val or []

    def current_list_len(self) -> int:
        frame = self.nav.top()
        if frame is None:
            return len(self.current_tab_rows())
        return len(frame.get("items") or [])

    # -- drill-down wiring: what Enter actually opens ----------------------

    def try_drill_down(self):
        frame = self.nav.top()
        idx = self.nav.cur_selection()
        if frame is None:
            if self.nav.tab != 0:
                return  # only Pipelines drills down in this phase
            rows = self.data.get("pipelines") or []
            if not (0 <= idx < len(rows)):
                return
            row = rows[idx]
            phases = sorted(row.get("phases", {}).items(), key=lambda kv: int(kv[0]))
            items = [{"label": f"phase {k}: {p.get('status')} role={p.get('role')}", "phase": k, "phase_row": p}
                     for k, p in phases]
            self.nav.push({"kind": "phases", "run": row["run"], "items": items})
        elif frame["kind"] == "phases":
            items = frame.get("items") or []
            if not (0 <= idx < len(items)):
                return
            phase_key = items[idx]["phase"]
            listing = model.phase_listing(frame["run"], phase_key)
            sub_items = listing["items"]
            self.nav.push({"kind": "items", "run": frame["run"], "phase": phase_key, "items": sub_items})
        elif frame["kind"] == "items":
            items = frame.get("items") or []
            if not (0 <= idx < len(items)):
                return
            entry = items[idx]
            # an item entry has one or more named files; pick the first present, in a
            # stable preference order, so Enter always opens *something* useful.
            for pref in ("result.json", "review.md", "brief.md", "transcript.jsonl", "meta.json"):
                if pref in entry:
                    self.nav.push({"kind": "item", "path": entry[pref], "items": []})
                    return
        # frame["kind"] == "item": nothing further to drill into.

    # -- the loop -----------------------------------------------------------

    def run(self, stdscr):
        curses.curs_set(0)
        self.palette = palette_mod.init_palette()
        # raw (not just cbreak, which curses.wrapper already set) disables ISIG so
        # Ctrl+C arrives as a normal byte (KEY_CTRL_C, handled below as copy-selection)
        # instead of the tty raising SIGINT -- otherwise Ctrl+C never reaches the app
        # at all, only ever a KeyboardInterrupt out of select.select().
        try:
            curses.raw()
        except curses.error:
            pass
        stdscr.nodelay(True)
        stdscr.keypad(True)
        if hasattr(curses, "mousemask"):
            curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        try:
            sys.stdout.write("\033[?1003h")
            sys.stdout.flush()
        except Exception:
            pass
        self.refresh_data()
        while not self.nav.quit:
            self.draw(stdscr)
            loading = self.conv_state is not None and self.conv_state.status == conv_mod.STATUS_LOADING
            poll_s = LOAD_SPIN_S if loading else POLL_S
            remaining = poll_s - (time.monotonic() - self.last_refresh)
            timeout = max(0.0, min(poll_s, remaining)) or poll_s
            try:
                ready, _, _ = select.select([sys.stdin], [], [], timeout)
            except (OSError, ValueError):
                ready = []
            if ready:
                for ch in _drain_keys(stdscr):
                    self._apply_key(ch, stdscr)
                self.draw(stdscr)  # every keystroke visible before the next wait
            if loading:
                self.conversation_tick()
            if time.monotonic() - self.last_refresh >= POLL_S:
                self.refresh_data()
                self.conversation_tick()

    def _apply_key(self, ch: int, stdscr=None):
        if ch == KEY_MOUSE and stdscr is not None:
            self._handle_mouse()
            return
        if ch == KEY_CTRL_C:
            self._copy_selection()
            return
        if ch in KEY_TOGGLE_MOUSE and not self._conversation_has_focus():
            self._toggle_mouse_mode()
            return
        if self._conversation_has_focus():
            if self._handle_conversation_key(ch):
                return
        if ch == KEY_PGUP:
            self._scroll_active(-1, page=True)
            return
        if ch == KEY_PGDN:
            self._scroll_active(1, page=True)
            return
        if ch == KEY_HOME:
            self._scroll_active(0, home=True)
            return
        if ch == KEY_END:
            self._scroll_active(0, end=True)
            return
        if ch in KEY_UP:
            vp = self.active_viewport()
            if self.nav.top() is None and self.nav.tab != CONVERSATION_TAB:
                # list tabs: Up/Down move the selection cursor (existing Nav
                # behaviour); the viewport keeps the cursor visible.
                self.nav.handle_key(ch, self.current_list_len())
                self.nav.clamp(self.current_list_len())
            else:
                vp.scroll(-1, self._pane_total(), self._last_body_h)
            return
        if ch in KEY_DOWN:
            if self.nav.top() is None and self.nav.tab != CONVERSATION_TAB:
                self.nav.handle_key(ch, self.current_list_len())
                self.nav.clamp(self.current_list_len())
            else:
                self.active_viewport().scroll(1, self._pane_total(), self._last_body_h)
            return
        list_len = self.current_list_len()
        if ch in KEY_ENTER:
            self.try_drill_down()
            return
        self.nav.handle_key(ch, list_len)
        self.nav.clamp(self.current_list_len())

    # -- conversation composer wiring ---------------------------------------

    def _conversation_has_focus(self) -> bool:
        return self.nav.tab == CONVERSATION_TAB and self.nav.top() is None

    def _handle_conversation_key(self, ch: int) -> bool:
        """Give the composer first crack at a key when the conversation tab is
        focused. Returns True if it consumed the key (so App._apply_key stops).
        Navigation keys (arrows, tab, esc, page/home/end, mouse) are NOT typing
        keys and fall through to the normal scroll/nav handling."""
        if ch in KEY_ENTER:
            sent = self.composer.submit()
            if sent is not None:
                self.conv_viewport.follow = True  # sending snaps to live
            return True
        if _is_typing_key(ch):
            if ch in KEY_BACKSPACE:
                self.composer.backspace()
            else:
                self.composer.type_char(chr(ch))
            return True
        return False

    # -- scroll helpers -------------------------------------------------------

    def _pane_total(self) -> int:
        lines = self._pane_lines()
        return len(scroll_mod.wrap_lines(lines, self._last_w))

    def _pane_lines(self) -> list:
        frame = self.nav.top()
        if frame is not None:
            if frame.get("kind") == "item":
                try:
                    return model.read_item(frame["path"]).splitlines() or [""]
                except Exception:
                    return [""]
            return [it.get("label", str(it)) for it in (frame.get("items") or [])]
        if self.nav.tab == CONVERSATION_TAB:
            cs = self.conv_state
            if cs is None or cs.status in (conv_mod.STATUS_ABSENT, conv_mod.STATUS_EMPTY):
                return []
            lines = cs.lines()
            progress = cs.progress_text()
            return (lines + [progress]) if progress else lines
        return [str(r) for r in self.current_tab_rows()]

    def _scroll_active(self, direction: int, page: bool = False, home: bool = False, end: bool = False):
        vp = self.active_viewport()
        total = self._pane_total()
        height = self._last_body_h
        if home:
            vp.home(total, height)
        elif end:
            vp.end(total, height)
        elif page:
            if direction < 0:
                vp.page_up(total, height)
            else:
                vp.page_down(total, height)

    def _handle_mouse(self):
        try:
            _, mx, my, _, bstate = curses.getmouse()
        except curses.error:
            return
        if not self.mouse_mode.captured:
            return  # native-terminal selection: the TUI does not touch it
        wheel_up = bool(bstate & getattr(curses, "BUTTON4_PRESSED", 0))
        wheel_down = bool(bstate & getattr(curses, "BUTTON5_PRESSED", 0))
        if wheel_up or wheel_down:
            vp = self.active_viewport()
            delta = -WHEEL_ROWS if wheel_up else WHEEL_ROWS
            vp.scroll(delta, self._pane_total(), self._last_body_h)
            return
        pressed = bool(bstate & getattr(curses, "BUTTON1_PRESSED", 0))
        released = bool(bstate & getattr(curses, "BUTTON1_RELEASED", 0))
        clicked = bool(bstate & getattr(curses, "BUTTON1_CLICKED", 0))
        if pressed or clicked:
            self.selection.begin(my, mx)
        elif released:
            self.selection.extend(my, mx)
            self.selection.end()
        else:
            self.selection.extend(my, mx)

    def _toggle_mouse_mode(self):
        captured = self.mouse_mode.toggle()
        try:
            if captured:
                curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
            else:
                curses.mousemask(0)
        except curses.error:
            pass

    def _copy_selection(self):
        text = self.selection.text(self._last_rows)
        try:
            sys.stdout.write(mouse_mod.osc52_payload(text))
            sys.stdout.flush()
        except Exception:
            pass
        self._clipboard_msg = f"copied {len(text)} chars" if text else ""

    def draw(self, stdscr):
        views.draw(stdscr, self)
        self._capture_screen_rows(stdscr)

    def _capture_screen_rows(self, stdscr):
        """Snapshot the exact text of every screen row after this paint, so a
        click-drag selection extracts precisely what the user sees (the SAME
        wrapped text the viewport just drew), not a re-derivation that could
        drift from it."""
        h, w = stdscr.getmaxyx()
        rows = []
        for y in range(h):
            try:
                row = stdscr.instr(y, 0, w - 1).decode("utf-8", "replace").rstrip()
            except curses.error:
                row = ""
            rows.append(row)
        self._last_rows = rows


def _drain_keys(stdscr) -> list:
    """Exhaustively drain every keystroke already delivered to the terminal (a
    paste, a fast burst) before the next repaint -- never leaves one behind for a
    later tick."""
    keys = []
    while True:
        ch = stdscr.getch()
        if ch == -1:
            break
        keys.append(ch)
    return keys


def run_app(stdscr, conv: str, all_sessions: bool = False):
    app = App(conv, all_sessions=all_sessions)
    app.run(stdscr)
    return app


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline tui")
    ap.add_argument("--conv")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    conv = model.resolve_conversation(a.conv)
    curses.wrapper(lambda stdscr: run_app(stdscr, conv, all_sessions=a.all))
    return 0


if __name__ == "__main__":
    sys.exit(main())
