#include <ESP8266WiFi.h>
#include <ArduinoOTA.h>
#include <PubSubClient.h>
#include <RCSwitch.h>
#include <LittleFS.h>
#include "secrets.h"
// Compile-time configuration checks: all must be defined via compiler flags
#ifndef WIFI_SSID
  #error "WIFI_SSID not defined. Define it with -DWIFI_SSID=\"your_ssid\" during compilation."
#endif
#ifndef WIFI_PASS
  #error "WIFI_PASS not defined. Define it with -DWIFI_PASS=\"your_password\" during compilation."
#endif
#ifndef MQTT_HOST
  #error "MQTT_HOST not defined. Define it with -DMQTT_HOST=\"broker_ip_or_hostname\"."
#endif
#ifndef MQTT_PORT
  #error "MQTT_PORT not defined. Define it with -DMQTT_PORT=1883."
#endif
#ifndef MQTT_USER
  #error "MQTT_USER not defined. Define it with -DMQTT_USER=\"username\"."
#endif
#ifndef MQTT_PASS
  #error "MQTT_PASS not defined. Define it with -DMQTT_PASS=\"password\"."
#endif
#ifndef OTA_PASS
  #error "OTA_PASS not defined: add it to secrets.h (updates over Wi-Fi, make ota)."
#endif

// Shown on 433/info after every boot: tells which build runs after an update
#define FIRMWARE_VERSION __DATE__ " " __TIME__
// The name on the network: rf-bridge.local, the target of make ota
#define HOSTNAME "rf-bridge"

// Global configuration variables assigned from compile-time macros
const char* ssid     = WIFI_SSID;
const char* pass     = WIFI_PASS;
const char* mqttHost = MQTT_HOST;
const uint16_t mqttPort = MQTT_PORT;
const char* mqttUser = MQTT_USER;
const char* mqttPass = MQTT_PASS;

// Network and RF communication objects
WiFiClient espClient;
PubSubClient client(espClient);
RCSwitch rfSender, rfReceiver;

// Define GPIO pins for RF communication
const int TX_PIN = D2; // GPIO4 for transmission
const int RX_PIN = D1; // GPIO5 for reception

// Variables to track last received code and heartbeat timing
unsigned long lastReceivedCode = 0;
unsigned long lastReceivedAt = 0;
const unsigned long repeatWindow = 1000; // a remote repeats its code for ~1 s: report one press, not ten
unsigned long lastHeartbeat = 0;
const unsigned long heartbeatInterval = 10000; // 10 seconds

// Self-healing: Wi-Fi or the broker missing for this long and the board restarts
// (on 2026-10-26 it stayed off the network after the Pi rebooted, until unplugged)
const unsigned long restartAfter = 120000;      // 2 minutes
const unsigned long mqttRetryInterval = 5000;   // between two attempts at the broker
unsigned long lastHealthyAt = 0;                // last time Wi-Fi and the broker were both up
unsigned long lastMqttAttempt = 0;

// Connect to Wi-Fi network using credentials provided at compile time
void setup_wifi() {
  Serial.print("Connecting to WiFi SSID: ");
  Serial.println(ssid);
  // What the ESP hears (2.4 GHz only): helps tell a wrong password from a network out of reach
  WiFi.mode(WIFI_STA);
  WiFi.hostname(HOSTNAME);
  // modem sleep made the broker drop the bridge for silence ("exceeded timeout")
  WiFi.setSleepMode(WIFI_NONE_SLEEP);
  WiFi.setAutoReconnect(true);
  WiFi.persistent(false);
  int n = WiFi.scanNetworks();
  Serial.printf("%d networks in range:\n", n);
  for (int i = 0; i < n; i++) {
    Serial.printf("  %s  ch %d  %d dBm\n", WiFi.SSID(i).c_str(), WiFi.channel(i), WiFi.RSSI(i));
  }
  WiFi.begin(ssid, pass);
  unsigned long since = millis();
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    if (millis() - start > restartAfter) {
      Serial.println(" no Wi-Fi for 2 min: restarting");
      ESP.restart();
    }
    if (millis() - since > 10000) {
      wl_status_t st = WiFi.status();
      Serial.println(st == WL_WRONG_PASSWORD ? " wrong password" : st == WL_NO_SSID_AVAIL ? " network not found" :
                     st == WL_CONNECT_FAILED ? " connection failed" : " still trying");
      since = millis();
    } else {
      Serial.print(".");
    }
  }
  Serial.println();
  Serial.print("Connected. IP address: ");
  Serial.println(WiFi.localIP());
}

// MQTT message callback to handle received commands on topic "433/send"
void mqttCallback(char* topic, byte* payload, unsigned int length) {
  payload[length] = '\0'; // Null-terminate the payload to convert to string
  String message = String((char*)payload);
  if (String(topic) == "433/send") {
    // "<code>" or "<code> <pulse us>": protocol 1 is 350 us, some remotes are faster (the screen's: 188)
    int space = message.indexOf(' ');
    unsigned long code = message.substring(0, space < 0 ? message.length() : space).toInt();
    long pulse = space < 0 ? 0 : message.substring(space + 1).toInt();
    if (code > 0) {
      rfSender.setProtocol(1); // back to 350 us
      if (pulse > 0) {
        rfSender.setPulseLength(pulse);
      }
      rfSender.send(code, 24); // Send RF code with 24-bit length
      // Echo it on 433/receive, the topic Homebridge and the status card follow: whoever sends a
      // code (Home app, Sapling Sunrise, mosquitto_pub), everyone sees the new state. The receiver
      // hears our own transmission too; marking it as just received keeps that from a second report.
      char echo[16];
      snprintf(echo, sizeof(echo), "%lu", code);
      client.publish("433/receive", echo);
      lastReceivedCode = code;
      lastReceivedAt = millis();
      Serial.print("Sent RF code: ");
      Serial.println(code);
    }
  }
}

// One attempt at the broker; the loop calls it again every few seconds, so OTA,
// the radio and the Wi-Fi keep running while the broker is away
void reconnect() {
  if (WiFi.status() != WL_CONNECTED || millis() - lastMqttAttempt < mqttRetryInterval) {
    return;
  }
  lastMqttAttempt = millis();
  Serial.printf("Connecting to MQTT broker at %s:%u with user %s ... ", mqttHost, mqttPort, mqttUser);
  if (client.connect("rf433-client", mqttUser, mqttPass)) {
    Serial.println("Connected");
    client.subscribe("433/send");
    client.publish("433/status", "boot");
    // retained: whoever subscribes later still learns which build runs, and where
    char info[96];
    snprintf(info, sizeof(info), "%s %s %d dBm", FIRMWARE_VERSION, WiFi.localIP().toString().c_str(), WiFi.RSSI());
    client.publish("433/info", info, true);
  } else {
    Serial.printf("Failed, rc=%d - retrying in %lu s\n", client.state(), mqttRetryInterval / 1000);
  }
}

// Updates over Wi-Fi: make ota sends the new firmware to rf-bridge.local
void setup_ota() {
  ArduinoOTA.setHostname(HOSTNAME);
  ArduinoOTA.setPassword(OTA_PASS);
  ArduinoOTA.onStart([]() {
    rfReceiver.disableReceive(); // no radio interrupts while the flash is written
    Serial.println("OTA: update starting");
  });
  ArduinoOTA.onEnd([]() { Serial.println("OTA: done, restarting"); });
  ArduinoOTA.onError([](ota_error_t error) {
    Serial.printf("OTA: error %u\n", error);
    rfReceiver.enableReceive(RX_PIN);
  });
  ArduinoOTA.begin();
}

void setup() {
  Serial.begin(115200);

  // Print loaded configuration to serial monitor
  Serial.println("=== CONFIGURATION ===");
  Serial.print("SSID: "); Serial.println(ssid);
  Serial.print("MQTT Broker: "); Serial.println(mqttHost);
  Serial.print("MQTT Port: "); Serial.println(mqttPort);
  Serial.print("MQTT User: "); Serial.println(mqttUser);
  Serial.println("=====================");

  // Connect to Wi-Fi
  setup_wifi();

  // Quick test connection to MQTT broker
  IPAddress broker;
  broker.fromString(mqttHost);
  WiFiClient testClient;
  Serial.print("Testing TCP connection to ");
  Serial.print(mqttHost);
  Serial.print(":");
  Serial.print(mqttPort);
  Serial.print(" ... ");
  if (testClient.connect(broker, mqttPort)) {
    Serial.println("Success");
    testClient.stop();
  } else {
    Serial.println("Failed! Check network access and open port.");
  }

  // Initialize RF sender and receiver
  rfSender.enableTransmit(TX_PIN);
  rfReceiver.enableReceive(RX_PIN);

  // Setup MQTT client and callback
  client.setServer(mqttHost, mqttPort);
  client.setCallback(mqttCallback);
  client.setSocketTimeout(5); // a broker that does not answer must not hold the loop for 15 s

  setup_ota();
  lastHealthyAt = millis();
}

void loop() {
  ArduinoOTA.handle();
  if (!client.connected()) {
    reconnect();
  }
  client.loop();

  if (WiFi.status() == WL_CONNECTED && client.connected()) {
    lastHealthyAt = millis();
  } else if (millis() - lastHealthyAt > restartAfter) {
    Serial.println("No Wi-Fi or no broker for 2 min: restarting");
    ESP.restart();
  }

  // Send periodic heartbeat message
  if (millis() - lastHeartbeat > heartbeatInterval) {
    if (client.connected()) {
      client.publish("433/status", "online");
    }
    lastHeartbeat = millis();
  }

  // Check for incoming RF codes and publish them to MQTT
  if (rfReceiver.available()) {
    unsigned long receivedCode = rfReceiver.getReceivedValue();
    bool repeat = receivedCode == lastReceivedCode && millis() - lastReceivedAt < repeatWindow;
    lastReceivedAt = millis();
    if (receivedCode != 0 && !repeat) {
      lastReceivedCode = receivedCode;
      char buf[16];
      snprintf(buf, sizeof(buf), "%lu", receivedCode);
      client.publish("433/receive", buf);
      // bits, protocol and pulse length too: needed if an outlet does not answer to the default ones
      char raw[48];
      snprintf(raw, sizeof(raw), "%lu %u bits, protocol %u, pulse %u us", receivedCode,
               rfReceiver.getReceivedBitlength(), rfReceiver.getReceivedProtocol(), rfReceiver.getReceivedDelay());
      client.publish("433/receive/raw", raw);
      Serial.print("Received RF code: ");
      Serial.println(buf);
    }
    rfReceiver.resetAvailable();
  }
}
