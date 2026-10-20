"""Kelvin and tint from camera neutrals, on the standard DNG scale."""

import pytest

from harbium_timelapse.colour_temperature import (
    kelvin_tint_from_neutral, kelvin_tint_from_xy, neutral_from_kelvin_tint, xy_from_kelvin_tint,
)

# Canon EOS R6, XYZ-to-camera (rawler / dnglab camera data), test data only
R6_A = (1.0492, -0.481, 0.0274, -0.3519, 1.0911, 0.3009, -0.0143, 0.1025, 0.6822)
R6_D65 = (0.8293, -0.1611, -0.1132, -0.4759, 1.2711, 0.2275, -0.1013, 0.2415, 0.5509)


@pytest.mark.parametrize("kelvin", [2000, 2856, 3500, 5000, 6504, 9000, 15000])
@pytest.mark.parametrize("tint", [-40, 0, 25])
def test_xy_round_trip(kelvin, tint):
    k, t = kelvin_tint_from_xy(*xy_from_kelvin_tint(kelvin, tint))
    assert abs(1e6 / k - 1e6 / kelvin) < 0.05   # mireds
    assert abs(t - tint) < 0.05


def test_illuminant_a_is_on_the_locus():
    k, t = kelvin_tint_from_xy(0.44757, 0.40745)
    assert abs(k - 2856) < 10 and abs(t) < 1


@pytest.mark.parametrize("kelvin,tint", [(3000, 0), (5600, 10), (7250, -8), (2500, 30)])
def test_neutral_round_trip_dual_illuminant(kelvin, tint):
    n = neutral_from_kelvin_tint(kelvin, tint, R6_D65, R6_A)
    assert n[1] == pytest.approx(1.0)
    k, t = kelvin_tint_from_neutral(n, R6_D65, R6_A)
    assert abs(k - kelvin) < 1 and abs(t - tint) < 0.1


def test_redder_neutral_reads_warmer_light():
    warm = kelvin_tint_from_neutral((0.80, 1, 0.43), R6_D65, R6_A)[0]
    cool = kelvin_tint_from_neutral((0.50, 1, 0.65), R6_D65, R6_A)[0]
    assert warm < 3500 < 5000 < cool


def test_tint_agrees_with_lightroom_on_a_real_frame():
    # A solved neutral for a real R6 frame; Lightroom read it as 3550 K / +21.
    # Tint agrees to a unit. Kelvin reads ~240 K higher: the camera profiles differ
    # (a matrix difference, not a maths one), so Kelvin is only comparable within
    # one editor's matrices.
    k, t = kelvin_tint_from_neutral((0.644, 1, 0.474), R6_D65, R6_A)
    assert abs(t - 21) < 2
    assert 3550 < k < 3850


def test_d65_only_is_a_single_pass():
    n = neutral_from_kelvin_tint(5000, 5, R6_D65)
    k, t = kelvin_tint_from_neutral(n, R6_D65)
    assert abs(k - 5000) < 1 and abs(t - 5) < 0.1
