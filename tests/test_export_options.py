"""Every export option does what it says, and impossible combinations are explained."""

import shutil
import subprocess

import numpy as np
import pytest
from PIL import Image

from harbium_timelapse import cli
from harbium_timelapse import finish as fin
from harbium_timelapse.app import session
from harbium_timelapse.finish import VideoOptions

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


@pytest.mark.parametrize("opts,message", [
    (VideoOptions("h264", bit_depth=10), "8-bit here"),
    (VideoOptions("prores", chroma="444"), "4444 profile"),
    (VideoOptions("prores", prores_profile="ultra"), "ProRes profile"),
    (VideoOptions("h265", chroma="411"), "420, 422 or 444"),
    (VideoOptions("h264", quality=70), "between 0 and 51"),
])
def test_impossible_combinations_are_explained(opts, message):
    with pytest.raises(ValueError, match=message):
        opts.check()


def test_formats_follow_from_the_choices():
    assert VideoOptions("h264").pix_fmt == "yuv420p"
    assert VideoOptions("h264", chroma="444").pix_fmt == "yuv444p"
    assert VideoOptions("h265").pix_fmt == "yuv420p10le"
    assert VideoOptions("h265", bit_depth=8, chroma="422").pix_fmt == "yuv422p"
    assert VideoOptions("prores").pix_fmt == "yuv422p10le"
    assert VideoOptions("prores", prores_profile="4444").pix_fmt == "yuv444p10le"
    assert any("HEVC" in n for n in VideoOptions("h265").notes())


def frames(folder, n=10, w=96, h=64):
    folder.mkdir()
    rng = np.random.default_rng(1)
    base = np.linspace(30, 220, w)[None, :, None] * np.ones((h, 1, 3))
    for i in range(n):
        Image.fromarray(np.clip(base * 2 ** rng.normal(0, 0.05) + rng.normal(0, 4, base.shape), 0, 255)
                        .astype(np.uint8)).save(folder / f"f{i:02d}.png")


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
                          "stream=codec_name,pix_fmt,profile,color_space,color_primaries,color_transfer,nb_read_frames",
                          "-of", "default=nw=1", str(path)], capture_output=True, text=True).stdout
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


@needs_ffmpeg
@pytest.mark.parametrize("opts,suffix,expect", [
    (VideoOptions("h264", chroma="444"), ".mp4", {"pix_fmt": "yuv444p"}),
    (VideoOptions("h265", bit_depth=8), ".mp4", {"pix_fmt": "yuv420p"}),
    (VideoOptions("prores", prores_profile="proxy"), ".mov", {"pix_fmt": "yuv422p10le", "profile": "Proxy"}),
    # ProRes 4444 is a 12-bit format: the file reports 12-bit even though it's fed 10-bit data
    (VideoOptions("prores", prores_profile="4444"), ".mov", {"pix_fmt": "yuv444p12le", "profile": "4444"}),
])
def test_each_option_makes_what_it_says_with_hd_colour_labels(tmp_path, opts, suffix, expect):
    if not fin.encoders_available(opts.codec):
        pytest.skip(f"no {opts.codec} encoder in this ffmpeg")
    frames(tmp_path / "e")
    video = tmp_path / ("film" + suffix)
    session.write_export(session.plan_export(tmp_path / "e"), video=video, options=opts)
    info = probe(video)
    for key, value in expect.items():
        assert info[key] == value, (key, info)
    assert info["color_space"] == info["color_primaries"] == info["color_transfer"] == "bt709"


@needs_ffmpeg
def test_saturated_colours_survive_encoding(tmp_path):
    """Regression: unlabelled standard-definition conversion shifted greens and reds by up to 20 levels."""
    from pathlib import Path
    cols = np.array([[40, 170, 60], [200, 60, 40], [60, 80, 200]], np.uint8)
    frame = np.ascontiguousarray(np.repeat(np.repeat(cols[None], 64, axis=0), 64, axis=1))
    e = fin._Encoder(Path(tmp_path / "c.mp4"), frame, 24, None, None, "h264")
    for _ in range(4):
        e.write(frame)
    e.close()
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(tmp_path / "c.mp4"), "-frames:v", "1", "-vf",
                          "scale=in_color_matrix=auto:in_range=auto,format=rgb24", "-f", "rawvideo", "-"],
                         capture_output=True).stdout
    out = np.frombuffer(raw, np.uint8).reshape(frame.shape)
    assert np.abs(out[32, 32::64].astype(int) - cols.astype(int)).max() <= 4


@needs_ffmpeg
def test_trimming_writes_only_the_chosen_frames(tmp_path):
    frames(tmp_path / "e", n=12)
    planned = session.plan_export(tmp_path / "e")
    done = session.write_export(planned, out=tmp_path / "out", video=tmp_path / "t.mp4", first=3, last=8)
    assert [p.name for p in done.written] == [f"f{i:02d}.png" for i in range(2, 8)]
    assert probe(tmp_path / "t.mp4")["nb_read_frames"] == "6"
    with pytest.raises(ValueError, match="within frames 1 to 12"):
        planned.plan.trimmed(9, 20)


def test_jpeg_quality_is_used(tmp_path):
    frames(tmp_path / "e", n=6, w=320, h=200)
    for q in (40, 95):
        session.write_export(session.plan_export(tmp_path / "e"), out=tmp_path / f"q{q}", fmt="jpg", jpeg_quality=q)
    size = lambda q: sum(p.stat().st_size for p in (tmp_path / f"q{q}").glob("*.jpg"))   # noqa: E731
    assert size(40) < size(95)


def test_the_parallel_self_check_matches_one_thread(monkeypatch):
    img = (np.random.default_rng(2).random((900, 1400, 3)) * 255).astype(np.uint8)
    many = fin._small_linear(img)
    monkeypatch.setattr(fin, "CORRECT_THREADS", 1)
    assert np.array_equal(many, fin._small_linear(img))


def test_the_command_line_refuses_an_impossible_choice(tmp_path, capsys):
    frames(tmp_path / "e")
    assert cli.main(["finish", str(tmp_path / "e"), "--video", str(tmp_path / "x.mov"),
                     "--codec", "prores", "--chroma", "420"]) == 1
    assert "always 4:2:2" in capsys.readouterr().err and not (tmp_path / "x.mov").exists()
