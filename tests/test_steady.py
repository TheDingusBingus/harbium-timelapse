"""Moving things (walkers, cars, birds) must not sway the brightness reading.

Found on a real R6 sequence: a crowd of dark clothing over ~15% of frame 1670
made the whole-frame measure read 0.2 stops darker; deflicker brightened the
frame by mistake (+0.4 in the render), and finish, swayed the same way, only
partly undid it. Measuring the still part of the scene fixes both.
"""

import numpy as np
from PIL import Image

from harbium_timelapse import measure_light
from harbium_timelapse.finish import LUMA, _linear_to_srgb, apply_finish, finished_frames, plan_finish
from harbium_timelapse.measure import steady_change

N = 40
rng = np.random.default_rng(3)
H, W = 120, 180
y, x = np.mgrid[0:H, 0:W]
_base = 0.03 + 0.5 * (x / W) * (0.6 + 0.4 * np.sin(y / 7.0) ** 2)
SCENE = np.stack([_base * 1.1, _base, _base * 0.8], -1) * np.exp(rng.normal(0, 0.35, (H, W)))[..., None] * 0.7
LIGHT = np.linspace(0, -0.6, N) + rng.normal(0, 0.05, N)           # dusk plus flicker
CROWD = set(range(4, N, 6))


def frames(folder, crowd=True):
    folder.mkdir()
    for i in range(N):
        lin = SCENE * 2.0 ** LIGHT[i]
        if crowd and i in CROWD:
            lin = lin.copy()
            lin[int(H * .6):int(H * .95), int(W * .42):int(W * .85)] *= 0.2   # dark walkers, ~15% of frame
        img = np.round(_linear_to_srgb(np.clip(lin, 0, 1)) * 255).astype(np.uint8)
        Image.fromarray(img).save(folder / f"f{i + 1}.png")
    return finished_frames(folder)


def error(measured):
    return (measured - measured.mean()) - (LIGHT - LIGHT.mean())


def test_crowd_does_not_sway_deflickers_measurement(tmp_path):
    fs = frames(tmp_path / "e")
    steady, _ = measure_light(fs)
    whole, _ = measure_light(fs, steady=False)
    assert np.abs(error(whole)).max() > 0.1          # the old measure is fooled
    assert np.abs(error(steady)).max() < 0.02        # the new one isn't


def test_still_exact_when_nothing_moves(tmp_path):
    steady, _ = measure_light(frames(tmp_path / "e", crowd=False))
    assert np.abs(error(steady)).max() < 0.01


def test_finish_does_not_lift_crowded_frames(tmp_path):
    fs = frames(tmp_path / "e")
    apply_finish(plan_finish(fs), out=tmp_path / "out")
    still = np.zeros((H, W), bool)
    still[:int(H * .5)] = True                          # top half: buildings and trees, never walked on
    def level(p):
        a = np.asarray(Image.open(p), float) / 255
        a = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
        return np.log2((a[still] @ LUMA).mean())
    out = finished_frames(tmp_path / "out")
    levels = np.array([level(p) for p in out])
    jumps = np.abs(np.diff(levels))
    assert jumps.max() < 0.03                           # crowded frames don't pop brighter or darker


def test_movers_are_set_aside():
    prev = np.full((50, 50), 0.2)
    cur = prev * 2.0 ** -0.3
    cur[:15] = 0.01                                      # 30% of pixels: something dark walks in
    assert abs(steady_change(prev, cur) - (-0.3)) < 1e-6
