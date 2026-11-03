"""The app's view model, driven the way the screens drive it, without a window."""

import hashlib
import shutil
import time
import types

import numpy as np
import pytest
from PIL import Image

import harbium_timelapse.editors.lightroom.adapter as lr_adapter
from harbium_timelapse.app import session
from harbium_timelapse.gui.models import AppModel, frame_at, render_timeline, Series
from harbium_timelapse.gui.models.app import STOPPED

N = 20
RAW = np.linspace(0, -0.8, N) + np.random.default_rng(2).normal(0, 0.06, N)


def xmp(name, exposure):
    return ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            f'<rdf:Description rdf:about="" xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/" '
            f'crs:Exposure2012="{exposure}" crs:RawFileName="{name}.CR2" crs:HasSettings="True"/>'
            '</rdf:RDF></x:xmpmeta>').encode()


@pytest.fixture
def folder(tmp_path, monkeypatch):
    raw = tmp_path / "timelapse photos"
    raw.mkdir()
    for i in range(N):
        (raw / f"IMG_{1000 + i}.CR2").write_bytes(b"")
        (raw / f"IMG_{1000 + i}.xmp").write_bytes(xmp(f"IMG_{1000 + i}", "0.00"))
    monkeypatch.setattr(lr_adapter, "running_programs", lambda: [])
    return raw


def services():
    """The real operations, except measuring (the stand-in photos are empty files) and the folder
    check's complaints about their missing camera settings."""
    def open_folder(folder, tracker=None):
        opened = session.open_folder(folder, tracker)
        opened.problems = [p for p in opened.problems if p.kind not in ("unknown_settings", "missing_time")]
        return opened
    return types.SimpleNamespace(**{**vars(session), "open_folder": open_folder,
                                    "measure": lambda seq, tracker=None: RAW.copy()})


def until(condition, timeout=20):
    end = time.time() + timeout
    while not condition():
        assert time.time() < end, "timed out"
        time.sleep(0.02)


def digest(folder):
    return {p.name: hashlib.sha1(p.read_bytes()).hexdigest() for p in sorted(folder.glob("*.xmp"))}


def opened(folder):
    m = AppModel(services=services())
    m.open_folder(folder)
    m.wait()
    assert m.screen == "folder", m.message
    return m


def test_opening_a_folder_shows_its_facts_and_remembers_it(folder):
    m = opened(folder)
    assert dict(m.facts)["Frames"] == str(N) and dict(m.facts)["Editor"] == "Lightroom"
    assert m.next_step == ("prepare", "Next: preview corrections")
    assert m.recent[0]["folder"].endswith("timelapse photos")


def test_the_stage_bar_only_opens_what_makes_sense(folder):
    m = AppModel(services=services())
    assert not m.can_go("prepare") and not m.can_go("finish")
    m.open_folder(folder)
    m.wait()
    states = {s: state for s, _, state in m.stage_items()}
    assert states["folder"] == "current" and states["prepare"] == "open"
    assert states["grade"] == "locked" and states["done"] == "locked"


def test_prepare_measures_once_previews_then_applies_and_undoes(folder):
    before = digest(folder)
    m = opened(folder)
    m.go("prepare")
    until(lambda: m.preview is not None and not m.previewing)
    measured, curve = m.prepare_series
    assert len(measured) == len(curve) == N and "wobble" in m.prepare_summary
    assert digest(folder) == before                                   # previewing writes nothing
    for w in (9, 15, 31, 45):                                          # a burst of slider moves
        m.set_window(w)
    until(lambda: not m.previewing)
    assert m.window == 45 and m.preview is not None
    assert m.needs_metadata_saved and not m.can_apply                  # Lightroom: save metadata first
    m.confirm_metadata_saved(True)
    m.apply()
    m.wait()
    assert m.screen == "grade" and m.project.stage == "prepared" and "Corrections applied" in m.message
    assert digest(folder) != before
    m.undo()
    m.wait()
    assert m.screen == "folder" and m.project.stage == "opened" and digest(folder) == before


def test_cancel_and_failures_are_explained(folder):
    m = opened(folder)
    def slow(tracker):
        hook = tracker.stage("Writing")
        for i in range(100_000):
            time.sleep(0.001)
            hook(i, 100_000)
    m._run("Writing", slow, lambda r: None)
    until(lambda: m.job is not None and m.job.total > 0)
    m.cancel()
    m.wait()
    assert m.job is None and m.message == STOPPED
    m.open_folder(folder / "not-here")
    assert m.message_kind == "error" and "isn't a folder" in m.message


def sky(folder, n=N):
    folder.mkdir()
    rng = np.random.default_rng(2)
    base = np.exp(rng.normal(-1.6, 0.5, (48, 64)))[..., None] * np.array([1.0, 0.95, 0.85])
    for i in range(n):
        img = np.clip(base * 2 ** rng.normal(0, 0.08), 0, 1) ** (1 / 2.2) * 255
        Image.fromarray(img.astype(np.uint8)).save(folder / f"f{i:02d}.png")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_the_export_is_noticed_finished_and_reviewed(folder, tmp_path):
    m = opened(folder)
    m.project.reach("prepared")
    m.go("grade")
    m.export_since = time.time() - 1
    m.poll_export()
    assert m.export.state == "waiting"
    sky(folder.parent / "final-export")
    m.poll_export()
    assert m.export.state == "arriving" and m.export.folder.name == "final-export"
    m.use_export()
    assert m.screen == "finish" and m.video_path.suffix == ".mp4"
    m.choose_preset("edit")
    assert m.video_path.suffix == ".mov"
    m.choose_preset("share")
    m.set_video_path(tmp_path / "film.mp4")
    m.start_finish()
    m.wait()
    assert m.screen == "done" and (tmp_path / "film.mp4").exists(), m.message
    assert "Brightness wobble" in m.result_lines[0] and m.project.stage == "finished"
    for name, _ in m.worth_a_look[:1]:
        m.looks_fine(name)
        assert name not in [n for n, _ in m.worth_a_look]


def test_the_timeline_draws_and_clicks_map_to_frames():
    img = render_timeline([Series(RAW, (132, 127, 117)), Series(RAW * 0.5, (232, 228, 219), 3)], 600, 160,
                          steps=(5,), flags=(9,), current=12, cant_judge=(0, 1, N - 2, N - 1))
    assert img.shape == (160, 600, 3) and img.std() > 0
    assert frame_at(0, 600, N) == 0 and frame_at(600, 600, N) == N - 1 and frame_at(300, 600, N) in (9, 10)
