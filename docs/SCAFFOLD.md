# timelapse-core — Build Scaffold

**Purpose:** a free, open timelapse processing library, callable from other editors. Portfolio piece and genuine contribution.

**Companion document:** `Timelapse_Library_Brief.md` (the *what*). This is the *how*, plus every decision made so far and why. A dev session needs both.

**Stack:** Python ≥3.10 · numpy · Pillow · exifread · rawpy (optional, RAW) · ExifTool (optional, external) · pytest · MIT

_Last updated: 2026-10-07. Status: **milestone 1 built, green on synthetic data, awaiting a real sequence** (§8). **Milestone 2 (RapidRAW) built and green against real sidecars**; awaiting a real end-to-end run (§5.3)._

---

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
│   ├── cli.py                  # `timelapse check` / `timelapse deflicker`
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

### 4.4 Deflicker runs *after* ramp compensation
Deflicker can't tell a planned shutter change from flicker. It would smooth a deliberate step into a slope. Ramping (milestone 3) compensates known EXIF changes first; deflicker cleans what's left. Until then, `check_sequence` flags every setting change so the photographer knows.

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

### 5.3 Still to verify on real data
- [ ] `timelapse-rapidraw check` on a real CR3 folder
- [ ] `deflicker` then reopen in RapidRAW: values appear on the sliders, nothing else changed
- [ ] Does RapidRAW, with the folder open, overwrite our values? (Decides whether the "close first" warning can go.)
- [ ] `export` path: where the `rapidraw` command lives in the macOS/Windows install (pass `--rapidraw` until known)
- [ ] Flicker in the exported render measurably lower than without deflicker

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
8. ✅ Tests: 26 passing (one runs real ExifTool when installed), synthetic sequences with known truth
9. ⏳ **Real sequence** — run on an actual R6 CR3 timelapse (§8). *Nothing in the RAW path has touched a real file yet.*
10. ⬜ GitHub: push, fill `[[ ]]` placeholders (CI workflow already in `.github/workflows/tests.yml`)
11. ✅ `integrations/rapidraw/` — 16 tests passing on real sidecars; real end-to-end run pending (§5.3)
12. ⬜ `ramping.py` — "mark where the light changes"
13. ⬜ Motion blur simulation

---

## 8. Acceptance — milestone 1 ("working deflicker on a real timelapse")

- [x] `python -m pytest -q` clean
- [x] Exact measurement under pure exposure change, including varying sky clipping
- [x] Sunset curve preserved; flicker reduced >10× on synthetic data
- [x] Lightning frame doesn't drag neighbours
- [x] CLI reports gaps, setting changes, mixed folders in plain sentences
- [ ] `timelapse check` on a real CR3 folder reads shutter/aperture/ISO/time (needs ExifTool)
- [ ] `timelapse deflicker` on that folder completes in careful mode
- [ ] Corrections applied by hand to a handful of frames in an editor visibly reduce flicker in a render
- [ ] Careful vs fast compared on the same sequence; difference recorded here

---

## 9. Open threads

- **RapidRAW EXIF fallback in the core?** Currently integration-only (core must not know RapidRAW). Fine while ExifTool remains the recommendation.
- **Smooth by time, not frame index?** Currently the x-axis is frame number. Across a missing frame or an interval change, capture time would be more truthful. Small change in `smooth_flicker`; decide after seeing real data.
- **Region of interest.** Some sequences need deflicker measured on the landscape only, ignoring a moving sky. Possible `area=` parameter, photographer-named.
- **Iterative passes.** If an editor's exposure isn't a pure linear gain, one pass may under-correct. The integration can re-render, re-measure, and repeat; the core needs nothing new for that.
- **Package name.** `timelapse-core` is descriptive but generic on PyPI. Check availability before first release; consider something with more character.
- LICENSE and README contain `[[ ]]` placeholders (name, GitHub username).
