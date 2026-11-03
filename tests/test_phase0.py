"""Phase 0: the groundwork the app stands on (review, report, cancelling, motion, fixes, hand-off)."""

import json
import shutil

import numpy as np
import pytest
from PIL import Image

from harbium_timelapse import cli, report
from harbium_timelapse.finish import Meter, apply_finish, measure_channels, plan_finish
from harbium_timelapse.progress import CancelToken, Cancelled, Tracker
from harbium_timelapse.review import EDGE, moving_camera_note, spikes
from harbium_timelapse.types import Problem

N = 60
DUSK = 0.4 - 2.6 / (1 + np.exp(-(np.arange(N) - 30) / 4.0))        # steep nightfall: up to ~0.16 stops a frame


# --- the spike check -------------------------------------------------------------

def test_a_spike_on_the_steepest_part_of_nightfall_is_caught_alone():
    y = DUSK.copy()
    y[30] -= 0.05
    found = spikes(y)
    assert [f.frame for f in found] == [30]                 # the spike, not its neighbours
    assert "darker" in found[0].note


def test_a_steep_ramp_alone_is_never_flagged():
    assert spikes(DUSK) == []


def test_the_ends_cannot_be_judged():
    y = DUSK.copy()
    y[1] += 0.2
    y[-1] -= 0.2
    assert spikes(y) == []
    assert EDGE == 2


def test_unmeasurable_frames_are_skipped_not_flagged():
    y = DUSK.copy()
    y[20] = np.nan
    y[40] += 0.06
    assert [f.frame for f in spikes(y)] == [40]


# --- on real frames: walkers don't count, flicker does -----------------------------

def frames(folder, spike_at=None, walkers=True, step=0):
    folder.mkdir()
    rng = np.random.default_rng(1)
    h, w = 120, 180
    base = np.exp(rng.normal(-1.6, 0.5, (h, w)))[..., None] * np.array([1.0, 0.95, 0.85])
    paths = []
    for i in range(N):
        img = base * 2 ** (DUSK[i] * 0.3)
        if spike_at == i:
            img = img * 2 ** 0.12
        if walkers:
            x = (i * 7) % (w - 20)
            img[60:110, x:x + 14] *= 0.15                   # a dark figure crossing the frame
        img = np.roll(img, i * step, axis=1)
        from harbium_timelapse.finish import _linear_to_srgb
        p = folder / f"f{i:02d}.png"
        Image.fromarray(np.round(_linear_to_srgb(np.clip(img, 0, 1)) * 255).astype(np.uint8)).save(p)
        paths.append(p)
    return paths


def test_review_flags_flicker_and_ignores_walkers(tmp_path, capsys):
    frames(tmp_path / "e", spike_at=37)
    assert cli.main(["review", str(tmp_path / "e"), "--json", str(tmp_path / "found.json")]) == 0
    out = capsys.readouterr().out
    found = json.loads((tmp_path / "found.json").read_text())
    assert [f["name"] for f in found if f["kind"] == "brightness"] == ["f37.png"]
    assert "Worth a look" in out and "f37.png" in out


def test_a_clean_sequence_with_walkers_comes_back_clean(tmp_path, capsys):
    frames(tmp_path / "e")
    assert cli.main(["review", str(tmp_path / "e")]) == 0
    assert "Nothing stands out" in capsys.readouterr().out


def test_a_moving_camera_is_noticed(tmp_path):
    paths = frames(tmp_path / "e", walkers=False, step=6)
    meter = Meter(motion=True)
    measure_channels(paths, meter=meter)
    assert moving_camera_note(meter.motion()) is not None
    still = Meter(motion=True)
    measure_channels(frames(tmp_path / "s", walkers=False), meter=still)
    assert moving_camera_note(still.motion()) is None


# --- finish: in-memory check, report, cancelling --------------------------------------

def test_the_in_memory_check_matches_reading_the_files_back(tmp_path):
    paths = frames(tmp_path / "e")
    plan = plan_finish(paths)
    meter = Meter()
    written = apply_finish(plan, out=tmp_path / "out", meter=meter)
    assert np.allclose(meter.series(), measure_channels(written), atol=1e-9, equal_nan=True)


def test_finish_writes_a_report_that_reads_back(tmp_path, capsys):
    frames(tmp_path / "e", spike_at=37)
    assert cli.main(["finish", str(tmp_path / "e"), "--out", str(tmp_path / "out")]) == 0
    data = report.read(tmp_path / "out")
    assert data["schema"] == "harbium-timelapse/report" and data["step"] == "finish"
    assert len(data["frames"]) == N and data["frames"][0]["output"] == "f00.png"
    assert data["source"] == "../e"                              # relative, never absolute
    assert data["cant_judge"] == [0, 1, N - 2, N - 1]
    assert "Report:" in capsys.readouterr().out


def test_reports_from_newer_versions_or_other_tools_are_refused(tmp_path):
    (tmp_path / report.NAME).write_text(json.dumps({"schema": report.SCHEMA, "version": 99}))
    with pytest.raises(ValueError, match="newer version"):
        report.read(tmp_path)
    (tmp_path / report.NAME).write_text(json.dumps({"schema": "something-else", "version": 1}))
    with pytest.raises(ValueError, match="isn't a harbium-timelapse run report"):
        report.read(tmp_path)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_cancelling_stops_writing_and_never_leaves_half_a_video(tmp_path):
    paths = frames(tmp_path / "e")
    plan = plan_finish(paths)
    video = tmp_path / "film.mp4"
    video.write_bytes(b"the previous film")                      # must survive a stopped run
    token = CancelToken()
    tracker = Tracker(token=token)
    hook = tracker.stage("Writing")

    def progress(done, total):
        if done == 20:
            token.cancel()
        hook(done, total)
    with pytest.raises(Cancelled):
        apply_finish(plan, video=video, progress=progress)
    assert video.read_bytes() == b"the previous film"
    assert not list(tmp_path.glob("*.partial*"))


def test_progress_events_are_named_and_counted():
    seen = []
    t = Tracker(seen.append)
    hook = t.stage("Measuring")
    hook(1, 3)
    hook(3, 3)
    assert [(e.stage, e.done, e.total) for e in seen] == [("Measuring", 1, 3), ("Measuring", 3, 3)]


# --- fixes and hand-off steps -------------------------------------------------------------

def test_problems_carry_a_fix_unless_their_message_already_says():
    assert "own folder" in Problem("mixed_cameras", "Frames from two cameras.").fix
    assert Problem("gap", "...", fix="Custom advice.").fix == "Custom advice."
    assert Problem("mixed_files", "... Move one type to its own folder.").fix == ""


def test_editors_give_their_own_handoff_steps():
    from harbium_timelapse.editors.lightroom import Lightroom
    from harbium_timelapse.editors.rapidraw.adapter import RapidRAW
    assert any("Dehaze" in s for s in RapidRAW().handoff_steps())
    assert any("Read Metadata from Files" in s for s in Lightroom().handoff_steps())


def test_a_plain_sky_never_raises_a_false_moving_camera_warning(tmp_path):
    """Regression: featureless frames (plain sky, fog) used to look like a moving camera."""
    folder = tmp_path / "sky"
    folder.mkdir()
    rng = np.random.default_rng(2)
    base = np.linspace(40, 200, 180)[None, :, None] * np.ones((120, 1, 3))
    paths = []
    for i in range(30):
        p = folder / f"f{i:02d}.png"
        Image.fromarray(np.clip(base + rng.normal(0, 2, base.shape), 0, 255).astype(np.uint8)).save(p)
        paths.append(p)
    meter = Meter(motion=True)
    measure_channels(paths, meter=meter)
    assert moving_camera_note(meter.motion()) is None
