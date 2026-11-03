"""The operations the app's screens call, each one plain Python.

Opening a folder, measuring once and previewing as often as needed, applying
and undoing, noticing the export, and finishing. The command line uses the same
finishing steps, so the two can't drift apart.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .. import report as run_report
from ..editors import detect
from ..editors.base import Editor, PrepareReport
from ..finish import (LUMA, FinishPlan, Meter, _wobble, apply_finish, check_video_target, finished_frames,
                      measure_channels, plan_finish)
from ..measure import measure_brightness
from ..progress import Tracker
from ..review import Finding, moving_camera_note
from ..review import review as review_frames
from ..sequencer import check_sequence, load_sequence
from ..types import Problem, Sequence

FRAME_SUFFIXES = (".jpg", ".jpeg", ".png", ".tif", ".tiff")
QUIET = 3.0          # seconds with no new or growing files before an export counts as finished


# --- opening a folder -------------------------------------------------------------------

@dataclass
class Opened:
    seq: Sequence
    editor: Editor | None
    problems: list[Problem]
    other_editors: list[str] = field(default_factory=list)

    @property
    def camera(self) -> str:
        cams = sorted({f.camera for f in self.seq if f.camera})
        return ", ".join(cams) or "unknown camera"

    @property
    def span(self) -> str:
        times = [f.taken_at for f in self.seq if f.taken_at]
        return f"{min(times):%H:%M} to {max(times):%H:%M}" if times else "times unknown"

    @property
    def blocking(self) -> list[Problem]:
        return [p for p in self.problems if p.serious]


def open_folder(folder: Path, tracker: Tracker | None = None) -> Opened:
    folder = Path(folder)
    editors = detect(folder)
    editor = editors[0] if editors else None
    reading = tracker.stage("Reading camera settings") if tracker else None
    seq = editor.load(folder, progress=reading) if editor else load_sequence(folder, progress=reading)
    problems = editor.problems(seq) if editor and hasattr(editor, "problems") else check_sequence(seq)
    return Opened(seq, editor, problems, [e.label for e in editors[1:]])


# --- prepare: measure once, preview many times, apply ------------------------------------

def measure(seq: Sequence, tracker: Tracker | None = None) -> np.ndarray:
    """Brightness of every RAW frame. The slow part: done once, then previews are instant."""
    return measure_brightness(seq, progress=tracker.stage("Measuring") if tracker else None)


def preview(editor: Editor, seq: Sequence, measured: np.ndarray, window: int = 25, strength: float = 1.0,
            white_balance: bool = True) -> PrepareReport:
    """What prepare would do, without writing anything."""
    return editor.prepare(seq, window=window, strength=strength, white_balance=white_balance,
                          dry_run=True, raw_brightness=measured)


def apply(editor: Editor, seq: Sequence, measured: np.ndarray, window: int = 25, strength: float = 1.0,
          white_balance: bool = True, tracker: Tracker | None = None) -> PrepareReport:
    if tracker:
        tracker.token.check()
    if editor.is_running():
        raise ValueError(f"{editor.label} is open. Close it, then try again: it could overwrite the corrections.")
    return editor.prepare(seq, window=window, strength=strength, white_balance=white_balance,
                          raw_brightness=measured, progress=tracker.stage("Writing") if tracker else None)


def undo(editor: Editor, seq: Sequence) -> int:
    if editor.is_running():
        raise ValueError(f"{editor.label} is open. Close it, then try again.")
    return editor.undo(seq)


# --- noticing the export ------------------------------------------------------------------

@dataclass(frozen=True)
class ExportStatus:
    state: str                       # "waiting", "arriving" or "complete"
    folder: Path | None = None
    frames: int = 0


def export_status(raw_folder: Path, expected: int, since: float, now: float | None = None) -> ExportStatus:
    """Look for a folder of exported frames near the RAW folder, made after ``since``.

    Candidates are folders inside the RAW folder and beside it. The one with the
    most new frames wins; it's complete when it holds at least ``expected``
    frames and nothing in it has changed for ``QUIET`` seconds."""
    raw_folder = Path(raw_folder).resolve()
    now = time.time() if now is None else now
    candidates = []
    for parent in (raw_folder, raw_folder.parent):
        try:
            for d in parent.iterdir():
                if d.is_dir() and d.resolve() != raw_folder and not d.name.startswith("."):
                    candidates.append(d)
        except OSError:
            continue
    best, best_count, newest = None, 0, 0.0
    for d in candidates:
        try:
            files = [f for f in d.iterdir() if f.suffix.lower() in FRAME_SUFFIXES]
            stamps = [f.stat().st_mtime for f in files]
        except OSError:
            continue
        fresh = [m for m in stamps if m >= since]
        if len(fresh) > best_count:
            best, best_count, newest = d, len(fresh), max(fresh)
    if best is None:
        return ExportStatus("waiting")
    if best_count >= expected and now - newest >= QUIET:
        return ExportStatus("complete", best, best_count)
    return ExportStatus("arriving", best, best_count)


# --- finishing ------------------------------------------------------------------------------

@dataclass
class Planned:
    frames: list[Path]
    plan: FinishPlan
    motion: float

    @property
    def moving_camera(self) -> str | None:
        return moving_camera_note(self.motion)


@dataclass
class Finished:
    written: list[Path]
    check: np.ndarray
    findings: list[Finding]
    report: Path | None
    video: Path | None

    @property
    def brightness_after(self) -> float:
        return _wobble(self.check @ LUMA)

    @property
    def colour_after(self) -> float:
        return _wobble(self.check[:, 0] - self.check[:, 2])


def plan_export(folder: Path, window: int = 25, colour: bool = True, tracker: Tracker | None = None) -> Planned:
    """Measure the exported frames and plan their corrections. Writes nothing."""
    frames = finished_frames(Path(folder).expanduser())
    meter = Meter(motion=True)
    measured = measure_channels(frames, tracker.stage("Measuring") if tracker else None, meter=meter)
    return Planned(frames, plan_finish(frames, window=window, colour=colour, measured=measured), meter.motion())


def write_export(planned: Planned, out: Path | None = None, video: Path | None = None, fps: float = 24,
                 width: int | None = None, codec: str = "h264", dither: bool = True, fmt: str | None = None,
                 settings: dict | None = None, tracker: Tracker | None = None) -> Finished:
    """Write corrected frames and/or the video, check them, review them, and save the run report."""
    if video is not None:
        check_video_target(video, codec)
    after = Meter()
    written = apply_finish(planned.plan, out=out, video=video, fps=fps, width=width,
                           progress=tracker.stage("Writing") if tracker else None, fmt=fmt, codec=codec,
                           dither=dither, meter=after)
    check = after.series()
    findings = review_frames(check, [f.name for f in planned.frames])
    where = Path(out) if out else Path(video).with_name(Path(video).stem + ".timelapse-report.json")
    settings = {"measure": "regional-vote-1", "codec": codec if video else None, "dither": dither,
                "fps": fps, "width": width, **(settings or {})}
    try:
        saved = run_report.write(run_report.finish_report(planned.plan, written if out else None, settings, check,
                                                          findings, planned.motion,
                                                          where=Path(out) if out else Path(video).parent), where)
    except OSError:
        saved = None
    return Finished(written, check, findings, saved, Path(video) if video else None)
