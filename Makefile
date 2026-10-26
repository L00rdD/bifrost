PI ?= dpi.local
PI_DIR ?= ~/bifrost
FQBN ?= esp8266:esp8266:nodemcuv2
PORT ?= $(firstword $(wildcard /dev/cu.usbserial-* /dev/cu.wchusbserial*))

.PHONY: deploy env logs sniff send adb flash ota monitor

deploy:          ## render the Homebridge config, copy the project to the Pi and (re)start everything
	python3 render.py
	rsync -az --inplace --delete --exclude .git --exclude data --exclude .env --exclude firmware \
	      --exclude .DS_Store ./ $(PI):$(PI_DIR)/
	ssh $(PI) 'cd $(PI_DIR) && mkdir -p data/homebridge data/mosquitto data/adb \
	  && cp homebridge/config.json data/homebridge/config.json && docker compose up -d --build --remove-orphans && docker compose restart hub \
	  && (docker exec homebridge test -d /homebridge/node_modules/homebridge-mqttthing \
	      || docker exec homebridge hb-service add homebridge-mqttthing) && docker compose restart homebridge'

env:             ## copy .env to the Pi (once, and after every change of .env)
	scp .env $(PI):$(PI_DIR)/.env

logs:
	ssh $(PI) 'cd $(PI_DIR) && docker compose logs -f --tail 100'

sniff:           ## print every RF code the bridge hears: press the remote buttons now
	ssh -t $(PI) 'cd $(PI_DIR) && set -a && . ./.env && docker exec -it mosquitto mosquitto_sub -u "$$MQTT_USER" -P "$$MQTT_PASS" -v -t "433/#"'

send:            ## make send CODE=1234567
	ssh $(PI) 'cd $(PI_DIR) && set -a && . ./.env && docker exec mosquitto mosquitto_pub -u "$$MQTT_USER" -P "$$MQTT_PASS" -t 433/send -m "$(CODE)"'

adb:             ## the hub's adb: make adb lists the projector ("unauthorized": allow it on its screen), make adb CMD="shell ..."
	ssh $(PI) 'docker exec bifrost-hub adb $(or $(CMD),devices -l)'

flash:           ## build and flash the RF bridge (board plugged in over USB)
	arduino-cli compile --fqbn $(FQBN) firmware/rf-bridge
	arduino-cli upload -p $(PORT) --fqbn $(FQBN) firmware/rf-bridge

# The bridge on the network (it answers to rf-bridge.local once flashed with OTA);
# BRIDGE=192.168.1.225 when mDNS does not reach it
BRIDGE ?= rf-bridge.local
# the OTA uploader shipped with the ESP8266 core (arduino-cli only uploads to ports it discovered itself)
ESPOTA = $(firstword $(wildcard $(HOME)/Library/Arduino15/packages/esp8266/hardware/esp8266/*/tools/espota.py $(HOME)/.arduino15/packages/esp8266/hardware/esp8266/*/tools/espota.py))
OTA_PASS = $(shell sed -n 's/^\#define OTA_PASS "\(.*\)".*/\1/p' firmware/rf-bridge/secrets.h)

ota:             ## build and send the RF bridge firmware over Wi-Fi (no USB; the first flash with OTA is made with make flash)
	arduino-cli compile --fqbn $(FQBN) --output-dir build/rf-bridge firmware/rf-bridge
	@python3 $(ESPOTA) -i $(BRIDGE) -p 8266 -a '$(OTA_PASS)' -f build/rf-bridge/rf-bridge.ino.bin -r

monitor:
	arduino-cli monitor -p $(PORT) -c baudrate=115200
