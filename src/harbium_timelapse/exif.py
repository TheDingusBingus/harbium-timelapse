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
import hashlib
import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable

from .programs import find_program
from .types import Frame

Progress = Callable[[int, int], None]

_EXIFTOOL_TAGS = [
    "-ExposureTime", "-FNumber", "-ISO", "-DateTimeOriginal",
    "-SubSecTimeOriginal", "-Model", "-ColorTempAsShot",
    "-WB_RGGBLevelsAsShot", "-AsShotNeutral",
]


def exiftool_available() -> bool:
    return find_program("exiftool") is not None


def read_frames(paths: list[Path], progress: Progress | None = None) -> list[Frame]:
    """Read settings for many files at once. Same order as ``paths``.

    Settings already read are remembered (see ``_SettingsCache``), so running
    a second command on the same folder doesn't read every file again: on a
    slow external drive that was minutes per command."""
    paths = [Path(p) for p in paths]
    have_exiftool = exiftool_available()
    cache = _SettingsCache(paths, have_exiftool)
    found: dict[str, Frame] = {}
    todo = [p for p in paths if cache.get(p) is None]
    if todo and have_exiftool:
        found = _read_with_exiftool(todo, progress)
    frames = []
    for p in paths:
        frame = cache.get(p)
        if frame is None:
            frame = found.get(_key(p)) or _read_with_exifread(p)
            cache.put(p, frame, "exiftool" if _key(p) in found else "exifread")
        frames.append(frame)
    if progress and not todo:
        progress(len(paths), len(paths))
    cache.save()
    return frames


class _SettingsCache:
    """Camera settings remembered between runs, per folder, in your user cache
    folder (never inside your photo folders).

    An entry is used only while the file's size and modified time are
    unchanged. Settings read without ExifTool are read again once ExifTool is
    installed. Any problem with the cache just means reading the files again.
    ``HARBIUM_NO_CACHE=1`` turns it off; ``HARBIUM_CACHE`` moves it."""

    def __init__(self, paths: list[Path], have_exiftool: bool) -> None:
        self.entries: dict[str, dict] = {}
        self.changed = False
        self.file: Path | None = None
        self.have_exiftool = have_exiftool
        if not paths or os.environ.get("HARBIUM_NO_CACHE"):
            return
        folder = os.path.normcase(os.path.abspath(str(paths[0].parent)))
        self.file = _cache_dir() / (hashlib.sha1(folder.encode("utf-8")).hexdigest()[:20] + ".json")
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            if data.get("version") == 1 and data.get("folder") == folder:
                self.entries = data.get("files", {})
        except (OSError, ValueError, AttributeError):
            self.entries = {}
        self.folder = folder

    @staticmethod
    def _stamp(path: Path) -> list | None:
        try:
            st = path.stat()
            return [st.st_size, st.st_mtime_ns]
        except OSError:
            return None

    def get(self, path: Path) -> Frame | None:
        entry = self.entries.get(path.name)
        if not entry or entry.get("stamp") != self._stamp(path):
            return None
        if entry.get("via") == "exifread" and self.have_exiftool:
            return None
        try:
            f = entry["frame"]
            return Frame(
                path=path,
                taken_at=datetime.fromisoformat(f["taken_at"]) if f.get("taken_at") else None,
                shutter=f.get("shutter"), aperture=f.get("aperture"), iso=f.get("iso"),
                camera=f.get("camera"), white_balance=f.get("white_balance"),
                white_balance_neutral=tuple(f["neutral"]) if f.get("neutral") else None,
            )
        except (KeyError, TypeError, ValueError):
            return None

    def put(self, path: Path, frame: Frame, via: str) -> None:
        if self.file is None:
            return
        stamp = self._stamp(path)
        if stamp is None:
            return
        self.entries[path.name] = {"stamp": stamp, "via": via, "frame": {
            "taken_at": frame.taken_at.isoformat() if frame.taken_at else None,
            "shutter": frame.shutter, "aperture": frame.aperture, "iso": frame.iso,
            "camera": frame.camera, "white_balance": frame.white_balance,
            "neutral": list(frame.white_balance_neutral) if frame.white_balance_neutral else None,
        }}
        self.changed = True

    def save(self) -> None:
        if self.file is None or not self.changed:
            return
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            temp = self.file.with_name(self.file.name + f".{os.getpid()}.tmp")
            temp.write_text(json.dumps({"version": 1, "folder": self.folder, "files": self.entries}),
                            encoding="utf-8")
            os.replace(temp, self.file)
        except OSError:
            pass                      # a cache that can't be saved just means reading again next time


def _cache_dir() -> Path:
    if os.environ.get("HARBIUM_CACHE"):
        return Path(os.environ["HARBIUM_CACHE"])
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "harbium-timelapse" / "cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "harbium-timelapse"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "harbium-timelapse"


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
        # Absolute paths: a file name starting with "-" can't then be read as an option.
        batch = [p.resolve() for p in paths[start:start + BATCH]]
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
                white_balance=_positive(rec.get("ColorTempAsShot")),
                white_balance_neutral=_neutral(rec),
            )
        if progress:
            progress(min(start + BATCH, len(paths)), len(paths))
    # Keep the caller's own Path objects, not ExifTool's spelling of them.
    by_key = {_key(p.resolve()): p for p in paths}
    return {k: dataclasses.replace(f, path=by_key[k]) for k, f in frames.items() if k in by_key}


def _exiftool_batch(batch: list[Path]) -> list[dict]:
    argfile = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".args", encoding="utf-8", delete=False) as f:
            f.write("\n".join(["-charset", "filename=utf8", *map(str, batch)]))
            argfile = f.name
        out = subprocess.run(
            [find_program("exiftool") or "exiftool", "-json", "-n", *_EXIFTOOL_TAGS, "-@", argfile],
            capture_output=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL,  # never let ExifTool sit waiting for a keypress
            timeout=BATCH_TIMEOUT, check=False,
        )
        if out.returncode not in (0, 1) and not out.stdout.strip():   # 1 = some files had no tags
            _note_problem(f"ExifTool stopped with an error: {out.stderr.strip()[:200] or out.returncode}")
        return json.loads(out.stdout or "[]")
    except subprocess.TimeoutExpired:
        _note_problem(f"ExifTool took over {BATCH_TIMEOUT} s on {len(batch)} files (a slow or sleeping "
                      "drive?) and was stopped")
        return []
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as e:
        _note_problem(f"ExifTool couldn't be run ({e})")
        return []
    finally:
        if argfile:
            Path(argfile).unlink(missing_ok=True)


def _key(path: Path | str) -> str:
    """Same file, same key, whichever way the slashes face or letters are cased."""
    return os.path.normcase(os.path.abspath(str(path)))


PROBLEMS: list[str] = []   # why ExifTool failed, if it did: shown by check, not swallowed


def _note_problem(text: str) -> None:
    if text not in PROBLEMS:
        PROBLEMS.append(text)


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

def _neutral(rec: dict) -> tuple[float, float, float] | None:
    """As-shot neutral (green = 1) from a DNG's AsShotNeutral or Canon's RGGB levels."""
    try:
        if rec.get("AsShotNeutral"):
            r, g, b = (float(v) for v in str(rec["AsShotNeutral"]).split()[:3])
            return (r / g, 1.0, b / g) if min(r, g, b) > 0 else None
        if rec.get("WB_RGGBLevelsAsShot"):
            r, g1, g2, b = (float(v) for v in str(rec["WB_RGGBLevelsAsShot"]).split()[:4])
            g = (g1 + g2) / 2
            # levels are multipliers; the neutral is their reciprocal, scaled to green
            return (g / r, 1.0, g / b) if min(r, g, b) > 0 else None
    except (ValueError, TypeError):
        return None
    return None


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
