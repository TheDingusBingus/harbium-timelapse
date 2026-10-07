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
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from timelapse_core import (
    FlickerCorrections, Frame, Problem, Sequence, check_sequence,
    load_sequence, measure_brightness, smooth_flicker,
)
from timelapse_core.deflicker import DEFAULT_WINDOW

from .sidecar import (
    get_exposure, get_temperature, read_sidecar, set_exposure, set_temperature, sidecar_path,
    with_sidecar_settings, write_sidecar,
)
from .state import clear_ledger, keep_original, read_ledger, write_ledger
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
        if data is None:
            missing.append(f.name)
        else:
            sidecars.append((f.name, data))

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

    if sidecars and not missing and not broken:
        problems += _check_edits(seq, sidecars)

    if sidecars and not missing and not broken:
        from .recover import overwritten_frames
        off = overwritten_frames(seq)
        if len(off) >= max(3, len(seq) // 10):
            problems.append(Problem(
                "overwritten",
                f"{len(off)} sidecars no longer hold what this tool last wrote (first: {off[0]}). "
                "RapidRAW has most likely saved over them. Close RapidRAW, then run: "
                "timelapse-rapidraw rebuild FOLDER",
                serious=True,
            ))
        elif off:
            problems.append(Problem(
                "exposure_changed",
                f"{len(off)} frame{'s' if len(off) != 1 else ''}' exposure no longer matches what this tool "
                f"last wrote (first: {off[0]}). If you changed it yourself in RapidRAW, it'll be kept next "
                "time deflicker runs. If RapidRAW reset the edit, run: timelapse-rapidraw rebuild FOLDER",
            ))

    if any(_VIRTUAL_COPY.search(p.name) for p in seq.folder.iterdir()):
        problems.append(Problem(
            "virtual_copies",
            "This folder has RapidRAW virtual copies. Only the main edit of each frame is changed.",
        ))
    return problems


def _check_edits(seq: Sequence, sidecars: list[tuple[str, dict]]) -> list[Problem]:
    """With two or more purple keyframes, every frame should match the blend
    between them. Without, every frame should match the rest."""
    from .keyframes import KEYFRAME_LABEL, frames_off_the_blend, keyframes_in

    keys = keyframes_in(seq, KEYFRAME_LABEL)
    if len(keys) == 1:
        return [Problem("one_keyframe", f"{seq[keys[0]].name} is the only purple keyframe. "
                        "Label at least one more (the first and last frames, say) to blend between them.")]
    if len(keys) >= 2:
        off = frames_off_the_blend(seq, KEYFRAME_LABEL)
        if not off:
            return []
        first, differ = off[0]
        return [Problem(
            "off_the_blend",
            f"{len(off)} frame{'s' if len(off) != 1 else ''} don't match the blend between your "
            f"purple keyframes (first: {first}, {', '.join(differ[:3])}). "
            "Run: timelapse-rapidraw keyframes FOLDER",
        )]
    return _check_matching_edits(sidecars)


def _check_matching_edits(sidecars: list[tuple[str, dict]]) -> list[Problem]:
    """Name every frame whose edit differs from the rest, and say what differs."""
    def look(d: dict) -> dict:
        adj = dict(d.get("adjustments") or {})
        adj.pop("exposure", None)
        adj.pop("whiteBalance", None)   # set per frame on purpose by the white-balance command
        return adj

    looks = [(name, look(d)) for name, d in sidecars]
    keys = [json.dumps(adj, sort_keys=True) for _, adj in looks]
    common_key = max(set(keys), key=keys.count)
    common = json.loads(common_key)

    problems = []
    for (name, adj), key in zip(looks, keys):
        if key == common_key:
            continue
        differ = sorted(k for k in set(adj) | set(common) if adj.get(k) != common.get(k))
        shown = ", ".join(differ[:4]) + (f" and {len(differ) - 4} more" if len(differ) > 4 else "")
        problems.append(Problem(
            "different_edit",
            f"{name} has a different edit from the rest ({shown}). Deflicker evens out "
            "brightness only, so this frame may still pop. Paste your settings onto it again, "
            "or if it's meant to be a keyframe, label it purple.",
            frame=next(i for i, (n, _) in enumerate(sidecars) if n == name),
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
    ledger = read_ledger(seq.folder)
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

    ledger = read_ledger(p.folder)

    for fp in p.frames:
        keep_original(fp.frame.path)
        data = read_sidecar(fp.frame.path)
        set_exposure(data, fp.exposure)
        write_sidecar(fp.frame.path, data)
        ledger.setdefault(fp.frame.name, {}).update(base=round(fp.base, 4), applied=round(fp.correction, 4))

    write_ledger(p.folder, ledger)
    (p.folder / ".timelapse-core" / "refine.json").unlink(missing_ok=True)  # a fresh deflicker: old refine passes no longer apply
    return len(p.frames)


def undo(folder: Path | str, colour_only: bool = False) -> int:
    """Take every deflicker and refine correction back out (exposure and
    colour). Your own edit stays. ``colour_only`` removes just refine's
    Temperature corrections and keeps the exposure work."""
    folder = Path(folder)
    ledger = read_ledger(folder)
    changed = 0
    for name, entry in ledger.items():
        image = folder / name
        data = read_sidecar(image)
        if data is None:
            continue
        if not colour_only:
            set_exposure(data, get_exposure(data) - entry["applied"])
        if entry.get("applied_temp"):
            set_temperature(data, get_temperature(data) - entry["applied_temp"])
            entry["applied_temp"] = 0.0
        write_sidecar(image, data)
        changed += 1
    if colour_only:
        write_ledger(folder, ledger)
        _forget_colour_history(folder)
    elif any("white_balance_before" in e for e in ledger.values()):
        # Keep what white balance needs to undo itself; exposure is back to base.
        kept = {name: {"base": round(get_exposure(read_sidecar(folder / name) or {}), 4), "applied": 0.0,
                       **{k: v for k, v in e.items() if k.startswith("white_balance")}}
                for name, e in ledger.items() if "white_balance_before" in e}
        write_ledger(folder, kept)
        (folder / ".timelapse-core" / "refine.json").unlink(missing_ok=True)
    else:
        clear_ledger(folder)
    return changed


def _forget_colour_history(folder: Path) -> None:
    """Refine passes keep their exposure history; their colour history goes."""
    path = folder / ".timelapse-core" / "refine.json"
    if not path.exists():
        return
    history = json.loads(path.read_text(encoding="utf-8"))
    for record in history.get("passes", []):
        for key in ("warmth", "applied_temp", "colour_target"):
            record.pop(key, None)
    path.write_text(json.dumps(history), encoding="utf-8")


# --- ledger -----------------------------------------------------------------

def _base(frame: Frame, ledger: dict) -> float:
    """Your exposure without deflicker. If you've changed a frame since the
    last run, the change is kept: current minus what we added."""
    data = _safe_read(frame.path)
    current = get_exposure(data) if data else 0.0
    return current - ledger.get(frame.name, {}).get("applied", 0.0)


def _safe_read(image: Path) -> dict | None:
    try:
        return read_sidecar(image)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
