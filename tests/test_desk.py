import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

if sys.platform != "darwin":
    raise unittest.SkipTest("desk.py reads the idle time of macOS")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "desk"))
import desk  # noqa: E402

NEVER = float("-inf")


class DueTest(unittest.TestCase):
    def test_the_minute_beat(self):
        self.assertTrue(desk.due(1000, 0, NEVER, NEVER))           # first look
        self.assertFalse(desk.due(1059, 1059, 1000, 1000))         # used all along: within the minute
        self.assertTrue(desk.due(1060, 1060, 1000, 1000))
        self.assertTrue(desk.due(1060, 0, 1000, 0))                # away: the beat still goes

    def test_a_key_after_a_minute_is_told_at_once(self):
        self.assertTrue(desk.due(1030, 1029, 1000, 960))           # the hub knows of nothing for 70 s
        self.assertFalse(desk.due(1030, 1029, 1000, 990))          # it knew of a key 40 s ago
        self.assertFalse(desk.due(1030, 961, 1000, 960))           # no new key: the idle time wobbles


class Hub(BaseHTTPRequestHandler):
    bodies = []

    def do_POST(self):
        Hub.bodies.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


class StepTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Hub)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/api/presences/desk"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Hub.bodies = []

    def test_tells_the_hub_when_due(self):
        self.assertEqual(desk.step(self.url, NEVER, NEVER, 1000, 3.14), (1000, 996.86))
        self.assertEqual(Hub.bodies, [("/api/presences/desk", {"idle": 3.1})])
        self.assertEqual(desk.step(self.url, 1000, 996.86, 1010, 1), (1000, 996.86))
        self.assertEqual(len(Hub.bodies), 1)

    def test_the_hub_away_is_tried_again_and_logged_once(self):
        with mock.patch("sys.stderr") as err:
            self.assertEqual(desk.step("http://127.0.0.1:1/x", 0, 0, 1000, 1), (0, 0))
            self.assertEqual(desk.step("http://127.0.0.1:1/x", 0, 0, 1002, 1), (0, 0))
            logged = err.write.call_count
            self.assertEqual(desk.step(self.url, 0, 0, 1004, 1), (1004, 1003))  # back home
            desk.step("http://127.0.0.1:1/x", 0, 0, 1100, 1)
            self.assertEqual(err.write.call_count, 2 * logged)

    def test_reads_the_idle_time(self):
        self.assertGreaterEqual(desk.idle(), 0)

    def test_main_looks_every_poll(self):
        with mock.patch("desk.idle", return_value=5), mock.patch("time.sleep", side_effect=[None, StopIteration]):
            self.assertRaises(StopIteration, desk.main, self.url.rsplit("/api", 1)[0] + "/", "desk")
        self.assertEqual(Hub.bodies, [("/api/presences/desk", {"idle": 5})])
