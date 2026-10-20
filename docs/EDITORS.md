# Adding your editor

`harbium-timelapse` measures and plans everything itself. An editor adapter only teaches it **how one editor stores an edit**, so that `timelapse prepare` can write steady white balance and deflicker straight into that editor's edit files, before your grade.

You don't need an adapter to use the tool with your editor: grade, export every frame, then run `timelapse finish`. An adapter adds the step *before* the grade, which matters most for big exposure changes (ND turns, ISO steps), where highlights and shadows still have room in the RAW.

## Supported

| Editor | Edit files | Status |
|---|---|---|
| RapidRAW | `<photo>.rrdata` (JSON) | Built in: white balance (exact Kelvin and tint) and exposure |
| Lightroom Classic / Camera Raw | `<photo>.xmp` (`crs:` tags) | Planned next |
| RawTherapee / ART | `<photo>.pp3` (INI) | Planned |
| darktable | `<photo>.xmp` (history stack) | Planned |

## The contract

Subclass `harbium_timelapse.editors.Editor` and fill in five methods:

```python
from pathlib import Path
from harbium_timelapse.editors import Editor, PrepareReport
from harbium_timelapse import load_sequence, smooth_flicker

class MyEditor(Editor):
    name = "myeditor"        # on the command line: --editor myeditor
    label = "My Editor"      # shown to people

    def detect(self, folder: Path) -> bool:
        """Are this editor's edit files in the folder?"""
        return any(p.suffix == ".myedit" for p in folder.iterdir())

    def is_running(self) -> bool:
        """Is the editor open? (It might save over the files.)"""
        return False

    def load(self, folder, progress=None):
        return load_sequence(folder, progress=progress)

    def prepare(self, seq, *, window, strength=1.0, white_balance=True,
                dry_run=False, force=False, progress=None) -> PrepareReport:
        fix = smooth_flicker(seq, window=window, strength=strength, progress=progress)
        report = PrepareReport(editor=self.label, frames=len(seq), deflicker=fix, dry_run=dry_run)
        if not dry_run:
            ...  # add fix.exposure[i] stops to each frame's exposure, in its edit file
        return report

    def undo(self, seq) -> int:
        ...  # take back out exactly what prepare added
```

Then register it in **your** package's `pyproject.toml`, and `timelapse prepare` finds it automatically:

```toml
[project.entry-points."harbium_timelapse.editors"]
myeditor = "my_package.adapter:MyEditor"
```

`timelapse editors` lists everything installed.

## Rules every adapter keeps

These come from real failures found while building the RapidRAW adapter. The shared tests check them.

1. **Never touch the photos.** Only the editor's edit files.
2. **Keep an untouched copy** of each edit file before its first change, so undo is always possible.
3. **Refuse to write while the editor is open**, unless the user adds `--force`. An open editor can save its own copy over your changes. That happened on a real 229-frame sequence.
4. **Validate every value before writing any file.** Never write NaN or infinity; a frame that can't be measured gets no correction (`fix.unmeasured`).
5. **Keep a record of what was added, saved as each file is written**, so an interrupted run (Ctrl+C, crash) can simply be run again without stacking corrections. See `editors/rapidraw/state.py` (`ledger_kept_in_step`).
6. **Write each file atomically**: write a temp file, then rename it over the original.

## Things to verify for your editor, with real files

- Where the exposure value lives, and in what units. It's usually stops, but check.
- Whether exposure is applied **before** the editor's tone mapping and other "smart" tools. Some tools measure the image before exposure (RapidRAW's Dehaze does), which makes them react to brightness changes that exposure has already evened out.
- Whether the editor writes edit files for every photo, or only edited ones (users may need to paste one edit onto all frames first).
- How white balance is stored: Kelvin and tint on whose scale? `colour_temperature.py` converts camera neutrals to the standard DNG/Adobe-style scale; check the editor's own readout against it on a real frame.

`editors/rapidraw/` is the reference implementation, and `docs/RAPIDRAW.md` describes what was verified there.
