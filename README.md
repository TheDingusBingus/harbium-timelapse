# harbium-timelapse

Free timelapse deflicker and steady white balance, **measured, not guessed**. An LRTimelapse alternative that works with any editor.

Timelapses flicker: exposure wobbles a fraction of a stop from frame to frame, and the camera's auto white balance jumps around, so a finished video shimmers and shifts colour. This tool removes that, with two commands and one export.

It's early, and honest about it: proven on one real sequence so far (229 frames, Canon R6, dusk into night, ND turns, ISO steps, crowds, light trails, RapidRAW on Windows). The finished video measured **0.012 stops** of frame-to-frame brightness change (the light's own pace at dusk: no flicker left) and **0.006** of colour wobble. A second sequence is next before any bigger claims.

## Three steps

```bash
timelapse prepare  ~/Pictures/sunset                         # 1. steady white balance + deflicker, into your editor's edits
#                                                            # 2. grade as you like, export every frame (JPEG is fine)
timelapse finish   ~/Pictures/sunset-export --video sunset.mp4   # 3. last wobble out, straight to video
```

1. **`prepare`** finds your editor's edit files, steadies the white balance and removes the flicker *before* your grade, where highlights and shadows still have room. It never touches the photos themselves, and `timelapse undo` takes it all back out.
2. **Export once**, from your editor, with your look on it.
3. **`finish`** measures every exported frame and takes out what's left, directly on the pixels. It doesn't need to know how your editor's sliders behave, so tone mappers and "smart" tools can't trip it up.

**Using an editor `prepare` doesn't support yet?** Skip step 1: grade, export, run `finish`. Step 1 matters most for big exposure changes (ND turns, ISO steps). `timelapse editors` shows what's supported; [adding an editor](docs/EDITORS.md) is a small class.

## What it does

- **Checks a sequence** first: missing frames, mixed RAW+JPEG, two cameras, exposure setting changes, frames that are black or blown. In sentences, not error codes.
- **Measures the still part of the scene**, so walkers, cars, birds and light trails don't sway it, and from the RAW data with identical scaling for every frame.
- **Removes flicker and keeps the light's real journey** (sunset, blue hour, night), and smooths out your own exposure changes: ISO and shutter steps, and hand-turned ND filters (found from the pictures, since an ND leaves no trace in the file).
- **Steadies auto white balance** frame to frame, exactly on the editor's own Kelvin and tint scale.
- **Finishes exported frames**: one brightness and colour correction per frame. No masks, nothing local, no halos.
- Safe by design: originals untouched, edit files backed up before the first change, interrupted runs can simply be run again, and nothing is written while your editor is open.

## Install

```bash
pip install "harbium-timelapse[raw] @ git+https://github.com/[[ username ]]/harbium-timelapse"
```

Also install [ExifTool](https://exiftool.org) (camera settings; needed for Canon CR3) and [ffmpeg](https://ffmpeg.org) (videos). Both are free. For 16-bit TIFF exports, use `harbium-timelapse[raw,tiff]`.

## All commands

```bash
timelapse prepare  FOLDER [--dry-run] [--no-white-balance] [--editor rapidraw]
timelapse finish   EXPORT --video film.mp4 [--out FOLDER] [--format jpg] [--width 3840] [--brightness-only]
timelapse undo     FOLDER
timelapse check    FOLDER                 # look before you leap
timelapse deflicker FOLDER --csv c.csv    # just the numbers, for any editor
timelapse render   EXPORT --max-mb 10     # quick preview video
timelapse editors                         # which editors prepare can write to
timelapse-rapidraw ...                    # RapidRAW extras: keyframes, refine, export, rebuild (docs/RAPIDRAW.md)
```

From Python:

```python
from harbium_timelapse import load_sequence, check_sequence, smooth_flicker

seq = load_sequence("~/Pictures/sunset")

for problem in check_sequence(seq):
    print(problem)

fix = smooth_flicker(seq)                  # careful, from the RAW data
fix = smooth_flicker(seq, window=40)       # smoother
fix = smooth_flicker(seq, strength=0.5)    # remove half, keep some life
fix.by_filename()                          # {"IMG_0001.CR3": +0.04, ...} in stops
```

## How deflicker works

1. Each frame's brightness change is measured in stops on the **still part of the scene**: every pixel is compared with the same pixel in the previous frame, pixels that moved far more than the rest (walkers, cars, birds, light trails) are set aside, and blown highlights and pure blacks are left out. RAW files are all decoded with identical scaling (LibRaw's per-frame "auto maximum" is switched off: left on, it invents flicker).
2. A smooth line is drawn through those readings. Each point on it comes from a short straight line fitted through that frame's neighbours, closest neighbours trusted most. The ends of the sequence don't droop, and a single odd frame (lightning, headlights) is recognised and doesn't pull its neighbours with it.
3. Each frame's correction is the gap between where it measured and where the line says it should be.

The full reasoning lives in the docstrings of [`deflicker.py`](src/harbium_timelapse/deflicker.py) and [`measure.py`](src/harbium_timelapse/measure.py).

## RapidRAW

If you edit in [RapidRAW](https://github.com/CyberTimon/RapidRAW), `timelapse prepare` writes into its `.rrdata` edit files directly. `timelapse-rapidraw` adds keyframes (purple labels, every setting blended between them), `refine`, headless `export` and `rebuild`. See [docs/RAPIDRAW.md](docs/RAPIDRAW.md).

## Pitfalls we measured (RapidRAW)

- **Don't use Dehaze on a sequence with exposure changes.** RapidRAW estimates haze from the image *before* Exposure is applied, so when an ND turn or ISO step makes the RAW brighter, Dehaze works harder and the midtones and shadows jump, even though Exposure evens out the brightness. Measured on a real ND turn: tone-shape jump +0.42 / −0.27 stops with Dehaze, −0.05 / +0.06 without.
- RapidRAW's Exposure slider moved the render ~18% more than the same amount of real light did on the test sequence. Corrections sized from the RAW get close; `timelapse finish` (or `timelapse-rapidraw refine`) takes care of the rest.

## Plans

- A second, different test sequence before bigger claims
- Lightroom / Camera Raw adapter (XMP), then RawTherapee and darktable
- A simple window (no command line) and a one-click installer with ExifTool and ffmpeg included
- Patch-weighted measuring in `finish` (small steps can survive at big ND turns)
- Camera-settings cache, so repeat runs on a slow drive are near-instant
- Faster measuring, using every CPU core
- Motion blur between frames

## Standing on shoulders

Good timelapse tools came first, and this one learned from the problems they
describe:

- [LRTimelapse](https://lrtimelapse.com), the reference for keyframes, holy
  grail ramping and visual deflicker.
- A [white balance smoothing script for darktable](https://discuss.pixls.us/t/fixing-white-balance-color-flicker-in-timelapse-videos-with-a-python-script-for-darktable/16251)
  on pixls.us, and [dtLapse](https://pypi.org/project/dtlapse/), which smooth
  Auto white balance and keyframe edits for darktable users.

No code was taken from any of them: everything here is written from scratch.
The maths it uses is long published: local regression (LOWESS, Cleveland 1979)
and monotone cubic interpolation (Fritsch and Carlson 1980).

It relies on, without including: [numpy](https://numpy.org) (BSD),
[Pillow](https://python-pillow.org) (MIT-CMU),
[exifread](https://github.com/ianare/exif-py) (BSD), and optionally
[rawpy](https://github.com/letmaik/rawpy) (MIT; its wheels bundle LibRaw, under
LGPL or CDDL). [ExifTool](https://exiftool.org) and [ffmpeg](https://ffmpeg.org)
are run as separate programs if you have them installed.

## Licence

MIT. Use it, change it, build it into something else.

RapidRAW is AGPL-3.0 and is not modified or included here. The adapter talks to it only through its own edit files and command line.
