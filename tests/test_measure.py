import numpy as np
import pytest

from synthetic import make_folder, scene
from timelapse_core import measure_brightness
from timelapse_core.measure import brightness_of


def test_one_stop_measures_as_one_stop():
    base = scene()
    assert brightness_of(base * 2) - brightness_of(base) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.parametrize("fmt, tolerance", [("png", 0.002), ("jpg", 0.03)])
def test_measures_known_shifts_from_files(tmp_path, fmt, tolerance):
    truth = np.array([0.0, 0.1, -0.2, 0.33, -1.0, -2.0])
    measured = measure_brightness(make_folder(tmp_path, truth, fmt=fmt))
    assert np.abs((measured - measured[0]) - truth).max() < tolerance


def test_blown_sky_does_not_drag_the_reading(tmp_path):
    truth = np.array([0.0, 0.15, -0.1, 0.3, -0.25])
    measured = measure_brightness(make_folder(tmp_path, truth, blown_sky=0.3))
    assert np.abs((measured - measured[0]) - truth).max() < 0.03


def test_rejects_unknown_speed(tmp_path):
    with pytest.raises(ValueError):
        measure_brightness(make_folder(tmp_path, [0, 0]), speed="quickly")


def test_sky_clipping_on_and_off_through_a_sunset_is_measured_exactly(tmp_path):
    """The bright end clips a sky, the dark end doesn't. Must still be exact."""
    from synthetic import write_png16
    base = scene()
    base[:20] = 0.7  # sky: blown in the brighter frames only
    truth = np.array([0.8, 0.6, 0.4, 0.1, -0.3, -1.0, -2.5])
    paths = []
    for i, t in enumerate(truth):
        paths.append(tmp_path / f"IMG_{i:04d}.png")
        write_png16(paths[-1], base * 2**t)
    measured = measure_brightness(paths)
    assert np.abs((measured - measured[-1]) - (truth - truth[-1])).max() < 0.002
