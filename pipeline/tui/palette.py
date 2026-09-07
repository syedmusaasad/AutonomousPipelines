"""Exactly two named curses color pairs -- accent and error -- registered once at
app startup via init_palette(). Every view.py callsite that wants a color goes
through the returned dict (app.palette["accent"] / app.palette["error"]) instead
of touching curses.init_pair/curses.color_pair directly, so "the app registers
exactly one accent pair and one error pair" stays true no matter how many more
views are added later.

Background is left at the terminal's own default (curses.use_default_colors(),
bg=-1) -- this TUI never forces a background color over whatever black/dark theme
the operator's real terminal already has; init_pair falls back to explicit
COLOR_BLACK only if use_default_colors() itself is unsupported.
"""

import curses

ACCENT_PAIR_NUM = 1
ERROR_PAIR_NUM = 2


def init_palette() -> dict:
    """Register exactly one accent pair (cyan-on-default) and one error pair
    (red-on-default). Degrades to plain attributes (0 == curses.A_NORMAL) for
    every failure mode a real terminal can hit -- no color support, start_color()
    unsupported, use_default_colors() unsupported -- so callers can always OR the
    result into an attr without checking has_colors() themselves."""
    degraded = {"accent": 0, "error": 0}
    if not hasattr(curses, "has_colors") or not curses.has_colors():
        return degraded
    try:
        curses.start_color()
    except curses.error:
        return degraded
    bg = -1
    try:
        curses.use_default_colors()
    except curses.error:
        bg = curses.COLOR_BLACK
    try:
        curses.init_pair(ACCENT_PAIR_NUM, curses.COLOR_CYAN, bg)
        curses.init_pair(ERROR_PAIR_NUM, curses.COLOR_RED, bg)
    except curses.error:
        return degraded
    return {"accent": curses.color_pair(ACCENT_PAIR_NUM), "error": curses.color_pair(ERROR_PAIR_NUM)}
