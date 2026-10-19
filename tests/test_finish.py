"""Finish: correct the exported frames themselves, after any editor."""

import shutil

import numpy as np
import pytest
from PIL import Image

from timelapse_core import cli
from timelapse_core.finish import (
    LUMA, _linear_to_srgb, _wobble, apply_finish, correct, finished_frames, measure_channels, plan_finish,
)

N = 60
rng = np.random.default_rng(7)
TREND = np.column_stack([np.linspace(0, -0.8, N) + d for d in (np.linspace(0, 0.15, N), 0, np.linspace(0, -0.15, N))])
WOBBLE = rng.normal(0, 0.06, (N, 1)) + rng.normal(0, 0.03, (N, 3))     # brightness flicker + colour jitter


def scene(h=90, w=135):
    y, x = np.mgrid[0:h, 0:w]
    base = 0.03 + 0.5 * (x / w) * (0.6 + 0.4 * np.sin(y / 7.0) ** 2)
    return np.stack([base * 1.1, base, base * 0.8], axis=-1)


def export(folder, fmt="png", wobble=WOBBLE):
    folder.mkdir()
    lin = scene()
    for i in range(N):
        img = np.clip(lin * 2.0 ** (TREND[i] + wobble[i]), 0, 1)
        Image.fromarray(np.round(_linear_to_srgb(img) * 255).astype(np.uint8)).save(folder / f"f{i + 1}.{fmt}")
    return finished_frames(folder)


def test_frames_in_natural_order(tmp_path):
    frames = export(tmp_path / "e")
    assert [f.stem for f in frames[:11]] == [f"f{i}" for i in range(1, 12)]   # f10 after f9, not after f1


def test_removes_the_wobble_and_keeps_the_journey(tmp_path):
    frames = export(tmp_path / "e")
    p = plan_finish(frames)
    assert p.brightness_before > 0.05 and p.colour_before > 0.03
    apply_finish(p, out=tmp_path / "out")
    after = measure_channels(finished_frames(tmp_path / "out"))
    assert _wobble(after @ LUMA) < p.brightness_before / 4
    assert _wobble(after[:, 0] - after[:, 2]) < p.colour_before / 3
    journey = (after[-5:].mean(0) - after[:5].mean(0)) - (TREND[-5:].mean(0) - TREND[:5].mean(0))
    assert np.all(np.abs(journey) < 0.06)              # dusk still darkens and warms as it really did


def test_works_on_jpegs_too(tmp_path):
    frames = export(tmp_path / "e", fmt="jpg")
    p = plan_finish(frames)
    apply_finish(p, out=tmp_path / "out")
    out = finished_frames(tmp_path / "out")
    assert [f.name for f in out] == [f.name for f in frames]
    assert _wobble(measure_channels(out) @ LUMA) < p.brightness_before / 3


def test_brightness_only_leaves_colour_alone(tmp_path):
    p = plan_finish(export(tmp_path / "e"), colour=False)
    assert np.allclose(p.gains[:, 0], p.gains[:, 1]) and np.allclose(p.gains[:, 1], p.gains[:, 2])
    assert abs(p.colour_after - p.colour_before) < 1e-9


def test_no_correction_changes_nothing():
    for img in (np.arange(256, dtype=np.uint8).repeat(3).reshape(16, 16, 3),
                (np.arange(4096, dtype=np.uint16) * 16).repeat(3).reshape(64, 64, 3)):
        assert np.array_equal(correct(img, [0.0, 0.0, 0.0]), img)


def test_never_overwrites_the_export(tmp_path):
    frames = export(tmp_path / "e")
    with pytest.raises(ValueError, match="different folder"):
        apply_finish(plan_finish(frames), out=tmp_path / "e")
    with pytest.raises(ValueError, match="where to put"):
        apply_finish(plan_finish(frames))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_video_straight_from_corrected_frames(tmp_path):
    p = plan_finish(export(tmp_path / "e"))
    apply_finish(p, video=tmp_path / "film.mp4", fps=24)
    assert (tmp_path / "film.mp4").stat().st_size > 1000
    assert not (tmp_path / "out").exists()                # nothing stored in between


def test_sixteen_bit_tiff_stays_sixteen_bit(tmp_path):
    tifffile = pytest.importorskip("tifffile")
    folder = tmp_path / "e"
    folder.mkdir()
    lin = scene()
    for i in range(N):
        img = np.clip(lin * 2.0 ** (TREND[i] + WOBBLE[i]), 0, 1)
        tifffile.imwrite(str(folder / f"f{i + 1}.tif"), np.round(_linear_to_srgb(img) * 65535).astype(np.uint16))
    p = plan_finish(finished_frames(folder))
    apply_finish(p, out=tmp_path / "out")
    out = tifffile.imread(str(tmp_path / "out" / "f1.tif"))
    assert out.dtype == np.uint16 and out.shape[-1] == 3
    assert _wobble(measure_channels(finished_frames(tmp_path / "out")) @ LUMA) < p.brightness_before / 4


def test_cli_dry_run_and_real_run(tmp_path, capsys):
    export(tmp_path / "e")
    assert cli.main(["finish", str(tmp_path / "e"), "--dry-run"]) == 0
    assert "Dry run" in capsys.readouterr().out
    assert cli.main(["finish", str(tmp_path / "e")]) == 1                # no destination given
    assert cli.main(["finish", str(tmp_path / "e"), "--out", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "Measured on the corrected frames" in out and len(list((tmp_path / "out").iterdir())) == N


# --- dark sequences: 16-bit in, small files out ----------------------------------

def dark_export(folder, kind):
    tifffile = pytest.importorskip("tifffile")
    folder.mkdir()
    lin = scene() * 2.0 ** -5.0                          # deep twilight: shadows on very few 8-bit levels
    for i in range(N):
        img = _linear_to_srgb(np.clip(lin * 2.0 ** (TREND[i] + WOBBLE[i]), 0, 1))
        if kind == "tif":
            tifffile.imwrite(str(folder / f"f{i + 1}.tif"), np.round(img * 65535).astype(np.uint16))
        else:
            Image.fromarray(np.round(img * 255).astype(np.uint8)).save(folder / f"f{i + 1}.jpg", quality=95)
    return finished_frames(folder)


def test_sixteen_bit_measures_dark_frames_exactly_where_jpeg_cannot(tmp_path):
    true = TREND + WOBBLE
    errs = {}
    for kind in ("jpg", "tif"):
        m = measure_channels(dark_export(tmp_path / kind, kind))
        errs[kind] = np.abs((m - m.mean(0)) - (true - true.mean(0))).max()
    assert errs["tif"] < 0.005 and errs["tif"] < errs["jpg"] / 3


def test_correct_in_sixteen_bit_write_jpegs(tmp_path):
    frames = dark_export(tmp_path / "e", "tif")
    apply_finish(plan_finish(frames), out=tmp_path / "out", fmt="jpg")
    out = finished_frames(tmp_path / "out")
    assert [f.suffix for f in out] == [".jpg"] * N and [f.stem for f in out] == [f.stem for f in frames]
    assert np.asarray(Image.open(out[0])).dtype == np.uint8


def test_rejects_unknown_output_format(tmp_path):
    with pytest.raises(ValueError, match="format"):
        apply_finish(plan_finish(export(tmp_path / "e")), out=tmp_path / "out", fmt="webp")


def test_deflicker_measurement_reads_sixteen_bit_tiff(tmp_path):
    from timelapse_core import measure_light
    b, _ = measure_light(dark_export(tmp_path / "e", "tif"))
    true = (TREND + WOBBLE) @ LUMA
    assert np.abs((b - b.mean()) - (true - true.mean())).max() < 0.02
