"""Deflicker: take out the frame-to-frame wobble, keep the light's real arc.

The idea, in photographer terms:

1. Measure how bright every frame is (see ``measure``).
2. Draw a smooth line through those measurements. A sunset still gets
   darker; it just stops shimmering on the way down.
3. Each frame's correction is the distance from where it measured to where
   the line says it should be.

How the line is drawn: for each frame, look at its neighbours (``window``
frames in total) and fit a short straight line through them, trusting the
closest ones most. That's local linear regression (LOWESS). Two properties
matter here:

- **The ends don't droop.** Simple averaging pulls the first and last frames
  towards the middle. A local straight line follows the trend right to the
  edge of the sequence.
- **One odd frame doesn't drag its neighbours.** A lightning strike or a car's
  headlights is spotted as an outlier and mostly ignored when drawing the
  line, so the frames either side aren't darkened to compensate.

Your own exposure changes (ISO steps, a turned ND filter) are handled
first, by ``ramping``: taken out before smoothing, then added back as one
long, gentle ramp. Without that, a deliberate one-stop step would be smeared
into a short fade, and the frames just before it brightened as if they knew
it was coming.
"""

from __future__ import annotations

import numpy as np

from .measure import Progress, measure_brightness
from .ramping import find_exposure_changes
from .types import FlickerCorrections, Sequence

DEFAULT_WINDOW = 25  # frames: one second of video at 25fps


def smooth_flicker(
    sequence: Sequence,
    window: int = DEFAULT_WINDOW,
    strength: float = 1.0,
    speed: str = "careful",
    brightness: np.ndarray | None = None,
    progress: Progress | None = None,
    blend: int | None = None,
) -> FlickerCorrections:
    """Work out how much to brighten or darken each frame to remove flicker.

    - ``window``: how many frames to smooth across. Wider = smoother, but
      slower to follow quick, real changes in light. 15–50 suits most work.
    - ``strength``: 1.0 removes all the flicker it finds. 0.5 removes half,
      for when a little life in the light is wanted.
    - ``speed``: "careful" or "fast". See ``measure_brightness``.
    - ``brightness``: pass measurements you already have to skip measuring.
    - ``blend``: how many frames your own exposure changes are spread across.
      Default: a third of the sequence, and never less than three windows.

    Nothing is written to any file. Apply the corrections in your editor, or
    through an integration.
    """
    if not 0.0 <= strength <= 1.0:
        raise ValueError("strength must be between 0 and 1")
    if brightness is None:
        brightness = measure_brightness(sequence, speed=speed, progress=progress)
    brightness = np.asarray(brightness, dtype=np.float64)
    if len(brightness) != len(sequence):
        raise ValueError("brightness must have one value per frame")

    yours, changes = find_exposure_changes(sequence.frames, brightness)
    if blend is None:
        blend = max(3 * window, len(brightness) // 3)

    light = brightness - yours
    target = smooth_brightness(light, window) + smooth_brightness(yours, blend, ignore_outliers=False)
    return FlickerCorrections(
        frames=sequence.frames,
        brightness=brightness,
        target=target,
        exposure=strength * (target - brightness),
        changes=tuple(changes),
    )


def smooth_brightness(brightness, window: int = DEFAULT_WINDOW, ignore_outliers: bool = True) -> np.ndarray:
    """The smooth curve through a list of brightness values (stops in, stops out)."""
    y = np.asarray(brightness, dtype=np.float64)
    n = len(y)
    window = min(int(window), n)
    if window < 3:
        return y.copy()

    trust = np.ones(n)
    fit = y.copy()
    for _ in range(4 if ignore_outliers else 1):
        fit = _local_lines(y, window, trust)
        if not ignore_outliers:
            break
        residual = y - fit
        spread = np.median(np.abs(residual))
        if spread < 1e-9:
            break
        u = residual / (6.0 * spread)
        trust = np.where(np.abs(u) < 1, (1 - u**2) ** 2, 0.0)
    return fit


def _local_lines(y: np.ndarray, window: int, trust: np.ndarray) -> np.ndarray:
    n = len(y)
    x = np.arange(n, dtype=np.float64)
    out = np.empty(n)
    for i in range(n):
        start = min(max(i - window // 2, 0), n - window)
        idx = slice(start, start + window)
        xs, ys = x[idx], y[idx]

        reach = np.abs(xs - i).max() + 1.0
        w = (1 - (np.abs(xs - i) / reach) ** 3) ** 3 * trust[idx]  # tricube × trust

        total = w.sum()
        if total <= 0:
            out[i] = y[i]
            continue
        mx = (w * xs).sum() / total
        my = (w * ys).sum() / total
        spread_x = (w * (xs - mx) ** 2).sum()
        slope = (w * (xs - mx) * (ys - my)).sum() / spread_x if spread_x > 1e-12 else 0.0
        out[i] = my + slope * (i - mx)
    return out
