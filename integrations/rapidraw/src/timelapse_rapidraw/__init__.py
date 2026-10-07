"""Deflicker RapidRAW timelapses through RapidRAW's own sidecar files.

    from timelapse_rapidraw import load, check, plan, apply, undo

    seq = load("~/Pictures/sunset")
    for problem in check(seq):
        print(problem)
    label_keyframes(seq, [s.frame for s in suggest(seq)])   # purple labels
    blend(seq)           # every setting blended between purple keyframes
    apply(plan(seq))     # deflicker: exposure into each .rrdata
    undo(seq.folder)     # takes deflicker back out
    restore(seq.folder)  # every sidecar back as it was before this tool
"""

from .deflicker import FramePlan, Plan, apply, check, load, plan, undo
from .keyframes import KEYFRAME_LABEL, blend, keyframes_in, label_keyframes, suggest
from .refine import refine
from .state import restore

__version__ = "0.1.0"
__all__ = [
    "load", "check", "plan", "apply", "undo", "restore", "refine",
    "suggest", "label_keyframes", "keyframes_in", "blend", "KEYFRAME_LABEL",
    "Plan", "FramePlan",
]
