"""``timelapse-rapidraw check | deflicker | undo | export``"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from timelapse_core import load_sequence, measure_brightness, smooth_brightness
from timelapse_core.deflicker import DEFAULT_WINDOW

from . import apply, check, load, plan, undo

CLOSE_FIRST = ("Close this folder in RapidRAW before running, and reopen it after. "
               "RapidRAW may otherwise save its own copy over the new exposures.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="timelapse-rapidraw",
                                 description="Deflicker RapidRAW timelapses. " + CLOSE_FIRST)
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="look over a sequence and its RapidRAW edits").add_argument("folder", type=Path)

    d = sub.add_parser("deflicker", help="write deflicker corrections into the sidecars")
    d.add_argument("folder", type=Path)
    d.add_argument("--window", type=int, default=DEFAULT_WINDOW, help=f"frames to smooth across (default {DEFAULT_WINDOW})")
    d.add_argument("--strength", type=float, default=1.0, help="0 to 1 (default 1)")
    d.add_argument("--fast", action="store_true", help="measure from embedded previews")
    d.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")

    sub.add_parser("undo", help="remove deflicker corrections").add_argument("folder", type=Path)

    e = sub.add_parser("export", help="render with RapidRAW, then measure the flicker left")
    e.add_argument("folder", type=Path)
    e.add_argument("output", type=Path)
    e.add_argument("--rapidraw", default=None, help="path to the rapidraw command, if not on PATH")
    e.add_argument("--quality", type=int, default=95)

    args = ap.parse_args(argv)
    folder = args.folder.expanduser()

    if args.command == "undo":
        print(f"Removed deflicker from {undo(folder)} frames. {CLOSE_FIRST}")
        return 0

    if args.command == "export":
        return _export(folder, args.output.expanduser(), args.rapidraw, args.quality)

    try:
        seq = load(folder)
    except ValueError as err:
        print(err, file=sys.stderr)
        return 1
    print(f"{len(seq)} frames in {seq.folder}")
    problems = check(seq)
    for p in problems:
        print(f"  {p}")
    if args.command == "check":
        if not problems:
            print("  Looks good.")
        return 1 if any(p.serious for p in problems) else 0
    if any(p.serious for p in problems) and not args.dry_run:
        print("Not writing anything until the problems above are sorted.")
        return 1

    def progress(i, n):
        print(f"\r  Measuring {i}/{n}", end="", file=sys.stderr, flush=True)
        if i == n:
            print(file=sys.stderr)

    try:
        p = plan(seq, window=args.window, strength=args.strength,
                 speed="fast" if args.fast else "careful", progress=progress)
    except ValueError as e:
        print(f"\n{e}", file=sys.stderr)
        return 1
    print(f"  Flicker found: {p.flicker.flicker_before:.3f} stops (typical wobble)")
    print(f"  Largest correction: {p.flicker.largest_correction:.3f} stops")
    if args.dry_run:
        for fp in p.frames[:10]:
            print(f"    {fp.frame.name}: exposure {fp.base:+.2f} → {fp.exposure:+.4f}")
        if len(p.frames) > 10:
            print(f"    … and {len(p.frames) - 10} more")
        print("  Dry run: nothing written.")
        return 0
    print(f"  Updated {apply(p)} sidecars. Undo any time with: timelapse-rapidraw undo {folder}")
    print(f"  {CLOSE_FIRST}")
    return 0


def _export(folder: Path, output: Path, rapidraw: str | None, quality: int) -> int:
    exe = rapidraw or shutil.which("rapidraw")
    if not exe:
        print("Can't find the rapidraw command. Pass its location with --rapidraw.", file=sys.stderr)
        return 1
    output.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([exe, "export", str(folder), "--output", str(output),
                             "--format", "jpeg", "--quality", str(quality)])
    if result.returncode != 0:
        print("RapidRAW's export didn't finish cleanly.", file=sys.stderr)
        return result.returncode
    rendered = load_sequence(output)
    seen = measure_brightness(rendered)
    left = float((seen - smooth_brightness(seen)).std())
    print(f"Exported {len(rendered)} frames. Flicker left in the render: {left:.3f} stops.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
