import socket
import struct
import cv2
import threading
import time
import pyaudio
from flask import Flask, render_template_string, Response
from flask_socketio import SocketIO

# --- CONFIGURATION ---
PICO_IP = "192.168.1.xxx"
PICO_PORT = 4210

STREAM_WIDTH = 640
STREAM_HEIGHT = 360
JPEG_QUALITY = 50

# Audio Config
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 44100

# --- CAMERA SELECTION ---
def list_cameras():
    available_indices = []
    for i in range(3):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if cap.isOpened():
            available_indices.append(i)
            cap.release()
    return available_indices

print(f"Available cameras: {list_cameras()}")
selected_cam = int(input("Enter index for USB Camera: "))

# --- THREADED CAMERA CLASS ---
class VideoStreamer:
    def __init__(self, src):
        self.cap = cv2.VideoCapture(src, cv2.CAP_DSHOW)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, STREAM_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, STREAM_HEIGHT)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        
        self.ret, self.frame = self.cap.read()
        self.running = True
        self.thread = threading.Thread(target=self.update, daemon=True)
        self.thread.start()

    def update(self):
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                self.frame = cv2.resize(frame, (STREAM_WIDTH, STREAM_HEIGHT))
            time.sleep(0.01)

    def get_frame(self):
        ret, buffer = cv2.imencode('.jpg', self.frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        return buffer.tobytes()

# --- AUDIO STREAMER CLASS ---
class AudioStreamer:
    def __init__(self, sio):
        self.sio = sio
        self.p = pyaudio.PyAudio()
        self.stream = self.p.open(format=FORMAT, channels=CHANNELS, rate=RATE, 
                                  input=True, frames_per_buffer=CHUNK)
        self.running = True
        self.thread = threading.Thread(target=self.stream_audio, daemon=True)
        self.thread.start()

    def stream_audio(self):
        while self.running:
            try:
                data = self.stream.read(CHUNK, exception_on_overflow=False)
                self.sio.emit('audio_data', data)
            except Exception as e:
                print(f"Audio error: {e}")
            time.sleep(0.01)

# Initialize
app = Flask(__name__)
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins='*')
streamer = VideoStreamer(selected_cam)
audio_streamer = AudioStreamer(socketio)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def generate_frames():
    while True:
        frame = streamer.get_frame()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        time.sleep(0.03)

HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>16:9 Low Latency Stream</title>
    <style>
        body { font-family: sans-serif; background: #000; color: #fff; text-align: center; margin: 0; overflow: hidden; }
        
        /* --- LAYOUT & TABS --- */
        .main-layout { display: flex; flex-direction: column; height: 100vh; width: 100vw; }
        .tab-nav {
            display: flex;
            background: #222;
            border-bottom: 1px solid #444;
            height: 40px;
            flex-shrink: 0;
        }
        .tab-btn {
            flex: 1;
            background: transparent;
            color: #aaa;
            border: none;
            cursor: pointer;
            font-size: 14px;
            font-weight: bold;
            transition: 0.3s;
        }
        .tab-btn:hover { background: #333; color: #fff; }
        .tab-btn.active { background: #444; color: #fff; border-bottom: 2px solid #0f0; }

        .tab-content { flex: 1; position: relative; display: none; overflow: hidden; }
        .tab-content.active { display: flex; }

        /* --- STREAM VIEW --- */
        .stream-container { 
            width: 100%; 
            height: 100%; 
            display: flex; 
            flex-direction: column;
            align-items: center; 
            justify-content: center; 
            background: #111; 
        }
        #usb-feed { 
            width: 100%; 
            max-width: 1280px; 
            aspect-ratio: 16 / 9; 
            object-fit: contain;
            background: #000;
        }
        .controls { position: absolute; bottom: 0; width: 100%; padding: 10px; background: rgba(0,0,0,0.7); pointer-events: none; }
        .controls button { pointer-events: auto; cursor: pointer; padding: 8px 16px; background: #333; color: #fff; border: 1px solid #555; }

        /* --- SETTINGS VIEW --- */
        .settings-container {
            width: 100%;
            height: 100%;
            background: #111;
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 20px;
            overflow-y: auto; box-sizing: border-box;
        }
        h2 { margin-top: 0; color: #ddd; }
        .mapping-grid {
            display: flex;
            gap: 20px;
            flex-wrap: wrap;
            justify-content: center;
            width: 100%;
            max-width: 1000px;
        }
        .map-column { flex: 1; min-width: 300px; }
        .mapping-table {
            border-collapse: collapse;
            width: 100%;
            background: #222;
            font-size: 13px;
        }
        .mapping-table th, .mapping-table td {
            border: 1px solid #444;
            padding: 8px;
            text-align: left;
        }
        .mapping-table th { background: #333; }
        .map-btn {
            background: #0066cc;
            color: white;
            border: none;
            padding: 4px 10px;
            cursor: pointer;
            width: 80px;
            border-radius: 3px;
        }
        .map-btn.listening { background: #cc6600; animation: pulse 0.8s infinite; }
        .reset-btn { margin-top: 20px; padding: 12px 24px; background: #c00; color: white; border: none; cursor: pointer; font-size: 14px; border-radius: 4px; }
        
        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.5; } 100% { opacity: 1; } }
    </style>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js  "></script>
</head>
<body>
    <div class="main-layout">
        <!-- Navigation -->
        <div class="tab-nav">
            <button id="btn-tab-stream" class="tab-btn active" onclick="switchTab('stream')">📺 Stream</button>
            <button id="btn-tab-settings" class="tab-btn" onclick="switchTab('settings')">⚙️ Controller Mapping</button>
        </div>

        <!-- Tab 1: Video Stream -->
        <div id="tab-stream" class="tab-content active">
            <div class="stream-container">
                <img id="usb-feed" src="/video_feed">
                <div class="controls">
                    <button onclick="startAudio()">ENABLE AUDIO</button>
                    <div id="status">Connecting...</div>
                    <div id="vals">Waiting for input...</div>
                </div>
            </div>
        </div>

        <!-- Tab 2: Settings -->
        <div id="tab-settings" class="tab-content">
            <div class="settings-container">
                <h2>Controller Configuration</h2>
                <p style="color: #aaa; margin-bottom: 20px;">Click "Remap" then <b>move the stick/press button</b> you want to assign.</p>
                
                <div class="mapping-grid">
                    <!-- Axes Column -->
                    <div class="map-column">
                        <h3>Axes (Sticks/Wheel)</h3>
                        <table class="mapping-table" id="axes-table">
                            <!-- JS Generated -->
                        </table>
                    </div>

                    <!-- Buttons Column -->
                    <div class="map-column">
                        <h3>Buttons</h3>
                        <table class="mapping-table" id="buttons-table">
                            <!-- JS Generated -->
                        </table>
                    </div>
                </div>
                
                <button class="reset-btn" onclick="resetDefaults()">Reset to Defaults</button>
            </div>
        </div>
    </div>

    <script>
    const socket = io();
    let audioContext;
    let nextStartTime = 0;
    let gamepadIndex = -1;

    // --- MAPPING CONFIGURATION ---
    const DEADZONE = 0.15; 
    
    const defaultAxes = { lx: 0, ly: 1, rx: 2, ry: 3 };
    const defaultButtons = {};
    for(let i=0; i<16; i++) defaultButtons[i] = i;

    let axisMap = Object.assign({}, defaultAxes);
    let buttonMap = Object.assign({}, defaultButtons);

    if(localStorage.getItem('axisMap')) {
        try { axisMap = JSON.parse(localStorage.getItem('axisMap')); } catch(e){}
    }
    if(localStorage.getItem('buttonMap')) {
        try { buttonMap = JSON.parse(localStorage.getItem('buttonMap')); } catch(e){}
    }

    // --- UI & TAB LOGIC ---
    function switchTab(tab) {
        document.querySelectorAll('.tab-content').forEach(e => e.classList.remove('active'));
        document.querySelectorAll('.tab-btn').forEach(e => e.classList.remove('active'));
        document.getElementById('tab-' + tab).classList.add('active');
        document.getElementById('btn-tab-' + tab).classList.add('active');
    }

    const axesLabels = { lx: "Left Stick X / Wheel", ly: "Left Stick Y / Gas", rx: "Right Stick X", ry: "Right Stick Y" };
    const btnLabels = ["A / X", "B / Circle", "X / Square", "Y / Triangle", "L1 / LB", "R1 / RB", "L2 / LT", "R2 / RT", "Select / Back", "Start", "L3", "R3", "D-Up", "D-Down", "D-Left", "D-Right"];

    function renderSettings() {
        let aHtml = `<tr><th>Function</th><th>Phy ID</th><th>Action</th></tr>`;
        for (let key in axisMap) {
            aHtml += `<tr>
                <td>${axesLabels[key] || key}</td>
                <td id="disp-axis-${key}">${axisMap[key]}</td>
                <td><button class="map-btn" onclick="startRemap('axis', '${key}', this)">Remap</button></td>
            </tr>`;
        }
        document.getElementById('axes-table').innerHTML = aHtml;

        let bHtml = `<tr><th>Bit / Label</th><th>Phy ID</th><th>Action</th></tr>`;
        for (let i = 0; i < 16; i++) {
            bHtml += `<tr>
                <td>Bit ${i} <span style="color:#888">(${btnLabels[i] || '?'})</span></td>
                <td id="disp-btn-${i}">${buttonMap[i]}</td>
                <td><button class="map-btn" onclick="startRemap('btn', '${i}', this)">Remap</button></td>
            </tr>`;
        }
        document.getElementById('buttons-table').innerHTML = bHtml;
    }

    // --- REMAPPING LOGIC (FIXED FOR WHEELS) ---
    let remapMode = null; 
    let baselineState = { axes: [], buttons: [] };

    function startRemap(type, key, el) {
        // 1. Snapshot the CURRENT state of the controller
        const gp = navigator.getGamepads()[gamepadIndex];
        if (gp) {
            baselineState.axes = gp.axes.slice(); // Copy array
            baselineState.buttons = gp.buttons.map(b => b.pressed);
        } else {
            // If no controller, assume zeros
            baselineState.axes = new Array(10).fill(0);
            baselineState.buttons = new Array(20).fill(false);
        }

        remapMode = { type, key, el };
        el.innerText = "Move...";
        el.classList.add('listening');
    }

    function checkRemapInput(gp) {
        if (!remapMode) return;

        // THRESHOLD: How much must a value CHANGE from the baseline to be registered?
        const AXIS_THRESHOLD = 0.5;

        if (remapMode.type === 'btn') {
            gp.buttons.forEach((btn, idx) => {
                // Only register if currently pressed AND wasn't pressed when we started remapping
                if (btn.pressed && !baselineState.buttons[idx]) {
                    buttonMap[remapMode.key] = idx;
                    finishRemap();
                }
            });
        }
        else if (remapMode.type === 'axis') {
            gp.axes.forEach((val, idx) => {
                // Calculate difference from the start state
                // This ignores pedals resting at -1.0, because -1.0 - (-1.0) = 0.0
                let delta = Math.abs(val - (baselineState.axes[idx] || 0));
                
                if (delta > AXIS_THRESHOLD) {
                    axisMap[remapMode.key] = idx;
                    finishRemap();
                }
            });
        }
    }

    function finishRemap() {
        localStorage.setItem('axisMap', JSON.stringify(axisMap));
        localStorage.setItem('buttonMap', JSON.stringify(buttonMap));
        
        remapMode.el.innerText = "Remap";
        remapMode.el.classList.remove('listening');
        
        if(remapMode.type === 'axis') document.getElementById(`disp-axis-${remapMode.key}`).innerText = axisMap[remapMode.key];
        else document.getElementById(`disp-btn-${remapMode.key}`).innerText = buttonMap[remapMode.key];

        remapMode = null;
    }

    function resetDefaults() {
        axisMap = Object.assign({}, defaultAxes);
        buttonMap = Object.assign({}, defaultButtons);
        localStorage.removeItem('axisMap');
        localStorage.removeItem('buttonMap');
        renderSettings();
    }

    // --- EXISTING FUNCTIONS ---

    function startAudio() {
        if (!audioContext) {
            audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 44100 });
        }
    }

    socket.on('audio_data', (data) => {
        if (!audioContext) return;
        const int16Array = new Int16Array(data);
        const float32Array = new Float32Array(int16Array.length);
        for (let i = 0; i < int16Array.length; i++) {
            float32Array[i] = int16Array[i] / 32768.0;
        }
        const buffer = audioContext.createBuffer(1, float32Array.length, 44100);
        buffer.getChannelData(0).set(float32Array);
        const source = audioContext.createBufferSource();
        source.buffer = buffer;
        source.connect(audioContext.destination);
        const currentTime = audioContext.currentTime;
        if (nextStartTime < currentTime) nextStartTime = currentTime;
        source.start(nextStartTime);
        nextStartTime += buffer.duration;
    });

    window.addEventListener("gamepadconnected", (e) => {
        gamepadIndex = e.gamepad.index;
        document.getElementById("status").innerText = "🎮 " + e.gamepad.id;
        renderSettings(); 
        requestAnimationFrame(updateLoop);
    });

    function normalizeAxis(val) {
        if (Math.abs(val) < DEADZONE) val = 0;
        if (val > 0) val = (val - DEADZONE) / (1 - DEADZONE);
        else if (val < 0) val = (val + DEADZONE) / (1 - DEADZONE);
        let mapped = Math.floor(((val + 1) * 127.5));
        return Math.max(0, Math.min(255, mapped));
    }

    function updateLoop() {
        const gp = navigator.getGamepads()[gamepadIndex];
        if (!gp) return;

        if (remapMode) {
            checkRemapInput(gp);
            requestAnimationFrame(updateLoop);
            return; 
        }

        // Use remapped axes
        let rawLX = gp.axes[axisMap.lx] || 0;
        let rawLY = gp.axes[axisMap.ly] || 0;
        let rawRX = gp.axes[axisMap.rx] || 0; 
        let rawRY = gp.axes[axisMap.ry] || 0;

        let lx = normalizeAxis(rawLX);
        let ly = normalizeAxis(rawLY);
        let rx = normalizeAxis(rawRX);
        let ry = normalizeAxis(rawRY);

        // Handle Buttons using remapped button map
        let buttonsValue = 0;
        for(let bit = 0; bit < 16; bit++) {
            let physicalIndex = buttonMap[bit];
            if (gp.buttons[physicalIndex] && gp.buttons[physicalIndex].pressed) {
                buttonsValue |= (1 << bit);
            }
        }

        // Display current values
        document.getElementById("vals").innerText = `LX:${lx} LY:${ly} RX:${rx} RY:${ry}`;

        // Emit data in the format of the second script
        socket.emit('input_data', { 
            buttons: buttonsValue, 
            lx: lx, 
            ly: ly, 
            rx: rx, 
            ry: ry 
        });

        requestAnimationFrame(updateLoop);
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
        packet = struct.pack('<HBBBBB', data['buttons'], 8, data['lx'], data['ly'], data['rx'], data['ry'])
        sock.sendto(packet, (PICO_IP, PICO_PORT))
    except: pass

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=8800, debug=False, use_reloader=False)
