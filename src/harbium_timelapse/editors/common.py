"""Safety machinery shared by every editor adapter.

Everything an adapter needs to change edit files without ever losing work:
an untouched copy of each file before its first change, a record of what was
added that's saved as each file is written (so an interrupted run can simply
be run again), one run per folder at a time, atomic writes, and plain-English
explanations instead of crashes when one of these files is damaged.

All of it lives in a hidden ``.timelapse-core`` folder beside the photos (the
name predates the rename and is kept so existing folders keep working).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

STATE_DIR = ".timelapse-core"
ORIGINALS = "originals"


# --- reading and writing the tool's own files ------------------------------------

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


def write_bytes_atomically(path: Path, data: bytes) -> None:
    """Replace a file in one step: a crash mid-write never leaves half a file.
    Bytes in, bytes out, so line endings and everything else stay exactly as given."""
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(data)
    os.replace(temp, path)


# --- untouched copies -------------------------------------------------------------

def keep_copy(edit_file: Path) -> None:
    """Copy an edit file aside before its first change. Later calls do nothing."""
    edit_file = Path(edit_file)
    backup = edit_file.parent / STATE_DIR / ORIGINALS / edit_file.name
    if edit_file.exists() and not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(edit_file, backup)


def restore_copies(folder: Path | str, names: set[str] | None = None) -> int:
    """Put edit files back exactly as they were (only ``names``, if given)."""
    folder = Path(folder)
    originals = folder / STATE_DIR / ORIGINALS
    count = 0
    with folder_lock(folder):
        if originals.is_dir():
            for backup in originals.iterdir():
                if backup.name.endswith(".tmp") or (names is not None and backup.name not in names):
                    continue
                shutil.copy2(backup, folder / backup.name)
                count += 1
    return count


# --- the record of what was added -------------------------------------------------

def read_record(folder: Path, name: str) -> dict:
    path = Path(folder) / STATE_DIR / name
    if not path.exists():
        return {}
    frames = read_json(path, "what it changed").get("frames", {})
    if not isinstance(frames, dict):
        raise LedgerDamaged(f"The tool's record ({path}) doesn't look right. Run restore, or delete it.")
    return frames


def write_record(folder: Path, name: str, frames: dict) -> None:
    write_json(Path(folder) / STATE_DIR / name, {"version": 1, "frames": frames})


@contextmanager
def record_kept_in_step(folder: Path, name: str):
    """The record, saved on the way out however the block ends (done, error,
    Ctrl+C), under the folder lock. Update each frame's entry right after its
    file is written, and the record always matches the files."""
    with folder_lock(folder):
        record = read_record(folder, name)
        try:
            yield record
        finally:
            if record:
                write_record(folder, name, record)
            else:
                (Path(folder) / STATE_DIR / name).unlink(missing_ok=True)


# --- one run per folder -----------------------------------------------------------

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




# --- is an editor open? -----------------------------------------------------------

def running_programs() -> list[str] | None:
    """Names of running programs, or ``None`` if they can't be listed."""
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                                 text=True, timeout=10).stdout
            return [line.split('","')[0].strip('"') for line in out.splitlines() if line.strip()]
        out = subprocess.run(["ps", "-A", "-o", "comm="], capture_output=True, text=True, timeout=10).stdout
        return [Path(line.strip()).name for line in out.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        return None
