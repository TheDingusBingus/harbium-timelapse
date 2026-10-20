"""Command line: ``timelapse prepare | finish | undo | check | deflicker | render | editors``.

Nothing here changes a photo or a sidecar. ``finish`` writes corrected copies
of exported frames (and/or a video) to a new place; the export is untouched.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from .deflicker import DEFAULT_WINDOW, check_window, smooth_flicker
from .measure import measure_light
from .sequencer import check_sequence, load_sequence


def main(argv: list[str] | None = None) -> int:
    """Bad input ends as a sentence, never a traceback."""
    try:
        return _main(argv)
    except ValueError as e:
        print(f"\n{e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130


def _fraction(text: str) -> float:
    """argparse type: a number from 0 to 1, checked before any slow work."""
    value = float(text)
    if not 0.0 <= value <= 1.0:
        raise argparse.ArgumentTypeError(f"must be between 0 and 1 (got {text})")
    return value


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="timelapse", description="Free timelapse processing.")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="look over a sequence before processing")
    check.add_argument("folder", type=Path)

    flick = sub.add_parser("deflicker", help="work out per-frame exposure corrections")
    flick.add_argument("folder", type=Path)
    flick.add_argument("--window", type=int, default=DEFAULT_WINDOW,
                       help=f"frames to smooth across (default {DEFAULT_WINDOW})")
    flick.add_argument("--strength", type=_fraction, default=1.0,
                       help="0 to 1, how much flicker to remove (default 1)")
    flick.add_argument("--fast", action="store_true",
                       help="measure from embedded previews: quicker, less exact")
    flick.add_argument("--blend", type=int, default=None,
                       help="frames your own exposure changes are spread across (default: a third of the sequence)")
    flick.add_argument("--csv", type=Path, help="save per-frame results to a CSV file")

    rend = sub.add_parser("render", help="make a quick preview video from finished frames (needs ffmpeg)")
    rend.add_argument("folder", type=Path)
    rend.add_argument("--fps", type=float, default=24, help="frames per second (default 24)")
    rend.add_argument("--width", type=int, default=None, help="scale to this width, e.g. 3840 (default: native)")
    rend.add_argument("--out", type=Path, default=None, help="video file to write (default: beside the folder)")
    rend.add_argument("--max-mb", type=float, default=None,
                      help="keep the file under this size, e.g. 10, for sending (full HD unless --width is given)")
    rend.add_argument("--blend-frames", type=int, default=1,
                      help="mix each frame with its neighbours, 2 or 3, to calm moving leaves (default 1: off)")

    fin = sub.add_parser("finish", help="take the last flicker and colour wobble out of exported frames, "
                                        "then write corrected frames and/or a video")
    fin.add_argument("folder", type=Path, help="your exported frames (JPEG, PNG or TIFF), every frame, in order")
    fin.add_argument("--out", type=Path, default=None, help="folder for corrected frames (same names and formats)")
    fin.add_argument("--video", type=Path, default=None, help="MP4 to make straight from the corrected frames")
    fin.add_argument("--fps", type=float, default=24, help="frames per second for the video (default 24)")
    fin.add_argument("--width", type=int, default=None, help="video width, e.g. 3840 (default: native)")
    fin.add_argument("--window", type=int, default=DEFAULT_WINDOW,
                     help=f"frames to smooth across (default {DEFAULT_WINDOW})")
    fin.add_argument("--format", default=None, choices=["jpg", "png", "tif"],
                     help="write corrected frames in this format (default: same as the export). "
                          "For dark sequences: export 16-bit TIFF, write jpg")
    fin.add_argument("--brightness-only", action="store_true", help="leave colour exactly as exported")
    fin.add_argument("--dry-run", action="store_true", help="measure and report, write nothing")
    fin.add_argument("--csv", type=Path, help="save per-frame measurements and corrections")

    prep = sub.add_parser("prepare", help="steady the white balance and deflicker, written straight into your "
                                          "editor's edit files (detected automatically)")
    prep.add_argument("folder", type=Path, help="the folder of RAW files you edit")
    prep.add_argument("--editor", default="auto", help="which editor (default: detect from the files)")
    prep.add_argument("--window", type=int, default=DEFAULT_WINDOW, help=f"frames to smooth across (default {DEFAULT_WINDOW})")
    prep.add_argument("--strength", type=_fraction, default=1.0, help="0 to 1, how much flicker to remove (default 1)")
    prep.add_argument("--no-white-balance", action="store_true", help="leave white balance as it is")
    prep.add_argument("--dry-run", action="store_true", help="measure and report, write nothing")
    prep.add_argument("--force", action="store_true", help="write even if the editor seems open or the folder has problems")

    und = sub.add_parser("undo", help="take everything prepare wrote back out")
    und.add_argument("folder", type=Path)
    und.add_argument("--editor", default="auto")
    und.add_argument("--force", action="store_true", help="even if the editor seems open")

    sub.add_parser("editors", help="list the editors this installation can write to")

    args = parser.parse_args(argv)

    if args.command == "editors":
        return _editors()
    if args.command in ("prepare", "undo"):
        return _prepare_or_undo(args)
    if args.command == "render":
        return _render(args)
    if args.command == "finish":
        return _finish(args)

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
    if fix.unmeasured:
        print("  " + describe_unmeasured([fix.frames[i].name for i in fix.unmeasured]))
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


def _render(args) -> int:
    from .render import render_video

    def progress(i: int, n: int) -> None:
        print(f"\r  Rendering {i}/{n}", end="", file=sys.stderr, flush=True)
        if i == n:
            print(file=sys.stderr)

    try:
        video = render_video(args.folder.expanduser(), args.out, fps=args.fps, width=args.width,
                             progress=progress, blend_frames=args.blend_frames, max_mb=args.max_mb)
    except (RuntimeError, ValueError) as e:
        print(e, file=sys.stderr)
        return 1
    print(f"Saved {video} ({video.stat().st_size / 1e6:.1f} MB)")
    return 0


def _finish(args) -> int:
    from .finish import apply_finish, finished_frames, measure_channels, plan_finish

    def bar(word):
        def progress(i: int, n: int) -> None:
            print(f"\r  {word} {i}/{n}", end="", file=sys.stderr, flush=True)
            if i == n:
                print(file=sys.stderr)
        return progress

    if args.fps <= 0 or (args.width is not None and args.width < 16) or args.window < 3:
        print("Check the numbers: --fps above 0, --width at least 16, --window at least 3.", file=sys.stderr)
        return 1
    if not (args.dry_run or args.out or args.video):
        print("Say where to put the result: --out FOLDER for frames, --video FILE.mp4, or both "
              "(or --dry-run to just measure).", file=sys.stderr)
        return 1
    try:
        frames = finished_frames(args.folder.expanduser())
        print(f"{len(frames)} finished frames in {frames[0].parent}")
        p = plan_finish(frames, window=args.window, colour=not args.brightness_only, progress=bar("Measuring"))
    except (ValueError, OSError) as e:
        print(f"\n{e}", file=sys.stderr)
        return 1
    if p.unmeasured:
        print("  " + describe_unmeasured([p.frames[i].name for i in p.unmeasured]))
    print(f"  Brightness wobble: {p.brightness_before:.3f} stops now, {p.brightness_after:.3f} after")
    if not args.brightness_only:
        print(f"  Colour wobble:     {p.colour_before:.3f} now, {p.colour_after:.3f} after")
    print(f"  Largest correction: {p.largest_brightness_correction:.3f} stops"
          + ("" if args.brightness_only else f", colour {p.largest_colour_correction:.3f}"))
    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["frame", "red", "green", "blue", "gain_red", "gain_green", "gain_blue"])
            for path, m, g in zip(p.frames, p.measured, p.gains):
                w.writerow([path.name, *(f"{v:.4f}" for v in m), *(f"{v:+.4f}" for v in g)])
        print(f"  Saved {args.csv}")
    if args.dry_run:
        print("  Dry run: nothing written.")
        return 0
    try:
        written = apply_finish(p, out=args.out, video=args.video, fps=args.fps, width=args.width,
                               progress=bar("Writing"), fmt=args.format)
    except (ValueError, RuntimeError, OSError) as e:
        print(f"\n{e}", file=sys.stderr)
        return 1
    if args.out:
        from .finish import _wobble, LUMA
        check = measure_channels(written, bar("Checking"))
        print(f"  Measured on the corrected frames: brightness {_wobble(check @ LUMA):.3f} stops, "
              f"colour {_wobble(check[:, 0] - check[:, 2]):.3f}")
        print(f"  Corrected frames: {args.out}")
    if args.video:
        print(f"  Video: {args.video} ({args.video.stat().st_size / 1e6:.1f} MB)")
    return 0


def _editors() -> int:
    from .editors import available
    print("Editors this installation can write to:")
    for name, cls in sorted(available().items()):
        print(f"  {cls.label:12s} --editor {name}")
    print("Any editor works with 'timelapse finish' (after exporting). Adding an editor: see docs/EDITORS.md.")
    return 0


def _pick_editor(folder: Path, choice: str):
    from .editors import detect, get
    if choice != "auto":
        return get(choice)
    found = detect(folder)
    if len(found) == 1:
        return found[0]
    if not found:
        raise ValueError(
            f"No edit files from a supported editor in {folder} (see: timelapse editors). "
            "Open the folder in your editor and paste one edit onto every frame first. "
            "Or skip this step: edit and export as usual, then run 'timelapse finish' on the export.")
    raise ValueError("Edit files from more than one editor here: choose with --editor "
                     + " or ".join(e.name for e in found) + ".")


def _prepare_or_undo(args) -> int:
    folder = args.folder.expanduser()
    if not folder.is_dir():
        raise ValueError(f"Can't find a folder at {folder}")
    editor = _pick_editor(folder, args.editor)
    if args.command == "prepare":
        check_window(args.window)
    if not getattr(args, "dry_run", False) and not args.force and editor.is_running():
        raise ValueError(f"{editor.label} is open. Close it completely first: it can save its own copy over "
                         "the edit files. (If you're sure it's safe, add --force.)")

    def bar(word):
        def progress(i: int, n: int) -> None:
            print(f"\r  {word} {i}/{n}", end="", file=sys.stderr, flush=True)
            if i == n:
                print(file=sys.stderr)
        return progress

    print(f"{editor.label}: {folder}")
    seq = editor.load(folder, progress=bar("Reading camera settings"))
    if args.command == "undo":
        n = editor.undo(seq)
        print(f"  Took the corrections back out of {n} frames. Reopen {editor.label} to see.")
        return 0
    r = editor.prepare(seq, window=args.window, strength=args.strength, white_balance=not args.no_white_balance,
                       dry_run=args.dry_run, force=args.force, progress=bar("Measuring"))
    print(f"  {r.frames} frames")
    for prob in r.problems:
        print(f"  {prob}")
    for note in r.notes:
        print(f"  {note}")
    if r.deflicker is not None:
        for line in describe_changes(r.deflicker):
            print(line)
        if r.deflicker.unmeasured:
            print("  " + describe_unmeasured([seq[i].name for i in r.deflicker.unmeasured]))
        print(f"  Flicker: {r.deflicker.flicker_before:.3f} stops before, {r.deflicker.flicker_after:.3f} after.")
    if r.dry_run:
        print("  Dry run: nothing written.")
        return 0
    print(f"  Written into {r.changed} {editor.label} edit files. Undo any time: timelapse undo \"{folder}\"")
    print(f"  Next: open the folder in {editor.label}, export every frame (JPEG is fine) into a new folder, then:")
    print('        timelapse finish "EXPORT FOLDER" --video film.mp4')
    return 0


def describe_unmeasured(names: list[str]) -> str:
    """One plain sentence about frames that couldn't be measured."""
    shown = ", ".join(names[:5]) + (f" and {len(names) - 5} more" if len(names) > 5 else "")
    return (f"Note: couldn't measure {len(names)} frame{'s' if len(names) != 1 else ''} ({shown}): "
            "completely black or blown out. Left exactly as they are.")


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
