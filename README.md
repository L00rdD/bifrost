<p align="center"><img src="dashboard/bifrost.svg" alt="Bifrost" width="140"></p>

# Bifrost

The bridge between the worlds of the house: 433 MHz radio, MQTT and HomeKit.
It runs on the Pi next to d-mediaserver and controls the RF outlets (first one:
the Sapling Sunrise grow lamp) from the iPhone Home app, Siri, or anything that
speaks MQTT.

```
iPhone (Home, Siri) ⇄ Homebridge :8581 ⇄ Mosquitto :1883 ⇄ hub ⇄ RF bridge (ESP8266) ⇄ 433 MHz ⇄ outlets
                       Sapling Sunrise ⇄ ┘
```

The RF codes live in **`outlets.toml`** only, which is not committed: anyone in radio
range could switch the outlets with them (copy `outlets.toml.example`). Everything
else (Homebridge, Sapling Sunrise, the home page card) knows an outlet by its `id`
and talks to the hub:

| Topic | Payload | |
| :--- | :--- | :--- |
| `bifrost/outlet/<id>/set` | `ON` / `OFF` | the hub sends the outlet's code on `433/send` |
| `bifrost/outlet/<id>/state` | `ON` / `OFF`, retained | from every code seen on `433/receive`: the remote, the Home app, the tracker, `make send` |

Retained means the broker keeps the last state: Homebridge gets it back as soon as it
restarts, and a client can confirm a switch by waiting for a fresh state.

| Part | Where | What |
| :--- | :--- | :--- |
| `mosquitto` | Pi, port `1883` | MQTT broker, login required (`MQTT_USER` / `MQTT_PASS`) |
| `homebridge` | Pi, host network, UI on `http://dpi.local:8581` | HomeKit bridge "Bifrost", plugin `homebridge-mqttthing`, one accessory per outlet of `outlets.toml` |
| `hub` (`bifrost-hub`) | Pi, port `8582` | outlet names ⇄ RF codes (`outlets.toml`), the dashboard on `http://dpi.local:8582`, and `/api/status.json` for the d-mediaserver home page card: broker, Homebridge, RF bridge, outlets, last code |
| `firmware/rf-bridge` | NodeMCU + 433 MHz TX (`D2`) and RX (`D1`) | Sends what arrives on `433/send`, publishes what it hears on `433/receive` |

All optional for d-mediaserver: its home page shows the Bifrost card only when
`:8582` answers, and its `autoheal` restarts these containers (label
`autoheal=true`) when their healthcheck fails, only if autoheal runs.

## MQTT topics

| Topic | Direction | Payload |
| :--- | :--- | :--- |
| `433/send` | to the bridge | a decimal code, sent as 24 bits, protocol 1 |
| `433/receive` | from the bridge | a decimal code heard on the air (one per press), and every code it sends: whoever switches an outlet, Homebridge sees the new state |
| `433/receive/raw` | from the bridge | the same code with its bits, protocol and pulse length |
| `433/status` | from the bridge | `boot`, then `online` every 10 s |

## Setup

```sh
cp .env.example .env     # fill it in (MQTT login = the one in firmware/rf-bridge/secrets.h)
cp outlets.toml.example outlets.toml   # the outlets and their RF codes (make sniff)
make env                 # copy .env to the Pi (~/bifrost)
make deploy              # render the Homebridge config, copy outlets.toml, start everything
make flash               # build and flash the RF bridge, board plugged in over USB
```

`firmware/rf-bridge/secrets.h` holds the Wi-Fi and MQTT login of the bridge
(not committed, copy `secrets.h.example`). `MQTT_HOST` is the
Pi's address, `192.168.1.229`: give the Pi a fixed lease in the box so it does
not move.

## Dashboard

`http://dpi.local:8582` shows each outlet with a switch, and the signal path from
HomeKit to the air. A switch sends `POST /api/outlets/<id>` with `{"on": true}`, then
waits for the RF bridge to echo the code: the bulb lights up when it does, and the
switch flips back if nothing comes within 6 s. The page takes the look of the
d-mediaserver hub (its theme kit on port 80) when the hub answers.

The POST only takes JSON and has no CORS header, so a page from another site cannot
switch the outlets through a visitor's browser. It has no login: anyone on the home
network can switch them, as with the remote.

## Tests

```sh
python3 -m unittest discover -s tests -t .
```

## Adding an RF outlet

1. `make sniff`, then press the remote's ON and OFF buttons: the codes show up on
   `433/receive`, with their protocol on `433/receive/raw`.
2. Check the outlet answers: `make send CODE=<on code>`.
3. Add an `[[outlet]]` to `outlets.toml` (id, name, on, off), then `make deploy`:
   the hub learns the codes and Homebridge gets the accessory.
4. In the Home app: add accessory, "More options", pick "Bifrost", enter the pin
   printed by `make logs` (the `HB_PIN` of `.env`).

RF outlets do not report their state: the state is the last code seen on
`433/receive`, which carries both the remote's presses and every code the bridge
sends. The outlet itself may still miss one, out of range.

The repo is the source of truth for the Homebridge config: `make deploy`
overwrites the one on the Pi, so edits made in the Homebridge UI's config editor
are lost at the next deploy (the accessories come from `outlets.toml`). Plugins installed from the UI stay.

## Origin

The firmware comes from `~/personal/domotics/rf_module/rf_sender`. Changes:
repeated presses of the same button are reported again after 1 s (they were
dropped until another code came), the serial log no longer prints the Wi-Fi
password and lists the networks in range at boot, `433/receive/raw` gives the
protocol of each code, and every code sent is echoed on `433/receive`.
