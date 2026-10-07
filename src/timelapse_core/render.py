"""Turn a folder of finished frames into a video, quickly, for checking.

Uses ffmpeg (free: ffmpeg.org; on Windows ``winget install Gyan.FFmpeg``).
Frames are fed to ffmpeg one by one in sequence order, so file names don't
need to follow any pattern and gaps in numbering don't matter.

This is a preview render: H.264, high quality, native resolution. For the
finished film, use your editor.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable

from .sequencer import _natural_key

RENDERABLE = {".jpg": "mjpeg", ".jpeg": "mjpeg", ".png": "png", ".tif": "tiff", ".tiff": "tiff"}


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def render_video(
    folder: Path | str,
    output: Path | str | None = None,
    fps: float = 24,
    width: int | None = None,
    quality: int = 16,
    progress: Callable[[int, int], None] | None = None,
    blend_frames: int = 1,
) -> Path:
    """Render every JPEG, PNG or TIFF in ``folder`` to an MP4, in name order.

    - ``fps``: frames per second of the video.
    - ``width``: scale to this width (e.g. 3840). Default: native size.
      Very large videos may stutter in some players; VLC copes best.
    - ``quality``: 0 is lossless, 16 is visually lossless, 23 is ffmpeg's
      default. Lower is better and bigger.
    - ``blend_frames``: mix each frame with its neighbours (2 or 3), the
      timelapse equivalent of a longer shutter. Calms shimmer from things
      that move between frames, like leaves in wind. 1 = off.

    Returns the path of the video.
    """
    folder = Path(folder)
    if not ffmpeg_available():
        raise RuntimeError(
            "Rendering needs ffmpeg. On Windows: winget install Gyan.FFmpeg "
            "(then open a new terminal). Elsewhere: ffmpeg.org."
        )
    frames = sorted(
        (p for p in folder.iterdir()
         if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in RENDERABLE),
        key=_natural_key,
    )
    if not frames:
        raise ValueError(f"No JPEG, PNG or TIFF frames found in {folder}")
    kinds = {RENDERABLE[p.suffix.lower()] for p in frames}
    if len(kinds) > 1:
        raise ValueError(f"{folder} mixes image types. Render one type at a time.")

    output = Path(output) if output else folder.parent / f"{folder.name}.mp4"
    if not 1 <= blend_frames <= 5:
        raise ValueError("blend_frames should be between 1 (off) and 5")
    scale = f"scale={width}:-2" if width else "scale=trunc(iw/2)*2:trunc(ih/2)*2"  # H.264 needs even sizes
    if blend_frames > 1:
        scale = f"tmix=frames={blend_frames}," + scale
    command = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "image2pipe", "-framerate", str(fps), "-c:v", kinds.pop(), "-i", "-",
        "-vf", f"{scale},format=yuv420p",
        "-c:v", "libx264", "-preset", "slow", "-crf", str(quality),
        "-movflags", "+faststart",
        str(output),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for i, frame in enumerate(frames, start=1):
            process.stdin.write(frame.read_bytes())
            if progress:
                progress(i, len(frames))
        process.stdin.close()
    except BrokenPipeError:
        pass  # ffmpeg stopped early; its own message is reported below
    error = process.stderr.read().decode(errors="replace").strip()
    if process.wait() != 0:
        raise RuntimeError(f"ffmpeg couldn't make the video: {error or 'no reason given'}")
    return output
