"""How fast can this drive give us what we need? Reads only; changes nothing.

    python benchmarks/reading_speed.py "F:/path/to/raw folder"

Times several ways of reading camera settings and image data, each on its own
set of files, so one method can't benefit from another having already pulled
files into memory. For honest numbers, unplug and reconnect the drive (or
restart the computer) just before running, so nothing is cached.

Prints seconds per file for each method, and what that would mean for the
whole folder.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from harbium_timelapse.exif import _EXIFTOOL_TAGS
from harbium_timelapse.programs import find_program
from harbium_timelapse.sequencer import RAW_EXTENSIONS

HEADER = 1024 * 1024        # bytes: about what ExifTool needs from the start of a RAW file


def exiftool(files: list[Path], fast: bool) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".args", encoding="utf-8", delete=False) as f:
        f.write("\n".join(["-charset", "filename=utf8", *map(str, files)]))
        argfile = f.name
    try:
        subprocess.run([find_program("exiftool") or "exiftool", "-json", "-n", *(["-fast"] if fast else []),
                        *_EXIFTOOL_TAGS, "-@", argfile],
                       capture_output=True, stdin=subprocess.DEVNULL, timeout=900, check=False)
    finally:
        Path(argfile).unlink(missing_ok=True)


def exiftool_parallel(files: list[Path], fast: bool, workers: int = 4) -> None:
    parts = [files[i::workers] for i in range(workers)]
    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(lambda part: exiftool(part, fast), parts))


def read_start(path: Path) -> None:
    with open(path, "rb") as f:
        f.read(HEADER)


def read_whole(path: Path) -> None:
    with open(path, "rb") as f:
        while f.read(8 * 1024 * 1024):
            pass


def one_at_a_time(fn):
    return lambda files: [fn(p) for p in files]


def four_at_once(fn):
    def run(files):
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(fn, files))
    return run


TESTS = [
    # name, how, share of files
    ("Camera settings, ExifTool as now", lambda fs: exiftool(fs, fast=False), 4),
    ("Camera settings, ExifTool -fast", lambda fs: exiftool(fs, fast=True), 4),
    ("Camera settings, 4 ExifTools at once", lambda fs: exiftool_parallel(fs, fast=False), 4),
    ("Camera settings, 4 at once and -fast", lambda fs: exiftool_parallel(fs, fast=True), 4),
    ("Start of each file (1 MB), one at a time", one_at_a_time(read_start), 2),
    ("Start of each file (1 MB), four at once", four_at_once(read_start), 2),
    ("Whole files, one at a time", one_at_a_time(read_whole), 1),
    ("Whole files, four at once", four_at_once(read_whole), 1),
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("folder", type=Path)
    args = ap.parse_args()
    files = sorted(p for p in args.folder.iterdir() if p.suffix.lower() in RAW_EXTENSIONS)
    if len(files) < 30:
        raise SystemExit(f"Found {len(files)} RAW files; at least 30 are needed for a fair test.")
    warm_up, files = files[-1], files[:-1]          # wake the drive up on a file no test uses
    read_start(warm_up)
    exiftool([warm_up], fast=False)                 # and load ExifTool itself, so the first test isn't penalised
    unit = len(files) // sum(share for _, _, share in TESTS)
    size = sum(p.stat().st_size for p in files) / len(files) / 1e6
    print(f"{len(files) + 1} RAW files, about {size:.0f} MB each. Each test uses its own files.\n")
    print(f"{'method':44s} {'files':>5s} {'per file':>9s} {'whole folder':>13s}")
    start = 0
    for name, how, share in TESTS:
        group = files[start:start + unit * share]
        start += len(group)
        t = time.perf_counter()
        how(group)
        each = (time.perf_counter() - t) / len(group)
        print(f"{name:44s} {len(group):5d} {each:8.3f}s {each * (len(files) + 1):12.1f}s")
    print("\nIf the 'as now' line is much slower than 'Start of each file', something other than "
          "the drive (antivirus scanning, for example) is likely slowing ExifTool down.")
    if os.name == "nt":
        print("Windows: antivirus often scans files as they're opened. That shows up as every "
              "method being slow, even 'Start of each file'.")


if __name__ == "__main__":
    main()
