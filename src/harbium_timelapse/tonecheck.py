"""Does the correction treat every brightness level alike?

finish applies one correction per frame to all brightness levels. If an editor's
tone curve compressed the highlights, a flicker moves bright areas less than the
midtones, so a single correction sized for the whole frame could over-correct
the highlights and add a little flicker there. This check measures it instead of
guessing: frame-to-frame jitter in shadows, midtones and highlights, before and
after.

The bands are chosen once, from the middle frame of BEFORE, so every frame is
judged on the same pixels (a walker can't move pixels between bands). Each
band's brightness per frame is a median, which ignores the few pixels anything
moving covers. Nearly black and nearly clipped pixels are left out.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

BANDS = (("shadows", 10, 40), ("midtones", 40, 75), ("highlights", 75, 97))   # percentiles of the middle frame
LUMA = np.array([0.2126, 0.7152, 0.0722])


@dataclass(frozen=True)
class Band:
    name: str
    share: float              # share of the measured pixels in this band
    before: float             # frame-to-frame jitter, stops
    after: float

    @property
    def kept(self) -> float:
        """How much of its jitter the band kept: 0 = all removed, 1 = untouched, above 1 = made worse."""
        return self.after / self.before if self.before > 0 else 0.0

    @property
    def worse(self) -> bool:
        return self.after > self.before * 1.1 and self.after - self.before > 0.002


def lagging(bands: list[Band]) -> list[Band]:
    """Bands the correction didn't steady like the rest: they kept over half their jitter while the best band
    kept under a third as much. That's how over-correcting a compressed band shows up in practice: the
    correction cancels flicker that wasn't fully there, so the band barely improves."""
    best = min(b.kept for b in bands)
    return [b for b in bands if b.kept > 0.5 and b.kept > 3 * best and b.after - best * b.before > 0.002]


def jitter(values: np.ndarray) -> float:
    """Frame-to-frame wobble with the real trend taken out (second differences), in stops."""
    d = np.diff(np.asarray(values, dtype=np.float64), 2)
    d = d[np.isfinite(d)]
    return float(np.std(d) / np.sqrt(6)) if d.size > 2 else 0.0


def band_series(frames: list[np.ndarray], masks: list[np.ndarray]) -> np.ndarray:
    """Median brightness (log2, linear light) of each band in each frame: frames x bands."""
    out = np.full((len(frames), len(masks)), np.nan)
    for i, img in enumerate(frames):
        lum = img[..., :3] @ LUMA
        for b, mask in enumerate(masks):
            values = lum[mask]
            values = values[values > 0]
            if values.size:
                out[i, b] = np.log2(np.median(values))
    return out


def masks_from(reference: np.ndarray) -> list[np.ndarray]:
    lum = reference[..., :3] @ LUMA
    usable = (lum > 1 / 1000) & (reference[..., :3].max(axis=-1) < 0.97)
    edges = np.percentile(lum[usable], [p for _, a, b in BANDS for p in (a, b)])
    return [usable & (lum >= edges[2 * i]) & (lum < edges[2 * i + 1]) for i in range(len(BANDS))]


def compare(before: list[np.ndarray], after: list[np.ndarray]) -> list[Band]:
    if len(before) != len(after) or len(before) < 5:
        raise ValueError("Before and after need the same frames, at least 5 of them.")
    if before[0].shape != after[0].shape:
        raise ValueError("Before and after frames are different sizes, so the same pixels can't be compared.")
    masks = masks_from(before[len(before) // 2])
    total = sum(int(m.sum()) for m in masks) or 1
    b, a = band_series(before, masks), band_series(after, masks)
    return [Band(name, int(m.sum()) / total, jitter(b[:, i]), jitter(a[:, i]))
            for i, ((name, _, _), m) in enumerate(zip(BANDS, masks))]


def check_folders(before: Path, after: Path, progress=None) -> tuple[list[Band], int]:
    """Read both folders (frames matched by name) and compare them. Returns the bands and the frame count."""
    from .finish import _small_linear_of, finished_frames
    from .parallel import ordered_map
    a_frames = {p.name: p for p in finished_frames(after)}
    pairs = [(p, a_frames[p.name]) for p in finished_frames(before) if p.name in a_frames]
    if len(pairs) < 5:
        raise ValueError(f"Only {len(pairs)} frames have the same name in both folders: need at least 5.")
    paths = [p for pair in pairs for p in pair]
    images = []
    for i, img in enumerate(ordered_map(_small_linear_of, paths), 1):
        images.append(img)
        if progress:
            progress(i, len(paths))
    return compare(images[0::2], images[1::2]), len(pairs)


def verdict(bands: list[Band]) -> str:
    worse = [b.name for b in bands if b.worse]
    behind = [b.name for b in lagging(bands) if not b.worse]
    if worse:
        return (f"The correction made the {', '.join(worse)} worse: one correction for every brightness level "
                "doesn't suit this sequence there. A tone-aware correction would help.")
    if behind:
        return (f"The {', '.join(behind)} barely got steadier while the rest improved a lot: most likely the "
                "editor's tone curve compressed them, so one correction for every level over-corrects there. "
                "A tone-aware correction would help.")
    return "Every brightness level got steadier by a similar amount: no sign of over-correcting any of them."
