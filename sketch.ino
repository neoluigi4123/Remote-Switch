#include "hardware/watchdog.h" // Import watchdog for internet safety
#include <WiFi.h>
#include <WiFiUdp.h>
#include "Adafruit_TinyUSB.h"
#include "pico/mutex.h" // Import Mutex for Dual Core safety

// --- WIFI SETTINGS ---
const char* ssid = "";
const char* password = "";
unsigned int localPort = 4210;

// --- ID SETTINGS ---
#define VID 0x0F0D
#define PID 0x0092

// --- HID REPORT DESCRIPTOR ---
uint8_t const desc_hid_report[] = {
    0x05, 0x01, 0x09, 0x05, 0xA1, 0x01, 
    0x15, 0x00, 0x25, 0x01, 0x35, 0x00, 0x45, 0x01, 
    0x75, 0x01, 0x95, 0x10, 0x05, 0x09, 0x19, 0x01, 
    0x29, 0x10, 0x81, 0x02,
    0x05, 0x01, 0x25, 0x07, 0x46, 0x3B, 0x01, 0x75, 0x04, 0x95, 0x01, 
    0x65, 0x14, 0x09, 0x39, 0x81, 0x42, 0x65, 0x00, 0x95, 0x01, 0x81, 0x01,
    0x26, 0xFF, 0x00, 0x46, 0xFF, 0x00, 0x09, 0x30, 0x09, 0x31, 
    0x09, 0x32, 0x09, 0x35, 0x75, 0x08, 0x95, 0x04, 0x81, 0x02,
    0x06, 0x00, 0xFF, 0x09, 0x20, 0x95, 0x01, 0x81, 0x02,
    0xC0
};

typedef struct __attribute__((packed)) {
    uint16_t buttons;
    uint8_t  hat;
    uint8_t  lx; uint8_t  ly; uint8_t  rx; uint8_t  ry;
    uint8_t  vendor;
} SwitchReport;

struct __attribute__((packed)) PacketData {
    uint8_t  playerId;
    uint16_t buttons;
    uint8_t  hat;
    uint8_t  lx; uint8_t  ly; uint8_t  rx; uint8_t  ry;
};

Adafruit_USBD_HID usb_hid1;
Adafruit_USBD_HID usb_hid2;
WiFiUDP udp;

// SHARED DATA
SwitchReport gp1, gp2;
auto_init_mutex(reportMutex); // AUTOMATIC MUTEX INITIALIZATION

// TIMEOUT LOGIC
unsigned long lastPacketTime1 = 0;
unsigned long lastPacketTime2 = 0;
const unsigned long TIMEOUT_MS = 500; // Reset controller if no data for 500ms

void resetReport(SwitchReport* r) {
    r->buttons = 0;
    r->hat = 0x08;
    r->lx = 128; r->ly = 128; r->rx = 128; r->ry = 128;
    r->vendor = 0;
}

void setup() {
    TinyUSBDevice.setID(VID, PID);
    TinyUSBDevice.setManufacturerDescriptor("HORI CO.,LTD.");
    TinyUSBDevice.setProductDescriptor("POKKEN DUAL");

    usb_hid1.setPollInterval(1);
    usb_hid1.setReportDescriptor(desc_hid_report, sizeof(desc_hid_report));
    usb_hid1.begin();

    usb_hid2.setPollInterval(1);
    usb_hid2.setReportDescriptor(desc_hid_report, sizeof(desc_hid_report));
    usb_hid2.begin();

    while (!TinyUSBDevice.mounted()) delay(1);

    resetReport(&gp1);
    resetReport(&gp2);
    pinMode(LED_BUILTIN, OUTPUT);
    watchdog_enable(3000, 1); 
}

// CORE 0: USB SENDING
void loop() {
    watchdog_update();

    static SwitchReport localGp1, localGp2;

    // 1. SAFELY COPY DATA FROM SHARED MEMORY
    mutex_enter_blocking(&reportMutex);
    memcpy(&localGp1, &gp1, sizeof(SwitchReport));
    memcpy(&localGp2, &gp2, sizeof(SwitchReport));
    mutex_exit(&reportMutex);

    // 2. SEND TO USB
    if (usb_hid1.ready()) usb_hid1.sendReport(0, &localGp1, sizeof(SwitchReport));
    if (usb_hid2.ready()) usb_hid2.sendReport(0, &localGp2, sizeof(SwitchReport));
    
    delay(1);
}

// CORE 1: WIFI RECEIVING
void setup1() {
    delay(2000);
    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid, password);
    while (WiFi.status() != WL_CONNECTED) {
        digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN));
        delay(250);
    }
    digitalWrite(LED_BUILTIN, HIGH);
    udp.begin(localPort);
}

void loop1() {
    // --- CONNECTION CHECK ---
    if (WiFi.status() != WL_CONNECTED) {
        digitalWrite(LED_BUILTIN, LOW); // Visual feedback: LED OFF = No WiFi
        
        // 1. Close the dead UDP socket
        udp.stop(); 
        
        // 2. Force disconnect and reconnect
        WiFi.disconnect();
        WiFi.begin(ssid, password);
        
        // 3. Wait for connection (blocking is okay here on Core 1)
        unsigned long startAttempt = millis();
        while (WiFi.status() != WL_CONNECTED && millis() - startAttempt < 10000) {
            delay(500);
        }

        // 4. IMPORTANT: Restart the UDP listener
        if (WiFi.status() == WL_CONNECTED) {
            udp.begin(localPort);
            digitalWrite(LED_BUILTIN, HIGH); // LED ON = Connected
        }
        return; 
    }

    // --- PACKET PROCESSING ---
    // Fix: Process ALL available packets to drain the buffer.
    // This ensures we always react to the LATEST packet and don't lag behind.
    int packetSize;
    while ((packetSize = udp.parsePacket())) { 
        if (packetSize >= sizeof(PacketData)) {
            PacketData packet;
            udp.read((char*)&packet, sizeof(packet));

            // Update Shared Memory
            mutex_enter_blocking(&reportMutex);
            if (packet.playerId == 1) {
                gp1.buttons = packet.buttons; gp1.hat = packet.hat;
                gp1.lx = packet.lx; gp1.ly = packet.ly; gp1.rx = packet.rx; gp1.ry = packet.ry;
                lastPacketTime1 = millis();
            } 
            else if (packet.playerId == 2) {
                gp2.buttons = packet.buttons; gp2.hat = packet.hat;
                gp2.lx = packet.lx; gp2.ly = packet.ly; gp2.rx = packet.rx; gp2.ry = packet.ry;
                lastPacketTime2 = millis();
            }
            mutex_exit(&reportMutex);
        } else {
            // Flush incomplete/garbage packets
            udp.flush(); 
        }
    }

    // --- TIMEOUT LOGIC ---
    unsigned long now = millis();
    mutex_enter_blocking(&reportMutex);
    if (now - lastPacketTime1 > TIMEOUT_MS) resetReport(&gp1);
    if (now - lastPacketTime2 > TIMEOUT_MS) resetReport(&gp2);
    mutex_exit(&reportMutex);

    // Short delay to yield to WiFi background tasks
    delay(2); 
}
