"""White balance as Kelvin and tint, from the camera's own colour numbers.

A RAW file records white balance as a *neutral*: how strongly the sensor's
red, green and blue respond to a grey under the scene's light. Editors show it
as Kelvin and tint. This module converts between the two, on the standard
scale used by the DNG specification (and, as of Oct 2026, by RapidRAW's
absolute ``whiteBalance`` field and by Lightroom):

- **Kelvin**: the colour temperature on the blackbody (Planckian) locus,
  found with Robertson's method (CIE 1960 uv, the published 31-isotherm
  table from Wyszecki & Stiles).
- **Tint**: the distance off that locus, along the isotherm, times -3000.
  Positive = magenta, negative = green. +/-150 covers real lights.

Going from a neutral to Kelvin needs the camera's colour matrices (XYZ to
camera, one measured under illuminant A and one under D65). As the DNG
specification describes, the two are blended by inverse temperature and the
answer is refined until it stops changing.

Written from the published standards. No editor's code is used.
"""

from __future__ import annotations

import numpy as np

TINT_SCALE = -3000.0
TEMPERATURE_A = 2856.0
TEMPERATURE_D65 = 6504.0

# mired, u, v, isotherm slope  (Robertson 1968; Wyszecki & Stiles)
_ROBERTSON = np.array([
    [0, 0.18006, 0.26352, -0.24341], [10, 0.18066, 0.26589, -0.25479],
    [20, 0.18133, 0.26846, -0.26876], [30, 0.18208, 0.27119, -0.28539],
    [40, 0.18293, 0.27407, -0.30470], [50, 0.18388, 0.27709, -0.32675],
    [60, 0.18494, 0.28021, -0.35156], [70, 0.18611, 0.28342, -0.37915],
    [80, 0.18740, 0.28668, -0.40955], [90, 0.18880, 0.28997, -0.44278],
    [100, 0.19032, 0.29326, -0.47888], [125, 0.19462, 0.30141, -0.58204],
    [150, 0.19962, 0.30921, -0.70471], [175, 0.20525, 0.31647, -0.84901],
    [200, 0.21142, 0.32312, -1.0182], [225, 0.21807, 0.32909, -1.2168],
    [250, 0.22511, 0.33439, -1.4512], [275, 0.23247, 0.33904, -1.7298],
    [300, 0.24010, 0.34308, -2.0637], [325, 0.24792, 0.34655, -2.4681],
    [350, 0.25591, 0.34951, -2.9641], [375, 0.26400, 0.35200, -3.5814],
    [400, 0.27218, 0.35407, -4.3633], [425, 0.28039, 0.35577, -5.3762],
    [450, 0.28863, 0.35714, -6.7262], [475, 0.29685, 0.35823, -8.5955],
    [500, 0.30505, 0.35907, -11.324], [525, 0.31320, 0.35968, -15.628],
    [550, 0.32129, 0.36011, -23.325], [575, 0.32931, 0.36038, -40.770],
    [600, 0.33724, 0.36051, -116.45],
])


def _unit(slope: float) -> np.ndarray:
    return np.array([1.0, slope]) / np.hypot(1.0, slope)


def kelvin_tint_from_xy(x: float, y: float) -> tuple[float, float]:
    """Kelvin and tint of a colour given as CIE xy."""
    d = -2 * x + 12 * y + 3
    u, v = 4 * x / d, 6 * y / d
    prev_dist, prev_dir = 0.0, np.zeros(2)
    for i in range(1, len(_ROBERTSON)):
        mired, iu, iv, slope = _ROBERTSON[i]
        direction = _unit(slope)
        # signed distance from this isotherm line
        dist = (v - iv) * direction[0] - (u - iu) * direction[1]
        last = i == len(_ROBERTSON) - 1
        if dist <= 0 or last:
            dist = max(-dist, 0.0)
            f = 0.0 if i == 1 else dist / (prev_dist + dist)
            pm, pu, pv, _ = _ROBERTSON[i - 1]
            lu, lv = pu * f + iu * (1 - f), pv * f + iv * (1 - f)
            n = direction * (1 - f) + prev_dir * f
            n /= np.linalg.norm(n)
            offset = (u - lu) * n[0] + (v - lv) * n[1]
            return 1e6 / (pm * f + mired * (1 - f)), offset * TINT_SCALE
        prev_dist, prev_dir = dist, direction
    raise AssertionError("unreachable")


def xy_from_kelvin_tint(kelvin: float, tint: float = 0.0) -> tuple[float, float]:
    """CIE xy of a Kelvin and tint. Inverse of ``kelvin_tint_from_xy``."""
    mired = 1e6 / kelvin
    i = int(np.searchsorted(_ROBERTSON[1:, 0], mired, side="right")) + 1
    i = min(i, len(_ROBERTSON) - 1)
    pm, pu, pv, ps = _ROBERTSON[i - 1]
    nm, nu, nv, ns = _ROBERTSON[i]
    f = (nm - mired) / (nm - pm)
    n = _unit(ps) * f + _unit(ns) * (1 - f)
    n /= np.linalg.norm(n)
    u = pu * f + nu * (1 - f) + n[0] * tint / TINT_SCALE
    v = pv * f + nv * (1 - f) + n[1] * tint / TINT_SCALE
    d = u - 4 * v + 2
    return 1.5 * u / d, v / d


def _xyz(x: float, y: float) -> np.ndarray:
    return np.array([x / y, 1.0, (1 - x - y) / y])


def _blend(matrix_a, matrix_d65, kelvin: float) -> np.ndarray:
    d = np.asarray(matrix_d65, float).reshape(3, 3)
    if matrix_a is None:
        return d
    a = np.asarray(matrix_a, float).reshape(3, 3)
    w = (1 / kelvin - 1 / TEMPERATURE_D65) / (1 / TEMPERATURE_A - 1 / TEMPERATURE_D65)
    w = float(np.clip(w, 0.0, 1.0))
    return w * a + (1 - w) * d


def kelvin_tint_from_neutral(neutral, matrix_d65, matrix_a=None) -> tuple[float, float]:
    """Kelvin and tint for a camera neutral (e.g. a DNG's AsShotNeutral).

    ``neutral`` is (R, G, B) with green usually 1. Matrices are XYZ-to-camera,
    nine numbers row by row. With only a D65 matrix the answer is a single
    pass; with both, it is refined until it settles (DNG specification).
    """
    neutral = np.asarray(neutral, float)[:3]

    def solve(m: np.ndarray) -> tuple[float, float]:
        xyz = np.linalg.solve(m, neutral)
        s = xyz.sum()
        return kelvin_tint_from_xy(xyz[0] / s, xyz[1] / s)

    kelvin, tint = solve(np.asarray(matrix_d65, float).reshape(3, 3))
    if matrix_a is None:
        return kelvin, tint
    for _ in range(20):
        nk, tint = solve(_blend(matrix_a, matrix_d65, kelvin))
        done = abs(nk - kelvin) < 0.1
        kelvin = nk
        if done:
            break
    return kelvin, tint


def neutral_from_kelvin_tint(kelvin: float, tint: float, matrix_d65, matrix_a=None) -> np.ndarray:
    """The camera neutral (G = 1) that a Kelvin and tint correspond to."""
    m = (np.asarray(matrix_d65, float).reshape(3, 3) if matrix_a is None
         else _blend(matrix_a, matrix_d65, kelvin))
    n = m @ _xyz(*xy_from_kelvin_tint(kelvin, tint))
    return n / n[1]
