# timelapse-rapidraw

Deflicker [RapidRAW](https://github.com/CyberTimon/RapidRAW) timelapses. Uses
[timelapse-core](../../README.md) to work out the corrections, then writes them
into each frame's exposure in RapidRAW's own sidecar files.

## Before you start

1. In RapidRAW, edit one frame of the sequence the way you want it.
2. Copy its settings and paste them onto every frame. This creates the sidecar
   file each frame needs.
3. Close the folder in RapidRAW.

## Use

```bash
timelapse-rapidraw check ~/Pictures/sunset
timelapse-rapidraw deflicker ~/Pictures/sunset --dry-run   # see what would change
timelapse-rapidraw deflicker ~/Pictures/sunset
timelapse-rapidraw undo ~/Pictures/sunset                  # take it back out
timelapse-rapidraw export ~/Pictures/sunset ~/Pictures/sunset-render
```

Reopen the folder in RapidRAW to see the new exposures.

## What it will and won't touch

- Only the **Exposure** slider of each frame. Every other setting, rating and tag
  stays exactly as it was.
- Corrections are **added to** your exposure, not written over it. If you've
  ramped exposure by hand, that ramp is kept.
- Run it again and the old correction is replaced, not stacked.
- Untouched copies of your sidecars are kept in `.timelapse-core/originals/`.
- Virtual copies are left alone.

## Licence

MIT. This package contains no RapidRAW code. It talks to RapidRAW (AGPL-3.0)
only through its sidecar files and command line.
