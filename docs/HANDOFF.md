# harbium-timelapse — Handoff & Working Log

_Prepared 9 October 2026, for moving into the end-to-end editor project._
_Companion to `docs/SCAFFOLD.md` (the design record: every decision and why). This file is the story, the state, and what's next. Read this first, then the scaffold sections it points to._

> **Current state (20 Oct 2026):** renamed **harbium-timelapse** (one package: core + editor adapters; RapidRAW built in). Workflow: **`timelapse prepare FOLDER` → one export → `timelapse finish EXPORT --video film.mp4`**; `refine` is optional. A full code review (docs: Addendum 3) fixed two critical and three high issues before the first public push. On the real R6 sequence the finished video measured 0.012 stops brightness change (the light's own pace) and 0.006 colour wobble; by eye "better than LRTimelapse, and significantly easier". Read **Addendum 2** at the end for everything learned on the way (Dehaze, LibRaw auto-maximum, crowds, 8-bit averaging). Next: a second, different sequence; patch-weighted measuring in `finish` for the small steps left at big ND turns.

---

## 0. In one breath

A free, MIT-licensed Python library for timelapse processing, in photographer language, plus a RapidRAW integration. Proven on one real sequence (229 frames, Canon R6, CR3, dusk, Windows): render flicker taken from **0.136 → 0.013 stops** (invisible). The remaining visible problem was **colour flicker from Auto white balance**; the proper fix (`white-balance`, writing smoothed absolute Kelvin into RapidRAW) is built and tested but **not yet run on the real sequence**. Four to six small jobs stand between here and a public MVP (§5).

---

## 1. What exists

**Two packages, one repo.** Core never imports the integration; the integration depends on core.

```
harbium-timelapse/
├── src/harbium_timelapse/            MIT, editor-agnostic
│   ├── types.py        THE CONTRACT: Frame, Sequence, Problem, FlickerCorrections, ExposureChange
│   ├── exif.py         ExifTool batch (argfile, 100/batch) → exifread fallback. Reads as-shot Kelvin.
│   ├── sequencer.py    load, order by capture time, check (gaps, mixed files, setting changes)
│   ├── measure.py      brightness (stops) + warmth (log2 R/B) per frame; RAW-native
│   ├── ramping.py      your exposure steps: from EXIF (where) + pictures (how big); ND detection
│   ├── deflicker.py    robust LOWESS; steps out, blended back as a long ramp
│   ├── keyframes.py    suggestions from real light/colour; monotone cubic blending
│   ├── render.py       preview MP4 via ffmpeg: --fps, --width, --max-mb, --blend-frames
│   └── cli.py          `timelapse check | deflicker | render`
├── integrations/rapidraw/          separate package `timelapse-rapidraw`, MIT, no RapidRAW code
│   └── src/harbium_timelapse.editors.rapidraw/
│       ├── sidecar.py      .rrdata read/write, byte-faithful, atomic
│       ├── deflicker.py    plan/apply/undo, ledger, keyframe-aware check, overwrite detection
│       ├── keyframes.py    purple labels, blend every setting incl. curves, keyframes never rewritten
│       ├── refine.py       closed loop against the exported render (exposure; colour opt-in)
│       ├── whitebalance.py smoothed as-shot Kelvin → absolute whiteBalance field
│       ├── recover.py      RapidRAW-running guard, rebuild after overwrite
│       ├── state.py        .timelapse-core/: originals, ledger, refine history
│       └── cli.py          `timelapse-rapidraw check | suggest-keyframes | keyframes | deflicker |
│                            refine | white-balance | undo | restore | rebuild | export`
├── docs/SCAFFOLD.md   design record (house style: contract, decided/don't-fix, non-negotiables)
├── docs/HANDOFF.md    this file
└── .github/workflows/tests.yml   CI: both suites, Python 3.10–3.13
```

**Tests:** 69 core + 66 RapidRAW, all passing. Synthetic sequences with known truth, plus cases taken from the real R6 run (real sidecars as fixtures, serial number scrubbed; the real as-shot Kelvin curve as a fixture).

**Dependencies:** numpy, Pillow, exifread; optional rawpy (RAW). External, optional: ExifTool (needed for CR3 and as-shot Kelvin), ffmpeg (render). All permissive or run as separate programs. No code taken from any other project (credits in README).

---

## 2. Working log

Chronological. Each line: what happened → what it changed.

1. **Milestone 1 built** (sequencer, measurement, deflicker, CLI) on synthetic data. Found **exifread can't read CR3** → ExifTool first. Found **measurement bias** (1 stop read as 0.89: clipping changes which pixels are averaged) → rank-based shared tonal band, exact.
2. **RapidRAW schema learned** from two real sidecars: `adjustments.exposure` in stops, linear gain; `<name>.CR3.rrdata`; virtual copies left alone. Integration built: corrections *added* to your exposure, ledger, originals, undo.
3. **Windows setup** (venv, PATH, ExifTool Perl-vs-exe mix-up). **First real `check` hung**: hundreds of long paths on one command line → argfile batches; ExifTool's forward-slash paths broke matching → normalised paths.
4. **First real deflicker** (0.111 → 0.009 predicted). Found lasting jumps at 1634/1645/1659 with no setting change = **hand-turned variable ND** → `ramping.py`. Found **third-stop ISO steps don't reliably show in the RAW** (EXIF says +3 stops over 40 frames, pictures barely move) → step *location* from EXIF, step *size* from pictures. Single f/22 frame → blips aren't steps.
5. **Keyframes:** suggestions from real light (not jumps); sensor warmth before WB; purple label (verified both directions in RapidRAW); setting-by-setting blending after real sidecars turned out to carry different key sets; **tone curves with different points** blended via union of x positions; lens profile only written into keyframes opened for editing (fixed by hand).
6. **First real render: worse than the RAWs** (0.136 vs 0.111). Regression on render vs RAW vs applied: **RapidRAW's Exposure slider acted like 1.4–1.8 stops of real light** under the grade, varying along the sequence. Corrections overshot and flipped the flicker → **`refine`**, closed loop against the export. Pass 1: **0.136 → 0.022**. Pass 3: **0.013** (done).
7. **Incident:** refine pass 2 ran **with RapidRAW open**; RapidRAW saved defaults over the frames clicked (2 frames; thumbnails made it look like all 229). Recovered fully via originals + ledger → **`rebuild`**, **guard** (refuse to write while RapidRAW runs), overwrite detection in `check`. **Guard bug on first Windows run**: it matched its own `timelapse-rapidraw.exe` → match names *starting* with "rapidraw".
8. **Render by eye: still flickery** (leaves, left side) **and sudden colour jumps.** Data: render colour wobble **0.051** vs light's **0.021**; correlates **−0.93** with camera as-shot WB (4845–7415 K, jumps up to **669 K**), worst at ND turns. Leaves also = wind in 5 s exposures → `render --blend-frames` (tmix; 0.114 → 0.034 on jitter).
9. **Colour refine via the relative Temperature slider made it worse** on real data (first-pass slider estimate confounded by keyframe temperatures, or the guess was far off) → now opt-in (`refine --colour`), `undo --colour` removes it.
10. **RapidRAW stores absolute WB**: `"whiteBalance": {"temperature": K, "tint": t}` (`null` = as shot); paste only copies relative sliders. → **`white-balance`**: camera's as-shot Kelvin smoothed in mireds over 50 frames (James's idea). On the real data: **669 K → 43 K** max frame-to-frame, journey 5245–7250 K kept. **Dry run on the real sequence matched; not yet applied.**
11. **Housekeeping:** `render --max-mb` (two-pass, 9.2 MB on worst-case 229 frames at full HD); camera serial number scrubbed from fixtures and all git history; README credits prior work (LRTimelapse, a darktable AWB-smoothing script, dtLapse) and licences.

**Real sequence state right now** (F:\…\dusk shoot\timelapse photos): keyframes blended (6 purple), deflicker + refine exposure applied (render 0.013). Colour refine was most likely removed with `undo --colour` (the dry run showed only 3 temperature mismatches, probably the two Kelvin test frames). `white-balance` dry-run only.

---

## 3. Key findings (the things worth carrying anywhere)

| Finding | Number | Consequence |
|---|---|---|
| Measure by tonal rank, shared band across the sequence | exact to ~1e-5 stops as a sky clips and unclips | never average "unclipped pixels" per frame |
| EXIF says *where* exposure changed, pictures say *how much* | third-stop ISO steps measured 0.0–0.4; +3 EV claimed, ~0 seen | don't trust EXIF step sizes |
| Variable ND leaves no EXIF trace | lasting jumps ≥ max(0.3, 4×noise) | detect from pictures; also shifts colour |
| An editor's sliders aren't what the docs say | RapidRAW Exposure: 1.4–1.8× real light, grade-dependent | the render gets the last word (closed loop) |
| Auto WB jitter is the dominant colour flicker | −0.93 correlation; 669 K jumps | smooth as-shot Kelvin, write absolute WB |
| Relative-slider colour correction is unreliable | made it worse | use absolute controls where they exist |
| Editors can overwrite sidecars while open | 2 frames reset to defaults | refuse to write while the editor runs; keep originals + ledger |
| Brightness metric ≠ "looks steady" | 0.013 brightness, still visible colour + leaf shimmer | measure colour; separate motion shimmer |

---

## 4. Concerns & considerations

**Unverified on real data**
- `white-balance` applied to the real sequence, and whether it looks right at full speed.
- **Whether RapidRAW still applies the relative Temperature/Tint sliders on top of an absolute white balance.** The Kelvin test sidecars had relative Temperature at 0, hinting RapidRAW may reset or ignore it in Kelvin mode. If so, keyframe colour grading needs folding into the Kelvin itself (keyframe Kelvin blended on top of the smoothed camera curve).
- Tint: written as 0. The camera's as-shot tint isn't known in RapidRAW's units, so this may shift green/magenta slightly.
- Curve tracing assumes monotone cubic Hermite; RapidRAW's exact tangents unknown (a level or two off near keyframes on strongly bent curves).

**Known limitations**
- Proven on **one** sequence, camera, editor and OS. Every claim should stay that size until more.
- Brightness measures the midtones only (ignores highlights/sky by design); local light changes (sun patches) can't be fixed by one value per frame (region of interest: open thread).
- Leaves in wind are motion, not exposure: only frame blending helps.
- `check` flags a *few* reset frames only as a note, not serious (can't yet tell a user tweak from a RapidRAW reset per frame).
- Smoothing runs on frame index, not capture time.
- Colour refine (relative slider) remains in the code as experimental; decide whether to keep or remove.

**Risks**
- **RapidRAW is a moving target** (sidecar format changed in 1.6.3; behaviour undocumented). Two surprises so far. Every integration claim is pinned to versions tested in Oct 2026.
- **Old git history on GitHub:** if the repo was pushed before the scrub, the serial number is in that history. Delete and re-push the cleaned repo.
- Package name `harbium-timelapse` is generic on PyPI; check before publishing.

---

## 5. Next steps to finish the library (in order)

1. **Run `white-balance` on the real sequence** (RapidRAW closed), check 1633→1634 in RapidRAW's Kelvin readout, and **answer the relative-slider question** (§4). If relative sliders are ignored in Kelvin mode: fold keyframe colour intent into the Kelvin (small change in `whitebalance.py` + keyframes).
2. **Export, render, watch.** Measure render colour wobble (`timelapse deflicker <export> --csv`, warmth column) against the light's 0.021. Try `--blend-frames 2` for the leaves. This is the MVP proof.
3. **Windows install guide** in the README: venv, activation per terminal, PowerShell execution policy, ExifTool (the Windows *executable* + `exiftool_files`, renamed, on PATH), ffmpeg via `winget install Gyan.FFmpeg`, OneDrive "keep on this device". Every bump James hit.
4. **GitHub:** fill `[[ your name ]]` (LICENSE) and `[[ username ]]` (README); delete any pre-scrub repo and push this clean one; confirm CI goes green.
5. **A second sequence**, different shoot (ideally fixed WB, no ND, or a different camera), to check nothing is tuned to this one.
6. Then, in rough priority: per-frame "was this reset by RapidRAW?" detection (compare the rest of the edit with originals and blend); tint handling; lens-profile auto-fill for keyframes missing it; decide on `refine --colour`; smooth by capture time; region-of-interest measurement; a PyPI name.

---

## 6. Carrying this into the end-to-end editor

**Reuse as-is:** the core library. It never knew an editor existed; RapidRAW is just the first adapter. Your editor becomes the second.

**Patterns to inherit:**
- **One contract, universal units:** stops for exposure, Kelvin/mireds + tint for white balance. Adapters translate to controls.
- **Plan, show, apply:** every correction is computed first (dry run), shown, then written on approval.
- **Never touch originals; everything undoable;** a ledger of what the tool changed, so re-runs replace rather than stack, and a user's later tweak is kept.
- **Measure the result, not the intention:** if anything between the correction and the pixels isn't perfectly known, close the loop on the output.

**What owning the editor removes:** the slider-mapping problem (define exposure as true linear gain and white balance as absolute Kelvin/tint yourself; corrections apply exactly, first time, no refine loop), and the overwrite risk (one program owns the edit state).

**Build next, so the editor inherits it:**
- **Analysis cache per shoot:** decode each RAW once; store brightness, tone profile, warmth, settings, as-shot WB beside the photos. Every later step reads the cache.
- **Grouping as a first-class idea:** timelapse sequences *and* lighting groups for events, both on capture time.
- **Shoot consistency as an import-stage process** (James's plan): group shots by lighting (time + settings), find each group's consensus white balance ignoring content-fooled outliers, pull shots towards it with a strength setting (mixed light should stay mixed), optional match-to-reference (a graded hero frame or grey card), drift through the day via time-based smoothing. Needs tint (LED/fluorescent casts). First test set candidate: the Sobremesa shoot (daylight → candlelight at 5 pm).
- **The tool's own white balance estimate per frame** from sensor colour, rather than trusting the camera's guess (James's idea): independent of camera settings, harder to fool.

**Honest scale note:** a full editor means RAW decoding, colour science, a fast image pipeline, UI and a catalogue, each substantial. The suggested path: finish the library, perhaps an "import assistant" that runs first-step processes and hands off to existing editors, then grow the editor around a proven core.

---

## 7. Commands cheat sheet (Windows)

```bat
:: every new terminal
C:\Users\you\Documents\timelapser\.venv\Scripts\activate

:: read-only, any folder of images
timelapse check "FOLDER"
timelapse deflicker "FOLDER" --csv out.csv
timelapse render "EXPORT" --max-mb 10 --blend-frames 2 --out C:\Users\you\Desktop\demo.mp4

:: RapidRAW (close RapidRAW first: writing commands refuse while it runs)
timelapse-rapidraw check "FOLDER"
timelapse-rapidraw suggest-keyframes "FOLDER" --label
timelapse-rapidraw keyframes "FOLDER" --dry-run
timelapse-rapidraw keyframes "FOLDER"            :: resets deflicker: run deflicker after
timelapse-rapidraw deflicker "FOLDER"
timelapse-rapidraw white-balance "FOLDER" --dry-run
timelapse-rapidraw white-balance "FOLDER"         :: --smoothness 80 calmer, --to 5600 fixed, --undo
timelapse-rapidraw refine "FOLDER" "EXPORT"       :: repeat after each export until "done"

:: safety
timelapse-rapidraw undo "FOLDER"                  :: remove deflicker/refine (keeps white balance)
timelapse-rapidraw undo "FOLDER" --colour         :: remove only refine's colour
timelapse-rapidraw rebuild "FOLDER"               :: after RapidRAW overwrites sidecars
timelapse-rapidraw restore "FOLDER"               :: everything back to before the tool
```

**Order of work:** check → suggest-keyframes → (grade purple keyframes in RapidRAW) → keyframes → deflicker → white-balance → export → refine → export → … → render.

---

## Addendum — 9 Oct 2026 (built while James was away; branch `white-balance-exact`, not merged)

**Read in RapidRAW's source (HEAD eb3556b, 9 Oct 2026) — facts, re-check per release:**
- **Relative sliders DO apply on top of an absolute white balance.** Render target = `whiteBalance` (or as-shot if `null`), then shifted by `temperature` × 1.5 mired and `tint` × 1.5 tint units. Answers §4's open question (in this version). Exception: if `sectionVisibility.color` is false, the render uses as-shot and ignores both.
- **RapidRAW's Kelvin/tint are standard and physically defined:** Robertson isotherms, tint = −3000 × offset from the locus (the DNG SDK convention), as-shot worked out from the raw neutral with rawler's dual-illuminant (A + D65) matrices. Tint range ±150; Kelvin 2,000–50,000.
- **Consequence: `white-balance` as merged writes Canon's `ColorTempAsShot` (Canon's private Kelvin scale) with tint 0 into a field RapidRAW reads on its own scale.** Even unsmoothed, that is a global cast against as-shot, likely greener (our R6 samples read tint +11 and +20) plus an unknown Kelvin offset. Flicker would be fixed; colour would shift against the graded keyframes.
- **Exposure is exact linear gain (`× 2^exposure`) before the AgX tonemapper.** The 1.4–1.8× seen by `refine` is output-side tonemapper/curve contrast, not a slider error.
- **RapidRAW decodes at each frame's own as-shot WB** and only adapts when `whiteBalance` is set. With `null`, Auto WB's per-frame multipliers reach the pixels (colour *and* some brightness). Hypothesis: part of the original overshoot. Prediction: with exact WB applied, `refine` converges in fewer passes.

**Built (branch `white-balance-exact`, 97 core + 71 RapidRAW tests passing):**
- `harbium_timelapse/colour_temperature.py` — neutral ↔ Kelvin/tint, written from the published standards (Robertson table, DNG dual-illuminant interpolation). No editor code. Checked against a real frame: a solved neutral `0.644 1 0.474` → tint **+20** (Lightroom read **+21**); Kelvin 3,790 vs Lightroom's 3,550 — a *matrix* difference (Lightroom's R6 profile vs rawler's), not maths. **Lesson: the neutral is the portable truth; Kelvin is per-editor.**
- `Frame.white_balance_neutral` (contract, optional) read by ExifTool from `WB_RGGBLevelsAsShot` (Canon) or `AsShotNeutral` (DNG).
- `harbium_timelapse.editors.rapidraw/cameras.py` — RapidRAW's (rawler's) matrices; R6 only so far.
- `white-balance` now works out as-shot on RapidRAW's own scale, smooths mireds *and* tint, writes both. With no jitter it writes exactly RapidRAW's as-shot (identity test). Falls back to the old behaviour with a printed note when the neutral or camera is unknown. `--tint` now defaults to automatic.

**To verify on return (RapidRAW closed):**
1. `timelapse check "FOLDER"` still fine; then in Python: `load_sequence(...)[0].white_balance_neutral` is not None (ExifTool reads the RGGB levels on CR3).
2. **Identity check:** pick one frame, note RapidRAW's as-shot Kelvin/tint readout; `white-balance --dry-run` should print the same (± 1 K, ± 0.5 tint) as "camera" for that frame. This proves our scale = RapidRAW's.
3. Apply; check 1633→1634 steady, keyframe grades still present on top; export, render, measure colour wobble vs the light's 0.021.
4. Then re-run `refine` and note passes needed / slider factor (tests the hypothesis above).

---

## Addendum 2 — 19 Oct 2026: what the real sequence taught us, and the `finish` paradigm

**Verified on the real sequence (R6, 229 frames):**
- **Exact white balance works.** Our Kelvin/tint = RapidRAW's own as-shot readout on 181A1579 (6457 K / +27, checked in the app). Tint really runs +27 → −9 through dusk; the old tint-0 write would have cast it. Colour wobble 0.051 → 0.029.
- **RapidRAW Dehaze reads haze before Exposure** (`refined_dark_channel` samples `input_texture`; fixed atmospheric light 0.95/0.97/1.0). Brighter raw → harder dehaze → midtones down, deep shadows up. Measured: at the 1659 ND turn the tone shape jumped +0.42 / −0.27; with Dehaze 0 alone: −0.05 / +0.06. RAW itself: pure brightness step (shape ±0.1). **Never use Dehaze on sequences with exposure changes.** Worth an upstream issue: scale the dark channel by the exposure gain.
- **LibRaw's default `adjust_maximum_thr = 0.75` invented flicker in our own measurement.** A frame whose brightest pixel sat just above 75% of white was rescaled to it: +0.32 to +0.44 stops on 1626/1628/1631/1632, whose light was steady (raw sensor counts: ±0.03). Also per-frame camera WB leaked into brightness. Fixed in `measure.py` (auto-maximum off, one fixed WB for every frame), with tests. Much of the old "slider acts like 1.4–1.8 stops" and the first render being worse than the RAWs (0.136 vs 0.111) came from this.
- **Even then, RapidRAW moves the render ~18% more per stop of Exposure than per stop of real light** (2.15 vs 1.83 on the real 1634 ND step, same with the grade stripped back). Cause unknown; not chased. RAW-sized corrections therefore land within ~0.05 but never exactly in RapidRAW.
- Result in RapidRAW: deflicker (true measurement) → export 0.046 → one exposure-only refine pass → **0.013**, biggest frame jump 0.05. Colour still 0.030 (ND/ISO casts at 1802, 1659, 1804, 1645).

**Refine fixes:** colour passes leave Exposure alone (`--colour` = colour only); Exposure strength is remembered per pass, or relearned only from passes where Exposure moved (a colour-only pass once taught it nonsense: 1.5-stop errors); first Temperature guess 0.012/unit from RapidRAW's 1.5 mired/unit (0.005 overshot ~2.5×).

**Paradigm shift (James, agreed): correct the finished frames, not the editor's sliders.** Steering a black-box renderer through sliders needs its response modelled, and every editor has its own surprises (Dehaze, the 1.18 ratio). New `timelapse finish`: measures each exported frame per colour channel on the shared tonal band, smooths each channel's journey, applies one linear-light gain per channel per frame through exact lookup tables. Global, no halos, exact by construction (it corrects what it measures, in the same terms), works after any editor, no adapter needed. Writes corrected frames (same names/formats; JPEG q95 4:4:4; 16-bit TIFF via `tifffile`) and/or encodes a video straight from them (nothing stored in between). Synthetic test: brightness 0.056 → 0.005, colour 0.041 → 0.008, journey kept. Speed at 20 MP: ~0.5 s/frame measuring, ~1 s/frame writing JPEG, ~2 s/frame encoding 4K.

**Intended workflow now:** prepare (white-balance + deflicker into sidecars: the coarse work, before the grade) → export once from any editor → `finish`. `refine` becomes optional. In James's own editor, the same library corrections apply before the non-linear stages, exactly, in one pass.

**Lessons (process):** three times the bug was our own instrument (whole-frame warmth with per-frame masks; LibRaw auto-maximum; a script reading the ledger wrong), and one test had an invalid premise. Check the ruler against ground truth (raw sensor counts) before building theories on it; after a failed prediction, stop and rethink rather than generating the next theory.

**RapidRAW notes:** headless export can't resize (`resize: None` hardcoded) — every measuring export is full-res; a `--resize` flag upstream would speed everyone up. `--adjustments FILE` overrides a frame's edit without touching sidecars (useful for controlled tests).

**Moving content (added later, 19 Oct):** walkers swayed both measurements. Frame 1670: a crowd of dark clothing over ~15% of the frame read as −0.2 stops on the RAW whole-frame measure → deflicker brightened the frame by mistake (static trees +0.6 in the export) → `finish`, swayed the same way, only pulled back 0.08. Fix in `measure.py` (used by deflicker, refine and finish): per-pixel changes since the previous frame identify movers (changed > max(0.25, 3×spread) stops off the frame's typical change); the change is the ratio of the remaining still pixels' total brightness; chained frame to frame; whole-frame tone measure fills any pair that can't be compared. A per-pixel *median* was tried first and rejected: 8-bit rounding made it noisy (0.009 per frame). Synthetic, with a 15% dark crowd: whole-frame error 0.13 (worst 0.34) → still-pixel ratio 0.003 (worst 0.010); clean scenes as exact as before. Needs a fixed camera (any tripod timelapse); `steady=False` restores the old measure.

**Dark frames need 16 bits (added later, 19 Oct):** after the crowd fix, the checker (`find_odd_frames.py`, robust per-patch view) still found ~0.1 residuals at 1800–1801 and small leftover steps at the ND turns 1645/1659; James: "perceivable, not aggressive". Chain: at the ISO steps (RAW steps land at 1802 and 1804, one frame after the 1801 EXIF change for the first) deflicker's RAW-exact corrections still overshoot in RapidRAW (the 1.18 ratio) and the grade's colour grading turns that brightness error into a colour shift (export: −0.21 brightness, +0.22 warmth at 1802). `finish` must then undo ~0.2 + 0.2 on the darkest, 8-bit frames, where shadows have few levels. Synthetic night scene with light trails: 8-bit JPEG measures with error 0.03 (worst 0.08) by either statistic; 16-bit TIFF: 0.000. Added: measurement reads 16-bit TIFF (tifffile), `finish --format jpg` (correct in 16-bit, write JPEGs), `timelapse-rapidraw export --tiff16`. Open: a colour shift that depends on brightness (colour grading) can't be fully undone by one gain per channel; whatever remains after 16-bit is that.
**Correction (same day):** the "8-bit can't measure night frames" result came from a synthetic scene with no sensor noise (every pixel rounded identically every frame). With realistic per-frame noise, 8-bit JPEG measured by AVERAGING blocks in linear light (not skipping pixels) gives error 0.005 (worst 0.013), close to 16-bit. `finish` now averages (lookup table, float32, ~0.75 s per 20 MP frame). 16-bit TIFF export (`--tiff16`) stays available but is not the default: full-res 16-bit exports from RapidRAW are slow (no headless resize) and up to ~27 GB.


---

## Addendum 3 — 20 Oct 2026: full review, fixes, rename, editor plug-ins

**Review** (every module read; ruff + bandit; coverage 89%; ~30 probes with awkward inputs). Fixed, each with a regression test that reproduced it first:
- **Critical:** one black or blown frame (lens cap) carried NaN through the frame-to-frame chain and the smoother: 20 of 30 frames got NaN; `finish` wrote 20 pure-black frames and reported success. Now unmeasurable frames are skipped (the chain bridges them, the smoother gives them no weight), left uncorrected, and named. Non-finite values are refused everywhere before writing.
- **High:** RapidRAW writes saved the ledger only at the end, so an interrupted run left corrections the record didn't know about and the next run stacked corrections on corrections. Now `ledger_kept_in_step` updates the record as each sidecar is written and saves it however the block ends; every value is validated before the first write; JSON is written atomically; a damaged record gives a plain explanation (restore, or delete it) instead of a traceback; `rebuild` keeps a safety copy until it finishes. Verified: interrupted after 12 of 30 frames, rerun = identical to a clean run.
- **Medium/low:** `--window` under 3 was a silent no-op (now refused); `finish --out` into a folder with other frames mixed them (now refused; reruns allowed) and its self-check read stale files (now only what it wrote); mixed frame sizes are caught before writing; fps/width/strength checked before slow work; odd widths rounded; missing folders created; flawed folders (RAW+JPEG, two cameras) refused unless `--force`; ExifTool failures and timeouts reported instead of swallowed.
- **Security (low):** file names starting with `-` were read by ExifTool as options (reproduced; now absolute paths); programs are looked up only on PATH, never the current folder (Windows planting). No network, no shell, no secrets.
- Then done (same day): camera-settings cache (per folder, in the user cache dir, keyed by size + mtime, ExifTool-aware), parallel frame decoding (`parallel.ordered_map`: in order, bounded memory, falls back to sequential; results bit-identical; `HARBIUM_WORKERS`), and a per-folder lock (reentrant, stale locks from crashed runs cleared). Speed-up not measurable in the 1-core sandbox: measure on a real machine.
- Still open: Windows long paths (>260 chars), detecting renamed frames between runs.

**Structure:** one package `harbium-timelapse` (import `harbium_timelapse`), commands `timelapse` and `timelapse-rapidraw`. Editors are plug-ins: `harbium_timelapse.editors.Editor` (detect, is_running, load, prepare, undo) plus the `harbium_timelapse.editors` entry-point group; RapidRAW is the built-in reference adapter. New commands: `timelapse prepare | undo | editors`. The on-disk working folder stays `.timelapse-core` for compatibility with existing sequences. Guide: `docs/EDITORS.md`.


---

## Addendum 4 — 20 Oct 2026: Lightroom adapter (experimental)

Built from James's real Lightroom Classic sidecars (Canon EOS 80D, 2024, Camera Raw 16.x, PV 15.4; files kept private, tests use a made-up XMP of the same shape). Findings: settings are attributes of the first `rdf:Description`; `crs:Exposure2012` occurs once there, formatted `0.00` / `+0.42` / `-1.20`; masks use `crs:LocalExposure2012`; files have 60k-character lines and a stray CR, so they're edited as bytes; copies such as `IMG_1273_1.xmp` belong to no photo. All sample files had a pasted Custom white balance (5250 K / +6), so Lightroom's per-frame as-shot readings weren't available: white balance is not written for Lightroom (its Kelvin scale comes from Adobe's DCP matrices; on the R6, ~240 K from rawler's at 3,500 K).

Verified on copies of all 14 real files: only the exposure value changes; undo restores every file byte for byte; the orphan copy is never touched. Not verified: Lightroom reading the result back (no Lightroom available). Workflow: Save Metadata to Files → close Lightroom → `timelapse prepare` → Read Metadata from Files. DNG/JPEG/TIFF (settings inside the file) not supported.

Refactor: generic safety machinery moved to `editors/common.py` (backups, record kept in step, folder lock, atomic byte writes, damaged-file explanations, process listing); RapidRAW's `state.py` re-exports it. Fixed in both adapters: a refusal now names the problem it refuses over.


---

## Addendum 5 — 20 Oct 2026: outside benchmark, and the regional vote

Benchmarked `finish` on the 16 TimeLapse clips of the CVPR 2023 blind-deflickering dataset (Lei et al., CC BY 4.0; 768×432 YouTube JPEGs, all tripod), scored with measures independent of the tool's own (patch-median regional wobble, sudden jumps, shadows+mid-tones). See docs/BENCHMARK.md and benchmarks/score_finish.py.

Weakness found: `steady_change` took the ratio of all still pixels' total brightness, so in linear light bright regions dominated. A sky or lights changing on their own (slow enough to pass the 0.25-stop mover test) were read as global flicker; on low-flicker clips (city, beach, tide) `finish` made things worse (city 0.003 → 0.012).

Fix (in `measure.steady_change`, so both deflicker-from-RAW and finish benefit): movers found as before; then 6×8 regions each give the ratio of their still pixels' totals (keeps 8-bit rounding averaging); regions vote. A region votes only with ≥200 still pixels and ≥30% of its area (mostly black/blown regions are biased samples: night2day's red channel crushed in 60% of frames regressed until this rule); votes weighted by pixel count; the middle 20% of the weight is averaged (a plain weighted median was noisier). Fewer than 5 voters: the old whole-frame ratio. Exact for a truly global change.

Evidence: known-answer synthetic test (static ground, sky changing on its own, 0.05 flicker): leftover ground wobble 0.025 (old) → 0.006–0.007 (new), 5 seeds. Sky-share sweep (truth 0.100): 20% → 0.100 (old 0.196), 35% → 0.126 (old 0.234), 45% → 0.188 (old 0.251): degrades as the self-changing part nears half (inherent to any vote). CVPR: new never worse than old on any clip; old made 3 clips worse than untouched, new none (regional score). James's dusk sequence (web-compressed "before" half of the README video, finish only, no prepare): regional wobble 0.178 → 0.054 (old) / 0.013 (new); jumps 46 → 11 / 0.

Not covered: moving cameras (no clip in the dataset); compare with the paper's own method (GPU; different metric).


---

## Addendum 6 — 20 Oct 2026: output quality

`finish` gains `--codec h264|h265|prores` (H.265 10-bit yuv420p10le with hvc1 tag; ProRes 422 HQ prores_ks profile 3, yuv422p10le, .mov only; file type and encoder availability checked before measuring) and dither (on by default, `--no-dither`): `correct()` can output a different bit depth and adds triangular noise (−1..+1 level, zero mean, seeded per frame) before rounding to 8 bits; uncorrected channels pass through untouched. 10-bit video is fed `correct(..., out_dtype=uint16)` from the original frames, skipping 8-bit rounding.

Measured (benchmarks/check_encoding.py, docs/BENCHMARK.md): on real dusk footage the encoder adds 0.0043 (H.264) / 0.0016 (H.265 10-bit) / 0.0003 (ProRes) stops of flicker; none visible. On a clean synthetic sky, banding staircase 0.28 source → 0.57 H.264 plain → 0.43 H.264 dithered → 0.26 H.265 10-bit. H.264 stays the default for compatibility.
