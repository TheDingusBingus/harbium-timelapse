"""Refine deflicker against an actual render.

Why this exists: deflicker measures the RAW files and assumes +1 on
RapidRAW's Exposure slider is one stop of light. On a real Canon R6 dusk
sequence it wasn't: through a finished grade, +1 on the slider moved the
render like 1.4 to 1.8 stops of real light, varying along the sequence with
the grade. Corrections came out up to 80% too strong, and the render
flickered *more* than the RAWs (0.136 stops against 0.111).

So the last word goes to the render. Each pass:

1. Measure the brightness of every exported frame.
2. Work out how strongly the Exposure slider moves the render, frame by
   frame. First pass: from how render brightness tracks the RAW
   measurements and the corrections already applied. Later passes, much more
   precisely: from how each frame changed between this export and the last.
3. Nudge each frame's exposure by exactly what removes the render's own
   flicker.

Export again and repeat. Usually two or three passes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from timelapse_core import Sequence, measure_brightness, smooth_brightness
from timelapse_core.deflicker import DEFAULT_WINDOW
from timelapse_core.sequencer import _natural_key

from .sidecar import get_exposure, read_sidecar, set_exposure, write_sidecar
from .state import STATE_DIR, keep_original, read_ledger, write_ledger

HISTORY = "refine.json"
INVISIBLE = 0.015       # stops of render flicker below which there's nothing to gain
MOST_PER_PASS = 1.0     # stops; no single pass moves a frame further than this
STRENGTH_RANGE = (0.3, 6.0)
RENDERED = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


@dataclass(frozen=True)
class RefineResult:
    pass_number: int
    flicker_in_render: float
    slider_strength: float      # stops of render change per +1 on the Exposure slider (typical)
    changed: int
    nothing_to_do: bool = False


def rendered_frames(folder: Path | str) -> list[Path]:
    folder = Path(folder)
    return sorted((p for p in folder.iterdir()
                   if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in RENDERED),
                  key=_natural_key)


def refine(
    seq: Sequence,
    rendered: Path | str | None = None,
    window: int = DEFAULT_WINDOW,
    render_brightness: np.ndarray | None = None,
    raw_brightness: np.ndarray | None = None,
    progress=None,
    dry_run: bool = False,
) -> RefineResult:
    """One refinement pass. ``rendered`` is the folder of exported frames,
    in the same order and number as the sequence."""
    ledger = read_ledger(seq.folder)
    if not ledger:
        raise ValueError("Run deflicker first: refine improves deflicker, it doesn't replace it.")

    if render_brightness is None:
        frames = rendered_frames(rendered)
        if len(frames) != len(seq):
            raise ValueError(f"The export has {len(frames)} frames but the sequence has {len(seq)}. "
                             "Export every frame, and nothing else, into that folder.")
        render_brightness = measure_brightness(frames, progress=progress)
    render = np.asarray(render_brightness, dtype=np.float64)

    applied = np.array([ledger.get(f.name, {}).get("applied", 0.0) for f in seq])
    current = np.array([get_exposure(read_sidecar(f.path) or {}) for f in seq])
    base = current - applied

    flicker = float(np.std(np.diff(render)) / np.sqrt(2))
    history = _read_history(seq.folder)
    pass_number = len(history) + 1

    if flicker < INVISIBLE:
        return RefineResult(pass_number, flicker, float("nan"), 0, nothing_to_do=True)

    strength = _slider_strength(seq, render, applied, base, history, raw_brightness, window)
    target = smooth_brightness(render, window)
    step = np.clip((target - render) / strength, -MOST_PER_PASS, MOST_PER_PASS)
    new_applied = applied + step

    if dry_run:
        return RefineResult(pass_number, flicker, float(np.median(strength)), 0)

    for f, b, a in zip(seq, base, new_applied):
        keep_original(f.path)
        data = read_sidecar(f.path)
        set_exposure(data, b + a)
        write_sidecar(f.path, data)
        ledger[f.name] = {"base": round(float(b), 4), "applied": round(float(a), 4)}
    write_ledger(seq.folder, ledger)
    _write_history(seq.folder, history + [{"render": render.round(5).tolist(), "applied": applied.round(5).tolist()}])
    return RefineResult(pass_number, flicker, float(np.median(strength)), len(seq))


def clear_history(folder: Path) -> None:
    """Deflicker or a keyframe blend starts afresh: old passes no longer apply."""
    (Path(folder) / STATE_DIR / HISTORY).unlink(missing_ok=True)


# --- how strongly the slider moves the render ---------------------------------

def _slider_strength(seq, render, applied, base, history, raw_brightness, window) -> np.ndarray:
    """Render stops per slider stop, per frame."""
    if history:
        last = history[-1]
        prev_render, prev_applied = np.array(last["render"]), np.array(last["applied"])
        if len(prev_render) == len(render):
            moved = applied - prev_applied
            ok = np.abs(moved) > 0.02
            if ok.sum() >= 10:
                ratio = np.full(len(render), np.nan)
                ratio[ok] = (render[ok] - prev_render[ok]) / moved[ok]
                return _local(ratio, window)

    # First pass: how the render tracks the RAW and the corrections, frame to frame.
    if raw_brightness is None:
        raw_brightness = measure_brightness(seq)
    raw = np.asarray(raw_brightness, dtype=np.float64)
    design = np.column_stack([np.diff(raw), np.diff(applied), np.diff(base)])
    coef, *_ = np.linalg.lstsq(design, np.diff(render), rcond=None)
    strength = float(np.clip(coef[1], *STRENGTH_RANGE))
    return np.full(len(render), strength)


def _local(ratio: np.ndarray, window: int) -> np.ndarray:
    """Frame-by-frame strengths are noisy: take a running median of the
    trustworthy ones, and fall back to the overall median where there are none."""
    ok = np.isfinite(ratio) & (ratio > STRENGTH_RANGE[0]) & (ratio < STRENGTH_RANGE[1])
    overall = float(np.median(ratio[ok])) if ok.any() else 1.0
    out = np.empty(len(ratio))
    half = max(window // 2, 3)
    for i in range(len(ratio)):
        nearby = ratio[max(0, i - half):i + half + 1]
        nearby = nearby[np.isfinite(nearby) & (nearby > STRENGTH_RANGE[0]) & (nearby < STRENGTH_RANGE[1])]
        out[i] = np.median(nearby) if len(nearby) >= 5 else overall
    return out


def _read_history(folder: Path) -> list:
    path = Path(folder) / STATE_DIR / HISTORY
    return json.loads(path.read_text(encoding="utf-8")).get("passes", []) if path.exists() else []


def _write_history(folder: Path, passes: list) -> None:
    path = Path(folder) / STATE_DIR / HISTORY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "passes": passes}), encoding="utf-8")
