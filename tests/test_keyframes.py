import math
from pathlib import Path

import numpy as np
import pytest

from synthetic import scene
from timelapse_core import Frame, Sequence, blend_between, suggest_keyframes
from timelapse_core.measure import _sensor_warmth, warmth_of


def frames(n):
    return Sequence(Path("."), tuple(Frame(Path(f"{i:04d}.CR3"), shutter=1 / 100, aperture=8.0, iso=100) for i in range(n)))


def noise(n, size=0.05, seed=1):
    return np.random.default_rng(seed).normal(0, size, n)


# --- blending ----------------------------------------------------------------

def test_blend_passes_through_every_keyframe_and_holds_outside():
    v = blend_between([10, 50, 90], [0.0, 20.0, 25.0], 120)
    assert v[10] == 0 and v[50] == 20 and v[90] == 25
    assert np.all(v[:10] == 0) and np.all(v[90:] == 25)


def test_blend_never_overshoots():
    v = blend_between([0, 30, 60, 100], [10.0, 30.0, 30.0, 0.0], 101)
    assert v.max() <= 30 + 1e-9 and v.min() >= 0 - 1e-9
    assert np.all(np.diff(v[:31]) >= -1e-9)              # rising stretch only rises
    assert np.allclose(v[30:61], 30)                     # flat stretch stays flat


def test_blend_eases_in_and_out_and_has_no_corners():
    v = blend_between([0, 40, 100], [0.0, 10.0, 30.0], 101)
    assert abs(v[1] - v[0]) < abs(v[20] - v[19])         # starts gently
    assert abs(v[100] - v[99]) < abs(v[70] - v[69])      # finishes gently
    pace = np.diff(v)
    assert abs(pace[40] - pace[39]) < 0.05               # no change of pace at the middle keyframe


def test_blend_rejects_muddled_keyframes():
    with pytest.raises(ValueError):
        blend_between([10, 5], [0, 1], 20)


# --- suggestions --------------------------------------------------------------

def test_suggests_a_keyframe_each_stop_of_real_light():
    n = 240
    light = np.linspace(0, -4, n) + noise(n)
    picks = suggest_keyframes(frames(n), light)
    assert picks[0].frame == 0 and picks[-1].frame == n - 1
    assert len(picks) == 5                                # 0, ~1, ~2, ~3 stops, end
    assert all("dimmer" in p.reason for p in picks[1:-1])


def test_nd_moves_dont_become_keyframes():
    n = 240
    b = np.linspace(0, -0.5, n) + noise(n)
    b[100:] += 1.0                                       # hand-turned ND, a full stop
    picks = suggest_keyframes(frames(n), b)
    assert [p.frame for p in picks] == [0, n - 1]


def test_a_colour_shift_suggests_a_keyframe():
    n = 240
    b = noise(n)
    warmth = np.where(np.arange(n) < 120, 0.0, 0.8) + noise(n, 0.02, seed=2)
    picks = suggest_keyframes(frames(n), b, warmth)
    assert len(picks) == 3
    assert "warmer" in picks[1].reason
    assert 100 < picks[1].frame < 140


def test_keyframes_keep_their_distance():
    n = 100
    picks = suggest_keyframes(frames(n), np.linspace(0, -20, n), closest=10)
    assert all(b.frame - a.frame >= 10 for a, b in zip(picks, picks[1:]))


# --- warmth -----------------------------------------------------------------

def test_warmth_reads_colour_not_brightness():
    base = np.stack([scene() * 1.2, scene(), scene() * 0.8], axis=-1)
    assert warmth_of(base) == pytest.approx(math.log2(1.5), abs=1e-6)
    assert warmth_of(base * 0.5) == pytest.approx(warmth_of(base), abs=1e-6)
    assert warmth_of(base[..., [2, 1, 0]]) < 0


class FakeRaw:
    """Just enough of rawpy's RawPy for the sensor reading: an RGGB pattern."""

    def __init__(self, red, green, blue, size=64):
        img = np.zeros((size, size), dtype=np.uint16)
        cols = np.zeros((size, size), dtype=np.uint8)
        for dy, dx, colour, value in [(0, 0, 0, red), (0, 1, 1, green), (1, 0, 3, green), (1, 1, 2, blue)]:
            img[dy::2, dx::2] = value
            cols[dy::2, dx::2] = colour
        self.raw_image_visible, self.raw_colors_visible = img, cols
        self.black_level_per_channel = [512, 512, 512, 512]
        self.white_level = 16000


def test_sensor_warmth_is_read_before_white_balance():
    assert _sensor_warmth(FakeRaw(red=512 + 2000, green=4000, blue=512 + 1000)) == pytest.approx(1.0)
    assert _sensor_warmth(FakeRaw(red=512 + 1000, green=4000, blue=512 + 1000)) == pytest.approx(0.0)
