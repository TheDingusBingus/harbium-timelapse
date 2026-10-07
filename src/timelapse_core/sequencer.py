"""Turn a folder of photographs into an ordered sequence, and check it.

Run ``check_sequence`` before anything else. Problems are much cheaper to
find here than in a rendered video.
"""

from __future__ import annotations

import re
from pathlib import Path
from statistics import median

from .exif import exiftool_available, read_frames
from .types import Frame, Problem, Sequence

RAW_EXTENSIONS = {
    ".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".raf",
    ".orf", ".rw2", ".pef", ".dng", ".3fr", ".iiq", ".x3f", ".srw",
}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".tif", ".tiff", ".png"}
SUPPORTED = RAW_EXTENSIONS | IMAGE_EXTENSIONS


def load_sequence(folder: Path | str, progress=None) -> Sequence:
    """Find every photo in ``folder`` and put them in shooting order.

    Ordered by capture time where every frame has one, which survives the
    camera's file counter rolling over from 9999 to 0001. Otherwise ordered
    by filename, with numbers compared as numbers (IMG_2 before IMG_10).

    Hidden files are skipped, including the ``._IMG_0001.CR2`` copies macOS
    leaves on external drives.

    ``progress(done, total)`` is called as camera settings are read.
    """
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError(f"Can't find a folder at {folder}")

    paths = [
        p for p in folder.iterdir()
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in SUPPORTED
    ]
    if not paths:
        raise ValueError(f"No photos found in {folder}")

    frames = read_frames(sorted(paths, key=_natural_key), progress=progress)
    if all(f.taken_at for f in frames):
        frames.sort(key=lambda f: (f.taken_at, _natural_key(f.path)))
    return Sequence(folder=folder, frames=tuple(frames))


def check_sequence(sequence: Sequence) -> list[Problem]:
    """Everything worth knowing before processing, in plain language."""
    problems: list[Problem] = []
    frames = sequence.frames

    if len(frames) < 2:
        problems.append(Problem("too_short", "A timelapse needs at least two frames.", serious=True))
        return problems

    extensions = {f.path.suffix.lower() for f in frames}
    if len(extensions) > 1:
        kinds = ", ".join(sorted(e.lstrip(".").upper() for e in extensions))
        problems.append(Problem(
            "mixed_files",
            f"This folder mixes file types ({kinds}). If you shot RAW+JPEG, every "
            "moment is in here twice. Move one type to its own folder.",
            serious=True,
        ))

    cameras = {f.camera for f in frames if f.camera}
    if len(cameras) > 1:
        problems.append(Problem(
            "mixed_cameras",
            f"Frames from more than one camera: {', '.join(sorted(cameras))}.",
            serious=True,
        ))

    problems += _check_settings_readable(frames)
    problems += _check_timing(frames)
    problems += _check_settings_changes(frames)
    return problems


# --- checks -----------------------------------------------------------------

def _check_settings_readable(frames: tuple[Frame, ...]) -> list[Problem]:
    missing = [f for f in frames if f.exposure_value is None]
    if not missing:
        return []
    hint = ""
    if any(f.path.suffix.lower() == ".cr3" for f in missing) and not exiftool_available():
        hint = " Canon CR3 files need ExifTool installed (free: exiftool.org)."
    return [Problem(
        "unknown_settings",
        f"Couldn't read shutter, aperture or ISO from {len(missing)} of {len(frames)} "
        f"frames. Deflicker still works, but setting changes can't be spotted.{hint}",
    )]


def _check_timing(frames: tuple[Frame, ...]) -> list[Problem]:
    if not all(f.taken_at for f in frames):
        return [Problem(
            "missing_time",
            "Some frames have no capture time, so they're ordered by filename. "
            "Gaps can't be detected.",
        )]

    gaps = [(b.taken_at - a.taken_at).total_seconds() for a, b in zip(frames, frames[1:])]
    usual = median(gaps)
    if usual <= 0:
        return []

    problems: list[Problem] = []
    for i, gap in enumerate(gaps):
        if gap > usual * 1.5:
            missing = round(gap / usual) - 1
            problems.append(Problem(
                "gap",
                f"About {missing} frame{'s' if missing != 1 else ''} may be missing between "
                f"{frames[i].name} and {frames[i + 1].name} ({_seconds(gap)} gap, "
                f"usual interval {_seconds(usual)}).",
                frame=i + 1,
            ))
        elif gap == 0:
            problems.append(Problem(
                "same_time",
                f"{frames[i].name} and {frames[i + 1].name} have the same capture time. "
                "Possibly a duplicate.",
                frame=i + 1,
            ))
    return problems


def _check_settings_changes(frames: tuple[Frame, ...]) -> list[Problem]:
    problems: list[Problem] = []
    for i, (a, b) in enumerate(zip(frames, frames[1:]), start=1):
        if a.exposure_value is None or b.exposure_value is None:
            continue
        shift = a.exposure_value - b.exposure_value  # positive = b gets more light
        if abs(shift) < 0.05:
            continue
        changed = []
        if a.shutter != b.shutter:
            changed.append(f"shutter {_shutter(a.shutter)} → {_shutter(b.shutter)}")
        if a.aperture != b.aperture:
            changed.append(f"aperture f/{a.aperture:g} → f/{b.aperture:g}")
        if a.iso != b.iso:
            changed.append(f"ISO {a.iso:g} → {b.iso:g}")
        problems.append(Problem(
            "settings_change",
            f"Exposure changes at {b.name}: {', '.join(changed)} "
            f"({format_stops(shift)} {'brighter' if shift > 0 else 'darker'}).",
            frame=i,
        ))
    return problems


# --- formatting -------------------------------------------------------------

def format_stops(stops: float) -> str:
    """0.333 -> '1/3 stop', 1.667 -> '1 2/3 stops'. Rounded to thirds."""
    thirds = round(abs(stops) * 3)
    whole, part = divmod(thirds, 3)
    text = " ".join(filter(None, [str(whole) if whole else "", f"{part}/3" if part else ""])) or "0"
    return f"{text} stop" + ("" if thirds <= 3 else "s")


def _shutter(seconds: float | None) -> str:
    if not seconds:
        return "?"
    return f"1/{round(1 / seconds)}" if seconds < 1 else f"{seconds:g}s"


def _seconds(s: float) -> str:
    return f"{s:.1f}s" if s < 10 else f"{s:.0f}s"


def _natural_key(path: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path.name)]
