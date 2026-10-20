from datetime import datetime, timedelta

from synthetic import make_folder, scene, write_jpeg
from harbium_timelapse import check_sequence, format_stops, load_sequence


def kinds(problems):
    return [p.kind for p in problems]


def test_orders_by_capture_time_across_counter_rollover(tmp_path):
    t = datetime(2026, 9, 1, 19, 0)
    names = ["IMG_9998.jpg", "IMG_9999.jpg", "IMG_0001.jpg", "IMG_0002.jpg"]
    for i, name in enumerate(names):
        write_jpeg(tmp_path / name, scene(), taken_at=t + timedelta(seconds=5 * i))
    assert [f.name for f in load_sequence(tmp_path)] == names


def test_skips_macos_hidden_copies(tmp_path):
    make_folder(tmp_path, [0, 0, 0], fmt="jpg")
    (tmp_path / "._IMG_0001.jpg").write_bytes(b"\x00\x05")
    assert len(load_sequence(tmp_path)) == 3


def test_clean_sequence_has_no_problems(tmp_path):
    make_folder(tmp_path, [0] * 10, fmt="jpg")
    assert check_sequence(load_sequence(tmp_path)) == []


def test_finds_missing_frames(tmp_path):
    paths = make_folder(tmp_path, [0] * 10, fmt="jpg")
    paths[4].unlink()
    paths[5].unlink()
    problems = check_sequence(load_sequence(tmp_path))
    assert kinds(problems) == ["gap"]
    assert "About 2 frames may be missing" in problems[0].message
    assert "IMG_0004.jpg" in problems[0].message


def test_spots_exposure_setting_change(tmp_path):
    t = datetime(2026, 9, 1, 19, 0)
    for i in range(4):
        shutter = (1, 100) if i < 2 else (1, 80)
        write_jpeg(tmp_path / f"IMG_{i:04d}.jpg", scene(), shutter=shutter,
                   taken_at=t + timedelta(seconds=5 * i))
    problems = check_sequence(load_sequence(tmp_path))
    assert kinds(problems) == ["settings_change"]
    assert "1/100 → 1/80" in problems[0].message
    assert "1/3 stop brighter" in problems[0].message
    assert problems[0].frame == 2


def test_flags_raw_plus_jpeg_folder_as_serious(tmp_path):
    make_folder(tmp_path, [0, 0, 0], fmt="jpg")
    (tmp_path / "IMG_0001.cr2").write_bytes(b"fake")
    problems = check_sequence(load_sequence(tmp_path))
    assert any(p.kind == "mixed_files" and p.serious for p in problems)


def test_empty_folder_says_so(tmp_path):
    import pytest
    with pytest.raises(ValueError, match="No photos found"):
        load_sequence(tmp_path)


def test_format_stops():
    assert format_stops(1 / 3) == "1/3 stop"
    assert format_stops(-1.0) == "1 stop"
    assert format_stops(1.667) == "1 2/3 stops"
    assert format_stops(2) == "2 stops"
