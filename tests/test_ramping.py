"""Exposure changes made while shooting. Cases drawn from a real Canon R6
dusk sequence: hand-turned ND steps, third-stop ISO steps that didn't always
show in the pictures, and one stray frame at f/22."""

import math
from pathlib import Path

import numpy as np
import pytest

from timelapse_core import Frame, Sequence, find_exposure_changes, smooth_flicker


def dusk(n, drop=2.0):
    return -drop / (1 + np.exp(-np.linspace(-4, 4, n)))


def noise(n, size=0.07, seed=3):
    return np.random.default_rng(seed).normal(0, size, n)


def frames(n, iso=None, aperture=None):
    iso = iso or {}
    aperture = aperture or {}
    out, current = [], 100
    for i in range(n):
        current = iso.get(i, current)
        out.append(Frame(Path(f"{i:04d}.CR3"), shutter=1 / 100, aperture=aperture.get(i, 20.0), iso=current))
    return Sequence(Path("."), tuple(out))


def biggest_jolt(fix):
    return float(np.abs(np.diff(fix.brightness + fix.exposure)).max())


def test_hand_turned_nd_step_is_found_and_blended():
    n = 200
    b = dusk(n) + noise(n)
    b[80:] += 1.0                                  # ND opened a stop at frame 80
    fix = smooth_flicker(frames(n), brightness=b)

    assert [(c.frame, c.cause) for c in fix.changes] == [(80, "not in camera settings")]
    assert fix.changes[0].stops == pytest.approx(1.0, abs=0.15)
    assert biggest_jolt(fix) < 0.06


def test_iso_step_is_measured_from_the_pictures():
    n = 120
    b = dusk(n) + noise(n)
    b[60:] += 1 / 3
    fix = smooth_flicker(frames(n, iso={60: 125}), brightness=b)
    (change,) = fix.changes
    assert change.cause == "camera settings"
    assert change.expected == pytest.approx(math.log2(1.25))
    assert change.stops == pytest.approx(1 / 3, abs=0.08)


def test_iso_step_the_pictures_dont_show_invents_nothing():
    """Seen on the R6: ISO 100 -> 125, no visible change in the RAW data."""
    n = 120
    b = dusk(n) + noise(n)
    fix = smooth_flicker(frames(n, iso={60: 125}), brightness=b)
    assert [c.cause for c in fix.changes] == ["camera settings"]
    assert abs(fix.changes[0].stops) < 0.08
    assert np.abs(np.diff((fix.brightness + fix.exposure)[50:70])).max() < 0.05  # no jolt either side


def test_one_stray_frame_at_a_different_aperture_is_not_a_step():
    """Seen on the R6: a single frame at f/22 among f/20s."""
    n = 120
    b = dusk(n) + noise(n, size=0.02)
    b[70] -= 2 * math.log2(22 / 20)
    fix = smooth_flicker(frames(n, aperture={70: 22.0}), brightness=b)
    assert fix.changes == ()
    fixed = fix.brightness + fix.exposure
    assert abs(fixed[70] - (fixed[69] + fixed[71]) / 2) < 0.05


def test_lightning_is_not_a_step():
    n = 120
    b = dusk(n) + noise(n, size=0.02)
    b[50] += 2.0
    _, changes = find_exposure_changes(frames(n).frames, b)
    assert changes == []


@pytest.mark.parametrize("seed", range(20))
def test_plain_flicker_never_looks_like_an_nd_step(seed):
    n = 230
    b = dusk(n) + np.random.default_rng(seed).normal(0, 0.1, n)
    _, changes = find_exposure_changes(frames(n).frames, b)
    assert changes == []


def test_nd_dip_and_recovery_is_lifted_back_into_line():
    """Seen on the R6: ND darker for eleven frames, then back."""
    n = 200
    b = dusk(n) + noise(n)
    b[60:71] -= 0.75
    fix = smooth_flicker(frames(n), brightness=b)
    assert [c.frame for c in fix.changes] == [60, 71]
    assert biggest_jolt(fix) < 0.06


def test_wider_blend_means_gentler_ramp():
    n = 200
    b = dusk(n) + noise(n)
    b[100:] += 1.5
    narrow = biggest_jolt(smooth_flicker(frames(n), brightness=b, blend=30))
    wide = biggest_jolt(smooth_flicker(frames(n), brightness=b, blend=120))
    assert wide < narrow
