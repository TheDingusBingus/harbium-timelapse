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

## Running it yourself

Download `All-In-One-Deflicker-Dataset.zip` from the [dataset page](https://huggingface.co/datasets/xrenaa/Blind-Video-Deflickering-Dataset), unzip it, then:

```bash
python benchmarks/score_finish.py All-In-One-Deflicker-Dataset/TimeLapse/Youtube
```

## Credit

The clips come from the dataset released with Chenyang Lei, Xuanchi Ren, Zhaoxiang Zhang and Qifeng Chen, *Blind Video Deflickering by Neural Filtering with a Flawed Atlas*, CVPR 2023, shared under the [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) licence. None of the clips are included in this repository.
