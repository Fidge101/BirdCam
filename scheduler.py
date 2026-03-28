"""
scheduler.py — Background frame-capture scheduler.

Why threading instead of a separate process:
    Running the scheduler as a daemon thread keeps it inside the same Python
    process as the live viewer and the web server.  This means all three
    components share the single CameraStream object without IPC overhead,
    and the process exits cleanly when the main thread ends.

Frame pruning:
    Frames are sorted lexicographically by filename (YYYYMMDD_HHMMSS.jpg),
    which is also chronological order.  The oldest files appear first, so
    deleting from the front of a sorted list is correct.
"""

import os
import time
import logging
import threading
from datetime import datetime

import cv2

from camera import CameraStream
from config import Config

logger = logging.getLogger(__name__)


def run_scheduler(
    camera_stream: CameraStream,
    config: Config,
    stop_event: threading.Event | None = None,
) -> threading.Thread:
    """
    Start the frame-capture loop in a background daemon thread.

    Every config.capture_interval_seconds the scheduler reads a frame from
    camera_stream, saves it as a JPEG, and prunes the oldest files if the
    total exceeds config.max_frames.

    Args:
        camera_stream: An already-connected (or at least initialised)
                       CameraStream instance.
        config:        Loaded application configuration.
        stop_event:    Optional threading.Event; set it to stop the thread
                       gracefully.  If None, the thread runs until the
                       process exits.

    Returns:
        The daemon Thread (already started).
    """
    if stop_event is None:
        stop_event = threading.Event()

    thread = threading.Thread(
        target=_capture_loop,
        args=(camera_stream, config, stop_event),
        daemon=True,
        name="FrameCaptureScheduler",
    )
    thread.start()
    logger.info(
        "Frame capture scheduler started — interval: %ds, store: %s, max: %d",
        config.capture_interval_seconds,
        config.frame_store_dir,
        config.max_frames,
    )
    return thread


def _capture_loop(
    camera_stream: CameraStream,
    config: Config,
    stop_event: threading.Event,
) -> None:
    """
    Inner loop executed by the background thread.

    Sleeps in short increments so the stop_event is checked frequently,
    enabling a responsive shutdown rather than waiting a full interval.
    """
    next_capture = time.monotonic()

    while not stop_event.is_set():
        now = time.monotonic()

        if now >= next_capture:
            _capture_frame(camera_stream, config)
            next_capture = now + config.capture_interval_seconds

        # Sleep in 0.5 s slices to stay responsive to stop_event.
        stop_event.wait(timeout=0.5)


def _capture_frame(camera_stream: CameraStream, config: Config) -> None:
    """
    Read a single frame and write it to disk, then prune if needed.

    Filenames are ISO-like timestamps (YYYYMMDD_HHMMSS.jpg) so that
    lexicographic sort == chronological sort, which the timelapse generator
    and the pruning logic both rely on.
    """
    frame = camera_stream.read_frame()

    if frame is None:
        logger.warning("Capture skipped — no frame available (camera offline?).")
        # Attempt a single reconnect so the next interval has a chance.
        camera_stream.reconnect()
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}.jpg"
    filepath = os.path.join(config.frame_store_dir, filename)

    success, encoded = cv2.imencode(
        ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, config.stream_quality]
    )
    if not success:
        logger.error("JPEG encoding failed for frame %s", filename)
        return

    with open(filepath, "wb") as fh:
        fh.write(encoded.tobytes())

    logger.info("Captured frame: %s (%.1f KB)", filepath, os.path.getsize(filepath) / 1024)

    _prune_old_frames(config)


def _prune_old_frames(config: Config) -> None:
    """
    Delete the oldest frames if the count exceeds config.max_frames.

    Sorting by name works because filenames are YYYYMMDD_HHMMSS.jpg —
    the oldest frames sort first.
    """
    frame_dir = config.frame_store_dir
    try:
        frames = sorted(
            f for f in os.listdir(frame_dir) if f.endswith(".jpg")
        )
    except FileNotFoundError:
        return

    excess = len(frames) - config.max_frames
    if excess <= 0:
        return

    for name in frames[:excess]:
        path = os.path.join(frame_dir, name)
        try:
            os.remove(path)
            logger.info("Pruned oldest frame: %s", name)
        except OSError as exc:
            logger.warning("Could not delete frame %s: %s", name, exc)
