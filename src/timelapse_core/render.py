"""Turn a folder of finished frames into a video, quickly, for checking.

Uses ffmpeg (free: ffmpeg.org; on Windows ``winget install Gyan.FFmpeg``).
Frames are fed to ffmpeg one by one in sequence order, so file names don't
need to follow any pattern and gaps in numbering don't matter.

This is a preview render: H.264, high quality, native resolution. For the
finished film, use your editor.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
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
    max_mb: float | None = None,
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
    - ``max_mb``: keep the file under this many megabytes, for sending
      around. Encodes in two passes at the best quality that fits. Without
      ``width``, scales to 1920 wide (full HD), which suits a size limit.

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
    kind = kinds.pop()

    output = Path(output) if output else folder.parent / f"{folder.name}.mp4"
    if not 1 <= blend_frames <= 5:
        raise ValueError("blend_frames should be between 1 (off) and 5")
    scale = f"scale={width}:-2" if width else "scale=trunc(iw/2)*2:trunc(ih/2)*2"  # H.264 needs even sizes
    if blend_frames > 1:
        scale = f"tmix=frames={blend_frames}," + scale
    if max_mb is not None:
        if not width:
            scale = scale.replace("scale=trunc(iw/2)*2:trunc(ih/2)*2", "scale=1920:-2")
        seconds = len(frames) / fps
        kbits = max_mb * 8 * 1000 * 0.92 / seconds   # 8% headroom for the container
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "pass")
            for number, target in ((1, os.devnull), (2, str(output))):
                _encode(frames, kind, fps, scale, target, progress,
                        ["-c:v", "libx264", "-preset", "slow", "-b:v", f"{kbits:.0f}k",
                         "-maxrate", f"{kbits * 1.5:.0f}k", "-bufsize", f"{kbits * 2:.0f}k",
                         "-pass", str(number), "-passlogfile", log]
                        + (["-f", "mp4"] if number == 1 else ["-movflags", "+faststart"]))
    else:
        _encode(frames, kind, fps, scale, str(output), progress,
                ["-c:v", "libx264", "-preset", "slow", "-crf", str(quality), "-movflags", "+faststart"])
    return output


def _encode(frames, kind, fps, scale, target, progress, codec_args) -> None:
    command = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "image2pipe", "-framerate", str(fps), "-c:v", kind, "-i", "-",
        "-vf", f"{scale},format=yuv420p", *codec_args, target,
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
