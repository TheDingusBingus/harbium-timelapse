"""Harbium Flet check: the Phase 1 experiments from docs/GUI_PLAN.md, in one app.

Run it two ways and compare: from source (`flet run src/main.py`), then as a
built Windows app (`flet build windows`). Each button runs one experiment and
adds its numbers to the results panel; "Save results" writes them to a text
file in your Documents folder to paste back.

Heavy work always runs off the window's event loop (Flet 1.0 runs event
handlers on it), so the window should stay responsive throughout; whether it
does is part of the test.
"""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import flet as ft
import numpy as np

APP_DIR = Path(__file__).resolve().parent
RAW = (".cr3", ".cr2", ".nef", ".arw", ".raf", ".orf", ".rw2", ".dng")
SMALL = int(os.environ.get("HARBIUM_CHECK_FRAMES", "0"))     # testing only: fewer frames for S3 and S5


# --- helpers (top level, so worker processes can use them) ------------------------

def assets_dir() -> Path:
    return Path(os.environ.get("FLET_ASSETS_DIR", APP_DIR / "assets")).resolve()


def bundled(name: str) -> str | None:
    """A program shipped inside the app, by full path; never from PATH or the current folder."""
    exe = assets_dir() / "bin" / (name + (".exe" if os.name == "nt" else ""))
    return str(exe) if exe.is_file() else None


def decode_small(path: str) -> tuple[int, int]:
    from harbium_timelapse.measure import _load_for_measuring
    img, _ = _load_for_measuring((Path(path), "careful"))
    return img.shape[:2]


def stay_busy(seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        pass


def memory_gb() -> float | None:
    if os.name != "nt":
        try:
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
        except (ValueError, OSError, AttributeError):
            return None
    import ctypes

    class Status(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                    ("avail", ctypes.c_ulonglong), ("pt", ctypes.c_ulonglong), ("pa", ctypes.c_ulonglong),
                    ("vt", ctypes.c_ulonglong), ("va", ctypes.c_ulonglong), ("ve", ctypes.c_ulonglong)]
    s = Status()
    s.length = ctypes.sizeof(Status)
    return s.total / 1e9 if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s)) else None


def draw_timeline(measured: np.ndarray, curve: np.ndarray, w: int = 900, h: int = 260) -> np.ndarray:
    """The timeline drawn as an image: measured light as a fine line, the smoothing curve heavier on top."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(im)
    lo, hi, pad = -3.2, 0.8, 8
    n = len(measured)
    def xy(values):
        return [(pad + i * (w - 2 * pad) / (n - 1), pad + (hi - v) / (hi - lo) * (h - 2 * pad))
                for i, v in enumerate(values)]
    for v in range(-3, 1):
        y = pad + (hi - v) / (hi - lo) * (h - 2 * pad)
        d.line([(pad, y), (w - pad, y)], fill=(225, 225, 225))
    d.line(xy(measured), fill=(150, 160, 170), width=1)
    d.line(xy(curve), fill=(0, 0, 0), width=3)
    return np.asarray(im)


def preview_frame(path: str) -> np.ndarray:
    """A small display copy of a RAW file (sRGB, 8-bit), as the real app's preview cache would hold."""
    from harbium_timelapse.finish import _linear_to_srgb
    from harbium_timelapse.measure import _load_for_measuring
    img, _ = _load_for_measuring((Path(path), "careful"))
    img = img / max(np.percentile(img, 99.5), 1e-6) * 0.9          # a quick, steady exposure for viewing
    return np.round(_linear_to_srgb(np.clip(img, 0, 1)) * 255).astype(np.uint8)


def add_markers(frames: list[np.ndarray]) -> list[np.ndarray]:
    """A bar that crosses the frame steadily and a frame counter: stutter shows up as the bar jumping."""
    from PIL import Image, ImageDraw
    out = []
    for i, f in enumerate(frames):
        im = Image.fromarray(np.ascontiguousarray(f[..., :3]))
        d = ImageDraw.Draw(im)
        w, h = im.size
        x = int((i % 96) / 96 * w)
        d.rectangle([x, 0, x + max(3, w // 160), h], fill=(255, 64, 160))
        d.rectangle([0, 0, 90, 22], fill=(0, 0, 0))
        d.text((6, 4), f"frame {i + 1}", fill=(255, 255, 255))
        out.append(np.asarray(im))
    return out


def synthetic_frames(n: int, w: int = 960, h: int = 640) -> list[np.ndarray]:
    """Dusk-like test frames: a sky gradient with noise and a light that falls."""
    rng = np.random.default_rng(1)
    ramp = np.linspace(0.25, 0.85, w)[None, :] * np.linspace(1.0, 0.55, h)[:, None]
    base = np.stack([ramp * 1.05, ramp * 0.9, ramp * 0.75], axis=-1)
    frames = []
    for i in range(n):
        light = 1.0 - 0.6 * i / max(n - 1, 1)
        img = base * light + rng.normal(0, 0.02, (h, w, 1))
        frames.append(np.clip(img * 255, 0, 255).astype(np.uint8))
    return frames


def summary(times_ms: list[float]) -> str:
    t = sorted(times_ms)
    return (f"median {statistics.median(t):.1f} ms, 95% under {t[int(0.95 * (len(t) - 1))]:.1f} ms, "
            f"worst {t[-1]:.1f} ms ({len(t)} frames)")


# --- the app ---------------------------------------------------------------------------

async def main(page: ft.Page) -> None:
    page.title = "Harbium Flet check"
    page.padding = 20
    page.scroll = ft.ScrollMode.AUTO
    picker = ft.FilePicker()
    page.services.append(picker)
    state = {"folder": None}

    results = ft.TextField(multiline=True, read_only=True, min_lines=10, max_lines=18, text_size=12,
                           label="Results", expand=True)
    status = ft.Text("")
    key_seen = ft.Text("Last key: (press any key)")
    raw = ft.RawImage(width=480, height=320)

    def log(line: str) -> None:
        results.value = (results.value or "") + line + "\n"
        page.update()

    def busy(text: str) -> None:
        status.value = text
        page.update()

    # -- machine and versions
    def machine() -> None:
        import PIL
        import rawpy
        import harbium_timelapse
        log("== Machine ==")
        log(f"{platform.platform()}, {os.cpu_count()} logical cores, "
            f"{(memory_gb() or 0):.1f} GB memory, Python {platform.python_version()}")
        packaged = not Path(sys.executable).stem.lower().startswith("python")
        log(f"Packaged app: {'yes' if packaged else 'no (running from source)'} ({Path(sys.executable).name})")
        log(f"flet {ft.__version__ if hasattr(ft, '__version__') else '?'}, numpy {np.__version__}, "
            f"Pillow {PIL.__version__}, rawpy {rawpy.__version__} (LibRaw {rawpy.libraw_version}), "
            f"harbium-timelapse {getattr(harbium_timelapse, '__version__', 'installed')}")

    # -- S1: packaging
    async def s1(_):
        busy("S1: checking bundled programs and your RAW files...")
        log("== S1 packaging ==")
        for name, args in (("exiftool", ["-ver"]), ("ffmpeg", ["-version"])):
            exe = bundled(name)
            if not exe:
                log(f"{name}: NOT FOUND in {assets_dir() / 'bin'}")
                continue
            out = await asyncio.to_thread(subprocess.run, [exe, *args], capture_output=True, text=True, timeout=60)
            log(f"{name}: runs from the app's own folder: {(out.stdout or out.stderr).splitlines()[0][:60]}")
        folder = await picker.get_directory_path(dialog_title="Choose a folder of RAW files")
        if not folder:
            log("No folder chosen.")
            return busy("")
        state["folder"] = folder
        files = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in RAW)
        log(f"{len(files)} RAW files in {folder}")
        if not files:
            return busy("")
        if bundled("exiftool"):
            os.environ["PATH"] = str(Path(bundled("exiftool")).parent) + os.pathsep + os.environ.get("PATH", "")
        from harbium_timelapse.exif import read_frames
        t = time.perf_counter()
        frames = await asyncio.to_thread(read_frames, files[:20])
        first = frames[0]
        log(f"Read settings of {len(frames)} files in {time.perf_counter() - t:.1f} s: first is "
            f"{first.camera}, ISO {first.iso}, {first.shutter} s, f/{first.aperture}")
        t = time.perf_counter()
        shape = await asyncio.to_thread(decode_small, str(files[0]))
        log(f"Decoded {files[0].name} for measuring ({shape[1]}x{shape[0]}) in {time.perf_counter() - t:.1f} s")
        ffmpeg = bundled("ffmpeg")
        if ffmpeg:
            video = Path(tempfile.gettempdir()) / "harbium-flet-check.mp4"

            def encode() -> int:
                frames = synthetic_frames(48)
                p = subprocess.Popen([ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                                      "-s", "960x640", "-framerate", "24", "-i", "-", "-pix_fmt", "yuv420p",
                                      "-c:v", "libx264", "-crf", "18", str(video)], stdin=subprocess.PIPE)
                for f in frames:
                    p.stdin.write(f.tobytes())
                p.stdin.close()
                return p.wait()
            code = await asyncio.to_thread(encode)
            log(f"Made a 2-second video with the bundled ffmpeg: "
                f"{'OK, ' + str(round(video.stat().st_size / 1e3)) + ' KB' if code == 0 else 'FAILED'}")
        busy("")

    # -- S2: worker processes
    async def s2(_):
        if not state["folder"]:
            return log("S2 needs a folder: run S1 first.")
        busy("S2: decoding on one core, then on all cores (watch for extra windows!)...")
        log("== S2 worker processes ==")
        from harbium_timelapse.parallel import default_workers, ordered_map
        files = [str(p) for p in sorted(Path(state["folder"]).iterdir()) if p.suffix.lower() in RAW][:24]
        timings = {}
        for workers in (1, default_workers()):
            t = time.perf_counter()
            await asyncio.to_thread(lambda w=workers: list(ordered_map(decode_small, files, workers=w)))
            timings[workers] = time.perf_counter() - t
        one, many = timings[1], timings[default_workers()]
        log(f"Decoding {len(files)} RAW files: {one:.1f} s on one core, {many:.1f} s with "
            f"{default_workers()} workers ({one / many:.1f}x faster)")

        def force_stop() -> tuple[bool, int | None]:
            p = mp.get_context("spawn").Process(target=stay_busy, args=(60,))
            p.start()
            time.sleep(1.5)
            p.terminate()
            p.join(3)
            return p.is_alive(), p.exitcode
        alive, code = await asyncio.to_thread(force_stop)
        log(f"Forced stop of a busy worker: {'STILL RUNNING' if alive else 'stopped'} (exit code {code})")
        log("Did any extra windows appear during S2? Note it here: ____")
        busy("")

    # -- S3: frame display speed
    async def s3(_):
        busy("S3: stepping through frames...")
        log("== S3 frame display ==")
        frames = await asyncio.to_thread(synthetic_frames, SMALL or 240)
        times = []
        for f in frames:
            t = time.perf_counter()
            await raw.render(f)
            times.append((time.perf_counter() - t) * 1000)
        log(f"RawImage, 960x640, confirmed on screen: {summary(times)}")
        folder = Path(tempfile.mkdtemp(prefix="harbium-previews-"))

        def write_jpegs() -> list[str]:
            from PIL import Image
            paths = []
            for i, f in enumerate(frames):
                p = folder / f"p{i:04d}-{time.time_ns()}.jpg"
                Image.fromarray(f).save(p, quality=85)
                paths.append(str(p))
            return paths
        paths = await asyncio.to_thread(write_jpegs)
        image = ft.Image(src=paths[0], width=480, height=320)
        page.add(image)
        times = []
        for p in paths:
            t = time.perf_counter()
            image.src = p
            image.update()
            await asyncio.sleep(0)
            times.append((time.perf_counter() - t) * 1000)
        page.remove(image)
        log(f"Image by file path (sent, not confirmed on screen): {summary(times)}")
        log("Did stepping look smooth or stuttery? Note it here: ____")
        busy("")

    # -- S4: the timeline chart
    async def s4(_):
        import flet_charts as fc
        from harbium_timelapse.deflicker import smooth_brightness
        busy("S4: drawing a 2,000-frame timeline; drag the slider back and forth...")
        log("== S4 timeline chart ==")
        n = 2000
        rng = np.random.default_rng(3)
        measured = -2.6 / (1 + np.exp(-(np.arange(n) - 1300) / 250)) + rng.normal(0, 0.05, n)

        def series(values, color, width):
            step = max(1, n // 1000)                       # thin to about one point per pixel
            pts = [fc.LineChartDataPoint(float(i), float(values[i])) for i in range(0, n, step)]
            return fc.LineChartData(points=pts, color=color, stroke_width=width)
        t = time.perf_counter()
        curve = await asyncio.to_thread(smooth_brightness, measured, 25)
        first_ms = (time.perf_counter() - t) * 1000
        chart = fc.LineChart(data_series=[series(measured, ft.Colors.BLUE_GREY_300, 1),
                                          series(curve, ft.Colors.BLACK, 2)],
                             min_y=-3.2, max_y=0.8, animation=False)
        drawn = ft.RawImage(width=900, height=260)
        plans, draws, waits, own = [], [], [], []
        latest = {"value": None, "moved_at": 0.0, "running": False}

        async def replan_until_caught_up():
            # Only ever calculate the newest slider position; moves made meanwhile are skipped, not queued.
            latest["running"] = True
            while latest["value"] is not None:
                value, moved_at = latest["value"], latest["moved_at"]
                latest["value"] = None
                t0 = time.perf_counter()
                c = await asyncio.to_thread(smooth_brightness, measured, value)
                t1 = time.perf_counter()
                chart.data_series = [series(measured, ft.Colors.BLUE_GREY_300, 1), series(c, ft.Colors.BLACK, 2)]
                chart.update()
                t2 = time.perf_counter()
                await drawn.render(await asyncio.to_thread(draw_timeline, measured, c))
                own.append((time.perf_counter() - t2) * 1000)
                plans.append((t1 - t0) * 1000)
                draws.append((t2 - t1) * 1000)
                waits.append((t2 - moved_at) * 1000)
                busy(f"S4: smoothness {value}: re-plan {plans[-1]:.0f} ms, extension chart {draws[-1]:.0f} ms, "
                     f"own drawing on screen {own[-1]:.0f} ms, slider to curve {waits[-1]:.0f} ms")
            latest["running"] = False

        async def changed(e):
            latest["value"], latest["moved_at"] = int(e.control.value), time.perf_counter()
            if not latest["running"]:
                page.run_task(replan_until_caught_up)

        async def done(_):
            if plans:
                log(f"Smoothing 2,000 frames: {first_ms:.0f} ms first time; while dragging ({len(plans)} redraws): "
                    f"re-plan median {statistics.median(plans):.0f} ms, chart update median "
                    f"{statistics.median(draws):.0f} ms, our own drawn timeline on screen median "
                    f"{statistics.median(own):.0f} ms, slider move to new curve median "
                    f"{statistics.median(waits):.0f} ms (worst {max(waits):.0f} ms)")
            log("Did the extension chart (top box) show lines, or stay grey? ____")
            log("Did the curve follow the slider smoothly? Note it here: ____")
            page.remove(box)
            busy("")
        slider = ft.Slider(min=7, max=61, divisions=27, value=25, label="{value}", on_change=changed, width=400)
        box = ft.Column([
            ft.Text("Chart extension (expected to show lines only in the built app):"),
            ft.Container(chart, height=300, width=900, bgcolor=ft.Colors.WHITE, padding=10,
                         border=ft.Border.all(1, ft.Colors.GREY_400)),
            ft.Text("Drawn by the app itself (works everywhere):"),
            drawn,
            ft.Row([ft.Text("Smoothness"), slider]),
            ft.Button("Done with S4", on_click=done)])
        page.add(box)
        await drawn.render(await asyncio.to_thread(draw_timeline, measured, curve))

    # -- S5: playback
    async def s5(_):
        busy("S5: preparing frames...")
        log("== S5 playback ==")
        if state["folder"]:
            from harbium_timelapse.parallel import ordered_map
            files = [str(p) for p in sorted(Path(state["folder"]).iterdir()) if p.suffix.lower() in RAW]
            frames = await asyncio.to_thread(lambda: list(ordered_map(preview_frame, files)))
            what = f"your sequence ({len(frames)} frames)"
        else:
            frames = await asyncio.to_thread(synthetic_frames, SMALL or 229)
            what = f"test frames ({len(frames)}; run S1 first to play your own)"
        frames = await asyncio.to_thread(add_markers, frames)
        busy(f"S5: playing {what} at 24 frames a second...")
        interval = 1 / 24
        late = 0
        start = time.perf_counter()
        for i, f in enumerate(frames):
            await raw.render(f)
            wait = start + (i + 1) * interval - time.perf_counter()
            if wait > 0:
                await asyncio.sleep(wait)
            else:
                late += 1
        took = time.perf_counter() - start
        log(f"Played {what} in {took:.1f} s ({len(frames) / took:.1f} frames a second; target 24), {late} late")
        log("Did playback look smooth? Did the marker glide or jump? Note it here: ____")
        busy("")

    # -- S6: keyboard and network
    async def s6(_):
        log("== S6 keyboard, accessibility, network ==")
        log(f"Keys: try arrow keys, F, B and Space now; the 'Last key' line should follow. {key_seen.value}")
        if os.name == "nt":
            out = await asyncio.to_thread(subprocess.run, ["netstat", "-ano", "-p", "tcp"], capture_output=True,
                                          text=True, timeout=30)
            mine = [line.split() for line in out.stdout.splitlines()
                    if "LISTENING" in line and line.split()[-1] == str(os.getpid())]
            log("Listening network ports opened by this app: " +
                (", ".join(parts[1] for parts in mine) if mine else "none"))
        log("Narrator: turn it on (Ctrl+Win+Enter), Tab through the buttons; are they read out? ____")
        log("Drag-and-drop from Explorer isn't built in (needs a community extension); not tested here.")

    def key(e: ft.KeyboardEvent) -> None:
        key_seen.value = f"Last key: {e.key}"
        page.update()
    page.on_keyboard_event = key

    async def save(_):
        target = Path.home() / "Documents" / "harbium-flet-check-results.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(results.value or "", encoding="utf-8")
        busy(f"Saved to {target}")

    def row(label: str, text: str, handler) -> ft.Row:
        return ft.Row([ft.Button(label, on_click=handler, width=110), ft.Text(text, expand=True)])

    page.add(
        ft.Text("Harbium Flet check", size=24, weight=ft.FontWeight.BOLD),
        ft.Text("Run these in order. Each adds its numbers to the results below."),
        row("S1", "Packaging: bundled ExifTool and ffmpeg, and your RAW files", s1),
        row("S2", "Worker processes: all cores, no extra windows, a forced stop", s2),
        row("S3", "Frame display: 240 frames as fast as they'll show", s3),
        row("S4", "Timeline chart: 2,000 frames, drag the smoothness slider", s4),
        row("S5", "Playback: 229 frames at 24 frames a second", s5),
        row("S6", "Keyboard, screen reader, network ports", s6),
        key_seen, status, raw, ft.Row([results]),
        ft.Row([ft.Button("Save results", on_click=save)]),
    )
    machine()


if __name__ == "__main__":
    mp.freeze_support()
    ft.run(main)
