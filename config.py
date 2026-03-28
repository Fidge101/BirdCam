"""
config.py — Central configuration loader for BirdCam.

Reads all settings from a .env file using python-dotenv so that no credentials
are ever hardcoded. A single Config object is constructed at import time and
re-used across every module — import it with:

    from config import config
"""

import os
import logging
from dataclasses import dataclass
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load the .env file from the project root (same directory as this file).
load_dotenv()


def _require(key: str) -> str:
    """Return the value of a required environment variable, or raise."""
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{key}' is missing. "
            f"Copy .env.example to .env and fill in your values."
        )
    return value


def _int(key: str, default: int) -> int:
    """Return an integer env var, falling back to a default."""
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid integer for %s=%r, using default %d", key, raw, default)
        return default


@dataclass(frozen=True)
class Config:
    """
    Immutable configuration object populated from environment variables.

    All camera credentials come exclusively from the .env file — never
    from command-line arguments or hardcoded strings.
    """

    # ── Camera ────────────────────────────────────────────────────────────────
    camera_ip: str
    camera_user: str
    camera_pass: str

    # The fully constructed RTSP URL.
    # /stream1 is the primary HD stream on the Tapo C120.
    # The camera also exposes /stream2 which delivers a lower-resolution feed —
    # useful for constrained bandwidth, but we default to HD here.
    rtsp_url: str

    # ── Capture ───────────────────────────────────────────────────────────────
    capture_interval_seconds: int
    frame_store_dir: str
    max_frames: int

    # ── Timelapse ─────────────────────────────────────────────────────────────
    timelapse_output_path: str

    # ── Web dashboard ─────────────────────────────────────────────────────────
    port: int
    stream_quality: int


def _load() -> Config:
    """Construct and return the Config object from environment variables."""
    camera_ip = _require("CAMERA_IP")
    camera_user = _require("CAMERA_USER")
    camera_pass = _require("CAMERA_PASS")

    # Build the RTSP URL.  Credentials are embedded in the URL as required by
    # the RTSP spec; OpenCV's VideoCapture passes them through transparently.
    rtsp_url = f"rtsp://{camera_user}:{camera_pass}@{camera_ip}/stream1"

    frame_store_dir = os.getenv("FRAME_STORE_DIR", "./frames/")
    # Ensure the frame store directory exists at load time.
    os.makedirs(frame_store_dir, exist_ok=True)

    return Config(
        camera_ip=camera_ip,
        camera_user=camera_user,
        camera_pass=camera_pass,
        rtsp_url=rtsp_url,
        capture_interval_seconds=_int("CAPTURE_INTERVAL_SECONDS", 60),
        frame_store_dir=frame_store_dir,
        max_frames=_int("MAX_FRAMES", 100),
        timelapse_output_path=os.getenv("TIMELAPSE_OUTPUT_PATH", "./timelapse.jpg"),
        port=_int("PORT", 5000),
        stream_quality=_int("STREAM_QUALITY", 80),
    )


# Module-level singleton — import this everywhere.
# Raises EnvironmentError on missing required vars so failures are caught early.
config = _load()
