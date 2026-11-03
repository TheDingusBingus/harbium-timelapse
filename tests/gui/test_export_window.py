"""The export window's model: presets, advanced options, checks, estimates and frames-only exports."""

import shutil

import numpy as np
import pytest
from PIL import Image

from harbium_timelapse.gui.models import AppModel
from harbium_timelapse.gui.models.export import user_presets


def opened_export(tmp_path, n=12):
    folder = tmp_path / "final-export"
    folder.mkdir()
    rng = np.random.default_rng(3)
    base = np.linspace(30, 220, 160)[None, :, None] * np.ones((100, 1, 3))
    for i in range(n):
        Image.fromarray(np.clip(base * 2 ** rng.normal(0, 0.06), 0, 255).astype(np.uint8)).save(folder / f"f{i:02d}.jpg")
    m = AppModel()
    m.open_folder(folder)
    m.wait()
    m.go("finish")
    m.set_video_path(tmp_path / "film.mp4")
    return m


def test_presets_and_custom(tmp_path):
    m = opened_export(tmp_path)
    assert m.out.preset == "share" and m.out.codec == "h264"
    m.choose_preset("edit")
    assert m.out.codec == "prores" and m.video_path.suffix == ".mov"      # the file follows the format
    m.set_export(prores_profile="4444")
    assert m.out.preset == "custom"
    m.choose_preset("frames")
    assert m.out.output == "frames" and m.out.jpeg_quality == 98


def test_your_own_presets_are_saved_and_can_be_deleted(tmp_path):
    m = opened_export(tmp_path)
    m.choose_preset("master")
    m.set_export(quality=14, fps=25.0)
    m.save_preset("My 4K delivery")
    assert "My 4K delivery" in user_presets() and ("My 4K delivery", "My 4K delivery") in m.presets
    m.choose_preset("share")
    m.choose_preset("My 4K delivery")
    assert (m.out.codec, m.out.quality, m.out.fps) == ("h265", 14, 25.0)
    m.save_preset("share")
    assert m.message_kind == "error" and "built-in" in m.message
    m.delete_preset("My 4K delivery")
    assert "My 4K delivery" not in user_presets()


def test_problems_block_the_export_and_say_why(tmp_path):
    m = opened_export(tmp_path)
    assert m.can_finish and "frames at" in m.export_check["estimate"] and "free" in m.export_check["estimate"]
    m.set_export(first=9, last=4)
    assert not m.can_finish and any("frame range" in p for p in m.export_check["problems"])
    m.set_export(first=None, last=None, codec="prores", chroma="420")
    assert any("always 4:2:2" in p for p in m.export_check["problems"])
    m.set_export(codec="h264", chroma=None)
    m.set_video_path("F:/\u2026/film.mp4")
    assert any("shortened example" in p for p in m.export_check["problems"])
    m.set_video_path(tmp_path / "film.mp4")
    m.set_export(output="frames")
    m.set_frames_folder(tmp_path / "final-export")
    assert any("their own folder" in p for p in m.export_check["problems"])
    assert any("HEVC" in n for n in (m.set_export(output="video", codec="h265") or m.export_check["notes"]))


def test_a_frames_only_export_and_the_time_estimate_afterwards(tmp_path):
    m = opened_export(tmp_path)
    m.choose_preset("frames")
    m.set_frames_folder(tmp_path / "series")
    assert "first export of this kind" in m.export_check["estimate"]
    m.start_finish()
    m.wait()
    assert m.screen == "done", m.message
    assert len(list((tmp_path / "series").glob("*.jpg"))) == 12 and not (tmp_path / "film.mp4").exists()
    assert "s a frame" in " ".join(m.result_lines)
    m.go("finish")
    assert "min" in m.export_check["estimate"]                           # now from this computer's real speed


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_trimmed_video_with_advanced_options(tmp_path):
    m = opened_export(tmp_path)
    m.set_export(codec="h264", chroma="444", quality=20, resolution="native", first=3, last=10)
    m.start_finish()
    m.wait()
    assert m.screen == "done" and (tmp_path / "film.mp4").exists(), m.message
    assert len(m.finished.check) == 8
