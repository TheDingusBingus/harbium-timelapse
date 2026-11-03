"""The app's state and commands, screen by screen, in plain Python.

Screens read properties and call commands; they never decide anything. Work
runs in background jobs (``app.jobs``); their events come back through
``post``, which the screen layer sets to "run this on the window's event loop"
(tests use "run it now"). After any change, ``changed()`` tells the screens to
redraw.

The operations themselves come from ``app.session``, passed in so tests can use
quick stand-ins where real RAW decoding isn't possible.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ...app import config
from ...app import session as default_session
from ...app.jobs import Done, Failed, JobRunner, Stopped
from ...app.project import STAGES, load_project
from ...progress import Progress
from .export import (BUILT_IN, RESOLUTIONS, ExportSettings, apply_preset, delete_user_preset, describe,
                     matching_preset,
                     estimate_bytes, estimate_seconds, free_bytes, notes, output_size, problems, remember_speed,
                     save_user_preset, user_presets)

STAGE_BAR = (("folder", "Folder"), ("prepare", "Prepare"), ("grade", "Grade"), ("finish", "Finish"),
             ("done", "Review"))

PRESETS = {k: {"label": v["label"], "detail": v["detail"], "codec": v["settings"].get("codec", "h264"),
               "width": RESOLUTIONS.get(v["settings"].get("resolution", "native")),
               "suffix": ".mov" if v["settings"].get("codec") == "prores" else ".mp4"}
           for k, v in BUILT_IN.items()}

STRIP = 12                      # thumbnails across the sequence in the visual trim
STOPPED = "Stopped. Everything written so far is consistent: run it again to carry on, or undo."


@dataclass
class JobState:
    name: str
    stage: str = "Starting"
    done: int = 0
    total: int = 0
    cancelling: bool = False

    @property
    def fraction(self) -> float | None:
        return self.done / self.total if self.total else None

    @property
    def text(self) -> str:
        if self.cancelling:
            return "Stopping at the next safe point..."
        return f"{self.stage} {self.done} of {self.total}" if self.total else f"{self.stage}..."


def _wobble(values) -> float:
    d = np.diff(np.asarray(values, dtype=np.float64))
    d = d[np.isfinite(d)]
    return float(np.std(d) / np.sqrt(2)) if d.size > 1 else 0.0


class AppModel:
    def __init__(self, post: Callable[[Callable[[], None]], None] | None = None, runner: JobRunner | None = None,
                 services=None, clock: Callable[[], float] = time.time):
        self.post = post or (lambda fn: fn())
        self.runner = runner or JobRunner()
        self.s = services or default_session
        self.clock = clock
        self._listeners: list[Callable[[], None]] = []
        self._handle = None
        self._last_handle = None
        self.screen = "start"
        self.job: JobState | None = None
        self.message: str | None = None
        self.message_kind = "info"
        self.recent = config.recent_projects()
        self.project = None
        self.opened = None
        self._reset_project_state()

    def _reset_project_state(self) -> None:
        self.measured = None
        self.preview = None
        self.previewing = False
        self._preview_wanted = None
        self.metadata_saved = False
        self.export_since = None
        self.export = None
        self.out = ExportSettings()
        self.trim_frames, self.trim_strip, self.trim_ends, self.trim_version = 0, [], (), 0
        self.out_version = 0            # goes up whenever an export setting changes (screens rebuild on it)
        self._check_cache = None
        self.planned = None
        self.finished = None
        self.current = None

    # --- listeners ---------------------------------------------------------------------------

    def subscribe(self, fn: Callable[[], None]) -> None:
        self._listeners.append(fn)

    def changed(self) -> None:
        for fn in list(self._listeners):
            fn()

    def _notice(self, text: str, kind: str = "info") -> None:
        self.message, self.message_kind = text, kind

    def dismiss_message(self) -> None:
        self.message = None
        self.changed()

    # --- jobs -----------------------------------------------------------------------------------

    def _run(self, name: str, work, on_done) -> bool:
        if self.job is not None:
            return False                                  # one job at a time
        self.job = JobState(name)
        self.message = None

        def on_event(event):
            self.post(lambda: self._event(event, on_done))
        self._handle = self._last_handle = self.runner.start(name, work, on_event)
        self.changed()
        return True

    def _event(self, event, on_done) -> None:
        if isinstance(event, Progress):
            if self.job is not None:
                self.job.stage, self.job.done, self.job.total = event.stage, event.done, event.total
                self.changed()
            return
        self.job, self._handle = None, None
        if isinstance(event, Done):
            try:
                on_done(event.result)
            except ValueError as e:
                self._notice(str(e), "error")
        elif isinstance(event, Stopped):
            self._notice(STOPPED)
        elif isinstance(event, Failed):
            self._notice(event.message, "error")
        self.changed()

    def cancel(self) -> None:
        if self._handle is not None and self.job is not None:
            self._handle.cancel()
            self.job.cancelling = True
            self.changed()

    def wait(self, timeout: float = 60) -> None:
        """For tests and scripts: until the current job has finished."""
        if self._last_handle is not None:
            self._last_handle.wait(timeout)

    # --- navigation -----------------------------------------------------------------------------

    def _reached(self, stage: str) -> bool:
        return self.project is not None and STAGES.index(self.project.stage) >= STAGES.index(stage)

    def can_go(self, screen: str) -> bool:
        if self.job is not None:
            return False
        if screen == "start":
            return True
        if self.opened is None:
            return False
        return {
            "folder": True,
            "prepare": self.opened.editor is not None and not self.opened.blocking,
            "grade": self._reached("prepared"),
            "finish": True,
            "done": self.finished is not None,
        }.get(screen, False)

    def stage_items(self) -> list[tuple[str, str, str]]:
        """(screen, label, state) for the stage bar; state is current, done, open or locked."""
        done_for = {"folder": "opened", "prepare": "prepared", "grade": "exported", "finish": "finished"}
        items = []
        no_editor = self.opened is not None and self.opened.editor is None
        for screen, label in STAGE_BAR:
            if no_editor and screen in ("prepare", "grade"):
                state = "locked"                       # nothing to prepare: these steps don't apply
            elif screen == self.screen:
                state = "current"
            elif screen in done_for and self._reached(done_for[screen]) and screen != "folder":
                state = "done"
            elif screen == "folder" and self.opened is not None:
                state = "done"
            else:
                state = "open" if self.can_go(screen) else "locked"
            items.append((screen, label, state))
        return items

    def go(self, screen: str) -> None:
        if not self.can_go(screen):
            return
        self.screen = screen
        if screen == "prepare":
            self._ensure_measured()
        elif screen == "grade" and self.export_since is None:
            self.export_since = self.clock()
        elif screen == "finish":
            self._check_cache = None                    # free space and remembered speeds may have changed
            self.request_trim_thumbs()
            if self.video_path is None:
                self._default_video_path()
        self.changed()

    # --- start --------------------------------------------------------------------------------

    def open_folder(self, folder: str | Path) -> None:
        folder = Path(folder)
        if not folder.is_dir():
            self._notice(f"Couldn't open {folder}: it isn't a folder.", "error")
            self.changed()
            return
        self._run("Opening", lambda t: self.s.open_folder(folder, t), lambda opened: self._opened(folder, opened))

    def _opened(self, folder: Path, opened) -> None:
        project = load_project(folder)                    # raises ValueError for a newer version: shown as a notice
        self._reset_project_state()
        self.opened, self.project = opened, project
        if project.note:
            self._notice(project.note)
        if self.opened.editor is not None:
            project.editor = self.opened.editor.name
        elif not project.export:
            project.export = str(folder)                 # no editor files: these frames are the export
        if project.export:
            self.export_since = 0.0
        config.remember_project(folder, len(opened.seq), project.stage)
        self.recent = config.recent_projects()
        self.screen = "folder"

    # --- folder check ---------------------------------------------------------------------------

    @property
    def facts(self) -> list[tuple[str, str]]:
        if self.opened is None:
            return []
        o = self.opened
        return [("Frames", str(len(o.seq))), ("Camera", o.camera), ("Shot", o.span),
                ("Editor", o.editor.label if o.editor else "none found")]

    @property
    def problems(self) -> list[dict]:
        if self.opened is None:
            return []
        cards, changes = [], [p for p in self.opened.problems if p.kind == "settings_change"]
        for p in self.opened.problems:
            if p.kind != "settings_change":
                cards.append({"serious": p.serious, "message": p.message, "fix": p.fix, "note": False})
        if changes:
            first, last = changes[0].message, changes[-1].message
            def name(m: str) -> str:
                return m.split(" at ", 1)[-1].split(":", 1)[0] if " at " in m else ""
            span = f", from {name(first)} to {name(last)}" if len(changes) > 1 else f" at {name(first)}"
            cards.append({"serious": False, "note": True, "fix": "",
                          "message": f"{len(changes)} exposure change{'s' if len(changes) > 1 else ''} while shooting"
                                     f"{span}. Nothing to do: each is smoothed into a gentle ramp.",
                          "details": [p.message for p in changes]})
        return cards

    @property
    def next_step(self) -> tuple[str, str]:
        if self.opened is not None and self.opened.editor is not None:
            return "prepare", "Next: preview corrections"
        return "finish", "Next: finish an export"

    @property
    def resume(self) -> tuple[str, str] | None:
        """Where an earlier session got to, and the screen to carry on from."""
        if self.project is None or self.project.stage == "opened":
            return None
        target = {"prepared": "grade", "exported": "finish", "finished": "finish"}[self.project.stage]
        words = {"prepared": "corrections applied", "exported": "exported", "finished": "finished"}
        return target, f"Last time this folder got as far as: {words[self.project.stage]}."

    # --- prepare --------------------------------------------------------------------------------

    @property
    def window(self) -> int:
        return int(self.project.settings["window"]) if self.project else 25

    @property
    def white_balance_available(self) -> bool:
        """Lightroom's white balance isn't supported yet, so its switch isn't shown."""
        return self.opened is not None and self.opened.editor is not None and self.opened.editor.name != "lightroom"

    @property
    def white_balance(self) -> bool:
        return bool(self.project.settings["white_balance"]) if self.project else True

    def _ensure_measured(self) -> None:
        if self.measured is None and self.opened is not None:
            self._run("Measuring", lambda t: self.s.measure(self.opened.seq, t), self._measured)

    def _measured(self, measured) -> None:
        self.measured = measured
        self.request_preview()

    def set_window(self, window: int) -> None:
        self.project.settings["window"] = int(window)
        self.request_preview()

    def set_white_balance(self, on: bool) -> None:
        self.project.settings["white_balance"] = bool(on)
        self.request_preview()

    def request_preview(self) -> None:
        """Re-plan with the current settings. While one preview is running, only the latest request waits."""
        if self.measured is None or self.opened is None or self.opened.editor is None:
            return
        self._preview_wanted = (self.window, self.white_balance)
        if not self.previewing:
            self._start_preview()

    def _start_preview(self) -> None:
        window, white_balance = self._preview_wanted
        self._preview_wanted = None
        self.previewing = True
        editor, seq, measured = self.opened.editor, self.opened.seq, self.measured
        self.runner.start("Preview", lambda t: self.s.preview(editor, seq, measured, window=window,
                                                                white_balance=white_balance),
                          lambda e: None if isinstance(e, Progress) else self.post(lambda: self._previewed(e)))

    def _previewed(self, event) -> None:
        self.previewing = False
        if isinstance(event, Done):
            self.preview = event.result
        elif isinstance(event, Failed):
            self._notice(event.message, "error")
        if self._preview_wanted is not None:
            self._start_preview()
        self.changed()

    @property
    def prepare_series(self) -> tuple[np.ndarray, np.ndarray] | None:
        """(measured, smoothing curve) for the timeline, once a preview exists."""
        if self.preview is None or self.preview.deflicker is None:
            return None
        return self.preview.deflicker.brightness, self.preview.deflicker.target

    @property
    def exposure_steps(self) -> tuple[int, ...]:
        if self.preview is None or self.preview.deflicker is None:
            return ()
        return tuple(int(c.frame) for c in self.preview.deflicker.changes)

    @property
    def prepare_summary(self) -> str:
        series = self.prepare_series
        if series is None:
            return ""
        measured, target = series
        biggest = float(np.nanmax(np.abs(self.preview.deflicker.exposure)))
        return (f"Frame-to-frame wobble {_wobble(measured):.3f} stops now, {_wobble(target):.3f} after. "
                f"Largest correction {biggest:.2f} stops.")

    @property
    def needs_metadata_saved(self) -> bool:
        return self.opened is not None and self.opened.editor is not None and self.opened.editor.name == "lightroom"

    def confirm_metadata_saved(self, yes: bool) -> None:
        self.metadata_saved = bool(yes)
        self.changed()

    @property
    def can_apply(self) -> bool:
        return (self.job is None and self.preview is not None and not self.previewing
                and (self.metadata_saved or not self.needs_metadata_saved))

    def apply(self) -> None:
        if not self.can_apply:
            return
        editor, seq, measured = self.opened.editor, self.opened.seq, self.measured
        window, wb = self.window, self.white_balance
        self._run("Applying corrections",
                  lambda t: self.s.apply(editor, seq, measured, window=window, white_balance=wb, tracker=t),
                  self._applied)

    def _applied(self, report) -> None:
        self.project.reach("prepared")
        self.project.save()
        self.export_since = self.clock()
        self._notice(f"Corrections applied to {report.changed} frames.")
        self.screen = "grade"

    def undo(self) -> None:
        if self.opened is None or self.opened.editor is None:
            return
        editor, seq = self.opened.editor, self.opened.seq
        self._run("Undoing", lambda t: self.s.undo(editor, seq), self._undone)

    def _undone(self, count) -> None:
        self.project.start_over()
        self.project.save()
        self._notice(f"Took the corrections out of {count} frames: the edit files are back as they were.")
        self.screen = "folder"

    # --- grade: waiting for the export ------------------------------------------------------------

    @property
    def handoff_steps(self) -> list[str]:
        if self.opened is not None and self.opened.editor is not None:
            return self.opened.editor.handoff_steps()
        return ["Grade the sequence in your editor.", "Export every frame as JPEG into a new, empty folder."]

    def poll_export(self) -> None:
        """Called every second or so by the screen while it's waiting."""
        if self.opened is None or self.export_since is None:
            return
        status = self.s.export_status(self.opened.seq.folder, len(self.opened.seq), self.export_since)
        if status != self.export:
            self.export = status
            self.changed()

    def use_export(self, folder: str | Path | None = None) -> None:
        folder = Path(folder) if folder else (self.export.folder if self.export else None)
        if folder is None or not Path(folder).is_dir():
            return
        self.project.export = str(folder)
        self.project.reach("exported")
        self.project.save()
        self.planned = self.finished = None
        self.screen = "finish"
        self._default_video_path()
        self.changed()

    # --- finish: the export window -------------------------------------------------------------------

    def _default_video_path(self) -> None:
        if self.opened is None:
            return
        saved = (self.project.settings.get("export") if self.project else None) or {}
        if saved:
            self.out = ExportSettings.from_dict(saved)
        videos = Path.home() / "Videos"
        base = videos if videos.is_dir() else Path.home()
        name = self.opened.seq.folder.name
        if not self.out.video_path:
            self.out.video_path = str(base / (name + self.out.suffix))
        if not self.out.frames_folder and self.project and self.project.export:
            source = Path(self.project.export)
            self.out.frames_folder = str(source.parent / (source.name + "-finished"))

    def _changed_export(self, custom: bool = True) -> None:
        self.out_version += 1
        self._check_cache = None
        self.out.preset = matching_preset(self.out)          # worked out, never just flagged
        if self.out.video_path:
            self.out.video_path = str(Path(self.out.video_path).with_suffix(self.out.suffix))
        if self.project is not None:
            self.project.settings["export"] = self.out.to_dict()
        self.changed()

    def set_export(self, **values) -> None:
        """Change options (codec, quality, resolution, fps, output, trim, ...). The preset shown is whichever one
        the encoding settings match; output, frame rate, trim and locations never change it."""
        for k, v in values.items():
            if not hasattr(self.out, k):
                raise AttributeError(k)
            setattr(self.out, k, v)
        self._changed_export()

    def choose_preset(self, preset: str) -> None:
        try:
            self.out = apply_preset(self.out, preset)
        except ValueError as e:
            self._notice(str(e), "error")
        self._changed_export(custom=False)

    def save_preset(self, name: str) -> None:
        try:
            save_user_preset(name, self.out)
            self._notice(f"Saved the preset {name.strip()}.")
        except ValueError as e:
            self._notice(str(e), "error")
        self._changed_export(custom=False)

    def delete_preset(self, name: str) -> None:
        delete_user_preset(name)
        self._changed_export(custom=False)

    @property
    def presets(self) -> list[tuple[str, str]]:
        """(key, label) for every preset: built-in first, then your own."""
        return [(k, v["label"]) for k, v in BUILT_IN.items()] + [(k, k) for k in sorted(user_presets())]

    @property
    def preset(self) -> str:
        return matching_preset(self.out)

    # --- the visual trim: thumbnails made quietly in the background -------------------------------------

    def request_trim_thumbs(self) -> None:
        """A strip of thumbnails across the export, and the chosen first and last frames, made in the background."""
        if self.project is None or not self.project.export or not Path(self.project.export).is_dir():
            return
        from ...app.thumbs import thumbnail
        from ...finish import finished_frames
        export = Path(self.project.export)
        first, last = self.out.first, self.out.last

        def work(t):
            frames = finished_frames(export)
            n = len(frames)
            picks = sorted({round(i * (n - 1) / (STRIP - 1)) for i in range(STRIP)})
            strip = [(i, thumbnail(frames[i])) for i in picks]
            a, b = (first or 1) - 1, (last or n) - 1
            ends = (thumbnail(frames[min(max(a, 0), n - 1)]), thumbnail(frames[min(max(b, 0), n - 1)]))
            return n, strip, ends

        def done(event):
            if isinstance(event, Done):
                self.trim_frames, self.trim_strip, self.trim_ends = event.result
                self.trim_version += 1
                self.changed()
        self.runner.start("Thumbnails", work, lambda e: None if isinstance(e, Progress) else self.post(lambda: done(e)))

    def set_trim(self, first: int | None, last: int | None) -> None:
        n = self.trim_frames
        self.set_export(first=None if not first or first <= 1 else int(first),
                        last=None if not last or (n and last >= n) else int(last))
        self.request_trim_thumbs()

    def frame_time(self, frame: int) -> str:
        """Where a frame lands in the video, as minutes:seconds."""
        seconds = (frame - 1) / max(self.out.fps, 1)
        return f"{int(seconds // 60)}:{seconds % 60:04.1f}"

    @property
    def video_path(self) -> Path | None:
        return Path(self.out.video_path) if self.out.video_path else None

    @property
    def fps(self) -> float:
        return self.out.fps

    @property
    def also_frames(self) -> bool:
        return self.out.output == "both"

    def set_video_path(self, path: str | Path) -> None:
        self.out.video_path = str(path)
        self._changed_export(custom=False)

    def set_frames_folder(self, path: str | Path) -> None:
        self.out.frames_folder = str(path)
        self._changed_export(custom=False)

    def set_fps(self, fps: float) -> None:
        self.set_export(fps=float(fps))

    def set_also_frames(self, on: bool) -> None:
        self.set_export(output="both" if on else "video")

    def _source(self) -> tuple[int, int, int, float]:
        """Width, height, number of frames and average file size of the export, for estimates."""
        from PIL import Image
        from ...finish import finished_frames
        frames = finished_frames(Path(self.project.export))
        with Image.open(frames[0]) as im:
            w, h = im.size
        sample = frames[:: max(1, len(frames) // 12)]
        return w, h, len(frames), sum(f.stat().st_size for f in sample) / len(sample)

    @property
    def export_check(self) -> dict:
        """Everything the export window shows before anything is written: problems, notes, estimates.
        Worked out once per change of settings (it reads a few files, which is slow on some drives)."""
        key = (self.out_version, self.project.export if self.project else None)
        if self._check_cache is None or self._check_cache[0] != key:
            self._check_cache = (key, self._work_out_check())
        return self._check_cache[1]

    def _work_out_check(self) -> dict:
        if self.project is None or not self.project.export or not Path(self.project.export).is_dir():
            return {"problems": ["Choose the folder of exported frames."], "notes": [], "estimate": ""}
        try:
            w, h, n, average = self._source()
        except (OSError, ValueError, IndexError) as e:
            return {"problems": [f"Couldn't read the exported frames ({e})."], "notes": [], "estimate": ""}
        s = self.out
        first, last = s.first or 1, s.last or n
        count = max(0, last - first + 1)
        out_w, out_h = output_size(s, w, h)
        size = estimate_bytes(s, w, h, count, average)
        seconds = estimate_seconds(s, count)
        amount = describe(size)
        bits = ["Under 1 MB" if amount.startswith("under") else f"About {amount}"]
        bits.append(f"Takes about {int(seconds // 60)} min {int(seconds % 60)} s" if seconds else
                    "Time shown after the first export of this kind")
        where = s.video_path if s.video else s.frames_folder
        free = free_bytes(where)
        problems_ = problems(s, n, Path(self.project.export))
        if free is not None:
            bits.append(f"{describe(free)} free")
            if size > free:
                problems_.append(f"There isn't enough free space: about {describe(size)} needed, "
                                 f"{describe(free)} free.")
        if s.video:
            bits.insert(0, f"{count} frames at {out_w} × {out_h}")
        return {"problems": problems_, "notes": notes(s, out_w if s.video else None), "estimate": ". ".join(bits) + "."}

    @staticmethod
    def _preset_width(export: Path, limit: int | None) -> int | None:
        """A width is a ceiling, never a target: frames are never enlarged."""
        if limit is None:
            return None
        from PIL import Image
        from ...finish import finished_frames
        try:
            with Image.open(finished_frames(export)[0]) as im:
                return min(limit, im.size[0])
        except (OSError, ValueError, IndexError):
            return limit

    @property
    def can_finish(self) -> bool:
        return self.job is None and not self.export_check["problems"]

    def start_finish(self) -> None:
        if not self.can_finish:
            return
        s = self.out
        export = Path(self.project.export)
        video = Path(s.video_path) if s.video else None
        frames_out = Path(s.frames_folder) if s.frames else None
        width = self._preset_width(export, s.width_limit()) if s.video else None
        options, window = s.options(), self.window
        fmt = s.frames_format if s.frames else None

        def work(t):
            planned = self.s.plan_export(export, window=window, tracker=t)
            done = self.s.write_export(planned, out=frames_out, video=video, fps=s.fps, width=width,
                                       codec=s.codec, dither=s.dither, fmt=fmt, options=options,
                                       jpeg_quality=s.jpeg_quality, first=s.first, last=s.last,
                                       settings={"window": window, "preset": s.preset}, tracker=t)
            return planned, done
        self._run("Finishing", work, self._finished)

    def _finished(self, result) -> None:
        self.planned, self.finished = result
        t = self.finished.timings
        if t.get("elapsed") and t.get("frames"):
            remember_speed(self.out, t["elapsed"] / t["frames"])     # for the next estimate of this kind
        self._check_cache = None
        self.project.video = str(self.finished.video) if self.finished.video else None
        self.project.reach("finished")
        self.project.save()
        config.remember_project(self.opened.seq.folder, len(self.opened.seq), self.project.stage)
        self.screen = "done"

    # --- done: what came out -------------------------------------------------------------------------

    @property
    def result_lines(self) -> list[str]:
        if self.finished is None:
            return []
        p = self.planned.plan
        lines = [f"Brightness wobble {p.brightness_before:.3f} stops before, {self.finished.brightness_after:.3f} after.",
                 f"Colour wobble {p.colour_before:.3f} before, {self.finished.colour_after:.3f} after."]
        if self.planned.moving_camera:
            lines.append(self.planned.moving_camera)
        if self.finished.where_the_time_went:
            lines.append(self.finished.where_the_time_went)
        return lines

    @property
    def worth_a_look(self) -> list[tuple[str, str]]:
        """(frame name, what's odd about it), one line per frame, without ones marked fine."""
        if self.finished is None:
            return []
        out: dict[str, list[str]] = {}
        for f in self.finished.findings:
            if f.name not in self.project.dismissed:
                out.setdefault(f.name, []).append(f.note)
        return [(name, "; ".join(notes)) for name, notes in out.items()]

    def looks_fine(self, name: str) -> None:
        if self.project is not None and name not in self.project.dismissed:
            self.project.dismissed.append(name)
            self.project.save()
            self.changed()
