"""RapidRAW's .rrdata sidecars: find, read, write, and nothing more.

Verified against real sidecars from RapidRAW (October 2026):

- Named ``<image file name>.rrdata``, e.g. ``181A0968.CR3.rrdata``.
  Virtual copies are ``181A0968.CR3.<6 hex>.rrdata`` and are left alone.
- JSON, two-space indent, no trailing newline, non-ASCII written as-is.
- ``adjustments.exposure`` is in stops, exactly as the slider shows it, and
  is a linear gain (``colour × 2^exposure``) ahead of the tone mapper.
- ``exif`` holds the frame's camera settings as text.

Writing rule: change ``adjustments.exposure`` and nothing else. Every other
byte of the file must come back as it was.
"""

from __future__ import annotations

import dataclasses
import json
import os
from datetime import datetime
from pathlib import Path

from timelapse_core import Frame

SUFFIX = ".rrdata"


def sidecar_path(image: Path) -> Path:
    return image.with_name(image.name + SUFFIX)


def read_sidecar(image: Path) -> dict | None:
    """The sidecar's contents, or ``None`` if RapidRAW hasn't made one yet."""
    path = sidecar_path(image)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def dump(data: dict) -> str:
    """Serialise exactly the way RapidRAW does."""
    return json.dumps(data, indent=2, ensure_ascii=False)


def write_sidecar(image: Path, data: dict) -> None:
    """Write atomically: a crash mid-write never leaves a half-written sidecar."""
    path = sidecar_path(image)
    temp = path.with_name(path.name + ".tmp")  # doesn't match RapidRAW's *.rrdata scan
    temp.write_text(dump(data), encoding="utf-8")
    os.replace(temp, path)


def get_exposure(data: dict) -> float:
    return float((data.get("adjustments") or {}).get("exposure") or 0.0)


def set_exposure(data: dict, stops: float) -> None:
    value = round(float(stops), 4)
    # RapidRAW writes whole numbers without a decimal point (1, not 1.0). Match it,
    # so an undo leaves the file byte-for-byte as it was.
    data.setdefault("adjustments", {})["exposure"] = int(value) if value == int(value) else value


def with_sidecar_settings(frame: Frame, data: dict | None) -> Frame:
    """Fill any settings the core couldn't read (e.g. CR3 without ExifTool)
    from the EXIF copy RapidRAW keeps in the sidecar. Never overrides."""
    exif = (data or {}).get("exif") or {}
    if not exif:
        return frame
    return dataclasses.replace(
        frame,
        shutter=frame.shutter or _shutter(exif.get("ExposureTime")),
        aperture=frame.aperture or _number(str(exif.get("FNumber", "")).removeprefix("f/")),
        iso=frame.iso or _number(exif.get("ISOSpeedRatings") or exif.get("PhotographicSensitivity")),
        taken_at=frame.taken_at or _time(exif.get("DateTimeOriginal"), exif.get("SubSecTimeOriginal")),
        camera=frame.camera or (str(exif["Model"]).strip() if exif.get("Model") else None),
    )


# --- parsing RapidRAW's EXIF text -------------------------------------------

def _number(text) -> float | None:
    try:
        value = float(str(text).strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _shutter(text) -> float | None:
    """'1/320 s' -> 0.003125, '30 s' -> 30.0"""
    if not text:
        return None
    t = str(text).replace("s", "").strip()
    if "/" in t:
        top, bottom = t.split("/", 1)
        top, bottom = _number(top), _number(bottom)
        return top / bottom if top and bottom else None
    return _number(t)


def _time(stamp, subsec) -> datetime | None:
    if not stamp:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S"):
        try:
            when = datetime.strptime(str(stamp)[:19], fmt)
            break
        except ValueError:
            continue
    else:
        return None
    digits = "".join(c for c in str(subsec or "") if c.isdigit())
    return when.replace(microsecond=int(digits[:6].ljust(6, "0"))) if digits else when


def get_temperature(data: dict) -> float:
    return float((data.get("adjustments") or {}).get("temperature") or 0.0)


def set_temperature(data: dict, value: float) -> None:
    v = round(float(value), 2)
    data.setdefault("adjustments", {})["temperature"] = int(v) if v == int(v) else v
