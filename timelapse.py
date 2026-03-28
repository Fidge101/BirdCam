"""
timelapse.py — Contact-sheet timelapse generator.

Why a contact sheet instead of a video:
    Generating a video requires a codec and a display library, both of which
    add complexity and may not be available on a headless Pi.  A contact
    sheet (grid of thumbnails) can be reviewed in any image viewer, sent
    over SSH with scp, or served as a static file by the web dashboard.
    It also gives an at-a-glance overview of the whole session.

Why Pillow instead of OpenCV for the grid assembly:
    Pillow's paste() API makes image grid construction trivial and requires
    no type conversions.  OpenCV would work too but involves more boilerplate
    for drawing borders and handling partial last rows.
"""

import os
import logging
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

_THUMB_WIDTH = 320
_THUMB_HEIGHT = 180


def generate_timelapse(
    frame_dir: str,
    output_path: str,
    columns: int = 10,
) -> None:
    """
    Build a contact-sheet JPEG from all frames in frame_dir.

    Frames are read in chronological order (YYYYMMDD_HHMMSS.jpg filenames
    sort lexicographically == temporally).  Each is resized to 320×180 and
    placed into a grid with `columns` thumbnails per row.  The last row is
    padded with solid-black tiles when the frame count isn't divisible by
    columns.

    If imageio is available, an animated GIF is also written alongside the
    JPEG at output_path + ".gif".

    Args:
        frame_dir:   Directory containing captured JPEG frames.
        output_path: Destination path for the output contact-sheet JPEG.
        columns:     Number of thumbnails per row (default: 10).
    """
    frame_files = sorted(
        p for p in Path(frame_dir).glob("*.jpg")
        # Exclude the timelapse output itself if it lives in the same dir.
        if p.resolve() != Path(output_path).resolve()
    )

    if not frame_files:
        logger.warning("No frames found in %s — nothing to generate.", frame_dir)
        print(f"No frames found in {frame_dir}.")
        return

    logger.info("Loading %d frames from %s", len(frame_files), frame_dir)

    thumbs: list[Image.Image] = []
    for path in frame_files:
        try:
            img = Image.open(path).convert("RGB")
            img = img.resize((_THUMB_WIDTH, _THUMB_HEIGHT), Image.LANCZOS)
            thumbs.append(img)
        except Exception as exc:
            logger.warning("Skipping unreadable frame %s: %s", path.name, exc)

    if not thumbs:
        logger.error("All frames were unreadable — aborting timelapse generation.")
        return

    # ── Build the contact sheet grid ─────────────────────────────────────────
    rows = (len(thumbs) + columns - 1) // columns  # ceiling division
    sheet_w = _THUMB_WIDTH * columns
    sheet_h = _THUMB_HEIGHT * rows

    sheet = Image.new("RGB", (sheet_w, sheet_h), color=(0, 0, 0))

    for idx, thumb in enumerate(thumbs):
        row, col = divmod(idx, columns)
        x = col * _THUMB_WIDTH
        y = row * _THUMB_HEIGHT
        sheet.paste(thumb, (x, y))

    # Ensure the output directory exists.
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    sheet.save(output_path, "JPEG", quality=85)
    print(f"Timelapse saved: {output_path}  ({len(thumbs)} frames, {rows}×{columns} grid)")
    logger.info("Timelapse saved to %s (%d frames)", output_path, len(thumbs))

    # ── Bonus: animated GIF ───────────────────────────────────────────────────
    _try_save_gif(thumbs, output_path)


def _try_save_gif(thumbs: list[Image.Image], output_path: str) -> None:
    """
    Save an animated GIF alongside the contact sheet if imageio is available.

    The GIF uses each thumbnail as one frame at ~10 fps (100 ms per frame).
    This is best-effort — if imageio is absent the function logs a note and
    returns silently without raising.
    """
    try:
        import imageio  # optional dependency
    except ImportError:
        logger.info(
            "imageio not installed — skipping animated GIF. "
            "Install it with: pip install imageio"
        )
        return

    gif_path = output_path + ".gif"
    try:
        frames_rgb = [thumb for thumb in thumbs]
        imageio.mimsave(gif_path, frames_rgb, fps=10, loop=0)
        print(f"Animated GIF saved: {gif_path}")
        logger.info("Animated GIF saved to %s", gif_path)
    except Exception as exc:
        logger.warning("GIF generation failed: %s", exc)
