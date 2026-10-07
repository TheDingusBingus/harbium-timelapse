import shutil
import subprocess

import pytest

from synthetic import make_folder
from timelapse_core import render_video

pytestmark = pytest.mark.skipif(not shutil.which("ffprobe"), reason="ffmpeg not installed")


def probe(video):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=width,height,nb_read_frames,r_frame_rate", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=True,
    ).stdout.strip().split(",")
    width, height, rate, frames = out
    return int(width), int(height), rate, int(frames)


def test_renders_every_frame_at_native_size(tmp_path):
    folder = tmp_path / "frames"
    folder.mkdir()
    make_folder(folder, [0.0] * 48, fmt="jpg")          # 96×64, as the camera made them
    video = render_video(folder, fps=24)
    assert video == tmp_path / "frames.mp4"
    assert probe(video) == (96, 64, "24/1", 48)


def test_odd_names_and_gaps_are_fine(tmp_path):
    folder = tmp_path / "frames"
    folder.mkdir()
    paths = make_folder(folder, [0.0] * 10, fmt="jpg")
    paths[4].unlink()
    paths[0].rename(folder / "first shot.jpg")
    assert probe(render_video(folder, tmp_path / "v.mp4", fps=25))[2:] == ("25/1", 9)


def test_width_scales_keeping_shape(tmp_path):
    folder = tmp_path / "frames"
    folder.mkdir()
    make_folder(folder, [0.0] * 5, fmt="jpg")
    assert probe(render_video(folder, fps=24, width=48))[:2] == (48, 32)


def test_no_frames_says_so(tmp_path):
    with pytest.raises(ValueError, match="No JPEG"):
        render_video(tmp_path)
