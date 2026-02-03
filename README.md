# Remote-Switch
A repo that enable you to host your switch on a web browser to play with your friends remotely

## Features
- Stream your nintendo switch on your network
- Play it remotely with any controller from your web-browser
- Support up to two user on the server (total of 3 player if one physically)

## Setup & Requirements

### Hardware

For this project, you need those pieces of hardware:

- Raspberry pi pico W (wireless)
- Video Capture Card

### Setup

For the raspberry pico pi card, you can either use the **Arduino IDE** or **Visual Studio Code** with *PlatformIO*, which is the recommanded solution.

**Arduino IDE:**
- Pick your board: `File`>`Preferences`>`Additional boards manager URLs:` `https://github.com/earlephilhower/arduino-pico/releases/download/global/package_rp2040_index.json`
- In the dropdown menu of `Tools`>`Board Manager`, pick the raspberry pi project V5.5.0 (or higher)
- `Tools`>`USB stacks`>`Adafruits TinyUSB`

**Visual Studio Code / PlatformIO:**
- Create a new project (and select a proper folder)
- Pick a dummy board and environement (they'll be replaced later in config file)
- In the configuration file `platformio.ini` paste the content of the same filename in the github.

**Python:**
- Look for the raspberry pico pi W ip on your network interface.
  (on linux with *nmap* you can use this command: `nmap -p 4210 192.168.1.0/24` to find it)
- Create a new environment and install the dependencies.
- Either run `main.py` if you're on windows, if on linux, run `main_linux.py`.


## Future features, improvements, and bug fixes:
- Rumble support (with toggle) | (feature)
- Keyboard support | (feature)
- Touch screen support | (feature)
- Quality settings | (feature)
- Gyroscope or fake-gyro support (with toggle) | (feature)
- OS detection algo: windows/linux video/audio record | (feature)
- Audio toggle (fix)
