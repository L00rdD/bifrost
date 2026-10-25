"""Fill homebridge/config.template.json with the values of .env and add one accessory per
outlet and cover of outlets.toml -> homebridge/config.json.

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
devices = tomllib.loads((root / "outlets.toml").read_text())
mqtt = {"accessory": "mqttthing", "url": "mqtt://127.0.0.1:1883", "username": env["MQTT_USER"], "password": env["MQTT_PASS"]}
for outlet in devices.get("outlet", []):
    config["accessories"].append({
        **mqtt,
        "type": "outlet",
        "name": outlet["name"],
        # names only: the hub turns ON / OFF into the outlet's codes and publishes the state
        "topics": {"setOn": f"bifrost/outlet/{outlet['id']}/set", "getOn": f"bifrost/outlet/{outlet['id']}/state"},
        "onValue": "ON",
        "offValue": "OFF",
    })
for cover in devices.get("cover", []):
    base = f"bifrost/cover/{cover['id']}"
    config["accessories"].append({
        **mqtt,
        "type": "windowCovering",  # a blind in the Home app: 100 % open = the screen down
        "name": cover["name"],
        "topics": {"setTargetPosition": f"{base}/set", "getTargetPosition": f"{base}/target",
                   "getCurrentPosition": f"{base}/position", "getPositionState": f"{base}/state"},
        "positionStateValues": ["DECREASING", "INCREASING", "STOPPED"],
    })
(root / "homebridge/config.json").write_text(json.dumps(config, indent=2) + "\n")
print(f"homebridge/config.json: {len(devices.get('outlet', []))} outlet(s), {len(devices.get('cover', []))} cover(s)")
