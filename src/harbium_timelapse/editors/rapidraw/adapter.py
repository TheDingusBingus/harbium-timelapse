"""RapidRAW, through the Editor contract: the reference adapter."""

from __future__ import annotations

from pathlib import Path

from ...deflicker import DEFAULT_WINDOW
from ..base import Editor, PrepareReport
from .deflicker import apply, check, load, plan, undo
from .recover import rapidraw_running
from .sidecar import SUFFIX
from .whitebalance import apply_white_balance, plan_white_balance, undo_white_balance

BLOCKING = {"too_short", "mixed_files", "mixed_cameras"}


class RapidRAW(Editor):
    name = "rapidraw"
    label = "RapidRAW"

    def detect(self, folder: Path) -> bool:
        folder = Path(folder)
        return folder.is_dir() and any(p.name.endswith(SUFFIX) for p in folder.iterdir())

    def is_running(self) -> bool:
        return rapidraw_running()

    def load(self, folder: Path, progress=None):
        return load(folder, progress=progress)

    def prepare(self, seq, *, window: int = DEFAULT_WINDOW, strength: float = 1.0,
                white_balance: bool = True, dry_run: bool = False, force: bool = False,
                progress=None) -> PrepareReport:
        report = PrepareReport(editor=self.label, frames=len(seq), dry_run=dry_run)
        report.problems = check(seq)
        blocking = [p for p in report.problems if p.kind in BLOCKING]
        if blocking and not force and not dry_run:
            raise ValueError("\n".join(str(p) for p in blocking) + "\nNot changing anything until that's "
                             "sorted: the results would be wrong. (If you're sure, add --force.)")

        if white_balance:
            try:
                wb = plan_white_balance(seq)
                report.notes.extend(f"White balance: {n}" for n in wb.notes)
                report.notes.append(
                    f"White balance: steadied to {wb.kelvin.min():.0f}-{wb.kelvin.max():.0f} K "
                    f"(the camera jumped up to {wb.biggest_jump_before:.0f} K between frames; now at most "
                    f"{wb.biggest_jump_after:.0f} K).")
                if not dry_run:
                    apply_white_balance(seq, wb)
            except ValueError as e:
                report.notes.append(f"White balance left as it is: {e}")

        p = plan(seq, window=window, strength=strength, progress=progress)
        report.deflicker = p.flicker
        if not dry_run:
            report.changed = apply(p)
        return report

    def undo(self, seq) -> int:
        n = undo(seq.folder)
        undo_white_balance(seq)
        return n
