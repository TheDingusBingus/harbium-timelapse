"""Make fake timelapse frames where the true brightness is known exactly."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

SIZE = (96, 64)


def scene(seed: int = 0, blown_sky: float = 0.0) -> np.ndarray:
    """Linear grey scene, tones spread over a few stops around mid-grey."""
    rng = np.random.default_rng(seed)
    w, h = SIZE
    img = 0.18 * 2.0 ** rng.normal(0, 1, (h, w))
    if blown_sky:
        img[: int(h * blown_sky)] = 4.0  # past white at any exposure used here
    return img


def encode(linear: np.ndarray) -> np.ndarray:
    v = np.clip(linear, 0, 1)
    return np.where(v <= 0.0031308, v * 12.92, 1.055 * v ** (1 / 2.4) - 0.055)


def write_png16(path: Path, linear: np.ndarray) -> None:
    Image.fromarray((encode(linear) * 65535 + 0.5).astype(np.uint16)).save(path)


def write_jpeg(path: Path, linear: np.ndarray, *, shutter=(1, 125), aperture=(8, 1),
               iso=100, taken_at: datetime | None = None, model="Test Camera") -> None:
    rgb = (encode(linear) * 255 + 0.5).astype(np.uint8)
    img = Image.fromarray(np.stack([rgb] * 3, axis=-1))
    exif = Image.Exif()
    exif[0x0110] = model
    ifd = exif.get_ifd(0x8769)
    ifd[0x829A] = IFDRational(*shutter)
    ifd[0x829D] = IFDRational(*aperture)
    ifd[0x8827] = iso
    if taken_at:
        ifd[0x9003] = taken_at.strftime("%Y:%m:%d %H:%M:%S")
    img.save(path, quality=95, exif=exif)


def make_folder(folder: Path, brightness_stops, *, start=datetime(2026, 9, 1, 19, 0, 0),
                interval=5, fmt="png", blown_sky=0.0) -> list[Path]:
    """One frame per brightness value. Same scene; only exposure changes."""
    base = scene(blown_sky=blown_sky)
    paths = []
    for i, b in enumerate(brightness_stops):
        frame = base * 2.0 ** b
        path = folder / f"IMG_{i + 1:04d}.{fmt}"
        if fmt == "png":
            write_png16(path, frame)
        else:
            write_jpeg(path, frame, taken_at=start + timedelta(seconds=interval * i))
        paths.append(path)
    return paths
