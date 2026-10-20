from pathlib import Path

import numpy as np
import pytest

from synthetic import make_folder
from harbium_timelapse import load_sequence, smooth_brightness, smooth_flicker
from harbium_timelapse.types import Frame, Sequence


def jitter(n, size, seed=42):
    return np.random.default_rng(seed).normal(0, size, n)


def sunset(n=300, drop=3.0):
    """S-curve: bright, falling through dusk, levelling off at night."""
    x = np.linspace(-4, 4, n)
    return -drop / (1 + np.exp(-x))


def test_straight_line_is_left_alone_right_to_the_ends():
    line = np.linspace(0, -2, 120)
    assert np.abs(smooth_brightness(line) - line).max() < 1e-9


def test_follows_a_real_sunset_curve():
    truth = sunset()
    assert np.abs(smooth_brightness(truth, window=25) - truth).max() < 0.02


def test_removes_flicker_and_keeps_the_sunset():
    truth = sunset()
    measured = truth + jitter(truth.size, 0.08)
    corrected = smooth_brightness(measured)

    assert np.std(np.diff(corrected)) < np.std(np.diff(measured)) / 10
    assert np.abs(corrected - truth).max() < 0.06
    assert corrected[-1] - corrected[0] == pytest.approx(truth[-1] - truth[0], abs=0.06)


def test_one_lightning_frame_does_not_pull_its_neighbours():
    truth = np.linspace(0, -1, 100)
    measured = truth + jitter(100, 0.02)
    measured[50] += 2.0
    fit = smooth_brightness(measured)
    neighbours = [47, 48, 49, 51, 52, 53]
    assert np.abs(fit[neighbours] - truth[neighbours]).max() < 0.05


def test_strength_scales_the_correction():
    measured = sunset(60) + jitter(60, 0.1)
    seq = Sequence(Path("."), tuple(Frame(Path(f"{i}.png")) for i in range(60)))
    full = smooth_flicker(seq, brightness=measured).exposure
    assert np.allclose(smooth_flicker(seq, brightness=measured, strength=0.5).exposure, full / 2)
    assert np.allclose(smooth_flicker(seq, brightness=measured, strength=0).exposure, 0)
    with pytest.raises(ValueError):
        smooth_flicker(seq, brightness=measured, strength=1.5)


def test_short_sequences_dont_break():
    assert np.allclose(smooth_brightness([0.0, 0.1]), [0.0, 0.1])
    assert len(smooth_brightness([0.0, 0.1, -0.1, 0.05], window=25)) == 4


def test_end_to_end_on_files(tmp_path):
    truth = sunset(80, drop=2.0)
    make_folder(tmp_path, truth + jitter(80, 0.1), fmt="jpg")
    fix = smooth_flicker(load_sequence(tmp_path))

    corrected = fix.brightness + fix.exposure
    assert fix.flicker_before > 0.05
    assert np.std(np.diff(corrected)) < np.std(np.diff(fix.brightness)) / 5
    assert set(fix.by_filename()) == {f"IMG_{i:04d}.jpg" for i in range(1, 81)}
