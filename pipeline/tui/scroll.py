"""Scroll model shared by EVERY tab: one Viewport per scrollable pane, offset+limit
over VISUAL rows (post-wrap), so follow-tail always pins the LAST WRAPPED ROW -- not
the last logical line, which could itself wrap across several screen rows.

No curses import here on purpose: this is the pure part the phase-3 brief calls out
for unit testing (wrap calc + follow rules), independent of any terminal.
"""

import textwrap


def wrap_row(text: str, width: int) -> list:
    """Wrap one logical line of text to `width` columns. An empty logical line still
    produces one (empty) visual row, so blank lines never collapse out of the
    display."""
    width = max(1, width)
    out = []
    for physical in (text or "").split("\n"):
        wrapped = textwrap.wrap(physical, width, break_long_words=True, break_on_hyphens=False)
        out.extend(wrapped or [""])
    return out or [""]


def wrap_lines(lines: list, width: int) -> list:
    """Flatten `lines` (logical lines) into the ordered list of visual rows at
    `width` -- this is what a Viewport actually scrolls over."""
    out = []
    for line in lines:
        out.extend(wrap_row(line, width))
    return out


def wrap_lines_with_index(lines: list, width: int) -> tuple:
    """Like wrap_lines, but also returns a parallel list mapping each visual row
    to the index of the logical line it came from. Lets a renderer apply a
    per-logical-line attribute (e.g. a "loud" bold flag, or a selection
    highlight) to every visual row that logical line wrapped into -- the same
    per-logical-row bookkeeping follow-tail and selection both need, kept in one
    place so they never disagree."""
    visual = []
    index = []
    for i, line in enumerate(lines):
        rows = wrap_row(line, width)
        visual.extend(rows)
        index.extend([i] * len(rows))
    return visual, index


def visual_bounds(lines: list, width: int, logical_idx: int) -> tuple:
    """(start, end) visual-row range (end exclusive) that logical line
    `logical_idx` occupies once wrapped at `width`. Used to keep a selected
    logical row (e.g. the cursor in a list tab) actually on screen when the
    viewport scrolls -- the SAME wrapping math follow-tail uses, so a selection
    and a streaming tail never disagree about where a row lives."""
    start = 0
    for i, line in enumerate(lines):
        n = len(wrap_row(line, width))
        if i == logical_idx:
            return start, start + n
        start += n
    return start, start


class Viewport:
    """offset over visual rows, with a `follow` flag: while True the viewport repins
    to the tail whenever content grows (streaming); any explicit upward scroll (Up,
    PgUp, Home) clears follow, and scrolling back down to the bottom edge re-enters
    it. `offset` is always clamped into [0, max(0, total-height)]."""

    def __init__(self, follow: bool = True):
        self.offset = 0
        self.follow = follow

    def max_offset(self, total: int, height: int) -> int:
        return max(0, total - max(1, height))

    def clamp(self, total: int, height: int):
        self.offset = max(0, min(self.offset, self.max_offset(total, height)))

    def sync_follow(self, total: int, height: int):
        """Call whenever content size may have grown (a streaming tick). If still
        following, repin to the tail; otherwise just re-clamp so a shrinking list
        never leaves the offset out of range."""
        if self.follow:
            self.offset = self.max_offset(total, height)
        else:
            self.clamp(total, height)

    def scroll(self, delta: int, total: int, height: int):
        """Move by `delta` visual rows (negative = up). Leaving the bottom edge going
        up clears follow; landing back on the bottom edge re-enters it."""
        mx = self.max_offset(total, height)
        self.offset = max(0, min(mx, self.offset + delta))
        self.follow = self.offset >= mx

    def page_up(self, total: int, height: int):
        self.scroll(-max(1, height - 1), total, height)
        self.follow = False

    def page_down(self, total: int, height: int):
        self.scroll(max(1, height - 1), total, height)

    def home(self, total: int, height: int):
        self.offset = 0
        self.follow = False

    def end(self, total: int, height: int):
        self.offset = self.max_offset(total, height)
        self.follow = True

    def visible_range(self, total: int, height: int) -> tuple:
        """(start, end) indices into the visual-row list to paint this frame. Clamps
        first so a shrinking list (e.g. after a filter change) never reads out of
        range."""
        self.clamp(total, height)
        return self.offset, min(total, self.offset + height)

    def is_at_bottom(self, total: int, height: int) -> bool:
        return self.offset >= self.max_offset(total, height)

    def ensure_visible(self, start: int, end: int, height: int):
        """Move offset (without touching `follow`) so the visual-row range
        [start, end) -- e.g. a selected logical line's wrapped rows from
        visual_bounds() -- is fully on screen, moving as little as possible.
        Used to keep a list tab's selection cursor visible while the list
        itself may be following or scrolled elsewhere."""
        if start < self.offset:
            self.offset = start
        elif end > self.offset + height:
            self.offset = max(0, end - height)
