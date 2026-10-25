"""Bifrost hub: turns outlet names into RF codes and back, serves the dashboard, and the
status of the whole thing for the d-mediaserver home page.

  bifrost/outlet/<id>/set    ON / OFF  -> publishes the outlet's code on 433/send
  433/receive  <code>                  -> bifrost/outlet/<id>/state ON / OFF (retained)
  :8582/                               -> the dashboard (dashboard/)
  :8582/api/status.json                -> broker, Homebridge, RF bridge, outlets (CORS open)
  POST :8582/api/outlets/<id>          -> {"on": true} switches the outlet, as /set does

The codes live in outlets.toml only. 433/receive carries both the remote's presses and
every code the bridge sends, so the state follows whoever switched the outlet.

Standard library only: a minimal MQTT 3.1.1 client (connect, subscribe, QoS 0
publishes, ping) is all the hub needs."""
from __future__ import annotations

import json
import logging
import os
import socket
import struct
import threading
import time
import tomllib
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BROKER = os.environ.get("MQTT_HOST", "mosquitto")
USER, PASS = os.environ.get("MQTT_USER", ""), os.environ.get("MQTT_PASS", "")
HOMEBRIDGE = (os.environ.get("HOMEBRIDGE_HOST", "host.docker.internal"), 8581)
PORT = int(os.environ.get("STATUS_PORT", "8582"))
DASHBOARD_DIR = Path(os.environ.get("DASHBOARD_DIR", Path(__file__).resolve().parent.parent / "dashboard"))
BRIDGE_TIMEOUT = 60  # the bridge says "online" every 10 s
OUTLETS = tomllib.loads(Path(os.environ.get("OUTLETS_FILE", "/app/outlets.toml")).read_text())["outlet"]
BY_ID = {o["id"]: o for o in OUTLETS}
BY_CODE = {str(o[k]): (o["id"], k == "on") for o in OUTLETS for k in ("on", "off")}
log = logging.getLogger("hub")

state = {"broker": False, "bridge_seen": None, "last_code": None, "outlets": {}}
lock = threading.Lock()
conn: socket.socket | None = None  # the broker connection, shared with the web server
send_lock = threading.Lock()       # one packet at a time on it, from either thread


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _str(s: str) -> bytes:
    b = s.encode()
    return struct.pack("!H", len(b)) + b


def _packet(kind: int, body: bytes) -> bytes:
    n, length = len(body), b""
    while True:
        n, digit = divmod(n, 128)
        length += bytes([digit | (0x80 if n else 0)])
        if not n:
            return bytes([kind]) + length + body


def _read(sock: socket.socket) -> tuple[int, bytes]:
    def exact(n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("broker closed the connection")
            buf += chunk
        return buf
    kind = exact(1)[0]
    n, shift = 0, 0
    while True:
        digit = exact(1)[0]
        n |= (digit & 0x7F) << shift
        shift += 7
        if not digit & 0x80:
            return kind, exact(n)


def _send(sock: socket.socket, data: bytes) -> None:
    with send_lock:
        sock.sendall(data)


def publish(sock: socket.socket, topic: str, payload: str, retain: bool = False) -> None:
    _send(sock, _packet(0x31 if retain else 0x30, _str(topic) + payload.encode()))


def switch(sock: socket.socket, outlet: dict, on: bool) -> None:
    publish(sock, "433/send", str(outlet["on" if on else "off"]))
    log.info("%s -> %s", outlet["id"], "ON" if on else "OFF")


def on_message(sock: socket.socket, topic: str, payload: str) -> None:
    parts = topic.split("/")
    if topic.startswith("bifrost/outlet/") and len(parts) == 4:
        outlet, cmd, value = BY_ID.get(parts[2]), parts[3], payload.strip().upper()
        if cmd == "set":
            if outlet and value in ("ON", "OFF"):
                switch(sock, outlet, value == "ON")
            else:
                log.warning("ignored %s = %r (unknown outlet or not ON/OFF)", topic, payload)
        elif cmd == "state" and outlet and value in ("ON", "OFF"):
            with lock:
                prev = state["outlets"].get(outlet["id"], {})
                state["outlets"][outlet["id"]] = {"on": value == "ON", "at": prev.get("at")}
        return
    if topic == "433/receive" and payload in BY_CODE:
        oid, on = BY_CODE[payload]
        with lock:
            state["outlets"][oid] = {"on": on, "at": now()}
        publish(sock, f"bifrost/outlet/{oid}/state", "ON" if on else "OFF", retain=True)
    with lock:
        if topic == "433/status":
            state["bridge_seen"] = time.time()
        elif topic in ("433/send", "433/receive"):
            last = state["last_code"]
            echo = (topic == "433/receive" and last and last["via"] == "sent" and last["code"] == payload
                    and time.time() - last["ts"] < 3)  # the bridge repeats what it sends on 433/receive
            if not echo:
                state["last_code"] = {"code": payload, "via": "sent" if topic == "433/send" else "remote",
                                      "at": now(), "ts": time.time()}


def listen() -> None:
    global conn
    while True:
        try:
            with socket.create_connection((BROKER, 1883), timeout=45) as sock:
                flags = 0x02 | (0x80 if USER else 0) | (0x40 if PASS else 0)
                body = _str("MQTT") + bytes([4, flags]) + struct.pack("!H", 60) + _str("bifrost-hub")
                body += (_str(USER) if USER else b"") + (_str(PASS) if PASS else b"")
                sock.sendall(_packet(0x10, body))
                kind, ack = _read(sock)
                if kind >> 4 != 2 or ack[1] != 0:
                    raise ConnectionError(f"connection refused (code {ack[1]})")
                topics = ("433/#", "bifrost/outlet/+/set", "bifrost/outlet/+/state")
                sock.sendall(_packet(0x82, struct.pack("!H", 1) + b"".join(_str(t) + b"\x00" for t in topics)))
                with lock:
                    state["broker"] = True
                conn = sock
                log.info("listening on %s", BROKER)
                while True:
                    try:
                        kind, data = _read(sock)
                    except socket.timeout:
                        _send(sock, b"\xc0\x00")  # ping, the broker drops us after 1.5 x keepalive
                        continue
                    if kind >> 4 == 3:
                        tlen = struct.unpack("!H", data[:2])[0]
                        skip = 2 if (kind >> 1) & 3 else 0  # packet id only for QoS > 0
                        on_message(sock, data[2:2 + tlen].decode(), data[2 + tlen + skip:].decode(errors="replace"))
        except Exception as e:  # noqa: BLE001 - keep retrying, the broker may restart
            log.warning("broker: %s", e)
        conn = None
        with lock:
            state["broker"] = False
        time.sleep(10)


def homebridge_up() -> bool:
    try:
        socket.create_connection(HOMEBRIDGE, timeout=2).close()
        return True
    except OSError:
        return False


def snapshot() -> dict:
    with lock:
        seen = state["bridge_seen"]
        return {
            "updated_at": now(),
            "broker": state["broker"],
            "homebridge": homebridge_up(),
            "bridge": {"online": bool(seen and time.time() - seen < BRIDGE_TIMEOUT),
                       "last_seen": datetime.fromtimestamp(seen).astimezone().isoformat(timespec="seconds") if seen else None},
            "outlets": [{"id": o["id"], "name": o["name"], **state["outlets"].get(o["id"], {"on": None, "at": None})}
                        for o in OUTLETS],
            "last_code": {k: v for k, v in state["last_code"].items() if k != "ts"} if state["last_code"] else None,
        }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DASHBOARD_DIR), **kwargs)

    def _json(self, code: int, data: dict, cors: bool = False) -> None:
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?", 1)[0] in ("/api/status.json", "/health"):
            return self._json(200, snapshot(), cors=True)
        return super().do_GET()

    def do_POST(self):
        # No CORS here, and JSON only: a browser asks first (preflight) before posting JSON to
        # another origin, gets no answer, and gives up. So a web page elsewhere cannot switch.
        parts = self.path.split("?", 1)[0].strip("/").split("/")
        outlet = BY_ID.get(parts[2]) if len(parts) == 3 and parts[:2] == ["api", "outlets"] else None
        if not outlet:
            return self._json(404, {"error": "unknown outlet"})
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self._json(415, {"error": "send JSON"})
        try:
            on = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))["on"]
            if not isinstance(on, bool):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return self._json(400, {"error": 'expected {"on": true} or {"on": false}'})
        sock = conn
        if sock is None:
            return self._json(503, {"error": "the broker is not connected"})
        try:
            switch(sock, outlet, on)
        except OSError as e:
            return self._json(503, {"error": f"broker: {e}"})
        self._json(202, {"id": outlet["id"], "on": on})

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def list_directory(self, path):
        self.send_error(404)
        return None

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    threading.Thread(target=listen, daemon=True, name="mqtt").start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
