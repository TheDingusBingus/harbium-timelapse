"""A frame that can't be measured (lens cap, black, fully blown) must not
poison the rest. Before the fix, one black frame in 30 gave 20 NaN
corrections and finish wrote 20 pure-black frames."""

import numpy as np
from PIL import Image

from timelapse_core import cli, measure_light, smooth_flicker
from timelapse_core.deflicker import smooth_brightness
from timelapse_core.finish import _linear_to_srgb, apply_finish, finished_frames, plan_finish
from timelapse_core.sequencer import load_sequence

N = 30
rng = np.random.default_rng(1)
_y, _x = np.mgrid[0:60, 0:90]
BASE = (0.05 + 0.4 * _x / 90)[..., None] * np.array([1.1, 1, 0.8]) * np.exp(rng.normal(0, 0.3, (60, 90)))[..., None]
WOBBLE = rng.normal(0, 0.05, N)


def frames(folder, bad=None, kind="black"):
    folder.mkdir()
    for i in range(N):
        lin = BASE * 2 ** WOBBLE[i]
        if i == bad:
            lin = lin * 0 if kind == "black" else lin * 0 + 1
        Image.fromarray(np.round(_linear_to_srgb(np.clip(lin, 0, 1)) * 255).astype(np.uint8)).save(folder / f"f{i + 1}.png")
    return finished_frames(folder)


def test_smoother_bridges_unknown_frames():
    y = np.linspace(0, 1, 40)
    y[[5, 6, 20]] = np.nan
    fit = smooth_brightness(y, 15)
    assert np.isfinite(fit).all() and np.allclose(fit, np.linspace(0, 1, 40), atol=1e-6)


def test_one_black_frame_only_affects_itself_in_deflicker(tmp_path):
    for kind in ("black", "white"):
        fs = frames(tmp_path / kind, bad=10, kind=kind)
        fix = smooth_flicker(load_sequence(fs[0].parent))
        assert fix.unmeasured == (10,)
        assert np.isfinite(fix.exposure).all() and fix.exposure[10] == 0.0
        assert fix.flicker_after < fix.flicker_before / 5


def test_finish_copies_the_bad_frame_and_corrects_the_rest(tmp_path):
    fs = frames(tmp_path / "e", bad=10)
    p = plan_finish(fs)
    assert p.unmeasured == (10,) and np.isfinite(p.gains).all()
    apply_finish(p, out=tmp_path / "out")
    out = finished_frames(tmp_path / "out")
    means = [np.asarray(Image.open(f)).mean() for f in out]
    assert sum(m < 1 for m in means) == 1                       # only the genuinely black frame
    assert np.array_equal(np.asarray(Image.open(out[10])), np.asarray(Image.open(fs[10])))


def test_cli_names_the_frame_and_rejects_silly_windows(tmp_path, capsys):
    frames(tmp_path / "e", bad=10)
    assert cli.main(["finish", str(tmp_path / "e"), "--dry-run"]) == 0
    assert "couldn't measure 1 frame (f11.png)" in capsys.readouterr().out
    assert cli.main(["finish", str(tmp_path / "e"), "--dry-run", "--window", "1"]) == 1
    assert "at least 3" in capsys.readouterr().err


def test_all_black_sequence_says_so(tmp_path, capsys):
    folder = tmp_path / "dark"
    folder.mkdir()
    for i in range(5):
        Image.fromarray(np.zeros((20, 20, 3), np.uint8)).save(folder / f"f{i}.png")
    assert cli.main(["finish", str(folder), "--dry-run"]) == 1
    assert "all black" in capsys.readouterr().err
