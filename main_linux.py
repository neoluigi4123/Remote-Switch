#!/usr/bin/env python3
import socket
import struct
import cv2
import threading
import time
import pyaudio
import os
from flask import Flask, render_template_string, Response, request
from flask_socketio import SocketIO

# --- CONFIGURATION ---
PICO_IP = "192.168.1.xxx" # CHANGE THIS TO YOUR PICO IP
PICO_PORT = 4210

STREAM_WIDTH = 256*2
STREAM_HEIGHT = 144*2
JPEG_QUALITY = 25

# --- AUDIO CONFIGURATION ---
# HDMI Capture cards (like MS2109) natively support 48000Hz or 96000Hz.
# 44100Hz often causes failure or glitches on these devices.
RATE = 48000  
CHANNELS = 1 
# Reduced chunk size slightly for lower latency at 48k
CHUNK = 2048 
FORMAT = pyaudio.paInt16

# --- DEVICE DISCOVERY ---
def list_cameras():
    """Scans for video devices (/dev/videoX)."""
    available_indices = []
    for i in range(10):
        cap = cv2.VideoCapture(i, cv2.CAP_V4L2)
        if cap.isOpened():
            available_indices.append(i)
            cap.release()
    return available_indices

def list_audio_devices():
    """Scans for audio input devices using PyAudio."""
    p = pyaudio.PyAudio()
    available_devices = {}
    print("\n--- Available Audio Input Devices ---")
    for i in range(p.get_device_count()):
        dev = p.get_device_info_by_index(i)
        if dev['maxInputChannels'] > 0:
            print(f"Index [{i}]: {dev['name']} (Channels: {dev['maxInputChannels']}, Rate: {int(dev['defaultSampleRate'])})")
            available_devices[i] = dev['name']
    p.terminate()
    return available_devices

# --- VIDEO STREAMER ---
class VideoStreamer:
    def __init__(self, src):
        self.cap = cv2.VideoCapture(src, cv2.CAP_V4L2)
        # Force MJPG to avoid USB bandwidth lag
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M', 'J', 'P', 'G'))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, STREAM_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, STREAM_HEIGHT)
        self.cap.set(cv2.CAP_PROP_FPS, 60)
        
        self.lock = threading.Lock()
        self.frame_event = threading.Event()
        self.jpeg_frame = None
        self.running = True
        
        self.thread = threading.Thread(target=self.update, daemon=True)
        self.thread.start()

    def update(self):
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                frame = cv2.resize(frame, (STREAM_WIDTH, STREAM_HEIGHT))
                success, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                if success:
                    with self.lock:
                        self.jpeg_frame = buffer.tobytes()
                    self.frame_event.set()
                time.sleep(0.005)
            else:
                time.sleep(0.1)

    def get_frame(self):
        if self.frame_event.wait(timeout=1.0):
            with self.lock:
                self.frame_event.clear()
                return self.jpeg_frame
        return None

# --- AUDIO STREAMER ---
class AudioStreamer:
    def __init__(self, sio, input_device_index=None):
        self.sio = sio
        self.p = pyaudio.PyAudio()
        self.input_device_index = input_device_index
        self.running = True
        
        try:
            self.stream = self.p.open(
                format=FORMAT, 
                channels=CHANNELS, 
                rate=RATE, 
                input=True, 
                input_device_index=self.input_device_index,
                frames_per_buffer=CHUNK
            )
            
            dev_name = "Default"
            if self.input_device_index is not None:
                dev_info = self.p.get_device_info_by_index(self.input_device_index)
                dev_name = dev_info.get('name')
            
            print(f"Audio Stream Started: {dev_name} @ {RATE}Hz")
            
            self.thread = threading.Thread(target=self.stream_audio, daemon=True)
            self.thread.start()
        except IOError as e:
            print(f"Audio Error: {e}")
            self.running = False

    def stream_audio(self):
        while self.running:
            try:
                # Read audio data (blocking)
                data = self.stream.read(CHUNK, exception_on_overflow=False)
                # Send to browser
                self.sio.emit('audio_data', data)
            except Exception:
                pass

# --- FLASK APP ---
app = Flask(__name__)
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins='*')

# --- SETUP PHASE ---
print("--- DEVICE SETUP ---")
# 1. Video
cams = list_cameras()
selected_cam = 0
if not cams:
    print("No cameras found.")
else:
    if len(cams) == 1:
        selected_cam = cams[0]
        print(f"Auto-selecting camera: {selected_cam}")
    else:
        print(f"Available cameras: {cams}")
        try:
            selected_cam = int(input(f"Enter Video Camera Index: "))
        except:
            selected_cam = 0

# 2. Audio
audio_devs = list_audio_devices()
selected_audio = None
if audio_devs:
    try:
        print("\nSelect the audio device ID for your Capture Card.")
        user_audio_input = input("Enter Audio Device Index (Press Enter for Default): ")
        if user_audio_input.strip() != "":
            selected_audio = int(user_audio_input)
    except:
        selected_audio = None

streamer = VideoStreamer(selected_cam)
audio_streamer = AudioStreamer(socketio, input_device_index=selected_audio)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def generate_frames():
    while True:
        try:
            frame = streamer.get_frame()
            if frame:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            else:
                time.sleep(0.01)
        except:
            break

HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Linux Stream Deck</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <style>
        body { font-family: sans-serif; background: #111; color: #fff; text-align: center; margin: 0; overflow: hidden; }
        .main-layout { display: flex; flex-direction: column; height: 100vh; width: 100vw; }
        .tab-nav { display: flex; background: #222; border-bottom: 1px solid #444; height: 40px; }
        .tab-btn { flex: 1; background: transparent; color: #aaa; border: none; cursor: pointer; font-size: 14px; font-weight: bold; }
        .tab-btn.active { background: #444; color: #fff; border-bottom: 2px solid #0f0; }
        .tab-content { flex: 1; display: none; position: relative; }
        .tab-content.active { display: flex; justify-content: center; align-items: center; background: #000; }
        #usb-feed { height: 100%; max-width: 100%; object-fit: contain; }
        .controls-bar { 
            position: absolute; bottom: 20px; background: rgba(0,0,0,0.8); 
            padding: 10px 20px; border-radius: 8px; display: flex; gap: 15px; align-items: center; 
            border: 1px solid #444; z-index: 10;
        }
        select, button { padding: 8px; border-radius: 4px; border: none; cursor: pointer; }
        button { background: #0066cc; color: white; font-weight: bold; }
        button:hover { background: #0055aa; }
        #status { font-weight: bold; color: #ff9900; min-width: 100px; text-align: left; }
        .settings-container { padding: 20px; overflow-y: auto; width: 100%; text-align: center; }
        .mapping-table { margin: 0 auto; border-collapse: collapse; background: #222; width: 80%; max-width: 600px; }
        .mapping-table td, .mapping-table th { border: 1px solid #444; padding: 8px; text-align: left; }
        .map-btn { background: #444; color: white; padding: 4px 10px; width: 80px; }
        .map-btn.listening { background: #e67e22; animation: pulse 1s infinite; }
        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.6; } 100% { opacity: 1; } }
    </style>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
</head>
<body>
    <div class="main-layout">
        <div class="tab-nav">
            <button class="tab-btn active" onclick="switchTab('stream')">📺 Stream & Play</button>
            <button class="tab-btn" onclick="switchTab('settings')">⚙️ Controller Mapping</button>
        </div>

        <div id="tab-stream" class="tab-content active">
            <img id="usb-feed" src="/video_feed">
            <div class="controls-bar">
                <label>Player:</label>
                <select id="player-select">
                    <option value="1">Player 1</option>
                    <option value="2">Player 2</option>
                </select>
                <button id="audio-btn" onclick="startAudio()">🔊 Enable Audio</button>
                <span id="status">Waiting for Gamepad...</span>
            </div>
        </div>

        <div id="tab-settings" class="tab-content">
            <div class="settings-container">
                <h2>Controller Mapping</h2>
                <button onclick="resetDefaults()" style="margin-bottom: 15px; background: #c0392b;">Reset Defaults</button>
                <div style="display:flex; justify-content:center; gap:20px;">
                    <div>
                        <h3>Axes</h3>
                        <table class="mapping-table" id="axes-table"></table>
                    </div>
                    <div>
                        <h3>Buttons</h3>
                        <table class="mapping-table" id="buttons-table"></table>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
    const socket = io({ transports: ['websocket'] });
    
    // --- AUDIO HANDLING WITH JITTER BUFFER ---
    let audioContext;
    let nextStartTime = 0;
    // Must match Python RATE (48000)
    const SAMPLE_RATE = 48000; 

    function startAudio() {
        if (!audioContext) {
            audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: SAMPLE_RATE });
        }
        if (audioContext.state === 'suspended') {
            audioContext.resume();
        }
        document.getElementById('audio-btn').innerText = "🔊 Audio Active";
        document.getElementById('audio-btn').style.background = "#27ae60";
    }
    
    socket.on('audio_data', (data) => {
        if (!audioContext) return;

        // 1. Convert PCM 16-bit Int to Float32
        const int16 = new Int16Array(data);
        const f32 = new Float32Array(int16.length);
        for (let i = 0; i < int16.length; i++) {
            f32[i] = int16[i] / 32768.0;
        }

        // 2. Create Audio Buffer
        const buffer = audioContext.createBuffer(1, f32.length, SAMPLE_RATE);
        buffer.getChannelData(0).set(f32);
        const source = audioContext.createBufferSource();
        source.buffer = buffer;
        source.connect(audioContext.destination);

        // 3. Jitter Buffer Logic (Prevents Flicker)
        // If nextStartTime is in the past (underrun), reset it to now
        if (nextStartTime < audioContext.currentTime) {
            nextStartTime = audioContext.currentTime + 0.02; // Small 20ms buffer
        }
        
        // Schedule play
        source.start(nextStartTime);
        
        // Advance time for next packet
        nextStartTime += buffer.duration;
    });

    // --- GAMEPAD & CONTROLS ---
    let gamepadIndex = -1;
    let lastSentTime = 0;
    const SEND_INTERVAL = 16; 
    const DEADZONE = 0.15;
    const defaultAxes = { lx: 0, ly: 1, rx: 2, ry: 3 };
    const defaultButtons = {};
    for(let i=0; i<16; i++) defaultButtons[i] = i;

    let axisMap = JSON.parse(localStorage.getItem('axisMap')) || defaultAxes;
    let buttonMap = JSON.parse(localStorage.getItem('buttonMap')) || defaultButtons;

    function switchTab(t) {
        document.querySelectorAll('.tab-content').forEach(e => e.classList.remove('active'));
        document.querySelectorAll('.tab-btn').forEach(e => e.classList.remove('active'));
        document.getElementById('tab-'+t).classList.add('active');
        event.target.classList.add('active');
    }

    window.addEventListener("gamepadconnected", (e) => {
        gamepadIndex = e.gamepad.index;
        document.getElementById("status").innerText = "🎮 Connected";
        document.getElementById("status").style.color = "#00ff00";
        renderSettings();
        requestAnimationFrame(updateLoop);
    });

    window.addEventListener("gamepaddisconnected", () => {
        document.getElementById("status").innerText = "❌ Disconnected";
        document.getElementById("status").style.color = "red";
    });

    function normalizeAxis(val) {
        if (Math.abs(val) < DEADZONE) val = 0;
        else val = (val > 0) ? (val - DEADZONE) / (1 - DEADZONE) : (val + DEADZONE) / (1 - DEADZONE);
        return Math.max(0, Math.min(255, Math.floor((val + 1) * 127.5)));
    }

    function updateLoop() {
        const gp = navigator.getGamepads()[gamepadIndex];
        if (gp && !remapMode) {
            const now = Date.now();
            if (now - lastSentTime > SEND_INTERVAL) {
                const pid = parseInt(document.getElementById('player-select').value);
                let btns = 0;
                for(let i=0; i<16; i++) {
                    if(gp.buttons[buttonMap[i]]?.pressed) btns |= (1 << i);
                }
                const lx = normalizeAxis(gp.axes[axisMap.lx] || 0);
                const ly = normalizeAxis(gp.axes[axisMap.ly] || 0);
                const rx = normalizeAxis(gp.axes[axisMap.rx] || 0);
                const ry = normalizeAxis(gp.axes[axisMap.ry] || 0);

                socket.emit('input_data', { 
                    player: pid, buttons: btns, 
                    lx: lx, ly: ly, rx: rx, ry: ry 
                });
                lastSentTime = now;
            }
        }
        if (remapMode) checkRemapInput(gp);
        requestAnimationFrame(updateLoop);
    }

    let remapMode = null; 
    let baselineState = { axes: [], buttons: [] };
    const btnLabels = ["A", "B", "X", "Y", "L1", "R1", "L2", "R2", "Select", "Start", "L3", "R3", "Up", "Down", "Left", "Right"];

    function renderSettings() {
        let aHtml = `<tr><th>Axis</th><th>ID</th><th></th></tr>`;
        for (let k in axisMap) {
            aHtml += `<tr><td>${k.toUpperCase()}</td><td>${axisMap[k]}</td><td><button class="map-btn" onclick="startRemap('axis', '${k}', this)">Set</button></td></tr>`;
        }
        document.getElementById('axes-table').innerHTML = aHtml;
        let bHtml = `<tr><th>Button</th><th>ID</th><th></th></tr>`;
        for (let i = 0; i < 16; i++) {
            bHtml += `<tr><td>${btnLabels[i]}</td><td>${buttonMap[i]}</td><td><button class="map-btn" onclick="startRemap('btn', '${i}', this)">Set</button></td></tr>`;
        }
        document.getElementById('buttons-table').innerHTML = bHtml;
    }

    function startRemap(type, key, el) {
        const gp = navigator.getGamepads()[gamepadIndex];
        if (gp) {
            baselineState.axes = [...gp.axes];
            baselineState.buttons = gp.buttons.map(b => b.pressed);
        }
        remapMode = { type, key, el };
        el.innerText = "...";
        el.classList.add('listening');
    }

    function checkRemapInput(gp) {
        if (!remapMode || !gp) return;
        if (remapMode.type === 'btn') {
            gp.buttons.forEach((btn, idx) => {
                if (btn.pressed && !baselineState.buttons[idx]) {
                    buttonMap[remapMode.key] = idx;
                    finishRemap();
                }
            });
        } else {
            gp.axes.forEach((val, idx) => {
                if (Math.abs(val - (baselineState.axes[idx] || 0)) > 0.5) {
                    axisMap[remapMode.key] = idx;
                    finishRemap();
                }
            });
        }
    }

    function finishRemap() {
        localStorage.setItem('axisMap', JSON.stringify(axisMap));
        localStorage.setItem('buttonMap', JSON.stringify(buttonMap));
        remapMode.el.innerText = "Set";
        remapMode.el.classList.remove('listening');
        renderSettings();
        remapMode = null;
    }
    function resetDefaults() {
        axisMap = {...defaultAxes}; buttonMap = {...defaultButtons};
        localStorage.clear(); renderSettings();
    }
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_PAGE)

@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@socketio.on('input_data')
def handle_input(data):
    try:
        pid = int(data.get('player', 1))
        packet = struct.pack('<BHBBBBB', pid, data['buttons'], 8, data['lx'], data['ly'], data['rx'], data['ry'])
        sock.sendto(packet, (PICO_IP, PICO_PORT))
    except Exception:
        pass

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=8801, debug=False, allow_unsafe_werkzeug=True)
