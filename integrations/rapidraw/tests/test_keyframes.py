"""Keyframes: purple labels, every setting blended between them, built on
the real RapidRAW sidecar format."""

import numpy as np
import pytest

from test_rapidraw import jitter, make_sequence, sunset
from timelapse_rapidraw import (
    apply, blend, check, keyframes_in, label_keyframes, load, plan, restore,
)
from timelapse_rapidraw.sidecar import dump, read_sidecar, sidecar_path


def edit(image, **changes):
    data = read_sidecar(image)
    for path, value in changes.items():
        target = data["adjustments"]
        *parents, last = path.split("__")
        for p in parents:
            target = target[p]
        target[last] = value
    sidecar_path(image).write_text(dump(data), encoding="utf-8")


def adjustment(image, path):
    value = read_sidecar(image)["adjustments"]
    for p in path.split("__"):
        value = value[p]
    return value


@pytest.fixture
def sunset_folder(tmp_path):
    """60 frames; keyframes at 0 (day), 30 (golden hour), 59 (night)."""
    images = make_sequence(tmp_path, 60)
    seq = load(tmp_path)
    label_keyframes(seq, [0, 30, 59])
    edit(images[0], temperature=0, contrast=10, shadows=0, exposure=0)
    edit(images[30], temperature=25, contrast=20, shadows=15, exposure=0.5)
    edit(images[59], temperature=-30, contrast=5, shadows=40, exposure=2)
    return tmp_path, images


# --- labels -----------------------------------------------------------------

def test_purple_label_replaces_other_colour_and_keeps_other_tags(tmp_path):
    images = make_sequence(tmp_path, 5)
    data = read_sidecar(images[2])
    data["tags"] = ["color:yellow", "client-favourite"]
    sidecar_path(images[2]).write_text(dump(data), encoding="utf-8")

    seq = load(tmp_path)
    notes = label_keyframes(seq, [2])
    assert read_sidecar(images[2])["tags"] == ["client-favourite", "color:purple"]
    assert notes == [f"{images[2].name}: yellow label replaced"]
    assert keyframes_in(load(tmp_path)) == [2]


# --- blending ---------------------------------------------------------------

def test_every_setting_passes_through_the_keyframes(sunset_folder):
    folder, images = sunset_folder
    blend(load(folder))
    assert adjustment(images[0], "temperature") == 0
    assert adjustment(images[30], "temperature") == 25
    assert adjustment(images[59], "temperature") == -30
    assert adjustment(images[59], "exposure") == 2


def test_frames_between_keyframes_are_overwritten_with_a_smooth_blend(sunset_folder):
    folder, images = sunset_folder
    edit(images[15], temperature=99, contrast=-50)          # a stray old edit, to be overwritten
    blend(load(folder))
    temps = np.array([adjustment(i, "temperature") for i in images])
    shadows = np.array([adjustment(i, "shadows") for i in images])
    assert 0 < temps[15] < 25                                # overwritten, and between its keyframes
    assert np.all(np.diff(temps[:31]) >= 0) and np.all(np.diff(temps[30:]) <= 0)
    assert np.all(np.diff(shadows) >= 0)                     # 0 → 15 → 40, never dips
    assert temps.max() <= 25 and temps.min() >= -30          # no overshoot


def test_settings_the_same_at_every_keyframe_are_copied_exactly(sunset_folder):
    folder, images = sunset_folder
    curves = read_sidecar(images[0])["adjustments"]["curves"]
    blend(load(folder))
    assert all(read_sidecar(i)["adjustments"]["curves"] == curves for i in images)


def test_whole_number_settings_stay_whole(sunset_folder):
    folder, images = sunset_folder
    edit(images[0], grainSize=25)
    edit(images[59], grainSize=50)
    blend(load(folder))
    assert all(isinstance(adjustment(i, "grainSize"), int) for i in images)


def test_curve_points_blend_when_shaped_alike(sunset_folder):
    folder, images = sunset_folder
    edit(images[59], curves__luma=[{"x": 0, "y": 20}, {"x": 255, "y": 235}])
    blend(load(folder))
    mid = adjustment(images[45], "curves__luma")
    assert 0 < mid[0]["y"] < 20 and 235 < mid[1]["y"] < 255


def test_what_cant_blend_is_held_and_named(sunset_folder):
    folder, images = sunset_folder
    edit(images[30], toneMapper="agx")
    edit(images[59], toneMapper="agx")
    result = blend(load(folder))
    assert "toneMapper" in result.held
    assert [adjustment(i, "toneMapper") for i in (images[0], images[29], images[30], images[45])] == \
        ["basic", "basic", "agx", "agx"]


def test_only_adjustments_change(sunset_folder):
    folder, images = sunset_folder
    before = read_sidecar(images[10])
    blend(load(folder))
    after = read_sidecar(images[10])
    assert {k: v for k, v in after.items() if k != "adjustments"} == \
           {k: v for k, v in before.items() if k != "adjustments"}


# --- with deflicker ---------------------------------------------------------

def test_blend_uses_your_keyframe_exposure_not_deflickers(sunset_folder):
    folder, images = sunset_folder
    apply(plan(load(folder), raw_brightness=sunset(60) + jitter(60)))
    assert adjustment(images[30], "exposure") != 0.5         # deflicker added to it
    blend(load(folder))
    assert adjustment(images[30], "exposure") == 0.5         # back to your own value
    assert not (folder / ".timelapse-core" / "rapidraw.json").exists()  # deflicker reset


def test_deflicker_after_blend_builds_on_the_blended_exposure(sunset_folder):
    folder, images = sunset_folder
    blend(load(folder))
    blended = np.array([adjustment(i, "exposure") for i in images])
    p = plan(load(folder), raw_brightness=sunset(60) + jitter(60))
    assert np.allclose([fp.base for fp in p.frames], blended, atol=1e-4)


# --- check and restore ------------------------------------------------------

def test_check_knows_about_keyframes(sunset_folder):
    folder, images = sunset_folder
    kinds = [p.kind for p in check(load(folder))]
    assert "off_the_blend" in kinds                          # not blended yet
    blend(load(folder))
    assert check(load(folder)) == []                         # blended: nothing to report
    edit(images[20], contrast=80)
    assert "off_the_blend" in [p.kind for p in check(load(folder))]


def test_a_single_keyframe_is_pointed_out(tmp_path):
    make_sequence(tmp_path, 10)
    label_keyframes(load(tmp_path), [4])
    assert "one_keyframe" in [p.kind for p in check(load(tmp_path))]
    with pytest.raises(ValueError, match="at least two"):
        blend(load(tmp_path))


def test_restore_puts_every_sidecar_back_byte_for_byte(sunset_folder):
    folder, images = sunset_folder
    before = [sidecar_path(i).read_bytes() for i in images]
    blend(load(folder))
    apply(plan(load(folder), raw_brightness=sunset(60) + jitter(60)))
    assert [sidecar_path(i).read_bytes() for i in images] != before
    restore(folder)
    assert [sidecar_path(i).read_bytes() for i in images] == before
