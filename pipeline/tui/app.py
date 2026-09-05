"""pipeline.tui.app: the curses event loop and navigation state.

Split deliberately: `Nav` holds pure navigation/drill-down logic with no curses
import, so it is unit-testable without a terminal; `App` wraps a `Nav` with the
data cache (read fresh from `pipeline.tui.model` every poll) and drives curses.

Event loop contract (hard requirement, tested end-to-end by the phase-5 probe):
  - select/poll over stdin AND the data-poll interval; on wake, drain ALL pending
    keystrokes exhaustively before repainting.
  - data arrival (poll interval elapses) repaints without any input.
  - every keystroke is applied before the next repaint; no batching behind a tick.
"""

import argparse
import curses
import os
import select
import sys
import time

from . import model, views

POLL_S = float(os.environ.get("PIPELINE_TUI_POLL_S", "0.5"))

TABS = ("Pipelines", "Dispatches", "Files", "Conversation")

KEY_QUIT = frozenset({ord("q"), ord("Q")})
KEY_UP = frozenset({curses.KEY_UP if hasattr(curses, "KEY_UP") else 259, ord("k")})
KEY_DOWN = frozenset({curses.KEY_DOWN if hasattr(curses, "KEY_DOWN") else 258, ord("j")})
KEY_ENTER = frozenset({10, 13, curses.KEY_ENTER if hasattr(curses, "KEY_ENTER") else 343})
KEY_ESC = 27
KEY_TAB = 9
KEY_BTAB = curses.KEY_BTAB if hasattr(curses, "KEY_BTAB") else 353
KEY_TOGGLE_ALL = frozenset({ord("a"), ord("A")})


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
    def __init__(self, conv: str, all_sessions: bool = False):
        self.conv = conv
        self.nav = Nav()
        self.nav.all_sessions = all_sessions
        self.data = {}
        self.last_refresh = 0.0

    # -- data -------------------------------------------------------------

    def refresh_data(self):
        conv, all_s = self.conv, self.nav.all_sessions
        self.data["pipelines"] = model.pipelines_tab(conv, all_s)
        self.data["dispatches"] = model.dispatches_tab(conv, all_s)
        self.data["files"] = model.files_tab(conv, all_s)
        self.data["conversation_text"] = model.conversation_stub_text(conv)
        try:
            self.data["status_bar"] = model.status_bar(conv)
        except Exception as e:
            self.data["status_bar"] = {
                "agent": "n/a", "model": "n/a", "effort": "n/a", "context_fill": "n/a",
                "session_id": conv, "auth_expiry": "n/a", "mouse_mode": "n/a", "_error": str(e),
            }
        self.last_refresh = time.monotonic()

    def current_tab_rows(self) -> list:
        key = ("pipelines", "dispatches", "files", "conversation_text")[self.nav.tab]
        val = self.data.get(key)
        if self.nav.tab == 3:  # conversation is text, not a row list
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
        stdscr.nodelay(True)
        stdscr.keypad(True)
        self.refresh_data()
        while not self.nav.quit:
            self.draw(stdscr)
            remaining = POLL_S - (time.monotonic() - self.last_refresh)
            timeout = max(0.0, min(POLL_S, remaining)) or POLL_S
            try:
                ready, _, _ = select.select([sys.stdin], [], [], timeout)
            except (OSError, ValueError):
                ready = []
            if ready:
                for ch in _drain_keys(stdscr):
                    self._apply_key(ch)
                self.draw(stdscr)  # every keystroke visible before the next wait
            if time.monotonic() - self.last_refresh >= POLL_S:
                self.refresh_data()

    def _apply_key(self, ch: int):
        list_len = self.current_list_len()
        if ch in KEY_ENTER:
            self.try_drill_down()
            return
        self.nav.handle_key(ch, list_len)
        self.nav.clamp(self.current_list_len())

    def draw(self, stdscr):
        views.draw(stdscr, self)


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
