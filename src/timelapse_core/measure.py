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
    return measure_light(frames, speed=speed, progress=progress)[0]


def measure_light(
    frames: Sequence | Iterable[Path | str],
    speed: str = "careful",
    progress: Progress | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Brightness and warmth of every frame.

    Warmth is how red the light is compared with blue, in stops: higher is
    warmer. Like brightness, only the differences between frames matter.
    For RAW files in careful mode it's read from the sensor data itself,
    before any white balance, so the camera's auto white balance changing its
    mind from frame to frame doesn't get mistaken for the light changing.
    """
    if speed not in ("careful", "fast"):
        raise ValueError('speed must be "careful" or "fast"')
    paths = [f.path for f in frames] if isinstance(frames, Sequence) else [Path(p) for p in frames]

    profiles = np.empty((len(paths), _PERCENTS.size))
    warmth = np.empty(len(paths))
    for i, path in enumerate(paths):
        try:
            image, warmth[i] = _load(path, speed)
        except ImportError:
            raise
        except Exception as e:
            raise ValueError(f"Couldn't read the picture in {path.name}. Is the file complete? ({e})") from e
        profiles[i] = tone_profile(image)
        if progress:
            progress(i + 1, len(paths))
    return brightness_from_profiles(profiles), warmth


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
    return _load(Path(path), speed)[0]


def warmth_of(linear_rgb: np.ndarray) -> float:
    """Red over blue, in stops, of a linear RGB image. Higher is warmer."""
    img = np.asarray(linear_rgb, dtype=np.float64)
    if img.ndim != 3:
        return 0.0
    ok = (img.max(axis=-1) < CLIPPED) & (img.min(axis=-1) > BLACK)
    if ok.sum() < 100:
        ok = np.ones(img.shape[:2], dtype=bool)
    red, blue = img[..., 0][ok].mean(), img[..., 2][ok].mean()
    return float(np.log2(red / blue)) if red > 0 and blue > 0 else 0.0


def _load(path: Path, speed: str) -> tuple[np.ndarray, float]:
    if path.suffix.lower() in RAW_EXTENSIONS:
        return _load_raw(path, speed)
    rgb = _load_image(Image.open(path))
    return rgb, warmth_of(rgb)


def _load_raw(path: Path, speed: str) -> tuple[np.ndarray, float]:
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
            rgb = None
            if thumb is not None and thumb.format == rawpy.ThumbFormat.JPEG:
                rgb = _load_image(Image.open(BytesIO(thumb.data)))
            elif thumb is not None and thumb.format == rawpy.ThumbFormat.BITMAP:
                rgb = _srgb_to_linear(_shrink(thumb.data) / 255.0)
            if rgb is not None:
                return rgb, warmth_of(rgb)  # fast: preview colour, after the camera's white balance

        warmth = _sensor_warmth(raw)
        # Every frame must be scaled the same way, or the measurement invents
        # flicker. Two LibRaw defaults don't do that:
        # - adjust_maximum_thr (0.75): if a frame's brightest pixel is above
        #   75% of the sensor's white level, LibRaw treats that pixel as white
        #   for that frame only. A glint at 78% brightened a whole frame by
        #   +0.36 stops on a real R6 sequence whose light was steady.
        # - the camera's own white balance, which changes frame to frame on
        #   Auto WB and leaks into brightness.
        # So: the sensor's fixed white level, and one fixed white balance.
        fixed_wb = [float(v) for v in list(getattr(raw, "daylight_whitebalance", []))[:3]]
        if len(fixed_wb) < 3 or min(fixed_wb) <= 0:
            fixed_wb = [1.0, 1.0, 1.0]
        rgb = raw.postprocess(
            half_size=True,
            gamma=(1, 1),
            no_auto_bright=True,
            output_bps=16,
            adjust_maximum_thr=0.0,
            use_camera_wb=False,
            use_auto_wb=False,
            user_wb=fixed_wb + [fixed_wb[1]],   # R, G, B, second G
            output_color=rawpy.ColorSpace.sRGB,
            user_flip=0,
        )
    return _shrink(rgb) / 65535.0, warmth


def _sensor_warmth(raw) -> float:
    """Red over blue straight from the sensor's red and blue photosites,
    before white balance. Unclipped pixels only."""
    img = raw.raw_image_visible
    colours = raw.raw_colors_visible
    black = raw.black_level_per_channel
    white = raw.white_level * CLIPPED
    step = 2 * max(1, max(img.shape) // (2 * LONG_EDGE))  # even, so the colour pattern survives
    means = {}
    for dy in (0, 1):
        for dx in (0, 1):
            colour = int(colours[dy, dx])
            if colour not in (0, 2):  # 0 red, 2 blue
                continue
            values = img[dy::step, dx::step].astype(np.float64)
            values = values[values < white] - black[colour]
            values = values[values > 0]
            if values.size:
                means[colour] = values.mean()
    if 0 in means and 2 in means:
        return float(np.log2(means[0] / means[2]))
    return 0.0


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
