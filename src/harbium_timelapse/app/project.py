"""A project: one folder of frames, where it's up to, and the choices made for it.

Saved beside the photos in ``.timelapse-core/project.json``, so it travels with
them and reopening the folder lands on the next step. Versioned: a newer file
is refused clearly, a damaged one is replaced with a fresh start (the photos
and edit files are never affected either way). Paths stored here are only
ever read from; anything the app writes goes where the user chose at the time.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..editors.common import STATE_DIR

FILE = "project.json"
VERSION = 1
STAGES = ("opened", "prepared", "exported", "finished")


@dataclass
class Project:
    folder: Path
    stage: str = "opened"
    editor: str | None = None                 # the editor plug-in's name, e.g. "rapidraw"
    export: str | None = None                 # the folder the export was found in
    video: str | None = None                  # the last video made
    settings: dict = field(default_factory=lambda: {"window": 25, "strength": 1.0, "white_balance": True,
                                                    "codec": "h264", "fps": 24, "width": None})
    dismissed: list[str] = field(default_factory=list)     # frames marked "looks fine"
    note: str = ""                            # set when the saved file couldn't be used

    def reach(self, stage: str) -> None:
        """Move forward to ``stage`` (never backwards; ``start_over`` does that)."""
        if STAGES.index(stage) > STAGES.index(self.stage):
            self.stage = stage

    def start_over(self) -> None:
        self.stage, self.export, self.video, self.dismissed = "opened", None, None, []

    def save(self) -> None:
        folder = Path(self.folder) / STATE_DIR
        folder.mkdir(parents=True, exist_ok=True)
        data = {k: v for k, v in asdict(self).items() if k not in ("folder", "note")}
        data.update(version=VERSION, saved=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        temp = folder / (FILE + ".tmp")
        temp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(temp, folder / FILE)


def load_project(folder: Path | str) -> Project:
    folder = Path(folder)
    path = folder / STATE_DIR / FILE
    if not path.exists():
        return Project(folder)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Project(folder, note="The saved project details were damaged, so this starts fresh. "
                                    "Your photos and edits aren't affected.")
    if not isinstance(data, dict) or not isinstance(data.get("version"), int):
        return Project(folder, note="The saved project details weren't recognised, so this starts fresh.")
    if data["version"] > VERSION:
        raise ValueError("This folder was last opened with a newer version of harbium-timelapse. "
                         "Update the app to open it.")
    project = Project(folder)
    if data.get("stage") in STAGES:
        project.stage = data["stage"]
    for key in ("editor", "export", "video"):
        if isinstance(data.get(key), str):
            setattr(project, key, data[key])
    if isinstance(data.get("settings"), dict):
        project.settings.update({k: v for k, v in data["settings"].items() if k in project.settings})
    if isinstance(data.get("dismissed"), list):
        project.dismissed = [str(x) for x in data["dismissed"]]
    return project
