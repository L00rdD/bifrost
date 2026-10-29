"""Bifrost hub: turns outlet names into RF codes and back, serves the dashboard, and the
status of the whole thing for the d-mediaserver home page.

  bifrost/outlet/<id>/set    ON / OFF  -> publishes the outlet's code on 433/send
  433/receive  <code>                  -> bifrost/outlet/<id>/state ON / OFF (retained)
  :8582/                               -> the dashboard (dashboard/)
  :8582/api/status.json                -> broker, Homebridge, RF bridge, outlets (CORS open)
  POST :8582/api/outlets/<id>          -> {"on": true} switches the outlet, as /set does

  bifrost/cover/<id>/set     0-100 / OPEN / CLOSE / STOP -> moves the cover (0 = up, 100 = open)
  bifrost/cover/<id>/press   up / down / stop -> the button as on the remote; stop comes by itself
                             once the measured time is over, the motor never stopping on its own
  bifrost/cover/<id>/declare 0-100 -> where the cover really is, nothing moves: the estimate was wrong
  bifrost/cover/<id>/position, /target, /state           <- retained, for HomeKit and the next start
  POST :8582/api/covers/<id>           -> {"position": 100}, {"stop": true}, {"press": "up"} or
                                          {"declare": 0}, as /set, /press and /declare do

  bifrost/projector/<id>/set  ON / OFF, /key  DPAD_UP..., /app/set  <package> or home -> over ADB
  bifrost/projector/<id>/state  ON / OFF, /app  <package>, home or screensaver  <- retained, polled every 5 s
  POST :8582/api/projectors/<id>       -> {"on": true}, {"key": "HOME"}, {"app": "<package>"} or
                                          {"tap": [x, y]} (0-1 across the screen, from the top left)
  GET :8582/api/projectors/<id>/screen.mjpg  -> what the projector shows, as Vysor would (MJPEG)

The codes live in outlets.toml only. 433/receive carries both the remote's presses and
every code the bridge sends, so the state follows whoever switched the outlet.

A cover (the projection screen) does not say where it is either: the hub sends up or down,
then stop when the time measured for that distance is over, and follows the remote's
presses the same way. The motor never stops on its own, not even at the top: every move
the hub starts ends with a stop, at the latest when the measured time is over. When the
estimate is wrong (a code the screen never got), /press moves it by hand as the remote
would, and /declare tells the hub where the screen really is.

The projector (Google TV) ignores power keys from the Google TV remote protocol, so the hub
drives it with adb (network debugging on, this hub allowed once on the projector's screen).
Asleep for long, its Wi-Fi lets adb through only now and then: an ON or OFF tries again for
3 minutes, dropping the dead connection adb keeps between two tries.
When it turns on, from anywhere, its screen comes down; when it has been off for 5 minutes,
the screen goes up (turned back on before that, the screen stays).

The screen comes from the scrcpy server, pushed to the projector and started only while someone
watches: it sends H.264, ffmpeg turns it into JPEGs that any browser shows, iPhone included, with
no HTTPS. Netflix and the other DRM apps show black, as with any mirroring.

Standard library only, plus the adb, ffmpeg and scrcpy-server binaries: a minimal MQTT 3.1.1 client (connect,
subscribe, QoS 0 publishes, ping) is all the hub needs."""
from __future__ import annotations

import collections
import itertools
import json
import logging
import os
import random
import re
import socket
import struct
import subprocess
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
PROJECTORS = CONFIG.get("projector", [])
PROJECTOR_BY_ID = {p["id"]: p for p in PROJECTORS}
ADB = os.environ.get("ADB", "adb")
PROJECTOR_POLL = 5  # s between two looks at the projector
WAKE_FOR = 180      # s an ON or OFF keeps trying: in deep sleep the projector's Wi-Fi answers adb only now and then
WAKE_RETRY = 2      # s between two tries
CLOSE_AFTER = 300   # s off before the screen goes up: a change of mind does not roll it up and down
# adb shell goes through a shell on the projector: only these ever reach it from a payload
KEY_RE = re.compile(r"[A-Z][A-Z0-9_]{0,39}")
PACKAGE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+")
SCRCPY_SERVER = os.environ.get("SCRCPY_SERVER", "/opt/scrcpy-server.jar")  # from the Dockerfile
SCRCPY_VERSION = os.environ.get("SCRCPY_VERSION", "5.0.1")  # the server refuses any other client version
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
MIRROR_PORT = 27183   # the first projector's local end of the adb forward, the next one +1...
MIRROR_SIZE = 1280    # px, the longest side
MIRROR_FPS = 15
MIRROR_LINGER = 10    # s the mirror keeps running once nobody watches: a reload does not restart it
# H.264 in, JPEGs out. yuvj420p: the JPEG encoder takes only full-range colours, and the projector
# sends limited range. No -fflags nobuffer nor -flags low_delay: with either, no picture comes out.
TO_MJPEG = ["-loglevel", "error", "-probesize", "32", "-analyzeduration", "0", "-f", "h264", "-i", "pipe:0",
            "-pix_fmt", "yuvj420p", "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "6", "pipe:1"]
MIRROR_START = 15     # s for the first picture, else the viewer gets an error
MIRROR_REPEAT = 1     # s: a still screen sends no picture, the viewer gets the last one again
REVERSE_PAUSE = 0.5   # s between stop and the other direction
ECHO_WINDOW = 3       # s: the bridge repeats what it sends on 433/receive
log = logging.getLogger("hub")

state = {"broker": False, "bridge_seen": None, "last_code": None, "outlets": {},
         # pos: % at `since` (None = unknown), dir: +1 opening (down), -1 closing (up), 0 stopped
         "covers": {c["id"]: {"pos": None, "dir": 0, "since": 0.0, "target": None, "run": None, "sent": {}}
                    for c in COVERS},
         # on: True / False / None (unreachable), known: the last on that was not None
         # closes_at: when the screen goes up, the projector being off since close_after
         "projectors": {p["id"]: {"on": None, "known": None, "app": None, "error": None, "at": None, "closes_at": None}
                        for p in PROJECTORS}}
pokes = {p["id"]: threading.Event() for p in PROJECTORS}      # look at the projector now
orders = {p["id"]: 0 for p in PROJECTORS}                      # the last order's number: a newer one ends the tries of an older
adb_locks = {p["id"]: threading.Lock() for p in PROJECTORS}   # one adb command at a time
lock = threading.Lock()
conn: socket.socket | None = None  # the broker connection, shared with the web server
send_lock = threading.Lock()       # one packet at a time on it, from either thread
last_out = 0.0                     # monotonic time of the last packet sent to the broker
KEEPALIVE = 60                     # s, told to the broker: it drops a client silent for 1.5 x that


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
    global last_out
    with send_lock:
        sock.sendall(data)
        last_out = time.monotonic()


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


def _run(cid: str, target: int | None, press: bool, run: threading.Event) -> None:
    """Moves to target (None: stop). press=False only follows a move the remote started."""
    cfg = COVER_BY_ID[cid]
    try:
        with lock:
            pos, moving = _position(cid), state["covers"][cid]["dir"]
        if target is None:
            _press(cid, "stop")
            return _cover_set(cid, 0)
        if pos is None:  # unknown: all the way up first, then it is 0. Not pressing (following
            if not press and target:  # the remote): only its up gets there, a down stays unknown
                return
            if press:
                _press(cid, "up")
            _cover_set(cid, -1, target=target)
            if run.wait(cfg["close_time"]):
                return
            if press:
                _press(cid, "stop")
            _cover_set(cid, 0, pos=0.0)
            pos, moving = 0.0, 0
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


def _manual(cid: str, key: str, run: threading.Event) -> None:
    """Up or down pressed by hand from the hub: as on the remote, no reckoning of the distance,
    but stop comes by itself once the measured time is over, the motor never stopping on its own."""
    cfg = COVER_BY_ID[cid]
    d = 1 if key == "down" else -1
    try:
        with lock:
            moving = state["covers"][cid]["dir"]
        if moving and moving != d:
            _press(cid, "stop")
            _cover_set(cid, 0)
            if run.wait(REVERSE_PAUSE):
                return
        _press(cid, key)
        _cover_set(cid, d, target=100 if d > 0 else 0)
        if run.wait(cfg["open_time"] if d > 0 else cfg["close_time"]):
            return
        _press(cid, "stop")
        if d < 0:  # a whole close_time up: at the top, wherever it started
            _cover_set(cid, 0, pos=0.0)
        else:      # down from unknown stays unknown; from a known place, the estimate followed
            _cover_set(cid, 0)
    except OSError as e:
        log.warning("%s: %s", cid, e)
        with lock:
            state["covers"][cid].update(pos=None, dir=0)
    finally:
        with lock:
            if state["covers"][cid]["run"] is run:
                state["covers"][cid]["run"] = None


def _start(cid: str, fn, *args) -> None:
    """Runs fn(cid, *args, run) in the background, cancelling the move under way."""
    with lock:
        c = state["covers"][cid]
        if c["run"]:
            c["run"].set()
        run = c["run"] = threading.Event()
    threading.Thread(target=fn, args=(cid, *args, run), daemon=True, name=f"cover-{cid}").start()


def move(cid: str, target: int | None, press: bool = True) -> None:
    """Starts a move to target (None: stop), cancelling the one under way."""
    _start(cid, _run, target, press)


def heard(cid: str, key: str) -> None:
    """A code of the cover on 433/receive that the hub did not send: someone used the remote."""
    log.info("%s <- %s (remote)", cid, key)
    follow(cid, key)


def press(cid: str, key: str) -> None:
    """The button by hand: stop, or up / down with a stop of the hub's own at the measured time."""
    if key == "stop":
        return move(cid, None)
    _start(cid, _manual, key)


def declare(cid: str, pos: int) -> None:
    """Where the cover really is, from someone who sees it: the estimate starts again from there.
    Nothing moves; a move under way is stopped first."""
    log.info("%s = %s %% (declared)", cid, pos)
    with lock:
        c = state["covers"][cid]
        if c["run"]:
            c["run"].set()
            c["run"] = None
        moving, c["lost"] = c["dir"], False
    if moving:
        _press(cid, "stop")
    _cover_set(cid, 0, pos=float(pos), target=pos)


def follow(cid: str, key: str) -> None:
    """Keeps the estimate going behind the remote's button; its stop is for whoever holds it."""
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
    if cmd == "press":
        if value not in ("UP", "DOWN", "STOP"):
            return log.warning("ignored %s = %r (not up, down or stop)", topic, payload)
        return press(cid, value.lower())
    if cmd == "declare":
        if not (value.isdigit() and int(value) <= 100):
            return log.warning("ignored %s = %r (not 0-100)", topic, payload)
        return declare(cid, int(value))
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


def adb(projector: dict, *args: str) -> str:
    """Runs adb against the projector. Raises OSError when it fails or the projector is away."""
    try:
        r = subprocess.run([ADB, "-s", projector["adb"], *args], capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired as e:
        raise OSError("adb: no answer in 10 s") from e
    if r.returncode:
        raise OSError((r.stderr.strip() or r.stdout.strip() or f"adb exit {r.returncode}").splitlines()[-1])
    return r.stdout


SCREENSAVER = "screensaver"  # the app in front while the projector dreams
HOME = "home"  # the app in front when it is the launcher, whichever it is; as an app to open, the HOME key


def probe(projector: dict) -> tuple[bool, str | None]:
    """Awake or asleep, and the app in front. Dreaming (the screensaver) is on: it still projects.
    The launcher is HOME: asked each time, it follows a change of launcher."""
    # asleep, no activity is resumed: the last grep finds nothing and fails, so the shell
    # exits 0 whatever it finds (and grep -m1 closing the pipe early is not an error either)
    out = adb(projector, "shell", "dumpsys power 2>/dev/null | grep -m1 mWakefulness=; "
                                  "dumpsys activity activities 2>/dev/null | grep -m1 topResumedActivity=; "
                                  "cmd package resolve-activity --brief -a android.intent.action.MAIN "
                                  "-c android.intent.category.HOME 2>/dev/null | tail -1; true")
    awake = re.search(r"mWakefulness=(\w+)", out)
    if not awake:
        raise OSError("adb: no power state from the projector")
    if awake.group(1) == "Dreaming":
        return True, SCREENSAVER
    app = re.search(r"topResumedActivity=\S*\{\S+ u\d+ ([\w.]+)/", out)
    home = re.search(r"^([\w.]+)/\S+$", out, re.M)
    app = app and app.group(1)
    return awake.group(1) == "Awake", HOME if app and home and app == home.group(1) else app


def projector_seen(pid: str, on: bool | None, app: str | None, error: str | None) -> None:
    """Keeps what the last look found, publishes what changed, and moves the screen when the
    projector turns on or off, whoever turned it (Home app, its own remote, the dashboard)."""
    cfg = PROJECTOR_BY_ID[pid]
    app = app if on else None
    with lock:
        p = state["projectors"][pid]
        before, changed = p["known"], (p["on"], p["app"]) != (on, app)
        p.update(on=on, app=app, error=error)
        if on is not None:
            p["known"] = on
        if changed and on is not None:
            p["at"] = now()
    if error and changed:
        log.warning("%s: %s", pid, error)
    sock = conn
    if changed and on is not None and sock is not None:
        try:
            publish(sock, f"bifrost/projector/{pid}/state", "ON" if on else "OFF", retain=True)
            publish(sock, f"bifrost/projector/{pid}/app", app or "", retain=True)
        except OSError as e:
            log.warning("%s: %s", pid, e)
    if on is None or cfg.get("cover") not in COVER_BY_ID:
        return
    if before is not None and on != before:
        log.info("%s turned %s", pid, "on" if on else "off")
        if on:
            _cancel_close(pid)
            move(cfg["cover"], 100)
        else:
            _close_later(pid)
    elif before is None and not on:  # the hub (re)started with the projector off: a close may have been lost
        with lock:
            down = _position(cfg["cover"]) != 0
        if down:
            _close_later(pid)


close_timers: dict[str, threading.Timer] = {}


def _cancel_close(pid: str) -> None:
    with lock:
        timer = close_timers.pop(pid, None)
        state["projectors"][pid]["closes_at"] = None
    if timer:
        timer.cancel()
        log.info("%s back on: the screen stays", pid)


def _close_later(pid: str) -> None:
    delay = PROJECTOR_BY_ID[pid].get("close_after", CLOSE_AFTER)

    def close():
        with lock:
            if close_timers.get(pid) is not timer or state["projectors"][pid]["known"] is not False:
                return
            del close_timers[pid]
            state["projectors"][pid]["closes_at"] = None
        log.info("%s off for %s s: the screen goes up", pid, delay)
        move(PROJECTOR_BY_ID[pid]["cover"], 0)

    timer = threading.Timer(delay, close)
    timer.daemon = True
    with lock:
        if old := close_timers.get(pid):
            old.cancel()
        close_timers[pid] = timer
        state["projectors"][pid]["closes_at"] = time.time() + delay
    timer.start()


def reconnect(projector: dict) -> None:
    """Drops the connection and opens a new one. Asleep for long, the projector leaves adb a dead
    connection ("offline", or no answer) that a plain connect keeps ("already connected")."""
    for cmd in ("disconnect", "connect"):
        try:
            subprocess.run([ADB, cmd, projector["adb"]], capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass


def watch(pid: str) -> None:
    projector = PROJECTOR_BY_ID[pid]
    while True:
        try:
            with adb_locks[pid]:
                on, app = probe(projector)
            projector_seen(pid, on, app, None)
        except OSError as e:
            projector_seen(pid, None, None, str(e))
            reconnect(projector)
        pokes[pid].wait(PROJECTOR_POLL)
        pokes[pid].clear()


def screen_size(projector: dict) -> tuple[int, int]:
    """The size taps are given in: the override when the projector renders below its panel."""
    out = adb(projector, "shell", "wm size")
    sizes = dict(re.findall(r"(Physical|Override) size: (\d+x\d+)", out))
    size = sizes.get("Override") or sizes.get("Physical")
    if not size:
        raise OSError("adb: no screen size from the projector")
    w, h = size.split("x")
    return int(w), int(h)


def projector_command(pid: str, on: bool | None = None, key: str | None = None, app: str | None = None,
                      tap: tuple[float, float] | None = None) -> None:
    """Runs one order in the background (adb takes a second or two), then looks again.
    key and app must have passed KEY_RE and PACKAGE_RE (or be HOME): they end up in a shell command."""
    projector = PROJECTOR_BY_ID[pid]

    with lock:
        if on is not None or app:
            orders[pid] += 1
        order = orders[pid]

    def power() -> None:
        """WAKEUP or SLEEP until the projector takes it, for WAKE_FOR at most: asleep for long, its
        Wi-Fi lets adb through only now and then. A newer order ends the tries."""
        deadline, tries = time.monotonic() + WAKE_FOR, 0
        while True:
            try:
                with adb_locks[pid]:  # SLEEP and WAKEUP, not POWER: sending one twice is harmless
                    adb(projector, "shell", f"input keyevent KEYCODE_{'WAKEUP' if on or app else 'SLEEP'}")
                return
            except OSError as e:
                if time.monotonic() + WAKE_RETRY >= deadline or orders[pid] != order:
                    raise
                if not tries:
                    log.info("%s: %s, trying again for %d s", pid, e, WAKE_FOR)
                tries += 1
                reconnect(projector)
                time.sleep(WAKE_RETRY)
                if orders[pid] != order:
                    raise OSError("a newer order came") from e

    def run():
        try:
            if on is not None or app:
                power()
            with adb_locks[pid]:
                if tap:
                    w, h = screen_size(projector)
                    adb(projector, "shell", f"input tap {round(tap[0] * (w - 1))} {round(tap[1] * (h - 1))}")
                if key or app == HOME:
                    adb(projector, "shell", f"input keyevent KEYCODE_{key or 'HOME'}")
                elif app:
                    adb(projector, "shell", f"monkey -p {app} -c android.intent.category.LEANBACK_LAUNCHER 1")
            if not tap:  # a tap is no news
                log.info("%s -> %s", pid, "ON" if on else "OFF" if on is False else key or app)
        except OSError as e:
            log.warning("%s: %s", pid, e)
        pokes[pid].set()
    threading.Thread(target=run, daemon=True, name=f"projector-{pid}").start()


def split_jpegs(buf: bytes) -> tuple[list[bytes], bytes]:
    """The whole JPEGs at the start of buf, and what is left. FF D9 ends a JPEG and appears nowhere
    else in one (the coded data escapes FF bytes)."""
    frames = []
    while (end := buf.find(b"\xff\xd9")) != -1:
        start = buf.find(b"\xff\xd8")
        if 0 <= start < end:
            frames.append(buf[start:end + 2])
        buf = buf[end + 2:]
    return frames, buf


class Mirror:
    """What the projector shows, while someone watches: the scrcpy server on the projector streams
    H.264 through an adb forward, ffmpeg turns it into JPEGs, the viewers get the latest one.
    The first viewer starts it, it stops MIRROR_LINGER s after the last one left."""

    def __init__(self, projector: dict, port: int):
        self.projector, self.port = projector, port
        self.cond = threading.Condition()
        self.frame, self.seq, self.viewers, self.idle_since = None, 0, 0, 0.0
        self.running, self.error, self.procs, self.sock, self.said = False, None, [], None, collections.deque(maxlen=5)

    def watch(self):
        """The JPEGs as they come, the latest one first, the same one again every MIRROR_REPEAT s while
        the screen stays still: that is how the server learns a viewer left. Ends when the mirror stops."""
        with self.cond:
            self.viewers += 1
            if not self.running:
                self.running, self.error, self.frame, self.seq = True, None, None, 0
                threading.Thread(target=self._run, daemon=True, name=f"mirror-{self.projector['id']}").start()
        try:
            seen = 0
            while True:
                with self.cond:
                    self.cond.wait_for(lambda: self.seq != seen or not self.running,
                                       timeout=MIRROR_REPEAT if seen else MIRROR_START)
                    if not self.running or self.seq == seen == 0:  # stopped, or no first picture in time
                        raise OSError(self.error or (self.said[-1] if self.said else "the projector sends no picture"))
                    seen, frame = self.seq, self.frame
                yield frame
        finally:
            with self.cond:
                self.viewers -= 1
                self.idle_since = time.monotonic()

    def _stop(self, error: str | None = None) -> None:
        with self.cond:
            if error and not self.error:
                self.error = error
        for proc in self.procs:
            if proc.poll() is None:
                proc.kill()
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass

    def _connect(self, server: subprocess.Popen) -> tuple[socket.socket, bytes]:
        """The forward takes connections before the server listens, and drops them: try until it sends."""
        deadline = time.monotonic() + MIRROR_START
        while time.monotonic() < deadline:
            if server.poll() is not None:
                time.sleep(0.2)  # its last words reach self.said
                raise OSError(self.said[-1] if self.said else f"scrcpy: server exit {server.returncode}")
            try:
                sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
                first = sock.recv(65536)
                if first:
                    sock.settimeout(None)
                    return sock, first
                sock.close()
            except OSError:
                pass
            time.sleep(0.3)
        raise OSError("scrcpy: the server did not start")

    def _run(self) -> None:
        projector, jar = self.projector, "/data/local/tmp/bifrost-scrcpy.jar"
        self.procs, self.sock, self.said = [], None, collections.deque(maxlen=5)
        scid = f"{random.getrandbits(31):08x}"  # its own socket name: a server left over cannot take it
        try:
            with adb_locks[projector["id"]]:
                adb(projector, "push", SCRCPY_SERVER, jar)
                adb(projector, "forward", f"tcp:{self.port}", f"localabstract:scrcpy_{scid}")
            server = subprocess.Popen(
                [ADB, "-s", projector["adb"], "shell", f"CLASSPATH={jar} app_process / com.genymobile.scrcpy.Server "
                 f"{SCRCPY_VERSION} scid={scid} tunnel_forward=true audio=false control=false cleanup=false raw_stream=true "
                 f"max_size={MIRROR_SIZE} max_fps={MIRROR_FPS} video_bit_rate=4000000"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            self.procs.append(server)
            threading.Thread(target=self._listen, args=(server, "scrcpy"), daemon=True).start()
            self.sock, first = self._connect(server)
            ffmpeg = subprocess.Popen(
                [FFMPEG, *TO_MJPEG],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.procs.append(ffmpeg)
            threading.Thread(target=self._listen, args=(ffmpeg, "ffmpeg"), daemon=True).start()
            threading.Thread(target=self._pump, args=(self.sock, first, ffmpeg), daemon=True).start()
            threading.Thread(target=self._linger, daemon=True).start()
            log.info("%s: mirroring the screen", projector["id"])
            buf = b""
            while chunk := ffmpeg.stdout.read1(65536):
                frames, buf = split_jpegs(buf + chunk)
                if frames:
                    with self.cond:
                        self.frame, self.seq = frames[-1], self.seq + 1
                        self.cond.notify_all()
        except (OSError, subprocess.SubprocessError) as e:
            self._stop(str(e))
            log.warning("%s: mirror: %s", projector["id"], e)
        finally:
            self._stop()
            for proc in self.procs:  # no zombie, no pipe left open
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                for pipe in (proc.stdin, proc.stdout, proc.stderr):
                    if pipe:
                        try:
                            pipe.close()
                        except OSError:
                            pass
            try:
                subprocess.run([ADB, "-s", projector["adb"], "forward", "--remove", f"tcp:{self.port}"],
                               capture_output=True, timeout=10)
            except (OSError, subprocess.SubprocessError):
                pass
            with self.cond:
                self.running = False
                self.cond.notify_all()
            log.info("%s: mirror stopped", projector["id"])

    def _listen(self, proc: subprocess.Popen, name: str) -> None:
        """Keeps the last lines of the scrcpy server or ffmpeg for the errors, and logs them: its pipe
        stays empty, so it never blocks on it."""
        for line in proc.stderr:
            if line := line.decode(errors="replace").strip():
                self.said.append(line)
                log.warning("%s: %s: %s", self.projector["id"], name, line)

    def _pump(self, sock: socket.socket, first: bytes, ffmpeg: subprocess.Popen) -> None:
        """The H.264 from the projector into ffmpeg."""
        try:
            data = first
            while data:
                ffmpeg.stdin.write(data)
                ffmpeg.stdin.flush()
                data = sock.recv(65536)
        except (OSError, ValueError):
            pass
        self._stop()

    def _linger(self) -> None:
        while True:
            time.sleep(1)
            with self.cond:
                if not self.running:
                    return
                if not self.viewers and time.monotonic() - self.idle_since >= MIRROR_LINGER:
                    break
        self._stop()


mirrors = {p["id"]: Mirror(p, MIRROR_PORT + i) for i, p in enumerate(PROJECTORS)}


def on_projector(topic: str, payload: str) -> None:
    parts, value = topic.split("/"), payload.strip()
    pid, cmd = parts[2], "/".join(parts[3:])
    if pid not in PROJECTOR_BY_ID:
        return log.warning("ignored %s (unknown projector)", topic)
    if cmd == "set" and value.upper() in ("ON", "OFF"):
        return projector_command(pid, on=value.upper() == "ON")
    if cmd == "key" and KEY_RE.fullmatch(value.upper()):
        return projector_command(pid, key=value.upper())
    if cmd == "app/set" and (value == HOME or PACKAGE_RE.fullmatch(value)):
        return projector_command(pid, app=value)
    log.warning("ignored %s = %r", topic, payload)


def on_message(sock: socket.socket, topic: str, payload: str, retained: bool = False) -> None:
    parts = topic.split("/")
    if topic.startswith("bifrost/cover/") and len(parts) == 4:
        return on_cover(topic, payload, retained)
    if topic.startswith("bifrost/projector/") and len(parts) in (4, 5):
        if not retained:  # a command left retained by mistake must not replay at each start
            on_projector(topic, payload)
        return
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
                body = _str("MQTT") + bytes([4, flags]) + struct.pack("!H", KEEPALIVE) + _str("bifrost-hub")
                body += (_str(USER) if USER else b"") + (_str(PASS) if PASS else b"")
                sock.sendall(_packet(0x10, body))
                kind, ack = _read(sock)
                if kind >> 4 != 2 or ack[1] != 0:
                    raise ConnectionError(f"connection refused (code {ack[1]})")
                topics = ("433/#", "bifrost/outlet/+/set", "bifrost/outlet/+/state", "bifrost/cover/+/set", "bifrost/cover/+/press", "bifrost/cover/+/declare",
                          "bifrost/cover/+/position", "bifrost/cover/+/state", "bifrost/projector/+/set",
                          "bifrost/projector/+/key", "bifrost/projector/+/app/set")
                _send(sock, _packet(0x82, struct.pack("!H", 1) + b"".join(_str(t) + b"\x00" for t in topics)))
                with lock:
                    state["broker"] = True
                conn = sock
                log.info("listening on %s", BROKER)
                while True:
                    try:
                        kind, data = _read(sock)
                    except socket.timeout:
                        kind = 0
                    # The keepalive counts what we send, not what we receive: the bridge's heartbeat
                    # every 10 s keeps the reads busy, so ping on our own silence, not on a read timeout.
                    if time.monotonic() - last_out > KEEPALIVE / 2:
                        _send(sock, b"\xc0\x00")
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
            "projectors": [{"id": p["id"], "name": p["name"], "cover": p.get("cover"),
                            "apps": [{"name": n, "package": a} for n, a in p.get("apps", {}).items()],
                            **{k: v for k, v in state["projectors"][p["id"]].items() if k not in ("known", "closes_at")},
                            "closes_at": (datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")
                                          if (t := state["projectors"][p["id"]]["closes_at"]) else None)}
                           for p in PROJECTORS],
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
        path = self.path.split("?", 1)[0]
        if path in ("/api/status.json", "/health"):
            return self._json(200, snapshot(), cors=True)
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "projectors"] and parts[3] == "screen.mjpg":
            if parts[2] not in mirrors:
                return self._json(404, {"error": "unknown projector"})
            return self._mirror(mirrors[parts[2]])
        return super().do_GET()

    def _mirror(self, mirror: Mirror) -> None:
        """MJPEG: one JPEG after the other in a never-ending response, which <img> shows as video.
        Each JPEG is followed by the boundary at once: a browser shows a part only once the next
        boundary came. No CORS: another site can show it but not read it."""
        frames = mirror.watch()
        try:
            first = next(frames)  # an error before any byte goes out still gets its status
        except OSError as e:
            frames.close()
            log.warning("%s: no screen for the viewer: %s", mirror.projector["id"], e)
            return self._json(503, {"error": str(e)})
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        try:
            self.wfile.write(b"--frame\r\n")
            for frame in itertools.chain([first], frames):
                self.wfile.write(b"Content-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n%b\r\n--frame\r\n"
                                 % (len(frame), frame))
                self.wfile.flush()
        except OSError:  # the viewer left, or the mirror stopped
            pass
        finally:
            frames.close()
            self.close_connection = True

    def do_POST(self):
        # No CORS here, and JSON only: a browser asks first (preflight) before posting JSON to
        # another origin, gets no answer, and gives up. So a web page elsewhere cannot switch.
        parts = self.path.split("?", 1)[0].strip("/").split("/")
        kind = parts[1] if len(parts) == 3 and parts[0] == "api" else None
        outlet = BY_ID.get(parts[2]) if kind == "outlets" else None
        cover = COVER_BY_ID.get(parts[2]) if kind == "covers" else None
        projector = PROJECTOR_BY_ID.get(parts[2]) if kind == "projectors" else None
        if not outlet and not cover and not projector:
            return self._json(404, {"error": f"unknown {kind[:-1] if kind in ('covers', 'projectors') else 'outlet'}"})
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self._json(415, {"error": "send JSON"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            if projector:
                if len(body) != 1 or not (isinstance(body.get("on"), bool)
                                          or isinstance(body.get("key"), str) and KEY_RE.fullmatch(body["key"])
                                          or body.get("app") == HOME
                                          or isinstance(body.get("app"), str) and PACKAGE_RE.fullmatch(body["app"])
                                          or isinstance(body.get("tap"), list) and len(body["tap"]) == 2
                                          and all(type(v) in (int, float) and 0 <= v <= 1 for v in body["tap"])):
                    raise ValueError
            elif outlet:
                on = body["on"]
                if not isinstance(on, bool):
                    raise ValueError
            elif len(body) != 1:
                raise ValueError
            elif body.get("stop") is True or body.get("press") in ("up", "down", "stop"):
                position = None
            else:
                position = body["declare"] if "declare" in body else body["position"]
                if type(position) is not int or not 0 <= position <= 100:
                    raise ValueError
        except (ValueError, KeyError, TypeError, AttributeError):
            return self._json(400, {"error": 'expected {"on": true} or {"on": false}' if outlet
                                    else 'expected {"position": 0-100}, {"stop": true}, {"press": "up"} or {"declare": 0}' if cover
                                    else 'expected {"on": true}, {"key": "HOME"}, {"app": "<package>"} or {"tap": [0.5, 0.5]}'})
        if projector:  # adb, not the broker
            projector_command(projector["id"], **body)
            return self._json(202, {"id": projector["id"], **body})
        sock = conn
        if sock is None:
            return self._json(503, {"error": "the broker is not connected"})
        if cover:
            if "press" in body:
                press(cover["id"], body["press"])
            elif "declare" in body:
                declare(cover["id"], position)
            else:
                move(cover["id"], position)
            return self._json(202, {"id": cover["id"], **body})
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
    for p in PROJECTORS:
        threading.Thread(target=watch, args=(p["id"],), daemon=True, name=f"watch-{p['id']}").start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
