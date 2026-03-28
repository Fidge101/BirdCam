"""
main.py — BirdCam entry point.

Usage examples:
    python main.py --live          # open live OpenCV viewer
    python main.py --capture       # run frame capture scheduler only
    python main.py --timelapse     # generate timelapse from saved frames
    python main.py --web           # start web dashboard + capture scheduler
    python main.py --all           # live viewer + web dashboard + scheduler

The camera is connected once and shared across all active components.
A KeyboardInterrupt (Ctrl-C) in any mode triggers a clean shutdown.
"""

import argparse
import logging
import signal
import socket
import sys
import threading

# ── Logging setup (must happen before any local imports that log at import time)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("birdcam.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    """Parse and return command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="birdcam",
        description="BirdCam — Tapo C120 capture, viewer, and web dashboard.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--live",
        action="store_true",
        help="Open a live OpenCV viewer window.",
    )
    mode.add_argument(
        "--capture",
        action="store_true",
        help="Run the frame-capture scheduler in the foreground.",
    )
    mode.add_argument(
        "--timelapse",
        action="store_true",
        help="Generate a timelapse contact sheet from saved frames (no camera needed).",
    )
    mode.add_argument(
        "--web",
        action="store_true",
        help="Start the web dashboard + frame-capture scheduler.",
    )
    mode.add_argument(
        "--all",
        action="store_true",
        help="Run live viewer + web dashboard + capture scheduler together.",
    )
    return parser.parse_args()


def _local_hostname() -> str:
    """Return the machine's hostname (used to print the dashboard URL)."""
    try:
        return socket.gethostname()
    except Exception:
        return "raspberrypi"


def main() -> None:
    """
    Entry point: parse args, load config, connect camera, dispatch to modules.

    Handles KeyboardInterrupt cleanly regardless of which mode is active so
    the camera is always released and threads stop before exit.
    """
    args = _parse_args()

    # ── Timelapse mode doesn't need a camera connection ───────────────────────
    if args.timelapse:
        from config import config
        from timelapse import generate_timelapse

        logger.info("Generating timelapse from %s", config.frame_store_dir)
        generate_timelapse(config.frame_store_dir, config.timelapse_output_path)
        return

    # ── All other modes need config + a connected camera ─────────────────────
    from config import config
    from camera import CameraStream

    camera = CameraStream(config.rtsp_url)

    if not camera.connect():
        logger.error("Cannot connect to camera. Exiting.")
        sys.exit(1)

    stop_event = threading.Event()

    def _shutdown(sig=None, frame=None) -> None:
        """Signal/interrupt handler: stop threads, release camera."""
        logger.info("Shutting down…")
        stop_event.set()
        camera.release()

    signal.signal(signal.SIGTERM, _shutdown)

    try:
        if args.live:
            _run_live(camera)

        elif args.capture:
            _run_capture(camera, config, stop_event)

        elif args.web:
            _run_web(camera, config, stop_event)

        elif args.all:
            _run_all(camera, config, stop_event)

    except KeyboardInterrupt:
        pass
    finally:
        _shutdown()


# ── Mode runners ──────────────────────────────────────────────────────────────

def _run_live(camera) -> None:
    """Open the live OpenCV viewer and block until the user presses 'q'."""
    from viewer import run_live_view
    run_live_view(camera)


def _run_capture(camera, config, stop_event: threading.Event) -> None:
    """
    Start the capture scheduler in a background thread and block.

    The main thread sleeps until Ctrl-C arrives.
    """
    from scheduler import run_scheduler
    run_scheduler(camera, config, stop_event)
    logger.info("Capture scheduler running. Press Ctrl-C to stop.")
    stop_event.wait()  # block main thread


def _run_web(camera, config, stop_event: threading.Event) -> None:
    """
    Start the capture scheduler + Flask web server.

    The scheduler runs as a daemon thread; Flask runs in the foreground
    (threaded=True so MJPEG streaming and API calls don't block each other).
    """
    from scheduler import run_scheduler
    from web.server import create_app

    run_scheduler(camera, config, stop_event)

    hostname = _local_hostname()
    logger.info("Dashboard: http://%s.local:%d", hostname, config.port)
    print(f"\n  Dashboard: http://{hostname}.local:{config.port}\n")

    app = create_app(camera, config)
    app.run(host="0.0.0.0", port=config.port, threaded=True, use_reloader=False)


def _run_all(camera, config, stop_event: threading.Event) -> None:
    """
    Start scheduler + web server as background threads, then open live viewer.

    The live viewer runs on the main thread (OpenCV requires the main thread
    on most platforms for GUI operations).  All background threads are daemon
    threads so they stop automatically when the main thread exits.
    """
    from scheduler import run_scheduler
    from web.server import create_app
    from viewer import run_live_view
    import threading

    run_scheduler(camera, config, stop_event)

    app = create_app(camera, config)
    web_thread = threading.Thread(
        target=lambda: app.run(
            host="0.0.0.0", port=config.port, threaded=True, use_reloader=False
        ),
        daemon=True,
        name="FlaskWebServer",
    )
    web_thread.start()

    hostname = _local_hostname()
    logger.info("Dashboard: http://%s.local:%d", hostname, config.port)
    print(f"\n  Dashboard: http://{hostname}.local:{config.port}\n")

    # Live viewer blocks until the user presses 'q'.
    run_live_view(camera)


if __name__ == "__main__":
    main()
