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

Kelvin and tint are on RapidRAW's own scale, worked out from each frame's raw
as-shot neutral with the colour matrices RapidRAW itself uses (``cameras.py``)
and the standard DNG maths (``harbium_timelapse.colour_temperature``). With no
jitter to remove, what's written is exactly what RapidRAW already shows as
shot, so the only change is the smoothing. Tint is smoothed alongside. If the
neutral or the camera's matrices aren't available, it falls back to the
camera's own Kelvin estimate and tint 0, and says so.

The relative Temperature/Tint sliders (your keyframe grading) are left as
they are; RapidRAW applies them on top of the absolute value (read in its
source, Oct 2026: 1.5 mired and 1.5 tint per slider unit). Undo restores each
frame's previous white balance.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from harbium_timelapse import Sequence, smooth_brightness
from harbium_timelapse.colour_temperature import kelvin_tint_from_neutral

from .cameras import matrices_for
from .sidecar import finite, read_sidecar, write_sidecar
from .state import keep_original, ledger_kept_in_step

DEFAULT_SMOOTHNESS = 50  # frames

EXACT = "RapidRAW's own scale, from the camera's raw colour"
CAMERA_GUESS = "the camera's Kelvin estimate (tint not known)"


@dataclass(frozen=True)
class WhiteBalancePlan:
    names: tuple[str, ...]
    as_shot: np.ndarray        # Kelvin
    kelvin: np.ndarray         # Kelvin to write
    tint: np.ndarray           # tint to write, per frame
    scale: str = CAMERA_GUESS  # where the as-shot numbers came from
    as_shot_tint: np.ndarray | None = None
    notes: tuple[str, ...] = field(default=())

    @property
    def biggest_jump_before(self) -> float:
        return float(np.abs(np.diff(self.as_shot)).max()) if len(self.as_shot) > 1 else 0.0

    @property
    def biggest_jump_after(self) -> float:
        return float(np.abs(np.diff(self.kelvin)).max()) if len(self.kelvin) > 1 else 0.0


def as_shot_on_rapidraws_scale(seq: Sequence) -> tuple[np.ndarray, np.ndarray] | None:
    """Each frame's as-shot Kelvin and tint exactly as RapidRAW reads them.

    ``None`` if any frame lacks a raw neutral or the camera's matrices
    aren't known yet.
    """
    kelvin, tint = [], []
    for f in seq:
        m = matrices_for(f.camera)
        if f.white_balance_neutral is None or m is None:
            return None
        k, t = kelvin_tint_from_neutral(f.white_balance_neutral, m["D65"], m.get("A"))
        kelvin.append(k)
        tint.append(t)
    return np.array(kelvin), np.array(tint)


def plan_white_balance(seq: Sequence, mode: str | float = "smooth",
                       smoothness: int = DEFAULT_SMOOTHNESS, tint: float | None = None) -> WhiteBalancePlan:
    """Work out each frame's Kelvin and tint. Writes nothing.

    When every frame's raw neutral can be read and the camera is known, the
    as-shot values are worked out on RapidRAW's own scale, so with no jitter
    the written white balance is exactly what RapidRAW already shows; tint is
    smoothed alongside temperature. Otherwise the camera's Kelvin estimate is
    used and tint is written as 0 (or ``tint``), which may shift the colour.
    """
    names = tuple(f.name for f in seq)
    exact = as_shot_on_rapidraws_scale(seq)
    notes: list[str] = []
    if exact is not None:
        shot, shot_tint, scale = exact[0], exact[1], EXACT
    else:
        scale, shot_tint = CAMERA_GUESS, None
        as_shot = [f.white_balance for f in seq]
        missing = [f.name for f, k in zip(seq, as_shot) if not k]
        if missing and mode != "first" and not isinstance(mode, (int, float)):
            raise ValueError(f"Couldn't read the as-shot white balance of {len(missing)} frames "
                             f"(first: {missing[0]}). This needs ExifTool installed.")
        if mode == "first" and not as_shot[0]:
            raise ValueError(f"Couldn't read the as-shot white balance of {seq[0].name}. This needs ExifTool.")
        shot = np.array([k or np.nan for k in as_shot], dtype=np.float64)
        if not isinstance(mode, (int, float)):
            notes.append("Used the camera's own Kelvin estimate, which isn't on RapidRAW's scale, and no tint: "
                         "the whole sequence may come out slightly warmer, cooler or greener than as shot. "
                         f"{_why_not_exact(seq)}")

    if mode == "smooth":
        kelvin = 1e6 / smooth_brightness(1e6 / shot, smoothness)
        tints = (smooth_brightness(shot_tint, smoothness) if shot_tint is not None
                 else np.zeros(len(seq)))
    elif mode == "first":
        kelvin = np.full(len(seq), shot[0])
        tints = np.full(len(seq), shot_tint[0] if shot_tint is not None else 0.0)
    else:
        kelvin = np.full(len(seq), float(mode))
        tints = np.zeros(len(seq))
    if tint is not None:
        tints = np.full(len(seq), float(tint))
    if np.any(kelvin < 2000) or np.any(kelvin > 50000):
        raise ValueError("That white balance is outside 2,000 to 50,000 K.")
    if np.any(np.abs(tints) > 150):
        raise ValueError("That tint is outside -150 to +150.")
    return WhiteBalancePlan(names, shot, np.round(kelvin), np.round(tints, 1),
                            scale, shot_tint, tuple(notes))


def _why_not_exact(seq: Sequence) -> str:
    unknown = sorted({f.camera or "unknown camera" for f in seq if matrices_for(f.camera) is None})
    if unknown:
        return (f"RapidRAW's colour matrices for {', '.join(unknown)} aren't in cameras.py yet.")
    missing = [f.name for f in seq if f.white_balance_neutral is None]
    return (f"Couldn't read the raw colour (as-shot neutral) of {len(missing)} frames "
            f"(first: {missing[0]}); this needs ExifTool.")


def apply_white_balance(seq: Sequence, plan: WhiteBalancePlan) -> int:
    """Write each frame's Kelvin into RapidRAW's whiteBalance field."""
    missing = [f.name for f in seq if read_sidecar(f.path) is None]
    if missing:
        raise ValueError(f"{len(missing)} frames have no RapidRAW sidecar (first: {missing[0]}). "
                         "Paste settings onto every frame first.")
    for k, t in zip(plan.kelvin, plan.tint, strict=True):   # every value checked before anything is written
        finite(k, "white balance")
        finite(t, "tint")
    with ledger_kept_in_step(seq.folder) as ledger:
        for f, k, t in zip(seq, plan.kelvin, plan.tint, strict=True):
            tint = int(t) if t == int(t) else float(t)
            data = read_sidecar(f.path)
            adjustments = data.setdefault("adjustments", {})
            before = adjustments.get("whiteBalance")
            keep_original(f.path)
            adjustments["whiteBalance"] = {"temperature": int(k), "tint": tint}
            write_sidecar(f.path, data)
            # recorded only once the sidecar really holds it
            entry = ledger.setdefault(f.name, {"base": float(adjustments.get("exposure") or 0.0), "applied": 0.0})
            entry.setdefault("white_balance_before", before)
            entry["white_balance"] = adjustments["whiteBalance"]
    return len(seq)


def undo_white_balance(seq: Sequence) -> int:
    """Each frame back to the white balance it had before this tool set one."""
    changed = 0
    with ledger_kept_in_step(seq.folder) as ledger:
        for f in seq:
            entry = ledger.get(f.name)
            if not entry or "white_balance_before" not in entry:
                continue
            data = read_sidecar(f.path)
            if data is None:
                continue
            data.setdefault("adjustments", {})["whiteBalance"] = entry["white_balance_before"]
            write_sidecar(f.path, data)
            entry.pop("white_balance_before")       # forgotten only once the sidecar is back
            entry.pop("white_balance", None)
            changed += 1
    return changed
