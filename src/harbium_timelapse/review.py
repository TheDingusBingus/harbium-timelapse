"""Which frames are worth a look? A second opinion after finishing.

Real light changes go one way over many frames (the sun on a building, night
falling); flicker goes out and comes back. So a frame is flagged when it sits
away from the midpoint of its two neighbours by more than a visible amount.
A ramp passes through that midpoint however steep it is; a spike doesn't.
(A simpler rule, "jumps one way, then back", missed a real spike on the
steepest part of nightfall in the mockup.) Only the peak of each spike is
flagged, so one bad frame doesn't drag its neighbours into the list.

The first and last ``EDGE`` frames have a neighbour on one side only, so
nobody can tell a ramp from a spike there: they're "can't judge", not
flagged.

``camera_motion`` measures how far the picture shifts between frames: the
measurement compares each pixel with the same pixel in the previous frame,
which only works when the camera is still. Hyperlapses aren't supported yet.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

VISIBLE = 0.03          # stops: about the smallest frame-to-frame change people notice
COLOUR_VISIBLE = 0.03   # same scale for the warm/cool balance
EDGE = 2                # frames at each end that can't be judged
MOVING = 0.01           # median shift between frames, as a share of the picture width


@dataclass(frozen=True)
class Finding:
    frame: int          # 0-based index
    name: str
    kind: str           # "brightness" or "colour"
    size: float         # signed: how far from the neighbours' midpoint
    note: str


def spikes(values, names: list[str] | None = None, kind: str = "brightness",
           visible: float = VISIBLE) -> list[Finding]:
    """Frames that break away from both neighbours by more than ``visible``."""
    y = np.asarray(values, dtype=np.float64)
    n = len(y)
    names = names or [str(i + 1) for i in range(n)]
    off = np.full(n, np.nan)
    if n >= 3:
        off[1:-1] = y[1:-1] - (y[:-2] + y[2:]) / 2
    found = []
    for i in range(EDGE, n - EDGE):
        d = off[i]
        if not np.isfinite(d) or abs(d) <= visible:
            continue
        neighbours = [abs(off[j]) for j in (i - 1, i + 1) if np.isfinite(off[j])]
        if any(abs(d) < m for m in neighbours):
            continue                                    # only the peak of the spike
        if kind == "brightness":
            note = f"{abs(d):.2f} stops {'brighter' if d > 0 else 'darker'} than its neighbours"
        else:
            note = f"{'warmer' if d > 0 else 'cooler'} than its neighbours ({abs(d):.2f})"
        found.append(Finding(i, names[i], kind, float(d), note))
    return found


def review(measured: np.ndarray, names: list[str] | None = None) -> list[Finding]:
    """Brightness and colour spikes in a frames x (red, green, blue) series of stops, biggest first."""
    m = np.asarray(measured, dtype=np.float64)
    brightness = m @ np.array([0.2126, 0.7152, 0.0722])
    warmth = m[:, 0] - m[:, 2]
    found = spikes(brightness, names, "brightness") + spikes(warmth, names, "colour", COLOUR_VISIBLE)
    return sorted(found, key=lambda f: -abs(f.size))


def edges(n: int) -> list[int]:
    """Frames that can't be judged: too close to either end."""
    return sorted(set(range(min(EDGE, n))) | set(range(max(0, n - EDGE), n)))


def shift(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """How far ``b`` is shifted from ``a``, in pixels (phase correlation)."""
    a = a - a.mean()
    b = b - b.mean()
    window = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1]))
    f = np.fft.fft2(a * window) * np.conj(np.fft.fft2(b * window))
    r = np.fft.ifft2(f / (np.abs(f) + 1e-12)).real
    y, x = np.unravel_index(int(r.argmax()), r.shape)
    h, w = r.shape
    return float(y if y < h / 2 else y - h), float(x if x < w / 2 else x - w)


def camera_motion(shifts: list[tuple[float, float]], width: int) -> float:
    """Typical movement between frames as a share of the picture width."""
    if not shifts or width <= 0:
        return 0.0
    return float(np.median([np.hypot(*s) for s in shifts]) / width)


def moving_camera_note(motion: float) -> str | None:
    if motion < MOVING:
        return None
    return (f"The picture moves by about {motion * 100:.0f}% of its width between frames, so the camera "
            "seems to move (a hyperlapse?). Moving cameras aren't supported yet: the measurement compares "
            "each pixel with the same pixel in the previous frame, so results will be unreliable.")
