"""Finding the outside programs this tool runs (ExifTool, ffmpeg, RapidRAW).

Only the folders on PATH are searched, never the current folder. On Windows
a bare program name can otherwise be found in the current folder first, so a
program file planted in a folder of downloaded photos could run instead of
the real one.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def find_program(name: str) -> str | None:
    """Full path of ``name`` on PATH, or ``None``. The current folder is not searched."""
    extensions = [""]
    if sys.platform.startswith("win"):
        extensions = [e.lower() for e in os.environ.get("PATHEXT", ".EXE;.BAT;.CMD").split(";") if e] + [""]
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        if not folder.strip() or folder.strip() in (".", ""):
            continue
        base = Path(folder.strip().strip('"'))
        if not base.is_absolute():
            continue                       # a relative PATH entry means "somewhere under here": skip
        for ext in extensions:
            candidate = base / (name + ext)
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return None
