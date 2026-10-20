"""Lightroom Classic and Adobe Camera Raw, through ``.xmp`` sidecars.

What it does: adds deflicker corrections to each frame's Exposure, on top of
your own exposure edits, and can take them back out exactly.

What it doesn't do yet: white balance. Lightroom's temperature scale comes
from Adobe's own camera profiles, which differ from the camera's figures by
a few hundred Kelvin, and there hasn't been a Lightroom to check against. Run
``timelapse finish`` after exporting to even out colour shifts.

Status: built and tested against real Lightroom sidecars (byte-for-byte),
not yet tested inside Lightroom itself. Reports welcome.

Lightroom keeps your edits in its catalogue and only reads ``.xmp`` files
when asked, so the steps are (see docs/LIGHTROOM.md):
1. In Lightroom: select every frame, Metadata > Save Metadata to Files (Ctrl+S).
2. Close Lightroom, run ``timelapse prepare FOLDER``.
3. In Lightroom: select every frame, Metadata > Read Metadata from Files.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ...deflicker import DEFAULT_WINDOW, smooth_flicker
from ...measure import measure_brightness
from ...sequencer import RAW_EXTENSIONS, check_sequence, load_sequence
from ...types import FlickerCorrections, Frame, Problem, Sequence
from ..base import Editor, PrepareReport
from ..common import keep_copy, record_kept_in_step, read_record, running_programs, write_bytes_atomically
from .xmp import EXPOSURE_RANGE, format_exposure, has_camera_raw_settings, read_exposure, with_exposure, xmp_path

RECORD = "lightroom.json"
BLOCKING = {"too_short", "mixed_files", "mixed_cameras", "no_lightroom_edits"}
SAVE_FIRST = ("In Lightroom, select every frame and choose Metadata > Save Metadata to Files (Ctrl+S), "
              "so each photo has an up-to-date .xmp file. Then close Lightroom and run this again.")


@dataclass(frozen=True)
class FrameChange:
    frame: Frame
    xmp: Path
    base: float          # your own exposure, without any deflicker
    correction: float    # what deflicker adds

    @property
    def exposure(self) -> float:
        return round(self.base + self.correction, 2)


class Lightroom(Editor):
    name = "lightroom"
    label = "Lightroom"

    # --- the contract ---------------------------------------------------------

    def detect(self, folder: Path) -> bool:
        folder = Path(folder)
        if not folder.is_dir():
            return False
        for photo in folder.iterdir():
            if photo.suffix.lower() in RAW_EXTENSIONS:
                side = xmp_path(photo)
                if side.exists() and has_camera_raw_settings(side.read_bytes()[:65536]):
                    return True
        return False

    def is_running(self) -> bool:
        names = running_programs() or []
        return any(n.lower().startswith(("lightroom", "adobe lightroom")) for n in names)

    def load(self, folder: Path, progress=None) -> Sequence:
        return load_sequence(folder, progress=progress)

    def problems(self, seq: Sequence) -> list[Problem]:
        problems = check_sequence(seq)
        missing = [f.name for f in seq if self._exposure_of(f) is None]
        if missing:
            problems.append(Problem(
                "no_lightroom_edits",
                f"{len(missing)} frame{'s have' if len(missing) != 1 else ' has'} no Lightroom settings file "
                f"(.xmp) with an Exposure value (first: {missing[0]}). {SAVE_FIRST}",
                serious=True))
        if any(f.path.suffix.lower() in (".dng",) for f in seq):
            problems.append(Problem(
                "dng_inside", "DNG files keep their Lightroom settings inside the file, not in an .xmp, "
                "and aren't supported yet.", serious=True))
        return problems

    def plan(self, seq: Sequence, window: int = DEFAULT_WINDOW, strength: float = 1.0,
             raw_brightness=None, progress=None) -> tuple[FlickerCorrections, list[FrameChange]]:
        """Work out every frame's new exposure. Writes nothing."""
        record = read_record(seq.folder, RECORD)
        bases = []
        for f in seq:
            current = self._exposure_of(f)
            if current is None:
                raise ValueError(f"{f.name} has no Lightroom exposure setting. {SAVE_FIRST}")
            bases.append(current - record.get(f.name, {}).get("applied", 0.0))
        bases = np.array(bases)
        if raw_brightness is None:
            raw_brightness = measure_brightness(seq, progress=progress)
        seen = np.asarray(raw_brightness, dtype=np.float64) + bases       # exposure is a linear gain
        fix = smooth_flicker(seq, window=window, strength=strength, brightness=seen)
        changes = [FrameChange(f, xmp_path(f.path), float(b), float(c))
                   for f, b, c in zip(seq, bases, fix.exposure, strict=True)]
        lo, hi = EXPOSURE_RANGE
        outside = [c.frame.name for c in changes if not lo <= c.exposure <= hi]
        if outside:
            raise ValueError(f"The corrections would push {len(outside)} frames past Lightroom's exposure range "
                             f"(first: {outside[0]}). Check those frames, or use a lower --strength.")
        if not all(np.isfinite([c.exposure for c in changes])):
            raise ValueError("Refusing to write an unknown exposure value.")
        return fix, changes

    def apply(self, seq: Sequence, changes: list[FrameChange]) -> int:
        with record_kept_in_step(seq.folder, RECORD) as record:
            for c in changes:
                data = c.xmp.read_bytes()
                keep_copy(c.xmp)
                write_bytes_atomically(c.xmp, with_exposure(data, c.exposure))
                record[c.frame.name] = {"base": round(c.base, 4), "applied": round(c.exposure - c.base, 4)}
        return len(changes)

    def prepare(self, seq, *, window: int = DEFAULT_WINDOW, strength: float = 1.0,
                white_balance: bool = True, dry_run: bool = False, force: bool = False,
                progress=None, raw_brightness=None) -> PrepareReport:
        report = PrepareReport(editor=self.label, frames=len(seq), dry_run=dry_run)
        report.problems = self.problems(seq)
        blocking = [p for p in report.problems if p.kind in BLOCKING or p.kind == "dng_inside"]
        if blocking and not force:
            raise ValueError("\n".join(str(p) for p in blocking) + "\nNot changing anything until that's sorted."
                             + ("" if dry_run else " (If you're sure, add --force.)"))
        if white_balance:
            report.notes.append("White balance left as it is: Lightroom support for it isn't ready yet. "
                                "'timelapse finish' evens out colour after you export.")
        fix, changes = self.plan(seq, window=window, strength=strength,
                                 raw_brightness=raw_brightness, progress=progress)
        report.deflicker = fix
        if not dry_run:
            report.changed = self.apply(seq, changes)
            report.notes.append("Back in Lightroom: select every frame, then Metadata > Read Metadata from "
                                "Files, so Lightroom picks up the new exposures.")
        return report

    def undo(self, seq: Sequence) -> int:
        changed = 0
        with record_kept_in_step(seq.folder, RECORD) as record:
            for name, entry in list(record.items()):
                photo = seq.folder / name
                side = xmp_path(photo)
                if not side.exists():
                    continue
                data = side.read_bytes()
                current = read_exposure(data)
                if current is None:
                    continue
                write_bytes_atomically(side, with_exposure(data, round(current - entry["applied"], 2)))
                del record[name]
                changed += 1
        return changed

    # --- helpers --------------------------------------------------------------

    @staticmethod
    def _exposure_of(frame: Frame) -> float | None:
        side = xmp_path(frame.path)
        if not side.exists():
            return None
        return read_exposure(side.read_bytes())


__all__ = ["Lightroom", "FrameChange", "format_exposure"]
