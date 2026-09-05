"""curses rendering for the four tabs and the drill-down stack. Pure rendering:
takes an App (data + nav state already computed) and paints. No data fetching
here -- that lives in app.py/model.py so this module stays a thin view layer."""

import curses


def draw(stdscr, app):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    _draw_tabs(stdscr, app.nav, w)
    body_top = 2
    body_h = max(0, h - body_top - 1)
    frame = app.nav.top()
    if frame is None:
        _draw_tab_body(stdscr, app, body_top, body_h, w)
    else:
        _draw_drilldown(stdscr, app, frame, body_top, body_h, w)
    _draw_status_bar(stdscr, app, h - 1, w)
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


def _draw_tabs(stdscr, nav, w):
    from .app import TABS
    x = 0
    for i, name in enumerate(TABS):
        label = f" {i + 1}:{name} "
        attr = curses.A_REVERSE if i == nav.tab else curses.A_NORMAL
        _safe_addstr(stdscr, 0, x, label, attr)
        x += len(label)
    scope = "ALL SESSIONS" if nav.all_sessions else "this conversation"
    _safe_addstr(stdscr, 0, max(x + 1, w - len(scope) - 1), scope, curses.A_DIM if hasattr(curses, "A_DIM") else 0)
    _safe_addstr(stdscr, 1, 0, "-" * max(0, w - 1))


def _draw_tab_body(stdscr, app, top, height, w):
    tab = app.nav.tab
    if tab == 0:
        _draw_pipelines(stdscr, app, top, height, w)
    elif tab == 1:
        _draw_dispatches(stdscr, app, top, height, w)
    elif tab == 2:
        _draw_files(stdscr, app, top, height, w)
    elif tab == 3:
        _draw_conversation(stdscr, app, top, height, w)


def _row_attr(selected: bool, loud: bool = False) -> int:
    attr = curses.A_REVERSE if selected else 0
    if loud:
        attr |= curses.A_BOLD
    return attr


def _draw_pipelines(stdscr, app, top, height, w):
    rows = app.data.get("pipelines") or []
    sel = app.nav.cur_selection()
    if not rows:
        _safe_addstr(stdscr, top, 0, "(no plan runs launched by this conversation)")
        return
    for i, r in enumerate(rows[:height]):
        y = top + i
        loud = r["state"] == "gate-waiting" or r["state"].startswith("stopped")
        line = f"{r['run']:<28} {r['name']:<24} {r['phase_progress']:>6}  {r['state']}"
        if r["reason"]:
            line += f"  ({r['reason'][:40]})"
        _safe_addstr(stdscr, y, 0, line, _row_attr(i == sel, loud))


def _draw_dispatches(stdscr, app, top, height, w):
    rows = app.data.get("dispatches") or []
    sel = app.nav.cur_selection()
    if not rows:
        _safe_addstr(stdscr, top, 0, "(no quick dispatches launched by this conversation)")
        return
    for i, r in enumerate(rows[:height]):
        y = top + i
        line = (f"{r['run']:<28} {str(r['role']):<16} {r['state']:<16} "
                f"wall={r['wall_s']:>7.1f}s cost=${r['cost']:.4f} tok={r['tokens_total']}")
        _safe_addstr(stdscr, y, 0, line, _row_attr(i == sel))


def _draw_files(stdscr, app, top, height, w):
    rows = app.data.get("files") or []
    sel = app.nav.cur_selection()
    if not rows:
        _safe_addstr(stdscr, top, 0, "(no deliverables registered yet)")
        return
    for i, r in enumerate(rows[:height]):
        y = top + i
        mark = "" if r["exists"] else " (missing!)"
        line = f"{r['run']:<28} phase={r['phase']:<4} {r['role']:<14} {r['path']}{mark}"
        _safe_addstr(stdscr, y, 0, line, _row_attr(i == sel))


def _draw_conversation(stdscr, app, top, height, w):
    text = app.data.get("conversation_text") or ""
    _safe_addstr(stdscr, top, 0, text)


def _draw_drilldown(stdscr, app, frame, top, height, w):
    sel = frame.get("selection", 0)
    kind = frame.get("kind")
    if kind == "item":
        content = None
        try:
            from . import model
            content = model.read_item(frame["path"])
        except Exception as e:
            content = f"(error reading item: {e})"
        _safe_addstr(stdscr, top, 0, f"-- {frame['path']} --", curses.A_BOLD)
        for i, line in enumerate(content.splitlines()[:height - 1]):
            _safe_addstr(stdscr, top + 1 + i, 0, line)
        return
    items = frame.get("items") or []
    header = {"phases": "Phases", "items": "Phase items"}.get(kind, kind)
    _safe_addstr(stdscr, top, 0, f"-- {header} (run {frame.get('run', '')}) --", curses.A_BOLD)
    for i, it in enumerate(items[: height - 1]):
        y = top + 1 + i
        _safe_addstr(stdscr, y, 0, it.get("label", str(it)), _row_attr(i == sel))


def _draw_status_bar(stdscr, app, y, w):
    bar = app.data.get("status_bar") or {}
    text = (f"agent={bar.get('agent', 'n/a')} model={bar.get('model', 'n/a')}"
            f"@{bar.get('effort', 'n/a')} ctx={bar.get('context_fill', 'n/a')} "
            f"session={bar.get('session_id', 'n/a')} auth={bar.get('auth_expiry', 'n/a')} "
            f"mouse={bar.get('mouse_mode', 'n/a')}")
    _safe_addstr(stdscr, y, 0, text, curses.A_REVERSE)
