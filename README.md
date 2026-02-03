# Remote-Switch
A repo that enable you to host your switch on a web browser to play with your friends remotely

## Features
- Stream your nintendo switch on your network
- Play it remotely with any controller from your web-browser
- Support up to two user on the server (total of 3 player if one physically)

# Setup & Requirements

## Hardware Requirements
To set this up, you will need:
1. **Raspberry Pi Pico W:** Acts as the "Brain" that translates web inputs into USB Controller signals for the Switch.
2. **HDMI Video Capture Card:** To get the video/audio signal from the Switch into your PC/Server.
3. **Micro-USB to USB-A Cable:** To connect the Pico W to the Switch dock.

## Setup

For the raspberry pico pi card, you can either use the **Arduino IDE** or **Visual Studio Code** with *PlatformIO*, which is the recommanded solution.

**Arduino IDE:**
1. Open `File` > `Preferences`.
2. Add this URL to **Additional Boards Manager URLs**:  
   `https://github.com/earlephilhower/arduino-pico/releases/download/global/package_rp2040_index.json`
3. Go to `Tools` > `Board` > `Boards Manager`, search for **RP2040**, and install the package (v5.5.0 or higher).
4. Go to `Tools` > `USB Stack` and select **Adafruit TinyUSB**.
5. Upload the provided sketch to your Pico W.

**Visual Studio Code / PlatformIO:**
1. Install the library from VSCode.
2. Create a new project.
3. Replace the contents of `platformio.ini` with the version provided in this repository.
4. Build and upload.

**Python:**
1. **Find the Pico W IP Address:**  
   Ensure your Pico W is on the same network. You can find its IP using `nmap` (Linux):
   ```bash
   nmap -p 4210 192.168.1.0/24
   ```
2. Create a new environment and install the dependencies.
   If you're encounting an error with the library `pyaudio` on linux, you may need to run this command first: `sudo apt-get install libasound2-dev libportaudio2 libportaudiocpp0 portaudio19-dev` then reinstall the library `pip install pyaudio`
4. Either run `main.py` if you're on windows, if on linux, run `main_linux.py`.
5. Enjoy

## Future Roadmap
- [ ] **Haptic Feedback:** Rumble support with a toggle.
- [ ] **Keyboard Input:** Map keyboard keys to controller buttons.
- [ ] **Touch Controls:** On-screen buttons for mobile/tablet users.
- [ ] **Dynamic Quality:** Adjustable bitrate and resolution settings.
- [ ] **Motion Controls:** Gyroscope / Fake-gyro support.
- [ ] **Auto-Detection:** Unified script to detect OS and hardware automatically.
- [ ] **Audio Toggle:** Improve UI for muting/unmuting the stream.
