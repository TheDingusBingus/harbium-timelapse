"""Measurements remembered between runs, so a folder is measured once.

Measuring is the slow part: every frame has to be read and decoded. The
results are a few numbers per frame, so they're kept in the user cache folder
(beside the camera-settings cache, never in photo folders) and reused while
nothing has changed.

What "nothing has changed" means: the same files, with the same names, sizes
and modified times, measured the same way. The key covers every file and the
measuring method's version (``METHOD``), so editing, adding or removing any
frame, or improving the measurement code, measures again. Brightness is
measured frame against frame (each step compares neighbours), so results are
kept for the folder as a whole rather than frame by frame.

Stored as plain numpy arrays loaded with ``allow_pickle=False``: a cache file
can only ever hold numbers, never code. A missing, damaged or mismatched file
just means measuring again. ``HARBIUM_NO_CACHE=1`` turns it off.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np

METHOD = "regional-vote-1"          # bump whenever measuring changes what it produces


def _folder() -> Path:
    from .exif import _cache_dir
    return _cache_dir() / "measurements"


def key(paths: list[Path], kind: str, extra: str = "") -> str | None:
    """A fingerprint of these files as they are now, or None if one can't be read."""
    h = hashlib.sha256(f"{METHOD}|{kind}|{extra}".encode())
    for p in paths:
        try:
            st = Path(p).stat()
        except OSError:
            return None
        h.update(f"|{Path(p).resolve()}|{st.st_size}|{st.st_mtime_ns}".encode())
    return h.hexdigest()[:32]


def load(paths: list[Path], kind: str, extra: str = "") -> dict[str, np.ndarray] | None:
    if os.environ.get("HARBIUM_NO_CACHE"):
        return None
    k = key(paths, kind, extra)
    if k is None:
        return None
    try:
        with np.load(_folder() / f"{k}.npz", allow_pickle=False) as data:
            arrays = {name: data[name] for name in data.files}
    except (OSError, ValueError, KeyError):
        return None
    first = next(iter(arrays.values()), None)
    if first is None or len(first) != len(paths):
        return None                                # not what it claims to be: measure again
    return arrays


def save(paths: list[Path], kind: str, arrays: dict[str, np.ndarray], extra: str = "") -> None:
    if os.environ.get("HARBIUM_NO_CACHE"):
        return
    k = key(paths, kind, extra)
    if k is None:
        return
    folder = _folder()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        temp = folder / f"{k}.{os.getpid()}.tmp.npz"
        np.savez(temp, **{name: np.asarray(a) for name, a in arrays.items()})
        os.replace(temp, folder / f"{k}.npz")
    except OSError:
        pass                                       # not being able to remember is never an error
