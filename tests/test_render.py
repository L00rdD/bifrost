import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = "MQTT_USER=u\nMQTT_PASS=p\nHB_USERNAME=0E:12:34:56:78:9A\nHB_PIN=123-45-678\n"


class RenderTest(unittest.TestCase):
    def render(self, env=ENV, outlets=True):
        """Runs render.py in a copy of the project; returns (exit code, output, config or None)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            shutil.copy(ROOT / "render.py", tmp)
            (tmp / "homebridge").mkdir()
            shutil.copy(ROOT / "homebridge/config.template.json", tmp / "homebridge")
            (tmp / ".env").write_text(env)
            if outlets:
                shutil.copy(ROOT / "tests/outlets.toml", tmp / "outlets.toml")
            r = subprocess.run([sys.executable, "-I", str(tmp / "render.py")], capture_output=True, text=True)
            out = tmp / "homebridge/config.json"
            return r.returncode, r.stdout + r.stderr, json.loads(out.read_text()) if out.exists() else None

    def test_one_accessory_per_outlet_by_name_only(self):
        code, _, config = self.render()
        self.assertEqual(code, 0)
        self.assertEqual(config["bridge"]["pin"], "123-45-678")
        fan = config["accessories"][1]
        self.assertEqual(fan["name"], "Fan")
        self.assertEqual(fan["topics"], {"setOn": "bifrost/outlet/fan/set", "getOn": "bifrost/outlet/fan/state"})
        self.assertNotIn("3333", json.dumps(config))  # the codes stay with the hub

    def test_cover_is_a_window_covering(self):
        _, out, config = self.render()
        self.assertIn("2 outlet(s), 1 cover(s), 1 projector(s)", out)
        screen = config["accessories"][2]
        self.assertEqual((screen["type"], screen["name"]), ("windowCovering", "Screen"))
        self.assertEqual(screen["topics"]["setTargetPosition"], "bifrost/cover/screen/set")
        self.assertEqual(screen["topics"]["getPositionState"], "bifrost/cover/screen/state")
        self.assertNotIn("5553", json.dumps(config))

    def test_projector_is_a_television_with_its_apps(self):
        _, out, config = self.render()
        self.assertIn("1 projector(s)", out)
        tv = config["accessories"][3]
        self.assertEqual((tv["type"], tv["name"], tv["onValue"]), ("television", "Projector", "ON"))
        self.assertEqual(tv["inputs"][0], {"name": "Netflix", "value": "com.netflix.ninja"})
        self.assertEqual(tv["topics"]["setRemoteKey"], "bifrost/projector/projector/key")
        self.assertEqual(len(tv["remoteKeyValues"]), 16)
        self.assertNotIn("10.0.0.9", json.dumps(config))  # the projector's address stays with the hub

    def test_missing_env_values(self):
        code, out, config = self.render(env="MQTT_USER=u\n")
        self.assertNotEqual(code, 0)
        self.assertIn("MQTT_PASS", out)
        self.assertIsNone(config)

    def test_missing_outlets_file(self):
        code, out, _ = self.render(outlets=False)
        self.assertNotEqual(code, 0)
        self.assertIn("outlets.toml.example", out)


if __name__ == "__main__":
    unittest.main()
