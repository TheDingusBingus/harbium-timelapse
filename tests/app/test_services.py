"""App services: recent projects, project state, background jobs, and the operations screens call."""

import os
import shutil
import threading
import time

import numpy as np
import pytest
from PIL import Image

from harbium_timelapse.app import config, session
from harbium_timelapse.app.jobs import Done, Failed, JobRunner, Stopped
from harbium_timelapse.app.project import Project, load_project
from harbium_timelapse.progress import Progress


# --- recent projects and preferences --------------------------------------------------

def test_recent_projects_newest_first_without_duplicates_or_missing_folders(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    config.remember_project(a, 10, "opened")
    config.remember_project(b, 20, "prepared")
    config.remember_project(a, 10, "finished")
    names = [os.path.basename(r["folder"]) for r in config.recent_projects()]
    assert names == ["a", "b"] and config.recent_projects()[0]["stage"] == "finished"
    shutil.rmtree(b)
    assert [os.path.basename(r["folder"]) for r in config.recent_projects()] == ["a"]


def test_preferences_round_trip():
    config.save_preferences({"theme": "dark"})
    assert config.preferences() == {"theme": "dark"}


# --- project state -------------------------------------------------------------------

def test_project_state_survives_reopening(tmp_path):
    p = Project(tmp_path)
    p.reach("prepared")
    p.settings["window"] = 31
    p.dismissed.append("181A1700.jpg")
    p.save()
    q = load_project(tmp_path)
    assert (q.stage, q.settings["window"], q.dismissed) == ("prepared", 31, ["181A1700.jpg"])


def test_stages_only_move_forward_unless_starting_over(tmp_path):
    p = Project(tmp_path)
    p.reach("finished")
    p.reach("prepared")
    assert p.stage == "finished"
    p.start_over()
    assert p.stage == "opened" and p.export is None


def test_a_damaged_or_newer_project_file_is_handled(tmp_path):
    state = tmp_path / ".timelapse-core"
    state.mkdir()
    (state / "project.json").write_text("{broken")
    assert "damaged" in load_project(tmp_path).note
    (state / "project.json").write_text('{"version": 99}')
    with pytest.raises(ValueError, match="newer version"):
        load_project(tmp_path)


# --- background jobs ------------------------------------------------------------------

def collect():
    events, done = [], threading.Event()
    def on(e):
        events.append(e)
        if not isinstance(e, Progress):
            done.set()
    return events, done, on


def test_a_job_reports_progress_and_its_result():
    events, done, on = collect()
    def work(tracker):
        hook = tracker.stage("Measuring")
        for i in range(1, 501):
            hook(i, 500)
        return "result"
    JobRunner().start("measure", work, on)
    assert done.wait(5)
    progress = [e for e in events if isinstance(e, Progress)]
    assert progress[-1] == Progress("Measuring", 500, 500) and len(progress) < 50      # thinned, last kept
    assert events[-1] == Done("result")


def test_cancel_stops_a_job_at_its_next_step():
    events, done, on = collect()
    started = threading.Event()
    def work(tracker):
        hook = tracker.stage("Writing")
        for i in range(10_000):
            started.set()
            time.sleep(0.001)
            hook(i, 10_000)
    handle = JobRunner().start("write", work, on)
    started.wait(2)
    handle.cancel()
    assert done.wait(5) and events[-1] == Stopped()


def test_failures_are_explained_in_plain_words():
    events, done, on = collect()
    JobRunner().start("x", lambda t: (_ for _ in ()).throw(ValueError("The folder has no frames.")), on)
    assert done.wait(5) and events[-1] == Failed("The folder has no frames.")
    events2, done2, on2 = collect()
    JobRunner().start("y", lambda t: {}["missing"], on2)
    assert done2.wait(5) and "didn't expect" in events2[-1].message


# --- noticing the export ------------------------------------------------------------------

def frames_in(folder, n, when=None):
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        f = folder / f"f{i:03d}.jpg"
        f.write_bytes(b"x")
        if when is not None:
            os.utime(f, (when, when))


def test_export_is_noticed_counted_and_called_complete(tmp_path):
    raw = tmp_path / "timelapse photos"
    frames_in(raw, 3)                                   # the RAW folder itself never counts
    since = time.time() - 1
    assert session.export_status(raw, 10, since).state == "waiting"
    frames_in(tmp_path / "old-export", 10, when=since - 100)       # made before: ignored
    assert session.export_status(raw, 10, since).state == "waiting"
    frames_in(tmp_path / "final-export", 6)
    s = session.export_status(raw, 10, since)
    assert (s.state, s.folder.name, s.frames) == ("arriving", "final-export", 6)
    frames_in(tmp_path / "final-export", 10)
    assert session.export_status(raw, 10, since).state == "arriving"              # still changing
    s = session.export_status(raw, 10, since, now=time.time() + session.QUIET + 1)
    assert (s.state, s.frames) == ("complete", 10)


# --- finishing through the shared service ---------------------------------------------------

def sky_frames(folder, n=24):
    folder.mkdir()
    rng = np.random.default_rng(2)
    base = np.linspace(40, 200, 96)[None, :, None] * np.ones((64, 1, 3))
    for i in range(n):
        img = np.clip(base * 2 ** rng.normal(0, 0.06) + rng.normal(0, 2, base.shape), 0, 255)
        Image.fromarray(img.astype(np.uint8)).save(folder / f"f{i:02d}.png")


def test_plan_then_write_frames_with_review_and_report(tmp_path):
    sky_frames(tmp_path / "e")
    planned = session.plan_export(tmp_path / "e")
    assert planned.moving_camera is None and planned.plan.brightness_after < planned.plan.brightness_before
    done = session.write_export(planned, out=tmp_path / "out")
    assert len(done.written) == 24 and done.report == tmp_path / "out" / "timelapse-report.json"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_a_video_only_finish_saves_its_report(tmp_path):
    """Regression: video-only runs used to fail to save their report, silently."""
    sky_frames(tmp_path / "e")
    done = session.write_export(session.plan_export(tmp_path / "e"), video=tmp_path / "film.mp4")
    assert done.report == tmp_path / "film.timelapse-report.json" and done.report.exists()
