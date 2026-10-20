"""A frame that can't be measured (lens cap, black, fully blown) must not
poison the rest. Before the fix, one black frame in 30 gave 20 NaN
corrections and finish wrote 20 pure-black frames."""

import numpy as np
from PIL import Image

from harbium_timelapse import cli, measure_light, smooth_flicker
from harbium_timelapse.deflicker import smooth_brightness
from harbium_timelapse.finish import _linear_to_srgb, apply_finish, finished_frames, plan_finish
from harbium_timelapse.sequencer import load_sequence

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


# --- stage 2: odd inputs and security -----------------------------------------

import shutil  # noqa: E402

import pytest  # noqa: E402


def test_output_folder_with_other_frames_is_refused(tmp_path):
    fs = frames(tmp_path / "e")
    apply_finish(plan_finish(fs), out=tmp_path / "out")
    apply_finish(plan_finish(fs), out=tmp_path / "out")                 # same names again: fine
    with pytest.raises(ValueError, match="other frames"):
        apply_finish(plan_finish(fs), out=tmp_path / "out", fmt="jpg")  # would mix PNG and JPG


def test_mixed_sizes_refused_before_anything_is_written(tmp_path):
    fs = frames(tmp_path / "e")
    Image.open(fs[5]).crop((0, 0, 80, 60)).save(fs[5])
    with pytest.raises(ValueError, match="same size"):
        apply_finish(plan_finish(fs), out=tmp_path / "out", video=tmp_path / "v.mp4")
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").iterdir())


@pytest.mark.skipif(shutil.which("exiftool") is None, reason="needs ExifTool")
def test_file_named_like_an_option_is_read_as_a_file(tmp_path, monkeypatch):
    from harbium_timelapse.exif import _read_with_exiftool
    fs = frames(tmp_path / "e")
    odd = fs[0].parent / "-delete_original!.png"
    fs[0].rename(odd)
    monkeypatch.chdir(odd.parent)
    from pathlib import Path
    found = _read_with_exiftool([Path(odd.name)], None)
    assert len(found) == 1


def test_programs_are_never_taken_from_the_current_folder(tmp_path, monkeypatch):
    import os
    import sys
    from harbium_timelapse.programs import find_program
    planted = tmp_path / ("exiftool.exe" if sys.platform.startswith("win") else "exiftool")
    planted.write_text("#!/bin/sh\necho planted\n")
    planted.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", os.pathsep.join([".", "", str(tmp_path / "nowhere")]))
    assert find_program("exiftool") is None


# --- parallel measuring gives exactly the same answers --------------------------

def test_parallel_measuring_matches_one_at_a_time(tmp_path, monkeypatch):
    from harbium_timelapse.finish import measure_channels
    fs = frames(tmp_path / "e", bad=10)
    monkeypatch.setenv("HARBIUM_WORKERS", "1")
    one_b, one_w = measure_light(fs)
    one_c = measure_channels(fs)
    monkeypatch.setenv("HARBIUM_WORKERS", "3")
    par_b, par_w = measure_light(fs)
    par_c = measure_channels(fs)
    assert np.array_equal(one_b, par_b, equal_nan=True) and np.array_equal(one_w, par_w)
    assert np.array_equal(one_c, par_c, equal_nan=True)


def test_a_bad_file_is_still_reported_in_parallel(tmp_path, monkeypatch):
    fs = frames(tmp_path / "e")
    fs[7].write_bytes(b"not a picture")
    monkeypatch.setenv("HARBIUM_WORKERS", "3")
    with pytest.raises(ValueError, match="f8.png"):
        measure_light(fs)


def test_ordered_map_keeps_order_and_falls_back():
    from harbium_timelapse.parallel import ordered_map
    assert list(ordered_map(abs, [-3, 1, -2, 5, -9, 4], workers=3)) == [3, 1, 2, 5, 9, 4]
    assert list(ordered_map(abs, [-1, -2], workers=4)) == [1, 2]            # too few to bother
