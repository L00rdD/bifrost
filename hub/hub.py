"""Bifrost hub: turns outlet names into RF codes and back, serves the dashboard, and the
status of the whole thing for the d-mediaserver home page.

  bifrost/outlet/<id>/set    ON / OFF  -> publishes the outlet's code on 433/send
  433/receive  <code>                  -> bifrost/outlet/<id>/state ON / OFF (retained)
  :8582/                               -> the dashboard (dashboard/)
  :8582/api/status.json                -> broker, Homebridge, RF bridge, outlets (CORS open)
  POST :8582/api/outlets/<id>          -> {"on": true} switches the outlet, as /set does

  bifrost/cover/<id>/set     0-100 / OPEN / CLOSE / STOP -> moves the cover (0 = up, 100 = open)
  bifrost/cover/<id>/position, /target, /state           <- retained, for HomeKit and the next start
  POST :8582/api/covers/<id>           -> {"position": 100} or {"stop": true}, as /set does

The codes live in outlets.toml only. 433/receive carries both the remote's presses and
every code the bridge sends, so the state follows whoever switched the outlet.

A cover (the projection screen) does not say where it is either: the hub sends up or down,
then stop when the time measured for that distance is over, and follows the remote's
presses the same way. Closing goes all the way to the top, where the motor stops by itself
and the estimate starts again from a known place.

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
CONFIG = tomllib.loads(Path(os.environ.get("OUTLETS_FILE", "/app/outlets.toml")).read_text())
OUTLETS, COVERS = CONFIG.get("outlet", []), CONFIG.get("cover", [])
BY_ID = {o["id"]: o for o in OUTLETS}
BY_CODE = {str(o[k]): (o["id"], k == "on") for o in OUTLETS for k in ("on", "off")}
COVER_BY_ID = {c["id"]: c for c in COVERS}
COVER_CODES = {str(c[k]): (c["id"], k) for c in COVERS for k in ("up", "down", "stop")}
TOP_MARGIN = 2.0      # s past the computed time when closing: the motor stops itself at the top
REVERSE_PAUSE = 0.5   # s between stop and the other direction
ECHO_WINDOW = 3       # s: the bridge repeats what it sends on 433/receive
log = logging.getLogger("hub")

state = {"broker": False, "bridge_seen": None, "last_code": None, "outlets": {},
         # pos: % at `since` (None = unknown), dir: +1 opening (down), -1 closing (up), 0 stopped
         "covers": {c["id"]: {"pos": None, "dir": 0, "since": 0.0, "target": None, "run": None, "sent": {}}
                    for c in COVERS}}
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


def rf(device: dict, key: str) -> str:
    """The 433/send payload: the code, and the pulse length in us when the remote is not at 350."""
    return f"{device[key]} {device['pulse']}" if device.get("pulse") else str(device[key])


def switch(sock: socket.socket, outlet: dict, on: bool) -> None:
    publish(sock, "433/send", rf(outlet, "on" if on else "off"))
    log.info("%s -> %s", outlet["id"], "ON" if on else "OFF")


def _position(cid: str) -> float | None:
    """Where the cover is now, from where it was and how long it has moved. Under lock."""
    c, cfg = state["covers"][cid], COVER_BY_ID[cid]
    if c["pos"] is None or not c["dir"]:
        return c["pos"]
    full = cfg["open_time"] if c["dir"] > 0 else cfg["close_time"]
    return min(100.0, max(0.0, c["pos"] + c["dir"] * 100 * (time.time() - c["since"]) / full))


def _cover_set(cid: str, direction: int, pos: float | None = ..., target: int | None = ...) -> None:
    """Changes the direction (keeping the position reached so far) and publishes it all, retained."""
    with lock:
        c = state["covers"][cid]
        c["pos"] = _position(cid) if pos is ... else pos
        c["dir"], c["since"] = direction, time.time()
        if target is not ...:
            c["target"] = target
        elif not direction and c["pos"] is not None:
            c["target"] = round(c["pos"])  # stopped: HomeKit shows a move until target = position
        pos, target = c["pos"], c["target"]
    sock = conn
    if sock is None:
        return
    base = f"bifrost/cover/{cid}"
    if pos is not None:
        publish(sock, f"{base}/position", str(round(pos)), retain=True)
    if target is not None:
        publish(sock, f"{base}/target", str(target), retain=True)
    publish(sock, f"{base}/state", {1: "INCREASING", -1: "DECREASING", 0: "STOPPED"}[direction], retain=True)


def _press(cid: str, key: str) -> None:
    sock = conn
    if sock is None:
        raise ConnectionError("the broker is not connected")
    with lock:
        state["covers"][cid]["sent"][str(COVER_BY_ID[cid][key])] = time.time()
    publish(sock, "433/send", rf(COVER_BY_ID[cid], key))
    log.info("%s -> %s", cid, key)


def _run(cid: str, target: int | None, run: threading.Event, press: bool) -> None:
    """Moves to target (None: stop). press=False only follows a move the remote started."""
    cfg = COVER_BY_ID[cid]
    try:
        with lock:
            pos, moving = _position(cid), state["covers"][cid]["dir"]
        if target is None:
            _press(cid, "stop")
            return _cover_set(cid, 0)
        if press and pos is None:  # unknown: all the way up first, then it is 0
            _press(cid, "up")
            _cover_set(cid, -1, target=target)
            if run.wait(cfg["close_time"] + TOP_MARGIN):
                return
            _press(cid, "stop")
            _cover_set(cid, 0, pos=0.0)
            pos, moving = 0.0, 0
        if pos is None:
            return
        d = 1 if target > pos else -1 if target < pos else 0
        if press and moving and moving != d:
            _press(cid, "stop")
            _cover_set(cid, 0)
            if run.wait(REVERSE_PAUSE):
                return
        if d == 0:
            return _cover_set(cid, 0, target=target)
        if press:
            _press(cid, "down" if d > 0 else "up")
        _cover_set(cid, d, target=target)
        duration = abs(target - pos) / 100 * (cfg["open_time"] if d > 0 else cfg["close_time"])
        if target == 0:
            duration += TOP_MARGIN  # the drift of the estimate goes away against the top
        if run.wait(duration):
            return
        if press:
            _press(cid, "stop")
        _cover_set(cid, 0, pos=float(target))
    except OSError as e:  # the broker went away mid-move: where the cover stopped is unknown
        log.warning("%s: %s", cid, e)
        with lock:
            state["covers"][cid].update(pos=None, dir=0)
    finally:
        with lock:
            if state["covers"][cid]["run"] is run:
                state["covers"][cid]["run"] = None


def move(cid: str, target: int | None, press: bool = True) -> None:
    """Starts a move in the background, cancelling the one under way."""
    with lock:
        c = state["covers"][cid]
        if c["run"]:
            c["run"].set()
        run = c["run"] = threading.Event()
    threading.Thread(target=_run, args=(cid, target, run, press), daemon=True, name=f"cover-{cid}").start()


def heard(cid: str, key: str) -> None:
    """A code of the cover on 433/receive that the hub did not send: someone used the remote."""
    with lock:
        c = state["covers"][cid]
        if c["run"]:
            c["run"].set()
            c["run"] = None
    if key == "stop":
        return _cover_set(cid, 0)
    # the remote's up and down run to the end: follow until then (down stops below 100 %,
    # at the bottom of the screen, but 100 % is as far as the estimate goes)
    move(cid, 100 if key == "down" else 0, press=False)


def on_cover(topic: str, payload: str, retained: bool) -> None:
    _, _, cid, cmd = topic.split("/")
    if cid not in COVER_BY_ID:
        return log.warning("ignored %s (unknown cover)", topic)
    value = payload.strip().upper()
    if cmd == "set":
        target = {"OPEN": 100, "CLOSE": 0, "STOP": None}.get(value, value)
        if target is not None and not (isinstance(target, int) or target.isdigit() and int(target) <= 100):
            return log.warning("ignored %s = %r (not 0-100, OPEN, CLOSE or STOP)", topic, payload)
        return move(cid, None if target is None else int(target))
    if not retained:  # our own publishes coming back
        return
    with lock:  # the broker's memory, at (re)connection: where the cover was left
        c = state["covers"][cid]
        if c["dir"] or c["run"]:
            return
        if cmd == "state" and value != "STOPPED":
            c.update(pos=None, lost=True)  # the hub stopped mid-move: the motor went on
        elif cmd == "position" and value.isdigit() and c["pos"] is None and not c.get("lost"):
            c["pos"] = min(100.0, float(value))


def on_message(sock: socket.socket, topic: str, payload: str, retained: bool = False) -> None:
    parts = topic.split("/")
    if topic.startswith("bifrost/cover/") and len(parts) == 4:
        return on_cover(topic, payload, retained)
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
    if topic in ("433/send", "433/receive"):
        payload = payload.split(" ", 1)[0]  # 433/send may carry the pulse length after the code
    if topic == "433/receive" and payload in BY_CODE:
        oid, on = BY_CODE[payload]
        with lock:
            state["outlets"][oid] = {"on": on, "at": now()}
        publish(sock, f"bifrost/outlet/{oid}/state", "ON" if on else "OFF", retain=True)
    if topic == "433/receive" and payload in COVER_CODES:
        cid, key = COVER_CODES[payload]
        with lock:
            sent = state["covers"][cid]["sent"].pop(payload, 0)
        if time.time() - sent >= ECHO_WINDOW:
            heard(cid, key)
    with lock:
        if topic == "433/status":
            state["bridge_seen"] = time.time()
        elif topic in ("433/send", "433/receive"):
            last = state["last_code"]
            echo = (topic == "433/receive" and last and last["via"] == "sent" and last["code"] == payload
                    and time.time() - last["ts"] < ECHO_WINDOW)
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
                topics = ("433/#", "bifrost/outlet/+/set", "bifrost/outlet/+/state", "bifrost/cover/+/set",
                          "bifrost/cover/+/position", "bifrost/cover/+/state")
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
                        on_message(sock, data[2:2 + tlen].decode(), data[2 + tlen + skip:].decode(errors="replace"),
                                   retained=bool(kind & 1))
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
            "covers": [{"id": c["id"], "name": c["name"],
                        "position": None if (p := _position(c["id"])) is None else round(p),
                        "moving": {1: "down", -1: "up"}.get(state["covers"][c["id"]]["dir"]),
                        "target": state["covers"][c["id"]]["target"]} for c in COVERS],
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
        kind = parts[1] if len(parts) == 3 and parts[0] == "api" else None
        outlet = BY_ID.get(parts[2]) if kind == "outlets" else None
        cover = COVER_BY_ID.get(parts[2]) if kind == "covers" else None
        if not outlet and not cover:
            return self._json(404, {"error": f"unknown {'cover' if kind == 'covers' else 'outlet'}"})
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self._json(415, {"error": "send JSON"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            if outlet:
                on = body["on"]
                if not isinstance(on, bool):
                    raise ValueError
            elif body.get("stop") is True:
                position = None
            else:
                position = body["position"]
                if type(position) is not int or not 0 <= position <= 100:
                    raise ValueError
        except (ValueError, KeyError, TypeError, AttributeError):
            return self._json(400, {"error": 'expected {"on": true} or {"on": false}' if outlet
                                    else 'expected {"position": 0-100} or {"stop": true}'})
        sock = conn
        if sock is None:
            return self._json(503, {"error": "the broker is not connected"})
        if cover:
            move(cover["id"], position)
            return self._json(202, {"id": cover["id"], "position": position} if position is not None
                              else {"id": cover["id"], "stop": True})
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
