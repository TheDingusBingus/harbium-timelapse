"""What this tool keeps in ``.timelapse-core/`` beside your photos.

- ``originals/``: a copy of each sidecar, taken the first time a command
  changes its edit (blend or deflicker). ``restore`` puts them all back.
  Purple labels are left as they are.
- ``rapidraw.json``: the deflicker ledger, so a second run replaces the
  first rather than stacking on top of it.
"""

from __future__ import annotations

import json
import os
import shutil
from contextlib import contextmanager
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


class LedgerDamaged(ValueError):
    """The tool's record of what it changed can't be read."""


def read_json(path: Path, what: str) -> dict:
    """Read one of the tool's own JSON files, explaining (not crashing) if damaged."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        return data
    except (ValueError, UnicodeDecodeError) as e:
        raise LedgerDamaged(
            f"The tool's record of {what} ({path}) is damaged, probably by a crash or power cut "
            f"while it was being saved ({e}). Your photos are fine. To start clean: run "
            f"'timelapse-rapidraw restore FOLDER' (every sidecar back as it was before this tool), "
            f"or delete that file to keep the sidecars as they are now.") from None


def write_json(path: Path, data: dict, indent: int | None = 2) -> None:
    """Write atomically: a crash mid-write never leaves a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, indent=indent), encoding="utf-8")
    os.replace(temp, path)


def read_ledger(folder: Path) -> dict:
    path = Path(folder) / STATE_DIR / LEDGER
    if not path.exists():
        return {}
    frames = read_json(path, "what it changed").get("frames", {})
    if not isinstance(frames, dict):
        raise LedgerDamaged(f"The tool's record ({path}) doesn't look right. Run restore, or delete it.")
    return frames


def write_ledger(folder: Path, frames: dict) -> None:
    write_json(Path(folder) / STATE_DIR / LEDGER, {"version": 1, "frames": frames})


@contextmanager
def ledger_kept_in_step(folder: Path):
    """The ledger, saved on the way out however the block ends (done, error,
    Ctrl+C). Update its entry right after each sidecar is written, and the
    record always matches the files, so an interrupted run can't make the
    next one stack corrections on corrections."""
    ledger = read_ledger(folder)
    try:
        yield ledger
    finally:
        if ledger:
            write_ledger(folder, ledger)
        else:
            (Path(folder) / STATE_DIR / LEDGER).unlink(missing_ok=True)   # nothing recorded: no file


def clear_ledger(folder: Path) -> None:
    """Forget deflicker, and any refine passes built on it."""
    (folder / STATE_DIR / LEDGER).unlink(missing_ok=True)
    (folder / STATE_DIR / "refine.json").unlink(missing_ok=True)
