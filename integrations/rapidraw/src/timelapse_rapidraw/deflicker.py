"""Deflicker a RapidRAW sequence by writing exposure into each frame's sidecar.

Your edits are never lost:

- Corrections are **added to** each frame's own exposure, never written over
  it. A manual ramp you've already set stays exactly as you set it.
- What gets smoothed is what you'll actually see: the RAW's brightness *plus*
  the exposure you've already dialled in.
- A ledger in ``.timelapse-core/`` remembers what was added, so running it
  again replaces the old correction instead of stacking another on top. If
  you nudge a frame's exposure in RapidRAW afterwards, that nudge is kept.
- The first time a sidecar is changed, an untouched copy is saved in
  ``.timelapse-core/originals/``.
- ``undo`` takes every correction back out.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from timelapse_core import (
    FlickerCorrections, Frame, Problem, Sequence, check_sequence,
    load_sequence, measure_brightness, smooth_flicker,
)
from timelapse_core.deflicker import DEFAULT_WINDOW

from .sidecar import (
    get_exposure, read_sidecar, set_exposure, sidecar_path,
    with_sidecar_settings, write_sidecar,
)

STATE_DIR = ".timelapse-core"
LEDGER = "rapidraw.json"
_VIRTUAL_COPY = re.compile(r"\.[0-9a-f]{6}\.rrdata$")


@dataclass(frozen=True)
class FramePlan:
    frame: Frame
    base: float        # your own exposure, without any deflicker
    correction: float  # what deflicker adds

    @property
    def exposure(self) -> float:
        return self.base + self.correction


@dataclass(frozen=True, eq=False)
class Plan:
    folder: Path
    frames: tuple[FramePlan, ...]
    flicker: FlickerCorrections


# --- load and check ---------------------------------------------------------

def load(folder: Path | str, progress=None) -> Sequence:
    """Load like the core, then fill gaps from RapidRAW's EXIF copies."""
    seq = load_sequence(folder, progress=progress)
    frames = [with_sidecar_settings(f, _safe_read(f.path)) for f in seq]
    if all(f.taken_at for f in frames):
        frames.sort(key=lambda f: (f.taken_at, f.name))
    return Sequence(seq.folder, tuple(frames))


def check(seq: Sequence) -> list[Problem]:
    problems = check_sequence(seq)
    sidecars, missing, broken = [], [], []
    for f in seq:
        try:
            data = read_sidecar(f.path)
        except (json.JSONDecodeError, UnicodeDecodeError):
            broken.append(f.name)
            continue
        (sidecars.append(data) if data is not None else missing.append(f.name))

    if missing:
        problems.append(Problem(
            "no_sidecar",
            f"{len(missing)} of {len(seq)} frames have no RapidRAW edits yet "
            f"(first: {missing[0]}), so there's nowhere to put their correction. "
            "In RapidRAW, edit one frame, copy its settings, and paste them onto every frame.",
            serious=True,
        ))
    if broken:
        problems.append(Problem(
            "broken_sidecar",
            f"Couldn't read the RapidRAW sidecar for {', '.join(broken[:3])}"
            f"{' and others' if len(broken) > 3 else ''}.",
            serious=True,
        ))

    if sidecars:
        def look(d):
            adj = dict(d.get("adjustments") or {})
            adj.pop("exposure", None)
            return json.dumps(adj, sort_keys=True)
        looks = [look(d) for d in sidecars]
        common = max(set(looks), key=looks.count)
        differ = sum(1 for x in looks if x != common)
        if differ:
            problems.append(Problem(
                "different_edits",
                f"{differ} frame{'s have' if differ != 1 else ' has'} different edits from the rest "
                "(beyond exposure). Deflicker evens out brightness only; mismatched edits "
                "can still flicker.",
            ))

    if any(_VIRTUAL_COPY.search(p.name) for p in seq.folder.iterdir()):
        problems.append(Problem(
            "virtual_copies",
            "This folder has RapidRAW virtual copies. Only the main edit of each frame is changed.",
        ))
    return problems


# --- plan, apply, undo ------------------------------------------------------

def plan(
    seq: Sequence,
    window: int = DEFAULT_WINDOW,
    strength: float = 1.0,
    speed: str = "careful",
    raw_brightness: np.ndarray | None = None,
    progress=None,
    blend: int | None = None,
) -> Plan:
    """Work out each frame's new exposure. Writes nothing."""
    ledger = _read_ledger(seq.folder)
    bases = np.array([_base(f, ledger) for f in seq])
    if raw_brightness is None:
        raw_brightness = measure_brightness(seq, speed=speed, progress=progress)

    seen = np.asarray(raw_brightness, dtype=np.float64) + bases  # exposure is a linear gain
    fix = smooth_flicker(seq, window=window, strength=strength, brightness=seen, blend=blend)
    return Plan(
        folder=seq.folder,
        frames=tuple(FramePlan(f, float(b), float(c)) for f, b, c in zip(seq, bases, fix.exposure)),
        flicker=fix,
    )


def apply(p: Plan) -> int:
    """Write the plan into the sidecars. Returns the number of frames changed."""
    missing = [fp.frame.name for fp in p.frames if not sidecar_path(fp.frame.path).exists()]
    if missing:
        raise ValueError(
            f"{len(missing)} frames have no RapidRAW sidecar (first: {missing[0]}). "
            "Paste settings onto every frame in RapidRAW first."
        )

    originals = p.folder / STATE_DIR / "originals"
    originals.mkdir(parents=True, exist_ok=True)
    ledger = _read_ledger(p.folder)

    for fp in p.frames:
        side = sidecar_path(fp.frame.path)
        backup = originals / side.name
        if not backup.exists():
            shutil.copy2(side, backup)
        data = read_sidecar(fp.frame.path)
        set_exposure(data, fp.exposure)
        write_sidecar(fp.frame.path, data)
        ledger[fp.frame.name] = {"base": round(fp.base, 4), "applied": round(fp.correction, 4)}

    _write_ledger(p.folder, ledger)
    return len(p.frames)


def undo(folder: Path | str) -> int:
    """Take every deflicker correction back out. Your own exposure stays."""
    folder = Path(folder)
    ledger = _read_ledger(folder)
    changed = 0
    for name, entry in ledger.items():
        image = folder / name
        data = read_sidecar(image)
        if data is None:
            continue
        set_exposure(data, get_exposure(data) - entry["applied"])
        write_sidecar(image, data)
        changed += 1
    (folder / STATE_DIR / LEDGER).unlink(missing_ok=True)
    return changed


# --- ledger -----------------------------------------------------------------

def _base(frame: Frame, ledger: dict) -> float:
    """Your exposure without deflicker. If you've changed a frame since the
    last run, the change is kept: current minus what we added."""
    data = _safe_read(frame.path)
    current = get_exposure(data) if data else 0.0
    return current - ledger.get(frame.name, {}).get("applied", 0.0)


def _read_ledger(folder: Path) -> dict:
    path = folder / STATE_DIR / LEDGER
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("frames", {})


def _write_ledger(folder: Path, frames: dict) -> None:
    path = folder / STATE_DIR / LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "frames": frames}, indent=2), encoding="utf-8")


def _safe_read(image: Path) -> dict | None:
    try:
        return read_sidecar(image)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
