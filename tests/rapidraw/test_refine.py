"""Refine against a render, with a simulated RapidRAW whose Exposure slider
is stronger than real light, by different amounts at different brightnesses,
as measured on a real Canon R6 dusk sequence (1.8x in daylight, 1.4x at night)."""


import numpy as np
import pytest

from test_rapidraw import exposures, jitter, make_sequence, sunset
from harbium_timelapse.editors.rapidraw import apply, load, plan, refine, undo


def slider_strength(seen):
    """How much stronger than real light the slider acts: 1.8 bright → 1.4 dark."""
    return np.interp(seen, [-2.0, 0.0], [1.4, 1.8])


def fake_render(raw, exposure):
    """What the export measures: the grade adds contrast (x1.5), and the
    slider counts for more than real light."""
    return 1.5 * (raw + slider_strength(raw) * exposure)


def flicker(values):
    return float(np.std(np.diff(values)) / np.sqrt(2))


@pytest.fixture
def deflickered(tmp_path):
    n = 150
    raw = sunset(n) + jitter(n, 0.1)
    make_sequence(tmp_path, n)
    apply(plan(load(tmp_path), raw_brightness=raw))
    return tmp_path, raw


def test_first_render_overcorrects_like_the_real_one(deflickered):
    folder, raw = deflickered
    render = fake_render(raw, exposures(sorted(folder.glob("*.CR3"))))
    assert flicker(render) > 0.05                     # deflicker alone isn't enough with this slider


def test_refine_converges_in_a_few_passes(deflickered):
    folder, raw = deflickered
    images = sorted(folder.glob("*.CR3"))
    history = []
    for _ in range(3):
        render = fake_render(raw, exposures(images))
        history.append(flicker(render))
        refine(load(folder), render_brightness=render, raw_brightness=raw)
    final = flicker(fake_render(raw, exposures(images)))
    assert history[1] < history[0] / 2
    assert final < 0.02                               # invisible


def test_refine_keeps_the_sunset(deflickered):
    folder, raw = deflickered
    images = sorted(folder.glob("*.CR3"))
    for _ in range(3):
        refine(load(folder), render_brightness=fake_render(raw, exposures(images)), raw_brightness=raw)
    render = fake_render(raw, exposures(images))
    assert render[-1] - render[0] < -1.5              # still gets dark: the light's arc survives


def test_nothing_to_do_when_the_render_is_already_clean(tmp_path):
    make_sequence(tmp_path, 40)
    apply(plan(load(tmp_path), raw_brightness=np.linspace(0, -1, 40)))
    result = refine(load(tmp_path), render_brightness=np.linspace(0, -1.5, 40), raw_brightness=np.linspace(0, -1, 40))
    assert result.nothing_to_do and result.changed == 0


def test_refine_needs_deflicker_first(tmp_path):
    make_sequence(tmp_path, 20)
    with pytest.raises(ValueError, match="Run deflicker first"):
        refine(load(tmp_path), render_brightness=np.zeros(20))


def test_export_must_match_the_sequence(deflickered, tmp_path_factory):
    folder, _ = deflickered
    export = tmp_path_factory.mktemp("export")
    (export / "001.jpg").write_bytes(b"x")
    with pytest.raises(ValueError, match="has 1 frames but the sequence has 150"):
        refine(load(folder), rendered=export)


def test_undo_removes_refine_too(deflickered):
    folder, raw = deflickered
    images = sorted(folder.glob("*.CR3"))
    refine(load(folder), render_brightness=fake_render(raw, exposures(images)), raw_brightness=raw)
    undo(folder)
    assert np.allclose(exposures(images), 0)
    assert not (folder / ".timelapse-core" / "refine.json").exists()


def test_a_fresh_deflicker_forgets_old_passes(deflickered):
    folder, raw = deflickered
    images = sorted(folder.glob("*.CR3"))
    refine(load(folder), render_brightness=fake_render(raw, exposures(images)), raw_brightness=raw)
    assert (folder / ".timelapse-core" / "refine.json").exists()
    apply(plan(load(folder), raw_brightness=raw))
    assert not (folder / ".timelapse-core" / "refine.json").exists()
