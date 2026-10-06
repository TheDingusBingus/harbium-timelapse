"""Command line: ``timelapse check FOLDER`` and ``timelapse deflicker FOLDER``.

Read-only. Nothing here changes a photo or a sidecar.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from .deflicker import DEFAULT_WINDOW, smooth_flicker
from .sequencer import check_sequence, load_sequence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="timelapse", description="Free timelapse processing.")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="look over a sequence before processing")
    check.add_argument("folder", type=Path)

    flick = sub.add_parser("deflicker", help="work out per-frame exposure corrections")
    flick.add_argument("folder", type=Path)
    flick.add_argument("--window", type=int, default=DEFAULT_WINDOW,
                       help=f"frames to smooth across (default {DEFAULT_WINDOW})")
    flick.add_argument("--strength", type=float, default=1.0,
                       help="0 to 1, how much flicker to remove (default 1)")
    flick.add_argument("--fast", action="store_true",
                       help="measure from embedded previews: quicker, less exact")
    flick.add_argument("--csv", type=Path, help="save per-frame results to a CSV file")

    args = parser.parse_args(argv)
    try:
        seq = load_sequence(args.folder.expanduser())
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1

    print(f"{len(seq)} frames in {seq.folder}")
    problems = check_sequence(seq)
    for p in problems:
        print(f"  {p}")
    if args.command == "check":
        if not problems:
            print("  Looks good.")
        return 1 if any(p.serious for p in problems) else 0

    def progress(i: int, n: int) -> None:
        print(f"\r  Measuring {i}/{n}", end="", file=sys.stderr, flush=True)
        if i == n:
            print(file=sys.stderr)

    try:
        fix = smooth_flicker(seq, window=args.window, strength=args.strength,
                             speed="fast" if args.fast else "careful", progress=progress)
    except ValueError as e:
        print(f"\n{e}", file=sys.stderr)
        return 1
    print(f"  Flicker before: {fix.flicker_before:.3f} stops (typical wobble)")
    print(f"  Largest correction: {fix.largest_correction:.3f} stops")

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["frame", "brightness_stops", "target_stops", "correction_stops"])
            for frame, b, t, e in zip(fix.frames, fix.brightness, fix.target, fix.exposure):
                w.writerow([frame.name, f"{b:.4f}", f"{t:.4f}", f"{e:+.4f}"])
        print(f"  Saved {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
