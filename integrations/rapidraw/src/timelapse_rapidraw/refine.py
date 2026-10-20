"""Refine deflicker against an actual render.

Why this exists: deflicker measures the RAW files and assumes +1 on
RapidRAW's Exposure slider is one stop of light. On a real Canon R6 dusk
sequence it wasn't: through a finished grade, +1 on the slider moved the
render like 1.4 to 1.8 stops of real light, varying along the sequence with
the grade. Corrections came out up to 80% too strong, and the render
flickered *more* than the RAWs (0.136 stops against 0.111).

So the last word goes to the render. Each pass:

1. Measure the brightness of every exported frame.
2. Work out how strongly the Exposure slider moves the render, frame by
   frame. First pass: from how render brightness tracks the RAW
   measurements and the corrections already applied. Later passes, much more
   precisely: from how each frame changed between this export and the last.
3. Nudge each frame's exposure by exactly what removes the render's own
   flicker.

Export again and repeat. Usually two or three passes.

**Colour** runs the same loop on the Temperature slider. On the same real
sequence the camera was on Auto white balance; its guess jumped by up to
669 K between frames (worst when a variable ND was turned), and the render's
colour wobbled 2.5x more than the light did. RapidRAW's Temperature is a
nudge from each frame's own as-shot white balance, so pasting can't fix it.
Refine measures the render's colour and nudges each frame's Temperature:

- ``white_balance="smooth"`` (default when the as-shot Kelvin is readable):
  each frame's effective white balance follows a heavily smoothed curve of
  the camera's own as-shot Kelvin. The camera's frame-to-frame guessing goes;
  the auto look, and real colour changes in the light, stay. (James's idea.)
- ``white_balance="steady"`` (default without ExifTool): smooth the render's
  colour itself. Stronger, but irons out real colour changes too.
- ``white_balance="first"`` or a Kelvin number: render every frame as if
  shot at that white balance (needs ExifTool for the as-shot values). Your
  keyframes' colour shifts too, so expect to touch up their temperature.

RapidRAW's Temperature units aren't documented, so the slider's effect is
learned from your exports, as for exposure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from timelapse_core import Sequence, measure_light, smooth_brightness
from timelapse_core.measure import fill_gaps
from timelapse_core.deflicker import DEFAULT_WINDOW
from timelapse_core.sequencer import _natural_key

from .sidecar import finite, get_exposure, read_sidecar, set_exposure, write_sidecar
from .state import STATE_DIR, keep_original, ledger_kept_in_step, read_json, read_ledger, write_json

HISTORY = "refine.json"
INVISIBLE = 0.015       # stops of render flicker below which there's nothing to gain
MOST_PER_PASS = 1.0     # stops; no single pass moves a frame further than this
STRENGTH_RANGE = (0.3, 6.0)
INVISIBLE_COLOUR = 0.015  # red-over-blue stops of frame-to-frame colour wobble
MOST_COLOUR_PER_PASS = 0.3  # red-over-blue stops
FIRST_GUESS = 0.012       # warmth per Temperature unit, used only if it can't be estimated.
                          # From RapidRAW's source: +1 Temperature = 1.5 mired; on a Canon R6
                          # that is ~0.0095 raw red/blue stops per mired, ~0.87x of it in the
                          # render. (0.005 overshot ~2.5x on the real sequence.) Refine then
                          # measures the real value from the next export.
FIRST_GUESS_CAP = 10.0    # Temperature units: a careful first step when guessing
RENDERED = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


@dataclass(frozen=True)
class RefineResult:
    pass_number: int
    flicker_in_render: float
    slider_strength: float      # stops of render change per +1 on the Exposure slider (typical)
    changed: int
    nothing_to_do: bool = False
    colour_in_render: float = float("nan")
    colour_strength: float = float("nan")     # warmth per +1 on Temperature (typical)
    colour_guessed: bool = False              # strength couldn't be estimated yet: a careful first step
    unmeasured: tuple = ()                    # exported frames that couldn't be measured: left as they are


def rendered_frames(folder: Path | str) -> list[Path]:
    folder = Path(folder)
    return sorted((p for p in folder.iterdir()
                   if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in RENDERED),
                  key=_natural_key)


def refine(
    seq: Sequence,
    rendered: Path | str | None = None,
    window: int = DEFAULT_WINDOW,
    render_brightness: np.ndarray | None = None,
    raw_brightness: np.ndarray | None = None,
    progress=None,
    dry_run: bool = False,
    render_warmth: np.ndarray | None = None,
    raw_warmth: np.ndarray | None = None,
    colour: bool = True,
    white_balance: str | float = "auto",
    adjust_exposure: bool = True,
) -> RefineResult:
    """One refinement pass. ``rendered`` is the folder of exported frames,
    in the same order and number as the sequence.

    ``adjust_exposure=False`` makes a colour-only pass: Exposure is left
    exactly as it is. Temperature changes nudge the render's brightness too,
    so exposure and colour are best refined in separate passes."""
    ledger = read_ledger(seq.folder)
    if not ledger:
        raise ValueError("Run deflicker first: refine improves deflicker, it doesn't replace it.")
    if white_balance == "auto":
        white_balance = "smooth" if all(f.white_balance for f in seq) else "steady"

    if render_brightness is None:
        frames = rendered_frames(rendered)
        if len(frames) != len(seq):
            raise ValueError(f"The export has {len(frames)} frames but the sequence has {len(seq)}. "
                             "Export every frame, and nothing else, into that folder.")
        render_brightness, measured_warmth = measure_light(frames, progress=progress)
        if render_warmth is None:
            render_warmth = measured_warmth
    render = np.asarray(render_brightness, dtype=np.float64)
    warm = None if render_warmth is None or not colour else np.asarray(render_warmth, dtype=np.float64)
    # Exported frames that couldn't be measured (black, blown) keep their
    # corrections exactly as they are; the maths bridges them.
    bad = ~np.isfinite(render) if warm is None else ~(np.isfinite(render) & np.isfinite(warm))
    if bad.any():
        render = fill_gaps(render)
        if warm is not None:
            warm = fill_gaps(warm)

    sidecars = [read_sidecar(f.path) or {} for f in seq]
    applied = np.array([ledger.get(f.name, {}).get("applied", 0.0) for f in seq])
    base = np.array([get_exposure(d) for d in sidecars]) - applied
    applied_t = np.array([ledger.get(f.name, {}).get("applied_temp", 0.0) for f in seq])
    base_t = np.array([float((d.get("adjustments") or {}).get("temperature") or 0.0) for d in sidecars]) - applied_t

    flicker = _wobble(render)
    colour_flicker = _wobble(warm) if warm is not None else float("nan")
    history = _read_history(seq.folder)
    pass_number = len(history) + 1

    fix_light = adjust_exposure and flicker >= INVISIBLE
    fix_colour = warm is not None and (colour_flicker >= INVISIBLE_COLOUR
                                       or white_balance not in ("steady", "smooth"))
    if not fix_light and not fix_colour:
        return RefineResult(pass_number, flicker, float("nan"), 0, nothing_to_do=True,
                            colour_in_render=colour_flicker)

    raws = {}
    def raw_light():
        if not raws:
            if raw_brightness is not None and (raw_warmth is not None or warm is None):
                raws["b"], raws["w"] = np.asarray(raw_brightness, float), (None if raw_warmth is None else np.asarray(raw_warmth, float))
            else:
                raws["b"], raws["w"] = measure_light(seq)
        return raws["b"], raws["w"]

    new_applied = applied.copy()
    strength = np.full(len(seq), np.nan)
    if fix_light:
        strength = _slider_strength(seq, render, applied, base, history, raw_light, window)
        target = smooth_brightness(render, window)
        new_applied = applied + np.clip((target - render) / strength, -MOST_PER_PASS, MOST_PER_PASS)

    new_applied_t = applied_t.copy()
    colour_strength, guessed, colour_target = np.full(len(seq), np.nan), False, None
    if fix_colour:
        colour_target = _colour_target(seq, warm, base_t, history, raw_light, window, white_balance)
        colour_strength, guessed = _temperature_strength(warm, applied_t, base_t, history, raw_light, seq, window)
        wanted = np.clip(colour_target - warm, -MOST_COLOUR_PER_PASS, MOST_COLOUR_PER_PASS)
        step_t = wanted / colour_strength
        if guessed:
            step_t = np.clip(step_t, -FIRST_GUESS_CAP, FIRST_GUESS_CAP)
        new_applied_t = applied_t + step_t

    result = RefineResult(
        pass_number, flicker, float(np.nanmedian(strength)) if fix_light else float("nan"), 0,
        colour_in_render=colour_flicker,
        colour_strength=float(np.nanmedian(colour_strength)) if fix_colour else float("nan"),
        colour_guessed=guessed,
    )
    new_applied[bad] = applied[bad]
    new_applied_t[bad] = applied_t[bad]
    result = RefineResult(**{**result.__dict__, "unmeasured": tuple(int(i) for i in np.flatnonzero(bad))})
    if dry_run:
        return result
    for b, a, bt, at in zip(base, new_applied, base_t, new_applied_t, strict=True):   # all checked first
        finite(b + a, "exposure")
        finite(bt + at, "temperature")

    with ledger_kept_in_step(seq.folder) as ledger:
        for f, data, b, a, bt, at in zip(seq, sidecars, base, new_applied, base_t, new_applied_t, strict=True):
            keep_original(f.path)
            set_exposure(data, b + a)
            if fix_colour:
                t = round(float(bt + at), 2)
                data.setdefault("adjustments", {})["temperature"] = int(t) if t == int(t) else t
            write_sidecar(f.path, data)
            entry = ledger.setdefault(f.name, {"base": 0.0, "applied": 0.0})
            entry.update(base=round(float(b), 4), applied=round(float(a), 4))
            if fix_colour or at:
                entry["applied_temp"] = round(float(at), 3)
    record = {"render": render.round(5).tolist(), "applied": applied.round(5).tolist()}
    if fix_light:
        record["strength"] = np.round(strength, 4).tolist()
    if warm is not None:
        record.update(warmth=warm.round(5).tolist(), applied_temp=applied_t.round(4).tolist())
    if colour_target is not None and white_balance != "steady":
        record["colour_target"] = colour_target.round(5).tolist()
    _write_history(seq.folder, history + [record])
    return RefineResult(**{**result.__dict__, "changed": len(seq)})


def _wobble(values: np.ndarray) -> float:
    return float(np.std(np.diff(values)) / np.sqrt(2)) if len(values) > 2 else 0.0


# --- colour ---------------------------------------------------------------------

def _colour_target(seq, warm, base_t, history, raw_light, window, white_balance) -> np.ndarray:
    """What each frame's colour should be. Steady: the render's own colour,
    smoothed. Fixed: as if every frame had the same as-shot white balance."""
    if white_balance == "steady":
        return smooth_brightness(warm, window)
    for record in reversed(history):
        if "colour_target" in record and len(record["colour_target"]) == len(warm):
            return np.array(record["colour_target"])

    kelvin = [f.white_balance for f in seq]
    if any(k is None for k in kelvin):
        raise ValueError("This white balance mode needs each frame's as-shot white balance, which "
                         "needs ExifTool installed. Or use --white-balance steady.")
    mired = 1e6 / np.array(kelvin, dtype=np.float64)   # mireds: even steps for the eye, unlike Kelvin
    _, raw_w = raw_light()
    columns = [np.diff(mired)] + ([np.diff(raw_w)] if raw_w is not None else [])
    coef, *_ = np.linalg.lstsq(np.column_stack(columns), np.diff(warm), rcond=None)
    per_mired = coef[0]                      # how the camera's guess shows up in the render

    if white_balance == "smooth":
        heavy = max(3 * window, len(mired) // 3)
        reference = smooth_brightness(mired, heavy)
        return warm - per_mired * (mired - reference)   # only the camera's jitter removed
    reference = mired[0] if white_balance == "first" else 1e6 / float(white_balance)
    return smooth_brightness(warm - per_mired * (mired - reference), window)


def _temperature_strength(warm, applied_t, base_t, history, raw_light, seq, window):
    """Render warmth per +1 on RapidRAW's Temperature slider, per frame.
    Returns (strengths, guessed)."""
    if history and "warmth" in history[-1] and len(history[-1]["warmth"]) == len(warm):
        moved = applied_t - np.array(history[-1]["applied_temp"])
        changed = warm - np.array(history[-1]["warmth"])
        big = np.abs(moved) > max(0.5, 0.25 * np.median(np.abs(moved[moved != 0])) if (moved != 0).any() else 0.5)
        if big.sum() >= 10:
            ratios = changed[big] / moved[big]
            overall = float(np.median(ratios))
            if abs(overall) > 1e-5:
                local = np.full(len(warm), np.nan)
                local[big] = ratios
                out = _local_signed(local, overall, window)
                return out, False

    # First colour pass: across the sequence, how the render's colour follows
    # the Temperature your keyframes set, alongside the light's own colour
    # and the camera's guess.
    if np.std(base_t) >= 2:
        _, raw_w = raw_light()
        columns = [np.ones(len(warm)), base_t]
        if raw_w is not None:
            columns.append(raw_w)
        kelvin = [f.white_balance for f in seq]
        if all(k is not None for k in kelvin):
            columns.append(1e6 / np.array(kelvin, dtype=np.float64))
        design = np.column_stack(columns)
        coef, *_ = np.linalg.lstsq(design, warm, rcond=None)
        fitted = design @ coef
        fit = 1 - np.sum((warm - fitted) ** 2) / max(np.sum((warm - warm.mean()) ** 2), 1e-12)
        if abs(coef[1]) > 1e-4 and fit > 0.5:
            return np.full(len(warm), float(coef[1])), False
    return np.full(len(warm), FIRST_GUESS), True


def _local_signed(ratio: np.ndarray, overall: float, window: int) -> np.ndarray:
    """Running median of per-frame strengths, kept to the overall sign and
    within a factor of four of the overall size."""
    lo, hi = abs(overall) / 4, abs(overall) * 4
    out = np.empty(len(ratio))
    half = max(window // 2, 3)
    for i in range(len(ratio)):
        nearby = ratio[max(0, i - half):i + half + 1]
        nearby = nearby[np.isfinite(nearby) & (np.sign(nearby) == np.sign(overall))
                        & (np.abs(nearby) >= lo) & (np.abs(nearby) <= hi)]
        out[i] = np.median(nearby) if len(nearby) >= 5 else overall
    return out


def clear_history(folder: Path) -> None:
    """Deflicker or a keyframe blend starts afresh: old passes no longer apply."""
    (Path(folder) / STATE_DIR / HISTORY).unlink(missing_ok=True)


# --- how strongly the slider moves the render ---------------------------------

def _slider_strength(seq, render, applied, base, history, raw_light, window) -> np.ndarray:
    """Render stops per slider stop, per frame."""
    if history:
        last = history[-1]
        prev_render, prev_applied = np.array(last["render"]), np.array(last["applied"])
        if len(prev_render) == len(render):
            moved = applied - prev_applied
            ok = np.abs(moved) > 0.02
            if ok.sum() >= 10:
                ratio = np.full(len(render), np.nan)
                ratio[ok] = (render[ok] - prev_render[ok]) / moved[ok]
                return _local(ratio, window)
        # Exposure barely moved since the last export (e.g. a colour-only pass):
        # any brightness change came from something else, so it can't teach us
        # the slider. Reuse what an earlier pass learned.
        for record in reversed(history):
            if len(record.get("strength") or []) == len(render):
                return np.array(record["strength"], dtype=np.float64)
        # Or learn it again from the last two earlier passes where Exposure did move.
        for older, newer in zip(reversed(history[:-1]), reversed(history[1:])):
            r0, a0 = np.array(older["render"]), np.array(older["applied"])
            r1, a1 = np.array(newer["render"]), np.array(newer["applied"])
            if len(r0) == len(r1) == len(render):
                moved = a1 - a0
                ok = np.abs(moved) > 0.02
                if ok.sum() >= 10:
                    ratio = np.full(len(render), np.nan)
                    ratio[ok] = (r1[ok] - r0[ok]) / moved[ok]
                    return _local(ratio, window)

    # First pass: how the render tracks the RAW and the corrections, frame to frame.
    raw, _ = raw_light()
    design = np.column_stack([np.diff(raw), np.diff(applied), np.diff(base)])
    coef, *_ = np.linalg.lstsq(design, np.diff(render), rcond=None)
    strength = float(np.clip(coef[1], *STRENGTH_RANGE))
    return np.full(len(render), strength)


def _local(ratio: np.ndarray, window: int) -> np.ndarray:
    """Frame-by-frame strengths are noisy: take a running median of the
    trustworthy ones, and fall back to the overall median where there are none."""
    ok = np.isfinite(ratio) & (ratio > STRENGTH_RANGE[0]) & (ratio < STRENGTH_RANGE[1])
    overall = float(np.median(ratio[ok])) if ok.any() else 1.0
    out = np.empty(len(ratio))
    half = max(window // 2, 3)
    for i in range(len(ratio)):
        nearby = ratio[max(0, i - half):i + half + 1]
        nearby = nearby[np.isfinite(nearby) & (nearby > STRENGTH_RANGE[0]) & (nearby < STRENGTH_RANGE[1])]
        out[i] = np.median(nearby) if len(nearby) >= 5 else overall
    return out


def _read_history(folder: Path) -> list:
    path = Path(folder) / STATE_DIR / HISTORY
    if not path.exists():
        return []
    passes = read_json(path, "refine passes").get("passes", [])
    return passes if isinstance(passes, list) else []


def _write_history(folder: Path, passes: list) -> None:
    write_json(Path(folder) / STATE_DIR / HISTORY, {"version": 1, "passes": passes}, indent=None)
