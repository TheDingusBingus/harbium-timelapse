"""Absolute white balance, in RapidRAW's own format (learned from two real
sidecars: "whiteBalance": {"temperature": 5600, "tint": 0}), from as-shot
Kelvin shaped like the real R6 sequence's Auto WB."""

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from test_keyframes import edit
from test_rapidraw import exposures, jitter, make_sequence, sunset
from timelapse_core import Sequence
from timelapse_rapidraw import (
    apply, apply_white_balance, check, load, plan, plan_white_balance, rebuild, undo, undo_white_balance,
)
from timelapse_rapidraw import cli
from timelapse_rapidraw.sidecar import dump, read_sidecar, sidecar_path

REAL = Path(__file__).parent / "fixtures" / "r6_dusk_as_shot_kelvin.txt"


def camera_kelvin(n, seed=5):
    rng = np.random.default_rng(seed)
    k = np.linspace(6100, 7000, n) + rng.normal(0, 110, n)
    for at, jump in [(30, -450), (45, +300), (60, +480), (110, -400)]:
        k[at:] += jump
    return k


def with_kelvin(seq, kelvin):
    return Sequence(seq.folder, tuple(replace(f, white_balance=float(k)) for f, k in zip(seq, kelvin)))


def written(images):
    return [read_sidecar(i)["adjustments"]["whiteBalance"] for i in images]


@pytest.fixture
def folder(tmp_path):
    images = make_sequence(tmp_path, 150)
    return tmp_path, images, camera_kelvin(150)


def test_written_in_rapidraws_own_format(folder):
    path, images, kelvin = folder
    seq = with_kelvin(load(path), kelvin)
    apply_white_balance(seq, plan_white_balance(seq))
    wb = written(images)
    assert all(set(w) == {"temperature", "tint"} and isinstance(w["temperature"], int) and w["tint"] == 0 for w in wb)
    # nothing else touched: the relative sliders stay as they were
    assert all(read_sidecar(i)["adjustments"]["temperature"] == 0 for i in images)


def test_smooth_removes_the_jumps_and_keeps_the_journey(folder):
    path, images, kelvin = folder
    seq = with_kelvin(load(path), kelvin)
    wb = plan_white_balance(seq)
    assert wb.biggest_jump_before > 400
    assert wb.biggest_jump_after < 60
    assert wb.kelvin[-1] - wb.kelvin[0] > 300                          # the drift survives


def test_on_the_real_r6_sequence():
    k = np.array([float(l) for l in REAL.read_text().splitlines() if l and not l.startswith("#")])
    from timelapse_core import smooth_brightness
    smooth = 1e6 / smooth_brightness(1e6 / k, 50)
    assert np.abs(np.diff(k)).max() > 600                              # 669 K jumps from the camera
    assert np.abs(np.diff(smooth)).max() < 50
    assert smooth.min() < 5400 and smooth.max() > 7100                 # daylight → blue hour → lamps kept


def test_first_and_fixed(folder):
    path, images, kelvin = folder
    seq = with_kelvin(load(path), kelvin)
    assert set(plan_white_balance(seq, "first").kelvin) == {round(kelvin[0])}
    assert set(plan_white_balance(seq, 5600).kelvin) == {5600}
    with pytest.raises(ValueError, match="outside"):
        plan_white_balance(seq, 900)


def test_needs_the_as_shot_values(folder):
    path, _, _ = folder
    with pytest.raises(ValueError, match="needs ExifTool"):
        plan_white_balance(load(path))                                 # no Kelvin read
    assert set(plan_white_balance(load(path), 5600).kelvin) == {5600}  # a fixed value doesn't need them


def test_undo_puts_back_null(folder):
    path, images, kelvin = folder
    before = [sidecar_path(i).read_bytes() for i in images]
    seq = with_kelvin(load(path), kelvin)
    apply_white_balance(seq, plan_white_balance(seq))
    apply_white_balance(seq, plan_white_balance(seq, 5600))           # twice: undo goes to the true original
    undo_white_balance(seq)
    assert [sidecar_path(i).read_bytes() for i in images] == before


def test_survives_deflicker_undo_and_rebuild(folder):
    path, images, kelvin = folder
    seq = with_kelvin(load(path), kelvin)
    apply_white_balance(seq, plan_white_balance(seq))
    wb = written(images)
    raw = sunset(150) + jitter(150, 0.1)
    apply(plan(load(path), raw_brightness=raw))
    assert written(images) == wb                                       # deflicker leaves it be
    assert check(load(path)) == []

    for i in images[:20]:                                              # RapidRAW resets some frames
        data = read_sidecar(i)
        data["adjustments"]["whiteBalance"] = None
        data["adjustments"]["exposure"] = 0
        sidecar_path(i).write_text(dump(data), encoding="utf-8")
    rebuild(load(path))
    assert written(images) == wb

    undo(path)                                                         # removing deflicker keeps white balance
    assert written(images) == wb
    assert np.allclose(exposures(images), 0)
    undo_white_balance(with_kelvin(load(path), kelvin))
    assert written(images) == [None] * 150


def test_cli_refuses_while_rapidraw_runs(folder, monkeypatch, capsys):
    path, _, _ = folder
    monkeypatch.setattr(cli, "rapidraw_running", lambda: True)
    assert cli.main(["white-balance", str(path), "--to", "5600"]) == 1
    assert "RapidRAW is running" in capsys.readouterr().err


def test_keyframe_blend_keeps_the_steady_white_balance(folder):
    from timelapse_rapidraw import blend, label_keyframes
    path, images, kelvin = folder
    seq = with_kelvin(load(path), kelvin)
    apply_white_balance(seq, plan_white_balance(seq))
    wb = written(images)
    label_keyframes(load(path), [0, 149])
    edit(images[149], contrast=30)
    blend(load(path))
    assert written(images) == wb
    assert check(load(path)) == []
    undo_white_balance(with_kelvin(load(path), kelvin))               # still undoable after a blend
    assert written(images) == [None] * 150
