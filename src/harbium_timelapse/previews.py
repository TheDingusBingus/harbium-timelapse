"""Small previews of RAW frames, saved while Prepare measures them.

Measuring already decodes every RAW frame (at half size, with one fixed white
balance and one fixed scale for every frame, so differences between frames are
real). The measuring worker saves a preview from that decoded frame, 1,600
pixels on the long edge, before shrinking it for measuring: the big image never
leaves the worker, and it gives the app what
it needs to show what corrections will do without decoding the RAW files again:
time slices at Prepare and Grade, and the live preview. Kept in the user cache
folder, keyed by each file's path, size and modified time.

Each preview is stored as bright as it can be without clipping its highlights
(a power-of-two gain, so dark frames keep their detail in 8 bits), with the
gain written in the file, so loading gives back the frame's true linear
brightness. (A fixed gain clipped bright daytime frames: one came back 0.96
stops too dark.)
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np

VERSION = "raw-preview-3"
MAX_GAIN = 256.0
SIZE = 1600                 # long edge in pixels: big enough for a full-screen view


def _folder() -> Path:
    from .exif import _cache_dir
    return _cache_dir() / "previews"


def _file(path: Path) -> Path | None:
    try:
        st = Path(path).stat()
    except OSError:
        return None
    key = hashlib.sha256(f"{VERSION}|{Path(path).resolve()}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:24]
    return _folder() / f"{key}.jpg"


def gain_for(linear: np.ndarray) -> float:
    """The largest power of two that keeps the frame's highlights (99.9th percentile) unclipped."""
    top = float(np.percentile(linear, 99.9))
    if top <= 0:
        return MAX_GAIN
    return float(min(max(2.0 ** np.floor(np.log2(0.95 / top)), 1.0), MAX_GAIN))


def save(path: Path, linear: np.ndarray) -> None:
    target = _file(path)
    if target is None or os.environ.get("HARBIUM_NO_CACHE"):
        return
    from PIL import Image
    from .timeslice import to_display
    rgb = _resize(linear[..., :3])
    gain = gain_for(rgb)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f"{target.stem}.{os.getpid()}.tmp.jpg")
        Image.fromarray(to_display(rgb, gain)).save(temp, quality=92, comment=f"gain={gain:g}".encode())
        os.replace(temp, target)
    except OSError:
        pass                                      # a preview that can't be saved is never an error


def load(path: Path) -> np.ndarray | None:
    """The frame's preview in linear light, at its true brightness, or None if there isn't one."""
    target = _file(path)
    if target is None or not target.exists():
        return None
    from PIL import Image
    from .timeslice import from_display
    try:
        with Image.open(target) as im:
            gain = float(im.info.get("comment", b"gain=1").decode().split("=", 1)[1])
            return from_display(np.asarray(im.convert("RGB"))) / gain
    except (OSError, ValueError, IndexError):
        return None


def _resize(rgb: np.ndarray) -> np.ndarray:
    """To SIZE on the long edge, in linear light (so brightness stays true), channel by channel."""
    h, w = rgb.shape[:2]
    if max(h, w) <= SIZE:
        return rgb.astype(np.float32)
    from PIL import Image
    scale = SIZE / max(h, w)
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    return np.stack([np.asarray(Image.fromarray(np.ascontiguousarray(rgb[..., c], dtype=np.float32), "F")
                                .resize(size, Image.LANCZOS)) for c in range(3)], axis=-1).clip(0)


def missing(paths: list[Path]) -> list[int]:
    return [i for i, p in enumerate(paths) if (f := _file(p)) is None or not f.exists()]
