# timelapse-rapidraw

Deflicker [RapidRAW](https://github.com/CyberTimon/RapidRAW) timelapses. Uses
[timelapse-core](../../README.md) to work out the corrections, then writes them
into each frame's exposure in RapidRAW's own sidecar files.

## Before you start

1. In RapidRAW, edit one frame of the sequence the way you want it.
2. Copy its settings and paste them onto every frame. This creates the sidecar
   file each frame needs.
3. **Close RapidRAW completely.** Every command that changes sidecars refuses
   to run while it's open: with the folder open, RapidRAW can save its own copy
   over them.

## Use

```bash
timelapse-rapidraw check ~/Pictures/sunset
timelapse-rapidraw deflicker ~/Pictures/sunset --dry-run   # see what would change
timelapse-rapidraw deflicker ~/Pictures/sunset
timelapse-rapidraw undo ~/Pictures/sunset                  # take deflicker back out
timelapse-rapidraw export ~/Pictures/sunset ~/Pictures/sunset-render
```

## Day to night: keyframes

When the look needs to change through the sequence (white balance, contrast,
shadows), edit a few keyframes and let the rest be blended.

```bash
timelapse-rapidraw suggest-keyframes ~/Pictures/sunset --label   # labels them purple
# in RapidRAW: edit each purple frame however you like, then close the folder
timelapse-rapidraw keyframes ~/Pictures/sunset                   # blends every setting
timelapse-rapidraw deflicker ~/Pictures/sunset                   # then deflicker on top
```

Keyframes are suggested where the light itself changes (about a stop dimmer,
or noticeably warmer or bluer), not where you moved your exposure. Move, add
or remove purple labels as you like: the suggestions are only a start.

`keyframes` **replaces** the edit on every frame between keyframes. Settings
that can't be blended (on/off switches, the tone mapper, masks shaped
differently) are held from one keyframe to the next and listed. To put
every sidecar back as it was before this tool changed it:

```bash
timelapse-rapidraw restore ~/Pictures/sunset
```

Reopen the folder in RapidRAW to see the new exposures.

## Check against a render: refine

Deflicker measures the RAW files. But a strong grade can make RapidRAW's
Exposure slider act like more than one stop of light (on one real sequence,
1.4 to 1.8 stops, changing along the sequence), so the render can still
flicker. `refine` measures your exported render and corrects that:

```bash
# export every frame from RapidRAW into a folder, then:
timelapse-rapidraw refine ~/Pictures/sunset ~/Pictures/sunset-export
# reopen, export again over the old export, and repeat until refine says you're done
```

Usually one or two passes.

Refine fixes **colour** too. If your camera was on Auto white balance, its
guess jumps from frame to frame, and RapidRAW's Temperature slider is only a
nudge from each frame's own guess, so pasting can't fix it. By default (with
ExifTool installed), refine nudges each frame so its white balance follows a
heavily smoothed curve of the camera's own: the jumps go, the auto look stays.

```bash
timelapse-rapidraw refine ... --white-balance smooth   # default with ExifTool
timelapse-rapidraw refine ... --white-balance steady   # smooth the render's colour itself
timelapse-rapidraw refine ... --white-balance first    # first frame's white balance throughout
timelapse-rapidraw refine ... --white-balance 5600     # a fixed Kelvin
timelapse-rapidraw refine ... --no-colour              # exposure only
```

Better still, next time set a fixed white balance in camera.

## If RapidRAW saves over your edits

If `check` reports that sidecars were overwritten (every edit suddenly back to
defaults), close RapidRAW and run:

```bash
timelapse-rapidraw rebuild ~/Pictures/sunset
```

It puts your keyframes back from the saved originals, re-blends between them,
and re-applies every deflicker and refine correction.

## What it will and won't touch

- `deflicker` changes only the **Exposure** slider of each frame.
- `keyframes` replaces the whole edit on frames between purple keyframes, and
  nothing on the keyframes themselves.
- Ratings and the EXIF copy are never changed. `suggest-keyframes --label`
  changes colour labels, and says which it replaced.
- Corrections are **added to** your exposure, not written over it. If you've
  ramped exposure by hand, that ramp is kept.
- Run it again and the old correction is replaced, not stacked.
- Untouched copies of your sidecars are kept in `.timelapse-core/originals/`.
- Virtual copies are left alone.

## Licence

MIT. This package contains no RapidRAW code. It talks to RapidRAW (AGPL-3.0)
only through its sidecar files and command line.
