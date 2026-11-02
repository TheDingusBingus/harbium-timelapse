"""Progress you can follow and work you can stop.

Long functions in the library report progress as ``progress(done, total)``.
``Tracker`` turns that into named events (``Progress(stage, done, total)``)
for an app's progress bar, and checks a ``CancelToken`` on every call, so
pressing Cancel stops any long job at its next step.

Stopping is always safe: the library writes files atomically and keeps its
records in step after every file, so a stopped run leaves everything
consistent and can simply be run again.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Progress:
    stage: str      # "Reading camera settings", "Measuring", "Writing", "Checking"
    done: int
    total: int


class Cancelled(Exception):
    """The user asked to stop. Everything written so far is consistent."""


class CancelToken:
    """Shared between the screen (which cancels) and the job (which checks)."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise Cancelled("Stopped.")


class Tracker:
    """Hands out ``(done, total)`` callbacks for each stage of a job."""

    def __init__(self, on_event: Callable[[Progress], None] | None = None, token: CancelToken | None = None):
        self.on_event = on_event
        self.token = token or CancelToken()

    def stage(self, name: str) -> Callable[[int, int], None]:
        self.token.check()

        def report(done: int, total: int) -> None:
            self.token.check()
            if self.on_event:
                self.on_event(Progress(name, done, total))
        return report
