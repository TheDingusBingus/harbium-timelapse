from datetime import datetime
from pathlib import Path

import pytest

from synthetic import scene, write_jpeg
from timelapse_core.exif import read_frame
from timelapse_core.types import Frame


def test_reads_settings_from_jpeg(tmp_path):
    path = tmp_path / "IMG_0001.jpg"
    write_jpeg(path, scene(), shutter=(1, 125), aperture=(56, 10), iso=400,
               taken_at=datetime(2026, 9, 1, 19, 30, 5), model="Canon EOS R6")
    frame = read_frame(path)
    assert frame.shutter == pytest.approx(1 / 125, rel=1e-3)
    assert frame.aperture == pytest.approx(5.6)
    assert frame.iso == 400
    assert frame.taken_at == datetime(2026, 9, 1, 19, 30, 5)
    assert frame.camera == "Canon EOS R6"


def test_unreadable_file_gives_unknown_settings_not_a_crash(tmp_path):
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"not a photo")
    assert read_frame(path).exposure_value is None


def test_exposure_value():
    def ev(**kw):
        return Frame(path=Path("x"), **kw).exposure_value

    # f/8 at 1/125, ISO 100 is EV ~12.97
    assert ev(shutter=1 / 125, aperture=8, iso=100) == pytest.approx(12.966, abs=1e-3)
    # doubling ISO is one stop more light, so EV one lower
    assert ev(shutter=1 / 125, aperture=8, iso=100) - ev(shutter=1 / 125, aperture=8, iso=200) == pytest.approx(1.0)
    assert ev(shutter=None, aperture=8, iso=100) is None
