// WARON IoT ESP8266 D1 Mini: portal setup + OTA, diadaptasi dari attendance.
#include <ArduinoJson.h>
#include <EEPROM.h>
#include <DHT.h>
#include <ESP8266HTTPClient.h>
#include <ESP8266WebServer.h>
#include <ESP8266WiFi.h>
#include <Updater.h>
extern "C" {
#include <ping.h>
#include <user_interface.h>
}
#include "ui_gzip.h"

struct ServiceProbe {
  bool measured = false, dnsOk = false, tcpOk = false;
  uint32_t dnsMs = 0, tcpMs = 0, measuredAt = 0;
};
ServiceProbe serviceProbes[4];
const char *SERVICE_IDS[] = {"google", "youtube", "tiktok", "netflix"};
const char *SERVICE_HOSTS[] = {"www.google.com", "www.youtube.com",
                               "www.tiktok.com", "www.netflix.com"};
uint8_t nextService = 0;
uint32_t lastServiceStep = 0;
bool serviceStarted = false;
struct Settings {
  uint32_t magic;
  char ssid[33], pass[65], backend[193], device[41], key[97], probe[193];
  char adminPass[25];
  uint32_t checksum;
};
struct ping_option pingOptions;
bool pingRunning = false, pingAvailable = false;
uint32_t pingJob = 0, pingReceived = 0, pingSent = 0, pingMin = 0, pingMax = 0,
         pingSum = 0, pingFinishedAt = 0;
String pingTarget;
struct PingPacket { uint32_t seq, bytes, ms; bool ok; };
PingPacket pingPackets[4];
uint8_t pingPacketCount = 0;
struct ping_option gatewayOptions;
bool gatewayRunning=false, gatewayAvailable=false, gatewaySampleReady=false;
uint32_t gatewaySent=0,gatewayReceived=0,gatewaySum=0,gatewayMin=0,gatewayMax=0,gatewayFinishedAt=0,gatewayMeasurement=0;
String gatewayTarget;
Settings cfg;
// Board pin D1 = GPIO5. DHT21 DATA must use a 3.3V pull-up.
DHT dht(D1, DHT21);
const bool networkEnabled = true, temperatureEnabled = true;
bool sensorOk = false;
float temperatureC = NAN, humidityPercent = NAN;
uint32_t lastSensorRead = 0;
String featuresJson() {
  return String("{\"network\":") + (networkEnabled ? "true" : "false") +
    ",\"temperature\":" + (temperatureEnabled ? "true" : "false") + "}";
}
String environmentJson() {
  return String("{\"sensor_ok\":") + (sensorOk ? "true" : "false") +
    ",\"temperature_c\":" + (sensorOk ? String(temperatureC, 1) : String("null")) +
    ",\"humidity_percent\":" + (sensorOk ? String(humidityPercent, 1) : String("null")) +
    ",\"reading_age_ms\":" + String(uint32_t(millis()-lastSensorRead)) + "}";
}
void sensorTick() {
  if (!temperatureEnabled || millis()-lastSensorRead < 3000) return;
  lastSensorRead = millis();
  temperatureC = dht.readTemperature(); humidityPercent = dht.readHumidity();
  sensorOk = isfinite(temperatureC) && isfinite(humidityPercent) && temperatureC >= -40 && temperatureC <= 80 && humidityPercent >= 0 && humidityPercent <= 100;
}
ESP8266WebServer web(80);
String sessionToken, apName;
bool storageReady = false, apActive = false, connecting = false,
     otaRunning = false, otaSuccess = false;
bool firstSend = true, scanPending = false;
bool liveMode = false, firstControl = true;
uint32_t telemetryIntervalS = 10, lastControlPoll = 0, liveStartedAt = 0,
         liveLeaseMs = 0, controlRevision = 0;
uint32_t liveIntervalS = 2;
uint32_t connectStart = 0, lastSend = 0, reconnectAt = 0, restartAt = 0,
         lastPortal = 0, otaBytes = 0;
int lastPost = 0;
WiFiEventHandler disconnectHandler, gotIpHandler;
String bootId;
uint32_t lastUiRequest = 0;
bool uiQuiet() { return !lastUiRequest || uint32_t(millis()-lastUiRequest)>=750; }
uint32_t telemetrySequence = 0, disconnectCount = 0, disconnectedMs = 0,
         disconnectedAt = 0;
int lastDisconnectReason = 0;
bool hadConnection = false, outageActive = false;
bool lastInternetOk = false;
int lastServiceIndex = -1;
uint32_t internetMeasuredAt = 0;
bool internetMeasured = false;
String otaError;
const uint32_t MAGIC = 0x4e505301;
const char SETUP_PASSWORD[] = "waron114";

uint32_t checksum(const Settings &value) {
  const uint8_t *p = (const uint8_t *)&value;
  uint32_t h = 2166136261UL;
  for (size_t i = 0; i < offsetof(Settings, checksum); i++) {
    h ^= p[i];
    h *= 16777619UL;
  }
  return h;
}
String randomHex() {
  char b[25];
  snprintf(b, sizeof(b), "%08lx%08lx%08lx", (unsigned long)os_random(),
           (unsigned long)os_random(), (unsigned long)os_random());
  return String(b);
}
bool saveSettings(Settings &next) {
  next.magic = MAGIC;
  next.checksum = checksum(next);
  EEPROM.put(0, next);
  return EEPROM.commit();
}
void loadSettings() {
  EEPROM.begin(sizeof(Settings) + 16);
  // Legacy feature bits are reserved; both measurements are always enabled.
  uint32_t marker = 0, storedInterval = 0;
  EEPROM.get(sizeof(Settings), marker);
  EEPROM.get(sizeof(Settings) + 4, storedInterval);
  if (marker == 0x544c4d31 && storedInterval >= 10 && storedInterval <= 3600)
    telemetryIntervalS = storedInterval;
  EEPROM.get(0, cfg);
  const bool valid = cfg.magic == MAGIC && cfg.checksum == checksum(cfg);
  if (!valid) {
    memset(&cfg, 0, sizeof(cfg));
  }
  // Migrate the old setup password while preserving Wi-Fi/backend settings.
  if (!valid ||
      strncmp(cfg.adminPass, SETUP_PASSWORD, sizeof(cfg.adminPass)) != 0) {
    memset(cfg.adminPass, 0, sizeof(cfg.adminPass));
    strlcpy(cfg.adminPass, SETUP_PASSWORD, sizeof(cfg.adminPass));
    storageReady = saveSettings(cfg);
  } else {
    storageReady = true;
  }
}
String jsonString(const String &value) {
  String out = "\"";
  for (size_t i = 0; i < value.length(); i++) {
    char c = value[i];
    if (c == '"' || c == '\\') {
      out += '\\';
      out += c;
    } else if ((uint8_t)c < 32) {
      char b[7];
      snprintf(b, sizeof(b), "\\u%04x", (unsigned char)c);
      out += b;
    } else
      out += c;
  }
  return out + '"';
}
void sendJson(int code, const String &body) {
  web.sendHeader("Cache-Control", "no-store");
  web.send(code, "application/json", body);
}
void message(int code, const String &text) {
  sendJson(code, String("{\"message\":") + jsonString(text) + "}");
}
bool authed() { return web.authenticate("admin", cfg.adminPass); }
bool requireAuth() {
  if (authed())
    return true;
  web.requestAuthentication(BASIC_AUTH, "WARON IoT Device");
  return false;
}
bool tokenValid() { return web.header("X-Device-Token") == sessionToken; }
bool requireWrite() {
  if (!requireAuth())
    return false;
  if (!tokenValid()) {
    message(403, "Token sesi salah; muat ulang halaman setup");
    return false;
  }
  return true;
}
void startAP() {
  if (apActive)
    return;
  WiFi.mode(WIFI_AP_STA);
  apActive = WiFi.softAP(apName.c_str(), cfg.adminPass);
  Serial.printf("Setup AP: %s | http://%s/setup\n", apName.c_str(),
                WiFi.softAPIP().toString().c_str());
}
void connectWifi() {
  for (auto &result : serviceProbes)
    result.measured = false;
  serviceStarted = false;
  hadConnection = false;
  WiFi.disconnect(false);
  WiFi.mode(apActive ? WIFI_AP_STA : WIFI_STA);
  WiFi.setSleepMode(WIFI_NONE_SLEEP);
  if (!cfg.ssid[0]) {
    connecting = false;
    startAP();
    return;
  }
  WiFi.begin(cfg.ssid, cfg.pass);
  connecting = true;
  connectStart = millis();
}
void networkTick() {
  if (reconnectAt && (int32_t)(millis() - reconnectAt) >= 0) {
    reconnectAt = 0;
    connectWifi();
  }
  if (WiFi.status() == WL_CONNECTED) {
    if (connecting) {
      Serial.printf("WiFi terhubung: http://%s/setup\n",
                    WiFi.localIP().toString().c_str());
      connecting = false;
      firstSend = true;
      if (apActive) {
        WiFi.softAPdisconnect(true);
        WiFi.mode(WIFI_STA);
        WiFi.setSleepMode(WIFI_NONE_SLEEP);
        apActive = false;
      }
    }
    return;
  }
  if (connecting && millis() - connectStart >= 30000) {
    connecting = false;
    startAP();
  }
  if (!connecting && cfg.ssid[0] && millis() - connectStart >= 60000)
    connectWifi();
}
// Public, read-only status: never exposes passwords, keys or setup token.
void gatewayReply(void *,void *data) {
  const struct ping_resp *r=(const struct ping_resp *)data;
  if (!gatewayRunning || r->ping_err!=0) return;
  if (!gatewayReceived || r->resp_time<gatewayMin) gatewayMin=r->resp_time;
  if (r->resp_time>gatewayMax) gatewayMax=r->resp_time;
  gatewaySum+=r->resp_time;gatewayReceived++;
}
void gatewayDone(void *,void *data) {
  const struct ping_resp *r=(const struct ping_resp *)data;
  gatewaySent=r->total_count;gatewayRunning=false;gatewaySampleReady=true;
  gatewayAvailable=gatewaySent>0;gatewayFinishedAt=millis();
}
void startGatewayPing() {
  IPAddress ip=WiFi.gatewayIP();
  gatewayAvailable=false;
  if (!ip || ip[0]==0) { gatewaySampleReady=true; return; }
  memset(&gatewayOptions,0,sizeof(gatewayOptions));
  gatewayOptions.count=1;gatewayOptions.coarse_time=1;gatewayOptions.ip=ip;
  gatewayOptions.recv_function=gatewayReply;gatewayOptions.sent_function=gatewayDone;
  gatewaySent=gatewayReceived=gatewaySum=gatewayMin=gatewayMax=0;
  gatewayTarget=ip.toString();gatewayRunning=true;
  if (!ping_start(&gatewayOptions)) { gatewayRunning=false;gatewaySampleReady=true;return; }
  gatewayMeasurement++;
}
String gatewayJson() {
  return String("{\"measurement_id\":")+String(gatewayMeasurement)+",\"target\":"+jsonString(gatewayTarget)+
    ",\"sent\":"+String(gatewaySent)+",\"received\":"+String(gatewayReceived)+
    ",\"loss_percent\":"+String(100.0*(gatewaySent-gatewayReceived)/gatewaySent,1)+
    ",\"min_ms\":"+(gatewayReceived?String(gatewayMin):String("null"))+
    ",\"avg_ms\":"+(gatewayReceived?String((float)gatewaySum/gatewayReceived,1):String("null"))+
    ",\"max_ms\":"+(gatewayReceived?String(gatewayMax):String("null"))+
    ",\"age_ms\":"+String(uint32_t(millis()-gatewayFinishedAt))+"}";
}
void pingReply(void *, void *data) {
  const struct ping_resp *response = (const struct ping_resp *)data;
  if (!pingRunning) return;
  if (pingPacketCount < 4) {
    PingPacket &packet = pingPackets[pingPacketCount++];
    packet.seq=response->seqno;packet.bytes=response->bytes;
    packet.ms=response->resp_time;packet.ok=response->ping_err==0;
  }
  if (response->ping_err != 0) return;
  uint32_t ms = response->resp_time;
  if (!pingReceived || ms < pingMin)
    pingMin = ms;
  if (ms > pingMax)
    pingMax = ms;
  pingSum += ms;
  pingReceived++;
}
void pingDone(void *, void *data) {
  const struct ping_resp *response = (const struct ping_resp *)data;
  pingSent = response->total_count;
  pingRunning = false;
  pingAvailable = true;
  pingFinishedAt = millis();
  firstSend = true;
}
void handlePingResult() {
  if (!requireAuth())
    return;
  String body = "{\"job_id\":" + String(pingJob) +
                ",\"running\":" + String(pingRunning ? "true" : "false") +
                ",\"available\":" + String(pingAvailable ? "true" : "false") +
                ",\"target\":" + jsonString(pingTarget);
  body +=
      ",\"sent\":" + String(pingSent) + ",\"received\":" + String(pingReceived);
  body += ",\"loss_percent\":" +
          (pingAvailable && pingSent
               ? String(100.0 * (pingSent - pingReceived) / pingSent, 1)
               : String("null"));
  body += ",\"min_ms\":" + (pingReceived ? String(pingMin) : String("null"));
  body +=
      ",\"avg_ms\":" + (pingReceived ? String((float)pingSum / pingReceived, 1)
                                     : String("null"));
  body +=
      ",\"max_ms\":" + (pingReceived ? String(pingMax) : String("null")) + "}";
  sendJson(200, body);
}
uint32_t lastRemotePing = 0;
void remotePing(uint32_t job, const String &target) {
  if (!job || job == lastRemotePing || !networkEnabled || pingRunning || gatewayRunning || otaRunning || restartAt || reconnectAt || scanPending) return;
  IPAddress ip;
  if (!ip.fromString(target) || ip[0]==0 || ip[0]==127 || ip[0]>=224 || ip[3]==255 || ip==WiFi.localIP()) return;
  memset(&pingOptions,0,sizeof(pingOptions));
  pingOptions.count=4; pingOptions.coarse_time=1; pingOptions.ip=ip;
  pingOptions.recv_function=pingReply; pingOptions.sent_function=pingDone;
  pingReceived=pingSent=pingMin=pingMax=pingSum=0;
  pingPacketCount=0;
  pingAvailable=false;pingTarget=target;pingRunning=true;
  if (!ping_start(&pingOptions)) { pingRunning=false; return; }
  pingJob=job;lastRemotePing=job;
}
void handleHealth() {
  lastUiRequest = millis();
  bool connected = WiFi.status() == WL_CONNECTED;
  bool fresh =
      connected && internetMeasured && millis() - internetMeasuredAt <= 60000;
  String body = "{\"features\":" + featuresJson() + ",\"device\":" + jsonString(cfg.device);
  if (temperatureEnabled) body += ",\"environment\":" + environmentJson();
  body += ",\"connected\":" + String(connected ? "true" : "false");
  body += ",\"ap\":" + String(apActive ? "true" : "false");
  body += ",\"ssid\":" + jsonString(cfg.ssid);
  body += ",\"ip\":" + jsonString(connected ? WiFi.localIP().toString()
                                            : WiFi.softAPIP().toString());
  body += ",\"rssi\":" + (connected ? String(WiFi.RSSI()) : String("null"));
  body += ",\"uptime\":" + String(millis() / 1000);
  body += ",\"heap\":" + String(ESP.getFreeHeap());
  body += ",\"last_post\":" + String(lastPost);
  body += ",\"delivery_mode\":" + jsonString(liveMode ? "live" : "telemetry");
  body += ",\"send_interval_s\":" +
          String(liveMode ? liveIntervalS : telemetryIntervalS);
  body += ",\"internet\":" + jsonString(!fresh           ? "unknown"
                                        : lastInternetOk ? "ok"
                                                         : "failed");
  body +=
      ",\"internet_age_s\":" +
      (internetMeasured ? String(uint32_t(millis() - internetMeasuredAt) / 1000)
                        : String("null")) +
      "}";
  sendJson(200, body);
}
void handleStatus() {
  lastUiRequest = millis();
  if (!requireAuth())
    return;
  lastPortal = millis();
  String s = "{\"features\":" + featuresJson() + ",\"telemetry_interval_s\":" + String(telemetryIntervalS) +
             ",\"token\":" + jsonString(sessionToken) + ",\"connected\":" +
             (WiFi.status() == WL_CONNECTED ? String("true") : String("false"));
  s += ",\"ssid\":" + jsonString(cfg.ssid) + ",\"ip\":" +
       jsonString(WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString()
                                                : WiFi.softAPIP().toString());
  s += ",\"rssi\":" + String(WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0) +
       ",\"heap\":" + String(ESP.getFreeHeap()) +
       ",\"last_post\":" + String(lastPost);
  s += ",\"backend\":" + jsonString(cfg.backend) +
       ",\"device\":" + jsonString(cfg.device) +
       ",\"probe\":" + jsonString(cfg.probe) + "}";
  sendJson(200, s);
}
void handleScan() {
  if (pingRunning || gatewayRunning) {
    message(409, "Tunggu ping selesai");
    return;
  }
  if (!requireAuth())
    return;
  lastPortal = millis();
  int count = WiFi.scanComplete();
  if (!scanPending) {
    WiFi.scanDelete();
    WiFi.scanNetworks(true);
    scanPending = true;
    sendJson(200, "{\"scanning\":true}");
    return;
  }
  if (count == WIFI_SCAN_RUNNING) {
    sendJson(200, "{\"scanning\":true}");
    return;
  }
  if (count < 0) {
    scanPending = false;
    message(500, "Scan gagal; coba lagi");
    return;
  }
  String s = "{\"scanning\":false,\"networks\":[";
  for (int i = 0; i < count && i < 30; i++) {
    if (i)
      s += ',';
    s += "{\"ssid\":" + jsonString(WiFi.SSID(i)) +
         ",\"rssi\":" + String(WiFi.RSSI(i)) + ",\"enc\":" +
         (WiFi.encryptionType(i) == ENC_TYPE_NONE ? String("false")
                                                  : String("true")) +
         "}";
  }
  s += "]}";
  WiFi.scanDelete();
  scanPending = false;
  sendJson(200, s);
}
void handleWifi() {
  if (pingRunning || gatewayRunning) {
    message(409, "Tunggu ping selesai");
    return;
  }
  if (!requireWrite())
    return;
  if (!storageReady) {
    message(500, "Penyimpanan konfigurasi tidak tersedia");
    return;
  }
  String ssid = web.arg("ssid"), pass = web.arg("pass");
  bool open = web.arg("open") == "1";
  if (!ssid.length() || ssid.length() > 32 || pass.length() > 64) {
    message(400, "SSID 1–32 byte dan password maksimal 64 byte");
    return;
  }
  if (open)
    pass = "";
  else if (!pass.length()) {
    if (ssid != String(cfg.ssid)) {
      message(400,
              "Masukkan password jaringan baru atau pilih jaringan terbuka");
      return;
    }
    pass = cfg.pass;
  }
  bool hex = true;
  for (size_t i = 0; i < pass.length(); i++)
    if (!isxdigit(pass[i]))
      hex = false;
  if (!open && (pass.length() < 8 || (pass.length() == 64 && !hex))) {
    message(400, "Password WPA 8–63 karakter atau 64 digit hex");
    return;
  }
  Settings next = cfg;
  strlcpy(next.ssid, ssid.c_str(), sizeof(next.ssid));
  strlcpy(next.pass, pass.c_str(), sizeof(next.pass));
  if (!saveSettings(next)) {
    message(500, "Gagal menyimpan konfigurasi");
    return;
  }
  cfg = next;
  message(200, "Disimpan, perangkat mencoba reconnect.");
  reconnectAt = millis() + 1000;
}
bool httpURL(const String &s) {
  return s.startsWith("http://") && s.length() > 7 && s.indexOf(' ') == -1 &&
         s.indexOf('\r') == -1 && s.indexOf('\n') == -1;
}
void handleConfig() {
  if (pingRunning || gatewayRunning) {
    message(409, "Tunggu ping selesai");
    return;
  }
  if (!requireWrite())
    return;
  if (!storageReady) {
    message(500, "Penyimpanan tidak tersedia");
    return;
  }
  String backend = web.arg("backend"), device = web.arg("device"),
         key = web.arg("key");
  backend.trim();

  while (backend.endsWith("/"))
    backend.remove(backend.length() - 1);
  if (!key.length())
    key = cfg.key;
  if (!httpURL(backend) || backend.length() > 192 || !device.length() ||
      device.length() > 40 || !key.length() || key.length() > 96) {
    message(400, "Isi backend HTTP, ID, dan kunci. "
                 "Periksa panjang input.");
    return;
  }
  for (size_t i = 0; i < device.length(); i++)
    if (!isalnum(device[i]) && device[i] != '-' && device[i] != '_') {
      message(400, "ID hanya huruf, angka, - dan _");
      return;
    }
  for (size_t i = 0; i < key.length(); i++)
    if ((uint8_t)key[i] < 33 || (uint8_t)key[i] > 126) {
      message(400, "Kunci harus karakter ASCII tanpa spasi");
      return;
    }
  uint32_t requestedInterval = telemetryIntervalS;
  if (web.hasArg("telemetry_interval_s")) {
    String value = web.arg("telemetry_interval_s");
    if (!value.length() || value.length() > 4) {
      message(400, "Interval Telemetri harus 10–3600 detik");
      return;
    }
    for (size_t i = 0; i < value.length(); i++)
      if (!isdigit(value[i])) {
        message(400, "Interval tidak valid");
        return;
      }
    requestedInterval = value.toInt();
    if (requestedInterval < 10 || requestedInterval > 3600) {
      message(400, "Interval Telemetri harus 10–3600 detik");
      return;
    }
  }
  uint32_t marker = 0x544c4d31;
  EEPROM.put(sizeof(Settings), marker);
  EEPROM.put(sizeof(Settings) + 4, requestedInterval);
  Settings next = cfg;
  strlcpy(next.backend, backend.c_str(), sizeof(next.backend));
  strlcpy(next.device, device.c_str(), sizeof(next.device));
  strlcpy(next.key, key.c_str(), sizeof(next.key));
  next.probe[0] =
      0; // Reserved for EEPROM compatibility; external HTTP probe removed.
  if (!saveSettings(next)) {
    message(500, "Gagal menyimpan pengaturan");
    return;
  }
  cfg = next;
  for (auto &result : serviceProbes)
    result.measured = false;
  serviceStarted = false;
  telemetryIntervalS = requestedInterval;
  sensorOk = false; lastSensorRead = millis()-3000;
  internetMeasured = false;

  firstSend = true;
  firstControl = true;
  liveMode = false;
  message(200, "Pengaturan backend tersimpan.");
}

// ESP8266 Updater has no abort(): end(false) clears an incomplete update.
void failOTA(const String &text) {
  otaError = text;
  otaRunning = false;
  otaSuccess = false;
  if (Update.isRunning())
    Update.end(false);
}
void handleUpload() {
  HTTPUpload &upload = web.upload();
  if (upload.status == UPLOAD_FILE_START) {
    otaSuccess = false;
    otaError = "";
    otaBytes = 0;
    if (!authed() || !tokenValid()) {
      otaError = "Akses ditolak";
      return;
    }
    if (reconnectAt || scanPending || pingRunning || gatewayRunning) {
      otaError = "Tunggu reconnect/scan selesai";
      return;
    }
    if (upload.name != "file" || !upload.filename.endsWith(".bin")) {
      otaError = "Pilih firmware .bin";
      return;
    }
    uint32_t freeSpace = ESP.getFreeSketchSpace();
    if (freeSpace <= 0x1000) {
      otaError = "Ruang OTA tidak cukup";
      return;
    }
    otaRunning = Update.begin((freeSpace - 0x1000) & 0xfffff000, U_FLASH);
    if (!otaRunning)
      otaError = Update.getErrorString();
  } else if (upload.status == UPLOAD_FILE_WRITE && otaRunning) {
    if (otaBytes == 0 && upload.currentSize && upload.buf[0] != 0xe9) {
      failOTA("Format firmware ESP8266 tidak valid");
      return;
    }
    if (Update.write(upload.buf, upload.currentSize) != upload.currentSize) {
      failOTA(Update.getErrorString());
      return;
    }
    otaBytes += upload.currentSize;
  } else if (upload.status == UPLOAD_FILE_END && otaRunning) {
    if (!otaBytes) {
      failOTA("Firmware kosong");
      return;
    }
    otaSuccess = Update.end(true);
    otaRunning = false;
    if (!otaSuccess)
      otaError = Update.getErrorString();
  } else if (upload.status == UPLOAD_FILE_ABORTED) {
    failOTA("Upload dibatalkan");
  }
}
void handleUploadDone() {
  if (!requireWrite())
    return;
  if (!otaSuccess) {
    message(400, otaError.length() ? otaError : "Firmware tidak diterima");
    return;
  }
  message(200, "Firmware berhasil diverifikasi. Perangkat restart…");
  restartAt = millis() + 800;
}

int getStatus(const String &url, uint32_t &elapsed) {
  WiFiClient client;
  client.setTimeout(2000);
  HTTPClient http;
  http.setTimeout(2000);
  http.setReuse(false);
  uint32_t begin = millis();
  if (!http.begin(client, url)) {
    elapsed = millis() - begin;
    return -1;
  }
  int result = http.GET();
  elapsed = millis() - begin;
  http.end();
  return result;
}
void pollDeliveryControl() {
  if (liveMode && uint32_t(millis() - liveStartedAt) >= liveLeaseMs) {
    liveMode = false;
    firstSend = true;
  }
  if (WiFi.status() != WL_CONNECTED || !cfg.backend[0] || !cfg.device[0] ||
      !cfg.key[0])
    return;
  if (!firstControl && uint32_t(millis() - lastControlPoll) < 5000)
    return;
  firstControl = false;
  lastControlPoll = millis();
  WiFiClient client;
  client.setTimeout(1500);
  HTTPClient http;
  http.setTimeout(1500);
  if (!http.begin(client, String(cfg.backend) + "/api/device/control"))
    return;
  http.addHeader("X-Device-ID", cfg.device);
  http.addHeader("X-Device-Key", cfg.key);
  int code = http.GET();
  if (code == 200 && http.getSize() <= 1024) {
    JsonDocument doc;
    DeserializationError error = deserializeJson(doc, http.getString());
    if (!error && doc["ping"].is<JsonObject>()) remotePing(doc["ping"]["job_id"] | 0U, String(doc["ping"]["target"] | ""));
    if (!error && doc["live_interval_s"].is<uint32_t>() &&
        doc["live_remaining_s"].is<uint32_t>()) {
      uint32_t interval = doc["live_interval_s"].as<uint32_t>();
      uint32_t remaining = doc["live_remaining_s"].as<uint32_t>();
      const char *mode = doc["mode"] | "";
      if (interval >= 2 && interval <= 30 && remaining <= 120 &&
          (!strcmp(mode, "live") || !strcmp(mode, "telemetry"))) {
        bool live = !strcmp(mode, "live") && remaining > 0;
        if (live != liveMode || interval != liveIntervalS ||
            (doc["revision"] | 0U) != controlRevision) {
          firstSend = true;
        }
        liveIntervalS = interval;
        liveMode = live;
        controlRevision = doc["revision"] | 0;
        liveStartedAt = millis();
        liveLeaseMs = remaining * 1000;
      }
    }
  }
  http.end();
}
bool measureNextService() {
  if (serviceStarted &&
      uint32_t(millis() - lastServiceStep) < (liveMode ? 3000 : 15000))
    return false;
  serviceStarted = true;
  lastServiceStep = millis();
  ServiceProbe &result = serviceProbes[nextService];
  IPAddress address;
  uint32_t start = millis();
  result.dnsOk =
      WiFi.hostByName(SERVICE_HOSTS[nextService], address, 2000) == 1;
  result.dnsMs = millis() - start;
  result.tcpOk = false;
  result.tcpMs = 0;
  if (result.dnsOk) {
    WiFiClient client;
    client.setTimeout(1500);
    start = millis();
    result.tcpOk = client.connect(address, 443) == 1;
    result.tcpMs = millis() - start;
    client.stop();
  }
  result.measured = true;
  result.measuredAt = millis();
  lastServiceIndex = nextService;
  lastInternetOk = result.tcpOk;
  internetMeasuredAt = result.measuredAt;
  internetMeasured = true;
  nextService = (nextService + 1) % 4;
  return true;
}
void sendTelemetry() {
  if (pingRunning || gatewayRunning) return;
  if (WiFi.status() != WL_CONNECTED || !cfg.backend[0] || !cfg.device[0] ||
      !cfg.key[0])
    return;
  uint32_t now = millis();
  if (!firstSend &&
      uint32_t(now - lastSend) <
          ((liveMode ? liveIntervalS : telemetryIntervalS) * 1000))
    return;
  if (networkEnabled && !gatewaySampleReady) { startGatewayPing(); return; }
  firstSend = false;
  lastSend = now;
  bool tested = networkEnabled && measureNextService();
  uint32_t localMs = 0, internetMs = 0;
  int local = networkEnabled ? getStatus(String(cfg.backend) + "/api/probe", localMs) : 0;
  if (tested && lastServiceIndex >= 0)
    internetMs = serviceProbes[lastServiceIndex].dnsMs +
                 serviceProbes[lastServiceIndex].tcpMs;
  String payload = "{\"rssi\":" + String(WiFi.RSSI()) + ",\"local_ms\":" +
                   (local == 200 ? String(localMs) : String("null"));
  payload += ",\"internet_ok\":" +
             String(tested ? (lastInternetOk ? "true" : "false") : "null") +
             ",\"internet_ms\":" +
             (tested && lastInternetOk ? String(internetMs) : String("null")) +
             "}";
  payload.remove(payload.length() - 1);
  payload += ",\"features\":" + featuresJson();
  if (networkEnabled && gatewayAvailable) payload += ",\"gateway_ping\":" + gatewayJson();
  gatewaySampleReady=false;
  if (temperatureEnabled) payload += ",\"environment\":" + environmentJson();
  payload +=
      ",\"delivery\":{\"mode\":" + jsonString(liveMode ? "live" : "telemetry") +
      ",\"send_interval_s\":" +
      String(liveMode ? liveIntervalS : telemetryIntervalS) +
      ",\"telemetry_interval_s\":" + String(telemetryIntervalS) +
      ",\"revision\":" + String(controlRevision) + "}";
  payload +=
      ",\"firmware_version\":\"1.20.0\",\"boot_id\":" + jsonString(bootId) +
      ",\"sequence\":" + String(++telemetrySequence);
  payload += ",\"wifi\":{\"ssid\":" + jsonString(WiFi.SSID()) +
             ",\"bssid\":" + jsonString(WiFi.BSSIDstr()) +
             ",\"channel\":" + String(WiFi.channel());
  payload += ",\"ip\":" + jsonString(WiFi.localIP().toString()) +
             ",\"gateway\":" + jsonString(WiFi.gatewayIP().toString()) +
             ",\"dns\":" + jsonString(WiFi.dnsIP().toString());
  payload += ",\"disconnect_count\":" + String(disconnectCount) +
             ",\"disconnected_ms\":" + String(disconnectedMs) +
             ",\"last_disconnect_reason\":" + String(lastDisconnectReason) +
             "}";
  payload +=
      ",\"device_health\":{\"uptime_s\":" + String(millis() / 1000) +
      ",\"free_heap_bytes\":" + String(ESP.getFreeHeap()) +
      ",\"heap_fragmentation_percent\":" + String(ESP.getHeapFragmentation()) +
      ",\"reset_reason\":" + jsonString(ESP.getResetReason()) + "}";
  payload += ",\"diagnostics\":{\"local_http_code\":" + String(local) +
             ",\"internet_http_code\":" + String("null") +
             ",\"internet_test_kind\":\"dns_tcp\",\"service_target\":" +
             jsonString(tested ? SERVICE_IDS[lastServiceIndex] : "") + "}";
  if (networkEnabled && pingAvailable) {
    payload += ",\"ping\":{\"job_id\":" + String(pingJob) +
               ",\"target\":" + jsonString(pingTarget) +
               ",\"sent\":" + String(pingSent) +
               ",\"received\":" + String(pingReceived);
    payload +=
        ",\"loss_percent\":" +
        String(100.0 * (pingSent - pingReceived) / pingSent, 1) +
        ",\"min_ms\":" + (pingReceived ? String(pingMin) : String("null"));
    payload +=
        ",\"avg_ms\":" +
        (pingReceived ? String((float)pingSum / pingReceived, 1)
                      : String("null")) +
        ",\"max_ms\":" + (pingReceived ? String(pingMax) : String("null"));
    payload += ",\"packets\":[";
    for (uint8_t i=0;i<pingPacketCount;i++) {
      if (i) payload += ',';
      const PingPacket &p=pingPackets[i];
      payload += "{\"seq\":"+String(p.seq)+",\"bytes\":"+String(p.bytes)+",\"ms\":"+String(p.ms)+",\"ok\":"+(p.ok?String("true"):String("false"))+"}";
    }
    payload += "],\"age_ms\":" + String(uint32_t(millis() - pingFinishedAt)) + "}";
  }
  payload += ",\"services\":{";
  bool comma = false;
  for (uint8_t i = 0; i < 4; i++) {
    const ServiceProbe &result = serviceProbes[i];
    if (!networkEnabled || !result.measured)
      continue;
    if (comma)
      payload += ',';
    comma = true;
    payload += jsonString(SERVICE_IDS[i]) +
               ":{\"dns_ok\":" + String(result.dnsOk ? "true" : "false") +
               ",\"tcp_ok\":" + String(result.tcpOk ? "true" : "false");
    payload += ",\"dns_ms\":" + String(result.dnsMs) + ",\"tcp_ms\":" +
               (result.dnsOk ? String(result.tcpMs) : String("null"));
    payload +=
        ",\"age_ms\":" + String(uint32_t(millis() - result.measuredAt)) + "}";
  }
  payload += "}}";
  WiFiClient client;
  client.setTimeout(2000);
  HTTPClient http;
  http.setTimeout(2000);
  if (http.begin(client, String(cfg.backend) + "/api/telemetry")) {
    http.addHeader("Content-Type", "application/json");
    http.addHeader("X-Device-ID", cfg.device);
    http.addHeader("X-Device-Key", cfg.key);
    lastPost = http.POST(payload);
    http.end();
    Serial.printf("Telemetri HTTP %d\n", lastPost);
  }
}

void setup() {
  Serial.begin(115200);
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  loadSettings();
  dht.begin();
  lastSensorRead = millis()-3000;
  bootId = randomHex();
  disconnectHandler = WiFi.onStationModeDisconnected(
      [](const WiFiEventStationModeDisconnected &event) {
        if (hadConnection) {
          disconnectCount++;
          lastDisconnectReason = event.reason;
          disconnectedAt = millis();
          outageActive = true;
          hadConnection = false;
        }
      });
  gotIpHandler = WiFi.onStationModeGotIP([](const WiFiEventStationModeGotIP &) {
    if (outageActive) {
      disconnectedMs += uint32_t(millis() - disconnectedAt);
      outageActive = false;
    }
    hadConnection = true;
  });
  sessionToken = randomHex();
  apName = "WARON-IOT-" + String(ESP.getChipId(), HEX);
  Serial.printf("\nWARON-IoT v1.20 | Login: admin | Password login/AP: %s\n",
                cfg.adminPass);
  if (!storageReady)
    Serial.println("ERROR: gagal menyimpan konfigurasi");
  connectWifi();
  web.collectHeaders("X-Device-Token");
  web.on("/", HTTP_GET, [] {
    web.sendHeader("Cache-Control", "no-store");
    lastUiRequest = millis();
    web.sendHeader("Content-Encoding","gzip");
    web.send_P(200,"text/html",reinterpret_cast<const char *>(STATUS_HTML_GZ),sizeof(STATUS_HTML_GZ));
  });
  web.on("/health", HTTP_GET, handleHealth);
  web.on("/setup", HTTP_GET, [] {
    if (!requireAuth())
      return;
    lastPortal = millis();
    web.sendHeader("Cache-Control", "no-store");
    lastUiRequest = millis();
    web.sendHeader("Content-Encoding","gzip");
    web.send_P(200,"text/html",reinterpret_cast<const char *>(SETUP_HTML_GZ),sizeof(SETUP_HTML_GZ));
  });
  web.on("/api/status", HTTP_GET, handleStatus);
  web.on("/api/scan", HTTP_GET, handleScan);
  web.on("/api/wifi", HTTP_POST, handleWifi);
  web.on("/api/config", HTTP_POST, handleConfig);
  web.on("/api/ota", HTTP_POST, handleUploadDone, handleUpload);
  web.onNotFound([] { message(404, "Tidak ditemukan"); });
  web.begin();
}
void loop() {
  web.handleClient();
  if (restartAt) {
    if ((int32_t)(millis() - restartAt) >= 0)
      ESP.restart();
    delay(1);
    return;
  }
  if (otaRunning) {
    delay(1);
    return;
  }
  networkTick();
  sensorTick();
  if (scanPending && millis() - lastPortal > 30000) {
    WiFi.scanDelete();
    scanPending = false;
  }
  if (uiQuiet() && !pingRunning && !scanPending && !reconnectAt &&
      (!lastPortal || millis() - lastPortal > 15000)) {
    pollDeliveryControl();
    web.handleClient();
    if (uiQuiet() && !otaRunning && !restartAt && !scanPending && !reconnectAt) sendTelemetry();
  }
  delay(1);
}
