"""The export window's settings, presets, checks and estimates, in plain Python.

Presets cover most people in one click; everything else is an advanced option.
Changing any option turns the preset into "Custom", which can be saved under a
name. Nothing about a project's size or quality is capped.

Estimates are honest approximations: file sizes come from typical rates for
each format (H.264 calibrated on James's own exports), times from this
computer's measured speed on earlier exports of the same kind, remembered in
the app's preferences.
"""

from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

from ...app import config
from ...finish import VideoOptions

RESOLUTIONS = {"native": None, "8K": 7680, "6K": 6144, "4K": 3840, "1440p": 2560, "1080p": 1920}
FRAME_RATES = (23.976, 24.0, 25.0, 29.97, 30.0, 50.0, 60.0)

# Approximate bytes per pixel per frame at each format's default quality. H.264: James's Share export
# (4K, CRF 16) was 90 MB for 229 frames, ~0.04 bytes a pixel. H.265 at its default is typically ~40% smaller.
# ProRes rates from Apple's published data rates (422 HQ ~220 Mb/s at 1080p29.97, and so on).
BYTES_PER_PIXEL = {"h264": 0.040, "h265": 0.024,
                   "prores:proxy": 0.09, "prores:lt": 0.21, "prores:standard": 0.29, "prores:hq": 0.44,
                   "prores:4444": 0.66}
DEFAULT_CRF = {"h264": 16, "h265": 18}


@dataclass
class ExportSettings:
    preset: str = "share"
    output: str = "video"                 # video, frames or both
    codec: str = "h264"
    quality: int | None = None            # CRF for H.264 / H.265; None = the default
    bit_depth: int | None = None
    chroma: str | None = None
    prores_profile: str = "hq"
    resolution: str = "4K"                # a name from RESOLUTIONS, or "custom"
    custom_width: int | None = None
    fps: float = 24.0
    dither: bool = True
    first: int | None = None              # 1-based, inclusive
    last: int | None = None
    frames_format: str = "jpg"            # jpg, png or tif
    jpeg_quality: int = 95
    video_path: str | None = None
    frames_folder: str | None = None

    @property
    def video(self) -> bool:
        return self.output in ("video", "both")

    @property
    def frames(self) -> bool:
        return self.output in ("frames", "both")

    def options(self) -> VideoOptions:
        return VideoOptions(codec=self.codec, quality=self.quality, bit_depth=self.bit_depth, chroma=self.chroma,
                            prores_profile=self.prores_profile)

    @property
    def suffix(self) -> str:
        return ".mov" if self.codec == "prores" else ".mp4"

    def width_limit(self) -> int | None:
        if self.resolution == "custom":
            return self.custom_width
        return RESOLUTIONS.get(self.resolution)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ExportSettings":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


BUILT_IN = {
    "share": {"label": "Share", "detail": "H.264, up to 4K: plays on anything",
              "settings": {"output": "video", "codec": "h264", "resolution": "4K", "quality": None, "chroma": None,
                           "bit_depth": None}},
    "master": {"label": "Master", "detail": "H.265 10-bit, up to 4K: smoother skies, smaller files",
               "settings": {"output": "video", "codec": "h265", "resolution": "4K", "quality": None, "chroma": None,
                            "bit_depth": 10}},
    "edit": {"label": "Edit", "detail": "ProRes 422 HQ at full size: near-lossless, for a video editor",
             "settings": {"output": "video", "codec": "prores", "prores_profile": "hq", "resolution": "native"}},
    "frames": {"label": "Frames", "detail": "Corrected frames as high-quality JPEGs, for stills or a photo series",
               "settings": {"output": "frames", "frames_format": "jpg", "jpeg_quality": 98}},
}

SETTING_KEYS = ("output", "codec", "quality", "bit_depth", "chroma", "prores_profile", "resolution", "custom_width",
                "fps", "dither", "frames_format", "jpeg_quality")


def user_presets() -> dict[str, dict]:
    presets = config.preferences().get("export_presets", {})
    return presets if isinstance(presets, dict) else {}


def save_user_preset(name: str, settings: ExportSettings) -> None:
    name = name.strip()
    if not name:
        raise ValueError("Give the preset a name.")
    if name.lower() in BUILT_IN:
        raise ValueError(f"{name} is a built-in preset's name; choose another.")
    prefs = config.preferences()
    presets = prefs.get("export_presets", {}) if isinstance(prefs.get("export_presets"), dict) else {}
    presets[name] = {k: getattr(settings, k) for k in SETTING_KEYS}
    prefs["export_presets"] = presets
    config.save_preferences(prefs)


def delete_user_preset(name: str) -> None:
    prefs = config.preferences()
    presets = prefs.get("export_presets", {})
    if isinstance(presets, dict) and name in presets:
        del presets[name]
        config.save_preferences(prefs)


def apply_preset(settings: ExportSettings, name: str) -> ExportSettings:
    values = BUILT_IN[name]["settings"] if name in BUILT_IN else user_presets().get(name)
    if values is None:
        raise ValueError(f"There's no preset called {name}.")
    out = replace(settings, preset=name, **{k: v for k, v in values.items() if k in SETTING_KEYS})
    if out.video_path:
        out.video_path = str(Path(out.video_path).with_suffix(out.suffix))
    return out


# --- checks ----------------------------------------------------------------------------------------

def problems(s: ExportSettings, frame_count: int, export_folder: Path | None) -> list[str]:
    """What stops this export from running, in plain words. Empty means ready."""
    out = []
    if s.video:
        try:
            s.options().check()
        except ValueError as e:
            out.append(str(e))
        if not s.video_path:
            out.append("Choose where to save the video.")
        elif Path(s.video_path).suffix.lower() != s.suffix:
            out.append(f"This video format needs a file ending in {s.suffix}.")
        if s.resolution == "custom" and not (s.custom_width and s.custom_width >= 16):
            out.append("Give a width of at least 16 pixels.")
    if s.frames:
        if not s.frames_folder:
            out.append("Choose a folder for the corrected frames.")
        elif export_folder and Path(s.frames_folder).resolve() == Path(export_folder).resolve():
            out.append("The corrected frames need their own folder, not the one they come from.")
        if not 1 <= s.jpeg_quality <= 100:
            out.append("JPEG quality must be between 1 and 100.")
    for path in (s.video_path if s.video else None, s.frames_folder if s.frames else None):
        if path and "\u2026" in path:
            out.append(f'{path} contains "…", which looks like a shortened example. Give the full path.')
    a, b = s.first or 1, s.last or frame_count
    if not 1 <= a < b <= frame_count:
        out.append(f"The frame range must be within 1 to {frame_count}, with the first before the last.")
    return out


def notes(s: ExportSettings, out_width: int | None) -> list[str]:
    """Things worth knowing about this choice that don't stop it."""
    out = s.options().notes(out_width) if s.video else []
    if s.frames and s.frames_format == "tif":
        out.append("16-bit TIFF frames are large: several times the size of JPEGs.")
    return out


# --- estimates -----------------------------------------------------------------------------------------

def output_size(s: ExportSettings, source_width: int, source_height: int) -> tuple[int, int]:
    limit = s.width_limit()
    w = min(limit, source_width) if limit else source_width
    return w // 2 * 2, round(source_height * w / source_width / 2) * 2


def estimate_bytes(s: ExportSettings, source_width: int, source_height: int, frames: int,
                   average_frame_bytes: float) -> int:
    total = 0.0
    if s.video:
        w, h = output_size(s, source_width, source_height)
        key = f"prores:{s.prores_profile}" if s.codec == "prores" else s.codec
        per_frame = BYTES_PER_PIXEL[key] * w * h
        if s.codec in DEFAULT_CRF and s.quality is not None:
            per_frame *= 2 ** ((DEFAULT_CRF[s.codec] - s.quality) / 6)        # about twice the size per 6 CRF
        if s.codec != "prores" and (s.chroma or "420") != "420":
            per_frame *= 1.5 if s.chroma == "422" else 2.0
        total += per_frame * frames
    if s.frames:
        if s.frames_format == "jpg":
            total += average_frame_bytes * frames * (1.0 + max(0, s.jpeg_quality - 95) * 0.12)
        elif s.frames_format == "png":
            total += source_width * source_height * 3 * 0.6 * frames
        else:
            total += source_width * source_height * 6 * 0.7 * frames
    return int(total)


def speed_key(s: ExportSettings) -> str:
    parts = []
    if s.video:
        parts.append(f"video:{s.codec}")
    if s.frames:
        parts.append(f"frames:{s.frames_format}")
    return "+".join(parts)


def remember_speed(s: ExportSettings, seconds_per_frame: float) -> None:
    prefs = config.preferences()
    speeds = prefs.get("export_speeds", {}) if isinstance(prefs.get("export_speeds"), dict) else {}
    speeds[speed_key(s)] = round(float(seconds_per_frame), 3)
    prefs["export_speeds"] = speeds
    config.save_preferences(prefs)


def estimate_seconds(s: ExportSettings, frames: int) -> float | None:
    """From this computer's speed on an earlier export of the same kind; None until there's been one."""
    speeds = config.preferences().get("export_speeds", {})
    per = speeds.get(speed_key(s)) if isinstance(speeds, dict) else None
    return per * frames if per else None


def free_bytes(path: str | Path | None) -> int | None:
    if not path:
        return None
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return None


def describe(bytes_: int) -> str:
    gb = bytes_ / 1e9
    if gb >= 1:
        return f"{gb:.1f} GB"
    return f"{bytes_ / 1e6:.0f} MB" if bytes_ >= 1e6 else "under 1 MB"
