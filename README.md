# BirdCam

A Raspberry Pi application for capturing, viewing, and timelapsing a **Tapo C120** IP camera over RTSP. Includes a live OpenCV viewer, a frame-capture scheduler, a timelapse contact-sheet generator, and a dark-themed web dashboard accessible from any browser on your local network.

---

## Table of contents

1. [Hardware & camera setup](#1-hardware--camera-setup)
2. [Raspberry Pi install](#2-raspberry-pi-install)
3. [Configuration](#3-configuration)
4. [Running the application](#4-running-the-application)
5. [Accessing the dashboard](#5-accessing-the-dashboard)
6. [Project structure](#6-project-structure)

---

## 1. Hardware & camera setup

### Find the camera's IP address

Two easy ways:

- **Tapo app** → tap your C120 → three-dot menu → *Device Info* → IP Address.
- **Router DHCP table** — log into your router (usually `192.168.1.1` or `192.168.0.1`) and look for "Tapo" or the device's MAC address in the client list.

### Enable RTSP on the Tapo C120

RTSP is **disabled by default**. Enable it before running BirdCam:

1. Open the **Tapo** app and select the C120.
2. Tap the settings gear → **Advanced Settings** → **Camera Account**.
3. Set a username and password (this is your `CAMERA_USER` / `CAMERA_PASS` — **not** your Tapo account credentials).
4. Tap **Save**.

The camera exposes two streams:

| URL | Resolution | Use |
|-----|-----------|-----|
| `rtsp://<user>:<pass>@<ip>/stream1` | Full HD (main stream) | Default — used by BirdCam |
| `rtsp://<user>:<pass>@<ip>/stream2` | Sub-stream (lower res) | Lower bandwidth, edit `config.py` to switch |

---

## 2. Raspberry Pi install

### System dependencies

OpenCV's Pi wheels depend on a few native libraries. Install them first:

```bash
sudo apt update
sudo apt install -y libopenblas-dev libhdf5-dev libhdf5-serial-dev
# Optional: for the OpenCV display window (not needed in --web / headless mode)
sudo apt install -y libgtk-3-dev
```

> **Note:** `libatlas-base-dev` was removed in Raspberry Pi OS Bookworm (2023).
> `libopenblas-dev` is its replacement and is already included above.

### Python dependencies

```bash
# Clone the repo
git clone https://github.com/fidge101/birdcam.git
cd birdcam

# Create a virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

`imageio` is optional — only needed for animated GIF output from `--timelapse`.

---

## 3. Configuration

Copy the example env file and edit it:

```bash
cp .env.example .env
nano .env
```

| Variable | Required | Description |
|----------|----------|-------------|
| `CAMERA_IP` | ✓ | IP address of the Tapo C120 |
| `CAMERA_USER` | ✓ | RTSP username (set in Tapo app Camera Account) |
| `CAMERA_PASS` | ✓ | RTSP password |
| `CAPTURE_INTERVAL_SECONDS` | ✓ | Seconds between captured frames (e.g. `60`) |
| `FRAME_STORE_DIR` | ✓ | Directory for saved frames (e.g. `./frames/`) |
| `MAX_FRAMES` | ✓ | Oldest frames deleted when this count is exceeded |
| `TIMELAPSE_OUTPUT_PATH` | ✓ | Output path for the contact-sheet JPEG |
| `PORT` | — | Web dashboard port (default `5000`) |
| `STREAM_QUALITY` | — | MJPEG JPEG quality 0–100 (default `80`) |

> **Security:** Never commit your `.env` file. It is listed in `.gitignore`.

---

## 4. Running the application

```bash
# Open a live OpenCV viewer window (requires a display)
python main.py --live

# Run the frame-capture scheduler only (headless friendly)
python main.py --capture

# Generate a timelapse contact sheet from saved frames (no camera needed)
python main.py --timelapse

# Start the web dashboard + capture scheduler
python main.py --web

# Live viewer + web dashboard + capture scheduler together
python main.py --all
```

Stop any mode with **Ctrl-C** — the camera is released cleanly.

---

## 5. Accessing the dashboard

When running `--web` or `--all`, the terminal prints:

```
Dashboard: http://raspberrypi.local:5000
```

Open that URL in any browser on the same Wi-Fi network. The dashboard shows:

- **Left panel** — live status badge, CPU temperature, uptime, manual capture button, config editor.
- **Centre panel** — live MJPEG feed (no plugins required), with scanline overlay and clock.
- **Right panel** — saved frame gallery with delete, timelapse generator with preview.
- **Bottom bar** — collapsible real-time log tail.

### Hostname not resolving?

If `raspberrypi.local` doesn't work, use the Pi's IP address directly:

```bash
# On the Pi:
hostname -I
# → 192.168.1.42
```

Then open `http://192.168.1.42:5000`.

### Remote access (outside local network)

The MJPEG stream requires no transcoding so it is CPU-light on the Pi. For
access from outside your local network, an SSH tunnel is the simplest option
(no port-forwarding or VPN setup required):

```bash
# On your remote machine:
ssh -L 5000:localhost:5000 pi@raspberrypi.local
# Then open http://localhost:5000 in your browser.
```

Full VPN or reverse-proxy setup is outside the scope of this project.

---

## 6. Project structure

```
BirdCam/
├── .env.example        # Template — copy to .env and fill in values
├── .gitignore
├── README.md
├── requirements.txt
│
├── config.py           # Loads settings from .env; single Config object
├── camera.py           # CameraStream: RTSP connect / read / reconnect
├── viewer.py           # Live OpenCV window viewer
├── scheduler.py        # Background frame-capture thread
├── timelapse.py        # Contact-sheet and GIF generator
├── main.py             # CLI entry point (--live / --capture / --timelapse / --web / --all)
│
├── frames/             # Captured frame JPEGs (gitignored)
│
└── web/
    ├── __init__.py
    ├── server.py       # Flask server: MJPEG stream, REST API, SSE log tail
    └── static/
        └── index.html  # Single-file dark dashboard (HTML + CSS + JS)
```
