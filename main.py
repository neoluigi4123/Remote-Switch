import socket
import struct
import cv2
import threading
import time
import pyaudio
from flask import Flask, render_template_string, Response
from flask_socketio import SocketIO

# --- CONFIGURATION ---
PICO_IP = "192.168.1.xxx" # The ip of the raspberry pi.
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
        .stream-container { 
            width: 100vw; 
            height: 100vh; 
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
        .controls { position: absolute; bottom: 0; width: 100%; padding: 10px; background: rgba(0,0,0,0.7); }
    </style>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
</head>
<body>
    <div class="stream-container">
        <img id="usb-feed" src="/video_feed">
        <div class="controls">
            <button onclick="startAudio()">ENABLE AUDIO</button>
            <div id="status">Connecting...</div>
            <div id="vals">LX:0 LY:0</div>
        </div>
    </div>

     <script>
    const socket = io();
    let audioContext;
    let nextStartTime = 0;
    let gamepadIndex = -1;

    // --- CONFIGURATION ---
    const DEADZONE = 0.15; // Ignore small movements (0.0 to 1.0)

    function startAudio() {
        if (!audioContext) {
            audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 44100 });
            console.log("Audio Context Started");
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
        requestAnimationFrame(updateLoop);
    });

    // --- IMPROVED NORMALIZATION ---
    function normalizeAxis(val) {
        // 1. Apply Deadzone
        if (Math.abs(val) < DEADZONE) val = 0;
        
        // 2. Rescale remaining range to be linear after deadzone
        // (This ensures that moving the stick slightly past the deadzone doesn't jump to 15%)
        if (val > 0) val = (val - DEADZONE) / (1 - DEADZONE);
        else if (val < 0) val = (val + DEADZONE) / (1 - DEADZONE);

        // 3. Map -1.0...1.0 to 0...255 (127 being center)
        let mapped = Math.floor(((val + 1) * 127.5));
        
        // 4. Clamp values to ensure they stay in 0-255 range
        return Math.max(0, Math.min(255, mapped));
    }

    function updateLoop() {
        const gp = navigator.getGamepads()[gamepadIndex];
        if (!gp) return;

        // Check for 'standard' mapping. If not standard, some controllers 
        // swap axes. You can log gp.axes to debug specific controllers.
        let rawLX = gp.axes[0];
        let rawLY = gp.axes[1];
        let rawRX = gp.axes[2] || 0; 
        let rawRY = gp.axes[3] || 0;

        let lx = normalizeAxis(rawLX);
        let ly = normalizeAxis(rawLY);
        let rx = normalizeAxis(rawRX);
        let ry = normalizeAxis(rawRY);

        // Handle Buttons (PSX/Switch have different button IDs than Xbox)
        let buttonsValue = 0;
        gp.buttons.forEach((btn, index) => {
            if (btn.pressed) buttonsValue |= (1 << index);
        });

        document.getElementById("vals").innerText = `LX:${lx} LY:${ly} RX:${rx} RY:${ry}`;
        
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
