"""Time slices: one picture of the scene with time running left to right.

Frame 1 gives the leftmost band of the picture, the last frame the rightmost,
each band about ``width / frames`` wide. The scene keeps its real shapes, the
light's genuine change reads as a smooth fade across it, and a frame that
jumps in brightness shows as a band lighter or darker than its neighbours.
Corrections are applied band by band in linear light, so Before and After can
be compared side by side, and redrawn instantly as the corrections change.
"""

from __future__ import annotations

import numpy as np

from .measure import _srgb_to_linear


def bands(width: int, frames: int) -> list[tuple[int, int]]:
    """Where each frame's band sits: consecutive, covering the whole width exactly."""
    edges = np.round(np.linspace(0, width, frames + 1)).astype(int)
    return [(int(edges[i]), int(edges[i + 1])) for i in range(frames)]


def compose(images: list[np.ndarray], stops: np.ndarray | None = None) -> np.ndarray:
    """A linear-light time slice from linear images (all the same size). ``stops``: a correction per frame
    (one value, or red/green/blue), applied to that frame's band."""
    h, w = images[0].shape[:2]
    out = np.empty((h, w, 3), np.float32)
    for i, (a, b) in enumerate(bands(w, len(images))):
        band = images[i][:, a:b, :3].astype(np.float32)
        if stops is not None:
            band = band * np.exp2(np.asarray(stops[i], dtype=np.float32))
        out[:, a:b] = band
    return out


def display_scale(images: list[np.ndarray]) -> float:
    """One brightness scale for showing a whole sequence (and its Before and After alike): the typical
    frame's average lands at a mid grey, so differences between frames stay visible."""
    means = [float(np.mean(img)) for img in images]
    typical = float(np.median([m for m in means if m > 0] or [1.0]))
    return 0.18 / typical


def stack(before: np.ndarray, after: np.ndarray) -> np.ndarray:
    """Before over After (8-bit pictures of the same size), on neutral grey with labels."""
    from PIL import Image, ImageDraw
    w = before.shape[1]
    label = max(28, w // 60)
    gap = np.full((label, w, 3), 128, np.uint8)                       # neutral grey, never tinted
    out = Image.fromarray(np.vstack([gap, before, gap, after]))
    d = ImageDraw.Draw(out)
    try:
        from PIL import ImageFont
        font = ImageFont.load_default(size=int(label * 0.6))
    except (TypeError, OSError):
        font = None
    d.text((label // 3, label // 6), "Before", fill=(255, 255, 255), font=font)
    d.text((label // 3, label + before.shape[0] + label // 6), "After", fill=(255, 255, 255), font=font)
    return np.asarray(out)


def to_8bit(img: np.ndarray) -> np.ndarray:
    return img if img.dtype == np.uint8 else np.round(img / 257.0).astype(np.uint8)


def to_display(linear: np.ndarray, scale: float = 1.0) -> np.ndarray:
    """Linear light to an 8-bit sRGB picture."""
    from .finish import _linear_to_srgb
    return np.round(_linear_to_srgb(np.clip(linear * scale, 0, 1)) * 255).astype(np.uint8)


def from_display(img: np.ndarray) -> np.ndarray:
    """An 8-bit sRGB picture back to linear light."""
    return _srgb_to_linear(img.astype(np.float32) / 255.0).astype(np.float32)
