"""The contract an editor adapter fulfils.

An adapter connects this library to one photo editor through the editor's own
edit files (sidecars). Measuring, planning and ``timelapse finish`` are shared;
an adapter only has to know how its editor stores an edit.

To add an editor, subclass ``Editor``, fill in the five methods, and register
it with an entry point in your package's ``pyproject.toml``::

    [project.entry-points."harbium_timelapse.editors"]
    myeditor = "my_package.adapter:MyEditor"

``timelapse prepare FOLDER`` then finds it automatically when that editor's
files are in the folder. See ``docs/EDITORS.md`` for a walkthrough, and
``editors/rapidraw/adapter.py`` for the reference implementation.

Rules every adapter must keep (the shared tests check the RapidRAW one):
- Never change the photos themselves; only the editor's edit files.
- Keep an untouched copy of each edit file before its first change, so
  ``undo`` and restore are always possible.
- Refuse to write while the editor is open, unless the user forces it.
- Validate every value before writing any file; never write NaN.
- Keep a record of what was added, saved as each file is written, so an
  interrupted run can simply be run again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from ..types import FlickerCorrections, Problem, Sequence


@dataclass
class PrepareReport:
    """What ``prepare`` found and did, for showing to the user."""

    editor: str
    frames: int
    problems: list[Problem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)       # plain sentences, in order
    deflicker: FlickerCorrections | None = None
    changed: int = 0
    dry_run: bool = False


class Editor:
    """Subclass this for each editor. Instances are cheap; make one per run."""

    name: ClassVar[str] = ""    # used on the command line: --editor rapidraw
    label: ClassVar[str] = ""   # shown to people: "RapidRAW"

    def detect(self, folder: Path) -> bool:
        """True if this editor's edit files are in ``folder``."""
        raise NotImplementedError

    def is_running(self) -> bool:
        """True if the editor is open (it might save over the files)."""
        return False

    def load(self, folder: Path, progress=None) -> Sequence:
        """The sequence, ordered, with camera settings."""
        raise NotImplementedError

    def prepare(self, seq: Sequence, *, window: int, strength: float = 1.0,
                white_balance: bool = True, dry_run: bool = False, force: bool = False,
                progress=None) -> PrepareReport:
        """Steady the white balance (if the editor allows) and deflicker, by
        writing into the editor's edit files. ``dry_run`` writes nothing."""
        raise NotImplementedError

    def undo(self, seq: Sequence) -> int:
        """Take everything ``prepare`` wrote back out. Returns frames changed."""
        raise NotImplementedError
