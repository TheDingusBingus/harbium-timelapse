"""The measurement cache: measure a folder once, never trust it once anything changes."""

import os
import shutil

import numpy as np
import pytest
from PIL import Image

import harbium_timelapse.app.session as session
from harbium_timelapse import measure_cache
from harbium_timelapse.progress import Tracker


def frames(folder, n=16):
    folder.mkdir()
    rng = np.random.default_rng(4)
    base = np.exp(rng.normal(-1.6, 0.5, (48, 64)))[..., None] * np.array([1.0, 0.95, 0.85])
    for i in range(n):
        img = np.clip(base * 2 ** rng.normal(0, 0.08), 0, 1) ** (1 / 2.2) * 255
        Image.fromarray(img.astype(np.uint8)).save(folder / f"f{i:02d}.png")
    return sorted(folder.iterdir())


def counting(monkeypatch):
    calls = {"n": 0}
    real = session.measure_channels
    def measure(*a, **k):
        calls["n"] += 1
        return real(*a, **k)
    monkeypatch.setattr(session, "measure_channels", measure)
    return calls


def test_a_folder_is_measured_once(tmp_path, monkeypatch):
    frames(tmp_path / "e")
    calls = counting(monkeypatch)
    first = session.plan_export(tmp_path / "e")
    second = session.plan_export(tmp_path / "e")
    assert calls["n"] == 1
    assert np.array_equal(first.plan.measured, second.plan.measured, equal_nan=True)
    assert first.motion == second.motion and np.array_equal(first.plan.gains, second.plan.gains)


def test_changing_any_frame_measures_again(tmp_path, monkeypatch):
    paths = frames(tmp_path / "e")
    calls = counting(monkeypatch)
    session.plan_export(tmp_path / "e")
    st = paths[7].stat()
    os.utime(paths[7], ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))   # touched, e.g. re-exported
    session.plan_export(tmp_path / "e")
    assert calls["n"] == 2


def test_raw_measurements_are_remembered_too(tmp_path, monkeypatch):
    paths = frames(tmp_path / "e", 6)
    seq = type("Seq", (), {"__iter__": lambda self: iter([type("F", (), {"path": p})() for p in paths])})()
    calls = {"n": 0}
    def fake(seq, progress=None):
        calls["n"] += 1
        return np.arange(6.0)
    monkeypatch.setattr(session, "measure_brightness", fake)
    assert np.array_equal(session.measure(seq), np.arange(6.0))
    assert np.array_equal(session.measure(seq), np.arange(6.0)) and calls["n"] == 1


def test_the_cache_can_be_turned_off(tmp_path, monkeypatch):
    frames(tmp_path / "e")
    monkeypatch.setenv("HARBIUM_NO_CACHE", "1")
    calls = counting(monkeypatch)
    session.plan_export(tmp_path / "e")
    session.plan_export(tmp_path / "e")
    assert calls["n"] == 2


def test_damaged_or_unsafe_cache_files_are_never_used(tmp_path, monkeypatch):
    paths = frames(tmp_path / "e")
    key = measure_cache.key(paths, "export-channels")
    folder = measure_cache._folder()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{key}.npz").write_bytes(b"not a cache file")
    assert measure_cache.load(paths, "export-channels") is None
    np.savez(folder / f"{key}.npz", measured=np.array([object()] * len(paths), dtype=object))
    assert measure_cache.load(paths, "export-channels") is None          # pickled objects refused
    np.savez(folder / f"{key}.npz", measured=np.zeros((3, 3)))
    assert measure_cache.load(paths, "export-channels") is None          # wrong length for this folder


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_finishing_the_video_file_is_shown_as_its_own_stage(tmp_path):
    frames(tmp_path / "e")
    stages = []
    tracker = Tracker(lambda p: stages.append(p.stage))
    session.write_export(session.plan_export(tmp_path / "e"), video=tmp_path / "film.mp4", tracker=tracker)
    assert stages[-1] == "Finishing the video file" and "Writing" in stages


def test_master_is_up_to_4k():
    from harbium_timelapse.gui.models.app import PRESETS
    assert PRESETS["master"]["width"] == 3840 and "up to 4K" in PRESETS["master"]["detail"]
