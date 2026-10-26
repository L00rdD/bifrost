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
| `bifrost/cover/<id>/set` | `0`-`100`, `OPEN`, `CLOSE`, `STOP` | moves a cover (the projection screen): 0 = rolled up, 100 = open |
| `bifrost/projector/<id>/set`, `/key`, `/app/set` | `ON` / `OFF`, an Android key (`HOME`, `DPAD_UP`…), an app package | the hub drives the projector over adb |
| `bifrost/projector/<id>/state`, `/app` | `ON` / `OFF`, the app in front, retained | from the hub's look at the projector every 5 s |
| `bifrost/cover/<id>/position`, `/target`, `/state` | `0`-`100`, `0`-`100`, `INCREASING` / `DECREASING` / `STOPPED`, retained | where the hub reckons the cover is, for HomeKit and the hub's next start |

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
| `433/send` | to the bridge | a decimal code, sent as 24 bits, protocol 1; `<code> <pulse>` for a remote faster or slower than its 350 us (`TE` of a Flipper capture) |
| `433/receive` | from the bridge | a decimal code heard on the air (one per press), and every code it sends: whoever switches an outlet, Homebridge sees the new state |
| `433/receive/raw` | from the bridge | the same code with its bits, protocol and pulse length |
| `433/status` | from the bridge | `boot`, then `online` every 10 s |
| `433/info` | from the bridge, retained | the build running, its IP and Wi-Fi signal: `Oct 26 2026 23:58:01 192.168.1.225 -61 dBm` |

## Setup

```sh
cp .env.example .env     # fill it in (MQTT login = the one in firmware/rf-bridge/secrets.h)
cp outlets.toml.example outlets.toml   # the outlets and their RF codes (make sniff)
make env                 # copy .env to the Pi (~/bifrost)
make deploy              # render the Homebridge config, copy outlets.toml, start everything
make flash               # build and flash the RF bridge, board plugged in over USB (the first time)
make ota                 # then: build and send it over Wi-Fi to rf-bridge.local, no USB
```

`firmware/rf-bridge/secrets.h` holds the Wi-Fi and MQTT login of the bridge, and `OTA_PASS`, the password of updates over Wi-Fi
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

## Adding a cover (projection screen)

A cover has up, stop and down buttons and does not report its position: the hub
times it. Opening sends down, then stop once the time measured for that distance
is over; closing sends up, then stop after `close_time`, no longer: the motor must
not run into its end stop. A new order mid-move goes through stop first. The remote's presses are
followed the same way (down then stop: the hub knows where it stopped).

1. With a Flipper Zero, *Sub-GHz > Read* each button: `Key` is the code in hex
   (`printf '%d' 0x1F37D4`), `TE` the pulse length.
2. Time it by hand: screen at the top, press down then stop at the projection
   height (`open_time`), then up until it stops at the top (`close_time`). Not with
   the Flipper's *Read RAW*: it loses the silences between presses, and the same
   38 s move came out at 25.7 s and 17.3 s.
3. Add a `[[cover]]` to `outlets.toml` (see the example), `make flash` if the bridge
   predates `<code> <pulse>`, then `make deploy`: the Home app gets a blind,
   100 % open = the screen down.

The first move after the hub starts with no position known (retained on the
broker otherwise) goes all the way up first. A down on the remote left to run
reaches the bottom of the screen, past 100 %: the estimate stops at 100.

The dashboard and the home page card get the covers in `/api/status.json`, and
`POST /api/covers/<id>` takes `{"position": 0-100}` or `{"stop": true}`.

## Adding a projector (Google TV)

The JMGO N1S 4K Mini ignores the power keys of the Google TV remote protocol (the one
of the phone app), so the hub drives it with adb, which also tells it whether the
projector is awake and which app is in front. Every 5 s the hub looks; when the
projector turns on, whoever turned it on, its `cover` comes down; once it has been
off for `close_after` (5 min), the cover goes up. Back on before that, the screen
stays: a change of mind does not roll it up and down.

1. On the projector: Settings > System > About, press Build seven times; then
   Developer options > USB debugging (network adb, port 5555).
2. Add a `[[projector]]` to `outlets.toml` (address, `cover`, the `apps` that become
   HomeKit inputs: `make adb CMD="shell cmd package query-activities -a android.intent.action.MAIN -c android.intent.category.LEANBACK_LAUNCHER --brief"`
   lists them), give the projector a fixed lease, then `make deploy`.
3. The projector asks to allow the hub's adb: tick "always", allow. `make adb`
   shows `device` once it is done (`unauthorized` before). The key stays in `data/adb`.

The Home app gets a TV: on / off, the apps as inputs, and the iPhone's remote
(control centre) for the arrows, select, back, play/pause. Standby keeps the network
up on this projector, so it wakes from the Home app; a longer power cut and the hub
shows it unreachable until it is back.

## Origin

The firmware comes from `~/personal/domotics/rf_module/rf_sender`. Changes:
repeated presses of the same button are reported again after 1 s (they were
dropped until another code came), the serial log no longer prints the Wi-Fi
password and lists the networks in range at boot, `433/receive/raw` gives the
protocol of each code, and every code sent is echoed on `433/receive`.

## The bridge heals itself

The board keeps the Wi-Fi awake (modem sleep made the broker drop it for
silence), retries the broker every 5 s without blocking, and restarts by
itself after 2 minutes without Wi-Fi or without the broker. On 2026-10-26 it
stayed off the network after the Pi rebooted until it was unplugged; it now
comes back on its own. Updates go over Wi-Fi (`make ota`, ArduinoOTA,
password `OTA_PASS`); only a board flashed before OTA needs the USB cable.
