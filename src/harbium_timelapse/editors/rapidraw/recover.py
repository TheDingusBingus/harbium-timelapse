"""When RapidRAW overwrites the sidecars, and how to stop it happening.

What happened (October 2026, real sequence): ``refine`` rewrote all 229
sidecars while RapidRAW had the folder open. RapidRAW then saved its own
defaults over them, keeping only the colour labels it had cached. Every
edit, keyframes included, read as reset.

Two defences:

- **Don't write while RapidRAW is running.** Every command that changes
  sidecars checks first, and refuses.
- **Rebuild from what this tool keeps.** ``.timelapse-core`` holds the
  untouched originals (with your keyframe grades) and the ledger (every
  deflicker and refine correction). ``rebuild`` puts your keyframes back,
  re-blends between them, and re-applies every correction, so you end up
  exactly where you were.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from harbium_timelapse import Sequence

from .sidecar import (get_exposure, get_temperature, read_sidecar, set_exposure,
                      set_temperature, sidecar_path, write_sidecar)
from .state import STATE_DIR, clear_ledger, folder_lock, read_json, read_ledger, write_json, write_ledger

REBUILD_COPY = "rapidraw.rebuild.json"


# --- is RapidRAW running? -----------------------------------------------------

def rapidraw_running() -> bool:
    """True if RapidRAW itself is running. If it can't tell, says no.

    Matches process names that *start* with "rapidraw" (RapidRAW.exe), so this
    tool's own command, timelapse-rapidraw.exe, isn't mistaken for it. (It
    was, on the first real run on Windows.)
    """
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                                 text=True, timeout=10).stdout
            names = [line.split('","')[0].strip('"') for line in out.splitlines() if line.strip()]
        else:
            out = subprocess.run(["ps", "-A", "-o", "comm="], capture_output=True,
                                 text=True, timeout=10).stdout
            names = [Path(line.strip()).name for line in out.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        return False
    return any(is_rapidraw(name) for name in names)


def is_rapidraw(process_name: str) -> bool:
    return process_name.lower().startswith("rapidraw")


RUNNING_MESSAGE = (
    "RapidRAW is running. Close it completely before this command changes any sidecars: "
    "if RapidRAW has the folder open, it can save its own copy over them. "
    "(If you're sure it's safe, add --force.)"
)


# --- spotting overwritten sidecars --------------------------------------------

def overwritten_frames(seq: Sequence) -> list[str]:
    """Frames whose exposure no longer matches what this tool last wrote.
    Many at once usually means RapidRAW saved over them."""
    ledger = read_ledger(seq.folder)
    off = []
    for f in seq:
        entry = ledger.get(f.name)
        data = read_sidecar(f.path)
        if entry is None or data is None:
            continue
        if abs(get_exposure(data) - (entry["base"] + entry["applied"])) > 1e-3:
            off.append(f.name)
    return off


# --- rebuilding ---------------------------------------------------------------

def rebuild(seq: Sequence) -> tuple[int, int]:
    """Back to where you were: keyframes from the originals, the blend between
    them, every deflicker and refine correction re-applied."""
    with folder_lock(seq.folder):
        return _rebuild(seq)


def _rebuild(seq: Sequence) -> tuple[int, int]:
    """The rebuild itself (see ``rebuild``).

    Returns (frames rebuilt, keyframes found).
    """
    from .keyframes import KEYFRAME_LABEL, blend, keyframes_in

    folder = seq.folder
    originals = folder / STATE_DIR / "originals"
    # A safety copy of the record survives until the rebuild finishes, so an
    # interrupted rebuild loses nothing: run it again and it carries on.
    safety = folder / STATE_DIR / REBUILD_COPY
    ledger = read_json(safety, "an unfinished rebuild").get("frames", {}) if safety.exists() else read_ledger(folder)
    if not ledger:
        raise ValueError("There's no deflicker ledger to rebuild from. Use restore instead.")
    missing = [f.name for f in seq if not (originals / sidecar_path(f.path).name).exists()]
    if missing:
        raise ValueError(f"{len(missing)} frames have no saved original (first: {missing[0]}), "
                         "so they can't be rebuilt. Use restore, then redo the steps.")

    history_path = folder / STATE_DIR / "refine.json"
    history = history_path.read_text(encoding="utf-8") if history_path.exists() else None
    write_json(safety, {"version": 1, "frames": ledger})

    # 1. Originals back, with today's labels (the originals predate some labelling).
    for f in seq:
        side = sidecar_path(f.path)
        labels = (read_sidecar(f.path) or {}).get("tags")
        original = read_json(originals / side.name, f"the original edit of {f.name}")
        if labels is not None:
            original["tags"] = labels
        write_sidecar(f.path, original)

    # 2. Blend between keyframes. The originals hold no deflicker, so the ledger
    #    is set aside first (it's held above), or the blend would subtract it.
    clear_ledger(folder)
    keys = keyframes_in(seq, KEYFRAME_LABEL)
    if len(keys) >= 2:
        blend(seq)

    # 3. Every correction re-applied on top of the rebuilt exposure.
    for f in seq:
        entry = ledger.get(f.name)
        if entry is None:
            continue
        data = read_sidecar(f.path)
        base = get_exposure(data)
        set_exposure(data, base + entry["applied"])
        if entry.get("applied_temp"):
            set_temperature(data, get_temperature(data) + entry["applied_temp"])
        if "white_balance" in entry:
            data.setdefault("adjustments", {})["whiteBalance"] = entry["white_balance"]
        write_sidecar(f.path, data)
        entry["base"] = round(base, 4)
    write_ledger(folder, ledger)
    if history is not None:
        history_path.write_text(history, encoding="utf-8")
    safety.unlink(missing_ok=True)          # finished: the safety copy isn't needed
    return len(seq), len(keys)
