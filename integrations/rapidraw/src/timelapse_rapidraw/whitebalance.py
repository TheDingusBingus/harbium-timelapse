"""White balance: a steady Kelvin for every frame, written straight in.

On Auto white balance the camera re-guesses every frame. On a real Canon R6
dusk sequence its guess jumped by up to 669 K between neighbouring frames,
worst where a variable ND was turned, and the render's colour flickered.

RapidRAW stores an absolute white balance per frame in its sidecar:

    "whiteBalance": {"temperature": 5600, "tint": 0}

(``null`` means "use the camera's as-shot guess".) Pasting in RapidRAW only
copies the *relative* Temperature/Tint sliders, which nudge each frame's own
guess, so it can't fix this. This tool writes the absolute value per frame:

- ``smooth`` (default): the camera's own as-shot Kelvin, heavily smoothed.
  The guessing goes; the auto look stays (James's idea). On the R6 sequence,
  smoothing over 50 frames took the biggest frame-to-frame change from 669 K
  to 43 K and kept the 5250 → 7250 K journey.
- ``first``: the first frame's as-shot Kelvin throughout.
- a number: that Kelvin throughout.

Smoothing happens in mireds (a million over Kelvin), where equal steps look
equally big to the eye; in Kelvin, a 100 K step at 3000 K is far bigger than
at 7000 K.

The relative Temperature/Tint sliders (your keyframe grading) are left as
they are. Undo restores each frame's previous white balance.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from timelapse_core import Sequence, smooth_brightness

from .sidecar import read_sidecar, write_sidecar
from .state import keep_original, read_ledger, write_ledger

DEFAULT_SMOOTHNESS = 50  # frames


@dataclass(frozen=True)
class WhiteBalancePlan:
    names: tuple[str, ...]
    as_shot: np.ndarray        # Kelvin
    kelvin: np.ndarray         # Kelvin to write
    tint: float

    @property
    def biggest_jump_before(self) -> float:
        return float(np.abs(np.diff(self.as_shot)).max()) if len(self.as_shot) > 1 else 0.0

    @property
    def biggest_jump_after(self) -> float:
        return float(np.abs(np.diff(self.kelvin)).max()) if len(self.kelvin) > 1 else 0.0


def plan_white_balance(seq: Sequence, mode: str | float = "smooth",
                       smoothness: int = DEFAULT_SMOOTHNESS, tint: float = 0.0) -> WhiteBalancePlan:
    """Work out each frame's Kelvin. Writes nothing."""
    as_shot = [f.white_balance for f in seq]
    missing = [f.name for f, k in zip(seq, as_shot) if not k]
    if missing and mode != "first" and not isinstance(mode, (int, float)):
        raise ValueError(f"Couldn't read the as-shot white balance of {len(missing)} frames "
                         f"(first: {missing[0]}). This needs ExifTool installed.")
    if mode == "first" and not as_shot[0]:
        raise ValueError(f"Couldn't read the as-shot white balance of {seq[0].name}. This needs ExifTool.")

    shot = np.array([k or np.nan for k in as_shot], dtype=np.float64)
    if mode == "smooth":
        kelvin = 1e6 / smooth_brightness(1e6 / shot, smoothness)
    elif mode == "first":
        kelvin = np.full(len(seq), shot[0])
    else:
        kelvin = np.full(len(seq), float(mode))
    if np.any(kelvin < 2000) or np.any(kelvin > 50000):
        raise ValueError("That white balance is outside 2,000 to 50,000 K.")
    return WhiteBalancePlan(tuple(f.name for f in seq), shot, np.round(kelvin), float(tint))


def apply_white_balance(seq: Sequence, plan: WhiteBalancePlan) -> int:
    """Write each frame's Kelvin into RapidRAW's whiteBalance field."""
    missing = [f.name for f in seq if read_sidecar(f.path) is None]
    if missing:
        raise ValueError(f"{len(missing)} frames have no RapidRAW sidecar (first: {missing[0]}). "
                         "Paste settings onto every frame first.")
    ledger = read_ledger(seq.folder)
    tint = int(plan.tint) if plan.tint == int(plan.tint) else plan.tint
    for f, k in zip(seq, plan.kelvin):
        data = read_sidecar(f.path)
        adjustments = data.setdefault("adjustments", {})
        entry = ledger.setdefault(f.name, {"base": float(adjustments.get("exposure") or 0.0), "applied": 0.0})
        if "white_balance_before" not in entry:
            entry["white_balance_before"] = adjustments.get("whiteBalance")
        keep_original(f.path)
        adjustments["whiteBalance"] = {"temperature": int(k), "tint": tint}
        entry["white_balance"] = adjustments["whiteBalance"]
        write_sidecar(f.path, data)
    write_ledger(seq.folder, ledger)
    return len(seq)


def undo_white_balance(seq: Sequence) -> int:
    """Each frame back to the white balance it had before this tool set one."""
    ledger = read_ledger(seq.folder)
    changed = 0
    for f in seq:
        entry = ledger.get(f.name)
        if not entry or "white_balance_before" not in entry:
            continue
        data = read_sidecar(f.path)
        if data is None:
            continue
        data.setdefault("adjustments", {})["whiteBalance"] = entry.pop("white_balance_before")
        entry.pop("white_balance", None)
        write_sidecar(f.path, data)
        changed += 1
    write_ledger(seq.folder, ledger)
    return changed
