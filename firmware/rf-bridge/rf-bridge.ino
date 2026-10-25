#include <ESP8266WiFi.h>
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

// Connect to Wi-Fi network using credentials provided at compile time
void setup_wifi() {
  Serial.print("Connecting to WiFi SSID: ");
  Serial.println(ssid);
  // What the ESP hears (2.4 GHz only): helps tell a wrong password from a network out of reach
  WiFi.mode(WIFI_STA);
  int n = WiFi.scanNetworks();
  Serial.printf("%d networks in range:\n", n);
  for (int i = 0; i < n; i++) {
    Serial.printf("  %s  ch %d  %d dBm\n", WiFi.SSID(i).c_str(), WiFi.channel(i), WiFi.RSSI(i));
  }
  WiFi.begin(ssid, pass);
  unsigned long since = millis();
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
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
    unsigned long code = message.toInt();
    if (code > 0) {
      rfSender.send(code, 24); // Send RF code with 24-bit length
      // Echo it on 433/receive, the topic Homebridge and the status card follow: whoever sends a
      // code (Home app, Sapling Sunrise, mosquitto_pub), everyone sees the new state. The receiver
      // hears our own transmission too; marking it as just received keeps that from a second report.
      client.publish("433/receive", message.c_str());
      lastReceivedCode = code;
      lastReceivedAt = millis();
      Serial.print("Sent RF code: ");
      Serial.println(code);
    }
  }
}

// Reconnect to the MQTT broker if disconnected, using username/password authentication
void reconnect() {
  while (!client.connected()) {
    Serial.print("Connecting to MQTT broker at ");
    Serial.print(mqttHost);
    Serial.print(":");
    Serial.print(mqttPort);
    Serial.print(" with user ");
    Serial.print(mqttUser);
    Serial.print(" ... ");
    if (client.connect("rf433-client", mqttUser, mqttPass)) {
      Serial.println("Connected");
      client.subscribe("433/send");
      client.publish("433/status", "boot");
    } else {
      Serial.print("Failed, rc=");
      Serial.print(client.state());
      Serial.println(" - retrying in 1 second");
      delay(1000);
    }
  }
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
}

void loop() {
  if (!client.connected()) {
    reconnect();
  }
  client.loop();

  // Send periodic heartbeat message
  if (millis() - lastHeartbeat > heartbeatInterval) {
    Serial.println("Sending heartbeat: online");
    client.publish("433/status", "online");
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
