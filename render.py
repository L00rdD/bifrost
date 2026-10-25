"""Fill homebridge/config.template.json with the values of .env and add one accessory per
outlet of outlets.toml -> homebridge/config.json.

The repo is the source of truth: `make deploy` overwrites the config on the Pi,
so edit the template or outlets.toml here rather than the Homebridge UI's config editor."""
import json
import pathlib
import string
import sys
import tomllib

root = pathlib.Path(__file__).resolve().parent
env = {}
for line in (root / ".env").read_text().splitlines():
    if line.strip() and not line.lstrip().startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
missing = [k for k in ("MQTT_USER", "MQTT_PASS", "HB_USERNAME", "HB_PIN") if not env.get(k)]
if missing:
    sys.exit(f".env: missing {', '.join(missing)}")
if not (root / "outlets.toml").exists():
    sys.exit("outlets.toml missing: copy outlets.toml.example and put the RF codes in it")
config = json.loads(string.Template((root / "homebridge/config.template.json").read_text()).substitute(env))
for outlet in tomllib.loads((root / "outlets.toml").read_text())["outlet"]:
    config["accessories"].append({
        "accessory": "mqttthing",
        "type": "outlet",
        "name": outlet["name"],
        "url": "mqtt://127.0.0.1:1883",
        "username": env["MQTT_USER"],
        "password": env["MQTT_PASS"],
        # names only: the hub turns ON / OFF into the outlet's codes and publishes the state
        "topics": {"setOn": f"bifrost/outlet/{outlet['id']}/set", "getOn": f"bifrost/outlet/{outlet['id']}/state"},
        "onValue": "ON",
        "offValue": "OFF",
    })
(root / "homebridge/config.json").write_text(json.dumps(config, indent=2) + "\n")
print(f"homebridge/config.json: {len(config['accessories'])} outlet(s)")
