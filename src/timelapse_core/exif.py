"""Read camera settings from each frame.

Strategy:
1. ExifTool, if it's installed. One call for the whole folder. Reads every
   RAW format including Canon CR3, which exifread cannot.
2. exifread (pure Python) for anything ExifTool didn't cover.
3. If neither works, the Frame comes back with ``None`` settings and the
   sequencer says so in plain language. Nothing crashes.

This module only reads. Writing changes is an integration's job (sidecars),
never the core's. The core never modifies an original file.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from .types import Frame

_EXIFTOOL_TAGS = [
    "-ExposureTime", "-FNumber", "-ISO", "-DateTimeOriginal",
    "-SubSecTimeOriginal", "-Model",
]


def exiftool_available() -> bool:
    return shutil.which("exiftool") is not None


def read_frames(paths: list[Path]) -> list[Frame]:
    """Read settings for many files at once. Same order as ``paths``."""
    paths = [Path(p) for p in paths]
    found: dict[Path, Frame] = {}
    if paths and exiftool_available():
        found = _read_with_exiftool(paths)
    return [found.get(p) or _read_with_exifread(p) for p in paths]


def read_frame(path: Path | str) -> Frame:
    return read_frames([Path(path)])[0]


# --- ExifTool ---------------------------------------------------------------

def _read_with_exiftool(paths: list[Path]) -> dict[Path, Frame]:
    try:
        out = subprocess.run(
            ["exiftool", "-json", "-n", *_EXIFTOOL_TAGS, *map(str, paths)],
            capture_output=True, text=True, timeout=600, check=False,
        )
        records = json.loads(out.stdout or "[]")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}

    by_name = {str(p): p for p in paths}
    frames: dict[Path, Frame] = {}
    for rec in records:
        path = by_name.get(rec.get("SourceFile", ""))
        if path is None:
            continue
        frames[path] = Frame(
            path=path,
            taken_at=_parse_time(rec.get("DateTimeOriginal"), rec.get("SubSecTimeOriginal")),
            shutter=_positive(rec.get("ExposureTime")),
            aperture=_positive(rec.get("FNumber")),
            iso=_positive(rec.get("ISO")),
            camera=_text(rec.get("Model")),
        )
    return frames


# --- exifread ---------------------------------------------------------------

def _read_with_exifread(path: Path) -> Frame:
    import exifread

    # exifread logs a warning for every file it can't parse (every CR3, for a
    # start). Hundreds of those would bury the messages that matter. The
    # sequencer reports unreadable settings once, in a sentence.
    logging.getLogger("exifread").setLevel(logging.ERROR)
    try:
        with open(path, "rb") as f:
            tags = exifread.process_file(f, details=False)
    except Exception:  # unreadable or unsupported: settings unknown, not fatal
        return Frame(path=path)

    def first(*names: str):
        for name in names:
            tag = tags.get(name)
            if tag is not None and getattr(tag, "values", None):
                return tag.values[0] if isinstance(tag.values, list) else tag.values
        return None

    return Frame(
        path=path,
        taken_at=_parse_time(
            _text(first("EXIF DateTimeOriginal", "Image DateTime")),
            _text(first("EXIF SubSecTimeOriginal")),
        ),
        shutter=_positive(first("EXIF ExposureTime")),
        aperture=_positive(first("EXIF FNumber")),
        iso=_positive(first("EXIF ISOSpeedRatings", "EXIF PhotographicSensitivity")),
        camera=_text(first("Image Model")),
    )


# --- helpers ----------------------------------------------------------------

def _positive(value) -> float | None:
    if isinstance(value, (tuple, list)):
        value = value[0] if value else None
    try:
        number = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return number if number > 0 else None


def _text(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip().strip("\x00").strip()
    return s or None


def _parse_time(stamp, subsec=None) -> datetime | None:
    if not stamp:
        return None
    try:
        when = datetime.strptime(str(stamp)[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    digits = "".join(ch for ch in str(subsec or "") if ch.isdigit())
    if digits:
        when = when.replace(microsecond=int(digits[:6].ljust(6, "0")))
    return when
