"""A tiny fake Chrome DevTools endpoint for the suite: HTTP /json/version plus a
WebSocket server that answers the handful of DevTools calls pipeline.browser makes."""

import base64
import hashlib
import json
import socket
import struct
import threading

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class FakeChrome:
    def __init__(self, page: dict, big_ping: bool = True, fire_load: bool = True):
        self.page = page            # {"title", "url", "text", "links"} returned by Runtime.evaluate
        self.calls = []             # (path, method, params)
        self.closed_targets = []
        self.big_ping = big_ping
        self.fire_load = fire_load  # False: a page whose load event never comes
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(8)
        self.port = self.srv.getsockname()[1]
        self.endpoint = f"http://127.0.0.1:{self.port}"
        self._stop = False
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self):
        self._stop = True
        try:
            self.srv.close()
        except OSError:
            pass

    def _accept(self):
        while not self._stop:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        try:
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
            head, rest = buf.split(b"\r\n\r\n", 1)
            lines = head.decode().split("\r\n")
            path = lines[0].split()[1]
            headers = {k.lower(): v.strip() for k, v in (l.split(":", 1) for l in lines[1:] if ":" in l)}
            if "sec-websocket-key" not in headers:
                body = json.dumps({"Browser": "FakeChrome/1.0",
                                   "webSocketDebuggerUrl": f"ws://127.0.0.1:{self.port}/devtools/browser/b1"}).encode()
                status = b"200 OK" if path == "/json/version" else b"404 Not Found"
                conn.sendall(b"HTTP/1.1 " + status + b"\r\nContent-Type: application/json\r\nContent-Length: "
                             + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
                return
            accept = base64.b64encode(hashlib.sha1((headers["sec-websocket-key"] + GUID).encode()).digest()).decode()
            conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                          f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
            self._ws(conn, path, rest)
        except OSError:
            pass
        finally:
            conn.close()

    @staticmethod
    def _send(conn, text: str, opcode: int = 0x1):
        data = text.encode()
        n = len(data)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | opcode, 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 127, n)
        conn.sendall(head + data)

    def _ws(self, conn, path, buf):
        def read(n):
            nonlocal buf
            while len(buf) < n:
                chunk = conn.recv(65536)
                if not chunk:
                    raise OSError("closed")
                buf += chunk
            out, buf = buf[:n], buf[n:]
            return out
        while True:
            b0, b1 = read(2)
            opcode, n = b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", read(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", read(8))[0]
            mask = read(4)
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(read(n)))
            if opcode == 0x8:
                return
            if opcode != 0x1:
                continue
            msg = json.loads(payload.decode())
            method, params = msg["method"], msg.get("params", {})
            self.calls.append((path, method, params))
            result = {}
            if method == "Target.createTarget":
                result = {"targetId": "T1"}
            elif method == "Target.closeTarget":
                self.closed_targets.append(params.get("targetId"))
                result = {"success": True}
            elif method == "Page.navigate":
                result = {"frameId": "F1"}
            elif method == "Runtime.evaluate" and params.get("returnByValue"):
                result = {"result": {"type": "string", "value": json.dumps(self.page)}}
            if method == "Page.navigate" and self.big_ping:
                self._send(conn, "", opcode=0x9)  # a ping the client must answer and skip
            self._send(conn, json.dumps({"id": msg["id"], "result": result}))
            if method == "Page.navigate":
                self._send(conn, json.dumps({"method": "Page.domContentEventFired", "params": {"timestamp": 1}}))
                if self.fire_load:
                    self._send(conn, json.dumps({"method": "Page.loadEventFired", "params": {"timestamp": 1}}))
