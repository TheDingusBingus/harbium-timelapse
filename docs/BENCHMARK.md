# Benchmark: timelapse clips from a deflickering research dataset

There isn't a standard set of RAW timelapse sequences for testing deflicker tools, so for an outside check I used the 16 timelapse clips from the dataset released with the CVPR 2023 paper *Blind Video Deflickering by Neural Filtering with a Flawed Atlas* (Lei, Ren, Zhang and Chen). They're short clips from YouTube timelapses (768×432 JPEG frames) with all sorts of flicker: exposure jumps, colour swings, a few clips that hardly flicker at all, and plenty of things moving through the shot.

These are finished video frames, not RAW files, so this tests `timelapse finish` only, which is the step that works after any editor.

## Results

Lower is steadier. "Before" is the clip as it comes; "after" is the output of `timelapse finish` with default settings.

| Clip | Frames | Regional wobble | Sudden jumps | Shadows + mid-tones |
|---|---|---|---|---|
| beach | 111 | 0.017 → 0.009 | 0 → 0 | 0.009 → 0.006 |
| butterfly | 100 | 0.037 → 0.008 | 0 → 0 | 0.026 → 0.009 |
| city | 100 | 0.003 → 0.003 | 0 → 0 | 0.002 → 0.002 |
| clock1 | 348 | 0.159 → 0.019 | 39 → 2 | 0.108 → 0.024 |
| clock2 | 150 | 0.055 → 0.005 | 7 → 0 | 0.034 → 0.004 |
| disney-fountain | 114 | 0.044 → 0.004 | 8 → 0 | 0.032 → 0.010 |
| disney-lake | 127 | 0.053 → 0.009 | 9 → 0 | 0.047 → 0.020 |
| disney-subway | 91 | 0.010 → 0.009 | 0 → 0 | 0.014 → 0.016 |
| maranta | 100 | 0.090 → 0.008 | 22 → 0 | 0.058 → 0.013 |
| night2day | 100 | 0.047 → 0.006 | 4 → 0 | 0.028 → 0.004 |
| sea-cucumber | 100 | 0.146 → 0.007 | 40 → 0 | 0.084 → 0.019 |
| snow | 100 | 0.071 → 0.023 | 8 → 0 | 0.063 → 0.039 |
| snownight | 100 | 0.013 → 0.005 | 1 → 0 | 0.017 → 0.014 |
| snowzilla | 100 | 0.029 → 0.003 | 0 → 0 | 0.020 → 0.006 |
| sunset | 100 | 0.005 → 0.004 | 0 → 0 | 0.004 → 0.004 |
| tide | 100 | 0.010 → 0.006 | 0 → 0 | 0.009 → 0.009 |

In total the clips go from 138 sudden jumps to 2, and no clip gets noticeably worse. The one tiny step backwards is the shadows score on disney-subway (0.014 to 0.016), on a clip that barely flickered to begin with.

## What the scores mean

The scores deliberately don't use the tool's own measurements, so it isn't marking its own homework:

- **Regional wobble:** each frame is split into 48 patches, and the median patch's change in brightness from one frame to the next is taken, with the slow change of the light removed. This is the flicker you'd actually see.
- **Sudden jumps:** frames that jump by more than a tenth of a stop against their neighbours.
- **Shadows + mid-tones:** how much the darkest 75% of the picture wobbles, grouped by tone rather than by area. It's less useful on busy scenes, where the darkest pixels are often people walking through.

## What this run found and fixed

The first run of this benchmark showed a real weakness. On clips with very little flicker but a lot going on in one part of the picture (clouds drifting over the sky, lights coming on, waves), `finish` mistook those changes for flicker and made three clips (city, beach and tide) slightly less steady than they started. The measurement now splits each frame into regions and takes a vote: real flicker changes every part of the picture by the same amount, so a sky doing its own thing gets outvoted. The table above is after that fix. Before it, city went from 0.003 to 0.012 instead of staying put.

The vote has a natural limit: if close to half the picture really changes on its own, there's no telling from brightness alone which half is the flicker. It's exact while the changing part is a small share of the frame, and gets less accurate from about a third of the frame upwards.

## What this doesn't cover

- **Moving cameras.** All 16 clips were shot on a tripod. Hyperlapses, where the camera moves between frames, haven't been tested and probably won't work yet, because the measurement compares each pixel with the same pixel in the previous frame.
- **RAW files and `prepare`.** For those, see the dusk sequence in the README.
- **A comparison with the paper's own method.** The paper uses a different measure, and its method runs on a GPU, so the two haven't been compared side by side.

## Video output: does encoding put flicker back?

After deflickering, the frames still have to be encoded into a video, which is lossy. Two questions: does the encoder add flicker of its own, and does it cause banding in smooth skies? Measured with `benchmarks/check_encoding.py`, which decodes the video and compares every frame with the corrected frames it was made from.

**On real dusk footage** (the 229-frame sequence from the README, 960×640):

| Format | Flicker added by the encoder | Largest single step | Similarity (1 = identical) | Size |
|---|---|---|---|---|
| H.264 | 0.0043 stops | 0.018 | 0.985 | 4.7 MB |
| H.265 10-bit | 0.0016 | 0.0085 | 0.989 | 4.6 MB |
| ProRes 422 HQ | 0.0003 | 0.0027 | 0.996 | 83 MB |

None of them undoes the deflicker: even H.264's largest step is below what's visible (about 0.03 stops). H.265 10-bit adds less than half H.264's flicker at the same size. With only one keyframe in a clip this short, there was no keyframe "pumping".

**On a clean, smooth sky** (synthetic, 48 frames with flicker; "staircase" is how far a 16-row average steps away from a smooth curve, in 8-bit levels, so noise averages out but bands don't):

| | Staircase | Widest band |
|---|---|---|
| Source frames (8-bit) | 0.28 levels | 12 px |
| H.264, no dither | 0.57 | 19 px |
| H.264, dithered (the default) | 0.43 | 16 px |
| H.265 10-bit | 0.26 | 15 px |

Plain rounding plus H.264 doubles the source's banding. Dither removes about a quarter of that, but H.264 smooths some of the dither away. H.265 10-bit, fed the correction at 16-bit precision, comes out smoother than the 8-bit source itself.

## Running it yourself

Download `All-In-One-Deflicker-Dataset.zip` from the [dataset page](https://huggingface.co/datasets/xrenaa/Blind-Video-Deflickering-Dataset), unzip it, then:

```bash
python benchmarks/score_finish.py All-In-One-Deflicker-Dataset/TimeLapse/Youtube
```

## Credit

The clips come from the dataset released with Chenyang Lei, Xuanchi Ren, Zhaoxiang Zhang and Qifeng Chen, *Blind Video Deflickering by Neural Filtering with a Flawed Atlas*, CVPR 2023, shared under the [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) licence. None of the clips are included in this repository.
