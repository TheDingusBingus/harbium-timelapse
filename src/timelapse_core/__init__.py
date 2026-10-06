"""timelapse-core: free, open timelapse processing in photographer language.

    from timelapse_core import load_sequence, check_sequence, smooth_flicker

    seq = load_sequence("~/Pictures/sunset")
    for problem in check_sequence(seq):
        print(problem)
    fix = smooth_flicker(seq)
    fix.by_filename()   # {"IMG_0001.CR3": 0.04, ...} stops per frame
"""

from .deflicker import smooth_brightness, smooth_flicker
from .measure import measure_brightness
from .sequencer import check_sequence, format_stops, load_sequence
from .types import FlickerCorrections, Frame, Problem, Sequence

__version__ = "0.1.0"

__all__ = [
    "load_sequence", "check_sequence", "measure_brightness",
    "smooth_flicker", "smooth_brightness", "format_stops",
    "Frame", "Sequence", "Problem", "FlickerCorrections",
]
