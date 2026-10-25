"""Fill homebridge/config.template.json with the values of .env and add one accessory per
outlet, cover and projector of outlets.toml -> homebridge/config.json.

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
for projector in devices.get("projector", []):
    base = f"bifrost/projector/{projector['id']}"
    config["accessories"].append({
        **mqtt,
        "type": "television",  # its apps are the inputs; the iPhone's remote (control centre) sends the keys
        "name": projector["name"],
        "topics": {"setActive": f"{base}/set", "getActive": f"{base}/state", "setActiveInput": f"{base}/app/set",
                   "getActiveInput": f"{base}/app", "setRemoteKey": f"{base}/key"},
        "inputs": [{"name": name, "value": package} for name, package in projector.get("apps", {}).items()],
        "onValue": "ON",
        "offValue": "OFF",
        # HomeKit's keys in its order (rewind, fast forward, next, previous, up, down, left, right,
        # select, back, exit, play/pause, 3 unused, info) as Android key names: the hub sends KEYCODE_<name>
        "remoteKeyValues": ["MEDIA_REWIND", "MEDIA_FAST_FORWARD", "MEDIA_NEXT", "MEDIA_PREVIOUS", "DPAD_UP", "DPAD_DOWN",
                            "DPAD_LEFT", "DPAD_RIGHT", "DPAD_CENTER", "BACK", "HOME", "MEDIA_PLAY_PAUSE", "UNUSED_12",
                            "UNUSED_13", "UNUSED_14", "MENU"],
    })
(root / "homebridge/config.json").write_text(json.dumps(config, indent=2) + "\n")
print(f"homebridge/config.json: {len(devices.get('outlet', []))} outlet(s), {len(devices.get('cover', []))} cover(s), "
      f"{len(devices.get('projector', []))} projector(s)")
