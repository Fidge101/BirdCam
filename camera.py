"""
camera.py — RTSP camera interface for the Tapo C120.

Why OpenCV VideoCapture:
    cv2.VideoCapture accepts an RTSP URL directly and handles all the
    low-level RTSP negotiation, TCP/UDP transport, and frame decoding
    internally via FFmpeg.  This avoids pulling in a separate GStreamer
    pipeline or custom RTSP library — OpenCV is already required for frame
    processing, so there are no extra dependencies.

Why cap.set(CAP_PROP_BUFFERSIZE, 1):
    OpenCV's default internal buffer holds several frames which introduces
    latency.  Setting it to 1 keeps the retrieved frame as close to "live"
    as possible, which matters when running alongside the live viewer.
"""

import time
import logging
import cv2
import numpy as np

logger = logging.getLogger(__name__)

_RECONNECT_ATTEMPTS = 3
_RECONNECT_DELAY_SECONDS = 5


class CameraStream:
    """
    Manages an RTSP video stream from the Tapo C120.

    Wraps cv2.VideoCapture with clean connect / read / release semantics
    and automatic reconnection for intermittent WiFi dropouts.
    """

    def __init__(self, rtsp_url: str) -> None:
        """
        Initialise the stream wrapper.

        Args:
            rtsp_url: Full RTSP URL including credentials, e.g.
                      rtsp://user:pass@192.168.1.100/stream1
        """
        self._rtsp_url = rtsp_url
        self._cap: cv2.VideoCapture | None = None

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        """
        Open the RTSP stream.

        Sets the internal buffer to 1 frame to minimise latency.  Returns
        True on success, False if the stream cannot be opened (wrong IP,
        bad credentials, camera offline, etc.).
        """
        logger.info("Connecting to RTSP stream: %s", self._redacted_url())
        self._cap = cv2.VideoCapture(self._rtsp_url, cv2.CAP_FFMPEG)

        # Minimise buffering lag — we want the freshest possible frame.
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not self._cap.isOpened():
            logger.error(
                "Failed to open RTSP stream. Check CAMERA_IP, CAMERA_USER, "
                "CAMERA_PASS, and that RTSP is enabled on the camera."
            )
            self._cap = None
            return False

        logger.info("RTSP stream connected successfully.")
        return True

    def reconnect(self) -> bool:
        """
        Attempt to re-open a dropped RTSP stream.

        WiFi IP cameras (like the C120) drop connections intermittently due
        to keep-alive timeouts or brief network hiccups.  This method tries
        up to _RECONNECT_ATTEMPTS times with a short pause between each.

        Returns True if reconnection succeeds, False after all attempts fail.
        """
        self.release()
        for attempt in range(1, _RECONNECT_ATTEMPTS + 1):
            logger.warning(
                "Reconnect attempt %d/%d…", attempt, _RECONNECT_ATTEMPTS
            )
            if self.connect():
                return True
            if attempt < _RECONNECT_ATTEMPTS:
                time.sleep(_RECONNECT_DELAY_SECONDS)
        logger.error("All reconnect attempts failed.")
        return False

    # ── Frame reading ─────────────────────────────────────────────────────────

    def read_frame(self) -> np.ndarray | None:
        """
        Return the latest frame as a BGR numpy array, or None on failure.

        The caller is responsible for handling None gracefully (e.g. by
        showing a placeholder or triggering a reconnect).
        """
        if self._cap is None or not self._cap.isOpened():
            return None
        ret, frame = self._cap.read()
        if not ret or frame is None:
            logger.warning("Frame read failed — stream may have dropped.")
            return None
        return frame

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def release(self) -> None:
        """
        Release the underlying VideoCapture and free associated resources.

        Safe to call even if the stream was never opened.
        """
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            logger.info("Camera stream released.")

    # ── Helpers ───────────────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        """True if the capture object exists and reports itself as open."""
        return self._cap is not None and self._cap.isOpened()

    def _redacted_url(self) -> str:
        """Return the RTSP URL with the password replaced by *** for logging."""
        try:
            # rtsp://user:pass@host/path  →  rtsp://user:***@host/path
            at_idx = self._rtsp_url.index("@")
            prefix = self._rtsp_url[:at_idx]
            suffix = self._rtsp_url[at_idx:]
            colon_idx = prefix.rindex(":")
            return prefix[: colon_idx + 1] + "***" + suffix
        except ValueError:
            return self._rtsp_url
