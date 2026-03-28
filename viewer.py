"""
viewer.py — Live OpenCV window viewer.

Why a separate module:
    Keeping the live viewer isolated means it can be imported and run
    independently of the capture scheduler and web server.  It also makes
    testing on a headless Pi easier — just don't import this module.

Display notes:
    The timestamp is burned into the rendered window (not into saved frames)
    using cv2.putText.  This avoids polluting the timelapse contact sheet
    with overlaid text while still giving the operator a live clock.
"""

import logging
from datetime import datetime

import cv2
import numpy as np

from camera import CameraStream

logger = logging.getLogger(__name__)

# Text rendering parameters for the on-screen timestamp.
_FONT = cv2.FONT_HERSHEY_SIMPLEX
_FONT_SCALE = 0.65
_FONT_THICKNESS = 1
_TEXT_COLOR = (255, 255, 255)      # white
_SHADOW_COLOR = (0, 0, 0)          # black drop-shadow for readability
_PADDING = 10                      # pixels from frame edge


def run_live_view(camera_stream: CameraStream) -> None:
    """
    Open an OpenCV window displaying the live camera feed.

    Overlays the current wall-clock timestamp in the bottom-left corner of
    each frame.  Press 'q' to close the window and return.

    Args:
        camera_stream: An already-connected CameraStream.  If the stream
                       goes offline mid-session the viewer shows a grey
                       placeholder and keeps retrying.
    """
    window_name = "BirdCam — Live View  (press q to quit)"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    logger.info("Live viewer started. Press 'q' to quit.")

    placeholder = _make_offline_frame()

    while True:
        frame = camera_stream.read_frame()

        if frame is None:
            display_frame = placeholder.copy()
        else:
            display_frame = _overlay_timestamp(frame)

        cv2.imshow(window_name, display_frame)

        # waitKey(1) releases the GIL briefly and processes window events.
        # 0xFF mask handles platforms where waitKey returns a 32-bit int.
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            logger.info("Live viewer closed by user.")
            break

    cv2.destroyAllWindows()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _overlay_timestamp(frame: np.ndarray) -> np.ndarray:
    """
    Return a copy of frame with the current datetime drawn in the bottom-left.

    A 1-pixel black shadow is drawn first so the white text is legible on
    both light and dark backgrounds.
    """
    out = frame.copy()
    text = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    h, _w = out.shape[:2]
    origin = (_PADDING, h - _PADDING)

    # Shadow pass
    cv2.putText(
        out, text,
        (origin[0] + 1, origin[1] + 1),
        _FONT, _FONT_SCALE, _SHADOW_COLOR, _FONT_THICKNESS + 1, cv2.LINE_AA,
    )
    # Foreground pass
    cv2.putText(
        out, text, origin,
        _FONT, _FONT_SCALE, _TEXT_COLOR, _FONT_THICKNESS, cv2.LINE_AA,
    )
    return out


def _make_offline_frame(width: int = 640, height: int = 480) -> np.ndarray:
    """
    Build a grey placeholder frame shown when the camera is unreachable.

    Draws "NO SIGNAL" centred in white text so the viewer window stays
    visible and informative rather than freezing on the last good frame.
    """
    frame = np.full((height, width, 3), 50, dtype=np.uint8)
    text = "NO SIGNAL"
    font_scale = 2.0
    thickness = 3
    (tw, th), baseline = cv2.getTextSize(text, _FONT, font_scale, thickness)
    x = (width - tw) // 2
    y = (height + th) // 2
    cv2.putText(frame, text, (x, y), _FONT, font_scale, (180, 180, 180), thickness, cv2.LINE_AA)
    return frame
