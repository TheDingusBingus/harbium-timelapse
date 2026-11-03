"""The time strip: one averaged slice per frame, before and after, saved beside the output at full detail."""

import numpy as np
from PIL import Image

from harbium_timelapse.app import session
from harbium_timelapse.finish import finished_frames, render_strip, strip_column


def test_a_column_is_the_band_average_in_linear_light():
    img = np.zeros((10, 100, 3), np.uint8)
    img[:, 49:51] = 255                                    # a bright line inside the centre band
    col = strip_column(img, 0.5, band=0.1)                 # band 10 px wide: 2 of 10 columns are white
    assert col.shape == (10, 3) and np.allclose(col, 0.2, atol=1e-6)


def test_the_strip_has_a_stripe_per_frame_and_before_over_after():
    cols = [np.full((40, 3), v, np.float32) for v in np.linspace(0.1, 0.9, 50)]
    img = render_strip(cols, cols, stripe=4)
    assert img.shape == (28 + 40 + 28 + 40, 200, 3)
    assert render_strip(cols, cols, stripe=4, height=10).shape[0] == 28 + 10 + 28 + 10


def flickering(folder, n=24):
    folder.mkdir()
    rng = np.random.default_rng(5)
    base = np.linspace(0.1, 0.8, 120)[None, :, None] * np.ones((80, 1, 3))
    for i in range(n):
        img = np.clip(base * 2 ** (0.3 * (i % 2) - 0.15 + rng.normal(0, 0.01)), 0, 1)   # strong frame-to-frame flicker
        Image.fromarray((img ** (1 / 2.2) * 255).astype(np.uint8)).save(folder / f"f{i:02d}.png")


def test_the_time_slice_is_saved_beside_the_frames_and_the_flicker_goes(tmp_path):
    flickering(tmp_path / "e")
    done = session.write_export(session.plan_export(tmp_path / "e"), out=tmp_path / "out")
    assert done.strip == tmp_path / "out.timelapse-slice.jpg" and done.strip.exists()
    assert len(finished_frames(tmp_path / "out")) == 24                 # never taken for a frame
    before = np.array([c.mean() for c in done.strip_columns["before"]])
    after = np.array([c.mean() for c in done.strip_columns["after"]])
    jump = lambda v: np.abs(np.diff(np.log2(v))).mean()                  # noqa: E731
    assert jump(after) < jump(before) / 4                                # the diagnostic strip agrees
    img = Image.open(done.strip)
    assert img.size == (120, 2 * 80 + 2 * 28)                            # full size: the whole frame, twice


def test_a_strip_sitting_in_a_frames_folder_is_ignored(tmp_path):
    flickering(tmp_path / "e", n=5)
    Image.new("RGB", (8, 8)).save(tmp_path / "e" / "old.timelapse-strip.png")
    Image.new("RGB", (8, 8)).save(tmp_path / "e" / "old.timelapse-slice.png")
    assert len(finished_frames(tmp_path / "e")) == 5
