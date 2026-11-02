# Harbium Flet check (Phase 1 experiments)

A small app that answers the questions in docs/GUI_PLAN.md, section 18, before any real app is built: can a packaged Flet app bundle our libraries and helper programs, use every CPU core, show frames fast enough, draw the timeline, play a preview, and handle the keyboard, all on your own Windows PC?

Checked in the sandbox (running as a web app in headless Chrome, Flet 1.0.5): it starts cleanly, every control and chart option exists in Flet 1.0.5, keyboard events arrive, and S3, S5 and S6 run to completion. Its speed numbers mean nothing there (software graphics, one core); yours are the real test.

## One-time setup (Windows)

1. Visual Studio 2022 Community, with "Desktop development with C++" ticked.
2. Developer Mode on: Settings > System > For developers.
3. In Git Bash, in this folder (`experiments/flet-check`):
   ```
   py -m venv .venv
   source .venv/Scripts/activate
   pip install "flet[all]==1.0.5" flet-charts==1.0.5 numpy pillow rawpy
   pip install -e ../..
   ```
4. Put `exiftool.exe` (with its `exiftool_files` folder) and `ffmpeg.exe` into `src/assets/bin/` (see the README there).

## Run it twice

**From source** (quick, no build):
```
flet run src/main.py
```

**As a real Windows app** (the actual test; the first build downloads Flutter and takes a while):
```
flet build windows
build/windows/x64/runner/Release/harbium_flet_check.exe
```
If the build complains about a package and Python 3.14, try `flet build windows --python-version 3.13`.

## In the app

Press S1 to S6 in order. S1 asks for a folder of RAW files (your dusk sequence is ideal). S2 uses the same folder: watch whether any extra windows appear. In S4, drag the smoothness slider back and forth, then press "Done with S4". In S6, press a few keys and, if you like, try Windows Narrator (Ctrl+Win+Enter). Fill in the "Note it here" lines by eye, press "Save results", and send back the file it saves in your Documents folder: one from the source run, one from the built app.

## What passes

From the plan: S1 bundled programs run and RAW files read; S2 several times faster on all cores, no extra windows, a forced stop that stops; S3 frames on screen within 100 ms (QR-6); S4 the curve follows the slider in under 150 ms (QR-4); S5 smooth at 24 frames a second; S6 keys arrive, no listening network ports in the built app.
