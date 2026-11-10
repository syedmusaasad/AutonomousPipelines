"""Browser bridge: read pages through the operator's own signed-in Chrome.

The operator's Mac runs Chrome with a dedicated profile and remote debugging bound to
its localhost; a reverse SSH tunnel (mac/browser-bridge.sh) makes that port reachable
on this machine's localhost. Workers read a page with `pipeline browser fetch <url>`.

Guards: only allowlisted domains (registry `browser.allow`), only roles holding the
`browser` tool grant, one navigation at a time with a pause between them and a daily
cap per site. Nothing here hides automation or answers a challenge: a CAPTCHA or
sign-in wall is reported so the operator can clear it in the bridge window.

Standard library only (a minimal WebSocket client speaks the DevTools protocol)."""

import base64
import datetime
import fcntl
import json
import os
import random
import socket
import struct
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from . import paths

DEFAULTS = {"endpoint": "http://127.0.0.1:19222", "allow": [], "min_interval_s": 8, "daily_max": 150}
EXIT_OK, EXIT_ERROR, EXIT_REFUSED, EXIT_OPERATOR = 0, 1, 2, 3
LOAD_GRACE_S = 8  # how long to wait for the full load event once the document is in


class BrowserError(Exception):
    def __init__(self, msg: str, code: int = EXIT_ERROR):
        super().__init__(msg)
        self.code = code


def config(reg: dict) -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(reg.get("browser") or {})
    if os.environ.get("PIPELINE_BROWSER_ENDPOINT"):
        cfg["endpoint"] = os.environ["PIPELINE_BROWSER_ENDPOINT"]
    cfg["endpoint"] = cfg["endpoint"].rstrip("/")
    return cfg


def validate(reg: dict) -> None:
    """Registry check: endpoint must be loopback, allow must be bare domains."""
    b = reg.get("browser")
    if b is None:
        return
    host = urlparse(b.get("endpoint", DEFAULTS["endpoint"])).hostname
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError(f"browser.endpoint must be loopback (the bridge is an SSH tunnel), got {host!r}")
    for d in b.get("allow", []):
        if not isinstance(d, str) or "/" in d or ":" in d or d.startswith(".") or "." not in d:
            raise ValueError(f"browser.allow entries are bare domains like example.com, got {d!r}")


def host_allowed(url: str, allow: list) -> bool:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    host = u.hostname.lower().rstrip(".")
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in allow)


def role_allowed(reg: dict, role: str = None) -> bool:
    """Workers need the `browser` tool grant. The operator's own shell (no role) may use it."""
    role = role if role is not None else os.environ.get("PIPELINE_ROLE")
    if not role:
        return True
    return bool(((reg.get("roles") or {}).get(role) or {}).get("tools", {}).get("browser"))


# ---- pacing: one navigation at a time, spaced out, capped per day ------------

def pace(host: str, cfg: dict, state: Path = None, sleep=time.sleep, now=time.time) -> float:
    """Blocks until this host may be visited again; returns seconds waited. Raises
    BrowserError(EXIT_REFUSED) when the host's daily cap is used up."""
    state = state or (paths.state_dir() / "browser-pace.json")
    state.parent.mkdir(parents=True, exist_ok=True)
    with open(str(state) + ".lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # held through the wait: callers queue up
        try:
            data = json.loads(state.read_text())
        except (OSError, ValueError):
            data = {}
        today = datetime.datetime.fromtimestamp(now(), datetime.timezone.utc).strftime("%Y-%m-%d")
        h = data.get(host) or {}
        if h.get("day") != today:
            h = {"day": today, "count": 0, "last": h.get("last", 0)}
        if h["count"] >= int(cfg["daily_max"]):
            raise BrowserError(f"daily cap reached for {host} ({cfg['daily_max']} pages); try tomorrow or raise "
                               f"browser.daily_max in roles/registry.json", EXIT_REFUSED)
        gap = float(cfg["min_interval_s"])
        wait = max(0.0, h.get("last", 0) + gap + random.uniform(0, gap / 2) - now()) if gap > 0 else 0.0
        if wait > 0:
            sleep(wait)
        h["count"] += 1
        h["last"] = now()
        data[host] = h
        state.write_text(json.dumps(data))
        return wait


# ---- minimal WebSocket client (RFC 6455, text frames, client-masked) ---------

class WS:
    def __init__(self, url: str, timeout: float = 20):
        u = urlparse(url)
        port = u.port or 80
        self.sock = socket.create_connection((u.hostname, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path + (("?" + u.query) if u.query else "")
        self.sock.sendall((f"GET {path or '/'} HTTP/1.1\r\nHost: {u.hostname}:{port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise BrowserError("browser closed the connection during the WebSocket handshake")
            self.buf += chunk
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise BrowserError("WebSocket upgrade refused: " + head.split(b"\r\n", 1)[0].decode(errors="replace"))

    def _frame(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(head + mask + masked)

    def send(self, text: str) -> None:
        self._frame(0x1, text.encode())

    def _read(self, n: int, deadline: float) -> bytes:
        while len(self.buf) < n:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError
            self.sock.settimeout(remaining)
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                raise TimeoutError
            if not chunk:
                raise BrowserError("browser closed the connection")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self, timeout: float):
        """One complete text message, or None when `timeout` passes first."""
        deadline = time.time() + timeout
        parts = []
        try:
            while True:
                b0, b1 = self._read(2, deadline)
                fin, opcode, n = b0 & 0x80, b0 & 0x0F, b1 & 0x7F
                if n == 126:
                    n = struct.unpack("!H", self._read(2, deadline))[0]
                elif n == 127:
                    n = struct.unpack("!Q", self._read(8, deadline))[0]
                mask = self._read(4, deadline) if b1 & 0x80 else None
                payload = self._read(n, deadline)
                if mask:
                    payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
                if opcode == 0x9:
                    self._frame(0xA, payload)
                    continue
                if opcode == 0xA:
                    continue
                if opcode == 0x8:
                    raise BrowserError("browser closed the DevTools connection")
                parts.append(payload)
                if fin:
                    return b"".join(parts).decode(errors="replace")
        except TimeoutError:
            if parts:
                raise BrowserError("timed out in the middle of a DevTools message")
            return None

    def close(self) -> None:
        try:
            self._frame(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class CDP:
    """One DevTools session: request/response by id, events collected on the side."""

    def __init__(self, ws_url: str):
        self.ws = WS(ws_url)
        self.next_id = 0
        self.events = []

    def call(self, method: str, params: dict = None, timeout: float = 30) -> dict:
        self.next_id += 1
        mid = self.next_id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while True:
            raw = self.ws.recv(max(0.05, deadline - time.time()))
            if raw is None:
                raise BrowserError(f"DevTools call {method} timed out after {timeout:.0f}s")
            msg = json.loads(raw)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise BrowserError(f"DevTools {method}: {msg['error'].get('message')}")
                return msg.get("result") or {}
            if "method" in msg:
                self.events.append(msg)

    def wait_event(self, name: str, timeout: float) -> bool:
        deadline = time.time() + timeout
        while True:
            for i, ev in enumerate(self.events):
                if ev.get("method") == name:
                    del self.events[: i + 1]
                    return True
            remaining = deadline - time.time()
            if remaining <= 0:
                return False
            raw = self.ws.recv(remaining)
            if raw is None:
                return False
            msg = json.loads(raw)
            if "method" in msg:
                self.events.append(msg)

    def close(self) -> None:
        self.ws.close()


# ---- the bridge ---------------------------------------------------------------

BRIDGE_DOWN = ("browser bridge is not reachable at {endpoint}. On the Mac: `~/.autonomous-pipelines/browser-bridge.sh "
               "status` (see docs/BROWSER-BRIDGE.md). No page was fetched.")


def version(cfg: dict) -> dict:
    try:
        with urllib.request.urlopen(cfg["endpoint"] + "/json/version", timeout=6) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise BrowserError(BRIDGE_DOWN.format(endpoint=cfg["endpoint"]) + f" ({e})", EXIT_OPERATOR)


def _ws_base(cfg: dict) -> str:
    u = urlparse(cfg["endpoint"])
    return f"ws://{u.hostname}:{u.port or 80}"


EXTRACT_JS = """(() => {
  const links = [];
  for (const a of document.querySelectorAll('a[href]')) {
    const t = (a.innerText || a.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ');
    if (t && a.href.startsWith('http')) links.push({t: t.slice(0, 120), h: a.href});
    if (links.length >= 400) break;
  }
  return JSON.stringify({title: document.title, url: location.href,
    text: document.body ? document.body.innerText : '', links});
})()"""

CHALLENGE_MARKS = ("just a moment", "verify you are human", "verifying you are human", "checking your browser",
                   "unusual activity", "are you a robot", "press and hold", "captcha", "access denied", "help us protect")
SIGNIN_PATHS = ("/login", "/signin", "/sign-in", "/sign_in", "/profile/login", "/member/login")


def classify(page: dict, requested: str) -> str:
    """'' for a normal page, else 'challenge' or 'signin' (the operator must act)."""
    text = (page.get("title", "") + "\n" + page.get("text", "")[:3000]).lower()
    if len(page.get("text", "")) < 2500 and any(m in text for m in CHALLENGE_MARKS):
        return "challenge"
    final, want = urlparse(page.get("url", "")), urlparse(requested)
    if any(final.path.lower().startswith(p) for p in SIGNIN_PATHS) and not any(
            want.path.lower().startswith(p) for p in SIGNIN_PATHS):
        return "signin"
    return ""


def fetch(url: str, cfg: dict, *, wait: float = 3.0, scroll: int = 0, timeout: float = 45) -> dict:
    """Open `url` in a background tab of the operator's browser, read it, close the tab."""
    info = version(cfg)
    browser_ws = _ws_base(cfg) + urlparse(info["webSocketDebuggerUrl"]).path
    browser = CDP(browser_ws)
    target = None
    try:
        target = browser.call("Target.createTarget", {"url": "about:blank", "background": True})["targetId"]
        page = CDP(f"{_ws_base(cfg)}/devtools/page/{target}")
        try:
            page.call("Page.enable")
            nav = page.call("Page.navigate", {"url": url}, timeout=timeout)
            if nav.get("errorText"):
                raise BrowserError(f"navigation failed: {nav['errorText']}")
            # The document itself must arrive; the full load event (ads, trackers, long
            # polls) is only given a short grace, since background tabs can take very
            # long to fire it. Slow pages are read as far as they got.
            page.wait_event("Page.domContentEventFired", timeout)
            page.wait_event("Page.loadEventFired", min(timeout, LOAD_GRACE_S))
            time.sleep(max(0.0, wait))
            for _ in range(max(0, scroll)):
                page.call("Runtime.evaluate", {"expression": "window.scrollBy(0, Math.round(innerHeight * 0.9))"})
                time.sleep(1.2)
            res = page.call("Runtime.evaluate", {"expression": EXTRACT_JS, "returnByValue": True}, timeout=timeout)
            out = json.loads(res.get("result", {}).get("value") or "{}")
        finally:
            page.close()
    finally:
        if target:
            try:
                browser.call("Target.closeTarget", {"targetId": target}, timeout=10)
            except BrowserError:
                pass
        browser.close()
    out["status"] = classify(out, url)
    return out


def render(page: dict, *, max_chars: int, links: bool) -> str:
    text = page.get("text", "")
    cut = len(text) > max_chars
    lines = [f"URL: {page.get('url', '')}", f"TITLE: {page.get('title', '')}", "---", text[:max_chars]]
    if cut:
        lines.append(f"[truncated at {max_chars} of {len(text)} characters; use --max-chars or --out]")
    if links:
        lines.append(f"--- LINKS ({len(page.get('links', []))})")
        lines += [f"{l['t']}\t{l['h']}" for l in page.get("links", [])]
    return "\n".join(lines) + "\n"


NEEDS_OPERATOR = {
    "challenge": "the site showed a bot check instead of the page. Ask the operator to open the bridge Chrome window "
                 "on the Mac, load this URL once and complete the check. Do not retry in a loop.",
    "signin": "the site redirected to its sign-in page. Ask the operator to sign in to this site in the bridge "
              "Chrome window on the Mac. Do not retry in a loop.",
}


def cmd(a, reg: dict) -> int:
    cfg = config(reg)
    try:
        if a.action == "status":
            info = version(cfg)
            print(f"browser bridge up: {info.get('Browser', '?')} at {cfg['endpoint']}")
            print("allowed sites: " + (", ".join(cfg["allow"]) or "(none; add domains to browser.allow in roles/registry.json)"))
            return EXIT_OK
        if not a.url:
            raise BrowserError("usage: pipeline browser fetch <url>", EXIT_REFUSED)
        if not role_allowed(reg):
            raise BrowserError(f"role {os.environ.get('PIPELINE_ROLE')} has no `browser` tool grant in roles/registry.json",
                               EXIT_REFUSED)
        if not host_allowed(a.url, cfg["allow"]):
            raise BrowserError(f"{urlparse(a.url).hostname or a.url} is not in browser.allow ({', '.join(cfg['allow']) or 'empty'}). "
                               f"The operator adds sites in roles/registry.json; do not work around this.", EXIT_REFUSED)
        version(cfg)  # fail fast, before spending a paced slot
        pace(urlparse(a.url).hostname.lower(), cfg)
        page = fetch(a.url, cfg, wait=a.wait, scroll=a.scroll, timeout=a.timeout)
        body = json.dumps(page, indent=1) + "\n" if a.json else render(page, max_chars=a.max_chars, links=a.links)
        if a.out:
            Path(a.out).write_text(body)
            print(f"wrote {a.out}: {len(page.get('text', ''))} characters, title {page.get('title', '')!r}, url {page.get('url', '')}")
        else:
            sys.stdout.write(body)
        if page["status"]:
            print(f"NEEDS OPERATOR ({page['status']}): {NEEDS_OPERATOR[page['status']]}", file=sys.stderr)
            return EXIT_OPERATOR
        return EXIT_OK
    except BrowserError as e:
        print(f"pipeline browser: {e}", file=sys.stderr)
        return e.code
