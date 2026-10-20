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
    assert len(seen) >= 2 and seen[-1] == (250, 250)                  # updates along the way, then complete
    assert [i for i, _ in seen] == sorted(i for i, _ in seen)           # only ever rising


def test_fast_reads_and_a_normal_reread_for_raw_files_missing_settings(tmp_path, monkeypatch):
    """ExifTool -fast is used first; a RAW file whose fast read lacks essentials is read again normally."""
    from harbium_timelapse import exif
    calls = []
    good = {"Model": "Canon EOS R6", "ISO": 400, "ExposureTime": 0.5, "DateTimeOriginal": "2026:10:01 18:00:00",
            "WB_RGGBLevelsAsShot": "2000 1024 1024 1500"}
    def fake(batch, fast=False):
        calls.append((fast, sorted(p.name for p in batch)))
        out = []
        for p in batch:
            rec = {"SourceFile": str(p), **good}
            if fast and p.name == "B.CR3":
                rec.pop("WB_RGGBLevelsAsShot")              # pretend -fast missed it for this file
            if fast and p.name == "c.jpg":
                rec = {"SourceFile": str(p), "Model": "export"}     # an export: not RAW, never re-read
            out.append(rec)
        return out
    monkeypatch.setattr(exif, "_exiftool_batch", fake)
    paths = [tmp_path / n for n in ("A.CR3", "B.CR3", "c.jpg")]
    for p in paths:
        p.write_bytes(b"x")
    frames = exif._read_with_exiftool(paths, None)
    assert all(fast for fast, _ in calls[:-1]) and calls[-1] == (False, ["B.CR3"])
    assert frames[exif._key(paths[1].resolve())].white_balance_neutral is not None


def test_one_exiftool_at_a_time_when_asked(tmp_path, monkeypatch):
    from harbium_timelapse import exif
    monkeypatch.setenv("HARBIUM_WORKERS", "1")
    seen = []
    monkeypatch.setattr(exif, "_exiftool_batch",
                        lambda batch, fast=False: seen.append(len(batch)) or [{"SourceFile": str(p)} for p in batch])
    paths = [tmp_path / f"f{i}.jpg" for i in range(10)]
    for p in paths:
        p.write_bytes(b"x")
    exif._read_with_exiftool(paths, None)
    assert seen == [10]                                       # one process, one batch
