"""The tone check finds over-correction where it exists, and only there."""

import numpy as np
from PIL import Image

from harbium_timelapse import cli
from harbium_timelapse.tonecheck import compare, lagging, verdict

rng = np.random.default_rng(7)
H, W, N = 60, 90, 60
# a scene with shadows, midtones and highlights (linear light), lightly textured
scene = np.concatenate([np.full((20, W), 0.02), np.full((20, W), 0.12), np.full((20, W), 0.5)])
scene = (scene * np.exp(rng.normal(0, 0.15, (H, W))))[..., None] * np.ones(3)
flicker = rng.normal(0, 0.06, N)                                         # stops per frame


def corrected(frames):
    """One correction per frame for every brightness level, from the whole frame (as finish does)."""
    lum = np.array([np.log2(np.median(f @ [0.2126, 0.7152, 0.0722])) for f in frames])
    smooth = np.convolve(lum, np.ones(9) / 9, mode="same")
    smooth[:4], smooth[-4:] = lum[:4].mean(), lum[-4:].mean()
    return [np.clip(f * 2 ** (smooth[i] - lum[i]), 0, 1) for i, f in enumerate(frames)]


def test_flicker_at_every_level_corrected_at_every_level():
    before = [np.clip(scene * 2 ** f, 0, 1) for f in flicker]
    bands = compare(before, corrected(before))
    assert all(b.after < b.before / 3 and not b.worse for b in bands) and lagging(bands) == []
    assert "similar amount" in verdict(bands)


def test_a_tone_curve_squeezing_the_highlights_is_caught():
    def tone_curve(x):                     # an editor's tone curve: a strong shoulder that compresses highlights
        return np.clip(1 - np.exp(-3 * x), 0, 1) / (1 - np.exp(-3))
    before = [tone_curve(scene * 2 ** f) for f in flicker]
    found = compare(before, corrected(before))
    bands = {b.name: b for b in found}
    assert [b.name for b in lagging(found)] == ["highlights"]            # over-corrected: barely steadier
    assert bands["midtones"].kept < 0.2 and bands["highlights"].kept > 0.8
    assert "highlights barely got steadier" in verdict(found)


def test_the_command_reads_two_folders(tmp_path, capsys):
    for name, frames in (("before", [np.clip(scene * 2 ** f, 0, 1) for f in flicker[:12]]),):
        (tmp_path / name).mkdir()
        for i, f in enumerate(frames):
            Image.fromarray((f ** (1 / 2.2) * 255).astype(np.uint8)).save(tmp_path / name / f"{i:03d}.png")
    (tmp_path / "after").mkdir()
    for i, f in enumerate(corrected([np.clip(scene * 2 ** f, 0, 1) for f in flicker[:12]])):
        Image.fromarray((f ** (1 / 2.2) * 255).astype(np.uint8)).save(tmp_path / "after" / f"{i:03d}.png")
    assert cli.main(["tone-check", str(tmp_path / "before"), str(tmp_path / "after")]) == 0
    out = capsys.readouterr().out
    assert "12 frames compared" in out and "highlights" in out and "kept" in out
