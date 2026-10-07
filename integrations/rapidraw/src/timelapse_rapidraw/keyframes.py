"""Keyframes in RapidRAW: purple-labelled frames, and everything between.

You label keyframes purple and edit each one fully. ``blend`` then
**replaces** the edit on every other frame with a smooth blend between the
keyframes either side of it. Every setting is blended, not just exposure.

How each setting is blended:

- **Numbers** (temperature, contrast, shadows, curve points...): a smooth
  curve through the keyframes. Whole-number settings stay whole numbers.
- **The same at every keyframe:** copied exactly, untouched.
- **Can't be blended** (on/off switches, the tone mapper, masks or curves
  shaped differently at different keyframes): each frame takes the setting
  of the keyframe before it, switching at the next keyframe. These are
  reported by name, so nothing is held back silently.

Ratings, labels and the EXIF copy are never changed: only ``adjustments``.
Untouched originals are kept, and ``restore`` puts them back.

Blending resets deflicker. Exposure becomes the blend of your keyframes'
own exposures, so run deflicker again afterwards.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field

import numpy as np
from timelapse_core import Sequence, blend_between, measure_light, suggest_keyframes
from timelapse_core.keyframes import KeyframeSuggestion

from .sidecar import get_exposure, read_sidecar, set_exposure, write_sidecar
from .state import clear_ledger, keep_original, read_ledger

KEYFRAME_LABEL = "color:purple"


# --- labels -----------------------------------------------------------------

def keyframes_in(seq: Sequence, label: str = KEYFRAME_LABEL) -> list[int]:
    """Positions of frames carrying the keyframe label."""
    found = []
    for i, f in enumerate(seq):
        data = read_sidecar(f.path)
        if data and label in (data.get("tags") or []):
            found.append(i)
    return found


def label_keyframes(seq: Sequence, frames: list[int], label: str = KEYFRAME_LABEL) -> list[str]:
    """Give these frames the keyframe label. A frame has one colour label in
    RapidRAW, so any other colour on it is replaced. Returns notes on what was
    replaced.

    Labelling deliberately doesn't take the ``restore`` backup: you'll edit
    keyframes after labelling them, and ``restore`` must never roll those
    edits back. Labels are easy to change in RapidRAW itself."""
    notes = []
    for i in frames:
        f = seq[i]
        data = read_sidecar(f.path)
        if data is None:
            raise ValueError(f"{f.name} has no RapidRAW sidecar yet. Paste settings onto every frame first.")
        tags = list(data.get("tags") or [])
        if label in tags:
            continue
        replaced = [t for t in tags if t.startswith("color:")]
        if replaced:
            notes.append(f"{f.name}: {replaced[0].split(':', 1)[1]} label replaced")
        data["tags"] = [t for t in tags if not t.startswith("color:")] + [label]
        write_sidecar(f.path, data)
    return notes


def suggest(seq: Sequence, every: float = 1.0, warmth_every: float = 0.5,
            speed: str = "careful", progress=None) -> list[KeyframeSuggestion]:
    brightness, warmth = measure_light(seq, speed=speed, progress=progress)
    return suggest_keyframes(seq, brightness, warmth, every=every, warmth_every=warmth_every)


# --- blending ---------------------------------------------------------------

@dataclass
class BlendResult:
    keyframes: list[str]
    changed: int
    held: list[str] = field(default_factory=list)   # settings that couldn't blend


def plan_blend(seq: Sequence, label: str = KEYFRAME_LABEL) -> tuple[list[dict], list[int], list[str]]:
    """Each frame's blended ``adjustments``, the keyframe positions, and the
    settings that had to be held rather than blended. Writes nothing."""
    keys = keyframes_in(seq, label)
    if len(keys) < 2:
        raise ValueError(
            f"Found {len(keys)} purple keyframe{'s' if len(keys) != 1 else ''}. "
            "Label at least two frames purple (first and last at minimum), edit them, then blend."
        )
    ledger = read_ledger(seq.folder)
    key_edits = []
    for i in keys:
        data = read_sidecar(seq[i].path)
        adj = copy.deepcopy(data.get("adjustments") or {})
        applied = ledger.get(seq[i].name, {}).get("applied", 0.0)
        set_exposure({"adjustments": adj}, get_exposure(data) - applied)  # your exposure, not deflicker's
        key_edits.append(adj)

    held: set[str] = set()
    per_frame = _blend(key_edits, keys, len(seq), "", held)
    return per_frame, keys, sorted(held)


def blend(seq: Sequence, label: str = KEYFRAME_LABEL) -> BlendResult:
    """Replace every frame's edit with the blend between keyframes."""
    missing = [f.name for f in seq if read_sidecar(f.path) is None]
    if missing:
        raise ValueError(f"{len(missing)} frames have no RapidRAW sidecar (first: {missing[0]}). "
                         "Paste settings onto every frame first.")
    per_frame, keys, held = plan_blend(seq, label)
    changed = 0
    for f, adj in zip(seq, per_frame):
        data = read_sidecar(f.path)
        if data.get("adjustments") == adj:
            continue
        keep_original(f.path)
        data["adjustments"] = adj
        write_sidecar(f.path, data)
        changed += 1
    clear_ledger(seq.folder)
    return BlendResult([seq[i].name for i in keys], changed, held)


def frames_off_the_blend(seq: Sequence, label: str = KEYFRAME_LABEL) -> list[tuple[str, list[str]]]:
    """Frames whose edit doesn't match the blend between keyframes (exposure
    aside, since deflicker changes it). For ``check``."""
    per_frame, _, _ = plan_blend(seq, label)
    off = []
    for f, expected in zip(seq, per_frame):
        actual = dict((read_sidecar(f.path) or {}).get("adjustments") or {})
        expected = dict(expected)
        actual.pop("exposure", None)
        expected.pop("exposure", None)
        differ = sorted(k for k in set(actual) | set(expected) if not _close(actual.get(k), expected.get(k)))
        if differ:
            off.append((f.name, differ))
    return off


# --- the blend itself -------------------------------------------------------

def _blend(values: list, keys: list[int], n: int, path: str, held: set[str]) -> list:
    """One value per frame, from one value per keyframe. Recursive."""
    first = values[0]
    if all(_same(v, first) for v in values[1:]):
        return [copy.deepcopy(first) for _ in range(n)]

    if all(_is_number(v) for v in values):
        curve = blend_between(keys, [float(v) for v in values], n)
        if all(isinstance(v, int) for v in values):
            return [int(round(x)) for x in curve]
        return [_tidy(x) for x in curve]

    if all(isinstance(v, dict) for v in values) and all(v.keys() == first.keys() for v in values):
        columns = {k: _blend([v[k] for v in values], keys, n, f"{path}.{k}" if path else k, held)
                   for k in first}
        return [{k: columns[k][i] for k in first} for i in range(n)]

    if all(isinstance(v, list) for v in values) and len({len(v) for v in values}) == 1:
        columns = [_blend([v[j] for v in values], keys, n, f"{path}[{j}]", held) for j in range(len(first))]
        return [[col[i] for col in columns] for i in range(n)]

    held.add(path or "(everything)")
    out = []
    for i in range(n):
        k = max((j for j, key in enumerate(keys) if key <= i), default=0)
        out.append(copy.deepcopy(values[k]))
    return out


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _tidy(x: float):
    x = round(float(x), 4)
    return int(x) if x == int(x) else x


def _same(a, b) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _close(a, b) -> bool:
    if _is_number(a) and _is_number(b):
        return abs(float(a) - float(b)) < 1e-3
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b))
    return a == b
