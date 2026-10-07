"""``timelapse-rapidraw``: check, suggest-keyframes, keyframes, deflicker,
undo, restore, export.

Order of work: check → suggest-keyframes → (edit purple keyframes in
RapidRAW) → keyframes → deflicker → export → refine → export → refine…"""

from __future__ import annotations

import argparse

import numpy as np
import shutil
import subprocess
import sys
from pathlib import Path

from timelapse_core import load_sequence, measure_brightness, smooth_brightness
from timelapse_core.cli import describe_changes
from timelapse_core.deflicker import DEFAULT_WINDOW

from . import apply, blend, check, label_keyframes, load, plan, rebuild, refine, restore, suggest, undo
from .recover import RUNNING_MESSAGE, rapidraw_running
from .whitebalance import DEFAULT_SMOOTHNESS, apply_white_balance, plan_white_balance, undo_white_balance
from .keyframes import plan_blend

CLOSE_FIRST = "Reopen RapidRAW to see the changes."


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="timelapse-rapidraw",
                                 description="Deflicker RapidRAW timelapses. Close RapidRAW before any command that changes sidecars.")
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

    u = sub.add_parser("undo", help="remove deflicker and refine corrections")
    u.add_argument("folder", type=Path)
    u.add_argument("--colour", action="store_true", help="remove only refine's Temperature corrections; keep exposure")

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

    rf = sub.add_parser("refine", help="improve deflicker using an exported render of the sequence")
    rf.add_argument("folder", type=Path, help="the RAW sequence")
    rf.add_argument("export", type=Path, help="folder of exported frames (JPEG/PNG/TIFF), every frame, in order")
    rf.add_argument("--dry-run", action="store_true", help="measure and report, write nothing")
    rf.add_argument("--colour", action="store_true",
                    help="also correct colour via the relative Temperature slider (experimental; "
                         "for camera white balance jumps, use the white-balance command instead)")
    rf.add_argument("--white-balance", default="auto",
                    help='"smooth" (default with ExifTool: follow a heavily smoothed curve of the camera\'s '
                         'own as-shot Kelvin), "steady" (smooth the render\'s colour itself), "first" '
                         '(the first frame\'s white balance throughout), or a Kelvin number, e.g. 5600')

    w = sub.add_parser("white-balance", help="give every frame a steady white balance, in Kelvin")
    w.add_argument("folder", type=Path)
    w.add_argument("--to", default="smooth",
                   help='"smooth" (default: the camera\'s as-shot Kelvin, heavily smoothed), '
                        '"first" (the first frame\'s), or a Kelvin number, e.g. 5600')
    w.add_argument("--smoothness", type=int, default=DEFAULT_SMOOTHNESS,
                   help=f"frames to smooth across (default {DEFAULT_SMOOTHNESS}); more is calmer, less follows the light more closely")
    w.add_argument("--tint", type=float, default=0.0, help="tint to set alongside (default 0)")
    w.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
    w.add_argument("--undo", action="store_true", help="put back each frame's previous white balance")

    sub.add_parser("rebuild", help="after RapidRAW overwrites sidecars: keyframes back, re-blend, re-apply every correction").add_argument("folder", type=Path)

    for name, command in sub.choices.items():
        if name not in ("check", "export"):
            command.add_argument("--force", action="store_true", help="run even if RapidRAW seems to be open")

    e = sub.add_parser("export", help="render with RapidRAW, then measure the flicker left")
    e.add_argument("folder", type=Path)
    e.add_argument("output", type=Path)
    e.add_argument("--rapidraw", default=None, help="path to the rapidraw command, if not on PATH")
    e.add_argument("--quality", type=int, default=95)

    args = ap.parse_args(argv)
    folder = args.folder.expanduser()

    writes = args.command in ("deflicker", "keyframes", "refine", "undo", "restore", "rebuild", "white-balance") \
        or (args.command == "suggest-keyframes" and args.label)
    if writes and not getattr(args, "dry_run", False) and not getattr(args, "force", False) and rapidraw_running():
        print(RUNNING_MESSAGE, file=sys.stderr)
        return 1

    if args.command == "undo":
        if args.colour:
            print(f"Removed refine's Temperature corrections from {undo(folder, colour_only=True)} frames; "
                  f"exposure corrections kept. {CLOSE_FIRST}")
        else:
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

    if args.command == "white-balance":
        return _white_balance(seq, args)
    if args.command == "rebuild":
        try:
            frames, keys = rebuild(seq)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 1
        print(f"  Rebuilt {frames} sidecars: your {keys} keyframes from the saved originals, "
              "the blend between them, and every deflicker and refine correction.")
        print(f"  {CLOSE_FIRST}")
        return 0
    if args.command == "refine":
        return _refine(seq, args)
    if args.command == "suggest-keyframes":
        return _suggest(seq, args, problems)
    if args.command == "keyframes":
        return _keyframes(seq, args, problems)
    if any(p.serious and p.kind != "overwritten" for p in problems) and not args.dry_run:
        print("Not writing anything until the problems above are sorted.")
        return 1
    if any(p.kind == "overwritten" for p in problems) and not args.dry_run:
        print("Run rebuild first: deflicker on top of overwritten sidecars would build on RapidRAW's defaults.")
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


def _white_balance(seq, args) -> int:
    if args.undo:
        print(f"  Put back the previous white balance on {undo_white_balance(seq)} frames. {CLOSE_FIRST}")
        return 0
    to = args.to
    if to not in ("smooth", "first"):
        try:
            to = float(to)
        except ValueError:
            print('--to must be "smooth", "first" or a Kelvin number like 5600', file=sys.stderr)
            return 1
    try:
        wb = plan_white_balance(seq, to, smoothness=args.smoothness, tint=args.tint)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    if np.isfinite(wb.as_shot).all():
        print(f"  Camera's white balance: {np.nanmin(wb.as_shot):.0f}-{np.nanmax(wb.as_shot):.0f} K, "
              f"jumping up to {wb.biggest_jump_before:.0f} K between frames.")
    print(f"  New: {wb.kelvin.min():.0f}-{wb.kelvin.max():.0f} K, changing at most "
          f"{wb.biggest_jump_after:.0f} K between frames. Tint {wb.tint:g}.")
    if args.dry_run:
        step = max(1, len(seq) // 8)
        for i in range(0, len(seq), step):
            shot = f"{wb.as_shot[i]:.0f} K" if np.isfinite(wb.as_shot[i]) else "unknown"
            print(f"    {seq[i].name}: camera {shot} -> {wb.kelvin[i]:.0f} K")
        print("  Dry run: nothing written.")
        return 0
    print(f"  Wrote white balance to {apply_white_balance(seq, wb)} frames. Your Temperature and Tint "
          "sliders are left as they are, on top.")
    print("  Undo with: timelapse-rapidraw white-balance FOLDER --undo")
    print(f"  {CLOSE_FIRST}")
    return 0


def _refine(seq, args) -> int:
    wb = args.white_balance
    if wb not in ("auto", "smooth", "steady", "first"):
        try:
            wb = float(wb)
        except ValueError:
            print('--white-balance must be "smooth", "steady", "first" or a Kelvin number like 5600', file=sys.stderr)
            return 1
    if any(p.kind == "overwritten" for p in check(seq)):
        print("Run rebuild first: these sidecars were overwritten since the last pass.", file=sys.stderr)
        return 1
    try:
        result = refine(seq, args.export.expanduser(), progress=_measuring, dry_run=args.dry_run,
                        colour=args.colour, white_balance=wb)
    except ValueError as e:
        print(f"\n{e}", file=sys.stderr)
        return 1
    print(f"  Pass {result.pass_number}. Flicker in the render: {result.flicker_in_render:.3f} stops"
          + ("" if result.colour_in_render != result.colour_in_render
             else f", colour {result.colour_in_render:.3f}"))
    if result.nothing_to_do:
        print("  That's below what anyone can see, in brightness and colour. Nothing to refine: you're done.")
        return 0
    if result.slider_strength == result.slider_strength:
        print(f"  In this grade, +1 on Exposure moves the render about {result.slider_strength:.1f} stops.")
    else:
        print("  Brightness is already steady; adjusting colour only.")
    if result.colour_strength == result.colour_strength:
        if result.colour_guessed:
            print("  Temperature: a careful first step, to learn how RapidRAW's slider behaves. "
                  "The next pass corrects properly.")
        else:
            print(f"  Temperature: +10 shifts the render's colour by about {10 * result.colour_strength:+.3f}.")
    if args.dry_run:
        print("  Dry run: nothing written.")
        return 0
    print(f"  Updated {result.changed} sidecars.")
    print("  Next: reopen in RapidRAW, export again (replacing the old export), then run refine again.")
    print("  Stop once refine says the flicker is below what anyone can see.")
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
