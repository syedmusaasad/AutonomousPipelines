"""Mouse support: click-drag selection over on-screen rows, and the OSC52 clipboard
payload builder. Pure logic only -- the curses mousemask wiring and the actual write
to the terminal live in app.py/views.py; this module is what the phase-3 brief calls
out for unit testing (selection buffer, OSC52 payload shape).

OSC52 is a raw terminal escape sequence (works over SSH, no clipboard daemon needed):
    ESC ] 52 ; c ; <base64(text)> BEL
"""

import base64

OSC52_START = "\x1b]52;c;"
OSC52_END = "\x07"
MAX_OSC52_BYTES = 74994  # a conservative payload ceiling many terminals honor


def osc52_payload(text: str) -> str:
    """The exact bytes to write to the terminal to set the clipboard to `text` via
    OSC52. Truncates the base64 payload (not the escape framing) if it would exceed
    a terminal-safe ceiling, so a huge selection never wedges the terminal."""
    encoded = base64.b64encode((text or "").encode("utf-8", "replace")).decode("ascii")
    if len(encoded) > MAX_OSC52_BYTES:
        encoded = encoded[:MAX_OSC52_BYTES]
    return f"{OSC52_START}{encoded}{OSC52_END}"


class Selection:
    """A click-drag text selection over the CURRENTLY PAINTED screen rows (list of
    strings, one per terminal row, already the exact text drawn this frame). Anchor
    is where the drag started; cursor is where it currently is (or ended). Order is
    normalized so dragging bottom-to-top or right-to-left still selects sensibly.
    """

    def __init__(self):
        self.active = False
        self.anchor = None   # (row, col)
        self.cursor = None   # (row, col)

    def begin(self, row: int, col: int):
        self.active = True
        self.anchor = (row, col)
        self.cursor = (row, col)

    def extend(self, row: int, col: int):
        if not self.active:
            self.begin(row, col)
            return
        self.cursor = (row, col)

    def end(self):
        self.active = False

    def clear(self):
        self.active = False
        self.anchor = None
        self.cursor = None

    def is_empty(self) -> bool:
        return self.anchor is None or self.cursor is None or self.anchor == self.cursor

    def ordered(self):
        """(start, end) with start <= end in (row, col) reading order."""
        a, b = self.anchor, self.cursor
        return (a, b) if a <= b else (b, a)

    def text(self, rows: list) -> str:
        """Extract the selected text from `rows` (one string per screen row). A
        single-row selection is a column slice; a multi-row selection takes the
        tail of the first row, whole middle rows, and the head of the last row,
        joined with newlines -- ordinary terminal drag-select semantics."""
        if self.is_empty():
            return ""
        (r0, c0), (r1, c1) = self.ordered()
        r1 = min(r1, len(rows) - 1)
        r0 = max(0, min(r0, len(rows) - 1))
        if r0 == r1:
            line = rows[r0] if r0 < len(rows) else ""
            lo, hi = min(c0, c1), max(c0, c1)
            return line[lo:hi + 1]
        out = []
        first = rows[r0] if r0 < len(rows) else ""
        out.append(first[c0:])
        for r in range(r0 + 1, r1):
            out.append(rows[r] if r < len(rows) else "")
        last = rows[r1] if r1 < len(rows) else ""
        out.append(last[:c1 + 1])
        return "\n".join(out)


class MouseMode:
    """Toggle between mouse-capture (wheel scroll + drag-select handled by the TUI)
    and native-terminal selection (mouse capture off, so the user's terminal emulator
    does its own selection/copy). `m` flips this; the hint line names both states."""

    def __init__(self, captured: bool = True):
        self.captured = captured

    def toggle(self):
        self.captured = not self.captured
        return self.captured

    def hint(self) -> str:
        if self.captured:
            return "mouse:pipeline (m: native-terminal select)"
        return "mouse:native-terminal (m: pipeline select)"
