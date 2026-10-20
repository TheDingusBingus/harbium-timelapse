"""Dither and video formats: what leaves the tool is as good as it can be."""

import shutil
import subprocess

import numpy as np
import pytest
from PIL import Image

from harbium_timelapse import cli
from harbium_timelapse.finish import _linear_to_srgb, check_video_target, correct
from harbium_timelapse.measure import _srgb_to_linear


def sky(h=200, w=600):
    """A smooth 8-bit dusk-sky gradient: where banding shows first."""
    ramp = np.linspace(0.02, 0.25, w)[None, :] * np.linspace(1.0, 0.7, h)[:, None]
    rgb = np.stack([ramp * 1.1, ramp, ramp * 0.8], axis=-1)
    return np.round(_linear_to_srgb(np.clip(rgb, 0, 1)) * 255).astype(np.uint8)


def exact_target(img, gain):
    return _linear_to_srgb(np.clip(_srgb_to_linear(img / 255.0) * 2.0 ** gain, 0, 1)) * 255


def block_means(a, n=8):
    h, w = a.shape[0] // n * n, a.shape[1] // n * n
    return a[:h, :w].reshape(h // n, n, w // n, n, -1).mean(axis=(1, 3))


def test_dither_gets_local_averages_right_where_rounding_cannot():
    img, gain = sky(), 0.3
    target = block_means(exact_target(img, gain))
    plain = block_means(correct(img, [gain] * 3).astype(float))
    dithered = block_means(correct(img, [gain] * 3, dither=True, seed=1).astype(float))
    err_plain = np.sqrt(np.mean((plain - target) ** 2))
    err_dither = np.sqrt(np.mean((dithered - target) ** 2))
    assert err_dither < 0.6 * err_plain                     # much closer on average
    assert abs(np.mean(dithered - target)) < 0.02             # and no brightness shift


def test_dither_leaves_uncorrected_frames_untouched_and_is_repeatable():
    img = sky()
    assert np.array_equal(correct(img, [0, 0, 0], dither=True), img)
    a = correct(img, [0.1, 0.0, -0.1], dither=True, seed=7)
    assert np.array_equal(a, correct(img, [0.1, 0.0, -0.1], dither=True, seed=7))
    assert np.array_equal(a[..., 1], img[..., 1])             # the uncorrected channel stays exact


def test_sixteen_bit_output_keeps_the_full_precision():
    img, gain = sky(), 0.3
    out = correct(img, [gain] * 3, out_dtype=np.uint16)
    assert out.dtype == np.uint16
    assert np.max(np.abs(out / 65535 * 255 - exact_target(img, gain))) < 0.01


@pytest.mark.parametrize("codec,name,ok,bad", [
    ("h264", "film.mp4", True, "film.avi"),
    ("h265", "film.mp4", True, "film.webm"),
    ("prores", "film.mov", True, "film.mp4"),
])
def test_file_types_are_checked_before_any_work(codec, name, ok, bad):
    if shutil.which("ffmpeg"):
        check_video_target(name, codec)
    with pytest.raises(ValueError, match="needs a file ending"):
        check_video_target(bad, codec)
    with pytest.raises(ValueError, match="Unknown video format"):
        check_video_target(name, "mpeg1")


def test_prores_into_mp4_is_refused_before_measuring(tmp_path, capsys):
    for i in range(6):
        Image.fromarray(sky(64, 96)).save(tmp_path / f"f{i}.jpg", quality=95)
    assert cli.main(["finish", str(tmp_path), "--video", str(tmp_path / "x.mp4"), "--codec", "prores"]) == 2
    captured = capsys.readouterr()
    assert ".mov" in captured.err and "Measuring" not in captured.out + captured.err


def _probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=codec_name,pix_fmt,profile", "-of", "default=nw=1", str(path)],
                         capture_output=True, text=True).stdout
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
@pytest.mark.parametrize("codec,suffix,expect", [
    ("h264", ".mp4", {"codec_name": "h264", "pix_fmt": "yuv420p"}),
    ("h265", ".mp4", {"codec_name": "hevc", "pix_fmt": "yuv420p10le"}),
    ("prores", ".mov", {"codec_name": "prores", "pix_fmt": "yuv422p10le"}),
])
def test_each_video_format_is_what_it_says(tmp_path, codec, suffix, expect):
    from harbium_timelapse.finish import encoders_available
    if not encoders_available(codec):
        pytest.skip(f"this ffmpeg has no encoder for {codec}")
    rng = np.random.default_rng(3)
    for i in range(12):
        frame = np.clip(sky(96, 160).astype(float) * 2 ** rng.normal(0, 0.05), 0, 255).astype(np.uint8)
        Image.fromarray(frame).save(tmp_path / f"f{i:02d}.jpg", quality=95)
    target = tmp_path / ("film" + suffix)
    assert cli.main(["finish", str(tmp_path), "--video", str(target), "--codec", codec, "--fps", "12"]) == 0
    info = _probe(target)
    for key, value in expect.items():
        assert info[key] == value
