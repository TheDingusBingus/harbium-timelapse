# timelapse-core

Free, open timelapse processing, written in the language photographers already use.

Timelapse sequences flicker. Exposure wobbles a fraction of a stop from frame to frame, and in a finished video that wobble reads as a shimmer. Fixing it properly has mostly meant paid tools. This library is a small attempt to do the same job for free, as something other editors can call rather than another app to learn.

It's early. Deflicker works, handles the exposure changes you make while shooting, and can write straight into RapidRAW.

## What it does now

- **Checks a sequence** before you process it: missing frames, mixed RAW+JPEG folders, exposure setting changes, unreadable files. In sentences, not error codes.
- **Measures brightness** of every frame, in stops, from the RAW data itself.
- **Works out deflicker corrections**: how much to brighten or darken each frame so the light follows its real arc without the shimmer.
- **Smooths out your own exposure changes.** ISO steps, shutter changes, and hand-turned ND filters (found from the pictures, since an ND leaves no trace in the file) are blended into one gentle ramp instead of jolts.

It never touches your original files.

## Install

```bash
pip install "timelapse-core[raw] @ git+https://github.com/[[ username ]]/timelapse-core"
```

For Canon CR3 files (and generally the most reliable settings reading), also install [ExifTool](https://exiftool.org). It's free.

## Use

From the command line:

```bash
timelapse check ~/Pictures/sunset
timelapse deflicker ~/Pictures/sunset --csv corrections.csv
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

1. Each frame's brightness is measured in stops. Blown highlights and pure blacks are left out, so a clipping sky doesn't drag the reading.
2. A smooth line is drawn through those readings. Each point on it comes from a short straight line fitted through that frame's neighbours, closest neighbours trusted most. The ends of the sequence don't droop, and a single odd frame (lightning, headlights) is recognised and doesn't pull its neighbours with it.
3. Each frame's correction is the gap between where it measured and where the line says it should be.

The full reasoning lives in the docstrings of [`deflicker.py`](src/timelapse_core/deflicker.py) and [`measure.py`](src/timelapse_core/measure.py).

## RapidRAW

If you edit in [RapidRAW](https://github.com/CyberTimon/RapidRAW), `timelapse-rapidraw` writes the corrections into each frame's exposure for you. See [integrations/rapidraw](integrations/rapidraw/README.md).

## Plans

- Marking where the light changes by hand, for sequences where automatic isn't enough
- Faster measuring, using every CPU core
- Motion blur between frames

## Licence

MIT. Use it, change it, build it into something else.

RapidRAW is AGPL-3.0 and is not modified or included here. Integrations talk to it only through its own sidecar files and command line.
