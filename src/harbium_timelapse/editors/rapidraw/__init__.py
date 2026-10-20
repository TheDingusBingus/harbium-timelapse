"""Deflicker RapidRAW timelapses through RapidRAW's own sidecar files.

    from harbium_timelapse.editors.rapidraw import load, check, plan, apply, undo

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
from .recover import rapidraw_running, rebuild
from .refine import refine
from .state import restore
from .whitebalance import apply_white_balance, plan_white_balance, undo_white_balance

__version__ = "0.1.0"
__all__ = [
    "load", "check", "plan", "apply", "undo", "restore", "refine", "rebuild", "rapidraw_running",
    "suggest", "label_keyframes", "keyframes_in", "blend", "KEYFRAME_LABEL",
    "plan_white_balance", "apply_white_balance", "undo_white_balance",
    "Plan", "FramePlan",
]
