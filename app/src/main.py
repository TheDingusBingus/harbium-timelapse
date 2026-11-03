"""Harbium Timelapse: the desktop app.

The app itself lives in the library (``harbium_timelapse.gui``); this file only
starts it. ExifTool and ffmpeg ship inside the app's ``assets/bin`` folder and
are used from there by full path, ahead of anything else on the computer.
"""

import multiprocessing
import os
from pathlib import Path

import flet as ft

ASSETS = Path(os.environ.get("FLET_ASSETS_DIR", Path(__file__).resolve().parent / "assets")).resolve()


def main(page: ft.Page) -> None:
    from harbium_timelapse.gui.views import start
    start(page)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    bundled = ASSETS / "bin"
    if (bundled / ("exiftool.exe" if os.name == "nt" else "exiftool")).exists() or \
            (bundled / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")).exists():
        os.environ["PATH"] = str(bundled) + os.pathsep + os.environ.get("PATH", "")
    ft.run(main, assets_dir=str(ASSETS))
