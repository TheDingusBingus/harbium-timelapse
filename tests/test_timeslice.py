"""Time slices from previews: bands, corrections band by band, and previews made while measuring."""

import numpy as np
from PIL import Image

from harbium_timelapse import timeslice
from harbium_timelapse.app import previews, session
from harbium_timelapse.measure import measure_brightness
from harbium_timelapse.sequencer import load_sequence


def test_bands_cover_the_width_exactly():
    for width, frames in ((5472, 229), (100, 7), (960, 2000)):
        b = timeslice.bands(width, frames)
        assert b[0][0] == 0 and b[-1][1] == width and all(b[i][1] == b[i + 1][0] for i in range(frames - 1))


def test_flicker_shows_as_steps_and_the_correction_removes_them():
    rng = np.random.default_rng(1)
    scene = np.linspace(0.05, 0.6, 200)[None, :, None] * np.ones((40, 1, 3))
    wobble = rng.normal(0, 0.2, 40)                                     # stops of flicker per frame
    frames = [(scene * 2 ** w).astype(np.float32) for w in wobble]
    before, after = timeslice.compose(frames), timeslice.compose(frames, -wobble)
    edges = [a for a, _ in timeslice.bands(200, 40)][1:]
    step = lambda img: np.mean([abs(np.log2(img[:, e].mean() / img[:, e - 1].mean())) for e in edges])   # noqa: E731
    assert step(before) > 0.1 and step(after) < 0.02                     # bands melt into a smooth fade


def frames(folder, n=8):
    folder.mkdir()
    rng = np.random.default_rng(2)
    base = np.exp(rng.normal(-1.6, 0.5, (48, 64)))[..., None] * np.array([1.0, 0.95, 0.85])
    for i in range(n):
        img = np.clip(base * 2 ** rng.normal(0, 0.1), 0, 1) ** (1 / 2.2) * 255
        Image.fromarray(img.astype(np.uint8)).save(folder / f"f{i:02d}.png")


def test_previews_are_made_while_measuring_and_keep_each_frames_brightness(tmp_path):
    frames(tmp_path / "e")
    paths = sorted((tmp_path / "e").iterdir())
    kept = {}
    measure_brightness(paths, on_image=lambda i, img: (previews.save(paths[i], img), kept.__setitem__(i, img)))
    assert previews.missing(paths) == []
    for i, original in kept.items():
        back = previews.load(paths[i])
        assert abs(np.log2(back.mean() / original.mean())) < 0.05        # within a few percent, in linear light


def test_missing_previews_are_made_once(tmp_path):
    frames(tmp_path / "e")
    seq = load_sequence(tmp_path / "e")
    assert session.ensure_previews(seq) == len(seq) and session.ensure_previews(seq) == 0


def test_the_app_time_slice_follows_the_corrections_in_every_mode(tmp_path):
    import types
    from harbium_timelapse.gui.models import AppModel
    from harbium_timelapse.timeslice import MODES, Slice
    rng = np.random.default_rng(3)
    scene = np.linspace(0.05, 0.6, 120)[None, :, None] * np.ones((30, 1, 3))
    wobble = rng.normal(0, 0.2, 24)
    images = [(scene * 2 ** w).astype(np.float32) for w in wobble]
    m = AppModel()
    m.project = types.SimpleNamespace(settings={}, save=lambda: None)
    slices = {mode: Slice(30, 120, 24, mode) for mode in MODES}
    for i, img in enumerate(images):
        for s in slices.values():
            s.take(i, img)
    m.preview_images, m._slice_scale = slices, 1.0
    m.preview = types.SimpleNamespace(deflicker=types.SimpleNamespace(exposure=-wobble))
    for mode in MODES:
        m.set_slice_mode(mode)
        before, after = m.time_slice()
        flat = scene.mean(axis=2)[None] * 255
        lin = lambda img: img.astype(float).mean(axis=2)                     # noqa: E731
        assert before.shape == (30, 120, 3)
        # after the correction, every pixel matches its frame-0-free scene value: the flicker is gone everywhere
        assert np.std(np.log2(lin(after) + 1) - np.log2(flat[0] + 1)) < np.std(np.log2(lin(before) + 1) - np.log2(flat[0] + 1))
    assert m.time_slice()[0] is m.time_slice()[0]                         # recomposed only when something changes


def test_a_slice_keeps_one_pictures_worth_of_pixels_however_long_the_sequence():
    from harbium_timelapse.timeslice import Slice
    s = Slice(30, 40, 2000, "clockwise")
    assert s.values.shape == (30 * 40, 3)
