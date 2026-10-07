"""What happened on the real sequence: refine rewrote every sidecar while
RapidRAW had the folder open, and RapidRAW saved its defaults over them,
keeping only the colour labels."""

import json

import numpy as np
import pytest

from test_keyframes import adjustment, edit
from test_rapidraw import FIXTURES, exposures, jitter, make_sequence, sunset
from test_refine import fake_render
from timelapse_rapidraw import apply, blend, check, label_keyframes, load, plan, rebuild, refine
from timelapse_rapidraw import cli
from timelapse_rapidraw.sidecar import dump, read_sidecar, sidecar_path
from timelapse_rapidraw.state import read_ledger


def rapidraw_saves_defaults(images):
    """Every adjustment back to RapidRAW's defaults; labels and EXIF kept."""
    defaults = json.loads((FIXTURES / "181A0968.CR3.rrdata").read_text(encoding="utf-8"))["adjustments"]
    defaults = {k: (0 if isinstance(v, (int, float)) and not isinstance(v, bool) and k == "exposure" else v)
                for k, v in defaults.items()}
    for image in images:
        data = read_sidecar(image)
        data["adjustments"] = dict(defaults)
        sidecar_path(image).write_text(dump(data), encoding="utf-8")


@pytest.fixture
def graded_and_refined(tmp_path):
    n = 90
    images = make_sequence(tmp_path, n)
    label_keyframes(load(tmp_path), [0, 45, 89])
    edit(images[0], contrast=-24, shadows=27, toneMapper="agx", exposure=-0.81)
    edit(images[45], contrast=-10, shadows=40, toneMapper="agx", exposure=-0.6)
    edit(images[89], contrast=5, shadows=55, toneMapper="agx", exposure=-0.4)
    blend(load(tmp_path))
    raw = sunset(n) + jitter(n, 0.1)
    apply(plan(load(tmp_path), raw_brightness=raw))
    refine(load(tmp_path), render_brightness=fake_render(raw, exposures(images)), raw_brightness=raw)

    state = {i.name: read_sidecar(i)["adjustments"] for i in images}
    return tmp_path, images, state


def test_check_notices_rapidraw_saved_over_everything(graded_and_refined):
    folder, images, _ = graded_and_refined
    rapidraw_saves_defaults(images)
    problems = check(load(folder))
    (p,) = [p for p in problems if p.kind == "overwritten"]
    assert p.serious and "rebuild" in p.message


def test_rebuild_puts_everything_back(graded_and_refined):
    folder, images, before = graded_and_refined
    ledger_before = read_ledger(folder)
    rapidraw_saves_defaults(images)

    frames, keys = rebuild(load(folder))
    assert (frames, keys) == (90, 3)
    for image in images:
        now = read_sidecar(image)["adjustments"]
        assert now.keys() == before[image.name].keys()
        for k in now:
            if isinstance(now[k], float):
                assert now[k] == pytest.approx(before[image.name][k], abs=2e-4), (image.name, k)
            else:
                assert now[k] == before[image.name][k], (image.name, k)
    assert read_sidecar(images[45])["tags"][-1] == "color:purple"
    assert {k: v["applied"] for k, v in read_ledger(folder).items()} == \
           {k: v["applied"] for k, v in ledger_before.items()}
    assert (folder / ".timelapse-core" / "refine.json").exists()      # refine can carry on
    assert [p.kind for p in check(load(folder))] == []


def test_refine_wont_build_on_overwritten_sidecars(graded_and_refined, capsys):
    folder, images, _ = graded_and_refined
    rapidraw_saves_defaults(images)
    assert cli.main(["refine", str(folder), str(folder), "--force"]) == 1
    assert "Run rebuild first" in capsys.readouterr().err


def test_writing_commands_refuse_while_rapidraw_runs(tmp_path, monkeypatch, capsys):
    make_sequence(tmp_path, 10)
    monkeypatch.setattr(cli, "rapidraw_running", lambda: True)
    for args in (["deflicker", str(tmp_path)], ["keyframes", str(tmp_path)], ["undo", str(tmp_path)],
                 ["restore", str(tmp_path)], ["rebuild", str(tmp_path)],
                 ["suggest-keyframes", str(tmp_path), "--label"]):
        assert cli.main(args) == 1, args
        assert "RapidRAW is running" in capsys.readouterr().err
    assert cli.main(["check", str(tmp_path)]) in (0, 1)            # reading is always fine
    assert "RapidRAW is running" not in capsys.readouterr().err


def test_the_guard_doesnt_mistake_this_tool_for_rapidraw():
    from timelapse_rapidraw.recover import is_rapidraw
    assert is_rapidraw("RapidRAW.exe")
    assert is_rapidraw("rapidraw")
    assert not is_rapidraw("timelapse-rapidraw.exe")   # this tool's own command on Windows
    assert not is_rapidraw("python.exe")


def test_rebuild_also_mends_just_a_couple_of_reset_frames(graded_and_refined):
    """What really happened: only the frames clicked in RapidRAW were reset."""
    folder, images, before = graded_and_refined
    rapidraw_saves_defaults([images[0], images[30]])
    assert "exposure_changed" in [p.kind for p in check(load(folder))]
    rebuild(load(folder))
    for image in (images[0], images[30]):
        assert read_sidecar(image)["adjustments"]["toneMapper"] == "agx"
        assert read_sidecar(image)["adjustments"]["exposure"] == pytest.approx(before[image.name]["exposure"], abs=2e-4)
    assert [p.kind for p in check(load(folder))] == []
