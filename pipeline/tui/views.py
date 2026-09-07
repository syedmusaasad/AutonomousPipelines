"""curses rendering for the four tabs and the drill-down stack. Pure rendering:
takes an App (data + nav state already computed) and paints. No data fetching
here -- that lives in app.py/model.py so this module stays a thin view layer.

Every scrollable pane (list tabs, drill-down listings, the item viewer, and the
Conversation tab) goes through the SAME scroll.Viewport + wrap machinery via
draw_scrollable() below, so follow-tail, selection-visibility, and wheel scroll
all agree on where a row lives on screen -- there is exactly one wrapping/paging
implementation, not four slightly different ones.
"""

import curses

from . import scroll
from . import conversation as conv_mod

FOOTER_STATUS_ROWS = 1
FOOTER_HINT_ROWS = 1


def draw(stdscr, app):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    _draw_tabs(stdscr, app.nav, w, palette=app.palette)
    body_top = 2
    from .app import CONVERSATION_TAB
    composer_rows = 1 if app.nav.tab == CONVERSATION_TAB and app.nav.top() is None else 0
    footer_h = FOOTER_STATUS_ROWS + FOOTER_HINT_ROWS + composer_rows
    body_h = max(0, h - body_top - footer_h)
    frame = app.nav.top()
    if frame is None:
        _draw_tab_body(stdscr, app, body_top, body_h, w)
    else:
        _draw_drilldown(stdscr, app, frame, body_top, body_h, w)
    hint_y = h - FOOTER_STATUS_ROWS - FOOTER_HINT_ROWS - composer_rows
    if composer_rows:
        _draw_composer(stdscr, app, hint_y, w)
        hint_y += 1
    _draw_hint_bar(stdscr, app, hint_y, w)
    _draw_status_bar(stdscr, app, h - 1, w)
    _draw_selection_highlight(stdscr, app.selection, h, w)
    # remember the geometry app.py needs to translate keys/wheel into the SAME
    # wrapping math this frame used, so scroll and selection never disagree.
    app._last_w = w
    app._last_body_h = body_h
    stdscr.noutrefresh()
    curses.doupdate()


def _safe_addstr(stdscr, y, x, text, attr=0):
    h, w = stdscr.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    text = text[: max(0, w - x - 1)]
    try:
        stdscr.addstr(y, x, text, attr)
    except curses.error:
        pass


def _draw_tabs(stdscr, nav, w, palette=None):
    from .app import TABS
    palette = palette or {}
    x = 0
    for i, name in enumerate(TABS):
        label = f" {i + 1}:{name} "
        attr = curses.A_REVERSE | palette.get("accent", 0) if i == nav.tab else curses.A_NORMAL
        _safe_addstr(stdscr, 0, x, label, attr)
        x += len(label)
    scope = "ALL SESSIONS" if nav.all_sessions else "this conversation"
    _safe_addstr(stdscr, 0, max(x + 1, w - len(scope) - 1), scope, curses.A_DIM if hasattr(curses, "A_DIM") else 0)
    _safe_addstr(stdscr, 1, 0, "-" * max(0, w - 1))


def _draw_tab_body(stdscr, app, top, height, w):
    tab = app.nav.tab
    if tab == 0:
        rows = app.data.get("pipelines") or []
        lines = [render_pipeline_line(r) for r in rows]
        attrs = [curses.A_BOLD if r["state"] == "gate-waiting" or r["state"].startswith("stopped") else 0
                 for r in rows]
        draw_scrollable(stdscr, top, height, w, lines, app.list_viewport(tab),
                         sel_idx=app.nav.cur_selection() if rows else None, attrs=attrs,
                         empty_msg="(no plan runs launched by this conversation)")
    elif tab == 1:
        rows = app.data.get("dispatches") or []
        lines = [render_dispatch_line(r) for r in rows]
        draw_scrollable(stdscr, top, height, w, lines, app.list_viewport(tab),
                         sel_idx=app.nav.cur_selection() if rows else None,
                         empty_msg="(no quick dispatches launched by this conversation)")
    elif tab == 2:
        rows = app.data.get("files") or []
        lines = [render_file_line(r) for r in rows]
        attrs = [app.palette["error"] if not r["exists"] else 0 for r in rows]
        draw_scrollable(stdscr, top, height, w, lines, app.list_viewport(tab),
                         sel_idx=app.nav.cur_selection() if rows else None, attrs=attrs,
                         empty_msg="(no deliverables registered yet)")
    elif tab == 3:
        _draw_conversation(stdscr, app, top, height, w)


def render_pipeline_line(r: dict) -> str:
    line = f"{r['run']:<28} {r['name']:<24} {r['phase_progress']:>6}  {r['state']}"
    if r.get("reason"):
        line += f"  ({r['reason'][:40]})"
    return line


def render_dispatch_line(r: dict) -> str:
    return (f"{r['run']:<28} {str(r['role']):<16} {r['state']:<16} "
            f"wall={r['wall_s']:>7.1f}s cost=${r['cost']:.4f} tok={r['tokens_total']}")


def render_file_line(r: dict) -> str:
    mark = "" if r["exists"] else " (missing!)"
    return f"{r['run']:<28} phase={r['phase']:<4} {r['role']:<14} {r['path']}{mark}"


def draw_scrollable(stdscr, top, height, w, lines, viewport, sel_idx=None, attrs=None, empty_msg=None):
    """The one scrollable-pane renderer every tab/drill-down/conversation view
    shares: wraps `lines` (logical lines) at `w`, lets `viewport` (a
    scroll.Viewport) settle -- repinning to the tail if it is following,
    otherwise just clamping -- then paints exactly the visible slice. `sel_idx`
    (a logical-line index) draws reverse-video on every visual row that logical
    line wrapped into; `attrs` is a parallel extra-attribute list per logical
    line (e.g. bold for a loud pipeline row)."""
    if height <= 0:
        return
    if not lines:
        if empty_msg:
            _safe_addstr(stdscr, top, 0, empty_msg)
        viewport.clamp(0, height)
        return
    visual, index = scroll.wrap_lines_with_index(lines, w)
    viewport.sync_follow(len(visual), height)
    start, end = viewport.visible_range(len(visual), height)
    for screen_row, vis_idx in enumerate(range(start, end)):
        logical_i = index[vis_idx]
        text = visual[vis_idx]
        attr = 0
        if attrs and logical_i < len(attrs):
            attr |= attrs[logical_i]
        if sel_idx is not None and logical_i == sel_idx:
            attr |= curses.A_REVERSE
        _safe_addstr(stdscr, top + screen_row, 0, text, attr)


def _draw_conversation(stdscr, app, top, height, w):
    cs = app.conv_state
    if cs is None:
        _safe_addstr(stdscr, top, 0, "no conversation attached")
        return
    if cs.status in (conv_mod.STATUS_ABSENT, conv_mod.STATUS_EMPTY):
        _safe_addstr(stdscr, top, 0, cs.banner())
        return
    lines = cs.lines()
    progress = cs.progress_text()
    if progress:
        lines = [progress] + lines
    draw_scrollable(stdscr, top, height, w, lines, app.conv_viewport)


def _draw_composer(stdscr, app, y, w):
    composer = app.composer
    left = f"> {composer.buffer}"
    hint = composer.hint()
    _safe_addstr(stdscr, y, 0, left)
    _safe_addstr(stdscr, y, max(len(left) + 2, w - len(hint) - 1), hint,
                 curses.A_DIM if hasattr(curses, "A_DIM") else 0)


def _draw_drilldown(stdscr, app, frame, top, height, w):
    kind = frame.get("kind")
    if kind == "item":
        content = None
        try:
            from . import model
            content = model.read_item(frame["path"])
        except Exception as e:
            content = f"(error reading item: {e})"
        _safe_addstr(stdscr, top, 0, f"-- {frame['path']} --", curses.A_BOLD)
        lines = content.splitlines() or [""]
        draw_scrollable(stdscr, top + 1, max(0, height - 1), w, lines, app.list_viewport(frame))
        return
    items = frame.get("items") or []
    header = {"phases": "Phases", "items": "Phase items"}.get(kind, kind)
    _safe_addstr(stdscr, top, 0, f"-- {header} (run {frame.get('run', '')}) --", curses.A_BOLD)
    lines = [it.get("label", str(it)) for it in items]
    sel = frame.get("selection", 0)
    draw_scrollable(stdscr, top + 1, max(0, height - 1), w, lines, app.list_viewport(frame),
                     sel_idx=sel if items else None)


def _draw_hint_bar(stdscr, app, y, w):
    from .app import CONVERSATION_TAB
    vp = app.active_viewport()
    scroll_state = "live" if getattr(vp, "follow", False) else f"scroll@{vp.offset}"
    parts = [f"scroll:{scroll_state}", app.mouse_mode.hint()]
    if app.nav.tab == CONVERSATION_TAB and app.nav.top() is None:
        parts.append(app.composer.hint())
    text = "  |  ".join(parts)
    _safe_addstr(stdscr, y, 0, text, curses.A_DIM if hasattr(curses, "A_DIM") else 0)


def _draw_status_bar(stdscr, app, y, w):
    bar = app.data.get("status_bar") or {}
    text = (f"agent={bar.get('agent', 'n/a')} model={bar.get('model', 'n/a')}"
            f"@{bar.get('effort', 'n/a')} ctx={bar.get('context_fill', 'n/a')} "
            f"session={bar.get('session_id', 'n/a')} auth={bar.get('auth_expiry', 'n/a')} "
            f"mouse={bar.get('mouse_mode', 'n/a')}")
    _safe_addstr(stdscr, y, 0, text, curses.A_REVERSE)


def _draw_selection_highlight(stdscr, sel, h, w):
    """Overlay reverse-video on a click-drag selection's screen cells, on top of
    whatever was just painted -- the SAME (row, col) coordinates the mouse
    events used, since both read directly off the real terminal geometry."""
    if sel is None or sel.anchor is None or sel.cursor is None:
        return
    (r0, c0), (r1, c1) = sel.ordered()
    r0 = max(0, r0)
    r1 = min(h - 1, r1)
    for r in range(r0, r1 + 1):
        if r0 == r1:
            lo, hi = min(c0, c1), max(c0, c1)
        elif r == r0:
            lo, hi = c0, w - 1
        elif r == r1:
            lo, hi = 0, c1
        else:
            lo, hi = 0, w - 1
        lo = max(0, lo)
        hi = min(w - 1, hi)
        length = hi - lo + 1
        if length > 0:
            try:
                stdscr.chgat(r, lo, length, curses.A_REVERSE)
            except curses.error:
                pass
