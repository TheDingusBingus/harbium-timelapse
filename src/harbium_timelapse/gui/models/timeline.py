"""The timeline, drawn by the app itself (GUI_PLAN.md, ADR-006).

A light-meter record over time: measured light as a fine line, the smoothing
curve heavier on top, exposure changes as notches on the time axis, flags as
diamonds above the line, the current frame as a dotted line. Drawn with Pillow
into an RGB array that the screen shows with ``RawImage``; clicks map back to
a frame with ``frame_at``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

PAD = 12
TOP = 18            # room above the plot for flag diamonds


@dataclass(frozen=True)
class Series:
    values: np.ndarray
    color: tuple[int, int, int]
    width: int = 1


@dataclass(frozen=True)
class Theme:
    background: tuple[int, int, int] = (17, 17, 17)       # brand panel, dark
    grid: tuple[int, int, int] = (31, 31, 31)
    text: tuple[int, int, int] = (168, 163, 154)
    mark: tuple[int, int, int] = (232, 228, 219)          # bone: flags, current frame


DARK = Theme()
LIGHT = Theme(background=(240, 237, 230), grid=(199, 193, 181), text=(94, 90, 82), mark=(10, 10, 10))


def _range(series: list[Series]) -> tuple[float, float]:
    values = np.concatenate([np.asarray(s.values, dtype=np.float64) for s in series])
    values = values[np.isfinite(values)]
    if values.size == 0:
        return -1.0, 1.0
    lo, hi = float(values.min()), float(values.max())
    span = max(hi - lo, 0.2)
    return lo - 0.08 * span, hi + 0.08 * span


def render_timeline(series: list[Series], width: int, height: int, theme: Theme = DARK,
                    steps: tuple[int, ...] = (), flags: tuple[int, ...] = (), current: int | None = None,
                    cant_judge: tuple[int, ...] = ()) -> np.ndarray:
    """The timeline as an RGB image (height x width x 3, uint8)."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (width, height), theme.background)
    if not series or len(series[0].values) < 2:
        return np.asarray(im)
    d = ImageDraw.Draw(im)
    n = len(series[0].values)
    lo, hi = _range(series)
    left, right, top, bottom = PAD, width - PAD, TOP + 6, height - PAD - 8

    def x(i: float) -> float:
        return left + i * (right - left) / (n - 1)

    def y(v: float) -> float:
        return top + (hi - v) / (hi - lo) * (bottom - top)

    for stop in range(int(np.ceil(lo)), int(np.floor(hi)) + 1):          # a line at every whole stop
        d.line([(left, y(stop)), (right, y(stop))], fill=theme.grid)
    for s in series:
        v = np.asarray(s.values, dtype=np.float64)
        points = [(x(i), y(float(v[i]))) for i in range(n) if np.isfinite(v[i])]
        if len(points) > 1:
            d.line(points, fill=s.color, width=s.width)
    for i in steps:                                                    # exposure changes: notches
        d.polygon([(x(i) - 4, height - 2), (x(i) + 4, height - 2), (x(i), height - 9)], fill=theme.text)
    for i in flags:                                                    # worth a look: diamonds
        cx, cy = x(i), TOP / 2 + 2
        d.polygon([(cx, cy - 6), (cx + 6, cy), (cx, cy + 6), (cx - 6, cy)], fill=theme.mark)
    for i in cant_judge[:1] + cant_judge[-1:]:                         # one "?" at each end
        d.text((x(i) - 3, 1), "?", fill=theme.text)
    if current is not None and 0 <= current < n:
        for yy in range(int(top - 4), int(bottom), 6):                 # dotted line
            d.line([(x(current), yy), (x(current), yy + 3)], fill=theme.mark)
    return np.asarray(im)


def frame_at(click_x: float, width: int, frames: int) -> int:
    """Which frame a click at ``click_x`` pixels from the left points at."""
    if frames < 2:
        return 0
    i = round((click_x - PAD) / (width - 2 * PAD) * (frames - 1))
    return int(min(max(i, 0), frames - 1))
