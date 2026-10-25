import json
import os
import socket
import struct
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["OUTLETS_FILE"] = str(ROOT / "tests" / "outlets.toml")
os.environ["HOMEBRIDGE_HOST"] = "127.0.0.1"
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
        for c in hub.state["covers"].values():
            if c["run"]:
                c["run"].set()
            c.clear()
            c.update(pos=None, dir=0, since=0.0, target=None, run=None, sent={})


hub.TOP_MARGIN, hub.REVERSE_PAUSE = 0.1, 0.05  # the test screen runs in 0.4 s down, 0.5 s up


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
        hub.on_message(self.sock, "bifrost/outlet/grow-lamp/set", "on")
        hub.on_message(self.sock, "bifrost/outlet/fan/set", "OFF")
        self.assertEqual(self.sock.published, [("433/send", "1111", False), ("433/send", "4444", False)])

    def test_set_ignores_unknown_outlets_and_values(self):
        hub.on_message(self.sock, "bifrost/outlet/nope/set", "ON")
        hub.on_message(self.sock, "bifrost/outlet/fan/set", "toggle")
        self.assertEqual(self.sock.published, [])

    def test_code_heard_publishes_the_state_retained(self):
        hub.on_message(self.sock, "433/receive", "2222")
        self.assertEqual(self.sock.published, [("bifrost/outlet/grow-lamp/state", "OFF", True)])
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

    def test_close_runs_past_the_time_into_the_top(self):
        self.at(100)
        start = time.time()
        hub.on_message(self.sock, "bifrost/cover/screen/set", "CLOSE")
        settle()
        self.assertAlmostEqual(time.time() - start, 0.5 + hub.TOP_MARGIN, delta=0.08)
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
            self.assertEqual([o["id"] for o in json.loads(r.read())["outlets"]], ["grow-lamp", "fan"])

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

    def test_post_cover_errors(self):
        self.assertEqual(self.post("/api/covers/nope", '{"position": 1}'), (404, {"error": "unknown cover"}))
        for body in ('{"position": 101}', '{"position": "50"}', '{"position": true}', '{"stop": false}', "[]"):
            with self.subTest(body=body):
                self.assertEqual(self.post("/api/covers/screen", body)[0], 400)
        self.assertEqual(self.sock.published, [])

    def test_post_has_no_cors(self):
        req = urllib.request.Request(self.url + "/api/outlets/fan", data=b'{"on": false}', method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertIsNone(r.headers["Access-Control-Allow-Origin"])

    def test_no_file_outside_the_dashboard(self):
        for path in ("/../hub/hub.py", "/%2e%2e/outlets.toml", "/tests/"):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(self.url + path, timeout=5)


if __name__ == "__main__":
    unittest.main()
