"""Tells the hub how long this Mac's keyboard and mouse have been idle, for the presence at the desk.

  python3 desk.py http://dpi.local:8582 desk

Every 2 s it reads the idle time of the HID system (any key, mouse move or click, trackpad);
it sends it to POST /api/presences/<id> once a minute, and at once when the desk is used again
after a minute or more without: the hub turns the outlet on with no wait. The hub turns it off
once nobody used the desk for its `away`: asleep or shut down, this Mac says nothing, and that
is enough. No permission to give: the idle time is not the keys.

Away from home (at work), dpi.local answers nowhere: nothing is told, nothing switches, and the
log says so once, not every 2 s.

Standard library only, Python 3.9 (the one of macOS), run by launchd: see com.bifrost.desk.plist."""
from __future__ import annotations

import ctypes
import ctypes.util
import json
import sys
import time
import urllib.request

POLL = 2   # s between two looks at the idle time
BEAT = 60  # s between two words to the hub, and the pause after which a key is news
HID_SYSTEM = 1             # kCGEventSourceStateHIDSystemState
ANY_INPUT = 0xFFFFFFFF     # kCGAnyInputEventType

_failing = [""]  # the last error logged: logged again only once the hub answered or the error changed

_cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))
_cg.CGEventSourceSecondsSinceLastEventType.restype = ctypes.c_double
_cg.CGEventSourceSecondsSinceLastEventType.argtypes = [ctypes.c_int32, ctypes.c_uint32]


def idle() -> float:
    """Seconds since the last key, click or mouse move."""
    return _cg.CGEventSourceSecondsSinceLastEventType(HID_SYSTEM, ANY_INPUT)


def due(now: float, last_input: float, sent: float, told: float) -> bool:
    """Whether to tell the hub now: the minute beat, or a key after a minute the hub knows of none.
    sent: when the hub last heard, told: the last input it knows of."""
    return now - sent >= BEAT or (last_input > told + POLL and now - told >= BEAT)


def tell(url: str, idle_s: float) -> None:
    req = urllib.request.Request(url, data=json.dumps({"idle": round(idle_s, 1)}).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5).close()


def step(url: str, sent: float, told: float, now: float, idle_s: float) -> tuple[float, float]:
    """One look: tells the hub if due. The new (sent, told); unchanged when the hub did not answer."""
    if not due(now, now - idle_s, sent, told):
        return sent, told
    try:
        tell(url, idle_s)
    except OSError as e:  # the hub or the Wi-Fi is away: the next look tries again
        if str(e) != _failing[0]:
            print(f"hub: {e}", file=sys.stderr, flush=True)
            _failing[0] = str(e)
        return sent, told
    _failing[0] = ""
    return now, now - idle_s


def main(hub: str, presence: str) -> None:
    url = f"{hub.rstrip('/')}/api/presences/{presence}"
    sent = told = float("-inf")
    while True:
        # wall time, not monotonic: on macOS the monotonic clock stops while the Mac sleeps
        sent, told = step(url, sent, told, time.time(), idle())
        time.sleep(POLL)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: desk.py <hub url> <presence id>")
    main(*sys.argv[1:])
