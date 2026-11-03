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


MODES = {"left-right": "Left to right", "right-left": "Right to left", "top-bottom": "Top to bottom",
         "bottom-top": "Bottom to top", "centre-out": "From the centre out", "edges-in": "From the edges in",
         "clockwise": "Round the clock"}


def frame_map(h: int, w: int, frames: int, mode: str = "left-right") -> np.ndarray:
    """Which frame each pixel of the time slice comes from (first frame 0). Every mode costs the same."""
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    x, y = (x + 0.5) / w, (y + 0.5) / h
    if mode in ("left-right", "right-left"):
        t = x
    elif mode in ("top-bottom", "bottom-top"):
        t = y
    elif mode in ("centre-out", "edges-in"):
        r = np.hypot((x - 0.5) * w, (y - 0.5) * h)
        t = r / r.max()
    elif mode == "clockwise":
        t = (np.arctan2(x - 0.5, -(y - 0.5)) / (2 * np.pi)) % 1.0        # 0 at twelve o'clock, then clockwise
    else:
        raise ValueError(f"Unknown time slice mode {mode!r}. Choose from: {', '.join(MODES)}.")
    if mode in ("right-left", "bottom-top", "edges-in"):
        t = 1.0 - t
    return np.clip((t * frames).astype(np.int32), 0, frames - 1)


class Slice:
    """A time slice gathered one frame at a time: each frame hands over only its own pixels, so memory is one
    picture's worth however long the sequence. ``picture(stops)`` applies a correction per frame instantly."""

    def __init__(self, h: int, w: int, frames: int, mode: str = "left-right", dtype=np.float32):
        self.h, self.w, self.frames, self.mode = h, w, frames, mode
        self.map = frame_map(h, w, frames, mode).ravel()
        self.order = np.argsort(self.map, kind="stable")
        self.edges = np.searchsorted(self.map[self.order], np.arange(frames + 1))
        self.values = np.zeros((h * w, 3), dtype)

    def take(self, i: int, image: np.ndarray) -> None:
        """Frame ``i`` (same size as the slice) hands over its pixels."""
        where = self.order[self.edges[i]:self.edges[i + 1]]
        self.values[where] = image.reshape(-1, image.shape[-1])[where, :3]

    def picture(self, stops: np.ndarray | None = None) -> np.ndarray:
        if stops is None:
            return self.values.reshape(self.h, self.w, 3)
        gain = np.exp2(np.asarray(stops, dtype=np.float32))
        gain = gain[self.map] if gain.ndim == 1 else gain[self.map, :]
        return (self.values * (gain[:, None] if gain.ndim == 1 else gain)).reshape(self.h, self.w, 3)


def compose(images: list[np.ndarray], stops: np.ndarray | None = None) -> np.ndarray:
    """A linear-light time slice from linear images (all the same size). ``stops``: a correction per frame
    (one value, or red/green/blue), applied to that frame's band."""
    h, w = images[0].shape[:2]
    out = Slice(h, w, len(images))
    for i, img in enumerate(images):
        out.take(i, img)
    return out.picture(stops)


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
