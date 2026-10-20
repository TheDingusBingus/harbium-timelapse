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
from .parallel import ordered_map
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
    steady: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Brightness and warmth of every frame.

    Brightness is measured on the still part of the scene: each pixel is
    compared with the same pixel in the previous frame, pixels that moved far
    more than the rest (walkers, cars, birds) are set aside, and the change is
    measured on the remainder. Flicker, real light and exposure changes move
    nearly every pixel, so they're measured in full.
    (A crowd crossing 15% of a real frame read as 0.2 stops of "darkening" on
    the whole-frame measure, and deflicker brightened that frame by mistake.)
    Needs a camera that stays put, as on any timelapse tripod. ``steady=False``
    uses the whole-frame tone measure alone.

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
    chain = SteadyChain()
    loaded = ordered_map(_load_for_measuring, [(path, speed) for path in paths])
    for i, (image, warmth[i]) in enumerate(loaded):
        profiles[i] = tone_profile(image)
        chain.add(steady_input(image))
        if progress:
            progress(i + 1, len(paths))
    band = brightness_from_profiles(profiles)
    return (chain.series(band) if steady else band), warmth


MIN_STEADY_PIXELS = 500   # fewer usable still pixels shared by two frames: fall back to the tone measure
MOVER = 0.25              # stops: a pixel that changed this much more than the frame's typical change is moving


def steady_input(linear: np.ndarray) -> np.ndarray:
    """One brightness value per pixel (2-D, linear) for ``steady_change``;
    blown pixels are marked as blown so they're left out."""
    img = np.asarray(linear, dtype=np.float64)
    if img.ndim == 2:
        return img
    lum = img[..., 0] * 0.2126 + img[..., 1] * 0.7152 + img[..., 2] * 0.0722
    return np.where(img.max(axis=-1) >= CLIPPED, 1.0, lum)


def steady_change(previous: np.ndarray | None, current: np.ndarray) -> float | None:
    """How much the still part of the scene changed between two frames, in stops.

    Per-pixel changes find the movers: pixels that changed far more (or less)
    than the frame's typical change (people, cars, birds). The change is then
    the ratio of the still pixels' total brightness. Totals over thousands of
    pixels average out 8-bit rounding, which per-pixel medians don't, and the
    ratio is exact for any change that really is global.
    ``None`` if the two frames can't be compared."""
    if previous is None or previous.shape != current.shape:
        return None
    ok = (previous > BLACK) & (current > BLACK) & (previous < CLIPPED) & (current < CLIPPED)
    if ok.sum() < MIN_STEADY_PIXELS:
        return None
    p, c = previous[ok], current[ok]
    d = np.log2(c) - np.log2(p)
    typical = np.median(d)
    spread = 1.4826 * np.median(np.abs(d - typical))
    still = np.abs(d - typical) < max(MOVER, 3 * spread)
    if still.sum() < MIN_STEADY_PIXELS:
        return None
    return float(np.log2(c[still].sum() / p[still].sum()))


def measurable(values: np.ndarray) -> bool:
    """Enough pixels that are neither black nor blown to measure a frame by.
    A lens cap, a black frame or a fully blown frame isn't."""
    v = np.asarray(values)
    return int(((v > BLACK) & (v < CLIPPED)).sum()) >= MIN_STEADY_PIXELS


class SteadyChain:
    """Builds a brightness series from the still part of the scene, one frame
    at a time, keeping only the last measurable frame in memory.

    A frame that can't be measured (black, blown) is skipped: the next frame
    is compared with the last one that could be, so one bad frame can't
    carry "unknown" into every frame after it. Skipped frames read as NaN.
    """

    def __init__(self) -> None:
        self.reference: np.ndarray | None = None
        self.steps: list[float | None] = []
        self.usable: list[bool] = []

    def add(self, current: np.ndarray) -> None:
        if not measurable(current):
            self.steps.append(None)
            self.usable.append(False)
            return
        step = None if self.reference is None else steady_change(self.reference, current)
        self.steps.append(step)
        self.usable.append(True)
        self.reference = current

    def series(self, fallback: np.ndarray) -> np.ndarray:
        """Chain the changes; where two frames couldn't be compared, the
        whole-frame tone measure's change fills in. Same overall level as
        ``fallback``; NaN for frames that couldn't be measured at all."""
        fallback = np.asarray(fallback, dtype=np.float64)
        out = np.full(len(fallback), np.nan)
        prev = None
        for i, (step, usable) in enumerate(zip(self.steps, self.usable, strict=True)):
            if not usable:
                continue
            if prev is None:
                out[i], prev = 0.0, i
                continue
            if step is None:
                step = fallback[i] - fallback[prev]
                if not np.isfinite(step):
                    continue
            out[i], prev = out[prev] + step, i
        both = np.isfinite(out) & np.isfinite(fallback)
        if both.any():
            out += fallback[both].mean() - out[both].mean()
        return out


def fill_gaps(values) -> np.ndarray:
    """Unknown (NaN) values replaced by a straight line between their known
    neighbours, for maths that needs every frame. Raises if under two are known."""
    y = np.asarray(values, dtype=np.float64)
    known = np.isfinite(y)
    if known.all():
        return y.copy()
    if known.sum() < 2:
        raise ValueError("Couldn't measure enough frames: they're all black or all blown out.")
    x = np.arange(len(y))
    return np.interp(x, x[known], y[known])


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


def _load_for_measuring(job: tuple[Path, str]) -> tuple[np.ndarray, float]:
    """One frame's small linear image and warmth. Runs in a worker process."""
    path, speed = job
    try:
        return _load(path, speed)
    except ImportError:
        raise
    except Exception as e:
        raise ValueError(f"Couldn't read the picture in {path.name}. Is the file complete? ({e})") from e


def _load(path: Path, speed: str) -> tuple[np.ndarray, float]:
    if path.suffix.lower() in RAW_EXTENSIONS:
        return _load_raw(path, speed)
    if path.suffix.lower() in (".tif", ".tiff"):
        rgb = _load_tiff(path)
        if rgb is not None:
            return rgb, warmth_of(rgb)
    rgb = _load_image(Image.open(path))
    return rgb, warmth_of(rgb)


def _load_tiff(path: Path) -> np.ndarray | None:
    """16-bit TIFFs at full precision (Pillow can't read 16-bit colour).
    ``None`` if tifffile isn't installed or the file isn't plain RGB."""
    try:
        import tifffile
    except ImportError:
        return None
    arr = tifffile.imread(str(path))
    if arr.ndim != 3 or arr.shape[-1] < 3 or arr.dtype not in (np.uint8, np.uint16):
        return None
    levels = 65535.0 if arr.dtype == np.uint16 else 255.0
    return _srgb_to_linear(_shrink(arr[..., :3]).astype(np.float64) / levels)


def _load_raw(path: Path, speed: str) -> tuple[np.ndarray, float]:
    try:
        import rawpy
    except ImportError as e:
        raise ImportError(
            "Reading RAW files needs rawpy. Install it with: pip install 'harbium-timelapse[raw]'"
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
