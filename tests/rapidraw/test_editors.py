"""The editor plug-in system and the one-step `timelapse prepare`."""

import numpy as np
import pytest

import harbium_timelapse.editors as editors
import harbium_timelapse.editors.rapidraw.deflicker as rr_deflicker
from harbium_timelapse import cli
from harbium_timelapse.editors import Editor, available, detect, get
from harbium_timelapse.editors.rapidraw.sidecar import get_exposure, read_sidecar
from test_rapidraw import make_sequence

N = 30
RAW = np.linspace(0, -1, N) + np.random.default_rng(0).normal(0, 0.05, N)


@pytest.fixture
def folder(tmp_path, monkeypatch):
    images = make_sequence(tmp_path, N)
    # the synthetic RAW files can't be decoded: give deflicker known brightness instead
    monkeypatch.setattr(rr_deflicker, "measure_brightness", lambda *a, **k: RAW)
    monkeypatch.setattr("harbium_timelapse.editors.rapidraw.adapter.rapidraw_running", lambda: False)
    return tmp_path, images


def exposures(images):
    return [get_exposure(read_sidecar(i)) for i in images]


def test_rapidraw_is_built_in_and_detected(folder, tmp_path_factory):
    path, _ = folder
    assert "rapidraw" in available()
    assert [e.name for e in detect(path)] == ["rapidraw"]
    assert detect(tmp_path_factory.mktemp("empty")) == []
    with pytest.raises(ValueError, match="No editor called"):
        get("photoshop")


def test_other_packages_can_add_editors(monkeypatch):
    class Darkroom(Editor):
        name, label = "darkroom", "Darkroom"

    class FakeEP:
        def __init__(self, name, obj):
            self.name, self._obj = name, obj
        def load(self):
            if isinstance(self._obj, Exception):
                raise self._obj
            return self._obj

    monkeypatch.setattr(editors, "entry_points",
                        lambda group: [FakeEP("darkroom", Darkroom), FakeEP("broken", ImportError("nope"))])
    found = available()
    assert found["darkroom"] is Darkroom and "rapidraw" in found and "broken" not in found


def test_prepare_then_undo_end_to_end(folder, capsys):
    path, images = folder
    before = exposures(images)
    assert cli.main(["prepare", str(path)]) == 0
    out = capsys.readouterr().out
    assert "RapidRAW" in out and "Next: open the folder in RapidRAW" in out and "timelapse finish" in out
    assert exposures(images) != before
    assert cli.main(["undo", str(path)]) == 0
    assert np.allclose(exposures(images), before, atol=1e-6)


def test_prepare_dry_run_writes_nothing(folder, capsys):
    path, images = folder
    before = exposures(images)
    assert cli.main(["prepare", str(path), "--dry-run"]) == 0
    assert "nothing written" in capsys.readouterr().out
    assert exposures(images) == before


def test_prepare_refuses_while_the_editor_is_open(folder, monkeypatch, capsys):
    path, images = folder
    monkeypatch.setattr("harbium_timelapse.editors.rapidraw.adapter.rapidraw_running", lambda: True)
    before = exposures(images)
    assert cli.main(["prepare", str(path)]) == 1
    assert "is open" in capsys.readouterr().err
    assert exposures(images) == before
    assert cli.main(["prepare", str(path), "--force"]) == 0


def test_prepare_without_editor_files_explains_the_alternative(tmp_path, capsys):
    (tmp_path / "IMG_0001.CR3").write_bytes(b"x")
    assert cli.main(["prepare", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "No edit files" in err and "timelapse finish" in err
