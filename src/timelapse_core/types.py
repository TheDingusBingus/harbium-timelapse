"""The contract. Every other module is built on these shapes.

Units rule: anything about brightness or exposure is measured in **stops**.
Positive means brighter. Nothing in the public API uses raw luminance
numbers, percentages, or editor-specific slider units.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

import numpy as np


@dataclass(frozen=True)
class Frame:
    """One photograph in a timelapse, plus the camera settings it was shot at.

    Any setting can be ``None`` if the file didn't carry it or couldn't be read.
    """

    path: Path
    taken_at: datetime | None = None
    shutter: float | None = None   # seconds, e.g. 0.008 for 1/125
    aperture: float | None = None  # f-number, e.g. 8.0
    iso: float | None = None
    camera: str | None = None
    white_balance: float | None = None  # as-shot, Kelvin (needs ExifTool)
    # As-shot neutral: how the sensor's red, green and blue saw grey, green = 1.
    # The camera's own colour numbers, before any editor's Kelvin scale. Needs ExifTool.
    white_balance_neutral: tuple[float, float, float] | None = None

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def exposure_value(self) -> float | None:
        """EV at ISO 100 for these settings. One higher = one stop less light.

        ``None`` if shutter, aperture or ISO is unknown.
        """
        if not (self.shutter and self.aperture and self.iso):
            return None
        return math.log2(self.aperture**2 / self.shutter) - math.log2(self.iso / 100)


@dataclass(frozen=True)
class Sequence:
    """An ordered timelapse: the frames, in the order they were shot."""

    folder: Path
    frames: tuple[Frame, ...]

    def __len__(self) -> int:
        return len(self.frames)

    def __iter__(self) -> Iterator[Frame]:
        return iter(self.frames)

    def __getitem__(self, i: int) -> Frame:
        return self.frames[i]


@dataclass(frozen=True)
class Problem:
    """Something worth knowing about a sequence before you process it.

    ``serious`` means results will probably be wrong if you carry on.
    Otherwise it's a note: worth reading, safe to ignore.
    """

    kind: str
    message: str
    frame: int | None = None
    serious: bool = False

    def __str__(self) -> str:
        prefix = "Problem" if self.serious else "Note"
        return f"{prefix}: {self.message}"


@dataclass(frozen=True)
class ExposureChange:
    """A step in exposure that you made while shooting.

    ``stops`` is how big the step measured in the pictures, positive when
    the frame got brighter. ``cause`` is "camera settings" (ISO, shutter,
    aperture) or "not in camera settings" (most often a variable ND filter).
    ``expected`` is what the camera settings alone predicted, if they changed.
    """

    frame: int
    stops: float
    cause: str
    expected: float | None = None


@dataclass(frozen=True, eq=False)
class FlickerCorrections:
    """What deflicker found, and what to do about it. All values in stops.

    - ``brightness``: how bright each frame measured
    - ``target``: the smooth curve the sequence should follow
    - ``exposure``: how much to brighten (+) or darken (-) each frame
    - ``changes``: the exposure steps you made while shooting, which were
      blended into a gentle ramp rather than treated as flicker
    """

    frames: tuple[Frame, ...]
    brightness: np.ndarray
    target: np.ndarray
    exposure: np.ndarray
    changes: tuple[ExposureChange, ...] = ()

    def by_filename(self) -> dict[str, float]:
        return {f.name: float(e) for f, e in zip(self.frames, self.exposure)}

    @property
    def flicker_before(self) -> float:
        """Typical frame-to-frame wobble before correction, in stops.

        Measured as the spread of differences between neighbouring frames,
        so a slow sunset or a deliberate ramp doesn't count as flicker.
        """
        if len(self.brightness) < 3:
            return 0.0
        return float(np.std(np.diff(self.brightness)) / np.sqrt(2))

    @property
    def flicker_after(self) -> float:
        """The same measure, once corrections are applied."""
        if len(self.brightness) < 3:
            return 0.0
        return float(np.std(np.diff(self.brightness + self.exposure)) / np.sqrt(2))

    @property
    def largest_correction(self) -> float:
        return float(np.max(np.abs(self.exposure))) if len(self.exposure) else 0.0
