"""Camera settings are remembered between runs, but never trusted once a file changes."""

import os

import harbium_timelapse.exif as exif
from harbium_timelapse.types import Frame


def fake_exiftool(monkeypatch):
    reads = []
    def read(paths, progress):
        reads.append([p.name for p in paths])
        return {exif._key(p): Frame(path=p, iso=100.0, camera="Canon EOS R6", white_balance=5600.0,
                                    white_balance_neutral=(0.5, 1.0, 0.7)) for p in paths}
    monkeypatch.setattr(exif, "exiftool_available", lambda: True)
    monkeypatch.setattr(exif, "_read_with_exiftool", read)
    return reads


def files(tmp_path, n=5):
    paths = []
    for i in range(n):
        p = tmp_path / f"IMG_{i}.CR3"
        p.write_bytes(b"raw" * (i + 1))
        paths.append(p)
    return paths


def test_second_read_comes_from_the_cache(tmp_path, monkeypatch):
    reads = fake_exiftool(monkeypatch)
    paths = files(tmp_path)
    first = exif.read_frames(paths)
    second = exif.read_frames(paths)
    assert reads == [[p.name for p in paths]]                 # ExifTool ran once
    assert [f.camera for f in second] == ["Canon EOS R6"] * 5
    assert second[0].white_balance_neutral == (0.5, 1.0, 0.7) and second[0].path == paths[0]
    assert first == second


def test_a_changed_file_is_read_again(tmp_path, monkeypatch):
    reads = fake_exiftool(monkeypatch)
    paths = files(tmp_path)
    exif.read_frames(paths)
    paths[2].write_bytes(b"edited, different size")
    exif.read_frames(paths)
    assert reads[1] == [paths[2].name]


def test_a_damaged_cache_is_ignored(tmp_path, monkeypatch):
    reads = fake_exiftool(monkeypatch)
    paths = files(tmp_path)
    exif.read_frames(paths)
    for f in (exif._cache_dir()).glob("*.json"):
        f.write_text("{broken")
    assert len(exif.read_frames(paths)) == 5 and len(reads) == 2


def test_cache_can_be_turned_off(tmp_path, monkeypatch):
    reads = fake_exiftool(monkeypatch)
    monkeypatch.setenv("HARBIUM_NO_CACHE", "1")
    paths = files(tmp_path)
    exif.read_frames(paths)
    exif.read_frames(paths)
    assert len(reads) == 2


def test_nothing_is_written_into_the_photo_folder(tmp_path, monkeypatch):
    fake_exiftool(monkeypatch)
    paths = files(tmp_path)
    before = sorted(os.listdir(tmp_path))
    exif.read_frames(paths)
    assert sorted(os.listdir(tmp_path)) == before
