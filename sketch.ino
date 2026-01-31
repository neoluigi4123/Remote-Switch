#include <WiFi.h>
#include <WiFiUdp.h>
#include "Adafruit_TinyUSB.h"

// --- WIFI SETTINGS ---
const char* ssid = "";
const char* password = "";
unsigned int localPort = 4210;

// --- HORI POKKEN PAD ID ---
#define VID 0x0F0D
#define PID 0x0092

// --- HID REPORT DESCRIPTOR (Standard HORI Pokken) ---
uint8_t const desc_hid_report[] = {
    0x05, 0x01,        // Usage Page (Generic Desktop Ctrls)
    0x09, 0x05,        // Usage (Game Pad)
    0xA1, 0x01,        // Collection (Application)
    
    // 16 Buttons (2 bytes)
    0x15, 0x00,        //   Logical Minimum (0)
    0x25, 0x01,        //   Logical Maximum (1)
    0x35, 0x00,        //   Physical Minimum (0)
    0x45, 0x01,        //   Physical Maximum (1)
    0x75, 0x01,        //   Report Size (1)
    0x95, 0x10,        //   Report Count (16)
    0x05, 0x09,        //   Usage Page (Button)
    0x19, 0x01,        //   Usage Minimum (1)
    0x29, 0x10,        //   Usage Maximum (16)
    0x81, 0x02,        //   Input (Data,Var,Abs,No Wrap,Linear,Preferred State,No Null Position)
    
    // Hat Switch (1 byte: 4 bits data + 4 bits padding)
    0x05, 0x01,        //   Usage Page (Generic Desktop Ctrls)
    0x25, 0x07,        //   Logical Maximum (7)
    0x46, 0x3B, 0x01,  //   Physical Maximum (315)
    0x75, 0x04,        //   Report Size (4)
    0x95, 0x01,        //   Report Count (1)
    0x65, 0x14,        //   Unit (System: English Rotation, Length: Centimeter)
    0x09, 0x39,        //   Usage (Hat switch)
    0x81, 0x42,        //   Input (Data,Var,Abs,No Wrap,Linear,Preferred State,Null State)
    0x65, 0x00,        //   Unit (None)
    0x95, 0x01,        //   Report Count (1)
    0x81, 0x01,        //   Input (Const,Array,Abs,No Wrap,Linear,Preferred State,No Null Position)
    
    // 4 Axes (4 bytes: LX, LY, RX, RY)
    0x26, 0xFF, 0x00,  //   Logical Maximum (255)
    0x46, 0xFF, 0x00,  //   Physical Maximum (255)
    0x09, 0x30,        //   Usage (X)
    0x09, 0x31,        //   Usage (Y)
    0x09, 0x32,        //   Usage (Z)
    0x09, 0x35,        //   Usage (Rz)
    0x75, 0x08,        //   Report Size (8)
    0x95, 0x04,        //   Report Count (4)
    0x81, 0x02,        //   Input (Data,Var,Abs,No Wrap,Linear,Preferred State,No Null Position)
    
    // Vendor Byte (1 byte - REQUIRED padding for 8-byte alignment)
    0x06, 0x00, 0xFF,  //   Usage Page (Vendor Defined 0xFF00)
    0x09, 0x20,        //   Usage (0x20)
    0x95, 0x01,        //   Report Count (1)
    0x81, 0x02,        //   Input (Data,Var,Abs)
    
    0xC0               // End Collection
};

// Struct must match the report exactly (8 bytes)
typedef struct __attribute__((packed)) {
    uint16_t buttons;
    uint8_t  hat;
    uint8_t  lx;
    uint8_t  ly;
    uint8_t  rx;
    uint8_t  ry;
    uint8_t  vendor;
} SwitchReport;

// Incoming UDP packet structure
struct __attribute__((packed)) PacketData {
    uint16_t buttons;
    uint8_t hat;
    uint8_t lx;
    uint8_t ly;
    uint8_t rx;
    uint8_t ry;
};

Adafruit_USBD_HID usb_hid;
WiFiUDP udp;
volatile SwitchReport gp;
volatile bool wifiConnected = false;

// --- CORE 0: USB ---
void setup() {
    // No Serial.begin() here!
    
    TinyUSBDevice.setID(VID, PID);
    TinyUSBDevice.setManufacturerDescriptor("HORI CO.,LTD.");
    TinyUSBDevice.setProductDescriptor("POKKEN CONTROLLER");
    TinyUSBDevice.setSerialDescriptor("000000000001");

    usb_hid.setPollInterval(1);
    usb_hid.setReportDescriptor(desc_hid_report, sizeof(desc_hid_report));
    usb_hid.begin();

    // Wait for USB mount
    while (!TinyUSBDevice.mounted()) {
        delay(1);
    }

    // Initialize Neutral State
    gp.buttons = 0;
    gp.hat = 0x08;
    gp.lx = 128;
    gp.ly = 128;
    gp.rx = 128;
    gp.ry = 128;
    gp.vendor = 0;

    pinMode(LED_BUILTIN, OUTPUT);
}

void loop() {
    if (usb_hid.ready()) {
        usb_hid.sendReport(0, (void*)&gp, sizeof(SwitchReport));
    }
    
    // Blink LED if NO WiFi, Solid if WiFi connected
    if (wifiConnected) {
        digitalWrite(LED_BUILTIN, HIGH);
    } else {
        digitalWrite(LED_BUILTIN, (millis() / 250) % 2);
    }
    
    delay(1);
}

// --- CORE 1: WiFi ---
void setup1() {
    delay(2000); // Give USB time to settle
    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid, password);

    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
    }

    udp.begin(localPort);
    wifiConnected = true;
}

void loop1() {
    if (wifiConnected) {
        int packetSize = udp.parsePacket();
        if (packetSize >= sizeof(PacketData)) {
            PacketData packet;
            udp.read((char*)&packet, sizeof(packet));

            gp.buttons = packet.buttons;
            gp.hat = packet.hat;
            gp.lx = packet.lx;
            gp.ly = packet.ly;
            gp.rx = packet.rx;
            gp.ry = packet.ry;
        }
    }
    delay(1);
}
