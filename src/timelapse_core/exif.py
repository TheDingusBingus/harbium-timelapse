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

import dataclasses
import json
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable

from .types import Frame

Progress = Callable[[int, int], None]

_EXIFTOOL_TAGS = [
    "-ExposureTime", "-FNumber", "-ISO", "-DateTimeOriginal",
    "-SubSecTimeOriginal", "-Model",
]


def exiftool_available() -> bool:
    return shutil.which("exiftool") is not None


def read_frames(paths: list[Path], progress: Progress | None = None) -> list[Frame]:
    """Read settings for many files at once. Same order as ``paths``."""
    paths = [Path(p) for p in paths]
    found: dict[str, Frame] = {}
    if paths and exiftool_available():
        found = _read_with_exiftool(paths, progress)
    return [found.get(_key(p)) or _read_with_exifread(p) for p in paths]


def read_frame(path: Path | str) -> Frame:
    return read_frames([Path(path)])[0]


# --- ExifTool ---------------------------------------------------------------

BATCH = 100        # files per ExifTool call
BATCH_TIMEOUT = 300  # seconds; a slow external drive can need a while


def _read_with_exiftool(paths: list[Path], progress: Progress | None) -> dict[str, Frame]:
    """File names go to ExifTool through an argument file, in batches.

    Putting hundreds of long paths on one command line fails on Windows in
    ways that can hang rather than error. An argument file is ExifTool's own
    recommended route, and it also carries non-English characters in folder
    names safely (UTF-8 both ways).
    """
    frames: dict[str, Frame] = {}
    for start in range(0, len(paths), BATCH):
        batch = paths[start:start + BATCH]
        for rec in _exiftool_batch(batch):
            source = rec.get("SourceFile")
            if not source:
                continue
            path = Path(source)
            frames[_key(path)] = Frame(
                path=path,
                taken_at=_parse_time(rec.get("DateTimeOriginal"), rec.get("SubSecTimeOriginal")),
                shutter=_positive(rec.get("ExposureTime")),
                aperture=_positive(rec.get("FNumber")),
                iso=_positive(rec.get("ISO")),
                camera=_text(rec.get("Model")),
            )
        if progress:
            progress(min(start + BATCH, len(paths)), len(paths))
    # Keep the caller's own Path objects, not ExifTool's spelling of them.
    by_key = {_key(p): p for p in paths}
    return {k: dataclasses.replace(f, path=by_key[k]) for k, f in frames.items() if k in by_key}


def _exiftool_batch(batch: list[Path]) -> list[dict]:
    argfile = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".args", encoding="utf-8", delete=False) as f:
            f.write("\n".join(["-charset", "filename=utf8", *map(str, batch)]))
            argfile = f.name
        out = subprocess.run(
            ["exiftool", "-json", "-n", *_EXIFTOOL_TAGS, "-@", argfile],
            capture_output=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL,  # never let ExifTool sit waiting for a keypress
            timeout=BATCH_TIMEOUT, check=False,
        )
        return json.loads(out.stdout or "[]")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []
    finally:
        if argfile:
            Path(argfile).unlink(missing_ok=True)


def _key(path: Path | str) -> str:
    """Same file, same key, whichever way the slashes face or letters are cased."""
    return os.path.normcase(os.path.abspath(str(path)))


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
