"""Where the app keeps its own things: preferences and recent projects.

The standard per-user folder on each system (never inside photo folders).
``HARBIUM_CONFIG`` moves it, which tests use.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

RECENT_LIMIT = 10


def config_dir() -> Path:
    if os.environ.get("HARBIUM_CONFIG"):
        return Path(os.environ["HARBIUM_CONFIG"])
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "harbium-timelapse"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "harbium-timelapse"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "harbium-timelapse"


def _read(name: str, default):
    try:
        return json.loads((config_dir() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(name: str, data) -> None:
    folder = config_dir()
    folder.mkdir(parents=True, exist_ok=True)
    temp = folder / (name + ".tmp")
    temp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(temp, folder / name)


def recent_projects() -> list[dict]:
    """Most recent first: folder, frames, stage, opened. Folders that no longer exist are left out."""
    items = _read("recent.json", [])
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict) and Path(str(i.get("folder", ""))).is_dir()]


def remember_project(folder: Path, frames: int, stage: str) -> None:
    folder = str(Path(folder).resolve())
    items = [i for i in recent_projects() if i.get("folder") != folder]
    items.insert(0, {"folder": folder, "frames": int(frames), "stage": stage,
                     "opened": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    try:
        _write("recent.json", items[:RECENT_LIMIT])
    except OSError:
        pass                                   # not being able to remember is never an error


def preferences() -> dict:
    prefs = _read("preferences.json", {})
    return prefs if isinstance(prefs, dict) else {}


def save_preferences(prefs: dict) -> None:
    try:
        _write("preferences.json", prefs)
    except OSError:
        pass
