"""What this tool keeps in ``.timelapse-core/`` beside your photos.

- ``originals/``: a copy of each sidecar, taken the first time a command
  changes its edit (blend or deflicker). ``restore`` puts them all back.
  Purple labels are left as they are.
- ``rapidraw.json``: the deflicker ledger, so a second run replaces the
  first rather than stacking on top of it.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .sidecar import sidecar_path

STATE_DIR = ".timelapse-core"
LEDGER = "rapidraw.json"


def keep_original(image: Path) -> None:
    """Copy the sidecar aside before its first change. Later calls do nothing."""
    side = sidecar_path(image)
    backup = image.parent / STATE_DIR / "originals" / side.name
    if side.exists() and not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(side, backup)


def restore(folder: Path | str) -> int:
    """Put every sidecar back exactly as it was before this tool touched it."""
    folder = Path(folder)
    originals = folder / STATE_DIR / "originals"
    count = 0
    if originals.is_dir():
        for backup in originals.iterdir():
            shutil.copy2(backup, folder / backup.name)
            count += 1
    clear_ledger(folder)
    return count


def read_ledger(folder: Path) -> dict:
    path = folder / STATE_DIR / LEDGER
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("frames", {})


def write_ledger(folder: Path, frames: dict) -> None:
    path = folder / STATE_DIR / LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "frames": frames}, indent=2), encoding="utf-8")


def clear_ledger(folder: Path) -> None:
    (folder / STATE_DIR / LEDGER).unlink(missing_ok=True)
