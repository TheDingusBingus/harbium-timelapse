from datetime import datetime
from pathlib import Path

import pytest

from synthetic import scene, write_jpeg
from harbium_timelapse.exif import read_frame
from harbium_timelapse.types import Frame


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


@pytest.mark.skipif(not __import__("shutil").which("exiftool"), reason="ExifTool not installed")
def test_exiftool_reads_a_big_folder_with_a_long_awkward_path(tmp_path, monkeypatch):
    """The Windows hang: hundreds of long paths. Batched through an argument file now."""
    from datetime import timedelta
    from harbium_timelapse import exif

    # Prove ExifTool did the reading: make the fallback reader return nothing.
    monkeypatch.setattr(exif, "_read_with_exifread", lambda p: Frame(path=p))

    folder = tmp_path / "Lightroom Catalogue" / "2026" / "dusk shoot" / "timelapse photos é"
    folder.mkdir(parents=True)
    start = datetime(2026, 9, 22, 19, 0, 0)
    paths = []
    for i in range(250):
        paths.append(folder / f"181A{i:04d}.jpg")
        write_jpeg(paths[-1], scene(), taken_at=start + timedelta(seconds=5 * i))

    seen = []
    frames = exif.read_frames(paths, progress=lambda i, n: seen.append((i, n)))
    assert [f.path for f in frames] == paths
    assert all(f.shutter == pytest.approx(1 / 125) for f in frames)
    assert frames[-1].taken_at == start + timedelta(seconds=5 * 249)
    assert seen == [(100, 250), (200, 250), (250, 250)]
