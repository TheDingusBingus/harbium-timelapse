"""Score `timelapse finish` on folders of frames, with measures independent of
the tool's own.

    python benchmarks/score_finish.py path/to/TimeLapse/Youtube [--out results]

Each sub-folder is one clip. For each, `finish` writes corrected frames to
``--out/<clip>``, and three scores are printed for before and after:

- regional wobble: the frame is split into 6 x 8 patches, the median patch's
  brightness change from frame to frame is taken, and the slow trend removed.
- sudden jumps: frames where that change departs from the local trend by
  more than 0.1 stops.
- shadows + mid-tones: wobble of the darkest 75% of pixels. (Unreliable on
  crowded scenes, where the darkest pixels are mostly people moving.)

Lower is steadier. Used for docs/BENCHMARK.md.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image

from harbium_timelapse.finish import LUMA, apply_finish, finished_frames, plan_finish
from harbium_timelapse.measure import _srgb_to_linear


def _luminance(path: Path) -> np.ndarray:
    return _srgb_to_linear(np.asarray(Image.open(path).convert("RGB"), dtype=np.float64) / 255) @ LUMA


def regional(frames: list[Path], rows: int = 6, cols: int = 8) -> tuple[float, int]:
    previous, changes = None, []
    for f in frames:
        a = _luminance(f)
        h, w = a.shape
        patches = np.array([[np.log2(a[r * h // rows:(r + 1) * h // rows, c * w // cols:(c + 1) * w // cols].mean() + 1e-6)
                             for c in range(cols)] for r in range(rows)])
        if previous is not None:
            changes.append(np.median(patches - previous))
        previous = patches
    d = np.array(changes)
    wobble = float(np.std(np.diff(d)) / np.sqrt(2))
    jumps = int((np.abs(d - np.convolve(d, np.ones(9) / 9, "same")) > 0.1).sum())
    return wobble, jumps


def shadows_and_mids(frames: list[Path]) -> float:
    v = []
    for f in frames:
        a = _luminance(f)
        v.append(np.log2(a[a <= np.quantile(a, 0.75)].mean() + 1e-6))
    return float(np.std(np.diff(v, 2)) / np.sqrt(6))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("clips", type=Path, help="folder whose sub-folders are clips of frames")
    ap.add_argument("--out", type=Path, default=Path("benchmark-results"))
    args = ap.parse_args()
    print(f"{'clip':16s} {'frames':>6s} | regional wobble | sudden jumps | shadows+mids")
    for clip in sorted(p for p in args.clips.iterdir() if p.is_dir()):
        frames = finished_frames(clip)
        written = apply_finish(plan_finish(frames), out=args.out / clip.name)
        (wb, jb), (wa, ja) = regional(frames), regional(written)
        print(f"{clip.name:16s} {len(frames):6d} | {wb:.3f} -> {wa:.3f}  | {jb:4d} -> {ja:<4d} | "
              f"{shadows_and_mids(frames):.3f} -> {shadows_and_mids(written):.3f}")


if __name__ == "__main__":
    main()
