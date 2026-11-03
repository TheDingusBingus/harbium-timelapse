"""The run report: what a run measured, corrected and found, as a small JSON file.

The contract between the work and anything that shows it: the app draws its
timeline and flags from it, ``timelapse review`` can print it, and it's what
to attach to a bug report. Versioned, so newer tools can read older reports
and older tools refuse newer ones clearly.

Paths are stored as names relative to the report's own folder; a report is
never trusted to say where to write anything.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "harbium-timelapse/report"
VERSION = 1
NAME = "timelapse-report.json"


def _num(x):
    if hasattr(x, "tolist"):                    # numpy arrays and numbers
        x = x.tolist()
    if isinstance(x, (list, tuple)):
        return [_num(v) for v in x]
    try:
        v = float(x)
    except (TypeError, ValueError):
        return x
    return round(v, 5) if math.isfinite(v) else None


def _rel(path, base: Path) -> str:
    try:
        return os.path.relpath(Path(path).resolve(), base.resolve()).replace(os.sep, "/")
    except ValueError:                          # another drive on Windows
        return Path(path).name


def finish_report(plan, written: list[Path] | None, settings: dict, check=None, findings=(),
                  motion: float = 0.0, where: Path | None = None) -> dict:
    """The report for a ``finish`` run. ``plan`` is a FinishPlan; ``check`` the re-measured output."""
    base = Path(where) if where else Path(plan.frames[0]).parent
    frames = []
    unmeasured = set(plan.unmeasured)
    gains = plan.gains
    for i, path in enumerate(plan.frames):
        entry = {"name": Path(path).name, "measured": _num(plan.measured[i]), "target": _num(plan.target[i]),
                 "correction": _num(gains[i]), "measurable": i not in unmeasured}
        if written:
            entry["output"] = _rel(written[i], base)
        if check is not None:
            entry["after"] = _num(check[i])
        frames.append(entry)
    from .review import edges
    return {
        "schema": SCHEMA, "version": VERSION, "tool": _tool_version(),
        "made": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "step": "finish", "settings": settings,
        "source": _rel(Path(plan.frames[0]).parent, base),
        "summary": {"brightness_before": _num(plan.brightness_before), "brightness_after": _num(plan.brightness_after),
                    "colour_before": _num(plan.colour_before), "colour_after": _num(plan.colour_after),
                    "camera_motion": _num(motion)},
        "frames": frames,
        "flags": [{"frame": f.frame, "name": f.name, "kind": f.kind, "size": _num(f.size), "note": f.note}
                  for f in findings],
        "cant_judge": edges(len(frames)),
    }


def write(report: dict, where: Path) -> Path:
    """Save atomically: as ``timelapse-report.json`` in a folder, or to a ``.json`` file path."""
    where = Path(where)
    target = where if where.suffix.lower() == ".json" else where / NAME
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(json.dumps(report, indent=1), encoding="utf-8")
    os.replace(temp, target)
    return target


def read(path: Path) -> dict:
    """Read a report, refusing ones this version doesn't understand."""
    path = Path(path)
    if path.is_dir():
        path = path / NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"Couldn't read the run report {path.name} ({e}).") from e
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError(f"{path.name} isn't a harbium-timelapse run report.")
    if not isinstance(data.get("version"), int) or data["version"] > VERSION:
        raise ValueError(f"{path.name} was made by a newer version of harbium-timelapse; update to read it.")
    return data


def _tool_version() -> str:
    try:
        from importlib.metadata import version
        return version("harbium-timelapse")
    except Exception:
        return "unknown"
