"""Built on two real RapidRAW sidecars (EOS R6, exposure +1 and +0.33)."""

import json
import shutil
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from timelapse_core import Frame
from timelapse_rapidraw import apply, check, load, plan, undo
from timelapse_rapidraw.sidecar import (
    dump, get_exposure, read_sidecar, set_exposure, sidecar_path, with_sidecar_settings,
)

FIXTURES = Path(__file__).parent / "fixtures"
REAL = FIXTURES / "181A0968.CR3.rrdata"


def sunset(n):
    return -2.0 / (1 + np.exp(-np.linspace(-4, 4, n)))


def jitter(n, size=0.08, seed=7):
    return np.random.default_rng(seed).normal(0, size, n)


def make_sequence(folder: Path, n: int, exposures=None) -> list[Path]:
    """Fake CR3 frames, each with a real RapidRAW sidecar."""
    template = json.loads(REAL.read_text(encoding="utf-8"))
    images = []
    for i in range(n):
        image = folder / f"181A{i + 1000:04d}.CR3"
        image.write_bytes(b"not really a raw")
        data = json.loads(json.dumps(template))
        set_exposure(data, 0 if exposures is None else exposures[i])
        data["exif"]["DateTimeOriginal"] = f"2026-09-22 19:{i // 12:02d}:{(i % 12) * 5:02d}"
        data["exif"]["SubSecTimeOriginal"] = "00"
        sidecar_path(image).write_text(dump(data), encoding="utf-8")
        images.append(image)
    return images


def exposures(images):
    return np.array([get_exposure(read_sidecar(i)) for i in images])


# --- the sidecar format -----------------------------------------------------

@pytest.mark.parametrize("name", ["181A0968.CR3.rrdata", "181A0969.CR3.rrdata"])
def test_real_sidecars_round_trip_byte_for_byte(name):
    raw = (FIXTURES / name).read_text(encoding="utf-8")
    assert dump(json.loads(raw)) == raw


def test_real_sidecar_exposures_are_read_in_stops():
    assert get_exposure(json.loads((FIXTURES / "181A0968.CR3.rrdata").read_text())) == 1
    assert get_exposure(json.loads((FIXTURES / "181A0969.CR3.rrdata").read_text())) == pytest.approx(0.33)


def test_settings_come_from_rapidraws_exif_copy_when_core_cant_read_cr3():
    frame = with_sidecar_settings(Frame(Path("181A0968.CR3")), json.loads(REAL.read_text()))
    assert frame.shutter == pytest.approx(1 / 320)
    assert frame.aperture == 4
    assert frame.iso == 100
    assert frame.taken_at == datetime(2026, 9, 22, 12, 21, 55, 700000)
    assert frame.camera == "EOS R6"


def test_sidecar_settings_never_override_what_the_core_read():
    frame = Frame(Path("x.CR3"), shutter=1 / 50)
    assert with_sidecar_settings(frame, json.loads(REAL.read_text())).shutter == 1 / 50


# --- deflicker --------------------------------------------------------------

def test_deflicker_writes_only_exposure(tmp_path):
    images = make_sequence(tmp_path, 60)
    before = [read_sidecar(i) for i in images]
    seq = load(tmp_path)
    apply(plan(seq, raw_brightness=sunset(60) + jitter(60)))
    for image, old in zip(images, before):
        new = read_sidecar(image)
        old["adjustments"].pop("exposure")
        new["adjustments"].pop("exposure")
        assert new == old


def test_what_you_see_stops_flickering(tmp_path):
    images = make_sequence(tmp_path, 120)
    raw = sunset(120) + jitter(120)
    apply(plan(load(tmp_path), raw_brightness=raw))
    seen = raw + exposures(images)
    assert np.std(np.diff(seen)) < np.std(np.diff(raw)) / 5
    assert seen[-1] - seen[0] == pytest.approx(sunset(120)[-1] - sunset(120)[0], abs=0.08)


def test_existing_exposure_ramp_is_kept_and_built_on(tmp_path):
    ramp = np.linspace(0, 2, 80)                       # your manual day-to-night lift
    images = make_sequence(tmp_path, 80, exposures=ramp)
    raw = sunset(80) + jitter(80)
    p = plan(load(tmp_path), raw_brightness=raw)
    assert np.allclose([fp.base for fp in p.frames], ramp, atol=1e-4)
    apply(p)
    seen = raw + exposures(images)
    assert np.std(np.diff(seen)) < np.std(np.diff(raw + ramp)) / 5      # wobble gone
    drift = seen - (sunset(80) + ramp)
    assert np.abs(drift - drift.mean()).max() < 0.1                      # ramp still there


def test_running_twice_replaces_rather_than_stacks(tmp_path):
    images = make_sequence(tmp_path, 50)
    raw = sunset(50) + jitter(50)
    apply(plan(load(tmp_path), raw_brightness=raw))
    first = exposures(images)
    apply(plan(load(tmp_path), raw_brightness=raw))
    assert np.allclose(exposures(images), first, atol=1e-4)


def test_your_tweak_after_deflicker_is_kept(tmp_path):
    images = make_sequence(tmp_path, 50)
    raw = sunset(50) + jitter(50)
    apply(plan(load(tmp_path), raw_brightness=raw))

    data = read_sidecar(images[10])                    # you nudge one frame in RapidRAW
    set_exposure(data, get_exposure(data) + 0.3)
    sidecar_path(images[10]).write_text(dump(data), encoding="utf-8")

    p = plan(load(tmp_path), raw_brightness=raw)
    assert p.frames[10].base == pytest.approx(0.3, abs=1e-4)


def test_undo_restores_every_sidecar_byte_for_byte(tmp_path):
    images = make_sequence(tmp_path, 40, exposures=np.round(np.linspace(0, 1, 40), 2))
    originals = [sidecar_path(i).read_bytes() for i in images]
    apply(plan(load(tmp_path), raw_brightness=sunset(40) + jitter(40)))
    assert [sidecar_path(i).read_bytes() for i in images] != originals
    assert undo(tmp_path) == 40
    assert [sidecar_path(i).read_bytes() for i in images] == originals


def test_untouched_copies_are_kept(tmp_path):
    images = make_sequence(tmp_path, 10)
    original = sidecar_path(images[0]).read_bytes()
    apply(plan(load(tmp_path), raw_brightness=jitter(10)))
    apply(plan(load(tmp_path), raw_brightness=jitter(10, seed=8)))
    assert (tmp_path / ".timelapse-core" / "originals" / sidecar_path(images[0]).name).read_bytes() == original


# --- checks -----------------------------------------------------------------

def test_missing_sidecars_are_serious_and_block_writing(tmp_path):
    images = make_sequence(tmp_path, 10)
    sidecar_path(images[3]).unlink()
    seq = load(tmp_path)
    assert any(p.kind == "no_sidecar" and p.serious for p in check(seq))
    with pytest.raises(ValueError, match="no RapidRAW sidecar"):
        apply(plan(seq, raw_brightness=jitter(10)))


def test_mismatched_edits_are_noted(tmp_path):
    images = make_sequence(tmp_path, 10)
    data = read_sidecar(images[4])
    data["adjustments"]["contrast"] = 20
    sidecar_path(images[4]).write_text(dump(data), encoding="utf-8")
    assert "different_edits" in [p.kind for p in check(load(tmp_path))]


def test_virtual_copies_are_noted_and_left_alone(tmp_path):
    images = make_sequence(tmp_path, 10)
    copy = tmp_path / (images[0].name + ".a1b2c3.rrdata")
    shutil.copy(sidecar_path(images[0]), copy)
    before = copy.read_bytes()
    seq = load(tmp_path)
    assert "virtual_copies" in [p.kind for p in check(seq)]
    apply(plan(seq, raw_brightness=jitter(10)))
    assert copy.read_bytes() == before


def test_clean_sequence_has_no_problems(tmp_path):
    make_sequence(tmp_path, 24)
    assert check(load(tmp_path)) == []
