"""Did the video encoder put flicker back, and how much detail did it cost?

    python benchmarks/check_encoding.py film.mp4 "path/to/corrected frames"

Decodes the video, lines each of its frames up with the matching corrected
frame (scaled to the video's size), and reports:

- encoder flicker: how much each frame's brightness was nudged by encoding,
  as frame-to-frame wobble in stops. The deflicker work aims for about 0.01
  stops, so the encoder should add far less than that.
- similarity (SSIM, 1.0 = identical) per frame, the worst frames, and whether
  keyframes look different from the frames between them ("pumping").

Reads only. For a fair comparison, save corrected frames losslessly
(`timelapse finish ... --out FOLDER --format png`) in the same run as the video.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from harbium_timelapse.finish import LUMA, finished_frames
from harbium_timelapse.measure import _srgb_to_linear
from harbium_timelapse.programs import find_program


def probe(video: Path) -> dict:
    out = subprocess.run([find_program("ffprobe") or "ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height,codec_name,pix_fmt,bit_rate:format=size,duration",
                          "-of", "json", str(video)], capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    return {**data["streams"][0], **data.get("format", {})}


def keyframes(video: Path) -> list[int]:
    out = subprocess.run([find_program("ffprobe") or "ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "frame=pict_type", "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True, check=True).stdout
    return [i for i, t in enumerate(out.split()) if t.strip().startswith("I")]


def decoded(video: Path, w: int, h: int):
    proc = subprocess.Popen([find_program("ffmpeg") or "ffmpeg", "-v", "error", "-i", str(video),
                             "-f", "rawvideo", "-pix_fmt", "rgb48le", "-"], stdout=subprocess.PIPE)
    size = w * h * 3 * 2
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        yield np.frombuffer(buf, dtype="<u2").reshape(h, w, 3) / 65535.0
    proc.wait()


def reference(path: Path, w: int, h: int) -> np.ndarray:
    img = Image.open(path)
    arr = np.asarray(img.convert("RGB") if img.mode not in ("RGB", "I;16") else img)
    scale = 65535.0 if arr.dtype == np.uint16 else 255.0
    small = Image.fromarray(arr if arr.dtype == np.uint8 else (arr / 257).astype(np.uint8)).resize((w, h), Image.LANCZOS)
    return np.asarray(small, dtype=np.float64) / 255.0 if arr.dtype == np.uint8 else np.asarray(small) / scale


def _box(a: np.ndarray, n: int = 8) -> np.ndarray:
    c = np.pad(a.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    return (c[n:, n:] - c[:-n, n:] - c[n:, :-n] + c[:-n, :-n]) / (n * n)


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    """Structural similarity of two luma images in 0..1 (8x8 windows)."""
    c1, c2 = 0.01 ** 2, 0.03 ** 2
    ma, mb = _box(a), _box(b)
    va, vb, cov = _box(a * a) - ma ** 2, _box(b * b) - mb ** 2, _box(a * b) - ma * mb
    return float(np.mean(((2 * ma * mb + c1) * (2 * cov + c2)) / ((ma ** 2 + mb ** 2 + c1) * (va + vb + c2))))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("video", type=Path)
    ap.add_argument("frames", type=Path)
    args = ap.parse_args()
    info = probe(args.video)
    w, h = int(info["width"]), int(info["height"])
    frames = finished_frames(args.frames)
    drift, sims = [], []
    for ref_path, vid in zip(frames, decoded(args.video, w, h)):
        ref = reference(ref_path, w, h)
        lv, lr = _srgb_to_linear(vid) @ LUMA, _srgb_to_linear(ref) @ LUMA
        drift.append(np.log2(lv.mean() / lr.mean()))
        sims.append(ssim(vid @ LUMA, ref @ LUMA))
    drift, sims = np.array(drift), np.array(sims)
    keys = [k for k in keyframes(args.video) if k < len(sims)]
    others = np.setdiff1d(np.arange(len(sims)), keys)
    wobble = float(np.std(np.diff(drift)) / np.sqrt(2)) if len(drift) > 2 else 0.0
    rate = float(info.get("bit_rate") or 0) / 1e6 or float(info["size"]) * 8 / float(info["duration"]) / 1e6
    print(f"{args.video.name}: {info['codec_name']} {info['pix_fmt']}, {w}x{h}, {len(sims)} frames compared, "
          f"{float(info['size']) / 1e6:.1f} MB ({rate:.1f} Mbit/s)")
    print(f"  Encoder flicker:  {wobble:.4f} stops frame to frame (largest step {np.max(np.abs(np.diff(drift))):.4f})")
    print(f"  Overall shift:    {np.median(drift):+.4f} stops")
    print(f"  Similarity:       average {sims.mean():.4f}, worst {sims.min():.4f} (frame {int(sims.argmin()) + 1})")
    if keys and len(others):
        print(f"  Keyframes:        {len(keys)}; similarity {sims[keys].mean():.4f} on keyframes "
              f"vs {sims[others].mean():.4f} between them")


if __name__ == "__main__":
    main()
