"""Lightroom / Camera Raw ``.xmp`` sidecars: find them, read and change exposure.

Learned from real Lightroom Classic sidecars (Canon EOS 80D, Camera Raw 16.x,
process version 15.4):

- The sidecar for ``IMG_1281.CR2`` is ``IMG_1281.xmp`` (the photo's name with
  its extension swapped). darktable's ``IMG_1281.CR2.xmp`` is a different
  format and is never touched. Copies such as ``IMG_1273_1.xmp`` belong to no
  photo and are ignored.
- Develop settings are attributes of the first ``rdf:Description``, e.g.
  ``crs:Exposure2012="+0.42"``. Masks carry their own, differently named
  settings (``crs:LocalExposure2012``) further down, and are left alone.
- Lightroom writes exposure with two decimals and a sign: ``0.00``, ``+0.42``,
  ``-1.20``. Writing the same way means an undo restores the file exactly.
- Files can have very long lines and mixed line endings, so they're edited
  as bytes: only the exposure value changes, nothing else moves.
"""

from __future__ import annotations

import re
from pathlib import Path

CAMERA_RAW_NS = b'xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"'
EXPOSURE_RANGE = (-5.0, 5.0)          # Lightroom's Exposure slider
_EXPOSURE = re.compile(rb'(\scrs:Exposure2012=")([^"]*)(")')


def xmp_path(photo: Path) -> Path:
    """Where Lightroom keeps (or would keep) this photo's settings."""
    for suffix in (".xmp", ".XMP", ".Xmp"):
        candidate = photo.with_suffix(suffix)
        if candidate.exists():
            return candidate
    return photo.with_suffix(".xmp")


def main_tag(data: bytes) -> tuple[int, int]:
    """Start and end of the first ``rdf:Description`` start tag, where the
    develop settings live. Quotes are respected, so a ``>`` inside a value
    can't end it early."""
    start = data.find(b"<rdf:Description")
    if start < 0:
        raise ValueError("no rdf:Description found")
    quote = None
    for i in range(start, len(data)):
        ch = data[i:i + 1]
        if quote:
            if ch == quote:
                quote = None
        elif ch in (b'"', b"'"):
            quote = ch
        elif ch == b">":
            return start, i
    raise ValueError("the settings tag never ends")


def has_camera_raw_settings(data: bytes) -> bool:
    try:
        start, end = main_tag(data)
    except ValueError:
        return False
    return CAMERA_RAW_NS in data[start:end]


def read_exposure(data: bytes) -> float | None:
    """The Exposure slider value, or ``None`` if the file doesn't carry one."""
    try:
        start, end = main_tag(data)
    except ValueError:
        return None
    m = _EXPOSURE.search(data, start, end)
    if not m:
        return None
    try:
        return float(m.group(2).decode("ascii"))
    except ValueError:
        return None


def format_exposure(stops: float) -> str:
    """Lightroom's own style: ``0.00``, ``+0.42``, ``-1.20``."""
    v = round(float(stops), 2)
    return "0.00" if v == 0 else f"{v:+.2f}"


def with_exposure(data: bytes, stops: float) -> bytes:
    """The same file with only the Exposure value changed."""
    lo, hi = EXPOSURE_RANGE
    if not lo <= stops <= hi:
        raise ValueError(f"An exposure of {stops:+.2f} is outside Lightroom's range ({lo:+.0f} to {hi:+.0f}).")
    start, end = main_tag(data)
    m = _EXPOSURE.search(data, start, end)
    if not m:
        raise ValueError("This file has no Exposure setting to change.")
    return data[:m.start(2)] + format_exposure(stops).encode("ascii") + data[m.end(2):]
