# Harbium Timelapse (the app)

The desktop app for harbium-timelapse. The app's code is in the library (`src/harbium_timelapse/gui`);
this folder only starts it and holds what gets packaged: fonts, the icon, and ExifTool and ffmpeg.

## Run it from source

```
cd app
py -m venv .venv
source .venv/Scripts/activate
pip install "flet[all]==1.0.5"
pip install -e "..[raw]"
flet run src/main.py
```

## Build the Windows app

Copy `exiftool.exe` (with `exiftool_files`) and `ffmpeg.exe` into `src/assets/bin/`, then:

```
flet build windows
```

The app lands in `build/windows`. It downloads the library from GitHub while building, so push first.

The fonts (Cormorant Garamond, Inter, DM Mono) are under the SIL Open Font Licence; see `src/assets/fonts`.
