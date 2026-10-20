"""Interrupted runs, damaged records and impossible values must never leave a
folder in a state the next command gets wrong. Each case was reproduced
against the code before the fix."""

import json

import numpy as np
import pytest

from test_rapidraw import make_sequence
import harbium_timelapse.editors.rapidraw.deflicker as D
from harbium_timelapse.editors.rapidraw import apply, cli, load, plan, undo
from harbium_timelapse.editors.rapidraw.sidecar import get_exposure, read_sidecar, set_exposure
from harbium_timelapse.editors.rapidraw.state import LedgerDamaged, read_ledger

RAW = np.linspace(0, -1, 30) + np.random.default_rng(0).normal(0, 0.05, 30)


def exposures(images):
    return [get_exposure(read_sidecar(i)) for i in images]


def interrupt_after(monkeypatch, frames):
    real, count = D.write_sidecar, {"n": 0}
    def write(image, data):
        count["n"] += 1
        if count["n"] > frames:
            raise KeyboardInterrupt
        real(image, data)
    monkeypatch.setattr(D, "write_sidecar", write)


def test_interrupted_deflicker_then_rerun_matches_a_clean_run(tmp_path, monkeypatch):
    (tmp_path / "clean").mkdir()
    (tmp_path / "cut").mkdir()
    clean = make_sequence(tmp_path / "clean", 30)
    apply(plan(load(tmp_path / "clean"), raw_brightness=RAW))
    images = make_sequence(tmp_path / "cut", 30)
    interrupt_after(monkeypatch, 12)
    with pytest.raises(KeyboardInterrupt):
        apply(plan(load(tmp_path / "cut"), raw_brightness=RAW))
    assert len(read_ledger(tmp_path / "cut")) == 12          # the record matches what was written
    monkeypatch.undo()
    apply(plan(load(tmp_path / "cut"), raw_brightness=RAW))
    assert np.allclose(exposures(images), exposures(clean), atol=1e-6)


def test_interrupted_undo_can_simply_be_run_again(tmp_path, monkeypatch):
    images = make_sequence(tmp_path, 30)
    before = exposures(images)
    apply(plan(load(tmp_path), raw_brightness=RAW))
    interrupt_after(monkeypatch, 10)
    with pytest.raises(KeyboardInterrupt):
        undo(tmp_path)
    monkeypatch.undo()
    undo(tmp_path)
    assert np.allclose(exposures(images), before, atol=1e-6)


def test_unknown_values_are_refused_before_anything_is_written(tmp_path):
    images = make_sequence(tmp_path, 30)
    before = exposures(images)
    p = plan(load(tmp_path), raw_brightness=RAW)
    object.__setattr__(p.frames[20], "correction", float("nan"))
    with pytest.raises(ValueError, match="unknown"):
        apply(p)
    assert exposures(images) == before                      # not one file touched
    with pytest.raises(ValueError):
        set_exposure({}, float("inf"))


def test_damaged_record_explains_itself(tmp_path, capsys):
    make_sequence(tmp_path, 10)
    (tmp_path / ".timelapse-core").mkdir(exist_ok=True)
    (tmp_path / ".timelapse-core" / "rapidraw.json").write_text('{"version":1,"frames":{"a')
    with pytest.raises(LedgerDamaged):
        read_ledger(tmp_path)
    assert cli.main(["undo", str(tmp_path), "--force"]) == 1
    err = capsys.readouterr().err
    assert "damaged" in err and "restore" in err and "Traceback" not in err


def test_ledger_written_atomically(tmp_path):
    make_sequence(tmp_path, 10)
    apply(plan(load(tmp_path), raw_brightness=RAW[:10]))
    assert json.loads((tmp_path / ".timelapse-core" / "rapidraw.json").read_text())["frames"]
    assert not list((tmp_path / ".timelapse-core").glob("*.tmp"))


def test_mixed_raw_and_jpeg_is_refused_unless_forced(tmp_path, capsys):
    images = make_sequence(tmp_path, 10)
    (tmp_path / "181A1003.JPG").write_bytes(b"\xff\xd8\xff")          # a RAW+JPEG pair in the folder
    before = exposures(images)
    assert cli.main(["deflicker", str(tmp_path)]) == 1
    assert "Not changing anything" in capsys.readouterr().err
    assert exposures(images) == before


# --- one run at a time ----------------------------------------------------------

def test_a_second_run_is_refused_while_one_is_writing(tmp_path, capsys):
    import json as _json
    import os as _os
    import time as _time
    images = make_sequence(tmp_path, 10)
    lock = tmp_path / ".timelapse-core" / "lock"
    lock.parent.mkdir(exist_ok=True)
    lock.write_text(_json.dumps({"pid": _os.getppid(), "started": _time.time()}))   # a live process
    before = exposures(images)
    with pytest.raises(ValueError, match="Another run"):
        apply(plan(load(tmp_path), raw_brightness=RAW[:10]))
    assert exposures(images) == before


def test_a_lock_left_by_a_crashed_run_is_cleared(tmp_path):
    import json as _json
    import time as _time
    make_sequence(tmp_path, 10)
    lock = tmp_path / ".timelapse-core" / "lock"
    lock.parent.mkdir(exist_ok=True)
    lock.write_text(_json.dumps({"pid": 999999, "started": _time.time()}))           # no such process
    apply(plan(load(tmp_path), raw_brightness=RAW[:10]))
    assert not lock.exists()                                                           # released afterwards
    lock.write_text(_json.dumps({"pid": 1, "started": _time.time() - 7 * 3600}))     # ancient
    apply(plan(load(tmp_path), raw_brightness=RAW[:10]))


def test_nested_steps_dont_lock_themselves_out(tmp_path):
    from harbium_timelapse.editors.rapidraw import rebuild
    images = make_sequence(tmp_path, 10)
    apply(plan(load(tmp_path), raw_brightness=RAW[:10]))
    rebuild(load(tmp_path))                     # rebuild -> blend -> ledger: all under one lock
    assert not (tmp_path / ".timelapse-core" / "lock").exists()
    assert len(exposures(images)) == 10
