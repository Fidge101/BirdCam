"""
web/server.py — Flask web server for the BirdCam dashboard.

Architecture notes:
    - MJPEG streaming uses multipart/x-mixed-replace.  Each frame is a
      JPEG boundary in a never-ending HTTP response.  Browsers render this
      as a continuously updated image natively — no WebRTC, no websockets,
      no JS decoder needed.  This is maximally compatible and CPU-light on
      the Pi because there is no transcoding step.

    - All API endpoints return JSON and are prefixed /api/.  CORS is
      enabled for all origins so the dashboard works from any device on
      the local network without browser security complaints.

    - The SSE log-tail endpoint (/api/logs/stream) streams new lines from
      the birdcam.log file as Server-Sent Events.  The browser EventSource
      API reconnects automatically if the connection drops.

    - Credentials are NEVER sent to the frontend.  /api/config masks the
      camera password with "***".
"""

import os
import time
import queue
import logging
import threading
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, Response, jsonify, request, send_file, abort
from flask_cors import CORS

from camera import CameraStream
from config import Config
from timelapse import generate_timelapse
from scheduler import _capture_frame  # reuse the save logic

logger = logging.getLogger(__name__)

# ── Module-level state shared between routes ──────────────────────────────────
_start_time = time.monotonic()
_frame_count = 0          # incremented by the /stream generator
_timelapse_running = False

# SSE log queue — the FileWatcher thread puts lines here; the SSE route reads them.
_log_queue: queue.Queue[str] = queue.Queue(maxsize=500)

# Pre-built "SIGNAL LOST" frame served when the camera is offline.
_OFFLINE_FRAME: np.ndarray | None = None


def _make_offline_frame() -> np.ndarray:
    """
    Build a 640×480 grey frame with "SIGNAL LOST" text.

    Built once at server startup and reused for every offline frame so
    we avoid re-drawing on every encode cycle.
    """
    frame = np.full((480, 640, 3), 40, dtype=np.uint8)
    font = cv2.FONT_HERSHEY_SIMPLEX
    text = "SIGNAL LOST"
    scale, thickness = 2.0, 3
    (tw, th), _ = cv2.getTextSize(text, font, scale, thickness)
    x = (640 - tw) // 2
    y = (480 + th) // 2
    cv2.putText(frame, text, (x, y), font, scale, (140, 140, 140), thickness, cv2.LINE_AA)
    return frame


def _encode_jpeg(frame: np.ndarray, quality: int) -> bytes:
    """Encode a numpy BGR frame as a JPEG byte string."""
    success, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not success:
        raise RuntimeError("JPEG encoding failed")
    return buf.tobytes()


def _cpu_temp() -> float | None:
    """
    Read the Pi CPU temperature from the thermal sysfs node.

    Returns degrees Celsius as a float, or None on non-Pi platforms.
    """
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as fh:
            return int(fh.read().strip()) / 1000.0
    except (OSError, ValueError):
        return None


class _LogWatcher(threading.Thread):
    """
    Background thread that tails birdcam.log and puts new lines into
    _log_queue so the SSE endpoint can forward them to the browser.
    """

    def __init__(self, log_path: str) -> None:
        super().__init__(daemon=True, name="LogWatcher")
        self._log_path = log_path

    def run(self) -> None:
        """Seek to end of file, then yield every new line."""
        try:
            with open(self._log_path, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(0, 2)  # seek to end
                while True:
                    line = fh.readline()
                    if line:
                        try:
                            _log_queue.put_nowait(line.rstrip("\n"))
                        except queue.Full:
                            pass  # drop oldest — the SSE consumer is too slow
                    else:
                        time.sleep(0.2)
        except FileNotFoundError:
            pass  # log file not created yet; thread exits silently


# ── Flask app factory ─────────────────────────────────────────────────────────

def create_app(camera_stream: CameraStream, config: Config) -> Flask:
    """
    Create and configure the Flask application.

    Using an app factory (rather than a module-level Flask instance) means
    the app can be instantiated in a thread from main.py with the correct
    camera and config objects injected — no globals needed.

    Args:
        camera_stream: The shared CameraStream instance.
        config:        Loaded application configuration.

    Returns:
        Configured Flask application (not yet running).
    """
    global _OFFLINE_FRAME
    _OFFLINE_FRAME = _make_offline_frame()

    # Start tailing the log file.
    _LogWatcher("birdcam.log").start()

    app = Flask(__name__, static_folder="static", static_url_path="/static")
    CORS(app, origins="*")

    # ── Helpers local to this factory (capture config in closure) ─────────────

    def _frame_files() -> list[str]:
        """Return sorted list of JPEG filenames in the frame store dir."""
        try:
            return sorted(
                f for f in os.listdir(config.frame_store_dir) if f.endswith(".jpg")
            )
        except FileNotFoundError:
            return []

    # ── Routes ────────────────────────────────────────────────────────────────

    @app.route("/")
    def index():
        """Serve the single-page dashboard."""
        return send_file(Path(__file__).parent / "static" / "index.html")

    # ── MJPEG stream ──────────────────────────────────────────────────────────

    @app.route("/stream")
    def stream():
        """
        MJPEG video stream endpoint.

        Yields individual JPEG frames as a multipart HTTP response.
        The browser treats the <img src="/stream"> tag as a continuously
        refreshing image without any JavaScript involvement.

        Frame failures (camera offline) substitute the pre-built offline
        frame so the stream never terminates from the client's perspective.
        """
        global _frame_count

        def generate():
            global _frame_count
            offline = _OFFLINE_FRAME
            while True:
                frame = camera_stream.read_frame()
                if frame is None:
                    frame = offline

                try:
                    jpeg = _encode_jpeg(frame, config.stream_quality)
                except RuntimeError:
                    time.sleep(0.1)
                    continue

                _frame_count += 1
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n"
                    + jpeg
                    + b"\r\n"
                )

        return Response(
            generate(),
            mimetype="multipart/x-mixed-replace; boundary=frame",
        )

    # ── API: status ───────────────────────────────────────────────────────────

    @app.route("/api/status")
    def api_status():
        """
        Return current system status.

        Response fields:
            connected    — bool, is the RTSP stream open
            uptime_s     — int, seconds since the server started
            frame_count  — int, total frames served by the stream endpoint
            cpu_temp_c   — float|null, Pi CPU temperature in Celsius
            saved_frames — int, number of JPEG files in the frame store
        """
        return jsonify({
            "connected": camera_stream.is_connected,
            "uptime_s": int(time.monotonic() - _start_time),
            "frame_count": _frame_count,
            "cpu_temp_c": _cpu_temp(),
            "saved_frames": len(_frame_files()),
        })

    # ── API: frames ───────────────────────────────────────────────────────────

    @app.route("/api/frames")
    def api_frames_list():
        """
        Return metadata for all saved frames, newest first.

        Each item: {filename, timestamp (ISO-8601), size_kb}
        """
        result = []
        for name in reversed(_frame_files()):
            path = os.path.join(config.frame_store_dir, name)
            try:
                stat = os.stat(path)
                # Filename: YYYYMMDD_HHMMSS.jpg → parse to ISO string.
                stem = name.replace(".jpg", "")
                dt = datetime.strptime(stem, "%Y%m%d_%H%M%S")
                result.append({
                    "filename": name,
                    "timestamp": dt.isoformat(),
                    "size_kb": round(stat.st_size / 1024, 1),
                })
            except (ValueError, OSError):
                continue
        return jsonify(result)

    @app.route("/api/frames/<name>")
    def api_frame_get(name: str):
        """Serve a specific saved frame JPEG by filename."""
        # Sanitise — prevent path traversal.
        if not name.endswith(".jpg") or "/" in name or ".." in name:
            abort(400)
        path = os.path.join(config.frame_store_dir, name)
        if not os.path.isfile(path):
            abort(404)
        return send_file(path, mimetype="image/jpeg")

    @app.route("/api/frames/<name>", methods=["DELETE"])
    def api_frame_delete(name: str):
        """Delete a specific saved frame."""
        if not name.endswith(".jpg") or "/" in name or ".." in name:
            abort(400)
        path = os.path.join(config.frame_store_dir, name)
        if not os.path.isfile(path):
            abort(404)
        os.remove(path)
        logger.info("Frame deleted via API: %s", name)
        return jsonify({"deleted": name})

    # ── API: manual capture ───────────────────────────────────────────────────

    @app.route("/api/capture", methods=["POST"])
    def api_capture():
        """
        Trigger an immediate frame capture outside the normal schedule.

        This reuses the same save logic as the scheduler so filenames and
        pruning behave identically.
        """
        _capture_frame(camera_stream, config)
        frames = _frame_files()
        return jsonify({"ok": True, "saved_frames": len(frames)})

    # ── API: timelapse ────────────────────────────────────────────────────────

    @app.route("/api/timelapse/preview")
    def api_timelapse_preview():
        """Serve the current timelapse JPEG if one has been generated."""
        path = config.timelapse_output_path
        if not os.path.isfile(path):
            abort(404)
        return send_file(path, mimetype="image/jpeg")

    @app.route("/api/timelapse/generate", methods=["POST"])
    def api_timelapse_generate():
        """
        Kick off timelapse generation in a background thread.

        Returns immediately with {status: "started"} or {status: "already_running"}.
        The client can poll /api/timelapse/preview to detect completion.
        """
        global _timelapse_running

        if _timelapse_running:
            return jsonify({"status": "already_running"})

        def _run():
            global _timelapse_running
            _timelapse_running = True
            try:
                generate_timelapse(
                    config.frame_store_dir, config.timelapse_output_path
                )
            finally:
                _timelapse_running = False

        threading.Thread(target=_run, daemon=True, name="TimelapseGenerator").start()
        return jsonify({"status": "started"})

    # ── API: config ───────────────────────────────────────────────────────────

    @app.route("/api/config")
    def api_config_get():
        """
        Return current configuration values.

        The camera password is ALWAYS masked as "***" — it must never be
        sent to the browser.
        """
        return jsonify({
            "camera_ip": config.camera_ip,
            "camera_user": config.camera_user,
            "camera_pass": "***",
            "capture_interval_seconds": config.capture_interval_seconds,
            "frame_store_dir": config.frame_store_dir,
            "max_frames": config.max_frames,
            "timelapse_output_path": config.timelapse_output_path,
            "port": config.port,
            "stream_quality": config.stream_quality,
        })

    @app.route("/api/config", methods=["POST"])
    def api_config_post():
        """
        Update CAPTURE_INTERVAL_SECONDS and MAX_FRAMES in the .env file.

        Only these two fields are writable via the API to limit the attack
        surface.  Camera credentials cannot be changed through the dashboard.

        Writes the updated values back to .env so they persist across restarts.
        Note: the in-memory config object is NOT mutated (it's frozen); the
        scheduler will pick up new values on next restart.
        """
        data = request.get_json(force=True, silent=True) or {}
        updates: dict[str, str] = {}

        if "capture_interval_seconds" in data:
            val = int(data["capture_interval_seconds"])
            if val < 1:
                return jsonify({"error": "capture_interval_seconds must be >= 1"}), 400
            updates["CAPTURE_INTERVAL_SECONDS"] = str(val)

        if "max_frames" in data:
            val = int(data["max_frames"])
            if val < 1:
                return jsonify({"error": "max_frames must be >= 1"}), 400
            updates["MAX_FRAMES"] = str(val)

        if not updates:
            return jsonify({"error": "No valid fields provided"}), 400

        _write_env_updates(updates)
        logger.info("Config updated via API: %s", updates)
        return jsonify({"ok": True, "updated": updates})

    # ── API: log stream (SSE) ─────────────────────────────────────────────────

    @app.route("/api/logs/stream")
    def api_logs_stream():
        """
        Server-Sent Events endpoint that tails the birdcam.log file.

        The browser EventSource API connects here and receives new log lines
        in real time.  The connection stays open until the client disconnects.
        Each event is: data: <log line>\\n\\n
        """
        def generate():
            while True:
                try:
                    line = _log_queue.get(timeout=15)
                    yield f"data: {line}\n\n"
                except queue.Empty:
                    # Send a keep-alive comment so the connection doesn't time out.
                    yield ": keep-alive\n\n"

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",   # disable Nginx buffering if proxied
            },
        )

    return app


# ── .env writer ───────────────────────────────────────────────────────────────

def _write_env_updates(updates: dict[str, str]) -> None:
    """
    Write key=value pairs into the .env file.

    If the key already exists its line is replaced; if not, it is appended.
    This is intentionally minimal — it only touches the specific keys in
    `updates` and leaves everything else untouched.
    """
    env_path = ".env"
    lines: list[str] = []

    if os.path.isfile(env_path):
        with open(env_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()

    written = set()
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}\n")
            written.add(key)
        else:
            new_lines.append(line)

    for key, value in updates.items():
        if key not in written:
            new_lines.append(f"{key}={value}\n")

    with open(env_path, "w", encoding="utf-8") as fh:
        fh.writelines(new_lines)
