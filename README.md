# harbium-timelapse

A free, open-source tool for cleaning up timelapses. It takes the flicker out of a sequence and stops the white balance jumping around between frames, and it's designed to fit around the photo editor you already use. I built it for my own work as a lighter, free alternative to LRTimelapse, and I'm sharing it in case it's useful to anyone else.

## Left: straight out of the editor. Right: using harbium-timelapse

https://github.com/user-attachments/assets/44b26f32-7309-4671-a73f-376aaa684f92

*The before has 33 frame-to-frame jumps of more than a tenth of a stop, the after has none.*


It's very much a work in progress. So far it has been tested properly on one real sequence: 229 frames from a Canon R6, shot from late afternoon into night, with a variable ND filter being turned, a few ISO changes, people walking through and light trails, edited in RapidRAW on Windows. The finished video looked smooth, and measured smooth as well. I'd like to try it on plenty more footage before claiming much beyond that, so if you give it a go, I'd love to hear how you get on.

As an outside check, `finish` has also been run on the 16 timelapse clips from a deflickering research dataset (CVPR 2023): between them they go from 138 sudden jumps to 2, and none gets noticeably worse. The details, and how to rerun it, are in [docs/BENCHMARK.md](docs/BENCHMARK.md).

It's built for timelapses shot on a tripod. Hyperlapses, where the camera moves between frames, aren't supported yet.

## How it works in practice

There are three steps:

```bash
timelapse prepare ~/Pictures/sunset
# grade the sequence in your editor, then export every frame
timelapse finish ~/Pictures/sunset-export --video sunset.mp4
```

`prepare` reads your RAW files and writes white balance and deflicker corrections into your editor's edit files, so they're in place before you start grading. It doesn't change the photos themselves, and `timelapse undo` takes out everything it added.

Then grade and export the way you normally would. JPEG exports are fine.

`finish` looks at the exported frames and smooths out any flicker or colour shift that's left, then makes a video. It can also write corrected frames if you'd rather put them together in a video editor. Because it works on the finished frames, it doesn't matter how your editor processes the image.

If `prepare` doesn't support your editor yet, you can still use the tool: grade and export as usual, then run `finish`. The `prepare` step helps most when the exposure changes a lot during a shoot (an ND filter being turned, ISO steps), because it corrects things while the RAW files still have the most room. At the moment `prepare` supports RapidRAW, and Lightroom on an experimental basis (see [docs/LIGHTROOM.md](docs/LIGHTROOM.md)). `timelapse editors` shows what's available on your install.

## Changing the look over time (keyframes)

The deflicker doesn't need keyframes. It measures how bright every frame is and fits a smooth curve through the whole sequence, so the slow change in the light (sunset into night, say) is kept and the frame-to-frame jitter is removed. Exposure changes you made while shooting are found automatically and blended into a gentle ramp. White balance is smoothed the same way.

Keyframes are for when you want your grade to change over the sequence, for example a warmer look in daylight and a cooler, darker one at night. In RapidRAW, give a few frames the purple colour label, edit those frames, and the tool blends every setting across the frames in between:

```bash
timelapse-rapidraw suggest-keyframes FOLDER   # optional: suggests where the light changes most
timelapse-rapidraw keyframes FOLDER           # blend between your purple frames
timelapse prepare FOLDER                      # then white balance and deflicker on top
```

Do the keyframe blend before `prepare`, because blending resets the deflicker. There's more detail in [docs/RAPIDRAW.md](docs/RAPIDRAW.md).

## What it can handle

- Checking a folder before doing anything, for things like missing frames, RAW and JPEG files mixed together, shots from two cameras, setting changes, and frames that are completely black or blown out.
- Frames that are randomly a touch brighter or darker, and the bigger steps you introduce yourself by changing ISO or shutter speed or turning an ND filter. ND changes aren't recorded in the file, so it finds those from the pictures.
- Auto white balance that wanders from frame to frame.
- People, cars, birds and light trails moving through the shot. Brightness is measured on the parts of the scene that stay still, so a crowd walking past won't make it think the whole frame got darker.
- Parts of the scene changing on their own, like clouds drifting over the sky or lights coming on in a building. Real flicker changes every part of the picture by the same amount, so each frame is measured region by region and a sky doing its own thing gets outvoted.
- Interruptions. Your original photos are never changed, edit files are backed up before their first change, a run that gets interrupted can simply be run again, and it won't write anything while your editor is open.

## Installing

You'll need Python 3.10 or newer. Then run:

```bash
pip install "harbium-timelapse[raw] @ git+https://github.com/TheDingusBingus/harbium-timelapse"
```

It's worth installing [ExifTool](https://exiftool.org) as well, which is used to read camera settings (Canon CR3 files need it), and [ffmpeg](https://ffmpeg.org) for making videos. Both are free. If you want to work with 16-bit TIFF exports, install `harbium-timelapse[raw,tiff]` instead.

A simple app with a window and a proper installer is on the to-do list, so you won't always need the command line.

## Commands

```text
timelapse prepare FOLDER      white balance and deflicker, written into your editor's edits
timelapse finish EXPORT       smooth the exported frames (add --video film.mp4 and/or --out FOLDER;
                              --codec h265 or prores for other video formats)
timelapse undo FOLDER         take out everything prepare added
timelapse check FOLDER        look over a folder before processing it
timelapse deflicker FOLDER    work out the corrections, for any editor (add --csv file.csv to save them)
timelapse render EXPORT       make a quick preview video
timelapse editors             list the editors prepare can write to
timelapse-rapidraw ...        extras for RapidRAW: keyframes, refine, export and rebuild
```

### Choosing a video format

`timelapse finish` can make three kinds of video with `--codec`:

- `h264` (the default) plays on practically anything. Good for sharing.
- `h265` is 10-bit H.265: about the same file size as H.264, with smoother skies and gradients. Some Windows PCs need Microsoft's HEVC extension to play it.
- `prores` is ProRes 422 HQ (10-bit, in a `.mov` file): close to lossless and much larger, made for cutting the film in a video editor.

For the 10-bit formats, the correction goes to the encoder at 16-bit precision, so it never gets rounded to 8 bits on the way. Corrected 8-bit frames and H.264 video are dithered by default, which reduces the banding that rounding can cause in smooth skies (`--no-dither` turns it off). H.264 smooths away part of the dither, so for skies where banding shows, H.265 10-bit is the better choice. The measurements behind these choices are in [docs/BENCHMARK.md](docs/BENCHMARK.md).

Most commands have a `--dry-run` option that shows what would happen without changing anything, and `--help` lists everything else.

You can also use it from Python:

```python
from harbium_timelapse import load_sequence, check_sequence, smooth_flicker

seq = load_sequence("~/Pictures/sunset")
for problem in check_sequence(seq):
    print(problem)

fix = smooth_flicker(seq)          # corrections in stops, one per frame
print(fix.by_filename())           # {"IMG_0001.CR3": 0.04, ...}
```

## How the deflicker works

For each frame, it works out how much brighter or darker the still parts of the scene are compared with the frame before. Anything that moved a lot more than the rest of the picture, like a person or a car, is left out of that comparison, along with blown highlights and pure black. RAW files are all read with exactly the same settings, so the reading itself can't introduce flicker.

It then draws a smooth line through those readings, built from short straight-line fits across each frame's neighbours (a technique called local regression). The ends of the sequence are handled properly, and a single odd frame, such as a flash of headlights, doesn't drag the frames around it. Each frame's correction is simply the gap between its reading and the line.

The details are written up in the comments in [`deflicker.py`](src/harbium_timelapse/deflicker.py) and [`measure.py`](src/harbium_timelapse/measure.py).

## Notes for RapidRAW users

If you edit in [RapidRAW](https://github.com/CyberTimon/RapidRAW), `timelapse prepare` writes straight into its `.rrdata` edit files, and white balance is matched to RapidRAW's own temperature and tint scale. Close RapidRAW before running anything that changes files.

One thing I found while testing: it's best to avoid the Dehaze slider on sequences where the exposure changes. RapidRAW works out how much haze to remove before it applies the exposure, so when a frame comes in brighter (after turning an ND filter, for instance), Dehaze works harder on it and the contrast jumps, even though the brightness has been evened out. Leaving Dehaze at zero and getting the look from contrast or curves instead avoids this.

[docs/RAPIDRAW.md](docs/RAPIDRAW.md) covers the RapidRAW extras in more detail.

## Adding another editor

Support for each editor is a small plug-in, so new ones can be added without touching the rest of the tool. RapidRAW and Lightroom are there so far, with RawTherapee and darktable on my list. If you'd like to add one yourself, [docs/EDITORS.md](docs/EDITORS.md) explains how.

## Notes for Lightroom users

Lightroom support is new and hasn't been tried inside Lightroom itself yet, so please treat it as experimental. It adds the deflicker to each frame's Exposure in the `.xmp` files, on top of your own edits, and leaves everything else in those files untouched. Because Lightroom keeps edits in its catalogue, you save the metadata to files first and read it back in afterwards; [docs/LIGHTROOM.md](docs/LIGHTROOM.md) walks through it. White balance isn't changed in Lightroom yet, so `timelapse finish` takes care of colour after export. If you try it, I'd love to know how it went.

## Plans

- Testing on more footage: sunrises, fixed white balance, other cameras
- Confirming Lightroom support with real Lightroom users, then white balance for Lightroom
- RawTherapee and darktable support
- A simple app with an installer, with ExifTool and ffmpeg included
- Better handling of very large ND filter changes in `finish`
- Hyperlapse support (lining frames up before measuring them)
- Motion blur between frames

## Credits

Plenty of good timelapse tools came first, and this one learned a lot from the problems they describe:

- [LRTimelapse](https://lrtimelapse.com), the reference for keyframing, holy grail ramping and visual deflicker.
- A [white balance smoothing script for darktable](https://discuss.pixls.us/t/fixing-white-balance-color-flicker-in-timelapse-videos-with-a-python-script-for-darktable/16251) on pixls.us, and [dtLapse](https://pypi.org/project/dtlapse/), which smooth auto white balance and keyframe edits for darktable users.

No code was taken from any of them; everything here was written from scratch. The maths is long published: local regression (LOWESS, Cleveland 1979) and monotone cubic interpolation (Fritsch and Carlson 1980).

It uses [numpy](https://numpy.org) (BSD), [Pillow](https://python-pillow.org) (MIT-CMU), [exifread](https://github.com/ianare/exif-py) (BSD) and, optionally, [rawpy](https://github.com/letmaik/rawpy) (MIT; its packages include LibRaw, under LGPL or CDDL) and [tifffile](https://github.com/cgohlke/tifffile) (BSD). [ExifTool](https://exiftool.org) and [ffmpeg](https://ffmpeg.org) are run as separate programs if you have them installed.

## Licence

MIT, so you're free to use it, change it, and build it into other things.

RapidRAW is AGPL-3.0 and isn't modified or included here. The RapidRAW support only talks to it through its own edit files and command line.
