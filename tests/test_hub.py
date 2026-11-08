import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["OUTLETS_FILE"] = str(ROOT / "tests" / "outlets.toml")
os.environ["HOMEBRIDGE_HOST"] = "127.0.0.1"
SCHEDULES = Path(tempfile.mkdtemp()) / "hub" / "schedules.json"
os.environ["SCHEDULES_FILE"] = str(SCHEDULES)
sys.path.insert(0, str(ROOT / "hub"))
import hub  # noqa: E402


class FakeSock:
    """Stands for the broker connection: keeps what the hub publishes as (topic, payload, retain)."""

    def __init__(self):
        self.published = []

    def sendall(self, data: bytes) -> None:
        assert data[0] >> 4 == 3, "only publishes expected"
        n, shift, i = 0, 0, 1
        while True:
            n |= (data[i] & 0x7F) << shift
            shift += 7
            i += 1
            if not data[i - 1] & 0x80:
                break
        body = data[i:i + n]
        tlen = struct.unpack("!H", body[:2])[0]
        self.published.append((body[2:2 + tlen].decode(), body[2 + tlen:].decode(), bool(data[0] & 1)))


def reset():
    with hub.lock:
        hub.state.update(broker=True, bridge_seen=None, last_code=None, outlets={})
        hub.schedules.clear()
        hub.applied.clear()
        for c in hub.state["covers"].values():
            if c["run"]:
                c["run"].set()
            c.clear()
            c.update(pos=None, dir=0, since=0.0, target=None, run=None, sent={})


hub.REVERSE_PAUSE = 0.05  # the test screen runs in 0.4 s down, 0.5 s up


def settle(timeout=3):
    """Waits for the screen's move to be over."""
    end = time.time() + timeout
    while time.time() < end:
        with hub.lock:
            if not hub.state["covers"]["screen"]["run"]:
                return
        time.sleep(0.02)
    raise AssertionError("the screen is still moving")


def screen():
    return hub.snapshot()["covers"][0]


class MessageTest(unittest.TestCase):
    def setUp(self):
        reset()
        self.sock = FakeSock()

    def test_set_sends_the_outlet_code(self):
        hub.on_message(self.sock, "bifrost/outlet/terrarium/set", "on")
        hub.on_message(self.sock, "bifrost/outlet/fan/set", "OFF")
        self.assertEqual(self.sock.published, [("433/send", "1111", False), ("433/send", "4444", False)])

    def test_set_ignores_unknown_outlets_and_values(self):
        hub.on_message(self.sock, "bifrost/outlet/nope/set", "ON")
        hub.on_message(self.sock, "bifrost/outlet/fan/set", "toggle")
        self.assertEqual(self.sock.published, [])

    def test_code_heard_publishes_the_state_retained(self):
        hub.on_message(self.sock, "433/receive", "2222")
        self.assertEqual(self.sock.published, [("bifrost/outlet/terrarium/state", "OFF", True)])
        self.assertFalse(hub.snapshot()["outlets"][0]["on"])

    def test_unknown_code_only_becomes_the_last_code(self):
        hub.on_message(self.sock, "433/receive", "999")
        self.assertEqual(self.sock.published, [])
        s = hub.snapshot()
        self.assertEqual(s["last_code"]["code"], "999")
        self.assertIsNone(s["outlets"][0]["on"])

    def test_echo_of_a_sent_code_is_not_a_remote_press(self):
        hub.on_message(self.sock, "433/send", "1111")
        hub.on_message(self.sock, "433/receive", "1111")
        self.assertEqual(hub.snapshot()["last_code"]["via"], "sent")
        self.assertTrue(hub.snapshot()["outlets"][0]["on"])

    def test_retained_state_restores_the_outlet(self):
        hub.on_message(self.sock, "bifrost/outlet/fan/state", "ON")
        fan = hub.snapshot()["outlets"][1]
        self.assertEqual((fan["id"], fan["on"], fan["at"]), ("fan", True, None))

    def test_bridge_heartbeat(self):
        self.assertFalse(hub.snapshot()["bridge"]["online"])
        hub.on_message(self.sock, "433/status", "online")
        self.assertTrue(hub.snapshot()["bridge"]["online"])


class KeepaliveTest(unittest.TestCase):
    def test_pings_while_the_broker_keeps_talking(self):
        # the bridge's heartbeat keeps the hub's reads busy: it must still ping on its own silence,
        # or the broker drops it after 1.5 x keepalive
        hub_end, broker = socket.socketpair()
        hub_end.settimeout(45)
        broker.settimeout(2)
        real_connect, real_keepalive = hub.socket.create_connection, hub.KEEPALIVE
        hub.socket.create_connection = lambda *a, **k: hub_end
        hub.KEEPALIVE = 1  # ping after 0.5 s of silence
        try:
            threading.Thread(target=hub.listen, daemon=True).start()
            broker.recv(1024)                  # CONNECT
            broker.sendall(b"\x20\x02\x00\x00")  # CONNACK
            broker.recv(1024)                  # SUBSCRIBE
            heartbeat = hub._packet(0x30, hub._str("433/status") + b"online")
            got = b""
            for _ in range(30):  # 1.5 s of heartbeats
                broker.sendall(heartbeat)
                time.sleep(0.05)
                broker.setblocking(False)
                try:
                    got += broker.recv(1024)
                except BlockingIOError:
                    pass
                broker.setblocking(True)
            self.assertIn(b"\xc0\x00", got)
        finally:
            hub.socket.create_connection, hub.KEEPALIVE = real_connect, real_keepalive
            hub.conn = None  # the thread stays blocked on the open pair, harmless


class CoverTest(unittest.TestCase):
    def setUp(self):
        reset()
        self.sock = hub.conn = FakeSock()

    def tearDown(self):
        reset()
        hub.conn = None

    def sent(self):
        return [p for t, p, _ in self.sock.published if t == "433/send"]

    def published(self, topic):
        return [p for t, p, _ in self.sock.published if t == f"bifrost/cover/screen/{topic}"]

    def at(self, pos):
        with hub.lock:
            hub.state["covers"]["screen"]["pos"] = float(pos)

    def test_open_sends_down_then_stop_after_the_measured_time(self):
        self.at(0)
        start = time.time()
        hub.on_message(self.sock, "bifrost/cover/screen/set", "100")
        time.sleep(0.2)
        self.assertEqual(screen()["moving"], "down")
        self.assertAlmostEqual(screen()["position"], 50, delta=10)
        settle()
        self.assertAlmostEqual(time.time() - start, 0.4, delta=0.1)
        self.assertEqual(self.sent(), ["5553 188", "5552 188"])  # down, stop, with the remote's pulse
        self.assertEqual((screen()["position"], screen()["moving"]), (100, None))
        self.assertEqual(self.published("state"), ["INCREASING", "STOPPED"])
        self.assertEqual(self.published("position")[-1], "100")
        self.assertTrue(all(r for t, _, r in self.sock.published if t.startswith("bifrost/cover/")))

    def test_partial_move_takes_its_share_of_the_time(self):
        self.at(100)
        start = time.time()
        hub.on_message(self.sock, "bifrost/cover/screen/set", "50")
        settle()
        self.assertAlmostEqual(time.time() - start, 0.25, delta=0.08)  # half of close_time
        self.assertEqual(self.sent(), ["5551 188", "5552 188"])
        self.assertEqual(screen()["position"], 50)

    def test_close_stops_at_close_time(self):
        self.at(100)
        start = time.time()
        hub.on_message(self.sock, "bifrost/cover/screen/set", "CLOSE")
        settle()
        self.assertAlmostEqual(time.time() - start, 0.5, delta=0.08)
        self.assertEqual(screen()["position"], 0)

    def test_unknown_position_goes_up_first(self):
        hub.on_message(self.sock, "bifrost/cover/screen/set", "OPEN")
        settle()
        self.assertEqual(self.sent(), ["5551 188", "5552 188", "5553 188", "5552 188"])
        self.assertEqual(screen()["position"], 100)

    def test_a_new_order_reverses_through_stop(self):
        self.at(0)
        hub.move("screen", 100)
        time.sleep(0.2)
        hub.move("screen", 0)
        settle()
        self.assertEqual(self.sent(), ["5553 188", "5552 188", "5551 188", "5552 188"])
        self.assertEqual(screen()["position"], 0)

    def test_stop(self):
        self.at(0)
        hub.move("screen", 100)
        time.sleep(0.2)
        hub.on_message(self.sock, "bifrost/cover/screen/set", "stop")
        settle()
        self.assertEqual(self.sent(), ["5553 188", "5552 188"])
        self.assertAlmostEqual(screen()["position"], 50, delta=10)
        self.assertEqual(screen()["target"], screen()["position"])  # HomeKit: no move left

    def test_bad_set_is_ignored(self):
        for value in ("101", "-1", "half"):
            hub.on_message(self.sock, "bifrost/cover/screen/set", value)
        hub.on_message(self.sock, "bifrost/cover/nope/set", "50")
        self.assertEqual(self.sock.published, [])

    def test_own_echo_is_not_a_remote_press(self):
        self.at(0)
        hub.move("screen", 100)
        time.sleep(0.05)
        hub.on_message(self.sock, "433/receive", "5553")  # the bridge echoes the down it sent
        settle()
        self.assertEqual(self.sent(), ["5553 188", "5552 188"])
        self.assertEqual(screen()["position"], 100)

    def test_remote_presses_are_followed(self):
        self.at(0)
        hub.on_message(self.sock, "433/receive", "5553")  # down on the remote
        time.sleep(0.2)
        self.assertEqual(screen()["moving"], "down")
        hub.on_message(self.sock, "433/receive", "5552")  # stop on the remote
        self.assertEqual(self.sent(), [])  # the hub sent nothing itself
        self.assertAlmostEqual(screen()["position"], 50, delta=10)
        hub.on_message(self.sock, "433/receive", "5551")  # up: the motor goes to the top alone
        settle()
        self.assertEqual((screen()["position"], screen()["moving"], self.sent()), (0, None, []))

    def test_remote_stop_cancels_the_hub_move(self):
        self.at(0)
        hub.move("screen", 100)
        time.sleep(0.2)
        hub.on_message(self.sock, "433/receive", "5552")
        time.sleep(0.4)
        self.assertEqual(self.sent(), ["5553 188"])  # no stop of its own after the remote's
        self.assertLess(screen()["position"], 70)

    def test_press_sends_the_button_and_the_stop_is_yours(self):
        self.at(0)
        hub.on_message(self.sock, "bifrost/cover/screen/press", "down")
        time.sleep(0.2)
        self.assertEqual((self.sent(), screen()["moving"]), (["5553 188"], "down"))
        hub.on_message(self.sock, "433/receive", "5553")  # the bridge's echo: not a second press
        hub.on_message(self.sock, "bifrost/cover/screen/press", "stop")
        settle()
        self.assertEqual(self.sent(), ["5553 188", "5552 188"])
        self.assertAlmostEqual(screen()["position"], 50, delta=10)

    def test_press_left_alone_stops_at_the_measured_time(self):
        self.at(100)  # wrong: the screen is really at the top, whatever the hub reckons
        start = time.time()
        hub.on_message(self.sock, "bifrost/cover/screen/press", "up")
        settle()
        self.assertAlmostEqual(time.time() - start, 0.5, delta=0.1)  # close_time, not 0 s
        self.assertEqual((self.sent(), screen()["position"]), (["5551 188", "5552 188"], 0))
        start = time.time()
        hub.on_message(self.sock, "bifrost/cover/screen/press", "down")
        settle()
        self.assertAlmostEqual(time.time() - start, 0.4, delta=0.1)  # open_time
        self.assertEqual((self.sent()[2:], screen()["position"]), (["5553 188", "5552 188"], 100))

    def test_press_from_unknown(self):
        hub.on_message(self.sock, "bifrost/cover/screen/press", "down")  # unknown stays unknown
        time.sleep(0.2)
        self.assertEqual((screen()["position"], screen()["moving"]), (None, "down"))
        settle()
        self.assertEqual((self.sent(), screen()["position"]), (["5553 188", "5552 188"], None))
        hub.on_message(self.sock, "bifrost/cover/screen/press", "up")  # a whole close_time up: the top
        settle()
        self.assertEqual((self.sent()[2:], screen()["position"]), (["5551 188", "5552 188"], 0))

    def test_remote_up_from_unknown_ends_at_the_top(self):
        hub.on_message(self.sock, "433/receive", "5551")
        settle()
        self.assertEqual((self.sent(), screen()["position"]), ([], 0))  # the remote's stop is not the hub's

    def test_declare_takes_the_position_without_moving(self):
        self.at(0)  # wrong: the screen is open, its code never arrived
        hub.on_message(self.sock, "bifrost/cover/screen/declare", "100")
        self.assertEqual(self.sent(), [])
        self.assertEqual((screen()["position"], screen()["target"], screen()["moving"]), (100, 100, None))
        self.assertEqual(self.published("position"), ["100"])
        self.assertEqual(self.published("state"), ["STOPPED"])
        hub.on_message(self.sock, "bifrost/cover/screen/set", "CLOSE")  # now a close knows its way
        settle()
        self.assertEqual((self.sent(), screen()["position"]), (["5551 188", "5552 188"], 0))

    def test_declare_mid_move_stops_first(self):
        self.at(0)
        hub.move("screen", 100)
        time.sleep(0.2)
        hub.on_message(self.sock, "bifrost/cover/screen/declare", "0")
        time.sleep(0.4)
        self.assertEqual((self.sent(), screen()["position"], screen()["moving"]), (["5553 188", "5552 188"], 0, None))

    def test_bad_press_and_declare_are_ignored(self):
        hub.on_message(self.sock, "bifrost/cover/screen/press", "left")
        hub.on_message(self.sock, "bifrost/cover/nope/press", "up")
        hub.on_message(self.sock, "bifrost/cover/screen/declare", "101")
        hub.on_message(self.sock, "bifrost/cover/screen/declare", "top")
        self.assertEqual(self.sock.published, [])

    def test_retained_position_is_restored(self):
        hub.on_message(self.sock, "bifrost/cover/screen/position", "100", retained=True)
        self.assertEqual(screen()["position"], 100)
        hub.on_message(self.sock, "bifrost/cover/screen/position", "40")  # live: our own publish
        self.assertEqual(screen()["position"], 100)

    def test_stopped_mid_move_means_unknown(self):
        hub.on_message(self.sock, "bifrost/cover/screen/state", "INCREASING", retained=True)
        hub.on_message(self.sock, "bifrost/cover/screen/position", "30", retained=True)
        self.assertIsNone(screen()["position"])

    def test_pulse_is_not_part_of_the_last_code(self):
        hub.on_message(self.sock, "433/send", "5553 188")
        hub.on_message(self.sock, "433/receive", "5553")
        self.assertEqual(hub.snapshot()["last_code"]["code"], "5553")
        self.assertEqual(hub.snapshot()["last_code"]["via"], "sent")

    def run_now(self, target, press=True):
        """_run in this thread, its move already cancelled: every wait ends at once."""
        run = threading.Event()
        run.set()
        with hub.lock:
            hub.state["covers"]["screen"]["run"] = run
        hub._run("screen", target, press, run)
        with hub.lock:
            return hub.state["covers"]["screen"]["run"]

    def test_remote_down_from_unknown_stays_unknown(self):
        hub.on_message(self.sock, "433/receive", "5553")
        settle()
        self.assertEqual((self.sock.published, screen()["position"]), ([], None))

    def test_a_cancelled_move_from_unknown_stops_on_its_way_up(self):
        self.assertIsNone(self.run_now(100))
        self.assertEqual((self.sent(), self.published("state")), (["5551 188"], ["DECREASING"]))
        self.assertEqual(self.published("target"), ["100"])

    def test_a_cancelled_reverse_stops_at_the_pause(self):
        with hub.lock:
            hub.state["covers"]["screen"].update(pos=50.0, dir=1, since=time.time())
        self.run_now(0)
        self.assertEqual((self.sent(), self.published("state")), (["5552 188"], ["STOPPED"]))

    def test_a_cancelled_move_does_not_send_its_stop(self):
        self.at(0)
        self.run_now(100)
        self.assertEqual((self.sent(), self.published("state")), (["5553 188"], ["INCREASING"]))
        self.run_now(0, press=False)
        self.assertEqual(self.sent(), ["5553 188"])

    def test_already_there_only_publishes(self):
        self.at(40)
        self.run_now(40)
        self.assertEqual((self.sent(), self.published("target"), self.published("state")), ([], ["40"], ["STOPPED"]))

    def test_broker_gone_mid_move_means_unknown(self):
        self.at(0)
        hub.conn = None
        self.run_now(100)
        self.assertEqual((screen()["position"], screen()["moving"]), (None, None))

    def test_retained_while_moving_is_ignored(self):
        self.at(20)
        with hub.lock:
            hub.state["covers"]["screen"]["dir"] = 1
        hub.on_message(self.sock, "bifrost/cover/screen/state", "INCREASING", retained=True)
        with hub.lock:
            c = hub.state["covers"]["screen"]
            self.assertEqual((c["pos"], c.get("lost")), (20.0, None))

    def test_retained_position_above_100_or_already_known(self):
        hub.on_message(self.sock, "bifrost/cover/screen/position", "250", retained=True)
        self.assertEqual(screen()["position"], 100)
        hub.on_message(self.sock, "bifrost/cover/screen/position", "30", retained=True)
        self.assertEqual(screen()["position"], 100)  # known already
        hub.on_message(self.sock, "bifrost/cover/screen/target", "30", retained=True)
        hub.on_message(self.sock, "bifrost/cover/screen/state", "STOPPED", retained=True)
        self.assertEqual(screen()["position"], 100)

    def test_set_open_close_stop_and_numbers(self):
        moves, real = [], hub.move
        hub.move = lambda cid, target, press=True: moves.append((cid, target))
        try:
            for value in ("open", "CLOSE", " 35 ", "Stop", "0", "100"):
                hub.on_message(self.sock, "bifrost/cover/screen/set", value)
        finally:
            hub.move = real
        self.assertEqual(moves, [("screen", 100), ("screen", 0), ("screen", 35), ("screen", None), ("screen", 0),
                                 ("screen", 100)])


class MessageMoreTest(unittest.TestCase):
    def setUp(self):
        reset()
        self.sock = FakeSock()

    def test_outlet_state_of_an_unknown_outlet_or_value_is_ignored(self):
        hub.on_message(self.sock, "bifrost/outlet/nope/state", "ON")
        hub.on_message(self.sock, "bifrost/outlet/fan/state", "maybe")
        hub.on_message(self.sock, "bifrost/outlet/fan/other", "ON")
        self.assertEqual(hub.snapshot()["outlets"][1]["on"], None)
        self.assertEqual(self.sock.published, [])

    def test_outlet_state_keeps_when_it_was_seen(self):
        hub.on_message(self.sock, "433/receive", "3333")
        at = hub.snapshot()["outlets"][1]["at"]
        hub.on_message(self.sock, "bifrost/outlet/fan/state", "OFF")
        self.assertEqual((hub.snapshot()["outlets"][1]["on"], hub.snapshot()["outlets"][1]["at"]), (False, at))

    def test_other_topics_change_nothing(self):
        for topic in ("433/info", "433/receive/raw", "bifrost/outlet/fan", "bifrost/cover/screen", "other/topic"):
            hub.on_message(self.sock, topic, "1111")
        s = hub.snapshot()
        self.assertEqual((s["last_code"], s["bridge"]["online"], s["outlets"][0]["on"]), (None, False, None))
        self.assertEqual(self.sock.published, [])

    def test_last_code_from_a_send_and_a_remote(self):
        hub.on_message(self.sock, "433/send", "1111 350")
        self.assertEqual({k: v for k, v in hub.snapshot()["last_code"].items() if k != "at"}, {"code": "1111", "via": "sent"})
        hub.on_message(self.sock, "433/receive", "4444")
        self.assertEqual({k: v for k, v in hub.snapshot()["last_code"].items() if k != "at"}, {"code": "4444", "via": "remote"})
        hub.on_message(self.sock, "433/receive", "4444")  # pressed again: the remote, not an echo
        self.assertEqual(hub.snapshot()["last_code"]["via"], "remote")

    def test_an_old_send_is_no_echo(self):
        hub.on_message(self.sock, "433/send", "1111")
        with hub.lock:
            hub.state["last_code"]["ts"] -= hub.ECHO_WINDOW
        hub.on_message(self.sock, "433/receive", "1111")
        self.assertEqual(hub.snapshot()["last_code"]["via"], "remote")

    def test_projector_topics_go_to_the_projector_unless_retained(self):
        calls, real = [], hub.on_projector
        hub.on_projector = lambda topic, payload: calls.append((topic, payload))
        try:
            hub.on_message(self.sock, "bifrost/projector/projector/set", "ON")
            hub.on_message(self.sock, "bifrost/projector/projector/app/set", "home")
            hub.on_message(self.sock, "bifrost/projector/projector/key", "HOME", retained=True)
            hub.on_message(self.sock, "bifrost/projector/projector", "ON")
        finally:
            hub.on_projector = real
        self.assertEqual(calls, [("bifrost/projector/projector/set", "ON"), ("bifrost/projector/projector/app/set", "home")])


AWAKE = """  mWakefulness=Awake
      topResumedActivity=ActivityRecord{67da29d u0 com.netflix.ninja/.MainActivity t4231}
"""


class FakeAdb:
    """Stands for adb: answers dumpsys with `out`, keeps the shell commands, fails when `fail` is set."""

    def __init__(self, out=AWAKE):
        self.out, self.fail, self.commands, self.done = out, None, [], threading.Event()
        self.misses = 0  # that many calls fail first, as from a projector asleep for long

    def __call__(self, projector, *args):
        if self.fail:
            raise OSError(self.fail)
        if self.misses:
            self.misses -= 1
            raise OSError("adb: no answer in 10 s")
        if args[-1] == "wm size":
            return "Physical size: 3840x2160\nOverride size: 1920x1080\n"
        if "dumpsys" not in args[-1]:
            self.commands.append(args[-1])
            self.done.set()
        return self.out


class ProjectorTest(unittest.TestCase):
    def setUp(self):
        reset()
        with hub.lock:
            hub.state["projectors"]["projector"].update(on=None, known=None, app=None, error=None, at=None, closes_at=None)
        for t in hub.close_timers.values():
            t.cancel()
        hub.close_timers.clear()
        self.sock = hub.conn = FakeSock()
        self.adb, self.real_adb, self.real_move = FakeAdb(), hub.adb, hub.move
        self.moves = []
        hub.adb, hub.move = self.adb, lambda cid, target, press=True: self.moves.append((cid, target))
        self.reconnects, self.real = 0, (hub.reconnect, hub.WAKE_FOR, hub.WAKE_RETRY, hub._focus_later)
        hub.reconnect = lambda projector: setattr(self, "reconnects", self.reconnects + 1)
        hub.WAKE_FOR, hub.WAKE_RETRY = 1, 0.05
        # its timer would fire in a later test: HealthTest tries it on its own
        self.focuses = []
        hub._focus_later = self.focuses.append

    def tearDown(self):
        hub.adb, hub.move, hub.conn = self.real_adb, self.real_move, None
        hub.reconnect, hub.WAKE_FOR, hub.WAKE_RETRY, hub._focus_later = self.real

    def wait_command(self):
        self.assertTrue(self.adb.done.wait(2), "no adb command")
        time.sleep(0.05)

    def look(self):
        on, app = hub.probe(hub.PROJECTOR_BY_ID["projector"])
        hub.projector_seen("projector", on, app, None)

    def test_probe_reads_power_and_app(self):
        self.assertEqual(hub.probe({}), (True, "com.netflix.ninja"))
        self.adb.out = "  mWakefulness=Asleep\n"
        self.assertEqual(hub.probe({}), (False, None))
        self.adb.out = ("  mWakefulness=Dreaming\n      topResumedActivity=ActivityRecord{8523068 u0 "
                        "com.google.android.apps.tv.dreamx/android.service.dreams.DreamActivity t4262}\n")
        self.assertEqual(hub.probe({}), (True, hub.SCREENSAVER))
        self.adb.out = ("  mWakefulness=Awake\n      topResumedActivity=ActivityRecord{1 u0 com.l0rdd.pensieve/.HomeActivity t9}\n"
                        "com.l0rdd.pensieve/.presentation.HomeActivity\n")
        self.assertEqual(hub.probe({}), (True, hub.HOME))
        self.adb.out = AWAKE + "com.l0rdd.pensieve/.presentation.HomeActivity\n"
        self.assertEqual(hub.probe({}), (True, "com.netflix.ninja"))
        self.adb.out = "error: device offline"
        with self.assertRaises(OSError):
            hub.probe({})

    def test_state_is_published_retained_when_it_changes(self):
        self.look()
        self.look()
        self.assertEqual(self.sock.published, [("bifrost/projector/projector/state", "ON", True),
                                               ("bifrost/projector/projector/app", "com.netflix.ninja", True)])
        p = hub.snapshot()["projectors"][0]
        self.assertEqual((p["on"], p["app"], p["apps"][1]), (True, "com.netflix.ninja", {"name": "Netflix", "package": "com.netflix.ninja"}))
        self.assertEqual(p["apps"][0]["package"], hub.HOME)  # the launcher first, a button on the dashboard

    def off(self):
        self.adb.out = "mWakefulness=Asleep"
        self.look()

    def on(self):
        self.adb.out = AWAKE
        self.look()

    def test_turning_on_brings_the_screen_down_and_off_up_after_a_while(self):
        with hub.lock:
            hub.state["covers"]["screen"]["pos"] = 0.0
        self.off()
        self.assertEqual(self.moves, [])  # the first look is no change
        self.on()
        self.assertEqual((self.moves, self.focuses), ([("screen", 100)], ["projector"]))
        self.off()
        self.assertEqual(self.moves, [("screen", 100)])  # not yet: close_after is 0.3 s here
        self.assertIsNotNone(hub.snapshot()["projectors"][0]["closes_at"])
        time.sleep(0.45)
        self.assertEqual(self.moves, [("screen", 100), ("screen", 0)])
        self.assertIsNone(hub.snapshot()["projectors"][0]["closes_at"])

    def test_back_on_before_the_delay_keeps_the_screen_down(self):
        self.off()
        self.on()
        self.off()
        time.sleep(0.1)
        self.on()
        time.sleep(0.4)
        self.assertEqual(self.moves, [("screen", 100), ("screen", 100)])  # the second one does not move: already there
        self.assertIsNone(hub.snapshot()["projectors"][0]["closes_at"])

    def test_off_at_start_with_the_screen_down_closes_it_later(self):
        with hub.lock:
            hub.state["covers"]["screen"]["pos"] = 100.0
        self.off()
        time.sleep(0.45)
        self.assertEqual(self.moves, [("screen", 0)])

    def test_off_at_start_with_the_screen_up_does_nothing(self):
        with hub.lock:
            hub.state["covers"]["screen"]["pos"] = 0.0
        self.off()
        time.sleep(0.45)
        self.assertEqual(self.moves, [])

    def test_unreachable_between_off_and_on_still_moves_the_screen(self):
        with hub.lock:
            hub.state["covers"]["screen"]["pos"] = 0.0
        self.off()
        hub.projector_seen("projector", None, None, "adb: no answer in 10 s")
        self.assertIsNone(hub.snapshot()["projectors"][0]["on"])
        self.adb.out = AWAKE
        self.look()
        self.assertEqual(self.moves, [("screen", 100)])

    def test_set_wakes_and_sleeps_over_adb(self):
        hub.on_message(self.sock, "bifrost/projector/projector/set", "ON")
        self.wait_command()
        self.adb.done.clear()
        hub.on_message(self.sock, "bifrost/projector/projector/set", "off")
        self.wait_command()
        self.assertEqual(self.adb.commands, ["input keyevent KEYCODE_WAKEUP", "input keyevent KEYCODE_SLEEP"])

    def test_app_wakes_the_projector_then_launches(self):
        hub.on_message(self.sock, "bifrost/projector/projector/app/set", "com.netflix.ninja")
        self.wait_command()
        self.assertEqual(self.adb.commands, ["input keyevent KEYCODE_WAKEUP",
                                             "monkey -p com.netflix.ninja -c android.intent.category.LEANBACK_LAUNCHER 1"])

    def test_home_wakes_the_projector_then_presses_home(self):
        hub.on_message(self.sock, "bifrost/projector/projector/app/set", "home")
        self.wait_command()
        self.assertEqual(self.adb.commands, ["input keyevent KEYCODE_WAKEUP", "input keyevent KEYCODE_HOME"])

    def test_key(self):
        hub.on_message(self.sock, "bifrost/projector/projector/key", "dpad_up")
        self.wait_command()
        self.assertEqual(self.adb.commands, ["input keyevent KEYCODE_DPAD_UP"])

    def test_nothing_reaches_the_shell_unchecked(self):
        for topic, payload in (("key", "HOME; reboot"), ("key", "12"), ("app/set", "x; rm -rf /"), ("app/set", "netflix"),
                               ("set", "toggle")):
            hub.on_message(self.sock, f"bifrost/projector/projector/{topic}", payload)
        hub.on_message(self.sock, "bifrost/projector/nope/set", "ON")
        hub.on_message(self.sock, "bifrost/projector/projector/set", "ON", retained=True)  # left on the broker
        time.sleep(0.2)
        self.assertEqual(self.adb.commands, [])

    def test_adb_failure_is_logged_not_raised(self):
        self.adb.fail = "adb: device unauthorized"
        hub.on_message(self.sock, "bifrost/projector/projector/set", "ON")
        time.sleep(1.2)  # past WAKE_FOR
        self.assertEqual(self.adb.commands, [])

    def test_on_tries_again_until_the_projector_answers(self):
        self.adb.misses = 3
        hub.on_message(self.sock, "bifrost/projector/projector/set", "ON")
        self.wait_command()
        self.assertEqual(self.adb.commands, ["input keyevent KEYCODE_WAKEUP"])
        self.assertEqual(self.reconnects, 3)  # a dead connection is dropped before each new try

    def test_app_launches_once_the_projector_woke(self):
        self.adb.misses = 2
        hub.on_message(self.sock, "bifrost/projector/projector/app/set", "com.netflix.ninja")
        self.assertTrue(self.adb.done.wait(2))
        time.sleep(0.1)
        self.assertEqual(self.adb.commands, ["input keyevent KEYCODE_WAKEUP",
                                             "monkey -p com.netflix.ninja -c android.intent.category.LEANBACK_LAUNCHER 1"])

    def test_a_newer_order_ends_the_tries(self):
        self.adb.misses = 1000
        hub.on_message(self.sock, "bifrost/projector/projector/set", "ON")
        time.sleep(0.1)
        hub.on_message(self.sock, "bifrost/projector/projector/set", "OFF")
        time.sleep(0.1)
        self.adb.misses = 0
        self.wait_command()
        time.sleep(0.2)
        self.assertEqual(self.adb.commands, ["input keyevent KEYCODE_SLEEP"])  # no WAKEUP after the OFF

    def test_tap_lands_on_the_screen_the_projector_renders(self):
        hub.projector_command("projector", tap=(0.5, 1))
        self.wait_command()
        self.assertEqual(self.adb.commands, ["input tap 960 1079"])  # the 1080p override, not the 4K panel

    def test_a_key_does_not_end_the_tries(self):
        self.adb.misses = 2
        hub.on_message(self.sock, "bifrost/projector/projector/set", "ON")
        hub.on_message(self.sock, "bifrost/projector/projector/key", "VOLUME_UP")
        time.sleep(0.5)
        self.assertIn("input keyevent KEYCODE_WAKEUP", self.adb.commands)

    def test_a_broker_gone_while_publishing_still_keeps_the_state(self):
        class Gone:
            def sendall(self, data):
                raise OSError("broken pipe")
        hub.conn = Gone()
        with self.assertLogs("hub", "WARNING") as logs:
            self.look()
        self.assertEqual(logs.output, ["WARNING:hub:projector: broken pipe"])
        p = hub.snapshot()["projectors"][0]
        self.assertEqual((p["on"], p["app"], p["error"]), (True, "com.netflix.ninja", None))

    def test_an_error_is_logged_once_and_the_screen_stays(self):
        self.on()
        with self.assertLogs("hub", "WARNING") as logs:
            hub.projector_seen("projector", None, "com.netflix.ninja", "adb: offline")
            hub.projector_seen("projector", None, None, "adb: offline")
        self.assertEqual(logs.output, ["WARNING:hub:projector: adb: offline"])
        p = hub.snapshot()["projectors"][0]
        self.assertEqual((p["on"], p["app"], p["error"]), (None, None, "adb: offline"))
        self.assertEqual(self.moves, [])  # the first look at start is no turning on

    def test_no_screen_without_a_cover(self):
        cfg = hub.PROJECTOR_BY_ID["projector"]
        cover = cfg.pop("cover")
        try:
            self.off()
            self.on()
        finally:
            cfg["cover"] = cover
        self.assertEqual(self.moves, [])

    def test_off_after_on_at_start_closes_later_and_logs(self):
        with hub.lock:
            hub.state["covers"]["screen"]["pos"] = 0.0
        self.on()
        with self.assertLogs("hub", "INFO") as logs:
            self.off()
        self.assertIn("INFO:hub:projector turned off", logs.output)
        self.assertIsNotNone(hub.snapshot()["projectors"][0]["closes_at"])

    def test_focus_without_a_focus_action_is_logged(self):
        cfg = hub.PROJECTOR_BY_ID["projector"]
        focus = cfg.pop("focus")
        try:
            with self.assertLogs("hub", "WARNING") as logs:
                hub.projector_command("projector", focus=True)
                time.sleep(0.2)
        finally:
            cfg["focus"] = focus
        self.assertEqual(logs.output, ["WARNING:hub:projector: no focus action for this projector"])
        self.assertEqual(self.adb.commands, [])

    def test_each_order_is_logged_but_a_tap(self):
        for kw, said in (({"on": True}, "ON"), ({"on": False}, "OFF"), ({"focus": True}, "focus"),
                         ({"key": "BACK"}, "BACK"), ({"app": "com.netflix.ninja"}, "com.netflix.ninja"),
                         ({"app": hub.HOME}, hub.HOME)):
            with self.subTest(kw=kw), self.assertLogs("hub", "INFO") as logs:
                self.adb.done.clear()
                hub.projector_command("projector", **kw)
                self.wait_command()
                time.sleep(0.05)
            self.assertIn(f"INFO:hub:projector -> {said}", logs.output)
        with self.assertNoLogs("hub", "INFO"):
            self.adb.done.clear()
            hub.projector_command("projector", tap=(0, 0))
            self.wait_command()
        self.assertEqual(self.adb.commands, [
            "input keyevent KEYCODE_WAKEUP", "input keyevent KEYCODE_SLEEP", "am broadcast -a com.jmgo.tof.autofocus",
            "input keyevent KEYCODE_BACK", "input keyevent KEYCODE_WAKEUP",
            "monkey -p com.netflix.ninja -c android.intent.category.LEANBACK_LAUNCHER 1",
            "input keyevent KEYCODE_WAKEUP", "input keyevent KEYCODE_HOME", "input tap 0 0"])


class HealthAdb:
    """Stands for adb on a projector with this much memory, up for that long, the vital process at that pid."""

    def __init__(self):
        self.memory, self.uptime, self.vital, self.calls = 800, 50000, "2112", []

    def __call__(self, projector, *args):
        cmd = " ".join(args)
        self.calls.append(cmd)
        if "MemAvailable" in cmd:
            return f"MemAvailable:     {self.memory * 1024} kB\n{self.uptime}.12 98765.43\nvital={self.vital}\n"
        if "boot_completed" in cmd:
            return "1\n"
        return ""

    def actions(self):
        return [c for c in self.calls if "MemAvailable" not in c and "boot_completed" not in c]


class HealthTest(unittest.TestCase):
    NIGHT = hub.datetime(2026, 11, 2, 4, 40)
    DAY = hub.datetime(2026, 11, 2, 15, 0)

    def setUp(self):
        reset()
        with hub.lock:
            hub.state["projectors"]["projector"].update(on=True, known=True, app=hub.HOME, memory=None, vital_deaths=0,
                                                        due=None, restarting=False, restarted_at=None, restart_reason=None)
        hub.vitals["projector"], hub.nights["projector"] = None, None
        hub.last_restart["projector"] = float("-inf")
        self.adb, self.real = HealthAdb(), (hub.adb, hub.reconnect, hub.RESTART_SETTLE, hub.WAKE_RETRY)
        hub.adb, hub.reconnect, hub.RESTART_SETTLE, hub.WAKE_RETRY = self.adb, lambda projector: None, 0, 0.01

    def tearDown(self):
        hub.adb, hub.reconnect, hub.RESTART_SETTLE, hub.WAKE_RETRY = self.real

    def check(self, when=DAY):
        hub.checkup("projector", when)

    def watching(self, app="com.netflix.ninja"):
        with hub.lock:
            hub.state["projectors"]["projector"]["app"] = app

    def test_all_well_does_nothing_but_keep_the_memory(self):
        self.check()
        self.check()
        self.assertEqual(self.adb.actions(), [])
        p = hub.snapshot()["projectors"][0]
        self.assertEqual((p["memory"], p["vital_deaths"], p["due"]), (800, 0, None))

    def test_short_of_memory_kills_the_background(self):
        self.adb.memory = 150
        self.check()
        self.assertEqual(self.adb.actions(), ["shell am kill-all"])

    def test_vital_killed_while_watching_cleans_then_restarts_once_nobody_watches(self):
        self.watching()
        self.check()
        self.adb.vital = "4382"  # killed, started again
        self.check()
        self.assertEqual(self.adb.actions(), ["shell am kill-all"])
        self.assertEqual(hub.snapshot()["projectors"][0]["vital_deaths"], 1)
        self.check()
        self.assertEqual(self.adb.actions(), ["shell am kill-all"])  # still watching: no restart
        self.watching(hub.HOME)
        self.check()
        self.assertEqual(self.adb.actions(), ["shell am kill-all", "reboot"])
        p = hub.snapshot()["projectors"][0]
        self.assertEqual((p["restarting"], p["due"], p["restart_reason"]), (False, None, "com.jmgo.hippo was killed"))

    def test_vital_gone_counts_as_killed(self):
        self.check()
        self.adb.vital = ""
        self.check()
        self.assertEqual(self.adb.actions(), ["reboot"])  # on the launcher: nobody watches

    def test_a_restart_back_to_sleep_when_it_was_off(self):
        with hub.lock:
            hub.state["projectors"]["projector"].update(on=False, known=False, app=None)
        self.check()
        self.adb.vital = "4382"
        self.check()
        self.assertEqual(self.adb.actions(), ["reboot", "shell input keyevent KEYCODE_SLEEP"])

    def test_no_second_restart_within_the_gap(self):
        self.check()
        self.adb.vital = "4382"
        self.check()
        self.adb.vital = "5000"
        self.check()  # the restarted one: no death
        self.adb.vital = "6000"
        self.check()
        self.assertEqual(self.adb.actions(), ["reboot", "shell am kill-all"])
        self.assertIsNotNone(hub.snapshot()["projectors"][0]["due"])  # kept for later

    def test_nightly_restart_once_nobody_watches_once_a_night(self):
        self.check(hub.datetime(2026, 11, 2, 4, 20))
        self.assertEqual(self.adb.actions(), [])  # before reboot_at
        self.watching()
        self.check(self.NIGHT)
        self.assertEqual(self.adb.actions(), [])  # someone watches at 4:40
        self.watching(hub.SCREENSAVER)
        self.check(self.NIGHT)
        self.assertEqual(self.adb.actions(), ["reboot"])
        hub.last_restart["projector"] = float("-inf")
        self.check(self.NIGHT)
        self.assertEqual(self.adb.actions(), ["reboot"])  # once a night
        self.check(hub.datetime(2026, 11, 3, 8, 0))
        self.assertEqual(self.adb.actions(), ["reboot"])  # past the window

    def test_no_nightly_restart_just_after_one(self):
        self.adb.uptime = 600
        self.check(self.NIGHT)
        self.assertEqual(self.adb.actions(), [])

    def test_unreachable_or_restarting_is_left_alone(self):
        with hub.lock:
            hub.state["projectors"]["projector"]["on"] = None
        self.check(self.NIGHT)
        with hub.lock:
            hub.state["projectors"]["projector"].update(on=True, restarting=True)
        self.check(self.NIGHT)
        self.assertEqual(self.adb.calls, [])

    def test_focus_and_restart_on_demand(self):
        hub.projector_command("projector", focus=True)
        hub.projector_command("projector", restart=True)
        time.sleep(0.3)
        self.assertIn("shell am broadcast -a com.jmgo.tof.autofocus", self.adb.actions())
        self.assertIn("reboot", self.adb.actions())

    def test_focus_again_once_the_screen_came_down(self):
        real = hub.projector_command
        orders = []
        hub.projector_command = lambda pid, **kw: orders.append(kw)
        cover = hub.COVER_BY_ID["screen"]
        saved = cover.get("open_time")
        cover["open_time"] = 0
        try:
            hub._focus_later("projector")
            time.sleep(2.3)
        finally:
            hub.projector_command = real
            cover["open_time"] = saved
        self.assertEqual(orders, [{"focus": True}])

    def test_no_memory_from_the_projector_is_an_error(self):
        hub.adb = lambda projector, *args: "error: closed"
        with self.assertRaises(OSError):
            self.check()
        self.assertIsNone(hub.snapshot()["projectors"][0]["memory"])

    def test_without_vital_nor_reboot_at_only_the_memory_counts(self):
        cfg = hub.PROJECTOR_BY_ID["projector"]
        saved = {k: cfg.pop(k) for k in ("vital", "reboot_at")}
        try:
            self.check(self.NIGHT)
            self.adb.vital = "4382"
            self.check(self.NIGHT)
            self.adb.memory = 100
            self.check(self.NIGHT)
        finally:
            cfg.update(saved)
        self.assertEqual(self.adb.actions(), ["shell am kill-all"])
        self.assertTrue(all("pidof" not in c for c in self.adb.calls))
        self.assertIsNone(hub.vitals["projector"])

    def test_a_vital_first_seen_is_no_death(self):
        self.adb.vital = ""
        self.check()
        self.adb.vital = "2112"
        self.check()
        self.assertEqual((self.adb.actions(), hub.snapshot()["projectors"][0]["vital_deaths"]), ([], 0))

    def test_the_first_reason_stays(self):
        self.watching()
        with hub.lock:
            hub.state["projectors"]["projector"]["due"] = "nightly"
        self.check()
        self.adb.vital = "4382"
        self.check()
        self.assertEqual(hub.snapshot()["projectors"][0]["due"], "nightly")


def rule(at, on, days=None):
    return {"at": at, "on": on, "days": days or list(range(1, 8))}


SUNDAY = datetime(2026, 11, 8, 9, 0)  # a Sunday


class ScheduleTest(unittest.TestCase):
    def setUp(self):
        reset()
        self.sock = FakeSock()
        hub.conn = self.sock
        SCHEDULES.unlink(missing_ok=True)

    def tearDown(self):
        hub.conn = None

    def sent(self):
        return [p for t, p, _ in self.sock.published if t == "433/send"]

    def test_rules_are_tidied(self):
        self.assertEqual(hub.check_rules([{"at": "08:30", "on": True}, {"at": "23:30", "on": False, "days": [7, 6, 6]}]),
                         [rule("08:30", True), rule("23:30", False, [6, 7])])
        self.assertEqual(hub.check_rules([]), [])

    def test_bad_rules_are_refused(self):
        for rules in ({}, "08:30", [None], [["08:30", True]], [{"at": "8:30", "on": True}], [{"at": "24:00", "on": True}],
                      [{"at": "08:30", "on": "yes"}], [{"at": "08:30"}], [{"on": True}], [{"at": "08:30", "on": True, "days": []}],
                      [{"at": "08:30", "on": True, "days": [0]}], [{"at": "08:30", "on": True, "days": [8]}],
                      [{"at": "08:30", "on": True, "days": "1"}], [{"at": "08:30", "on": True, "days": [True]}],
                      [{"at": "08:30", "on": True, "outlet": "fan"}], [rule("08:30", True)] * 21):
            with self.subTest(rules=rules):
                self.assertRaises(ValueError, hub.check_rules, rules)

    def test_the_rule_in_force_is_the_latest_within_a_day(self):
        rules = [rule("08:30", True), rule("23:30", False)]
        self.assertEqual(hub.due(rules, SUNDAY), (datetime(2026, 11, 8, 8, 30), rules[0]))
        self.assertEqual(hub.due(rules, datetime(2026, 11, 8, 8, 30)), (datetime(2026, 11, 8, 8, 30), rules[0]))
        self.assertEqual(hub.due(rules, datetime(2026, 11, 8, 23, 29)), (datetime(2026, 11, 8, 8, 30), rules[0]))
        self.assertEqual(hub.due(rules, datetime(2026, 11, 9, 0, 10)), (datetime(2026, 11, 8, 23, 30), rules[1]))
        self.assertIsNone(hub.due([], SUNDAY))
        # weekdays only: on Sunday, Saturday's and Sunday's rules do not fall; Friday's is too old
        weekdays = [rule("08:30", True, [1, 2, 3, 4, 5])]
        self.assertIsNone(hub.due(weekdays, SUNDAY))
        self.assertEqual(hub.due(weekdays, datetime(2026, 11, 7, 8, 0)), (datetime(2026, 11, 6, 8, 30), weekdays[0]))
        # two rules at the same moment: the last one of the list
        same = [rule("08:30", True), rule("08:30", False)]
        self.assertEqual(hub.due(same, SUNDAY), (datetime(2026, 11, 8, 8, 30), same[1]))

    def test_the_next_rule(self):
        rules = [rule("08:30", True), rule("23:30", False, [1, 2, 3, 4, 5])]
        self.assertEqual(hub.upcoming(rules, SUNDAY), (datetime(2026, 11, 9, 8, 30), rules[0]))
        self.assertEqual(hub.upcoming(rules, datetime(2026, 11, 9, 9, 0)), (datetime(2026, 11, 9, 23, 30), rules[1]))
        self.assertEqual(hub.upcoming([rules[1]], SUNDAY), (datetime(2026, 11, 9, 23, 30), rules[1]))
        self.assertIsNone(hub.upcoming([], SUNDAY))

    def test_tick_applies_each_rule_once_and_catches_up(self):
        hub.schedules["terrarium"] = [rule("08:30", True), rule("23:30", False)]
        hub.tick(SUNDAY)  # 09:00: the 08:30 rule was missed, it applies now
        self.assertEqual(self.sent(), ["1111"])
        hub.tick(datetime(2026, 11, 8, 12, 0))  # applied already
        hub.tick(datetime(2026, 11, 8, 23, 29))
        self.assertEqual(self.sent(), ["1111"])
        hub.tick(datetime(2026, 11, 8, 23, 30))
        hub.tick(datetime(2026, 11, 9, 0, 5))
        self.assertEqual(self.sent(), ["1111", "2222"])
        hub.tick(datetime(2026, 11, 9, 8, 30))  # the next day, the same rule falls again
        self.assertEqual(self.sent(), ["1111", "2222", "1111"])
        self.assertEqual(hub.applied, {"terrarium": "2026-11-09 08:30 on"})
        self.assertEqual(json.loads(SCHEDULES.read_text())["applied"], {"terrarium": "2026-11-09 08:30 on"})

    def test_tick_waits_for_the_broker(self):
        hub.schedules["fan"] = [rule("08:30", True)]
        hub.conn = None
        hub.tick(SUNDAY)
        self.assertEqual(hub.applied, {})
        hub.conn = self.sock
        hub.tick(SUNDAY)
        self.assertEqual(self.sent(), ["3333"])

    def test_tick_does_nothing_without_a_rule_in_force(self):
        hub.schedules["fan"] = [rule("08:30", True, [1, 2, 3, 4, 5])]
        hub.tick(SUNDAY)
        self.assertEqual(self.sent(), [])
        self.assertFalse(SCHEDULES.exists())

    def test_set_schedule_saves_and_applies_at_once(self):
        just_passed = (datetime.now() - timedelta(minutes=1)).strftime("%H:%M")
        hub.set_schedule("fan", [rule(just_passed, False)])
        self.assertEqual(self.sent(), ["4444"])
        self.assertEqual(json.loads(SCHEDULES.read_text())["rules"], {"fan": [rule(just_passed, False)]})
        self.assertEqual(hub._load_schedules(), ({"fan": [rule(just_passed, False)]}, hub.applied))
        hub.set_schedule("fan", [])
        self.assertEqual(hub.schedules, {})
        self.assertEqual(json.loads(SCHEDULES.read_text())["rules"], {})

    def test_loading_drops_unknown_outlets_and_bad_files(self):
        SCHEDULES.parent.mkdir(parents=True, exist_ok=True)
        SCHEDULES.write_text(json.dumps({"rules": {"fan": [rule("08:30", True)], "gone": []}, "applied": {"gone": "x", "fan": "k"}}))
        self.assertEqual(hub._load_schedules(), ({"fan": [rule("08:30", True)]}, {"fan": "k"}))
        SCHEDULES.write_text("{not json")
        self.assertEqual(hub._load_schedules(), ({}, {}))
        SCHEDULES.unlink()
        self.assertEqual(hub._load_schedules(), ({}, {}))

    def test_snapshot_tells_the_schedule_and_the_next_rule(self):
        hub.schedules["fan"] = [rule("08:30", True), rule("23:30", False)]
        outlets = {o["id"]: o for o in hub.snapshot()["outlets"]}
        self.assertEqual(outlets["terrarium"]["schedule"], [])
        self.assertIsNone(outlets["terrarium"]["next"])
        self.assertEqual(outlets["fan"]["schedule"], hub.schedules["fan"])
        expected = hub.upcoming(hub.schedules["fan"], datetime.now())
        self.assertEqual(outlets["fan"]["next"], {"at": expected[0].astimezone().isoformat(timespec="minutes"), "on": expected[1]["on"]})


class WebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), hub.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        reset()
        self.sock = FakeSock()
        hub.conn = self.sock

    def tearDown(self):
        hub.conn = None

    def post(self, path, body, ctype="application/json"):
        req = urllib.request.Request(self.url + path, data=body.encode(), method="POST", headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_status_json_with_cors(self):
        with urllib.request.urlopen(self.url + "/api/status.json", timeout=5) as r:
            self.assertEqual(r.headers["Access-Control-Allow-Origin"], "*")
            self.assertEqual([o["id"] for o in json.loads(r.read())["outlets"]], ["terrarium", "fan"])

    def test_dashboard_is_served(self):
        with urllib.request.urlopen(self.url + "/", timeout=5) as r:
            self.assertIn(b"<title>Bifrost</title>", r.read())

    def test_post_switches_the_outlet(self):
        self.assertEqual(self.post("/api/outlets/fan", '{"on": true}'), (202, {"id": "fan", "on": True}))
        self.assertEqual(self.sock.published, [("433/send", "3333", False)])

    def test_post_refuses_what_is_not_json(self):
        # what a form on another site could send without asking the browser first
        self.assertEqual(self.post("/api/outlets/fan", "on=true", "text/plain")[0], 415)
        self.assertEqual(self.sock.published, [])

    def test_post_errors(self):
        self.assertEqual(self.post("/api/outlets/nope", '{"on": true}')[0], 404)
        self.assertEqual(self.post("/api/outlets/fan", '{"on": "yes"}')[0], 400)
        self.assertEqual(self.post("/api/outlets/fan", "not json")[0], 400)
        hub.conn = None
        self.assertEqual(self.post("/api/outlets/fan", '{"on": true}')[0], 503)
        self.assertEqual(self.sock.published, [])

    def test_post_moves_the_cover(self):
        with hub.lock:
            hub.state["covers"]["screen"]["pos"] = 0.0
        self.assertEqual(self.post("/api/covers/screen", '{"position": 100}'), (202, {"id": "screen", "position": 100}))
        self.assertEqual(self.post("/api/covers/screen", '{"stop": true}'), (202, {"id": "screen", "stop": True}))
        settle()
        self.assertEqual([p for t, p, _ in self.sock.published if t == "433/send"], ["5553 188", "5552 188"])

    def test_post_presses_and_declares_the_cover(self):
        with hub.lock:
            hub.state["covers"]["screen"]["pos"] = 100.0
        self.assertEqual(self.post("/api/covers/screen", '{"press": "up"}'), (202, {"id": "screen", "press": "up"}))
        settle()
        self.assertEqual(self.post("/api/covers/screen", '{"declare": 100}'), (202, {"id": "screen", "declare": 100}))
        self.assertEqual([p for t, p, _ in self.sock.published if t == "433/send"], ["5551 188", "5552 188"])
        self.assertEqual(screen()["position"], 100)

    def test_post_cover_errors(self):
        self.assertEqual(self.post("/api/covers/nope", '{"position": 1}'), (404, {"error": "unknown cover"}))
        for body in ('{"position": 101}', '{"position": "50"}', '{"position": true}', '{"stop": false}', "[]",
                     '{"press": "left"}', '{"press": "UP"}', '{"declare": 101}', '{"declare": "0"}',
                     '{"press": "up", "stop": true}'):
            with self.subTest(body=body):
                self.assertEqual(self.post("/api/covers/screen", body)[0], 400)
        self.assertEqual(self.sock.published, [])

    def test_post_drives_the_projector(self):
        fake, real = FakeAdb(), hub.adb
        hub.adb = fake
        try:
            self.assertEqual(self.post("/api/projectors/projector", '{"key": "HOME"}'), (202, {"id": "projector", "key": "HOME"}))
            self.assertTrue(fake.done.wait(2))
            self.assertEqual(fake.commands, ["input keyevent KEYCODE_HOME"])
            fake.done.clear()
            self.assertEqual(self.post("/api/projectors/projector", '{"tap": [0, 0.25]}')[0], 202)
            self.assertTrue(fake.done.wait(2))
            self.assertEqual(fake.commands[-1], "input tap 0 270")
            for body in ('{"tap": [0.5]}', '{"tap": [1.5, 0]}', '{"tap": [true, 0]}', '{"tap": ["0; reboot", 0]}', '{"tap": "1 1"}',
                         '{"key": "HOME; reboot"}', '{"app": "a b"}', '{"on": 1}', '{"on": true, "key": "HOME"}', "{}"):
                with self.subTest(body=body):
                    self.assertEqual(self.post("/api/projectors/projector", body)[0], 400)
            self.assertEqual(self.post("/api/projectors/nope", '{"on": true}'), (404, {"error": "unknown projector"}))
        finally:
            hub.adb = real

    def put(self, path, body, ctype="application/json"):
        req = urllib.request.Request(self.url + path, data=body.encode(), method="PUT", headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_put_sets_the_schedule(self):
        just_passed = (datetime.now() - timedelta(minutes=1)).strftime("%H:%M")
        rules = [{"at": just_passed, "on": True}, {"at": "23:30", "on": False, "days": [5, 6, 7]}]
        self.assertEqual(self.put("/api/outlets/fan/schedule", json.dumps(rules)),
                         (200, {"id": "fan", "schedule": [rule(just_passed, True), rule("23:30", False, [5, 6, 7])]}))
        self.assertEqual(self.sock.published, [("433/send", "3333", False)])  # the rule just passed applies at once
        with urllib.request.urlopen(self.url + "/api/status.json", timeout=5) as r:
            fan = [o for o in json.loads(r.read())["outlets"] if o["id"] == "fan"][0]
        self.assertEqual(fan["schedule"], [rule(just_passed, True), rule("23:30", False, [5, 6, 7])])
        self.assertIsNotNone(fan["next"])
        self.assertEqual(self.put("/api/outlets/fan/schedule", "[]"), (200, {"id": "fan", "schedule": []}))

    def test_put_errors(self):
        self.assertEqual(self.put("/api/outlets/nope/schedule", "[]"), (404, {"error": "unknown outlet"}))
        self.assertEqual(self.put("/api/outlets/fan", "[]"), (404, {"error": "not found"}))
        self.assertEqual(self.put("/api/covers/screen/schedule", "[]"), (404, {"error": "not found"}))
        self.assertEqual(self.put("/api/outlets/fan/schedule", "[]", "text/plain")[0], 415)
        for body in ("not json", "{}", '[{"at": "8:30", "on": true}]', '[{"at": "08:30", "on": 1}]', '[{"at": "08:30", "on": true, "days": [0]}]'):
            with self.subTest(body=body):
                self.assertEqual(self.put("/api/outlets/fan/schedule", body), (400, {"error": hub.SCHEDULE_EXPECTED}))
        self.assertEqual(self.sock.published, [])
        self.assertEqual(hub.schedules, {})

    def test_post_has_no_cors(self):
        req = urllib.request.Request(self.url + "/api/outlets/fan", data=b'{"on": false}', method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertIsNone(r.headers["Access-Control-Allow-Origin"])

    def test_post_error_messages(self):
        for path, body, expected in (
                ("/api/outlets/fan", "[]", (400, {"error": 'expected {"on": true} or {"on": false}'})),
                ("/api/outlets/fan", "{}", (400, {"error": 'expected {"on": true} or {"on": false}'})),
                ("/api/covers/screen", "{}", (400, {"error": 'expected {"position": 0-100}, {"stop": true}, '
                                                              '{"press": "up"} or {"declare": 0}'})),
                ("/api/covers/screen", '{"open": 1}', (400, {"error": 'expected {"position": 0-100}, {"stop": true}, '
                                                                     '{"press": "up"} or {"declare": 0}'})),
                ("/api/projectors/projector", '{"app": 3}', (400, {"error": 'expected {"on": true}, {"key": "HOME"}, '
                                                                           '{"app": "<package>"}, {"tap": [0.5, 0.5]}, '
                                                                           '{"focus": true} or {"restart": true}'})),
                ("/api/projectors/projector", '{"focus": false}', (400, {"error": 'expected {"on": true}, {"key": "HOME"}, '
                                                                                 '{"app": "<package>"}, {"tap": [0.5, 0.5]}, '
                                                                                 '{"focus": true} or {"restart": true}'})),
                ("/api/lamps/fan", '{"on": true}', (404, {"error": "unknown outlet"})),
                ("/api/outlets", '{"on": true}', (404, {"error": "unknown outlet"})),
                ("/api/outlets/fan/x", '{"on": true}', (404, {"error": "unknown outlet"})),
                ("/other/outlets/fan", '{"on": true}', (404, {"error": "unknown outlet"})),
                ("/api/projectors/fan", '{"on": true}', (404, {"error": "unknown projector"})),
                ("/api/covers/fan?x=1", '{"on": true}', (404, {"error": "unknown cover"}))):
            with self.subTest(path=path, body=body):
                self.assertEqual(self.post(path, body), expected)
        self.assertEqual(self.post("/api/outlets/fan?x=1", '{"on": false}', "application/json; charset=utf-8"),
                         (202, {"id": "fan", "on": False}))

    def test_post_every_projector_order(self):
        orders, real = [], hub.projector_command
        hub.projector_command = lambda pid, **kw: orders.append((pid, kw))
        try:
            for body in ({"on": False}, {"focus": True}, {"restart": True}, {"app": "home"}, {"app": "com.netflix.ninja"},
                         {"tap": [1, 0.5]}, {"key": "DPAD_UP"}):
                with self.subTest(body=body):
                    self.assertEqual(self.post("/api/projectors/projector", json.dumps(body)), (202, {"id": "projector", **body}))
            hub.conn = None  # adb, not the broker
            self.assertEqual(self.post("/api/projectors/projector", '{"on": true}')[0], 202)
        finally:
            hub.projector_command = real
        self.assertEqual(len(orders), 8)
        self.assertEqual(orders[5], ("projector", {"tap": [1, 0.5]}))

    def test_post_cover_without_the_broker_or_with_it_gone(self):
        hub.conn = None
        self.assertEqual(self.post("/api/covers/screen", '{"position": 10}'), (503, {"error": "the broker is not connected"}))

        class Gone:
            def sendall(self, data):
                raise OSError("broken pipe")
        hub.conn = Gone()
        self.assertEqual(self.post("/api/outlets/fan", '{"on": true}'), (503, {"error": "broker: broken pipe"}))

    def test_post_declare_without_a_move(self):
        self.assertEqual(self.post("/api/covers/screen", '{"declare": 0}'), (202, {"id": "screen", "declare": 0}))
        self.assertEqual(self.post("/api/covers/screen", '{"press": "stop"}'), (202, {"id": "screen", "press": "stop"}))
        settle()
        self.assertEqual([p for t, p, _ in self.sock.published if t == "433/send"], ["5552 188"])
        self.assertEqual(screen()["position"], 0)

    def test_no_file_outside_the_dashboard(self):
        for path in ("/../hub/hub.py", "/%2e%2e/outlets.toml", "/tests/"):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(self.url + path, timeout=5)


if __name__ == "__main__":
    unittest.main()


JPEG_A = b"\xff\xd8a-frame\xff\xd9"
JPEG_B = b"\xff\xd8b-frame\xff\xd9"


class MirrorTest(unittest.TestCase):
    """The mirror end to end, with a script for adb, cat for ffmpeg, and a TCP server for the
    projector's end of the forward that sends JPEGs (cat passes them through)."""

    def setUp(self):
        import tempfile
        self.dir = tempfile.TemporaryDirectory()
        adb = Path(self.dir.name, "adb")
        adb.write_text('#!/bin/sh\n[ "$3" = shell ] && exec sleep 30\nexit 0\n')
        ffmpeg = Path(self.dir.name, "ffmpeg")
        ffmpeg.write_text("#!/bin/sh\nexec cat\n")
        adb.chmod(0o755), ffmpeg.chmod(0o755)
        self.real = (hub.ADB, hub.FFMPEG, hub.adb, hub.MIRROR_LINGER, hub.MIRROR_START, hub.MIRROR_REPEAT, hub.mirrors)
        hub.ADB, hub.FFMPEG, hub.adb = str(adb), str(ffmpeg), FakeAdb()
        hub.MIRROR_LINGER, hub.MIRROR_START, hub.MIRROR_REPEAT = 1, 3, 0.2
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.mirror = hub.Mirror(hub.PROJECTOR_BY_ID["projector"], self.listener.getsockname()[1])
        hub.mirrors = {"projector": self.mirror}
        self.clients = []
        threading.Thread(target=self.projector, daemon=True).start()

    def projector(self):
        while True:
            try:
                c, _ = self.listener.accept()
            except OSError:
                return
            self.clients.append(c)
            c.sendall(JPEG_A[:5])  # a JPEG cut in two
            time.sleep(0.1)
            c.sendall(JPEG_A[5:] + JPEG_B)

    def tearDown(self):
        self.listener.close()
        for c in self.clients:
            c.close()
        hub.ADB, hub.FFMPEG, hub.adb, hub.MIRROR_LINGER, hub.MIRROR_START, hub.MIRROR_REPEAT, hub.mirrors = self.real
        self.dir.cleanup()

    def test_split_jpegs(self):
        self.assertEqual(hub.split_jpegs(JPEG_A + JPEG_B[:4]), ([JPEG_A], JPEG_B[:4]))
        self.assertEqual(hub.split_jpegs(b"junk" + JPEG_A), ([JPEG_A], b""))

    def test_viewers_get_the_frames_and_the_mirror_stops_once_alone(self):
        frames = self.mirror.watch()
        got = [next(frames)]
        if got[0] != JPEG_B:  # the two may come in one read: only the latest is kept
            got.append(next(frames))
        self.assertEqual(got[-1], JPEG_B)
        late = self.mirror.watch()
        self.assertEqual(next(late), JPEG_B)  # a second viewer starts with the latest picture
        frames.close(), late.close()
        time.sleep(2.5)  # MIRROR_LINGER, checked every second
        self.assertFalse(self.mirror.running)
        self.assertEqual(len(self.clients), 1)  # one stream for both viewers

    @unittest.skipUnless(shutil.which("ffmpeg"), "no ffmpeg here")
    def test_ffmpeg_turns_h264_into_jpegs(self):
        h264 = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=15", "-t", "1",
                               "-c:v", "libx264", "-bf", "0", "-f", "h264", "pipe:1"], capture_output=True, check=True).stdout
        out = subprocess.run(["ffmpeg", *hub.TO_MJPEG], input=h264, capture_output=True, timeout=30)
        frames, _ = hub.split_jpegs(out.stdout)
        self.assertEqual(len(frames), 15, out.stderr.decode())

    def test_a_still_screen_sends_its_last_picture_again(self):
        frames = self.mirror.watch()
        while next(frames) != JPEG_B:
            pass
        start = time.monotonic()
        self.assertEqual(next(frames), JPEG_B)  # nothing new from the projector
        self.assertLess(time.monotonic() - start, 1)
        frames.close()

    def test_the_browser_gets_each_picture_with_its_boundary_and_leaving_stops_the_mirror(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), hub.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            c = socket.create_connection(server.server_address, timeout=5)
            c.sendall(b"GET /api/projectors/projector/screen.mjpg HTTP/1.1\r\nHost: x\r\n\r\n")
            got = b""
            while JPEG_B + b"\r\n--frame\r\n" not in got:  # the boundary right after: the browser shows it now
                got += c.recv(65536)
            self.assertIn(b"multipart/x-mixed-replace; boundary=frame", got)
            c.close()
            time.sleep(2.5)  # a repeat finds the browser gone, then MIRROR_LINGER
            self.assertFalse(self.mirror.running)
        finally:
            server.shutdown()
            server.server_close()

    def test_no_picture_is_an_error(self):
        self.listener.close()
        with self.assertRaises(OSError):
            next(self.mirror.watch())
