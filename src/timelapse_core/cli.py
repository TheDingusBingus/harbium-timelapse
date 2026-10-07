"""Command line: ``timelapse check FOLDER`` and ``timelapse deflicker FOLDER``.

Read-only. Nothing here changes a photo or a sidecar.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from .deflicker import DEFAULT_WINDOW, smooth_flicker
from .measure import measure_light
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
    flick.add_argument("--blend", type=int, default=None,
                       help="frames your own exposure changes are spread across (default: a third of the sequence)")
    flick.add_argument("--csv", type=Path, help="save per-frame results to a CSV file")

    args = parser.parse_args(argv)

    def reading(i: int, n: int) -> None:
        print(f"\r  Reading camera settings {i}/{n}", end="", file=sys.stderr, flush=True)
        if i == n:
            print(file=sys.stderr)

    print(f"Looking in {args.folder}", file=sys.stderr, flush=True)
    try:
        seq = load_sequence(args.folder.expanduser(), progress=reading)
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
        brightness, warmth = measure_light(seq, speed="fast" if args.fast else "careful", progress=progress)
        fix = smooth_flicker(seq, window=args.window, strength=args.strength, blend=args.blend,
                             brightness=brightness)
    except ValueError as e:
        print(f"\n{e}", file=sys.stderr)
        return 1
    for line in describe_changes(fix):
        print(line)
    print(f"  Flicker: {fix.flicker_before:.3f} stops before, {fix.flicker_after:.3f} after (typical frame-to-frame wobble)")
    print(f"  Largest correction: {fix.largest_correction:.3f} stops")

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["frame", "brightness_stops", "target_stops", "correction_stops", "warmth"])
            for frame, b, t, e, c in zip(fix.frames, fix.brightness, fix.target, fix.exposure, warmth):
                w.writerow([frame.name, f"{b:.4f}", f"{t:.4f}", f"{e:+.4f}", f"{c:.4f}"])
        print(f"  Saved {args.csv}")
    return 0


def describe_changes(fix) -> list[str]:
    """Plain-language lines for the exposure steps deflicker found."""
    if not fix.changes:
        return []
    lines = ["  Your exposure changes, blended into a gentle ramp:"]
    for c in fix.changes:
        name = fix.frames[c.frame].name
        if c.cause == "camera settings":
            why = "camera settings"
            if c.expected is not None and abs(c.expected - c.stops) >= 0.15:
                why += f", though the settings alone suggested {c.expected:+.2f}"
        else:
            why = "no setting change, so most likely an ND filter"
        lines.append(f"    {name}: {c.stops:+.2f} stops ({why})")
    return lines


if __name__ == "__main__":
    sys.exit(main())
