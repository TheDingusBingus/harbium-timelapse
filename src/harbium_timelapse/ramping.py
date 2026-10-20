"""Your exposure changes: find them, take them out, give them back gently.

During a sunset you change exposure to keep up with the light: ISO up a
third, shutter longer, an ND filter turned. Each change is a step. In a
timelapse, a step is a jolt.

How steps are found:

- **Where:** wherever the camera settings change (ISO, shutter, aperture),
  plus anywhere the pictures jump suddenly and stay jumped with no setting
  change to explain it. That's what a variable ND filter looks like, since it
  leaves no trace in the file.
- **How big:** always measured from the pictures, never taken from the
  settings. On real files the two don't always agree (a Canon R6 sequence
  showed third-stop ISO changes measuring anywhere from 0 to +0.3 stops), and
  the pictures are what will be on screen.

A one-frame excursion (a single frame at f/22 among f/20s, lightning, a
flash) is not a step: a step has to last. Those are left to deflicker, which
treats them as odd frames.

Deflicker then works on the light with your steps taken out, and your steps
are added back as one long, gentle ramp. You keep the brightness you were
going for. You lose the jolts.
"""

from __future__ import annotations

import numpy as np

from .types import ExposureChange, Frame

LASTING = 5          # frames a jump must hold, either side, to count as lasting
MEASURE = 20         # frames either side used to measure a step's size (tested: unbiased, ~0.05 stop error at typical flicker)
SMALLEST_JUMP = 0.3  # stops; an unexplained jump smaller than this is left to deflicker
MIN_STRETCH = 2      # frames; a setting that lasts less than this is a blip, not a step


def camera_exposure(frames: tuple[Frame, ...]) -> np.ndarray:
    """How much brighter each frame's settings should make it than the first
    frame's, in stops. Frames with unknown settings carry the last known value."""
    out = np.zeros(len(frames))
    first = last = None
    for i, f in enumerate(frames):
        ev = f.exposure_value
        if ev is not None:
            first = ev if first is None else first
            last = ev
        out[i] = 0.0 if last is None else first - last
    return out


def find_exposure_changes(
    frames: tuple[Frame, ...], brightness: np.ndarray
) -> tuple[np.ndarray, list[ExposureChange]]:
    """Every lasting exposure step in the sequence.

    Returns the running total of the steps per frame (stops, positive =
    brighter than the first frame), and a description of each step.
    """
    b = np.asarray(brightness, dtype=np.float64)
    n = len(b)
    if n < 2 * MIN_STRETCH + 1:
        return np.zeros(n), []

    settings = camera_exposure(frames)
    claimed = {i: float(settings[i] - settings[i - 1])
               for i in range(1, n) if abs(settings[i] - settings[i - 1]) > 0.01}
    unexplained = [i for i in _sudden_lasting_jumps(b)
                   if all(abs(i - j) > MIN_STRETCH for j in claimed)]

    edges, blips = _drop_blips(sorted(set(claimed) | set(unexplained)), n)
    usable = np.ones(n, dtype=bool)
    usable[list(blips)] = False

    # With blips set aside, a setting change is judged against the last frame
    # that isn't a blip. f/20, f/22 (one frame), f/20 is no change at all.
    def net_claim(i):
        j = i - 1
        while j > 0 and not usable[j]:
            j -= 1
        change = float(settings[i] - settings[j])
        return change if abs(change) > 0.01 else None

    claimed = {i: c for i in edges if i in claimed and (c := net_claim(i)) is not None}
    edges = [i for i in edges if i in claimed or i in unexplained]

    total = np.zeros(n)
    changes: list[ExposureChange] = []
    bounds = [0, *edges, n]
    for k, i in enumerate(edges, start=1):
        size = _measure_step(b, usable, i,
                             start=max(bounds[k - 1], i - MEASURE),
                             stop=min(bounds[k + 1], i + MEASURE))
        if size is None:
            continue
        total[i:] += size
        changes.append(ExposureChange(
            frame=i,
            stops=size,
            cause="camera settings" if i in claimed else "not in camera settings",
            expected=claimed.get(i),
        ))
    return total, changes


def _sudden_lasting_jumps(b: np.ndarray) -> list[int]:
    n = len(b)
    if n < 2 * LASTING + 1:
        return []
    noise = 1.4826 * np.median(np.abs(np.diff(b)))
    threshold = max(SMALLEST_JUMP, 4 * noise)
    candidates = [
        i for i in range(LASTING, n - LASTING + 1)
        if abs(np.median(b[i:i + LASTING]) - np.median(b[i - LASTING:i])) > threshold
        and abs(b[i] - b[i - 1]) > threshold / 2
    ]
    picked: list[int] = []  # neighbouring candidates are one jump: keep the sharpest edge
    for i in candidates:
        if picked and i - picked[-1] < LASTING:
            if abs(b[i] - b[i - 1]) > abs(b[picked[-1]] - b[picked[-1] - 1]):
                picked[-1] = i
        else:
            picked.append(i)
    return picked


def _drop_blips(edges: list[int], n: int) -> tuple[list[int], set[int]]:
    """A setting that only lasts a frame is a blip. Its frames are set aside
    and its edge removed, so the step either side of it is measured cleanly."""
    kept, blips = [], set()
    for k, i in enumerate(edges):
        nxt = edges[k + 1] if k + 1 < len(edges) else n
        if nxt - i < MIN_STRETCH:
            blips.update(range(i, nxt))
        else:
            kept.append(i)
    return kept, blips


def _measure_step(b, usable, i, start, stop) -> float | None:
    """Size of the step at frame i: fit one straight line through the frames
    either side, allowing a jump at i. The shared slope stops a sunset's own
    darkening being mistaken for part of the step."""
    x = np.arange(start, stop)
    keep = usable[start:stop]
    x, y = x[keep], b[start:stop][keep]
    after = x >= i
    if after.sum() < MIN_STRETCH or (~after).sum() < MIN_STRETCH:
        return None
    design = np.column_stack([np.ones(len(x)), x - i, after.astype(float)])
    coef, *_ = np.linalg.lstsq(design, y, rcond=None)
    return float(coef[2])
