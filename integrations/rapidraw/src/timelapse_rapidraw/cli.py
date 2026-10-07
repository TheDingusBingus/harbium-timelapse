"""``timelapse-rapidraw``: check, suggest-keyframes, keyframes, deflicker,
undo, restore, export.

Order of work: check → suggest-keyframes → (edit purple keyframes in
RapidRAW) → keyframes → deflicker → export."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from timelapse_core import load_sequence, measure_brightness, smooth_brightness
from timelapse_core.cli import describe_changes
from timelapse_core.deflicker import DEFAULT_WINDOW

from . import apply, blend, check, label_keyframes, load, plan, restore, suggest, undo
from .keyframes import plan_blend

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
    d.add_argument("--blend", type=int, default=None,
                   help="frames your own exposure changes are spread across (default: a third of the sequence)")
    d.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")

    sub.add_parser("undo", help="remove deflicker corrections").add_argument("folder", type=Path)

    sk = sub.add_parser("suggest-keyframes", help="where keyframes belong, from how the light changes")
    sk.add_argument("folder", type=Path)
    sk.add_argument("--every", type=float, default=1.0, help="stops of light change between keyframes (default 1)")
    sk.add_argument("--warmth-every", type=float, default=0.5, help="colour shift between keyframes (default 0.5)")
    sk.add_argument("--fast", action="store_true", help="measure from embedded previews")
    sk.add_argument("--label", action="store_true", help="label the suggested frames purple")

    k = sub.add_parser("keyframes", help="blend every setting between purple keyframes, replacing in-between edits")
    k.add_argument("folder", type=Path)
    k.add_argument("--dry-run", action="store_true", help="show what would happen, write nothing")

    sub.add_parser("restore", help="put every sidecar back as it was before this tool changed it").add_argument("folder", type=Path)

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

    if args.command == "restore":
        print(f"Restored {restore(folder)} sidecars to how they were before this tool changed them. "
              f"Purple labels are left as they are. {CLOSE_FIRST}")
        return 0

    if args.command == "export":
        return _export(folder, args.output.expanduser(), args.rapidraw, args.quality)

    def reading(i, n):
        print(f"\r  Reading camera settings {i}/{n}", end="", file=sys.stderr, flush=True)
        if i == n:
            print(file=sys.stderr)

    print(f"Looking in {folder}", file=sys.stderr, flush=True)
    try:
        seq = load(folder, progress=reading)
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

    if args.command == "suggest-keyframes":
        return _suggest(seq, args, problems)
    if args.command == "keyframes":
        return _keyframes(seq, args, problems)
    if any(p.serious for p in problems) and not args.dry_run:
        print("Not writing anything until the problems above are sorted.")
        return 1

    def progress(i, n):
        print(f"\r  Measuring {i}/{n}", end="", file=sys.stderr, flush=True)
        if i == n:
            print(file=sys.stderr)

    try:
        p = plan(seq, window=args.window, strength=args.strength, blend=args.blend,
                 speed="fast" if args.fast else "careful", progress=progress)
    except ValueError as e:
        print(f"\n{e}", file=sys.stderr)
        return 1
    for line in describe_changes(p.flicker):
        print(line)
    print(f"  Flicker: {p.flicker.flicker_before:.3f} stops before, {p.flicker.flicker_after:.3f} after (typical frame-to-frame wobble)")
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


def _measuring(i, n):
    print(f"\r  Measuring {i}/{n}", end="", file=sys.stderr, flush=True)
    if i == n:
        print(file=sys.stderr)


def _suggest(seq, args, problems) -> int:
    if any(p.serious for p in problems) and args.label:
        print("Not labelling anything until the problems above are sorted.")
        return 1
    try:
        picks = suggest(seq, every=args.every, warmth_every=args.warmth_every,
                        speed="fast" if args.fast else "careful", progress=_measuring)
    except ValueError as e:
        print(f"\n{e}", file=sys.stderr)
        return 1
    print(f"  Suggested keyframes ({len(picks)}):")
    for pick in picks:
        print(f"    {seq[pick.frame].name}: {pick.reason}")
    if args.label:
        for note in label_keyframes(seq, [p.frame for p in picks]):
            print(f"  {note}")
        print(f"  Labelled purple. {CLOSE_FIRST}")
        print("  Next: open RapidRAW, edit each purple keyframe, close the folder, then run keyframes.")
    else:
        print("  Add --label to label these purple, or label your own choice in RapidRAW.")
    return 0


def _keyframes(seq, args, problems) -> int:
    if any(p.serious for p in problems):
        print("Not blending until the problems above are sorted.")
        return 1
    try:
        if args.dry_run:
            per_frame, keys, held = plan_blend(seq)
            print(f"  Would blend between {len(keys)} keyframes: {', '.join(seq[i].name for i in keys)}")
            _report_held(held)
            print("  Dry run: nothing written.")
            return 0
        result = blend(seq)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"  Blended between {len(result.keyframes)} keyframes: {', '.join(result.keyframes)}")
    print(f"  Updated {result.changed} frames.")
    _report_held(result.held)
    print("  Deflicker was reset by this. Next: timelapse-rapidraw deflicker FOLDER")
    print(f"  {CLOSE_FIRST}")
    return 0


def _report_held(held: dict[str, str]) -> None:
    """Group held settings by reason, so 40 missing settings read as one line."""
    groups: dict[str, list[str]] = {}
    for setting, why in held.items():
        groups.setdefault(why, []).append(setting)
    for why, settings in groups.items():
        shown = ", ".join(settings[:6]) + (f" and {len(settings) - 6} more" if len(settings) > 6 else "")
        if why.startswith("not set on"):
            print(f"  {len(settings)} setting{'s' if len(settings) != 1 else ''} {why.replace('not set on', 'missing from the sidecar of')}, "
                  f"so held rather than blended: {shown}")
        else:
            print(f"  Can't blend, so held from keyframe to keyframe: {shown}")


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
