"""Deflicker RapidRAW timelapses through RapidRAW's own sidecar files.

    from timelapse_rapidraw import load, check, plan, apply, undo

    seq = load("~/Pictures/sunset")
    for problem in check(seq):
        print(problem)
    apply(plan(seq))     # writes exposure into each .rrdata
    undo(seq.folder)     # takes it back out
"""

from .deflicker import FramePlan, Plan, apply, check, load, plan, undo

__version__ = "0.1.0"
__all__ = ["load", "check", "plan", "apply", "undo", "Plan", "FramePlan"]
