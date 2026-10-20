"""Editor adapters: built in, or added by other packages through the
``harbium_timelapse.editors`` entry point group (see ``base.py``)."""

from __future__ import annotations

import sys
from importlib.metadata import entry_points
from pathlib import Path

from .base import Editor, PrepareReport

__all__ = ["Editor", "PrepareReport", "available", "detect", "get"]


def available() -> dict[str, type[Editor]]:
    """Every editor this installation knows, by command-line name."""
    from .lightroom.adapter import Lightroom
    from .rapidraw.adapter import RapidRAW
    found: dict[str, type[Editor]] = {RapidRAW.name: RapidRAW, Lightroom.name: Lightroom}
    for ep in entry_points(group="harbium_timelapse.editors"):
        if ep.name in found:
            continue
        try:
            cls = ep.load()
        except Exception as e:                       # a broken plug-in mustn't break the tool
            print(f"Note: couldn't load the editor plug-in '{ep.name}' ({e}).", file=sys.stderr)
            continue
        if isinstance(cls, type) and issubclass(cls, Editor) and cls.name:
            found[cls.name] = cls
    return found


def get(name: str) -> Editor:
    editors = available()
    if name not in editors:
        raise ValueError(f"No editor called '{name}'. Available: {', '.join(sorted(editors))}.")
    return editors[name]()


def detect(folder: Path | str) -> list[Editor]:
    """Editors whose edit files are in ``folder``."""
    folder = Path(folder)
    return [cls() for cls in available().values() if cls().detect(folder)]
