import json
import os
import struct
import sys
import threading
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
