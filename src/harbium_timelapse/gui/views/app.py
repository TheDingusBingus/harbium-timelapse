"""The screens: a shell (header, stage bar, messages, job progress) and one class per step.

Each screen is built once when it's shown and then refreshed in place, so a
slider being dragged is never rebuilt under the pointer. Everything shown comes
from ``AppModel``; every click calls one of its commands.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import flet as ft

from ...app.session import ExportStatus
from ..models import AppModel, Series, render_timeline
from ..models.export import (BUILT_IN, CHROMA_CHOICES, CODEC_CHOICES, DEPTH_CHOICES, FRAME_RATES, PRORES_CHOICES,
                             RESOLUTIONS, explain)
from ..models.timeline import Theme
from ..theme import BODY, DARK, DISPLAY, FONTS, LIGHT, MONO

FPS_CHOICES = ("23.976", "24", "25", "29.97", "30", "50", "60")


def open_path(path: Path, select: bool = False) -> None:
    """Open a file with its usual program, or show it in its folder."""
    path = Path(path)
    if sys.platform.startswith("win"):
        if select:
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            os.startfile(str(path))                      # noqa: S606 - opening the user's own file
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R" if select else "", str(path)] if select else ["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent if select else path)])


def _image_bytes(path) -> bytes:
    """A small image's data, so it shows the same in the desktop app and in a browser (which can't read local paths)."""
    try:
        return Path(path).read_bytes()
    except OSError:
        return b""


class UI:
    """Small helpers so every screen uses the same brand styles."""

    def __init__(self, tokens: dict):
        self.t = tokens

    def title(self, text: str, size: int = 32) -> ft.Text:
        return ft.Text(text, font_family=DISPLAY, size=size, color=self.t["text"], weight=ft.FontWeight.W_500)

    def text(self, text: str, dim: bool = False, size: int = 15, mono: bool = False, selectable=False) -> ft.Text:
        return ft.Text(text, font_family=MONO if mono else BODY, size=size - (1 if mono else 0),
                       color=self.t["dim" if dim else "text"], selectable=selectable)

    def primary(self, label: str, on_click, disabled: bool = False) -> ft.FilledButton:
        t = self.t
        return ft.FilledButton(label, on_click=on_click, disabled=disabled, style=ft.ButtonStyle(
            bgcolor={ft.ControlState.DEFAULT: t["primary_bg"], ft.ControlState.DISABLED: t["line"]},
            color={ft.ControlState.DEFAULT: t["primary_text"], ft.ControlState.DISABLED: t["dim"]},
            shape=ft.RoundedRectangleBorder(radius=2), padding=ft.Padding(18, 14, 18, 14)))

    def secondary(self, label: str, on_click, disabled: bool = False) -> ft.OutlinedButton:
        t = self.t
        return ft.OutlinedButton(label, on_click=on_click, disabled=disabled, style=ft.ButtonStyle(
            color={ft.ControlState.DEFAULT: t["text"], ft.ControlState.DISABLED: t["dim"]},
            shape=ft.RoundedRectangleBorder(radius=2),
            side=ft.BorderSide(1, t["line"]), padding=ft.Padding(16, 14, 16, 14)))

    def panel(self, content, padding: int = 16) -> ft.Container:
        return ft.Container(content, bgcolor=self.t["panel"], padding=padding, border_radius=2,
                            border=ft.Border.all(1, self.t["line"]))

    def mark(self, size: int = 28) -> ft.Container:
        """The H mark from harbium.studio's favicon."""
        return ft.Container(ft.Text("H", font_family=DISPLAY, size=size * 0.78, color=self.t["bg"]),
                            width=size, height=size, bgcolor=self.t["text"], alignment=ft.Alignment.CENTER)


# --- the screens ---------------------------------------------------------------------------------

class Screen:
    def __init__(self, view: "View"):
        self.view, self.m, self.ui = view, view.model, view.ui

    def build(self) -> ft.Control:
        raise NotImplementedError

    def refresh(self) -> None:
        """Update in place after the model changes. Default: nothing to update."""

    def leave(self) -> None:
        """Called when another screen takes over."""


class SlicePanel:
    """Before and after the prepared corrections, side by side, as time slices from the RAW previews."""
    WIDTH = 784                     # the same width as the timeline panel, so the page never wraps
    EACH = 372

    def __init__(self, screen: Screen, words: str):
        self.screen, self.m, self.ui = screen, screen.m, screen.ui
        self.before = ft.Image(src=b"", width=self.EACH, fit=ft.BoxFit.CONTAIN, visible=False)
        self.after = ft.Image(src=b"", width=self.EACH, fit=ft.BoxFit.CONTAIN, visible=False)
        self.status = self.ui.text("", dim=True, size=13)
        self.words = words
        self._shown = None

    def control(self) -> ft.Control:
        ui = self.ui
        side = lambda label, image: ft.Column([ui.text(label, size=13), image], spacing=4)   # noqa: E731
        return ft.Container(ui.panel(ft.Column(
            [ui.text("Time slice: the scene with time running left to right", dim=True, size=13),
             ui.text(self.words, dim=True, size=12), self.status,
             ft.Container(ft.Row([side("Before", self.before), side("After", self.after)], spacing=8),
                          bgcolor=ui.t["surround"], padding=6, border_radius=2)], spacing=6), padding=12),
            width=self.WIDTH)

    def refresh(self) -> None:
        self.status.value = self.m.preview_status
        pictures = self.m.time_slice()
        if pictures is not None and pictures[0] is not self._shown:
            self._shown = pictures[0]
            self.screen.view.page.run_task(self.draw, pictures)

    async def draw(self, pictures):
        import io
        from PIL import Image

        def make(picture) -> bytes:
            img = Image.fromarray(picture)
            scale = (self.EACH * 2) / img.width                       # twice the shown size: sharp on high-DPI screens
            img = img.resize((self.EACH * 2, max(1, round(img.height * scale))), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=88)
            return buf.getvalue()
        self.before.src, self.after.src = await asyncio.gather(asyncio.to_thread(make, pictures[0]),
                                                               asyncio.to_thread(make, pictures[1]))
        self.before.visible = self.after.visible = True
        self.screen.view.page.update()


class StartScreen(Screen):
    def build(self):
        ui = self.ui
        recent = [ft.Container(
            ft.Row([ui.text(f"{Path(r['folder']).parent.name} / {Path(r['folder']).name}"),
                    ui.text(f"{r['frames']} frames", dim=True),
                    ui.text(r["stage"].capitalize(), dim=True)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            padding=ft.Padding(4, 12, 4, 12), border=ft.Border(bottom=ft.BorderSide(1, ui.t["line"])),
            on_click=lambda e, f=r["folder"]: self.m.open_folder(f), ink=True) for r in self.m.recent]
        return ft.Column([
            ui.title("Open a timelapse"),
            ui.text("Choose the folder with your RAW frames, or an export to finish. Nothing in it changes "
                    "until you say so.", dim=True),
            ft.Container(height=8),
            ft.Row([ui.primary("Choose folder", self.choose)]),
            ft.Container(height=24),
            *([ui.text("Recent", size=14), *recent] if recent else []),
        ], spacing=10, width=760)

    async def choose(self, e):
        folder = await self.view.ask("get_directory_path", dialog_title="Choose a folder of timelapse frames")
        if folder:
            self.m.open_folder(folder)


class FolderScreen(Screen):
    def build(self):
        ui, m = self.ui, self.m
        facts = ft.Row([ft.Column([ui.text(k, dim=True, size=13), ui.text(v)], spacing=2) for k, v in m.facts],
                       spacing=36, wrap=True)
        cards = []
        for p in m.problems or [{"serious": False, "message": "Nothing to report: the folder looks ready.", "fix": ""}]:
            icon = ft.Container(ft.Text("!" if (p["serious"] or p["fix"]) and not p.get("note") else "✓", size=12,
                                        color=ui.t["bg"]),
                                width=20, height=20, border_radius=10, bgcolor=ui.t["text"],
                                alignment=ft.Alignment.CENTER)
            body = [ui.text(p["message"])] + ([ui.text(p["fix"], dim=True, size=14)] if p["fix"] else [])
            if p.get("details"):
                listing = ft.Column([ui.text(d, dim=True, size=13) for d in p["details"]], spacing=2, visible=False)
                def toggle(e, box=listing):
                    box.visible = not box.visible
                    e.control.text = "Hide them" if box.visible else "Show them"
                    self.view.page.update()
                body += [ft.TextButton("Show them", on_click=toggle, style=ft.ButtonStyle(color=ui.t["dim"])), listing]
            cards.append(ft.Container(ft.Row([icon, ft.Column(body, spacing=2, expand=True)],
                                             vertical_alignment=ft.CrossAxisAlignment.START),
                                      padding=ft.Padding(0, 10, 0, 10),
                                      border=ft.Border(bottom=ft.BorderSide(1, ui.t["line"]))))
        target, label = m.next_step
        actions = [ui.primary(label, lambda e: m.go(target), disabled=not m.can_go(target))]
        resume = []
        if m.resume:
            where, words = m.resume
            resume = [ui.panel(ft.Row([ui.text(words), ui.secondary("Carry on from there", lambda e: m.go(where))],
                                      alignment=ft.MainAxisAlignment.SPACE_BETWEEN))]
        return ft.Column([ui.title(Path(m.opened.seq.folder).name, 30), facts, *resume,
                          ui.text("Folder check", size=14), *cards, ft.Row(actions, alignment=ft.MainAxisAlignment.END)],
                         spacing=14, width=900)


class PrepareScreen(Screen):
    WIDTH, HEIGHT = 760, 260

    def build(self):
        ui, m = self.ui, self.m
        self.timeline = ft.RawImage(width=self.WIDTH, height=self.HEIGHT)
        self.summary = ui.text("", dim=True, size=14)
        self.notes = ft.Column(spacing=4)
        self.apply_btn = ui.primary("Apply corrections", lambda e: m.apply())
        self.state = ui.text("", dim=True, size=14)
        self.slider = ft.Slider(min=7, max=61, divisions=27, value=m.window, label="{value} frames",
                                on_change=lambda e: m.set_window(int(e.control.value)), expand=True)
        side = [ui.title("Prepare"),
                ui.text("The light as measured from your RAW files, and the smooth curve the corrections "
                        "will follow. Nothing changes until you apply.", dim=True, size=14)]
        if m.white_balance_available:
            side.append(ft.Switch(label="Steady the camera's auto white balance", value=m.white_balance,
                                  on_change=lambda e: m.set_white_balance(e.control.value)))
        side += [ui.text("Smoothness", size=14),
                 ft.Row([ui.text("Steady", dim=True, size=13), self.slider, ui.text("Follows\nthe light", dim=True,
                                                                                       size=13)], spacing=4)]
        if m.needs_metadata_saved:
            side.append(ft.Row([ft.Checkbox(value=m.metadata_saved,
                                            on_change=lambda e: m.confirm_metadata_saved(e.control.value)),
                                ft.Container(ui.text("In Lightroom, I selected every frame and chose Metadata > "
                                                     "Save Metadata to Files", size=14), expand=True)],
                               vertical_alignment=ft.CrossAxisAlignment.START))
        side += [self.summary, self.notes, self.state,
                 ft.Row([ui.secondary("Undo", lambda e: m.undo()), self.apply_btn], spacing=10)]
        self._drawn = None
        self.refresh()
        self.slice = SlicePanel(self, "Each frame gives one band. A frame that jumps shows as a band lighter or "
                                      "darker than its neighbours; after the corrections the bands should fade "
                                      "smoothly. A rough guide from the RAW files, before your grade.")
        self.slice.refresh()
        return ft.Row([ft.Column(side, spacing=12, width=380),
                       ft.Column([ui.panel(ft.Column([ui.text("Brightness (stops): measured, and the smoothing curve",
                                                              dim=True, size=13), self.timeline], spacing=8),
                                           padding=12), self.slice.control()], spacing=16)],
                      spacing=28, vertical_alignment=ft.CrossAxisAlignment.START, wrap=True)

    def refresh(self):
        m, ui = self.m, self.ui
        self.summary.value = m.prepare_summary
        self.notes.controls = [ui.text(n, dim=True, size=13) for n in (m.preview.notes if m.preview else [])]
        self.apply_btn.disabled = not m.can_apply
        self.state.value = ("Measuring your RAW files..." if m.measured is None
                            else "Updating..." if m.previewing else "")
        if m.preview is not None and m.preview is not self._drawn:
            self._drawn = m.preview
            self.view.page.run_task(self.draw)
        if hasattr(self, "slice"):
            self.slice.refresh()

    async def draw(self):
        series = self.m.prepare_series
        if series is None:
            return
        measured, curve = series
        t = self.ui.t
        img = await asyncio.to_thread(render_timeline,
                                      [Series(measured, t["measured"], 1), Series(curve, t["curve"], 3)],
                                      self.WIDTH, self.HEIGHT, self.view.chart_theme, steps=self.m.exposure_steps)
        await self.timeline.render(img)


class GradeScreen(Screen):
    def build(self):
        ui, m = self.ui, self.m
        editor = m.opened.editor.label if m.opened.editor else "your editor"
        self.watch = ft.Column(spacing=10)
        self._polling = True
        self.view.page.run_task(self.poll)
        self.refresh()
        steps = [ft.Container(ui.text(s, size=14), padding=ft.Padding(0, 10, 0, 10),
                              border=ft.Border(bottom=ft.BorderSide(1, ui.t["line"]))) for s in m.handoff_steps]
        self.slice = SlicePanel(self, "What the applied corrections do to the light, from the RAW files (your "
                                      "grade isn't in it). After should fade smoothly from band to band.")
        self.slice.refresh()
        return ft.Row([ft.Column([ui.title(f"Over to {editor}."),
                                  ui.text("Grade the sequence however you like, then export every frame. "
                                          "This screen notices when the export lands.", dim=True, size=14),
                                  *steps], spacing=8, width=420),
                       ft.Column([ft.Container(ui.panel(self.watch), width=440), self.slice.control()], spacing=16)],
                      spacing=32, vertical_alignment=ft.CrossAxisAlignment.START, wrap=True)

    def refresh(self):
        ui, m = self.ui, self.m
        if hasattr(self, "slice"):
            self.slice.refresh()
        s = m.export or ExportStatus("waiting")
        choose = ui.secondary("Choose folder instead", self.choose)
        if s.state == "waiting":
            self.watch.controls = [ui.text("Watching for a new export..."), choose]
        elif s.state == "arriving":
            total = len(m.opened.seq)
            self.watch.controls = [ui.text(f"Export arriving: {s.frames} of {total} frames"),
                                   ui.text(str(s.folder), dim=True, mono=True, size=13),
                                   ft.ProgressBar(value=min(1.0, s.frames / total), color=ui.t["text"],
                                                  bgcolor=ui.t["line"]), choose]
        else:
            self.watch.controls = [ui.text(f"Found {s.frames} exported frames.", size=17),
                                   ui.text(str(s.folder), dim=True, mono=True, size=13),
                                   ui.text("All there and nothing changing for a few seconds, so the export "
                                           "looks complete.", dim=True, size=14),
                                   ft.Row([ui.primary("Finish now", lambda e: m.use_export()), choose])]

    async def poll(self):
        while self._polling:
            await asyncio.to_thread(self.m.poll_export)
            await asyncio.sleep(1.0)

    def leave(self):
        self._polling = False

    async def choose(self, e):
        folder = await self.view.ask("get_directory_path", dialog_title="Choose the folder of exported frames")
        if folder:
            self.m.use_export(folder)


class FinishScreen(Screen):
    """The export window: presets up front, every option one click away, checks before anything is written."""

    def build(self):
        self.box = ft.Column(spacing=14, width=920)
        self.advanced_open = getattr(self, "advanced_open", False)
        self._version = None
        self.go_btn = None
        self.refresh()
        return self.box

    def refresh(self):
        m = self.m
        version = (m.out_version, m.trim_version)
        if self._version != version:                         # settings or thumbnails changed: lay out again
            self._version = version
            self.box.controls = self._controls()
        if self.go_btn is not None:
            self.go_btn.disabled = not m.can_finish

    # -- layout ----------------------------------------------------------------------------------
    def _controls(self) -> list:
        ui, m, s = self.ui, self.m, self.m.out
        check = m.export_check

        def row(label, *controls):
            return ft.Row([ft.Container(ui.text(label, dim=True, size=14), width=150), *controls],
                          vertical_alignment=ft.CrossAxisAlignment.CENTER, wrap=True)

        def dropdown(value, options, on_pick, width=200):
            return ft.Dropdown(options=[ft.DropdownOption(key=str(k), text=t) for k, t in options], value=str(value),
                               width=width, dense=True, on_select=lambda e: on_pick(e.control.value))

        def path_field(value, on_done, width=520):
            return ft.TextField(value=value or "", text_size=13, text_style=ft.TextStyle(font_family=MONO),
                                dense=True, width=width, border_color=ui.t["line"],
                                on_blur=lambda e: on_done(e.control.value), on_submit=lambda e: on_done(e.control.value))

        current = m.preset
        presets = []
        for key, label in m.presets:
            presets.append((ui.primary if key == current else ui.secondary)(label, lambda e, k=key: m.choose_preset(k)))
        presets.append(ui.primary("Custom", None) if current == "custom" else
                       ui.secondary("Custom", None, disabled=True))        # always there, so nothing jumps
        detail = BUILT_IN[current]["detail"] if current in BUILT_IN else (
            "Your own preset." if current != "custom" else "Your own settings: save them as a preset below.")

        out = [ui.title("Export"),
               ui.text("Smooths whatever flicker is left in your export, then writes the video, the frames, or both.",
                       dim=True, size=14),
               row("Exported frames", ui.text(m.project.export or "Not chosen yet", mono=True, size=14),
                   ui.secondary("Change", self.choose_export)),
               row("Output", ft.SegmentedButton(
                   segments=[ft.Segment(value=v, label=ft.Text(t)) for v, t in
                             (("video", "Video"), ("frames", "Frames"), ("both", "Both"))],
                   selected=[s.output], allow_multiple_selection=False,
                   on_change=lambda e: m.set_export(output=list(e.control.selected)[0])))]
        if s.video:
            out += [row("Video preset", ft.Row(presets, spacing=8, wrap=True)),
                    row("", ui.text(detail, dim=True, size=14))]
        if s.video:
            out.append(row("Video", path_field(s.video_path, self.typed_video), ui.secondary("Change", self.choose_video)))
        if s.frames:
            out.append(row("Frames", path_field(s.frames_folder, lambda v: v.strip() and m.set_frames_folder(v.strip().strip('"'))),
                           ui.secondary("Change", self.choose_frames)))
            fmt = [dropdown(s.frames_format, [("jpg", "JPEG"), ("png", "PNG"), ("tif", "16-bit TIFF")],
                            lambda v: m.set_export(frames_format=v), 180)]
            if s.frames_format == "jpg":
                fmt += [ui.text("Quality", dim=True, size=13),
                        ft.Slider(min=70, max=100, divisions=30, value=s.jpeg_quality, label="{value}", width=220,
                                  on_change_end=lambda e: m.set_export(jpeg_quality=int(e.control.value)))]
            out.append(row("Frame format", *fmt))

        out.append(ft.TextButton(("▾ " if self.advanced_open else "▸ ") + "Advanced", on_click=self.toggle_advanced,
                                 style=ft.ButtonStyle(color=ui.t["text"])))
        if self.advanced_open:
            out += self._advanced()

        out.append(ui.panel(ft.Column(
            [ui.text(check["estimate"], size=14)]
            + [ui.text(n, dim=True, size=13) for n in check["notes"]]
            + [ui.text("Before exporting: " + p, size=14) for p in check["problems"]], spacing=6)))
        name = ft.TextField(hint_text="Preset name", dense=True, width=220, text_size=13, border_color=ui.t["line"])
        saving = [name, ui.secondary("Save as preset", lambda e: m.save_preset(name.value or ""))]
        if s.preset not in BUILT_IN and s.preset != "custom":
            saving.append(ui.secondary("Delete this preset", lambda e: m.delete_preset(s.preset)))
        self.go_btn = ui.primary("Export", lambda e: m.start_finish(), disabled=not m.can_finish)
        out.append(ft.Row([ft.Row(saving, spacing=8), self.go_btn], alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                          wrap=True))
        return out

    def _advanced(self) -> list:
        ui, m, s = self.ui, self.m, self.m.out

        def row(label, *controls):
            return ft.Row([ft.Container(ui.text(label, dim=True, size=14), width=150), *controls],
                          vertical_alignment=ft.CrossAxisAlignment.CENTER, wrap=True)

        def dropdown(value, options, on_pick, width=200):
            return ft.Dropdown(options=[ft.DropdownOption(key=str(k), text=t) for k, t in options], value=str(value),
                               width=width, dense=True, on_select=lambda e: on_pick(e.control.value))
        rows = []
        if s.video:
            res = [dropdown(s.resolution, [(k, "Original size" if k == "native" else f"Up to {k}") for k in RESOLUTIONS]
                            + [("custom", "Custom width")], lambda v: m.set_export(resolution=v), 200)]
            if s.resolution == "custom":
                res.append(ft.TextField(value=str(s.custom_width or ""), hint_text="width in pixels", width=150, dense=True,
                                        text_size=13, border_color=ui.t["line"],
                                        on_blur=lambda e: e.control.value.strip().isdigit() and
                                        m.set_export(custom_width=int(e.control.value))))
            rows.append(row("Resolution", *res))
            rows.append(row("Frame rate", dropdown(s.fps, [(f, f"{f:g}") for f in FRAME_RATES],
                                                   lambda v: m.set_export(fps=float(v)), 140)))
            fmt = [dropdown(s.codec, CODEC_CHOICES, lambda v: m.set_export(codec=v, chroma=None, bit_depth=None), 300)]
            if s.codec == "h265":
                fmt.append(dropdown(s.bit_depth or 10, DEPTH_CHOICES, lambda v: m.set_export(bit_depth=int(v)), 270))
            if s.codec == "prores":
                fmt.append(dropdown(s.prores_profile, PRORES_CHOICES, lambda v: m.set_export(prores_profile=v), 280))
            else:
                fmt.append(dropdown(s.chroma or "420", CHROMA_CHOICES, lambda v: m.set_export(chroma=v), 360))
            rows.append(row("Format", *fmt))
            rows.append(row("", ft.Container(ui.text(explain(s), dim=True, size=13), width=720)))
            if s.codec != "prores":
                crf = s.quality if s.quality is not None else (16 if s.codec == "h264" else 18)
                rows.append(row("Quality", ui.text("Higher", dim=True, size=13),
                                ft.Slider(min=8, max=32, divisions=24, value=crf, width=260,
                                          label="{value}" + (" (the default)" if s.quality is None else ""),
                                          on_change_end=lambda e: m.set_export(quality=int(e.control.value))),
                                ui.text("Lower", dim=True, size=13),
                                ui.text("Higher quality means bigger files.", dim=True, size=13)))
            rows.append(row("Dither", ft.Switch(value=s.dither, on_change=lambda e: m.set_export(dither=e.control.value)),
                            ui.text("hides banding in smooth skies (costs a little file size)", dim=True, size=13)))
        rows += self._trim()
        return rows

    def _trim(self) -> list:
        """Trim by eye: thumbnails across the sequence, a two-handled slider, and the chosen first and last frames."""
        ui, m, s = self.ui, self.m, self.m.out
        n = m.trim_frames
        if n < 2:
            return [ft.Row([ft.Container(ui.text("Trim", dim=True, size=14), width=150),
                            ui.text("Making thumbnails...", dim=True, size=13)])]
        first, last = s.first or 1, s.last or n
        strip = ft.Row([ft.Image(src=_image_bytes(path), width=62, height=42, fit=ft.BoxFit.COVER, border_radius=2,
                                 opacity=1.0 if first - 1 <= i <= last - 1 else 0.3) for i, path in m.trim_strip],
                       spacing=2)
        slider = ft.RangeSlider(min=1, max=n, start_value=first, end_value=last, divisions=n - 1, label="{value}",
                                width=62 * len(m.trim_strip) + 2 * (len(m.trim_strip) - 1),
                                on_change_end=lambda e: m.set_trim(int(e.control.start_value), int(e.control.end_value)))
        ends = []
        for label, frame, path in (("First", first, m.trim_ends[0] if m.trim_ends else None),
                                   ("Last", last, m.trim_ends[1] if m.trim_ends else None)):
            ends.append(ft.Column([ft.Image(src=_image_bytes(path), width=180, height=120, fit=ft.BoxFit.COVER, border_radius=2)
                                   if path else ft.Container(width=180, height=120),
                                   ui.text(f"{label}: frame {frame} ({m.frame_time(frame)})", dim=True, size=13)],
                                  spacing=4))
        summary = (f"All {n} frames" if (first, last) == (1, n) else
                   f"Frames {first} to {last}: {last - first + 1} of {n}, "
                   f"{(last - first + 1) / max(s.fps, 1):.1f} s of video")
        return [ft.Row([ft.Container(ui.text("Trim", dim=True, size=14), width=150),
                        ft.Column([strip, slider, ui.text(summary, dim=True, size=13), ft.Row(ends, spacing=16)],
                                  spacing=6)], vertical_alignment=ft.CrossAxisAlignment.START)]

    # -- actions ----------------------------------------------------------------------------------
    def toggle_advanced(self, e):
        self.advanced_open = not self.advanced_open
        self.box.controls = self._controls()
        self.view.page.update()

    def typed_video(self, value):
        text = (value or "").strip().strip('"')
        if text:
            self.m.set_video_path(Path(text).with_suffix(self.m.out.suffix))

    async def choose_export(self, e):
        folder = await self.view.ask("get_directory_path", dialog_title="Choose the folder of exported frames")
        if folder:
            self.m.use_export(folder)

    async def choose_frames(self, e):
        folder = await self.view.ask("get_directory_path", dialog_title="Choose a folder for the corrected frames")
        if folder:
            self.m.set_frames_folder(folder)

    async def choose_video(self, e):
        current = self.m.video_path
        path = await self.view.ask(
            "save_file", dialog_title="Save the video as", file_name=current.name if current else None,
            initial_directory=str(current.parent) if current else None,
            file_type=ft.FilePickerFileType.CUSTOM, allowed_extensions=[self.m.out.suffix.lstrip(".")])
        if path:
            self.m.set_video_path(Path(path).with_suffix(self.m.out.suffix))


class DoneScreen(Screen):
    def build(self):
        ui, m = self.ui, self.m
        self.flags = ft.Column(spacing=0)
        self.refresh()
        video = m.finished.video
        return ft.Column([
            ui.title("Finished."),
            *[ui.text(line, dim=True, size=14) for line in m.result_lines],
            ui.panel(ft.Row([ui.text(str(video), mono=True, size=14, selectable=True),
                             ft.Row([ui.secondary("Show in folder", lambda e: open_path(video, select=True)),
                                     ui.primary("Play video", lambda e: open_path(video))], spacing=10)],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN, wrap=True)) if video else ft.Container(),
            *self._strip(),
            ui.text("Worth a look", size=14), self.flags,
            ft.Row([ui.secondary("Open another folder", lambda e: m.go("start"))]),
        ], spacing=14, width=900)

    def _strip(self) -> list:
        """The time strip: a quick-to-load view to zoom and pan, and the full-detail file a click away."""
        ui, m = self.ui, self.m
        path = m.finished.strip
        if not path or "slice_before" not in m.finished.strip_columns:
            return []
        self.strip_view = ft.Image(src=b"", width=880, fit=ft.BoxFit.CONTAIN)
        self.view.page.run_task(self._load_strip)
        return [ui.text("Time slice", size=14),
                ui.text("Your scene with time running left to right: each frame gives one band of the picture. A frame "
                        "that jumped shows as a band lighter or darker than its neighbours; After should fade smoothly. "
                        "Scroll to zoom, drag to look around.", dim=True, size=13),
                ft.Container(ft.InteractiveViewer(content=self.strip_view, min_scale=1, max_scale=12,
                                                  boundary_margin=ft.Margin.all(40)),
                             bgcolor=ui.t["surround"], padding=8, border_radius=2, width=896, height=420,
                             clip_behavior=ft.ClipBehavior.HARD_EDGE),
                ft.Row([ui.secondary("Open full size", lambda e: open_path(path)),
                        ui.secondary("Show in folder", lambda e: open_path(path, select=True)),
                        ui.text(path.name, dim=True, mono=True, size=13)], spacing=10)]

    async def _load_strip(self):
        import io
        from PIL import Image
        cols = self.m.finished.strip_columns

        def make() -> bytes:
            from ...timeslice import stack, to_8bit
            img = Image.fromarray(stack(to_8bit(cols["slice_before"]), to_8bit(cols["slice_after"])))
            img.thumbnail((1760, 2400))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=88)
            return buf.getvalue()
        self.strip_view.src = await asyncio.to_thread(make)
        self.view.page.update()

    def refresh(self):
        ui, m = self.ui, self.m
        items = m.worth_a_look
        if not items:
            self.flags.controls = [ui.text("Nothing stands out: no frame breaks away from its neighbours.",
                                           dim=True, size=14)]
            return
        self.flags.controls = [ft.Container(
            ft.Row([ui.text(name, mono=True, size=14), ft.Container(ui.text(note, dim=True, size=14), expand=True),
                    ui.secondary("Looks fine", lambda e, n=name: m.looks_fine(n))], spacing=16),
            padding=ft.Padding(0, 8, 0, 8), border=ft.Border(bottom=ft.BorderSide(1, ui.t["line"])))
            for name, note in items]


SCREENS = {"start": StartScreen, "folder": FolderScreen, "prepare": PrepareScreen, "grade": GradeScreen,
           "finish": FinishScreen, "done": DoneScreen}


# --- the shell --------------------------------------------------------------------------------------

class View:
    def __init__(self, page: ft.Page, model: AppModel):
        self.page, self.model = page, model
        dark = page.platform_brightness != ft.Brightness.LIGHT
        self.tokens = DARK if dark else LIGHT
        self.ui = UI(self.tokens)
        self.chart_theme = Theme() if dark else Theme(background=(222, 217, 206), grid=(199, 193, 181),
                                                      text=(94, 90, 82), mark=(10, 10, 10))
        self.picker = ft.FilePicker()
        page.services.append(self.picker)
        self.screen: Screen | None = None
        self.screen_name = None
        self._build_shell()
        model.subscribe(self.refresh)
        self.refresh()

    async def ask(self, method: str, **kwargs):
        """Open a file dialog. If the window doesn't answer (Flet 1.0 sometimes loses track of a
        long-lived picker: "Timeout waiting for invoke method listener"), try once more with a fresh
        picker, as Flet's own examples do; if that fails too, say so plainly instead of crashing."""
        last = None
        for picker in (self.picker, None):
            try:
                return await getattr(picker or ft.FilePicker(), method)(**kwargs)
            except Exception as e:                       # noqa: BLE001 - any failure here is the dialog's
                last = e
        self.model._notice("The file dialog didn't open, so nothing was changed. "
                           + ("You can type the path into the box instead. " if method == "save_file" else "")
                           + f"(Flet said: {str(last)[:160]})", "error")
        self.model.changed()
        return None

    def _build_shell(self):
        ui, t = self.ui, self.tokens
        self.project_name = ui.text("", dim=True, size=14)
        self.stage_bar = ft.Row(spacing=4)
        self.banner_text = ui.text("", size=14)
        self.banner = ft.Container(ft.Row([ft.Container(self.banner_text, expand=True),
                                           ft.TextButton("Dismiss", on_click=lambda e: self.model.dismiss_message(),
                                                         style=ft.ButtonStyle(color=t["text"]))]),
                                   padding=ft.Padding(16, 10, 16, 10), bgcolor=t["panel"],
                                   border=ft.Border(bottom=ft.BorderSide(1, t["line"])), visible=False)
        self.job_text = ui.text("", size=14)
        self.job_bar = ft.ProgressBar(value=None, color=t["text"], bgcolor=t["line"])
        self.cancel_btn = ui.secondary("Cancel", lambda e: self.model.cancel())
        self.job_panel = ft.Container(ft.Column([ft.Row([ft.Container(self.job_text, expand=True), self.cancel_btn]),
                                                 self.job_bar], spacing=8),
                                      padding=ft.Padding(24, 12, 24, 12), bgcolor=t["panel"],
                                      border=ft.Border(bottom=ft.BorderSide(1, t["line"])), visible=False)
        self.body = ft.Container(padding=ft.Padding(32, 28, 32, 28), expand=True)
        header = ft.Container(ft.Row([ui.mark(), ft.Text("Harbium", font_family=DISPLAY, size=26, color=t["text"]),
                                      ui.text("Timelapse", dim=True, size=14), ft.Container(width=16),
                                      self.project_name], spacing=10,
                                     vertical_alignment=ft.CrossAxisAlignment.CENTER),
                              padding=ft.Padding(24, 14, 24, 14), border=ft.Border(bottom=ft.BorderSide(1, t["line"])))
        self.stage_box = ft.Container(self.stage_bar, padding=ft.Padding(20, 6, 20, 6),
                                      border=ft.Border(bottom=ft.BorderSide(1, t["line"])))
        self.page.add(ft.Column([header, self.stage_box, self.banner, self.job_panel,
                                 ft.Column([self.body], scroll=ft.ScrollMode.AUTO, expand=True)],
                                spacing=0, expand=True))

    def _stage_button(self, number: int, screen: str, label: str, state: str) -> ft.Control:
        t = self.tokens
        filled = state in ("done", "current")
        dot = ft.Container(ft.Text("✓" if state == "done" else str(number), size=12,
                                   color=t["bg"] if filled else t["dim"]),
                           width=22, height=22, border_radius=11, alignment=ft.Alignment.CENTER,
                           bgcolor=t["text"] if filled else None, border=ft.Border.all(1, t["line"] if not filled else t["text"]))
        text = ft.Text(label, font_family=BODY, size=14, weight=ft.FontWeight.W_600 if state == "current" else None,
                       color=t["text"] if state in ("current", "done", "open") else t["dim"])
        return ft.Container(ft.Row([dot, text], spacing=8), padding=ft.Padding(10, 8, 10, 8),
                            on_click=None if state == "locked" else (lambda e, s=screen: self.model.go(s)),
                            opacity=0.5 if state == "locked" else 1.0)

    def refresh(self):
        m = self.model
        self.project_name.value = Path(m.opened.seq.folder).name if m.opened else ""
        self.stage_box.visible = m.screen != "start"
        self.stage_bar.controls = [self._stage_button(i + 1, s, label, state)
                                   for i, (s, label, state) in enumerate(m.stage_items())]
        self.banner.visible = bool(m.message)
        self.banner_text.value = m.message or ""
        self.banner_text.color = self.tokens["text"]
        self.job_panel.visible = m.job is not None
        if m.job is not None:
            self.job_text.value = f"{m.job.name}: {m.job.text}"
            self.job_bar.value = m.job.fraction
            self.cancel_btn.disabled = m.job.cancelling
        if m.screen != self.screen_name:
            if self.screen is not None:
                self.screen.leave()
            self.screen_name = m.screen
            self.screen = SCREENS[m.screen](self)
            self.body.content = self.screen.build()
        else:
            self.screen.refresh()
        self.page.update()


def start(page: ft.Page) -> View:
    """Set up the window and show the app. Fonts are read from the app's assets folder."""
    page.title = "Harbium Timelapse"
    page.padding = 0
    page.fonts = FONTS
    dark = page.platform_brightness != ft.Brightness.LIGHT

    def brand(t: dict) -> ft.Theme:
        # Switches, sliders, checkboxes, the presets and the dropdown all take these: bone on ink, or ink on bone.
        return ft.Theme(font_family=BODY, color_scheme=ft.ColorScheme(
            primary=t["text"], on_primary=t["bg"], secondary=t["text"], on_secondary=t["bg"],
            surface=t["bg"], on_surface=t["text"], outline=t["line"], secondary_container=t["line"],
            on_secondary_container=t["text"]))
    page.theme, page.dark_theme = brand(LIGHT), brand(DARK)
    page.theme_mode = ft.ThemeMode.DARK if dark else ft.ThemeMode.LIGHT
    page.bgcolor = (DARK if dark else LIGHT)["bg"]

    async def call(fn):
        fn()
    model = AppModel(post=lambda fn: page.run_task(call, fn))
    return View(page, model)
