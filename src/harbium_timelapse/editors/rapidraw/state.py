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
import sys
import time
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
    with folder_lock(folder):
        if originals.is_dir():
            for backup in originals.iterdir():
                if backup.name.endswith(".tmp"):
                    continue
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


LOCK = "lock"
STALE_AFTER = 6 * 3600          # seconds: a lock older than this is from a run that died
_held: dict[str, int] = {}      # locks this process holds (so nested steps don't lock themselves out)


@contextmanager
def folder_lock(folder: Path):
    """Only one run changes a folder at a time. A second run is refused with an
    explanation; a lock left by a run that crashed is noticed and cleared."""
    key = os.path.normcase(os.path.abspath(str(folder)))
    if _held.get(key):
        _held[key] += 1
        try:
            yield
        finally:
            _held[key] -= 1
        return
    path = Path(folder) / STATE_DIR / LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in (1, 2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            pid, started = _lock_owner(path)
            if attempt == 1 and (time.time() - started > STALE_AFTER or not _alive(pid)):
                path.unlink(missing_ok=True)          # left by a run that's no longer going
                continue
            when = time.strftime("%H:%M", time.localtime(started)) if started else "earlier"
            raise ValueError(
                f"Another run is changing this folder right now (started {when}). Wait for it to "
                f"finish. If nothing else is running, delete {path} and try again.") from None
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps({"pid": os.getpid(), "started": time.time()}))
    _held[key] = 1
    try:
        yield
    finally:
        _held.pop(key, None)
        path.unlink(missing_ok=True)


def _lock_owner(path: Path) -> tuple[int, float]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data.get("pid", 0)), float(data.get("started", 0))
    except (OSError, ValueError, TypeError):
        return 0, 0.0


def _alive(pid: int) -> bool:
    """Is that process still running? (Unsure counts as yes: safer.)"""
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if sys.platform.startswith("win"):
        try:
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # query-limited
            if not handle:
                return False
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(handle)
            return code.value == 259                                          # STILL_ACTIVE
        except Exception:
            return True
    try:
        os.kill(pid, 0)                     # POSIX: signal 0 only checks; never used on Windows
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return True


@contextmanager
def ledger_kept_in_step(folder: Path):
    """The ledger, saved on the way out however the block ends (done, error,
    Ctrl+C). Update its entry right after each sidecar is written, and the
    record always matches the files, so an interrupted run can't make the
    next one stack corrections on corrections."""
    with folder_lock(folder):
        ledger = read_ledger(folder)
        try:
            yield ledger
        finally:
            _save_or_clear(folder, ledger)


def _save_or_clear(folder: Path, ledger: dict) -> None:
    if ledger:
        write_ledger(folder, ledger)
    else:
        (Path(folder) / STATE_DIR / LEDGER).unlink(missing_ok=True)   # nothing recorded: no file


def clear_ledger(folder: Path) -> None:
    """Forget deflicker, and any refine passes built on it."""
    (folder / STATE_DIR / LEDGER).unlink(missing_ok=True)
    (folder / STATE_DIR / "refine.json").unlink(missing_ok=True)
