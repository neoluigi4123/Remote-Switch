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
JPEG_QUALITY = 65

# Audio Config
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 44100

# --- CAMERA SELECTION ---
def list_cameras():
    available_indices = []
    for i in range(3):
        # CAP_DSHOW is Windows specific. Use CAP_V4L2 on Linux/Pi if needed.
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW) 
        if cap.isOpened():
            available_indices.append(i)
            cap.release()
    return available_indices

print(f"Available cameras: {list_cameras()}")
try:
    selected_cam = int(input("Enter index for USB Camera: "))
except ValueError:
    selected_cam = 0

# --- THREADED CAMERA CLASS (OPTIMIZED) ---
class VideoStreamer:
    def __init__(self, src):
        self.cap = cv2.VideoCapture(src, cv2.CAP_DSHOW)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, STREAM_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, STREAM_HEIGHT)
        # Request 60 FPS from hardware if possible
        self.cap.set(cv2.CAP_PROP_FPS, 60)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        
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
                # Resize
                frame = cv2.resize(frame, (STREAM_WIDTH, STREAM_HEIGHT))
                # Encode once here to save processing time in the HTTP thread
                success, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                if success:
                    with self.lock:
                        self.jpeg_frame = buffer.tobytes()
                    # Signal that a new frame is ready
                    self.frame_event.set()
            else:
                # If read fails, sleep briefly to prevent CPU spin
                time.sleep(0.01)

    def get_frame(self):
        # Wait for a new frame to be available (Timeout prevents hanging if cam dies)
        if self.frame_event.wait(timeout=1.0):
            with self.lock:
                self.frame_event.clear() # Reset event
                return self.jpeg_frame
        return None

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
                # Read is blocking, so we don't need time.sleep here
                data = self.stream.read(CHUNK, exception_on_overflow=False)
                self.sio.emit('audio_data', data)
            except Exception as e:
                print(f"Audio error: {e}")
                time.sleep(0.1)

# Initialize Flask
app = Flask(__name__)
# threading mode is used here. 
# For production/even higher performance, consider 'eventlet' + gunicorn.
socketio = SocketIO(app, 
                    async_mode='threading', 
                    cors_allowed_origins='*',
                    max_http_buffer_size=1000000)

streamer = VideoStreamer(selected_cam)
audio_streamer = AudioStreamer(socketio)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def generate_frames():
    while True:
        # This now blocks until the camera actually has a new frame
        # removing the need for manual sleep() and syncing perfectly with hardware.
        frame = streamer.get_frame()
        if frame:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>16:9 High FPS Stream</title>
    <style>
        body { font-family: sans-serif; background: #000; color: #fff; text-align: center; margin: 0; overflow: hidden; }
        .main-layout { display: flex; flex-direction: column; height: 100vh; width: 100vw; }
        .tab-nav { display: flex; background: #222; border-bottom: 1px solid #444; height: 40px; flex-shrink: 0; }
        .tab-btn { flex: 1; background: transparent; color: #aaa; border: none; cursor: pointer; font-size: 14px; font-weight: bold; transition: 0.3s; }
        .tab-btn:hover { background: #333; color: #fff; }
        .tab-btn.active { background: #444; color: #fff; border-bottom: 2px solid #0f0; }
        .tab-content { flex: 1; position: relative; display: none; overflow: hidden; }
        .tab-content.active { display: flex; }
        .stream-container { width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; background: #111; }
        #usb-feed { width: 100%; max-width: 1280px; aspect-ratio: 16 / 9; object-fit: contain; background: #000; }
        .controls { position: absolute; bottom: 0; width: 100%; padding: 10px; background: rgba(0,0,0,0.7); pointer-events: none; }
        .controls button { pointer-events: auto; cursor: pointer; padding: 8px 16px; background: #333; color: #fff; border: 1px solid #555; }
        .settings-container { width: 100%; height: 100%; background: #111; display: flex; flex-direction: column; align-items: center; padding: 20px; overflow-y: auto; box-sizing: border-box; }
        .mapping-grid { display: flex; gap: 20px; flex-wrap: wrap; justify-content: center; width: 100%; max-width: 1000px; }
        .map-column { flex: 1; min-width: 300px; }
        .mapping-table { border-collapse: collapse; width: 100%; background: #222; font-size: 13px; }
        .mapping-table th, .mapping-table td { border: 1px solid #444; padding: 8px; text-align: left; }
        .map-btn { background: #0066cc; color: white; border: none; padding: 4px 10px; cursor: pointer; width: 80px; border-radius: 3px; }
        .map-btn.listening { background: #cc6600; animation: pulse 0.8s infinite; }
        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.5; } 100% { opacity: 1; } }
    </style>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
</head>
<body>
    <div class="main-layout">
        <div class="tab-nav">
            <button id="btn-tab-stream" class="tab-btn active" onclick="switchTab('stream')">📺 Stream</button>
            <button id="btn-tab-settings" class="tab-btn" onclick="switchTab('settings')">⚙️ Controller Mapping</button>
        </div>

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

        <div id="tab-settings" class="tab-content">
            <div class="settings-container">
                <h2>Controller Configuration</h2>
                <div class="mapping-grid">
                    <div class="map-column">
                        <h3>Axes</h3>
                        <table class="mapping-table" id="axes-table"></table>
                    </div>
                    <div class="map-column">
                        <h3>Buttons</h3>
                        <table class="mapping-table" id="buttons-table"></table>
                    </div>
                </div>
                <button style="margin-top:20px; padding:10px;" onclick="resetDefaults()">Reset to Defaults</button>
            </div>
        </div>
    </div>

    <script>
    const socket = io({ transports: ['websocket'], upgrade: false });
    
    let audioContext;
    let nextStartTime = 0;
    let gamepadIndex = -1;
    let lastSentTime = 0;
    const SEND_INTERVAL = 33; 

    const DEADZONE = 0.15; 
    const defaultAxes = { lx: 0, ly: 1, rx: 2, ry: 3 };
    const defaultButtons = {};
    for(let i=0; i<16; i++) defaultButtons[i] = i;

    let axisMap = JSON.parse(localStorage.getItem('axisMap')) || defaultAxes;
    let buttonMap = JSON.parse(localStorage.getItem('buttonMap')) || defaultButtons;

    function switchTab(tab) {
        document.querySelectorAll('.tab-content').forEach(e => e.classList.remove('active'));
        document.querySelectorAll('.tab-btn').forEach(e => e.classList.remove('active'));
        document.getElementById('tab-' + tab).classList.add('active');
        document.getElementById('btn-tab-' + tab).classList.add('active');
    }

    const axesLabels = { lx: "Left X / Wheel", ly: "Left Y / Gas", rx: "Right X", ry: "Right Y" };
    const btnLabels = ["A", "B", "X", "Y", "L1", "R1", "L2", "R2", "Select", "Start", "L3", "R3", "Up", "Down", "Left", "Right"];

    function renderSettings() {
        let aHtml = `<tr><th>Function</th><th>ID</th><th>Action</th></tr>`;
        for (let key in axisMap) {
            aHtml += `<tr><td>${axesLabels[key]}</td><td id="disp-axis-${key}">${axisMap[key]}</td><td><button class="map-btn" onclick="startRemap('axis', '${key}', this)">Remap</button></td></tr>`;
        }
        document.getElementById('axes-table').innerHTML = aHtml;

        let bHtml = `<tr><th>Label</th><th>ID</th><th>Action</th></tr>`;
        for (let i = 0; i < 16; i++) {
            bHtml += `<tr><td>${btnLabels[i]}</td><td id="disp-btn-${i}">${buttonMap[i]}</td><td><button class="map-btn" onclick="startRemap('btn', '${i}', this)">Remap</button></td></tr>`;
        }
        document.getElementById('buttons-table').innerHTML = bHtml;
    }

    let remapMode = null; 
    let baselineState = { axes: [], buttons: [] };

    function startRemap(type, key, el) {
        const gp = navigator.getGamepads()[gamepadIndex];
        if (gp) {
            baselineState.axes = [...gp.axes];
            baselineState.buttons = gp.buttons.map(b => b.pressed);
        }
        remapMode = { type, key, el };
        el.innerText = "Move...";
        el.classList.add('listening');
    }

    function checkRemapInput(gp) {
        if (!remapMode) return;
        
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
        remapMode.el.innerText = "Remap";
        remapMode.el.classList.remove('listening');
        renderSettings();
        remapMode = null;
    }

    function resetDefaults() {
        axisMap = {...defaultAxes}; buttonMap = {...defaultButtons};
        localStorage.clear(); renderSettings();
    }

    function startAudio() {
        if (!audioContext) audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 44100 });
    }

    socket.on('audio_data', (data) => {
        if (!audioContext) return;
        const int16 = new Int16Array(data);
        const f32 = new Float32Array(int16.length);
        for (let i = 0; i < int16.length; i++) f32[i] = int16[i] / 32768.0;
        const buffer = audioContext.createBuffer(1, f32.length, 44100);
        buffer.getChannelData(0).set(f32);
        const source = audioContext.createBufferSource();
        source.buffer = buffer; source.connect(audioContext.destination);
        const now = audioContext.currentTime;
        if (nextStartTime < now) nextStartTime = now;
        source.start(nextStartTime);
        nextStartTime += buffer.duration;
    });

    window.addEventListener("gamepadconnected", (e) => {
        gamepadIndex = e.gamepad.index;
        document.getElementById("status").innerText = "🎮 Connected";
        renderSettings();
        requestAnimationFrame(updateLoop);
    });

    function normalizeAxis(val) {
        if (Math.abs(val) < DEADZONE) val = 0;
        else val = (val > 0) ? (val - DEADZONE) / (1 - DEADZONE) : (val + DEADZONE) / (1 - DEADZONE);
        return Math.max(0, Math.min(255, Math.floor((val + 1) * 127.5)));
    }

    function updateLoop() {
        const gp = navigator.getGamepads()[gamepadIndex];
        if (!gp) return;

        if (remapMode) {
            checkRemapInput(gp);
        } else {
            const now = Date.now();
            if (now - lastSentTime > SEND_INTERVAL) {
                let lx = normalizeAxis(gp.axes[axisMap.lx] || 0);
                let ly = normalizeAxis(gp.axes[axisMap.ly] || 0);
                let rx = normalizeAxis(gp.axes[axisMap.rx] || 0);
                let ry = normalizeAxis(gp.axes[axisMap.ry] || 0);

                let btns = 0;
                for(let i=0; i<16; i++) {
                    if(gp.buttons[buttonMap[i]]?.pressed) btns |= (1 << i);
                }

                document.getElementById("vals").innerText = `LX:${lx} LY:${ly} RX:${rx} RY:${ry}`;
                socket.emit('input_data', { buttons: btns, lx: lx, ly: ly, rx: rx, ry: ry });
                lastSentTime = now;
            }
        }
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
    except Exception:
        pass

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=8801, debug=False, use_reloader=False)
