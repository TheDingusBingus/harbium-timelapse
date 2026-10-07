"""Keyframes: the frames you edit by hand. Everything between is blended.

Two jobs live here, both editor-agnostic:

- **Suggest where keyframes belong.** Not where the brightness jumps (those
  are your own exposure moves, which ``ramping`` already smooths), but where
  the *light itself* has changed character: about a stop dimmer, or
  noticeably warmer or bluer, since the last keyframe. The first and last
  frames are always keyframes.
- **Blend a value between keyframes.** A smooth curve that passes exactly
  through every keyframe, never overshoots between them (a contrast that
  goes 10 → 30 never visits 32 on the way), and eases in and out at the
  ends rather than starting or stopping abruptly.

Applying blended values to a particular editor's settings is an
integration's job.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .deflicker import DEFAULT_WINDOW, smooth_brightness
from .ramping import find_exposure_changes
from .types import Sequence


@dataclass(frozen=True)
class KeyframeSuggestion:
    frame: int
    reason: str


def suggest_keyframes(
    sequence: Sequence,
    brightness: np.ndarray,
    warmth: np.ndarray | None = None,
    every: float = 1.0,
    warmth_every: float = 0.5,
    closest: int | None = None,
) -> list[KeyframeSuggestion]:
    """Where to put keyframes, and why.

    - ``every``: suggest a keyframe each time the light has changed by this
      many stops since the last one.
    - ``warmth_every``: or each time its colour has shifted this much
      (red-over-blue, in stops).
    - ``closest``: never put two keyframes nearer than this many frames.
      Default: about a thirtieth of the sequence, at least 5.
    """
    b = np.asarray(brightness, dtype=np.float64)
    n = len(b)
    if n == 0:
        return []
    if n == 1:
        return [KeyframeSuggestion(0, "only frame")]
    closest = closest if closest is not None else max(5, round(n / 30))

    yours, _ = find_exposure_changes(sequence.frames, b)
    light = smooth_brightness(b - yours, DEFAULT_WINDOW)
    colour = None if warmth is None else smooth_brightness(np.asarray(warmth, dtype=np.float64), DEFAULT_WINDOW)

    picks = [KeyframeSuggestion(0, "first frame")]
    k = 0
    for i in range(closest, n):
        if i - k < closest:
            continue
        dimmer = light[k] - light[i]
        shift = 0.0 if colour is None else colour[i] - colour[k]
        reasons = []
        if abs(dimmer) >= every:
            reasons.append(f"light {abs(dimmer):.1f} stops {'dimmer' if dimmer > 0 else 'brighter'} than the last keyframe")
        if abs(shift) >= warmth_every:
            reasons.append(f"light noticeably {'warmer' if shift > 0 else 'bluer'}")
        if reasons:
            picks.append(KeyframeSuggestion(i, " and ".join(reasons)))
            k = i

    last = n - 1
    if last - picks[-1].frame < closest and picks[-1].frame != 0:
        picks[-1] = KeyframeSuggestion(last, "last frame (" + picks[-1].reason + ")")
    elif picks[-1].frame != last:
        picks.append(KeyframeSuggestion(last, "last frame"))
    return picks


def blend_between(keyframes, values, length: int) -> np.ndarray:
    """One value per frame, blended smoothly between keyframe values.

    ``keyframes`` are frame positions (ascending), ``values`` the value at
    each. Before the first keyframe and after the last, the value holds.
    Monotone cubic (Fritsch–Carlson), with flat ends for an eased start and
    finish.
    """
    x = np.asarray(keyframes, dtype=np.float64)
    y = np.asarray(values, dtype=np.float64)
    if len(x) != len(y) or len(x) == 0:
        raise ValueError("need one value per keyframe")
    if np.any(np.diff(x) <= 0):
        raise ValueError("keyframes must be in order, with no repeats")
    out = np.empty(length)
    if len(x) == 1:
        out[:] = y[0]
        return out

    h = np.diff(x)
    d = np.diff(y) / h
    m = np.zeros(len(x))  # flat at the first and last keyframe: ease in, ease out
    for k in range(1, len(x) - 1):
        if d[k - 1] * d[k] > 0:
            w1, w2 = 2 * h[k] + h[k - 1], h[k] + 2 * h[k - 1]
            m[k] = (w1 + w2) / (w1 / d[k - 1] + w2 / d[k])

    t = np.arange(length, dtype=np.float64)
    out[t <= x[0]] = y[0]
    out[t >= x[-1]] = y[-1]
    inside = (t > x[0]) & (t < x[-1])
    j = np.searchsorted(x, t[inside], side="right") - 1
    s = (t[inside] - x[j]) / h[j]
    h00 = (1 + 2 * s) * (1 - s) ** 2
    h10 = s * (1 - s) ** 2
    h01 = s**2 * (3 - 2 * s)
    h11 = s**2 * (s - 1)
    out[inside] = h00 * y[j] + h10 * h[j] * m[j] + h01 * y[j + 1] + h11 * h[j] * m[j + 1]
    return out
