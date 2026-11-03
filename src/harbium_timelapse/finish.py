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

import os
import subprocess
import time
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from .deflicker import DEFAULT_WINDOW, check_window, smooth_brightness
from .measure import (
    CLIPPED, LONG_EDGE, SteadyChain, _srgb_to_linear, brightness_from_profiles,
    tone_profile,
)
from .parallel import ordered_map
from .render import RENDERABLE, ffmpeg, ffmpeg_available
from .sequencer import _natural_key

Progress = Callable[[int, int], None]
LUMA = np.array([0.2126, 0.7152, 0.0722])
JPEG_QUALITY = 95
DITHER_BAND = 256        # rows corrected at a time (each band can go to its own thread)
CORRECT_THREADS = max(1, min(8, (os.cpu_count() or 1)))


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
    measured: np.ndarray   # frames × (red, green, blue); NaN where a frame couldn't be measured
    target: np.ndarray     # the smoothed journey, same shape

    def trimmed(self, first: int | None = None, last: int | None = None) -> "FinishPlan":
        """Frames ``first`` to ``last`` (1-based, inclusive) of this plan. The smoothing was done on the whole
        sequence, so a trimmed video keeps the same smooth curve, with no new edges at the cut."""
        n = len(self.frames)
        a, b = (first or 1) - 1, last or n
        if not 0 <= a < b <= n:
            raise ValueError(f"The range must be within frames 1 to {n}, with the first before the last.")
        return FinishPlan(tuple(self.frames[a:b]), self.measured[a:b], self.target[a:b])

    @property
    def unmeasured(self) -> tuple[int, ...]:
        """Frames that couldn't be measured (black, fully blown): copied unchanged."""
        return tuple(int(i) for i in np.flatnonzero(~np.isfinite(self.measured).all(axis=1)))

    @property
    def gains(self) -> np.ndarray:
        g = self.target - self.measured
        g[~np.isfinite(g).all(axis=1)] = 0.0
        return g

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
        return float(np.max(np.abs(self.gains @ LUMA))) if len(self.frames) else 0.0

    @property
    def largest_colour_correction(self) -> float:
        return float(np.max(np.abs(self.gains[:, 0] - self.gains[:, 2]))) if len(self.frames) else 0.0


class Meter:
    """Measures frames one at a time, as they arrive (from disk or straight
    from memory while they're being written), so the same measurement serves
    the plan and the self-check without reading every file again."""

    def __init__(self, motion: bool = False):
        self.profiles: list[list[np.ndarray]] = [[], [], []]
        self.chains = [SteadyChain(), SteadyChain(), SteadyChain()]
        self.shifts: list[tuple[float, float]] | None = [] if motion else None
        self.width = 0
        self._previous = None

    def add(self, small: np.ndarray) -> None:
        blown = small.max(axis=-1) >= CLIPPED
        for c in range(3):
            self.profiles[c].append(tone_profile(small[..., c]))
            self.chains[c].add(np.where(blown, 1.0, small[..., c]))
        if self.shifts is not None:
            luma = small @ LUMA
            if self._previous is not None and luma.shape == self._previous.shape:
                from .review import shift
                self.shifts.append(shift(self._previous, luma))
            self._previous, self.width = luma, luma.shape[1]

    def series(self, steady: bool = True) -> np.ndarray:
        band = [brightness_from_profiles(np.array(self.profiles[c])) for c in range(3)]
        return np.column_stack([self.chains[c].series(band[c]) if steady else band[c] for c in range(3)])

    def motion(self) -> float:
        from .review import camera_motion
        return camera_motion(self.shifts or [], self.width)


def measure_channels(frames: list[Path], progress: Progress | None = None, steady: bool = True,
                     meter: Meter | None = None) -> np.ndarray:
    """Brightness of each colour channel of each frame (frames × 3, stops).

    Like deflicker's measurement: the change of the still part of the scene
    since the previous frame, so walkers and cars can't sway it; the
    whole-frame tone measure fills in where two frames can't be compared.
    Pass a ``Meter(motion=True)`` to also learn whether the camera moved."""
    meter = meter or Meter()
    for i, small in enumerate(ordered_map(_small_linear_of, frames)):
        meter.add(small)
        if progress:
            progress(i + 1, len(frames))
    return meter.series(steady)


def plan_finish(frames: list[Path], window: int = DEFAULT_WINDOW, colour: bool = True,
                measured: np.ndarray | None = None, progress: Progress | None = None) -> FinishPlan:
    """Work out each frame's correction. Writes nothing.

    ``colour=False`` corrects brightness only: the same gain on all three
    channels, so colour is left exactly as exported.
    """
    check_window(window)
    if measured is None:
        measured = measure_channels(frames, progress)
    measured = np.asarray(measured, dtype=np.float64)
    if np.isfinite(measured).all(axis=1).sum() < 2:
        raise ValueError("Couldn't measure enough frames: they're all black or all blown out.")
    if colour:
        target = np.column_stack([smooth_brightness(measured[:, c], window) for c in range(3)])
    else:
        luma = measured @ LUMA
        target = measured + (smooth_brightness(luma, window) - luma)[:, None]
    return FinishPlan(tuple(frames), measured, target)


CODECS = {
    # name: what it is, ffmpeg encoder, allowed files, 10-bit?, default quality (CRF; lower is better)
    "h264": ("H.264, plays anywhere", "libx264", (".mp4", ".mov", ".mkv"), False, 16),
    "h265": ("H.265 10-bit, smaller files with smoother gradients", "libx265", (".mp4", ".mov", ".mkv"), True, 18),
    "prores": ("ProRes 422 HQ 10-bit, for editing", "prores_ks", (".mov",), True, None),
}


def check_output_path(path: Path | str) -> None:
    """Refuse a path containing "…": it's almost certainly a shortened example copied from instructions
    (Windows happily makes a folder called "…" at the top of the drive)."""
    if "\u2026" in str(path):
        raise ValueError(f'The path {path} contains "…", which looks like a shortened example rather than a real '
                         "folder. Give the full path.")


PRORES_PROFILES = {"proxy": 0, "lt": 1, "standard": 2, "hq": 3, "4444": 4}
CHROMA = ("420", "422", "444")


@dataclass(frozen=True)
class VideoOptions:
    """Everything about how the video is encoded (resolution and frame rate are separate).

    codec: "h264", "h265" or "prores". quality: CRF for H.264/H.265 (lower is
    better; None = the codec's default). bit_depth: 8 or 10 (H.264 is 8-bit;
    H.265 defaults to 10; ProRes is 10). chroma: "420", "422" or "444" colour
    detail (ProRes takes it from its profile). prores_profile: proxy, lt,
    standard, hq or 4444."""
    codec: str = "h264"
    quality: int | None = None
    bit_depth: int | None = None
    chroma: str | None = None
    prores_profile: str = "hq"

    @property
    def depth(self) -> int:
        if self.codec == "prores":
            return 10
        return self.bit_depth or (10 if self.codec == "h265" else 8)

    @property
    def subsampling(self) -> str:
        if self.codec == "prores":
            return "444" if self.prores_profile == "4444" else "422"
        return self.chroma or "420"

    @property
    def pix_fmt(self) -> str:
        base = {"420": "yuv420p", "422": "yuv422p", "444": "yuv444p"}[self.subsampling]
        return base + ("10le" if self.depth == 10 else "")

    @property
    def ten_bit(self) -> bool:
        return self.depth == 10

    def check(self) -> None:
        """Refuse combinations that can't be made, saying why, rather than quietly changing them."""
        if self.codec not in CODECS:
            raise ValueError(f"Unknown video format {self.codec!r}. Choose from: {', '.join(CODECS)}.")
        if self.chroma is not None and self.chroma not in CHROMA:
            raise ValueError("Colour detail must be 420, 422 or 444.")
        if self.codec == "h264" and self.bit_depth not in (None, 8):
            raise ValueError("H.264 is made in 8-bit here. For 10-bit, choose H.265 or ProRes.")
        if self.codec == "h265" and self.bit_depth not in (None, 8, 10):
            raise ValueError("H.265 can be 8-bit or 10-bit.")
        if self.codec == "prores":
            if self.prores_profile not in PRORES_PROFILES:
                raise ValueError(f"ProRes profile must be one of: {', '.join(PRORES_PROFILES)}.")
            if self.chroma is not None and self.chroma != self.subsampling:
                raise ValueError(f"ProRes {self.prores_profile} is always {self.subsampling[0]}:"
                                 f"{self.subsampling[1]}:{self.subsampling[2]}. For 4:4:4 choose the 4444 profile.")
            if self.bit_depth not in (None, 10):
                raise ValueError("ProRes is always 10-bit.")
        if self.quality is not None and self.codec != "prores" and not 0 <= self.quality <= 51:
            raise ValueError("Quality (CRF) must be between 0 and 51; lower means better and bigger.")

    def notes(self, width: int | None = None) -> list[str]:
        """Plain compatibility notes for this choice, for showing before anything is written."""
        out = []
        if self.codec == "h264" and self.subsampling != "420":
            out.append("H.264 with 4:2:2 or 4:4:4 colour plays in editing software, but many players, "
                       "phones and TVs can't play it.")
        if self.codec == "h264" and width and width > 4096:
            out.append("H.264 wider than 4K won't play on some phones and TVs.")
        if self.codec == "h265":
            out.append("Some Windows PCs need Microsoft's HEVC extension to play H.265.")
            if self.subsampling != "420":
                out.append("H.265 with 4:2:2 or 4:4:4 colour is for editing; most players can't play it.")
        if self.codec == "prores":
            out.append("ProRes files are large: made for video editors, not for sharing.")
        return out


def check_video_target(video: Path | str, codec: str = "h264") -> None:
    """Refuse a video that can't be made, before any slow work starts."""
    if codec not in CODECS:
        raise ValueError(f"Unknown video format {codec!r}. Choose from: {', '.join(CODECS)}.")
    label, encoder, suffixes, _, _ = CODECS[codec]
    if Path(video).suffix.lower() not in suffixes:
        raise ValueError(f"{label.split(',')[0]} video needs a file ending in {' or '.join(suffixes)} "
                         f"(not {Path(video).name}).")
    if ffmpeg_available() and encoder not in _ffmpeg_encoders():
        raise ValueError(f"This ffmpeg can't make {label.split(',')[0]} video (it has no {encoder}). "
                         "Use --codec h264, or install a full ffmpeg build.")


_ENCODERS: set[str] | None = None


def encoders_available(codec: str) -> bool:
    """Can this computer's ffmpeg make this video format?"""
    return ffmpeg_available() and CODECS[codec][1] in _ffmpeg_encoders()


def _ffmpeg_encoders() -> set[str]:
    global _ENCODERS
    if _ENCODERS is None:
        try:
            out = subprocess.run([ffmpeg(), "-hide_banner", "-encoders"], capture_output=True, text=True,
                                 timeout=30).stdout
            _ENCODERS = {line.split()[1] for line in out.splitlines() if len(line.split()) > 1}
        except (OSError, subprocess.SubprocessError):
            _ENCODERS = set()
    return _ENCODERS


def apply_finish(plan: FinishPlan, out: Path | str | None = None, video: Path | str | None = None,
                 fps: float = 24, width: int | None = None, quality: int | None = None,
                 progress: Progress | None = None, fmt: str | None = None,
                 codec: str = "h264", dither: bool = True, meter: "Meter | None" = None,
                 finalising: Callable[[], None] | None = None, timings: dict | None = None,
                 options: "VideoOptions | None" = None, jpeg_quality: int = JPEG_QUALITY) -> list[Path]:
    """Correct every frame. ``out``: a folder for corrected frames (same names;
    same format unless ``fmt`` is "jpg", "png" or "tif"). ``video``: an MP4
    encoded straight from the corrected frames, so nothing in between needs
    storing. Your exported frames are never changed.

    Frames that go dark (dusk into night) are best exported as 16-bit TIFF:
    in 8-bit, shadows have too few levels to measure or correct small changes
    precisely. Correct in 16-bit, write JPEGs with ``fmt="jpg"``.

    ``codec``: "h264" (default, plays anywhere), "h265" (10-bit) or "prores"
    (ProRes 422 HQ, 10-bit, in a .mov). 10-bit video is fed the correction at
    16-bit precision, so it never goes through 8-bit rounding. ``dither``: hide
    the 8-bit rounding of corrected frames (see ``correct``). ``meter``:
    measures each corrected frame in memory as it's written, for the
    self-check. ``finalising``: called once every frame is in, before the
    encoder finishes the file (which can take a while).

    The video is written under a temporary name and renamed when complete, so
    a stopped or failed run never leaves a half-made video behind. The next
    frame is read in the background while the current one is corrected
    (decoding runs outside Python's lock), so the drive and the correction
    overlap. ``timings``: if given, gets the seconds spent on each part (read:
    waiting for a frame; read_busy: the reading itself; correct, save, check,
    encode: waiting for the encoder; finalise) and the bytes read."""
    if fmt is not None and fmt.lower().lstrip(".") not in ("jpg", "jpeg", "png", "tif", "tiff"):
        raise ValueError('The output format must be "jpg", "png" or "tif".')
    if out is None and video is None:
        raise ValueError("Say where to put the result: a folder for frames, a video file, or both.")
    for target_path in (out, video):
        if target_path is not None:
            check_output_path(target_path)
    if options is not None:
        options.check()
        codec = options.codec
    if not 1 <= jpeg_quality <= 100:
        raise ValueError("JPEG quality must be between 1 and 100.")
    if fps <= 0:
        raise ValueError(f"Frames per second must be above 0 (got {fps}).")
    if width is not None and width < 16:
        raise ValueError(f"The video width must be at least 16 pixels (got {width}).")
    targets: list[Path] = []
    if out is not None:
        out = Path(out)
        if out.exists() and not out.is_dir():
            raise ValueError(f"{out} is a file, not a folder. Give a folder for the corrected frames.")
        if out.resolve() == plan.frames[0].parent.resolve():
            raise ValueError("Write the corrected frames to a different folder: your export stays untouched.")
        suffix = None if fmt is None else "." + fmt.lower().lstrip(".")
        targets = [out / p.name if suffix is None else (out / p.name).with_suffix(suffix) for p in plan.frames]
        if out.is_dir():
            # Re-running into the same folder is fine (same names are replaced); other
            # frames there would end up mixed with these ones.
            wanted = {t.name.lower() for t in targets}
            others = [p.name for p in finished_frames_or_none(out) if p.name.lower() not in wanted]
            if others:
                raise ValueError(f"{out} already holds other frames (e.g. {others[0]}). Use an empty "
                                 "folder, so these frames don't get mixed with them.")
    if video is not None:
        _same_size_or_raise(plan.frames)          # checked before a single frame is written
        Path(video).parent.mkdir(parents=True, exist_ok=True)
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
    encoder = None
    partial = None
    if video is not None:
        video = Path(video)
        partial = video.with_name(video.stem + ".partial" + video.suffix)
    finished = False
    try:
        ten_bit = video is not None and (options.ten_bit if options is not None else CODECS[codec][3])
        clock = time.perf_counter
        spent = timings if timings is not None else {}
        for k in ("read", "correct", "save", "check", "encode", "finalise", "read_busy"):
            spent.setdefault(k, 0.0)
        spent.setdefault("bytes", 0)

        def read(path):
            start = clock()
            got = _read(path, with_info=True)
            return got, clock() - start, path.stat().st_size

        saver = ThreadPoolExecutor(1) if out is not None else None      # frames are saved while the next is corrected
        saved = None

        def save(target, image, information):
            start = clock()
            _write(target, image, information, jpeg_quality)
            return clock() - start

        reader = ThreadPoolExecutor(1)
        checker = ThreadPoolExecutor(1) if meter is not None else None     # in order, alongside the next frame
        checked = None

        def check(image):
            start = clock()
            meter.add(_small_linear(image))
            return clock() - start
        ahead = reader.submit(read, plan.frames[0]) if plan.frames else None
        for i, (path, gains) in enumerate(zip(plan.frames, plan.gains, strict=True)):
            t0 = clock()
            (img, info), busy, size = ahead.result()
            if i + 1 < len(plan.frames):
                ahead = reader.submit(read, plan.frames[i + 1])
            spent["read"] += clock() - t0
            spent["read_busy"] += busy
            spent["bytes"] += size
            fixed = None
            if out is not None:
                t0 = clock()
                to_8bit = targets[i].suffix.lower() in (".jpg", ".jpeg", ".png")
                fixed = correct(img, gains, out_dtype=np.uint8 if to_8bit else None, dither=dither, seed=i)
                spent["correct"] += clock() - t0
                if saved is not None:
                    spent["save"] += saved.result()               # the previous frame's save, done meanwhile
                saved = saver.submit(save, targets[i], fixed, info)
            if video is not None:
                t0 = clock()
                if ten_bit:
                    frame = correct(img, gains, out_dtype=np.uint16)
                elif fixed is not None and fixed.dtype == np.uint8:
                    frame = fixed
                else:
                    frame = correct(img, gains, out_dtype=np.uint8, dither=dither, seed=i)
                t1 = clock()
                if encoder is None:
                    encoder = _Encoder(partial, frame, fps, width, quality, codec, options)
                encoder.write(frame)
                spent["correct"] += t1 - t0
                spent["encode"] += clock() - t1
            if checker is not None:
                if checked is not None:
                    spent["check"] += checked.result()          # the previous frame's check, done meanwhile
                checked = checker.submit(check, fixed if fixed is not None else frame)
            if progress:
                progress(i + 1, len(plan.frames))
        if checked is not None:
            spent["check"] += checked.result()          # the last frame's check
        if saved is not None:
            spent["save"] += saved.result()                     # the last frame's save
        if encoder is not None and finalising is not None:
            finalising()              # every frame is in; the encoder can take a while to finish the file
        finished = True
    finally:
        if "reader" in locals():
            reader.shutdown(wait=True, cancel_futures=True)
        if locals().get("checker") is not None:
            checker.shutdown(wait=True, cancel_futures=True)
        if locals().get("saver") is not None:
            saver.shutdown(wait=True, cancel_futures=True)
        t_close = time.perf_counter()
        if encoder is not None:
            try:
                encoder.close()
            except RuntimeError:
                finished = False
                raise
            finally:
                if timings is not None:
                    timings["finalise"] = timings.get("finalise", 0.0) + time.perf_counter() - t_close
                if partial is not None and partial.exists():
                    if finished:
                        os.replace(partial, video)
                    else:
                        partial.unlink(missing_ok=True)
    return targets


def finished_frames_or_none(folder: Path) -> list[Path]:
    try:
        return finished_frames(folder)
    except ValueError:                             # fewer than 3: still worth listing
        return [p for p in Path(folder).iterdir()
                if p.is_file() and p.suffix.lower() in RENDERABLE and not p.name.startswith(".")]


def _frame_size(path: Path) -> tuple[int, int]:
    if path.suffix.lower() in (".tif", ".tiff"):
        try:
            import tifffile
            with tifffile.TiffFile(str(path)) as t:
                return tuple(t.pages[0].shape[:2])[::-1]
        except ImportError:
            pass
    with Image.open(path) as im:                    # reads the header only
        return im.size


def _same_size_or_raise(frames) -> None:
    first = _frame_size(frames[0])
    for p in frames[1:]:
        if _frame_size(p) != first:
            raise ValueError(f"Every frame must be the same size to make a video: {frames[0].name} is "
                             f"{first[0]}x{first[1]}, {p.name} is {'x'.join(map(str, _frame_size(p)))}.")


def correct(img: np.ndarray, gains_stops, out_dtype=None, dither: bool = False, seed: int = 0) -> np.ndarray:
    """One gain per channel, in linear light, through exact lookup tables.

    ``out_dtype``: the bit depth to produce (default: the same as ``img``).
    Asking for 16-bit from an 8-bit frame keeps the correction's full
    precision, for 10-bit video.

    ``dither``: when the result is rounded to 8 bits, add a tiny amount of
    triangular noise first (spread over less than one level either way, zero
    on average). Brightening or darkening 8-bit levels otherwise leaves a few
    levels unused, which can show as banding in smooth skies; dither spreads
    the rounding so the average of nearby pixels is exactly right. A channel
    with no correction is passed through untouched, and ``seed`` makes the
    noise the same every time the same frame is written."""
    gains = np.asarray(gains_stops, dtype=np.float64)
    if not np.all(np.isfinite(gains)):
        raise ValueError("Refusing to apply an unknown (non-finite) correction.")
    out_dtype = np.dtype(out_dtype or img.dtype)
    in_levels = _levels(img)
    out_levels = 65535 if out_dtype == np.uint16 else 255
    if out_dtype == img.dtype:
        out = img.copy()
    else:
        # Fill channel by channel through lookup tables: converting the whole frame to floating point
        # first made a copy of about 480 MB for a 20-megapixel frame, only to overwrite it.
        out = np.empty(img.shape, out_dtype)
        identity = np.round(np.arange(in_levels + 1, dtype=np.float64) * (out_levels / in_levels)).astype(out_dtype)
        for c in range(len(gains), img.shape[-1]):
            out[..., c] = identity[img[..., c]]
    linear = _srgb_to_linear(np.arange(in_levels + 1, dtype=np.float64) / in_levels)
    tables = []
    for c, g in enumerate(gains):
        if g == 0 and out_dtype == img.dtype:
            tables.append(None)                          # nothing to change: leave it exactly as it was
            continue
        exact = _linear_to_srgb(np.clip(linear * 2.0 ** float(g), 0.0, 1.0)) * out_levels
        tables.append(np.round(exact).astype(out_dtype) if not dither or out_levels > 255
                      else exact.astype(np.float32))

    def band(index: int, top: int, bottom: int) -> None:
        # Each band of rows on its own thread: table lookups, noise and rounding all run outside Python's
        # lock, so the bands really are corrected side by side. Each band's noise has its own seed, so
        # the result is the same however many threads there are.
        noise = np.random.default_rng([seed, index]) if dither else None
        for c, table in enumerate(tables):
            if table is None:
                continue
            values = np.take(table, img[top:bottom, :, c])
            if table.dtype == np.float32:
                values += noise.random(values.shape, dtype=np.float32)
                values -= noise.random(values.shape, dtype=np.float32)
                values = np.clip(np.rint(values), 0, 255).astype(np.uint8)
            out[top:bottom, :, c] = values

    rows = img.shape[0]
    bands = [(i, top, min(rows, top + DITHER_BAND)) for i, top in enumerate(range(0, rows, DITHER_BAND))]
    if len(bands) > 1 and CORRECT_THREADS > 1:
        with ThreadPoolExecutor(CORRECT_THREADS) as pool:
            list(pool.map(lambda b: band(*b), bands))
    else:
        for b in bands:
            band(*b)
    return out


def _small_linear_of(path: Path) -> np.ndarray:
    """A frame's small measuring copy, read from disk. Runs in a worker process."""
    return _small_linear(_read(path))


def _small_linear(img: np.ndarray) -> np.ndarray:
    """A small linear-light copy for measuring, made by AVERAGING blocks of
    pixels, not skipping them. Averaging beats 8-bit rounding: on noisy night
    frames (like real ones) it measured ~3x more precisely than skipping, close
    to 16-bit, so JPEG exports are enough."""
    lut = _srgb_to_linear(np.arange(_levels(img) + 1, dtype=np.float64) / _levels(img)).astype(np.float32)
    step = max(1, max(img.shape[:2]) // LONG_EDGE)
    h, w = (img.shape[0] // step) * step, (img.shape[1] // step) * step
    out = np.empty((h // step, w // step, 3))

    def band(r0: int, r1: int) -> None:    # block-rows r0..r1, a channel at a time: small and memory-friendly
        for c in range(3):
            rows = np.take(lut, img[r0 * step:r1 * step, :w, c]).reshape((r1 - r0) * step, w // step, step)
            rows = rows.sum(axis=2, dtype=np.float64)
            out[r0:r1, :, c] = rows.reshape(r1 - r0, step, w // step).sum(axis=1) / (step * step)
    n = h // step
    per = max(1, -(-n // CORRECT_THREADS))
    bands = [(r, min(n, r + per)) for r in range(0, n, per)]
    if len(bands) > 1 and CORRECT_THREADS > 1:
        with ThreadPoolExecutor(CORRECT_THREADS) as pool:
            list(pool.map(lambda b: band(*b), bands))
    else:
        for b in bands:
            band(*b)
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


def _write(path: Path, img: np.ndarray, info: dict, jpeg_quality: int = JPEG_QUALITY) -> None:
    suffix = path.suffix.lower()
    if suffix in (".tif", ".tiff") and (img.dtype == np.uint16 or info.get("tiff")):
        import tifffile
        tifffile.imwrite(str(path), img, photometric="rgb", compression="zlib")
        return
    if img.dtype == np.uint16:                      # 16-bit in, 8-bit out (JPEG, PNG)
        img = np.round(img / 257.0).astype(np.uint8)
    extra = {k: v for k, v in info.items() if k in ("exif", "icc_profile", "dpi")}
    if suffix in (".jpg", ".jpeg"):
        Image.fromarray(img).save(path, quality=jpeg_quality, subsampling=0, **extra)
    else:
        Image.fromarray(img).save(path, **extra)


class _Encoder:
    """Feeds corrected frames straight to ffmpeg: no files in between."""

    def __init__(self, target: Path, first: np.ndarray, fps: float, width: int | None,
                 quality: int | None, codec: str = "h264", options: "VideoOptions | None" = None):
        if not ffmpeg_available():
            raise RuntimeError("Making a video needs ffmpeg. On Windows: winget install Gyan.FFmpeg "
                               "(then open a new terminal). Elsewhere: ffmpeg.org.")
        self.shape, self.dtype = first.shape, first.dtype
        h, w = first.shape[:2]
        scale = f"scale={width // 2 * 2}:-2" if width else "scale=trunc(iw/2)*2:trunc(ih/2)*2"
        opts = options or VideoOptions(codec=codec, quality=quality)
        pix = "rgb48le" if first.dtype == np.uint16 else "rgb24"
        crf = str(opts.quality if opts.quality is not None else CODECS[opts.codec][4])
        video_args = {
            "h264": ["-c:v", "libx264", "-preset", "slow", "-crf", crf],
            "h265": ["-c:v", "libx265", "-preset", "medium", "-crf", crf,
                     "-x265-params", "log-level=error", "-tag:v", "hvc1"],
            "prores": ["-c:v", "prores_ks", "-profile:v", str(PRORES_PROFILES[opts.prores_profile]),
                       "-vendor", "apl0"],
        }[opts.codec]
        # Convert to video colour with the HD formula (BT.709) and say so in the file. ffmpeg's default is
        # the standard-definition formula, unlabelled, which players showing HD or 4K decode as BT.709:
        # colours came out slightly shifted, greens and reds most.
        convert = f"{scale}:out_color_matrix=bt709:out_range=tv,format={opts.pix_fmt}"
        tags = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
        self.process = subprocess.Popen(
            [ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", pix,
             "-s", f"{w}x{h}", "-framerate", str(fps), "-color_range", "pc", "-i", "-",
             "-vf", convert, *video_args, *tags, "-movflags", "+faststart", str(target)],
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
    d = np.diff(np.asarray(values, dtype=np.float64))
    d = d[np.isfinite(d)]
    return float(np.std(d) / np.sqrt(2)) if d.size > 1 else 0.0
