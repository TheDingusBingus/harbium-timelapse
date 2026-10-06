"""Measure how bright each frame is, in stops.

Two speeds:

- ``"careful"`` (default): decodes the RAW data itself, linear, no camera
  tone curve. A correction measured this way is exactly what an editor's
  exposure slider needs. Slower: about a second a frame.
- ``"fast"``: reads the small JPEG preview the camera embedded in the RAW.
  Much quicker, slightly less exact, because the camera's own tone curve has
  already been applied. Good for a first look.

JPEG, TIFF and PNG frames are always read directly; speed makes no difference.

How brightness is measured: every frame's tones are ranked darkest to
brightest. Blown highlights and pure blacks sit at the ends of that ranking.
Brightness is the average, in stops, of the middle band of tones that is
neither blown nor black in *any* frame of the sequence. Because the same
band is used for every frame, an exposure change of exactly one stop
measures as exactly one stop, even as a sky clips more or less across the
sequence.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
from PIL import Image

from .sequencer import RAW_EXTENSIONS
from .types import Sequence

LONG_EDGE = 512        # pixels; brightness doesn't need more
CLIPPED = 0.98         # linear; at or above this a pixel counts as blown
BLACK = 1 / 65535      # linear; at or below this a pixel counts as black
BAND = (10.0, 90.0)    # percent of tones considered, darkest to brightest
STEP = 0.5             # percent between points in a tone profile
MIN_BAND = 5.0         # percent; below this the shared band is too thin to trust

Progress = Callable[[int, int], None]


def measure_brightness(
    frames: Sequence | Iterable[Path | str],
    speed: str = "careful",
    progress: Progress | None = None,
) -> np.ndarray:
    """Brightness of every frame, in stops. Higher is brighter.

    The absolute number means little on its own. What matters is the
    difference between frames: 0.1 apart means one frame is a tenth of a
    stop brighter than the other.
    """
    if speed not in ("careful", "fast"):
        raise ValueError('speed must be "careful" or "fast"')
    paths = [f.path for f in frames] if isinstance(frames, Sequence) else [Path(p) for p in frames]

    profiles = np.empty((len(paths), _PERCENTS.size))
    for i, path in enumerate(paths):
        profiles[i] = tone_profile(load_linear(path, speed))
        if progress:
            progress(i + 1, len(paths))
    return brightness_from_profiles(profiles)


def brightness_of(linear_rgb: np.ndarray) -> float:
    """Brightness in stops of a single linear image (H×W×3 or H×W, values 0–1)."""
    return float(brightness_from_profiles(tone_profile(linear_rgb)[None, :])[0])


_PERCENTS = np.arange(0.0, 100.0 + STEP / 2, STEP)


def tone_profile(linear_rgb: np.ndarray) -> np.ndarray:
    """The frame's tones ranked darkest to brightest, in stops, one value per
    ``STEP`` percent. Blown tones are ``+inf``, black tones ``-inf``."""
    img = np.asarray(linear_rgb, dtype=np.float64)
    if img.ndim == 3:
        luminance = img[..., 0] * 0.2126 + img[..., 1] * 0.7152 + img[..., 2] * 0.0722
        blown = img.max(axis=-1) >= CLIPPED
    else:
        luminance = img
        blown = img >= CLIPPED
    with np.errstate(divide="ignore"):
        stops = np.log2(np.maximum(luminance, 0.0))
    stops[luminance <= BLACK] = -np.inf
    stops[blown] = np.inf
    return np.percentile(stops.ravel(), _PERCENTS, method="nearest")


def brightness_from_profiles(profiles: np.ndarray) -> np.ndarray:
    """Turn tone profiles (frames × percents) into brightness per frame.

    Uses the band of tones that is usable in every frame, so all frames are
    measured on the same tones. Falls back to each frame's own usable band
    only if the shared band is too thin (some frame almost entirely blown or
    black), which is noted in the docstring rather than hidden.
    """
    profiles = np.atleast_2d(profiles)
    in_band = (_PERCENTS >= BAND[0]) & (_PERCENTS <= BAND[1])
    usable = np.isfinite(profiles)

    shared = in_band & usable.all(axis=0)
    if shared.sum() * STEP >= MIN_BAND:
        return profiles[:, shared].mean(axis=1)

    out = np.empty(len(profiles))
    for i, row in enumerate(profiles):
        own = in_band & usable[i]
        if not own.any():
            own = usable[i]
        out[i] = row[own].mean() if own.any() else np.nan
    return out


# --- loading ----------------------------------------------------------------

def load_linear(path: Path, speed: str = "careful") -> np.ndarray:
    """Load a frame as small linear RGB, values 0–1."""
    if path.suffix.lower() in RAW_EXTENSIONS:
        return _load_raw(path, speed)
    return _load_image(Image.open(path))


def _load_raw(path: Path, speed: str) -> np.ndarray:
    try:
        import rawpy
    except ImportError as e:
        raise ImportError(
            "Reading RAW files needs rawpy. Install it with: pip install 'timelapse-core[raw]'"
        ) from e

    with rawpy.imread(str(path)) as raw:
        if speed == "fast":
            try:
                thumb = raw.extract_thumb()
            except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
                thumb = None
            if thumb is not None and thumb.format == rawpy.ThumbFormat.JPEG:
                return _load_image(Image.open(BytesIO(thumb.data)))
            if thumb is not None and thumb.format == rawpy.ThumbFormat.BITMAP:
                return _srgb_to_linear(_shrink(thumb.data) / 255.0)

        rgb = raw.postprocess(
            half_size=True,
            gamma=(1, 1),
            no_auto_bright=True,
            output_bps=16,
            use_camera_wb=True,
            output_color=rawpy.ColorSpace.sRGB,
            user_flip=0,
        )
    return _shrink(rgb) / 65535.0


def _load_image(img: Image.Image) -> np.ndarray:
    img.draft("RGB", (LONG_EDGE, LONG_EDGE))  # JPEG decodes at reduced size: fast
    if img.mode in ("I;16", "I;16B", "I;16L", "I"):
        arr = np.asarray(img, dtype=np.float64) / 65535.0
    else:
        arr = np.asarray(img.convert("RGB"), dtype=np.float64) / 255.0
    return _srgb_to_linear(_shrink(np.clip(arr, 0, 1)))


def _shrink(arr: np.ndarray) -> np.ndarray:
    step = max(1, max(arr.shape[:2]) // LONG_EDGE)
    return arr[::step, ::step]


def _srgb_to_linear(v: np.ndarray) -> np.ndarray:
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)
