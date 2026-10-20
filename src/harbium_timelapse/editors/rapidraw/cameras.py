"""The camera colour matrices RapidRAW uses, for the cameras tested so far.

RapidRAW turns a RAW file's as-shot neutral into Kelvin and tint with its RAW
decoder's colour matrices (rawler, from the dnglab project). To write a white
balance on *RapidRAW's* Kelvin scale we must use the same matrices: a
different camera profile reads differently (for the Canon R6 near 3,500 K,
Lightroom's profile reads about 240 K lower than these for the same neutral;
tint agrees to within a unit).

Numbers are XYZ-to-camera, row by row, copied from rawler's camera data
(dnglab, rawler/data/cameras) for interoperability. Add a camera by copying
its two matrices from there, keyed by the EXIF Model name.
"""

from __future__ import annotations

MATRICES: dict[str, dict[str, tuple[float, ...]]] = {
    "Canon EOS R6": {
        "A":   (1.0492, -0.481, 0.0274, -0.3519, 1.0911, 0.3009, -0.0143, 0.1025, 0.6822),
        "D65": (0.8293, -0.1611, -0.1132, -0.4759, 1.2711, 0.2275, -0.1013, 0.2415, 0.5509),
    },
}


def matrices_for(camera: str | None) -> dict[str, tuple[float, ...]] | None:
    if not camera:
        return None
    return MATRICES.get(camera.strip())
