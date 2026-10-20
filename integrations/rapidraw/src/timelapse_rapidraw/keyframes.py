"""Keyframes in RapidRAW: purple-labelled frames, and everything between.

You label keyframes purple and edit each one fully. ``blend`` then
**replaces** the edit on every other frame with a smooth blend between the
keyframes either side of it. Every setting is blended, not just exposure.

How each setting is blended:

- **Numbers** (temperature, contrast, shadows...): a smooth curve through
  the keyframes. Whole-number settings stay whole numbers.
- **Tone curves**, even with different points at different keyframes: each
  keyframe's curve is read at every point position any keyframe uses (traced
  the way RapidRAW draws curves), and the heights are blended.
- **Keyframes themselves are never rewritten.** Your edit there stays exactly
  as you made it.
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
from .state import STATE_DIR, keep_original, ledger_kept_in_step, read_ledger

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
    held: dict[str, str] = field(default_factory=dict)   # setting -> why it couldn't blend


_MISSING = object()  # a setting a keyframe's sidecar doesn't contain at all


def plan_blend(seq: Sequence, label: str = KEYFRAME_LABEL) -> tuple[list[dict], list[int], dict[str, str]]:
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

    held: dict[str, str] = {}
    names = [seq[i].name for i in keys]
    per_frame = _blend(key_edits, keys, names, len(seq), "", held)
    for j, i in enumerate(keys):  # a keyframe is your edit, exactly. Never a reconstruction of it.
        per_frame[i] = copy.deepcopy(key_edits[j])
    return per_frame, keys, dict(sorted(held.items()))


def blend(seq: Sequence, label: str = KEYFRAME_LABEL) -> BlendResult:
    """Replace every frame's edit with the blend between keyframes."""
    missing = [f.name for f in seq if read_sidecar(f.path) is None]
    if missing:
        raise ValueError(f"{len(missing)} frames have no RapidRAW sidecar (first: {missing[0]}). "
                         "Paste settings onto every frame first.")
    per_frame, keys, held = plan_blend(seq, label)
    changed = 0
    with ledger_kept_in_step(seq.folder) as ledger:
        steady_wb = {name: dict(e) for name, e in ledger.items() if "white_balance" in e}
        for f, adj in zip(seq, per_frame, strict=True):
            data = read_sidecar(f.path)
            if f.name in steady_wb:          # white balance set by the white-balance command stays
                adj = {**adj, "whiteBalance": steady_wb[f.name]["white_balance"]}
            if data.get("adjustments") != adj:
                keep_original(f.path)
                data["adjustments"] = adj
                write_sidecar(f.path, data)
                changed += 1
            # This frame now holds the blend, so no deflicker is on it any more. Recorded
            # per frame, so an interrupted blend leaves an honest record.
            wb = {k: v for k, v in steady_wb.get(f.name, {}).items() if k.startswith("white_balance")}
            if wb:
                ledger[f.name] = {"base": round(get_exposure(data), 4), "applied": 0.0, **wb}
            else:
                ledger.pop(f.name, None)
    (seq.folder / STATE_DIR / "refine.json").unlink(missing_ok=True)
    return BlendResult([seq[i].name for i in keys], changed, held)


def frames_off_the_blend(seq: Sequence, label: str = KEYFRAME_LABEL) -> list[tuple[str, list[str]]]:
    """Frames whose edit doesn't match the blend between keyframes (exposure
    aside, since deflicker changes it). For ``check``."""
    per_frame, _, _ = plan_blend(seq, label)
    off = []
    for f, expected in zip(seq, per_frame):
        actual = dict((read_sidecar(f.path) or {}).get("adjustments") or {})
        expected = dict(expected)
        for per_frame_setting in ("exposure", "whiteBalance"):
            actual.pop(per_frame_setting, None)
            expected.pop(per_frame_setting, None)
        differ = sorted(k for k in set(actual) | set(expected) if not _close(actual.get(k), expected.get(k)))
        if differ:
            off.append((f.name, differ))
    return off


# --- the blend itself -------------------------------------------------------

def _blend(values: list, keys: list[int], names: list[str], n: int, path: str, held: dict) -> list:
    """One value per frame, from one value per keyframe. Recursive.

    Settings are blended one by one: a setting that can't blend, or that's
    missing from some keyframes' sidecars, is held on its own and reported,
    without stopping everything else from blending.
    """
    if any(v is _MISSING for v in values):
        lacking = [name for name, v in zip(names, values) if v is _MISSING]
        held[path] = "not set on " + ", ".join(lacking)
        return _hold(values, keys, n)

    first = values[0]
    if all(_same(v, first) for v in values[1:]):
        return [copy.deepcopy(first) for _ in range(n)]

    if all(_is_number(v) for v in values):
        curve = blend_between(keys, [float(v) for v in values], n)
        if all(isinstance(v, int) for v in values):
            return [int(round(x)) for x in curve]
        return [_tidy(x) for x in curve]

    if all(isinstance(v, dict) for v in values):
        order = list(max(values, key=len))                          # fullest keyframe's order,
        order += [k for v in values for k in v if k not in order]   # then anything only others have
        columns = {k: _blend([v.get(k, _MISSING) for v in values], keys, names, n,
                             f"{path}.{k}" if path else k, held)
                   for k in order}
        return [{k: columns[k][i] for k in order if columns[k][i] is not _MISSING} for i in range(n)]

    if all(_is_curve(v) for v in values) and not _same_points(values):
        return _blend_curves(values, keys, n)

    if all(isinstance(v, list) for v in values) and len({len(v) for v in values}) == 1:
        columns = [_blend([v[j] for v in values], keys, names, n, f"{path}[{j}]", held)
                   for j in range(len(first))]
        return [[col[i] for col in columns] for i in range(n)]

    held[path or "(everything)"] = "can't be blended"
    return _hold(values, keys, n)


def _hold(values: list, keys: list[int], n: int) -> list:
    """Each frame takes the value of the keyframe at or before it."""
    out = []
    for i in range(n):
        k = max((j for j, key in enumerate(keys) if key <= i), default=0)
        out.append(copy.deepcopy(values[k]) if values[k] is not _MISSING else _MISSING)
    return out


def _is_curve(v) -> bool:
    """A tone curve: two or more {x, y} points, left to right."""
    return (isinstance(v, list) and len(v) >= 2
            and all(isinstance(p, dict) and set(p) == {"x", "y"} and _is_number(p["x"]) and _is_number(p["y"])
                    for p in v)
            and all(a["x"] < b["x"] for a, b in zip(v, v[1:])))


def _same_points(curves: list) -> bool:
    """Same number of points at the same x positions: blend point by point."""
    return len({tuple(p["x"] for p in c) for c in curves}) == 1


def _blend_curves(curves: list, keys: list[int], n: int) -> list:
    """Curves with different points. Give every keyframe's curve the same
    points (every x any keyframe uses), reading each curve's shape between its
    own points the way RapidRAW draws it (cubic Hermite, monotone, flat beyond
    the end points), then blend the height at each x across keyframes."""
    xs = sorted({p["x"] for c in curves for p in c})
    heights = np.array([_curve_at(c, xs) for c in curves])           # keyframes × xs
    blended = np.array([blend_between(keys, heights[:, j], n) for j in range(len(xs))])  # xs × frames
    whole = all(isinstance(p["y"], int) for c in curves for p in c)
    return [[{"x": x, "y": int(round(blended[j, i])) if whole else _tidy(blended[j, i])}
             for j, x in enumerate(xs)] for i in range(n)]


def _curve_at(points: list[dict], xs: list[float]) -> list[float]:
    """Height of one curve at each x: monotone cubic Hermite through its
    points, flat before the first point and after the last."""
    px = np.array([p["x"] for p in points], dtype=np.float64)
    py = np.array([p["y"] for p in points], dtype=np.float64)
    h, d = np.diff(px), np.diff(py) / np.diff(px)
    m = np.empty(len(px))
    m[0], m[-1] = d[0], d[-1]
    for k in range(1, len(px) - 1):
        if d[k - 1] * d[k] > 0:
            w1, w2 = 2 * h[k] + h[k - 1], h[k] + 2 * h[k - 1]
            m[k] = (w1 + w2) / (w1 / d[k - 1] + w2 / d[k])
        else:
            m[k] = 0.0
    out = []
    for x in xs:
        if x <= px[0]:
            out.append(py[0]); continue
        if x >= px[-1]:
            out.append(py[-1]); continue
        j = int(np.searchsorted(px, x, side="right") - 1)
        t = (x - px[j]) / h[j]
        out.append((1 + 2 * t) * (1 - t) ** 2 * py[j] + t * (1 - t) ** 2 * h[j] * m[j]
                   + t * t * (3 - 2 * t) * py[j + 1] + t * t * (t - 1) * h[j] * m[j + 1])
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
