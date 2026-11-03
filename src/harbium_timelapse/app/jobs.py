"""Long work in the background: progress you can follow, a Cancel that works.

``JobRunner.start(name, work, on_event)`` runs ``work(tracker)`` on a
background thread. ``work`` hands ``tracker.stage("Measuring")`` and so on to
the library as its progress callback; pressing Cancel makes the next progress
call raise ``Cancelled``, which stops the job at a safe point (the library
writes atomically and keeps its records in step after every file).

Events go to ``on_event`` on the job's own thread; the screen layer moves them
onto its event loop. Progress events are thinned to about ten a second.

The heavy parts of a job (decoding RAW files) already run in worker processes
through ``parallel.ordered_map``, so a thread is enough to keep the window
responsive. A job that ignores Cancel can't be stopped by force yet; that
needs the job itself in a worker process (GUI_PLAN.md, ADR-003), planned for
when a job type needs it.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..progress import CancelToken, Cancelled, Progress, Tracker

log = logging.getLogger(__name__)
EVERY = 0.1          # seconds between progress events (the last one always gets through)


@dataclass(frozen=True)
class Done:
    result: Any


@dataclass(frozen=True)
class Failed:
    message: str


@dataclass(frozen=True)
class Stopped:
    """Cancelled by the user; everything written so far is consistent."""


Event = Progress | Done | Failed | Stopped


class JobHandle:
    def __init__(self, name: str, token: CancelToken):
        self.name = name
        self.token = token
        self.thread: threading.Thread | None = None
        self.outcome: Event | None = None

    def cancel(self) -> None:
        self.token.cancel()

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def wait(self, timeout: float | None = None) -> Event | None:
        if self.thread:
            self.thread.join(timeout)
        return self.outcome


class JobRunner:
    def start(self, name: str, work: Callable[[Tracker], Any],
              on_event: Callable[[Event], None] | None = None) -> JobHandle:
        handle = JobHandle(name, CancelToken())
        last = [0.0]

        def progress(p: Progress) -> None:
            now = time.monotonic()
            if p.done >= p.total or now - last[0] >= EVERY:
                last[0] = now
                if on_event:
                    on_event(p)

        def run() -> None:
            try:
                outcome: Event = Done(work(Tracker(progress, handle.token)))
            except Cancelled:
                outcome = Stopped()
            except (ValueError, RuntimeError, OSError) as e:      # the library explains these in plain words
                outcome = Failed(str(e))
            except Exception as e:                                 # a bug: say so honestly, keep the details
                log.exception("Job %r failed", name)
                outcome = Failed(f"Something went wrong that the app didn't expect ({type(e).__name__}: {e}). "
                                 "Nothing has been left half-done. Please report it with the diagnostics file.")
            handle.outcome = outcome
            if on_event:
                on_event(outcome)

        handle.thread = threading.Thread(target=run, name=f"job: {name}", daemon=True)
        handle.thread.start()
        return handle
