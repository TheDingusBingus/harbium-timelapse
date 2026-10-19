"""Finish: take the last wobble out of the finished frames themselves.

Works after any editor. Deflicker and white balance do the heavy lifting on
the RAW files, before your grade, where highlights and shadows still have
room. Whatever an editor then does on top (tone mappers, dehaze, sliders that
don't move the picture as much as real light does) can leave a little
frame-to-frame wobble. Finish measures the frames you exported and removes
it, directly on the pixels: no sliders, no learning how an editor behaves,
no exporting again.

How:
- Each colour channel's brightness is measured per frame on the same band of
  tones in every frame (as deflicker does, so clipped skies and black
  shadows can't fool it).
- Each channel's journey through the sequence is smoothed, keeping the
  light's real arc (sunset, night falling) and removing the jumps.
- The difference is applied to each frame as one gain per channel, in linear
  light. One number per channel per frame: no masks, nothing local, no halos.
  Because it corrects exactly what it measured, in the same terms, there is
  nothing to tune and no second pass.

Limits: it can't bring back highlights or shadows the export already clipped,
so keep corrections before the grade (prepare, deflicker) doing the big work.
Small corrections like these don't band, even on 8-bit JPEGs.

Reads JPEG, PNG and TIFF (16-bit TIFF needs the ``tifffile`` package).
Writes corrected frames, a video (needs ffmpeg), or both.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from .deflicker import DEFAULT_WINDOW, smooth_brightness
from .measure import (
    LONG_EDGE, _PERCENTS, _srgb_to_linear, brightness_from_profiles, steady_change, steady_series,
    tone_profile,
)
from .render import RENDERABLE, ffmpeg_available
from .sequencer import _natural_key

Progress = Callable[[int, int], None]
LUMA = np.array([0.2126, 0.7152, 0.0722])
JPEG_QUALITY = 95


def finished_frames(folder: Path | str) -> list[Path]:
    """The exported frames in a folder, in name order (frame numbers sort naturally)."""
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError(f"Can't find a folder at {folder}")
    frames = [p for p in folder.iterdir()
              if p.is_file() and p.suffix.lower() in RENDERABLE and not p.name.startswith(".")]
    frames.sort(key=_natural_key)
    if len(frames) < 3:
        raise ValueError(f"Found {len(frames)} frames (JPEG, PNG or TIFF) in {folder}: need at least 3.")
    return frames


@dataclass(frozen=True)
class FinishPlan:
    """What finish found and will do. All values in stops."""
    frames: tuple[Path, ...]
    measured: np.ndarray   # frames × (red, green, blue)
    target: np.ndarray     # the smoothed journey, same shape

    @property
    def gains(self) -> np.ndarray:
        return self.target - self.measured

    @property
    def brightness_before(self) -> float:
        return _wobble(self.measured @ LUMA)

    @property
    def brightness_after(self) -> float:
        return _wobble(self.target @ LUMA)

    @property
    def colour_before(self) -> float:
        return _wobble(self.measured[:, 0] - self.measured[:, 2])

    @property
    def colour_after(self) -> float:
        return _wobble(self.target[:, 0] - self.target[:, 2])

    @property
    def largest_brightness_correction(self) -> float:
        return float(np.max(np.abs(self.gains @ LUMA)))

    @property
    def largest_colour_correction(self) -> float:
        return float(np.max(np.abs(self.gains[:, 0] - self.gains[:, 2])))


def measure_channels(frames: list[Path], progress: Progress | None = None, steady: bool = True) -> np.ndarray:
    """Brightness of each colour channel of each frame (frames × 3, stops).

    Like deflicker's measurement: the change of the still part of the scene
    since the previous frame, so walkers and cars can't sway it; the
    whole-frame tone measure fills in where two frames can't be compared."""
    profiles = np.empty((3, len(frames), _PERCENTS.size))
    steps: list[list] = [[], [], []]
    previous = None
    for i, path in enumerate(frames):
        img = _read(path)
        step = max(1, max(img.shape[:2]) // LONG_EDGE)
        small = _srgb_to_linear(img[::step, ::step, :3].astype(np.float64) / _levels(img))
        current = [np.where(small.max(axis=-1) >= 0.98, 1.0, small[..., c]) for c in range(3)]
        for c in range(3):
            profiles[c, i] = tone_profile(small[..., c])
            if i:
                steps[c].append(steady_change(previous[c], current[c]))
        previous = current
        if progress:
            progress(i + 1, len(frames))
    band = [brightness_from_profiles(profiles[c]) for c in range(3)]
    return np.column_stack([steady_series(steps[c], band[c]) if steady else band[c] for c in range(3)])


def plan_finish(frames: list[Path], window: int = DEFAULT_WINDOW, colour: bool = True,
                measured: np.ndarray | None = None, progress: Progress | None = None) -> FinishPlan:
    """Work out each frame's correction. Writes nothing.

    ``colour=False`` corrects brightness only: the same gain on all three
    channels, so colour is left exactly as exported.
    """
    if measured is None:
        measured = measure_channels(frames, progress)
    measured = np.asarray(measured, dtype=np.float64)
    if colour:
        target = np.column_stack([smooth_brightness(measured[:, c], window) for c in range(3)])
    else:
        luma = measured @ LUMA
        target = measured + (smooth_brightness(luma, window) - luma)[:, None]
    return FinishPlan(tuple(frames), measured, target)


def apply_finish(plan: FinishPlan, out: Path | str | None = None, video: Path | str | None = None,
                 fps: float = 24, width: int | None = None, quality: int = 16,
                 progress: Progress | None = None, fmt: str | None = None) -> None:
    """Correct every frame. ``out``: a folder for corrected frames (same names;
    same format unless ``fmt`` is "jpg", "png" or "tif"). ``video``: an MP4
    encoded straight from the corrected frames, so nothing in between needs
    storing. Your exported frames are never changed.

    Frames that go dark (dusk into night) are best exported as 16-bit TIFF:
    in 8-bit, shadows have too few levels to measure or correct small changes
    precisely. Correct in 16-bit, write JPEGs with ``fmt="jpg"``."""
    if fmt is not None and fmt.lower().lstrip(".") not in ("jpg", "jpeg", "png", "tif", "tiff"):
        raise ValueError('The output format must be "jpg", "png" or "tif".')
    if out is None and video is None:
        raise ValueError("Say where to put the result: a folder for frames, a video file, or both.")
    if out is not None:
        out = Path(out)
        if out.resolve() == plan.frames[0].parent.resolve():
            raise ValueError("Write the corrected frames to a different folder: your export stays untouched.")
        out.mkdir(parents=True, exist_ok=True)
    encoder = None
    try:
        for i, (path, gains) in enumerate(zip(plan.frames, plan.gains)):
            img, info = _read(path, with_info=True)
            fixed = correct(img, gains)
            if out is not None:
                target = out / path.name if fmt is None else (out / path.name).with_suffix("." + fmt.lower().lstrip("."))
                _write(target, fixed, info)
            if video is not None:
                if encoder is None:
                    encoder = _Encoder(Path(video), fixed, fps, width, quality)
                encoder.write(fixed)
            if progress:
                progress(i + 1, len(plan.frames))
    finally:
        if encoder is not None:
            encoder.close()


def correct(img: np.ndarray, gains_stops) -> np.ndarray:
    """One gain per channel, in linear light, through exact lookup tables."""
    levels = _levels(img)
    v = np.arange(levels + 1, dtype=np.float64) / levels
    linear = _srgb_to_linear(v)
    out = img.copy()
    for c, g in enumerate(gains_stops):
        lut = np.round(_linear_to_srgb(np.clip(linear * 2.0 ** float(g), 0.0, 1.0)) * levels).astype(img.dtype)
        out[..., c] = lut[img[..., c]]
    return out


# --- files ---------------------------------------------------------------------

def _levels(img: np.ndarray) -> int:
    return 65535 if img.dtype == np.uint16 else 255


def _linear_to_srgb(v: np.ndarray) -> np.ndarray:
    return np.where(v <= 0.0031308, v * 12.92, 1.055 * np.power(v, 1 / 2.4) - 0.055)


def _read(path: Path, with_info: bool = False):
    if path.suffix.lower() in (".tif", ".tiff"):
        try:
            import tifffile
        except ImportError:
            tifffile = None
        if tifffile is not None:
            arr = tifffile.imread(str(path))
            if arr.ndim == 3 and arr.shape[-1] >= 3 and arr.dtype in (np.uint8, np.uint16):
                arr = np.ascontiguousarray(arr[..., :3])
                return (arr, {"tiff": True}) if with_info else arr
    with Image.open(path) as im:
        if im.mode not in ("RGB", "RGBA", "L"):
            raise ValueError(f"{path.name}: {im.mode} images aren't supported. For 16-bit TIFF, "
                             "install tifffile (pip install tifffile).")
        info = {k: im.info[k] for k in ("exif", "icc_profile", "dpi") if k in im.info}
        arr = np.asarray(im.convert("RGB"))
    return (arr, info) if with_info else arr


def _write(path: Path, img: np.ndarray, info: dict) -> None:
    suffix = path.suffix.lower()
    if suffix in (".tif", ".tiff") and (img.dtype == np.uint16 or info.get("tiff")):
        import tifffile
        tifffile.imwrite(str(path), img, photometric="rgb", compression="zlib")
        return
    if img.dtype == np.uint16:                      # 16-bit in, 8-bit out (JPEG, PNG)
        img = np.round(img / 257.0).astype(np.uint8)
    extra = {k: v for k, v in info.items() if k in ("exif", "icc_profile", "dpi")}
    if suffix in (".jpg", ".jpeg"):
        Image.fromarray(img).save(path, quality=JPEG_QUALITY, subsampling=0, **extra)
    else:
        Image.fromarray(img).save(path, **extra)


class _Encoder:
    """Feeds corrected frames straight to ffmpeg: no files in between."""

    def __init__(self, target: Path, first: np.ndarray, fps: float, width: int | None, quality: int):
        if not ffmpeg_available():
            raise RuntimeError("Making a video needs ffmpeg. On Windows: winget install Gyan.FFmpeg "
                               "(then open a new terminal). Elsewhere: ffmpeg.org.")
        self.shape, self.dtype = first.shape, first.dtype
        h, w = first.shape[:2]
        scale = f"scale={width}:-2" if width else "scale=trunc(iw/2)*2:trunc(ih/2)*2"
        pix = "rgb48le" if first.dtype == np.uint16 else "rgb24"
        self.process = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", pix,
             "-s", f"{w}x{h}", "-framerate", str(fps), "-i", "-",
             "-vf", f"{scale},format=yuv420p", "-c:v", "libx264", "-preset", "slow",
             "-crf", str(quality), "-movflags", "+faststart", str(target)],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    def write(self, frame: np.ndarray) -> None:
        if frame.shape != self.shape or frame.dtype != self.dtype:
            raise ValueError("Every frame must be the same size and bit depth to make a video.")
        data = frame.astype("<u2") if frame.dtype == np.uint16 else frame
        try:
            self.process.stdin.write(np.ascontiguousarray(data).tobytes())
        except BrokenPipeError:
            pass   # ffmpeg stopped; its message is reported on close

    def close(self) -> None:
        try:
            self.process.stdin.close()
        except BrokenPipeError:
            pass
        error = self.process.stderr.read().decode(errors="replace").strip()
        if self.process.wait() != 0:
            raise RuntimeError(f"ffmpeg couldn't make the video: {error or 'no reason given'}")


def _wobble(values: np.ndarray) -> float:
    v = np.asarray(values, dtype=np.float64)
    return float(np.std(np.diff(v)) / np.sqrt(2)) if len(v) > 2 else 0.0
