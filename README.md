# timelapse-core

Free, open timelapse processing, written in the language photographers already use.

Timelapse sequences flicker. Exposure wobbles a fraction of a stop from frame to frame, and in a finished video that wobble reads as a shimmer. Fixing it properly has mostly meant paid tools. This library is a small attempt to do the same job for free, as something other editors can call rather than another app to learn.

It's early, and honest about it: proven on one real sequence so far (229 frames, Canon R6, dusk into night, ND turns, ISO steps, crowds, light trails, RapidRAW on Windows). On that sequence the finished video measured **0.012 stops** of frame-to-frame brightness change (the light's own pace at dusk, i.e. no flicker left) and **0.006** of colour wobble, from **one export and one command**. A second sequence is next before any bigger claims.

## The workflow

1. **Deflicker and steady the white balance on the RAWs** (in RapidRAW: `timelapse-rapidraw deflicker` and `timelapse-rapidraw white-balance`; in any editor: apply the corrections `timelapse deflicker` works out). This does the heavy lifting before your grade, where highlights and shadows still have room.
2. **Export once**, from whatever editor you use.
3. **`timelapse finish`** on the exported frames: it measures each frame and takes out the last flicker and colour wobble directly on the pixels, then writes a video and/or corrected frames.

Step 3 works after *any* editor: it doesn't need to know how your editor's sliders behave, so tone mappers, dehaze and the rest can't trip it up.

## What it does now

- **Checks a sequence** before you process it: missing frames, mixed RAW+JPEG folders, exposure setting changes, unreadable files. In sentences, not error codes.
- **Measures brightness** of every frame, in stops, from the RAW data itself.
- **Works out deflicker corrections**: how much to brighten or darken each frame so the light follows its real arc without the shimmer.
- **Suggests keyframes** where the light itself changes, in brightness or colour, and blends values smoothly between them.
- **Finishes exported frames** (`timelapse finish`): one brightness and colour correction per frame, measured on the still part of the scene so walkers, cars and light trails can't sway it. No masks, nothing local, no halos.
- **Steady white balance in RapidRAW**, exact to RapidRAW's own Kelvin and tint scale, from each frame's raw colour.
- **Smooths out your own exposure changes.** ISO steps, shutter changes, and hand-turned ND filters (found from the pictures, since an ND leaves no trace in the file) are blended into one gentle ramp instead of jolts.

It never touches your original files.

## Install

```bash
pip install "timelapse-core[raw] @ git+https://github.com/[[ username ]]/timelapse-core"
```

For Canon CR3 files (and generally the most reliable settings reading), also install [ExifTool](https://exiftool.org). It's free. For videos, install [ffmpeg](https://ffmpeg.org). For 16-bit TIFF exports, add the `tiff` extra (`timelapse-core[raw,tiff]`).

## Use

From the command line:

```bash
timelapse check ~/Pictures/sunset
timelapse deflicker ~/Pictures/sunset --csv corrections.csv
timelapse render ~/Pictures/sunset-exported --fps 24      # quick preview video (needs ffmpeg)
timelapse render ~/Pictures/sunset-exported --max-mb 10   # under 10 MB, for sending
timelapse finish ~/Pictures/sunset-exported --video sunset.mp4        # last flicker + colour wobble out, straight to video
timelapse finish ~/Pictures/sunset-exported --out ~/Pictures/sunset-final   # or corrected frames for your video editor
# Optional, for maximum precision in very dark sequences: correct 16-bit TIFFs, write JPEGs
timelapse finish ~/Pictures/sunset-tiff16 --format jpg --out ~/Pictures/sunset-final --video sunset.mp4
```

From Python:

```python
from timelapse_core import load_sequence, check_sequence, smooth_flicker

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

The full reasoning lives in the docstrings of [`deflicker.py`](src/timelapse_core/deflicker.py) and [`measure.py`](src/timelapse_core/measure.py).

## RapidRAW

If you edit in [RapidRAW](https://github.com/CyberTimon/RapidRAW), `timelapse-rapidraw` writes the corrections into each frame's exposure for you. See [integrations/rapidraw](integrations/rapidraw/README.md).

## Pitfalls we measured (RapidRAW)

- **Don't use Dehaze on a sequence with exposure changes.** RapidRAW estimates haze from the image *before* Exposure is applied, so when an ND turn or ISO step makes the RAW brighter, Dehaze works harder and the midtones and shadows jump, even though Exposure evens out the brightness. Measured on a real ND turn: tone-shape jump +0.42 / −0.27 stops with Dehaze, −0.05 / +0.06 without.
- RapidRAW's Exposure slider moved the render ~18% more than the same amount of real light did on the test sequence. Corrections sized from the RAW get close; `timelapse finish` (or `timelapse-rapidraw refine`) takes care of the rest.

## Plans

- A second, different test sequence before bigger claims
- Patch-weighted measuring in `finish` (small steps can survive at big ND turns)
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

RapidRAW is AGPL-3.0 and is not modified or included here. Integrations talk to it only through its own sidecar files and command line.
