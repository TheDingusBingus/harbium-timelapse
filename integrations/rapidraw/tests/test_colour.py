"""Colour refine, against a simulated camera on Auto white balance, like the
real R6 sequence: as-shot 4845 to 7415 K, jumping up to 669 K between frames,
worst where a variable ND was turned."""

import numpy as np
import pytest

from test_keyframes import edit
from test_rapidraw import exposures, jitter, make_sequence, sunset
from test_refine import fake_render
from timelapse_rapidraw import apply, blend, label_keyframes, load, plan, rebuild, refine, undo
from timelapse_rapidraw.sidecar import read_sidecar
from timelapse_rapidraw.state import read_ledger

N = 150
PER_MIRED = -0.0152  # render warmth per mired of as-shot WB: measured on the real R6 sequence
SLIDER = 0.006       # render warmth per +1 Temperature: the tool must learn this


def as_shot_kelvin(seed=5):
    """Auto WB: a slow drift with sudden jumps, as on the real sequence."""
    rng = np.random.default_rng(seed)
    k = np.linspace(6100, 6900, N) + rng.normal(0, 110, N)   # ~3 mired wobble, as measured
    for at, jump in [(30, -450), (45, +300), (60, +480), (110, -400)]:   # ND turns, ISO steps
        k[at:] += jump
    return k


def temperatures(images):
    return np.array([float(read_sidecar(i)["adjustments"].get("temperature") or 0) for i in images])


def fake_colour(light_warmth, kelvin, temperature):
    return light_warmth + PER_MIRED * (1e6 / kelvin) + SLIDER * temperature


def wobble(v):
    return float(np.std(np.diff(v)) / np.sqrt(2))


@pytest.fixture
def awb_sequence(tmp_path, monkeypatch):
    images = make_sequence(tmp_path, N)
    seq = load(tmp_path)
    label_keyframes(seq, [0, 75, 149])
    edit(images[0], temperature=0)
    edit(images[75], temperature=20)          # golden hour, warmed by hand
    edit(images[149], temperature=-15)        # night, cooled
    blend(load(tmp_path))
    raw = sunset(N) + jitter(N, 0.1)
    apply(plan(load(tmp_path), raw_brightness=raw))
    kelvin = as_shot_kelvin()

    import timelapse_rapidraw.deflicker as d
    real_load = d.load
    def load_with_wb(folder, progress=None):
        s = real_load(folder, progress)
        from dataclasses import replace
        return s.__class__(s.folder, tuple(replace(f, white_balance=k) for f, k in zip(s.frames, kelvin)))
    return tmp_path, images, raw, kelvin, load_with_wb


def one_pass(folder, images, raw, kelvin, light_w, loader, **kw):
    render_b = fake_render(raw, exposures(images))
    render_w = fake_colour(light_w, kelvin, temperatures(images))
    return refine(loader(folder), render_brightness=render_b, raw_brightness=raw,
                  render_warmth=render_w, raw_warmth=light_w, **kw)


def test_auto_wb_jumps_are_removed(awb_sequence):
    folder, images, raw, kelvin, loader = awb_sequence
    light_w = np.linspace(0, 0.3, N)                       # the light warms gently
    before = wobble(fake_colour(light_w, kelvin, temperatures(images)))
    for _ in range(4):
        one_pass(folder, images, raw, kelvin, light_w, loader, white_balance="steady")
    after = wobble(fake_colour(light_w, kelvin, temperatures(images)))
    assert before > 0.03
    assert after < 0.01


def test_steady_keeps_the_keyframe_look(awb_sequence):
    folder, images, raw, kelvin, loader = awb_sequence
    light_w = np.linspace(0, 0.3, N)
    before = fake_colour(light_w, kelvin, temperatures(images))
    for _ in range(4):
        one_pass(folder, images, raw, kelvin, light_w, loader, white_balance="steady")
    after = fake_colour(light_w, kelvin, temperatures(images))
    for k in (0, 75, 149):                                 # around each keyframe, the look holds
        assert abs(np.median(after[max(0, k - 10):k + 10]) - np.median(before[max(0, k - 10):k + 10])) < 0.06


def test_fixed_to_the_first_frames_white_balance(awb_sequence):
    folder, images, raw, kelvin, loader = awb_sequence
    light_w = np.zeros(N)
    for _ in range(4):
        one_pass(folder, images, raw, kelvin, light_w, loader, white_balance="first")
    after = fake_colour(light_w, kelvin, temperatures(images))
    assert wobble(after) < 0.01
    # Every frame now renders as if shot at the first frame's white balance, plus
    # only your keyframe temperatures: the camera's drift has gone.
    ideal = fake_colour(light_w, np.full(N, kelvin[0]), blend_temps(folder))
    assert np.abs(after - ideal).max() < 0.06


def blend_temps(folder):
    ledger = read_ledger(folder)
    images = sorted(folder.glob("*.CR3"))
    return np.array([float(read_sidecar(i)["adjustments"].get("temperature") or 0)
                     - ledger[i.name].get("applied_temp", 0.0) for i in images])


def test_undo_and_rebuild_handle_temperature(awb_sequence):
    folder, images, raw, kelvin, loader = awb_sequence
    light_w = np.zeros(N)
    blended = temperatures(images)
    one_pass(folder, images, raw, kelvin, light_w, loader)
    one_pass(folder, images, raw, kelvin, light_w, loader)
    refined = temperatures(images)
    assert not np.allclose(refined, blended)

    rebuild(load(folder))
    assert np.allclose(temperatures(images), refined, atol=0.02)
    undo(folder)
    assert np.allclose(temperatures(images), blended, atol=0.02)


def test_no_colour_leaves_temperature_alone(awb_sequence):
    folder, images, raw, kelvin, loader = awb_sequence
    before = temperatures(images)
    one_pass(folder, images, raw, kelvin, np.zeros(N), loader, colour=False)
    assert np.allclose(temperatures(images), before)


def test_smooth_follows_the_cameras_own_white_balance_minus_its_jitter(awb_sequence):
    """James's idea: heavily smooth the as-shot Kelvin. The camera's guessing
    goes; its overall drift (the auto look) and the light's real colour stay."""
    from timelapse_core import smooth_brightness
    folder, images, raw, kelvin, loader = awb_sequence
    light_w = np.where(np.arange(N) < 100, 0.0, 0.15)      # lamps come on: a real colour change
    before = fake_colour(light_w, kelvin, temperatures(images))
    for _ in range(4):
        one_pass(folder, images, raw, kelvin, light_w, loader)   # default: smooth
    after = fake_colour(light_w, kelvin, temperatures(images))

    mired = 1e6 / kelvin
    smoothed = smooth_brightness(mired, max(75, N // 3))
    ideal = light_w + PER_MIRED * smoothed + SLIDER * blend_temps(folder)
    assert wobble(after) < wobble(before) / 3
    assert np.abs(after - ideal).max() < 0.05              # exactly the smoothed-Kelvin look
    lamps = after - PER_MIRED * smoothed - SLIDER * blend_temps(folder)
    assert np.median(lamps[105:120]) - np.median(lamps[80:95]) == pytest.approx(0.15, abs=0.04)  # the lamps' warmth survives


def test_smooth_is_the_default_only_with_kelvin(tmp_path):
    images = make_sequence(tmp_path, 40)
    apply(plan(load(tmp_path), raw_brightness=np.zeros(40)))
    with pytest.raises(ValueError, match="needs ExifTool"):
        refine(load(tmp_path), render_brightness=np.zeros(40), raw_brightness=np.zeros(40),
               render_warmth=np.random.default_rng(1).normal(0, 0.05, 40), raw_warmth=np.zeros(40),
               white_balance="smooth")
    r = refine(load(tmp_path), render_brightness=np.zeros(40), raw_brightness=np.zeros(40),
               render_warmth=np.random.default_rng(1).normal(0, 0.05, 40), raw_warmth=np.zeros(40))
    assert not r.nothing_to_do                             # fell back to steady without Kelvin


def test_colour_only_undo_keeps_the_exposure_work(awb_sequence):
    folder, images, raw, kelvin, loader = awb_sequence
    blended_t = temperatures(images)
    one_pass(folder, images, raw, kelvin, np.zeros(N), loader, white_balance="steady")
    one_pass(folder, images, raw, kelvin, np.zeros(N), loader, white_balance="steady")
    exposure_before = exposures(images)
    undo(folder, colour_only=True)
    assert np.allclose(temperatures(images), blended_t, atol=0.02)
    assert np.allclose(exposures(images), exposure_before)
    history = (folder / ".timelapse-core" / "refine.json").read_text()
    assert "applied_temp" not in history and "render" in history


# --- found on the real sequence: colour and exposure passes must not mix ----------

def test_colour_only_pass_leaves_exposure_exactly(awb_sequence):
    folder, images, raw, kelvin, loader = awb_sequence
    light_w = np.linspace(0, 0.3, N)
    before_e, before_t = exposures(images).copy(), temperatures(images).copy()
    one_pass(folder, images, raw, kelvin, light_w, loader, white_balance="steady", adjust_exposure=False)
    assert np.array_equal(exposures(images), before_e)
    assert not np.array_equal(temperatures(images), before_t)


def test_exposure_strength_is_remembered_not_learned_from_a_colour_pass():
    # A colour pass moves Temperature, which nudges brightness; Exposure doesn't
    # move. The next exposure pass must not read those nudges as the slider's
    # strength (on the real sequence that produced 1.5-stop errors).
    from timelapse_rapidraw.refine import _slider_strength
    n = 60
    applied = np.zeros(n)
    history = [{"render": np.zeros(n).tolist(), "applied": applied.tolist(), "strength": [2.1] * n}]
    render_now = np.random.default_rng(1).normal(0, 0.02, n)          # brightness nudged by the colour pass
    s = _slider_strength(None, render_now, applied, np.zeros(n), history, None, 25)
    assert np.allclose(s, 2.1)


def test_strength_relearned_from_earlier_passes_when_not_stored():
    # History written before strengths were stored: learn from the last pair of
    # passes where Exposure really moved, not from a colour-only change.
    from timelapse_rapidraw.refine import _slider_strength
    n = 60
    a0, a1 = np.zeros(n), np.full(n, -0.2)
    history = [{"render": np.zeros(n).tolist(), "applied": a0.tolist()},
               {"render": (2.1 * (a1 - a0)).tolist(), "applied": a1.tolist()}]   # exposure moved: 2.1x
    s = _slider_strength(None, np.zeros(n), a1, np.zeros(n), history, None, 25)  # now: nothing moved
    assert np.allclose(np.nanmedian(s), 2.1, atol=0.01)
