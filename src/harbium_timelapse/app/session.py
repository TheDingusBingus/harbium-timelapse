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

from .. import measure_cache
from . import previews
from .. import report as run_report
from ..editors import detect
from ..editors.base import Editor, PrepareReport
from ..finish import (JPEG_QUALITY, LUMA, FinishPlan, Meter, VideoOptions, _wobble, apply_finish,
                      check_video_target, finished_frames, measure_channels, plan_finish)
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
    """Brightness of every RAW frame. The slow part: done once per folder (and remembered between
    runs while no file changes), then previews are instant."""
    paths = [f.path for f in seq]
    cached = measure_cache.load(paths, "raw-brightness")
    if cached is not None:
        return cached["brightness"]
    brightness = measure_brightness(seq, progress=tracker.stage("Measuring") if tracker else None,
                                    on_image=lambda i, img: previews.save(paths[i], img))
    measure_cache.save(paths, "raw-brightness", {"brightness": brightness})
    return brightness


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


def ensure_previews(seq: Sequence, tracker: Tracker | None = None) -> int:
    """Make any previews that are missing (folders measured before previews existed). Decodes those RAW
    files once, at measuring speed. Returns how many were made."""
    from ..measure import _load_for_measuring
    from ..parallel import ordered_map
    paths = [f.path for f in seq]
    todo = previews.missing(paths)
    hook = tracker.stage("Making previews") if tracker else None
    for done, (i, (image, _)) in enumerate(zip(todo, ordered_map(_load_for_measuring,
                                                                  [(paths[i], "careful") for i in todo])), 1):
        previews.save(paths[i], image)
        if hook:
            hook(done, len(todo))
    return len(todo)


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
    timings: dict = field(default_factory=dict)
    strip: Path | None = None              # the time strip, full detail, beside the report
    strip_columns: dict = field(default_factory=dict)

    @property
    def where_the_time_went(self) -> str:
        """One plain sentence: what writing spent its time on, and the drive's reading speed."""
        t = self.timings
        parts = {"reading frames": t.get("read_busy", 0.0), "correcting": t.get("correct", 0.0),
                 "checking": t.get("check", 0.0), "the video encoder": t.get("encode", 0.0) + t.get("finalise", 0.0),
                 "saving frames": t.get("save", 0.0)}
        total = sum(parts.values())
        if total <= 0:
            return ""
        shares = ", ".join(f"{name} {100 * v / total:.0f}%" for name, v in
                           sorted(parts.items(), key=lambda kv: -kv[1]) if v / total >= 0.02)
        line = f"Where the time went: {shares}."
        if t.get("elapsed") and t.get("frames"):
            line = f"Took {_duration(t['elapsed'])}, about {t['elapsed'] / t['frames']:.2f} s a frame. " + line
        if t.get("read_busy", 0) > 0 and t.get("bytes"):
            speed = t["bytes"] / t["read_busy"] / 1e6
            line += f" Frames were read at about {speed:.0f} MB/s"
            if speed < 100 and max(parts, key=parts.get) == "reading frames":
                line += ": the drive (or its cable) is what's holding things up"
            line += "."
        return line

    @property
    def brightness_after(self) -> float:
        return _wobble(self.check @ LUMA)

    @property
    def colour_after(self) -> float:
        return _wobble(self.check[:, 0] - self.check[:, 2])


def _duration(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    return f"{m} min {s} s" if m else f"{s} s"


def plan_export(folder: Path, window: int = 25, colour: bool = True, tracker: Tracker | None = None) -> Planned:
    """Measure the exported frames and plan their corrections. Writes nothing."""
    frames = finished_frames(Path(folder).expanduser())
    cached = measure_cache.load(frames, "export-channels")
    if cached is not None:
        measured, motion = cached["measured"], float(cached["motion"][0])
    else:
        meter = Meter(motion=True)
        measured = measure_channels(frames, tracker.stage("Measuring") if tracker else None, meter=meter)
        motion = meter.motion()
        measure_cache.save(frames, "export-channels", {"measured": measured, "motion": np.full(len(frames), motion)})
    return Planned(frames, plan_finish(frames, window=window, colour=colour, measured=measured), motion)


def write_export(planned: Planned, out: Path | None = None, video: Path | None = None, fps: float = 24,
                 width: int | None = None, codec: str = "h264", dither: bool = True, fmt: str | None = None,
                 settings: dict | None = None, tracker: Tracker | None = None, options: VideoOptions | None = None,
                 jpeg_quality: int = JPEG_QUALITY, first: int | None = None, last: int | None = None,
                 strip_x: float = 0.5) -> Finished:
    """Write corrected frames and/or the video, check them, review them, and save the run report.
    ``first``/``last`` (1-based) trim what's written; the smoothing still comes from the whole sequence."""
    if options is not None:
        options.check()
        codec = options.codec
    if video is not None:
        check_video_target(video, codec)
    if first is not None or last is not None:
        planned = Planned(list(planned.plan.trimmed(first, last).frames), planned.plan.trimmed(first, last),
                          planned.motion)
    after = Meter()
    timings: dict = {}
    columns: dict = {"x": strip_x}
    began = time.perf_counter()
    written = apply_finish(planned.plan, out=out, video=video, fps=fps, width=width, options=options,
                           jpeg_quality=jpeg_quality, strip=columns,
                           progress=tracker.stage("Writing") if tracker else None, fmt=fmt, codec=codec,
                           dither=dither, meter=after,
                           finalising=(lambda: tracker.stage("Finishing the video file")(0, 0)) if tracker else None,
                           timings=timings)
    timings["elapsed"] = time.perf_counter() - began
    timings["frames"] = len(planned.frames)
    check = after.series()
    findings = review_frames(check, [f.name for f in planned.frames])
    where = Path(out) if out else Path(video).with_name(Path(video).stem + ".timelapse-report.json")
    # The time slice, full size, beside the frames folder or the video: never inside a frames folder, where
    # image-sequence importers (and finish itself) would take it for a frame.
    strip_path = (Path(out).parent / (Path(out).name + ".timelapse-slice.jpg") if out else
                  Path(video).with_name(Path(video).stem + ".timelapse-slice.jpg"))
    try:
        from PIL import Image
        from ..timeslice import stack, to_8bit
        picture = stack(to_8bit(columns["slice_before"]), to_8bit(columns["slice_after"]))
        Image.fromarray(picture).save(strip_path, quality=93)
    except (OSError, KeyError, ValueError):
        strip_path = None
    settings = {"measure": "regional-vote-1", "codec": codec if video else None, "dither": dither,
                "video": None if not video or options is None else
                {"pix_fmt": options.pix_fmt, "quality": options.quality, "prores_profile": options.prores_profile},
                "frames": [first or 1, last or None] if (first or last) else None,
                "fps": fps, "width": width, **(settings or {}),
                "seconds": {k: round(v, 2) for k, v in timings.items() if k not in ("bytes", "frames")},
                "seconds_per_frame": round(timings["elapsed"] / max(1, timings["frames"]), 3),
                "bytes_read": timings.get("bytes", 0),
                "strip": strip_path.name if strip_path else None, "strip_x": strip_x}
    try:
        saved = run_report.write(run_report.finish_report(planned.plan, written if out else None, settings, check,
                                                          findings, planned.motion,
                                                          where=Path(out) if out else Path(video).parent), where)
    except OSError:
        saved = None
    return Finished(written, check, findings, saved, Path(video) if video else None, timings, strip_path, columns)
