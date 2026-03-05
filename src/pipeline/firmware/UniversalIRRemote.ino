/*
 * Universal IR Remote Control for ATmega328
 * Supports 10 major TV brands with auto-scan functionality
 * 
 * ONE FIRMWARE FOR ALL REMOTES - No recompilation needed!
 * Just connect the buttons you need. Unused pins are ignored.
 * 
 * Hardware:
 * - ATmega328 (Arduino Uno/Nano compatible)
 * - IR LED on pin 3 (PWM capable)
 * - Connect only the buttons you need (unused pins auto-ignored)
 * - Optional: Status LED on pin 13
 * 
 * Button Pins (active LOW, internal pull-up):
 * - Pin 2:  Power / Auto-Scan (quick press = power, hold 5s = scan)
 * - Pin 4:  Volume Up    (optional)
 * - Pin 5:  Volume Down  (optional)
 * - Pin 6:  Channel 1    (optional)
 * - Pin 7:  Channel 2    (optional)
 * - Pin 8:  Channel 3    (optional)
 * - Pin 9:  Channel 4    (optional)
 * - Pin 10: Channel 5    (optional)
 * - Pin 11: (free - unused)
 * - Pin 12: Brand Select (optional)
 * 
 * POWER BUTTON DUAL FUNCTION:
 * - Quick press (<5s) = Send power on/off command
 * - Hold (5+ seconds) = Enter auto-scan mode
 * - During scan: any button press confirms brand
 * 
 * WIRING: Connect button between pin and GND. That's it!
 * Unconnected pins stay HIGH (pull-up) and are ignored.
 * 
 * Install IRremote library: Arduino IDE -> Tools -> Manage Libraries -> "IRremote"
 */

#include <IRremote.hpp>
#include <EEPROM.h>

// ============== PIN DEFINITIONS ==============
// Change these values to match your PCB routing
// See STANDALONE_GUIDE.md for ATmega physical pin mappings
//
// IMPORTANT: IR_SEND_PIN must be PWM-capable: 3, 5, 6, 9, 10, or 11
//
// Arduino Pin → ATmega328 Physical Pin:
//   D2→Pin4   D3→Pin5   D4→Pin6   D5→Pin11  D6→Pin12  D7→Pin13
//   D8→Pin14  D9→Pin15  D10→Pin16 D11→Pin17 D12→Pin18 D13→Pin19
//   A0→Pin23  A1→Pin24  A2→Pin25  A3→Pin26  A4→Pin27  A5→Pin28

#define IR_SEND_PIN     3   // ATmega Pin 5  - MUST be PWM pin!
#define POWER_BTN       2   // ATmega Pin 4  - Dual: quick=power, hold=scan
#define VOL_UP_BTN      4   // ATmega Pin 6  - Optional
#define VOL_DOWN_BTN    5   // ATmega Pin 11 - Optional
#define CH1_BTN         6   // ATmega Pin 12 - Optional
#define CH2_BTN         7   // ATmega Pin 13 - Optional
#define CH3_BTN         8   // ATmega Pin 14 - Optional
#define CH4_BTN         9   // ATmega Pin 15 - Optional
#define CH5_BTN         10  // ATmega Pin 16 - Optional
#define BRAND_BTN       12  // ATmega Pin 18 - Optional
#define STATUS_LED      13  // ATmega Pin 19 - Optional

// ============== CONSTANTS ==============
#define NUM_BRANDS      10
#define DEBOUNCE_MS     200
#define SCAN_DELAY_MS   2000  // Time between brand attempts during scan
#define LONG_PRESS_MS   5000  // Hold power button 5s to enter scan mode
#define EEPROM_BRAND_ADDR 0

// ============== ENUMERATIONS ==============
enum TVBrand {
  BRAND_SAMSUNG = 0,
  BRAND_LG,
  BRAND_SONY,
  BRAND_PANASONIC,
  BRAND_PHILIPS,
  BRAND_TOSHIBA,
  BRAND_SHARP,
  BRAND_VIZIO,
  BRAND_TCL,
  BRAND_HISENSE
};

enum Protocol {
  PROTO_NEC,
  PROTO_SAMSUNG,
  PROTO_SONY,
  PROTO_RC5,
  PROTO_RC6
};

// Brand names for serial output
const char* brandNames[] = {
  "Samsung",
  "LG",
  "Sony",
  "Panasonic",
  "Philips",
  "Toshiba",
  "Sharp",
  "Vizio",
  "TCL",
  "Hisense"
};

// ============== IR CODE STRUCTURE ==============
struct IRCode {
  Protocol protocol;
  uint32_t power;
  uint32_t volUp;
  uint32_t volDown;
  uint32_t ch1;
  uint32_t ch2;
  uint32_t ch3;
  uint32_t ch4;
  uint32_t ch5;
  uint8_t bits;
  uint16_t address;
};

// ============== IR CODES DATABASE ==============
// Common IR codes for each brand (some TV models may vary)
const IRCode brandCodes[NUM_BRANDS] = {
  // Samsung - Samsung protocol
  {
    PROTO_SAMSUNG,
    0xE0E040BF,  // Power
    0xE0E0E01F,  // Vol+
    0xE0E0D02F,  // Vol-
    0xE0E020DF,  // 1
    0xE0E0A05F,  // 2
    0xE0E0609F,  // 3
    0xE0E010EF,  // 4
    0xE0E0906F,  // 5
    32,
    0x07
  },
  // LG - NEC protocol
  {
    PROTO_NEC,
    0x20DF10EF,  // Power
    0x20DF40BF,  // Vol+
    0x20DFC03F,  // Vol-
    0x20DF8877,  // 1
    0x20DF48B7,  // 2
    0x20DFC837,  // 3
    0x20DF28D7,  // 4
    0x20DFA857,  // 5
    32,
    0x04
  },
  // Sony - SIRC protocol (12-bit)
  {
    PROTO_SONY,
    0xA90,       // Power
    0x490,       // Vol+
    0xC90,       // Vol-
    0x010,       // 1
    0x810,       // 2
    0x410,       // 3
    0xC10,       // 4
    0x210,       // 5
    12,
    0x01
  },
  // Panasonic - Kaseikyo protocol
  {
    PROTO_NEC,
    0x400401BC,  // Power
    0x40040420,  // Vol+
    0x40040421,  // Vol-
    0x40040401,  // 1
    0x40040402,  // 2
    0x40040403,  // 3
    0x40040404,  // 4
    0x40040405,  // 5
    32,
    0x4004
  },
  // Philips - RC5 protocol
  {
    PROTO_RC5,
    0x100C,      // Power
    0x1010,      // Vol+
    0x1011,      // Vol-
    0x1001,      // 1
    0x1002,      // 2
    0x1003,      // 3
    0x1004,      // 4
    0x1005,      // 5
    14,
    0x00
  },
  // Toshiba - NEC protocol
  {
    PROTO_NEC,
    0x02FD48B7,  // Power
    0x02FD58A7,  // Vol+
    0x02FD7887,  // Vol-
    0x02FD807F,  // 1
    0x02FD40BF,  // 2
    0x02FDC03F,  // 3
    0x02FD20DF,  // 4
    0x02FDA05F,  // 5
    32,
    0x40
  },
  // Sharp - NEC variant
  {
    PROTO_NEC,
    0x41A2ABCD,  // Power
    0x41A228D7,  // Vol+
    0x41A2A857,  // Vol-
    0x41A2E817,  // 1
    0x41A218E7,  // 2
    0x41A29867,  // 3
    0x41A258A7,  // 4
    0x41A2D827,  // 5
    32,
    0x41A2
  },
  // Vizio - NEC protocol
  {
    PROTO_NEC,
    0x20DF10EF,  // Power
    0x20DF40BF,  // Vol+
    0x20DFC03F,  // Vol-
    0x20DF8877,  // 1
    0x20DF48B7,  // 2
    0x20DFC837,  // 3
    0x20DF28D7,  // 4
    0x20DFA857,  // 5
    32,
    0x04
  },
  // TCL / RCA - NEC protocol
  {
    PROTO_NEC,
    0x40BF12ED,  // Power
    0x40BF1AE5,  // Vol+
    0x40BF1EE1,  // Vol-
    0x40BF00FF,  // 1
    0x40BF807F,  // 2
    0x40BF40BF,  // 3
    0x40BFC03F,  // 4
    0x40BF20DF,  // 5
    32,
    0x02
  },
  // Hisense - NEC protocol
  {
    PROTO_NEC,
    0x20DF10EF,  // Power
    0x20DF40BF,  // Vol+
    0x20DFC03F,  // Vol-
    0x20DF8877,  // 1
    0x20DF48B7,  // 2
    0x20DFC837,  // 3
    0x20DF28D7,  // 4
    0x20DFA857,  // 5
    32,
    0x04
  }
};

// ============== GLOBAL VARIABLES ==============
uint8_t currentBrand = 0;
bool scanMode = false;
unsigned long lastButtonPress = 0;

// ============== FUNCTION DECLARATIONS ==============
void sendCommand(uint32_t code, const char* cmdName);
void cycleBrand();
void saveBrand();
void startAutoScan();
bool debounce();
void blinkLED(int times, int delayMs);

// ============== SETUP ==============
void setup() {
  // Initialize serial for debugging
  Serial.begin(9600);
  Serial.println(F("================================"));
  Serial.println(F("  Universal IR Remote v1.0"));
  Serial.println(F("  10 TV Brands Supported"));
  Serial.println(F("================================"));
  
  // Initialize IR sender
  IrSender.begin(IR_SEND_PIN);
  
  // Configure button pins with internal pull-ups
  // All pins configured - unconnected pins stay HIGH and are ignored
  pinMode(POWER_BTN, INPUT_PULLUP);
  pinMode(VOL_UP_BTN, INPUT_PULLUP);
  pinMode(VOL_DOWN_BTN, INPUT_PULLUP);
  pinMode(CH1_BTN, INPUT_PULLUP);
  pinMode(CH2_BTN, INPUT_PULLUP);
  pinMode(CH3_BTN, INPUT_PULLUP);
  pinMode(CH4_BTN, INPUT_PULLUP);
  pinMode(CH5_BTN, INPUT_PULLUP);
  pinMode(BRAND_BTN, INPUT_PULLUP);
  
  // Status LED
  pinMode(STATUS_LED, OUTPUT);
  
  // Load saved brand from EEPROM
  currentBrand = EEPROM.read(EEPROM_BRAND_ADDR);
  if (currentBrand >= NUM_BRANDS) {
    currentBrand = 0;  // Default to Samsung if invalid
  }
  
  Serial.println();
  Serial.print(F("Current brand: "));
  Serial.println(brandNames[currentBrand]);
  Serial.println();
  Serial.println(F("CONTROLS:"));
  Serial.println(F("- Quick POWER   = Power on/off"));
  Serial.println(F("- Hold POWER 5s = Auto-scan mode"));
  Serial.println(F("- Press BRAND   = Cycle brands"));
  Serial.println(F("================================"));
  
  // Startup indication
  blinkLED(3, 100);
}

// ============== MAIN LOOP ==============
void loop() {
  // Check for power button (dual function: quick=power, hold=scan)
  if (digitalRead(POWER_BTN) == LOW) {
    delay(50);  // Debounce
    if (digitalRead(POWER_BTN) == LOW) {
      unsigned long pressStart = millis();
      bool longPressTriggered = false;
      
      // Wait for release or long press threshold
      while (digitalRead(POWER_BTN) == LOW) {
        if (millis() - pressStart >= LONG_PRESS_MS && !longPressTriggered) {
          // Long press detected - start auto-scan
          longPressTriggered = true;
          startAutoScan();
          // Wait for button release before continuing
          while (digitalRead(POWER_BTN) == LOW) {
            delay(50);
          }
          return;
        }
      }
      
      // Button released before long press - send power command
      if (!longPressTriggered && debounce()) {
        sendCommand(brandCodes[currentBrand].power, "Power");
      }
    }
    return;
  }
  
  // Check for brand select button
  if (digitalRead(BRAND_BTN) == LOW && debounce()) {
    cycleBrand();
    return;
  }
  
  // Check function buttons
  // Unconnected pins stay HIGH (pull-up) and never trigger
  if (digitalRead(VOL_UP_BTN) == LOW && debounce()) {
    sendCommand(brandCodes[currentBrand].volUp, "Volume Up");
  }
  else if (digitalRead(VOL_DOWN_BTN) == LOW && debounce()) {
    sendCommand(brandCodes[currentBrand].volDown, "Volume Down");
  }
  else if (digitalRead(CH1_BTN) == LOW && debounce()) {
    sendCommand(brandCodes[currentBrand].ch1, "Channel 1");
  }
  else if (digitalRead(CH2_BTN) == LOW && debounce()) {
    sendCommand(brandCodes[currentBrand].ch2, "Channel 2");
  }
  else if (digitalRead(CH3_BTN) == LOW && debounce()) {
    sendCommand(brandCodes[currentBrand].ch3, "Channel 3");
  }
  else if (digitalRead(CH4_BTN) == LOW && debounce()) {
    sendCommand(brandCodes[currentBrand].ch4, "Channel 4");
  }
  else if (digitalRead(CH5_BTN) == LOW && debounce()) {
    sendCommand(brandCodes[currentBrand].ch5, "Channel 5");
  }
}

// ============== HELPER FUNCTIONS ==============

bool debounce() {
  if (millis() - lastButtonPress > DEBOUNCE_MS) {
    lastButtonPress = millis();
    return true;
  }
  return false;
}

void blinkLED(int times, int delayMs) {
  for (int i = 0; i < times; i++) {
    digitalWrite(STATUS_LED, HIGH);
    delay(delayMs);
    digitalWrite(STATUS_LED, LOW);
    delay(delayMs);
  }
}

void sendCommand(uint32_t code, const char* cmdName) {
  const IRCode& brand = brandCodes[currentBrand];
  
  Serial.print(F("[TX] "));
  Serial.print(brandNames[currentBrand]);
  Serial.print(F(" - "));
  Serial.print(cmdName);
  Serial.print(F(": 0x"));
  Serial.println(code, HEX);
  
  // Visual feedback
  digitalWrite(STATUS_LED, HIGH);
  
  // Send based on protocol type
  switch (brand.protocol) {
    case PROTO_NEC:
      // NEC: Extract address and command from 32-bit raw code
      // Format: [addr][~addr][cmd][~cmd]
      IrSender.sendNEC((code >> 24) & 0xFF, (code >> 8) & 0xFF, 0);
      break;
      
    case PROTO_SAMSUNG:
      // Samsung: Extract command from 32-bit raw code
      // Format: [addr][addr][cmd][~cmd] - bit reversed in transmission
      // The 3rd byte contains the bit-reversed command
      {
        uint8_t cmdByte = (code >> 8) & 0xFF;
        // Bit-reverse the command byte for IRremote 4.x
        uint8_t cmd = 0;
        for (int i = 0; i < 8; i++) {
          if (cmdByte & (1 << i)) cmd |= (1 << (7 - i));
        }
        IrSender.sendSamsung(brand.address, cmd, 0);
      }
      break;
      
    case PROTO_SONY:
      // Sony protocol requires 3 transmissions
      for (int i = 0; i < 3; i++) {
        IrSender.sendSony(brand.address, code & 0x7F, 0, brand.bits);
        delay(25);
      }
      break;
      
    case PROTO_RC5:
      IrSender.sendRC5(brand.address, code & 0x3F, 0);
      break;
      
    case PROTO_RC6:
      IrSender.sendRC6(brand.address, code & 0xFF, 0);
  }
  
  digitalWrite(STATUS_LED, LOW);
}

void cycleBrand() {
  currentBrand = (currentBrand + 1) % NUM_BRANDS;
  saveBrand();
  
  Serial.print(F("[BRAND] Changed to: "));
  Serial.print(brandNames[currentBrand]);
  Serial.print(F(" ("));
  Serial.print(currentBrand + 1);
  Serial.print(F("/"));
  Serial.print(NUM_BRANDS);
  Serial.println(F(")"));
  
  // Blink LED to indicate brand number (1-10)
  blinkLED(currentBrand + 1, 100);
}

void saveBrand() {
  EEPROM.update(EEPROM_BRAND_ADDR, currentBrand);
}

void startAutoScan() {
  Serial.println();
  Serial.println(F("========== AUTO-SCAN MODE =========="));
  Serial.println(F("Point remote at TV and watch for it"));
  Serial.println(F("to turn ON/OFF. Press any button to"));
  Serial.println(F("confirm when TV responds."));
  Serial.println(F("===================================="));
  Serial.println();
  
  scanMode = true;
  
  // Wait for user to release the power button first
  Serial.println(F("Release button..."));
  while (digitalRead(POWER_BTN) == LOW) {
    delay(10);
  }
  
  // 2-second delay before starting scan
  Serial.println(F("Starting scan in 2 seconds..."));
  for (int i = 0; i < 4; i++) {
    digitalWrite(STATUS_LED, HIGH);
    delay(250);
    digitalWrite(STATUS_LED, LOW);
    delay(250);
  }
  Serial.println(F("Scanning now!"));
  Serial.println();
  
  for (uint8_t brand = 0; brand < NUM_BRANDS && scanMode; brand++) {
    currentBrand = brand;
    
    Serial.print(F("[SCAN] Trying "));
    Serial.print(brand + 1);
    Serial.print(F("/"));
    Serial.print(NUM_BRANDS);
    Serial.print(F(": "));
    Serial.print(brandNames[brand]);
    Serial.println(F("..."));
    
    // Visual indication - blink for brand number
    blinkLED(brand + 1, 50);
    
    // Send power command for this brand
    sendCommand(brandCodes[brand].power, "Power");
    
    // Wait and check for user confirmation
    unsigned long waitStart = millis();
    while (millis() - waitStart < SCAN_DELAY_MS) {
      // Check if any button is pressed to confirm
      // Unconnected pins stay HIGH and won't trigger
      if (digitalRead(POWER_BTN) == LOW || 
          digitalRead(VOL_UP_BTN) == LOW || 
          digitalRead(VOL_DOWN_BTN) == LOW ||
          digitalRead(CH1_BTN) == LOW ||
          digitalRead(CH2_BTN) == LOW ||
          digitalRead(CH3_BTN) == LOW ||
          digitalRead(CH4_BTN) == LOW ||
          digitalRead(CH5_BTN) == LOW ||
          digitalRead(BRAND_BTN) == LOW) {
        
        // User confirmed this brand works!
        scanMode = false;
        saveBrand();
        
        Serial.println();
        Serial.println(F("===================================="));
        Serial.print(F("SUCCESS! Brand set to: "));
        Serial.println(brandNames[currentBrand]);
        Serial.println(F("Settings saved to EEPROM"));
        Serial.println(F("===================================="));
        
        // Success indication - long blink
        digitalWrite(STATUS_LED, HIGH);
        delay(1000);
        digitalWrite(STATUS_LED, LOW);
        
        // Wait for button release
        while (digitalRead(POWER_BTN) == LOW || 
               digitalRead(VOL_UP_BTN) == LOW || 
               digitalRead(VOL_DOWN_BTN) == LOW ||
               digitalRead(BRAND_BTN) == LOW) {
          delay(50);
        }
        return;
      }
      delay(50);
    }
  }
  
  if (scanMode) {
    Serial.println();
    Serial.println(F("===================================="));
    Serial.println(F("Scan complete - no brand confirmed"));
    Serial.println(F("Try manual selection with BRAND btn"));
    Serial.println(F("===================================="));
    
    // Reset to first brand
    currentBrand = 0;
    scanMode = false;
    
    // Failure indication
    blinkLED(5, 200);
  }
}
