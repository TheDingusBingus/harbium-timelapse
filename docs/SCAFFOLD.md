# timelapse-core — Build Scaffold

**Purpose:** a free, open timelapse processing library, callable from other editors. Portfolio piece and genuine contribution.

**Companion document:** `Timelapse_Library_Brief.md` (the *what*). This is the *how*, plus every decision made so far and why. A dev session needs both.

**Stack:** Python ≥3.10 · numpy · Pillow · exifread · rawpy (optional, RAW) · ExifTool (optional, external) · pytest · MIT

_Last updated: 2026-10-07. **First full real run complete:** check → suggest-keyframes → keyframes → deflicker on a 229-frame R6 dusk sequence; scrub-through in RapidRAW looks right._ _Previously: Status: **milestone 1 proven on a real 229-frame R6 sequence; holy-grail ramping added as a result** (§4.4, §8). **Milestone 2 (RapidRAW) built and green against real sidecars**; awaiting a real end-to-end run (§5.3)._

---

## Vision

This library is the first-step processing core of a planned **end-to-end editor** (James's project). The core stays editor-agnostic; RapidRAW is its first adapter, the editor will be the next. Import-stage processes it should grow into: an analysis cache per shoot, lighting groups as well as sequences, **shoot-wide white balance consistency**, and the tool's own per-frame white balance estimate. Full notes: `docs/HANDOFF.md` §6.

## 0. Instructions for the dev session

Read this whole document before writing code.

**The hard rule:** `src/timelapse_core/` never imports anything from `integrations/`, and never knows any editor exists. If a task tempts you to put RapidRAW (or Lightroom, or Resolve) logic in the core, the abstraction is wrong. Fix the abstraction.

**Verify as you go.** After every change: `python -m pytest -q`. Do not batch up failures.

**Do not** add dependencies beyond the stack line above without recording the reason here. No GUI toolkit, no OpenCV, no SciPy (numpy does everything needed so far), no telemetry, no network calls.

---

## 1. Structure

```
timelapse-core/
├── pyproject.toml              # hatchling; console script `timelapse`
├── README.md                   # public face. Humble voice.
├── LICENSE                     # MIT — [[ name ]] placeholder to fill
├── docs/SCAFFOLD.md            # this file
├── src/timelapse_core/
│   ├── types.py                # THE CONTRACT — Frame, Sequence, Problem, FlickerCorrections
│   ├── exif.py                 # read settings. ExifTool batch → exifread fallback. Read-only.
│   ├── sequencer.py            # load, order, check. Plain-language Problems.
│   ├── measure.py              # brightness per frame, in stops (added; see §4.1)
│   ├── deflicker.py            # smooth_flicker → FlickerCorrections
│   ├── render.py               # preview video via ffmpeg (external, optional)
│   ├── cli.py                  # `timelapse check` / `deflicker` / `render`
│   └── __init__.py             # public API
├── tests/                      # synthetic sequences with known truth
└── integrations/rapidraw/      # milestone 2. Empty bar a README.
```

`measure.py` is the one addition to the brief's architecture. Measurement is shared by deflicker now and ramping later, so it earned its own module.

---

## 2. House rules

These are the library's equivalent of the boilerplate's tokens. Every new function obeys them.

| Rule | What it means in practice |
|---|---|
| **Stops, always** | Every brightness and exposure value in the public API is in stops, positive = brighter. No luminance floats, no percentages, no editor slider units. Conversion to an editor's units happens in that editor's integration. |
| **Photographer language** | Public names pass the test: could a photographer guess what it does without docs? `smooth_flicker`, `strength`, `window`, `speed="careful"`. Not `luminance_correction_vector`, not `alpha`. |
| **Sentences, not codes** | Every `Problem.message` is a full sentence naming the file and saying what to do. |
| **Never touch originals** | The core reads files. It never writes to a photo, ever. Sidecars are an integration's job. |
| **Degrade, don't crash** | Missing EXIF → `None` + a Problem. Unreadable file → unknown settings. Short sequence → returned unchanged. |
| **Humble voice** | README and messages: plain, warm, no claims of "industry-leading" anything. No digs at LRTimelapse. It's good software; this is a free alternative. |

---

## 3. The contract (`types.py`)

Built first; everything depends on its shape. Summary:

- `Frame` — path, `taken_at`, `shutter` (s), `aperture` (f-number), `iso`, `camera`; `.exposure_value` (EV100, higher = less light).
- `Sequence` — folder + ordered tuple of frames.
- `Problem` — `kind`, `message`, `frame` index, `serious` (results probably wrong if you carry on).
- `FlickerCorrections` — `brightness`, `target`, `exposure` arrays (stops), `.by_filename()`, `.flicker_before`, `.largest_correction`.

Change the contract deliberately and update this section when you do.

---

## 4. DECIDED — do not "fix" these

Each looks like it could be simplified. Each was chosen on purpose.

### 4.1 Measurement is rank-based across the whole sequence
Each frame becomes a **tone profile**: its tones ranked darkest to brightest at 0.5% steps, blown = `+inf`, black = `-inf`. Brightness is the mean of the band (within 10–90%) that is usable in **every** frame.

**Why:** the first version excluded blown pixels per frame, then trimmed. Brighter frames clip more pixels, so the averaged set changed with exposure and one stop measured as **0.89**. Deflicker would then have "corrected" an error of its own making. Rank-based with a shared band is exact: verified to ~1e-5 stops on 16-bit files with a sky that clips at the bright end of a sunset and not at the dark end (`test_sky_clipping_on_and_off_through_a_sunset_is_measured_exactly`).

Fallback: if the shared band is under 5% wide (some frame nearly all blown or black), each frame uses its own band. Less exact, still sensible.

### 4.2 "careful" (linear RAW) is the default, not "fast" (embedded JPEG)
Careful decodes RAW half-size, linear gamma, no auto-brightening, camera WB. A correction measured there is exactly what a linear exposure slider needs. Fast reads the camera JPEG, which has a tone curve baked in, so corrections are approximate. Fast is for a first look.

### 4.3 Smoothing is robust local linear regression (LOWESS), not a moving average
- Moving averages pull the first and last frames toward the middle (end droop). Local lines don't: a straight line passes through untouched, to 1e-9.
- Bisquare reweighting (4 passes) means one lightning frame doesn't darken its neighbours.
- Window counts frames, nearest-neighbour style, so edge frames still get a full window.

### 4.4 Your exposure changes are taken out, then given back as a gentle ramp (`ramping.py`)
Deflicker can't tell a deliberate exposure step from flicker. First version smeared a one-stop ND step into a 25-frame fade and built a hump around it. Now:
1. **Find steps.** *Where*: every camera-setting change, plus any sudden jump that lasts (≥5 frames either side) with no setting change — what a hand-turned ND looks like. Threshold: max(0.3 stops, 4 × typical frame-to-frame noise). Never fired on 20 seeds of pure 0.1-stop flicker.
2. **Measure each step from the pictures, never from the EXIF.** One straight line through up to 20 frames either side with a jump allowed at the step (shared slope, so a sunset's darkening isn't counted as step). Unbiased; ~0.05 stop typical error at real flicker levels.
3. **One-frame setting blips are not steps** (f/20, f/22, f/20): set aside, left to the robust smoother as an odd frame.
4. **Deflicker the light** (brightness minus steps) with `window`; **add the steps back** smoothed over `blend` frames (default max(3 × window, n/3)).

**Why the EXIF sizes aren't trusted** — first real sequence (R6, 229 CR3s, dusk, 2026-10-07): the f/22 frame measured −0.27 against a predicted −0.28, so measurement is sound; but third-stop ISO steps measured anywhere from 0.0 to +0.4, and across 1766–1807 the settings claim +3 stops while the pictures barely move. Cause unknown (simultaneous ND tweaks, the light falling, or Canon's third-stop ISO handling in RAW). Trusting EXIF sizes invented a −0.44 "ND step" and threw the f/22 frame's correction to +0.70. The settings say *where*; the pictures say *how much*.

### 4.4a Keyframes: suggested from the light, blended for every setting
- **Suggestions follow the real light**, not brightness jumps: brightness minus your exposure steps (§4.4), smoothed; a keyframe each ~1 stop of change or ~0.5 stop of colour shift, never closer than ~n/30 frames, first and last always. On the R6 sequence: 6 keyframes, bunching at the end where light falls fastest. Jumps are poor keyframes: they're your ND/ISO moves, the least representative frames.
- **Colour ("warmth") = log2(red/blue).** For RAW in careful mode it's read from the sensor's red and blue photosites *before* white balance, because the R6 shot on Auto WB (`WhiteBalance: 0` in the sidecar EXIF) and its per-frame guesses would masquerade as the light changing. Fast mode uses the preview, after camera WB: less trustworthy for colour.
- **Blending** (core `blend_between`): monotone cubic (Fritsch–Carlson), exact at keyframes, no overshoot, flat at first and last keyframe (ease in/out), no change of pace at middle keyframes. Holds outside the keyframe range.
- **The tool suggests; you decide.** It only labels with `--label`. The keyframe edits are the creative part.

### 4.5 ExifTool first, exifread second
**exifread cannot read CR3** (checked in its source: its ISO-BMFF parser only accepts heic/avif/mif1 brands). James's R6 shoots CR3. ExifTool reads everything, in one batch call per folder. If neither is available the sequence still loads and a Problem says how to fix it.

### 4.5a ExifTool is fed through an argument file, in batches of 100
First real run (Windows, 2026-10-07) hung inside the ExifTool call: every path on one command line, from an external drive with long folder names. Now: UTF-8 argument file (`-@`), `-charset filename=utf8`, `stdin=DEVNULL` so it can never wait for a keypress, 300s timeout per batch, progress after each batch. Results are matched back by normalised path (`normcase(abspath())`), because ExifTool on Windows reports paths with forward slashes. Tested with 250 files in a long, spaced, accented folder name.

### 4.6 Capture-time ordering
Frames are ordered by capture time when every frame has one. This survives Canon's counter rolling 9999 → 0001 mid-sequence. macOS `._` AppleDouble files are skipped.

---

## 5. RapidRAW (milestone 2)

### 5.1 Verified facts
From RapidRAW's releases and source docs, and from two real sidecars James exported from an EOS R6 (now test fixtures in `integrations/rapidraw/tests/fixtures/`):

- **Sidecar name:** `<image file name>.rrdata`, e.g. `181A0968.CR3.rrdata`. Virtual copies: `181A0968.CR3.<6 hex>.rrdata`.
- **Format:** JSON, 2-space indent, no trailing newline, non-ASCII written raw (there's a key called `centré`). Top level: `version`, `rating`, `adjustments` (84 keys), `tags`, `exif`.
- **`adjustments.exposure` is in stops, stored exactly as the slider reads** (slider +1.00 → `1`, +0.33 → `0.33`). Whole numbers are written without a decimal point.
- **Exposure is a pure linear gain**, `colour × 2^exposure`, applied ahead of the tone mapper. Linear RAW measurements from the core therefore transfer exactly. `brightness` is a different, midtone-shaping slider: **never touch it.**
- **`exif`** holds the frame's settings as text (`ExposureTime: "1/320 s"`, `FNumber: "f/4"`, `DateTimeOriginal: "2026-09-22 12:21:55"`, `SubSecTimeOriginal`). Used as a fallback when the core can't read a CR3.
- **CLI:** `rapidraw export <folder> --output <dir> --format jpeg --quality 90`. `--adjustments` applies one preset to all, so per-frame values go in per-frame sidecars.
- **Since v1.6.3, sidecars only exist for edited frames.** The integration refuses to write until every frame has one, and says how to fix it (paste settings onto all frames).

### 5.2 DECIDED — do not "fix" these
- **Separate package** (`integrations/rapidraw/`, `timelapse-rapidraw`), depends on the core, never the other way. MIT; it contains no RapidRAW code.
- **Corrections are added to the frame's own exposure, never written over it.** What gets smoothed is RAW brightness *plus* current exposure, i.e. what the photographer will actually see. A manual ramp is kept and built on.
- **Ledger** (`.timelapse-core/rapidraw.json`) records each frame's applied correction. Base exposure = current − applied, so re-running replaces rather than stacks, and a manual nudge made after deflicker survives.
- **Originals** copied once to `.timelapse-core/originals/` before first change. `undo` restores files byte-for-byte (tested).
- **Only `adjustments.exposure` changes.** Every other byte round-trips identically (tested against the real sidecars). Writes are atomic (`.tmp` + rename).
- **Virtual copies are left alone.**
- **Tell the user to close the folder in RapidRAW first.** It may save its in-memory edits over ours. Unverified whether it does; cheaper to warn than to find out on someone's sequence.

### 5.2a Keyframes in RapidRAW — DECIDED
- **Keyframe = purple colour label** (`tags` contains `color:purple`). One colour label per frame, so labelling replaces another colour (reported). **Unverified:** that RapidRAW offers purple and writes it as `color:purple`. Real sidecars showed `color:yellow` and `color:red`. Verify (§5.3).
- **`keyframes` overwrites the whole `adjustments` block of every non-keyframe frame** with the blend (James's call: in-between edits are replaced). Never touches `rating`, `tags`, `exif`.
- **Blended setting by setting.** First real dry run held "(everything)": real sidecars don't all carry the same keys (some RapidRAW writes, e.g. selective paste, store only some settings), and one key mismatch held the whole edit. Now a setting missing from some keyframes is held on its own ("not set on <keyframe>") and everything else still blends. Settings absent at the governing keyframe are left unset on the in-between frame, so RapidRAW applies its own default, exactly as on that keyframe.
- **Tone curves blend even with different points.** RapidRAW draws curves with cubic Hermite interpolation (per its source docs), and end points can slide along x for flat-line clipping. Each keyframe's curve is traced (monotone cubic Hermite, flat beyond its end points) at the union of every keyframe's x positions; heights are blended per x. Same points at the same x → point-by-point blend. **Known approximation:** if RapidRAW's exact tangent choice differs from monotone Fritsch–Carlson, frames right next to a keyframe can differ from it by a level or two on a strongly bent curve. Check visually on real data.
- **Keyframes are never rewritten** (byte-for-byte, tested). The blend is only ever written to frames between them.
- Per setting: same at all keyframes → copied exactly; numbers → blended (whole numbers stay whole); dicts with the same keys and lists of the same length → blended inside; anything else (booleans, `toneMapper`, differently shaped masks/curves) → **held** from each keyframe until the next, and named in the output.
- **Blending resets deflicker:** keyframe exposure is read as *your* exposure (current minus the ledger's applied correction), the blended exposure is written, the ledger is cleared. Run deflicker again after. Deflicker then builds on the blended exposure.
- **`check` is keyframe-aware:** with ≥2 keyframes it compares every frame with the blend (exposure aside); with 1 it says so; with none, the old "matches the rest" check, now also suggesting purple for a deliberate one-off.
- **`restore`** copies every sidecar back from `originals/` and clears the ledger. Backups are taken at the first *edit* change (blend or deflicker), **not** at labelling: otherwise `restore` would roll keyframe grades back to before they were made. Restore leaves labels.

### 5.2b FINDING: RapidRAW's Exposure slider is not one stop per unit in a render — `refine`
First real render (229-frame R6 dusk, six-keyframe grade): **render flicker 0.136 stops, worse than the RAWs' 0.111.** The ledger was correct (predicted 0.008 if the slider were 1:1). Regressing render frame-to-frame changes on RAW changes and slider changes: render moves ~1.5 stops per stop of real light (the grade's contrast), but ~2.5 per stop of slider, i.e. **the slider acted like 1.8× real light in the brighter half and 1.4× at dusk/night.** Corrections overshot and flipped the flicker (1659: RAW +1.01, applied −1.01, render −1.18). One global factor only reaches ~0.076; ~0.074 of the render's flicker wasn't explained by a single factor at all.

Decision: **deflicker on RAW is pass zero; the render has the last word.** `timelapse-rapidraw refine FOLDER EXPORT`:
- Pass 1: slider strength (render stops per slider stop) from regressing render changes on RAW changes, applied corrections and base exposure.
- Later passes: strength **per frame** from how each frame's render changed between exports versus how its slider changed (running median, clamped 0.3–6).
- Step = (smoothed render − render) / strength, clamped ±1 stop per pass; added to the ledger's `applied`, so `undo` still removes all of it.
- Stops when render flicker < 0.015 stops.
- History in `.timelapse-core/refine.json`; cleared by deflicker, blend, undo, restore.
- Simulated with a slider 1.4–1.8× too strong plus 1.5× grade contrast: 0.082 → 0.011 in one pass. Predicted on the real render from its own numbers: 0.136 → ~0.02 after pass 1.

### 5.2c INCIDENT: RapidRAW saved its defaults over every sidecar — guard + `rebuild`
Refine pass 1 ran with RapidRAW closed and worked: the next export measured **0.022 stops** (from 0.136). Pass 2 ran **with RapidRAW open on the folder**. Afterwards every sidecar held RapidRAW's *defaults* (exposure 0, Basic tone mapper, flat curve, no grade), keyframes included; only colour labels survived. Refine didn't write that: reproduced here with the real ledger and measurements, it writes valid files with exposure ≈ −0.83 and nothing else changed. Exposure 0 means RapidRAW saved *after* refine; most likely it re-read files mid-replacement, failed, fell back to defaults and saved them.

Decisions:
- **Every writing command refuses while a RapidRAW process is running** (`tasklist` on Windows, `ps` elsewhere; matches "rapidraw"). `--force` overrides. Reading commands (`check`, dry runs) always allowed. The old "close the folder first" warning wasn't enough.
- **`check` detects overwrites:** frames whose exposure ≠ the ledger's base + applied. ≥ max(3, n/10) → serious `overwritten`, pointing to `rebuild`; fewer → a note (your deliberate tweak, kept next deflicker). `deflicker` and `refine` refuse to build on overwritten sidecars.
- **`rebuild`**: originals back (current labels kept), ledger set aside, re-blend between keyframes, every ledger correction re-applied, ledger and refine history restored. Tested: a graded, keyframed, deflickered, refined sequence overwritten with defaults comes back setting-for-setting.
- Found while building: `rebuild` must set the ledger aside before blending, or the blend subtracts deflicker from keyframe originals that never had it.
- **Guard bug found on first Windows run:** matching "rapidraw" anywhere caught this tool's own `timelapse-rapidraw.exe`, so it always refused. Now matches names *starting* with "rapidraw".
- **The real damage was 2 frames, not 229:** `check` showed 227 sidecars still matching the ledger; only the frames clicked in RapidRAW were reset. The thumbnails misled. `check`'s note for a few mismatches no longer assumes the change was the user's.
- **Still to do:** tell a deliberate exposure tweak from a RapidRAW reset per frame (compare the rest of the edit with the originals and the blend), so a single reset frame is flagged as serious.

### 5.2d FINDING: Auto white balance is the remaining colour flicker
After refine (render brightness 0.013), James still saw flicker (leaves, left side) and "sudden jumps in colour warmth". Render colour wobble **0.051** vs **0.021** in the RAW sensor warmth: 2.5× more colour flicker than the light had. Render colour jumps beyond the real light correlate **−0.93** with the camera's as-shot white balance (ExifTool `ColorTempAsShot`: 4845–7415 K, jumps up to 669 K between frames). Biggest jumps at the **hand-turned ND moves** (1634, 1644–45, 1659): a variable ND shifts colour as it turns and AWB re-guesses; the rest at late ISO steps. Brightness was fine; colour wasn't measured by refine at all.

**UPDATE — RapidRAW *does* store absolute white balance**, learned from two real sidecars: `"whiteBalance": {"temperature": 5600, "tint": 0}` (`null` = as shot). The relative `temperature`/`tint` sliders are separate fields and stayed 0. Paste only copies the relative sliders, which is why copying couldn't fix it; the tool writes the absolute field per frame.

**`timelapse-rapidraw white-balance`** (the real fix): writes each frame's Kelvin directly. `--to smooth` (default; James's idea): the camera's as-shot Kelvin smoothed in mireds over `--smoothness` 50 frames. On the R6: biggest frame-to-frame change **669 K → 43 K**, journey 5250 → 7250 K kept. (76 frames: 37 K but flattens the ends; the final lamp-lit drop to ~5000 K only reaches ~5370.) `--to first` / Kelvin number for fixed. `--tint` default 0. Relative sliders untouched (keyframe grading stays on top). Ledger keeps `white_balance_before` for `--undo`; survives deflicker, full `undo`, `keyframes` re-blend and `rebuild`. `check` treats `whiteBalance` as per-frame like exposure. **Unverified:** RapidRAW applies the relative sliders on top of an absolute WB; and what tint the as-shot actually has (0 may shift green/magenta slightly).

**First real attempt, the relative-slider colour loop, made it worse** (more obvious colour flicker after one pass): its first-pass estimate of the Temperature slider's effect was confounded by keyframe temperatures set in response to colour, or its guess was far off. It's now opt-in (`refine --colour`), experimental; `undo --colour` removes it while keeping exposure work. Original description below for the record. Decision was: **colour channel in `refine`**, the same closed loop as exposure, on the Temperature slider:
- `--white-balance smooth` (**default when ExifTool gives as-shot Kelvin; James's idea**): target = render colour with the camera's as-shot mired replaced by a heavily smoothed curve of it (window max(3×window, n/3)). Removes only the camera's frame-to-frame guessing; keeps the auto look and real colour changes in the light (lamps coming on).
- `steady`: target = smoothed render colour. Stronger; irons out real colour changes too. Default without Kelvin.
- `first` / Kelvin number: as if every frame were shot at one white balance; keyframe looks shift.
- Effect of the camera's guess on the render measured from the export by regression (real R6: **−0.0152 warmth per mired**). Temperature slider strength: first pass from keyframe temperatures across the sequence (levels regression with light colour and as-shot mired; used if fit > 0.5), else a careful capped guess (±10 units); later passes per frame from export-to-export changes, sign learned, within 4× of the overall.
- Ledger gains `applied_temp`; `undo` and `rebuild` handle it; `--no-colour` skips it. Colour target is stored in refine history for fixed and smooth modes so passes converge on one target.
- Simulated (real-calibrated AWB effect, ND/ISO jumps, unknown slider scale): steady 0.03+ → < 0.01 in 4 passes; smooth matches the smoothed-Kelvin ideal within 0.05 and keeps a 0.15 lamp warmth step.

Leaves: 5 s exposures in wind blur foliage differently each frame: motion shimmer, not exposure. `timelapse render --blend-frames 2|3` (ffmpeg `tmix`) blends neighbours; on random 0.15-stop jitter it cut wobble 0.114 → 0.034. That's the brief's "motion blur simulation" in its simplest form.

### 5.3 Still to verify on real data
- [x] `timelapse-rapidraw check` on a real CR3 folder
- [x] `deflicker` then reopen in RapidRAW: values appear on the sliders, nothing else changed (229 frames, 0.111 → 0.008 stops)
- [ ] Does RapidRAW, with the folder open, overwrite our values? (Decides whether the "close first" warning can go.)
- [ ] `export` path: where the `rapidraw` command lives in the macOS/Windows install (pass `--rapidraw` until known)
- [ ] Flicker in the exported render measurably lower than without deflicker
- [x] **Purple label round trip:** a frame labelled purple in RapidRAW is found by `keyframes --dry-run`, and a frame labelled by `suggest-keyframes --label` shows purple in RapidRAW
- [x] Blended in-between frames open in RapidRAW with sensible sliders (223 frames, six keyframes, curves included; rough scrub-through looks right)
- [x] Sensor warmth on real CR3s: steady (~0.01 frame-to-frame), tracks shade → golden → blue hour → lamp-lit night

## 6. Non-negotiables

| | |
|---|---|
| Originals | Never written to. |
| Units | Stops, positive = brighter, everywhere public. |
| Measurement accuracy | A pure exposure change is measured to within 0.002 stops on 16-bit files. A test enforces it. |
| Trend preservation | A straight brightness line passes through deflicker unchanged. A test enforces it. |
| Outliers | A single +2 stop frame moves its neighbours' targets by < 0.05 stops. A test enforces it. |
| Core isolation | No editor-specific code or imports in `src/timelapse_core/`. |
| Errors | Plain sentences naming the file. No stack traces for expected problems in the CLI. Library warnings (e.g. exifread's per-file noise) are silenced in favour of one summarising Problem. |
| Dependencies | numpy, Pillow, exifread required. rawpy optional. Nothing else without a reason recorded here. |

---

## 7. Build order

1. ✅ Repo, `pyproject.toml`, MIT, README
2. ✅ `types.py` — the contract
3. ✅ `exif.py` — ExifTool batch + exifread fallback
4. ✅ `sequencer.py` — load, order, check
5. ✅ `measure.py` — rank-based, shared band
6. ✅ `deflicker.py` — robust LOWESS
7. ✅ `cli.py` — `check`, `deflicker --csv`
8. ✅ Tests: 63 core passing (one runs real ExifTool when installed), synthetic sequences with known truth plus cases taken from the real R6 sequence
9. ⏳ **Real sequence** — run on an actual R6 CR3 timelapse (§8). *Nothing in the RAW path has touched a real file yet.*
10. ⬜ GitHub: push, fill `[[ ]]` placeholders (CI workflow already in `.github/workflows/tests.yml`)
11. ✅ `integrations/rapidraw/` — 16 tests passing on real sidecars; real end-to-end run pending (§5.3)
12. ✅ `ramping.py` — automatic: finds setting changes and ND-style jumps, blends them (§4.4)
12d. ✅ RapidRAW-running guard, overwrite detection in `check`, `rebuild` (§5.2c)
12c. ✅ `refine` — closed-loop deflicker against the exported render (§5.2b)
12b. ✅ `timelapse render` — preview MP4 from finished frames via ffmpeg, any names, native size
12a. ✅ Keyframes — core suggestions + blending; RapidRAW `suggest-keyframes`, `keyframes`, `restore`, keyframe-aware `check` (§4.4a, §5.2a). 63 core + 29 integration tests
13. ⬜ Motion blur simulation

---

## 8. Acceptance — milestone 1 ("working deflicker on a real timelapse")

- [x] `python -m pytest -q` clean
- [x] Exact measurement under pure exposure change, including varying sky clipping
- [x] Sunset curve preserved; flicker reduced >10× on synthetic data
- [x] Lightning frame doesn't drag neighbours
- [x] CLI reports gaps, setting changes, mixed folders in plain sentences
- [x] `timelapse check` on a real CR3 folder reads shutter/aperture/ISO/time (229 frames, Windows, external drive)
- [x] `timelapse deflicker` on that folder completes in careful mode (flicker 0.111 → 0.009 stops)
- [x] A rendered video, judged by eye: **failed** (0.136 stops, worse than the RAWs) → §5.2b, `refine`
- [x] Refine pass 1 on the real render: 0.136 → **0.022** stops
- [ ] `rebuild` restores the real sequence after the RapidRAW overwrite (§5.2c)
- [ ] RapidRAW-running guard fires on Windows
- [x] Refine passes bring it under 0.015 (0.136 → 0.022 → **0.013**)
- [ ] Looks right at full speed: **not yet** (colour from Auto WB, leaf motion: §5.2d)
- [x] Colour refine (relative slider) on the real sequence: **made it worse**; now opt-in
- [ ] `white-balance` on the real sequence: render colour wobble from 0.051 down near the light's own; no sudden warmth jumps by eye
- [ ] Confirm relative Temperature/Tint still apply on top of an absolute white balance in RapidRAW
- [ ] Cross-check learned Temperature strength against RapidRAW's Kelvin readout
- [ ] Careful vs fast compared on the same sequence; difference recorded here

---

## 9. Open threads

- **RapidRAW EXIF fallback in the core?** Currently integration-only (core must not know RapidRAW). Fine while ExifTool remains the recommendation.
- **Third-stop ISO puzzle (§4.4).** Worth a controlled test: same scene, ISO 100/125/160/200, locked everything else. Tells us whether Canon's in-between ISOs reach the RAW data.
- **Measurement speed.** Real timing on careful mode still unreported; multi-core measurement is the obvious next speed-up.
- **The tool's own white balance check per frame** (James): estimate each frame's white balance from the sensor colour (already measured) rather than trusting the camera's Auto guess, smooth that, write it. Independent of camera settings; harder to fool with a red coat or a turned ND.
- **Smooth by time, not frame index?** Currently the x-axis is frame number. Across a missing frame or an interval change, capture time would be more truthful. Small change in `smooth_flicker`; decide after seeing real data.
- **Region of interest.** Some sequences need deflicker measured on the landscape only, ignoring a moving sky. Possible `area=` parameter, photographer-named.
- **Iterative passes.** If an editor's exposure isn't a pure linear gain, one pass may under-correct. The integration can re-render, re-measure, and repeat; the core needs nothing new for that.
- **Lens profile omissions.** RapidRAW only wrote `lensMaker`/`lensModel` into keyframes that were opened for editing; three keyframes lacked them, which would have switched lens correction off mid-sequence. Fixed by hand (paste lens settings). Candidate: auto-fill a setting that's identical on every keyframe that has it and missing from the rest, and say so.
- **Package name.** `timelapse-core` is descriptive but generic on PyPI. Check availability before first release; consider something with more character.
- LICENSE and README contain `[[ ]]` placeholders (name, GitHub username).
