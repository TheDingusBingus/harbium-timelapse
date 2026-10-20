"""Doing per-frame work on every CPU core, results still in frame order.

Decoding frames is the slow part of measuring and each frame is independent,
so they're decoded in parallel worker processes. Results come back strictly
in order, and only a few frames are in flight at once, so memory stays small
on long sequences.

If parallel workers can't be used (one core, a restricted environment, a
frozen app without multiprocessing support, a worker that died), it quietly
does the same work one frame at a time. The results are identical either way.

``HARBIUM_WORKERS=1`` turns it off; any other number sets how many workers.
"""

from __future__ import annotations

import os
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")

MAX_WORKERS = 8   # beyond this the disk, not the CPU, is usually the limit


def default_workers() -> int:
    setting = os.environ.get("HARBIUM_WORKERS", "").strip()
    if setting:
        try:
            return max(1, int(setting))
        except ValueError:
            pass
    cores = os.cpu_count() or 1
    return max(1, min(cores - 1, MAX_WORKERS))     # leave a core for the computer to stay usable


def ordered_map(fn: Callable[[T], R], items: Iterable[T], workers: int | None = None) -> Iterator[R]:
    """``fn(item)`` for each item, in order, using several processes when that
    helps. ``fn`` must be a top-level function (so it can be sent to a worker)."""
    items = list(items)
    workers = default_workers() if workers is None else max(1, workers)
    if workers == 1 or len(items) < 4:
        yield from map(fn, items)
        return
    done = 0
    try:
        pool = ProcessPoolExecutor(max_workers=workers)
    except (OSError, NotImplementedError, ValueError):
        yield from map(fn, items)
        return
    try:
        with pool:
            pending: deque = deque()
            upcoming = iter(items)
            for item in upcoming:
                pending.append(pool.submit(fn, item))
                if len(pending) >= 2 * workers:
                    break
            while pending:
                result = pending.popleft().result()      # a worker's error is raised here, as usual
                done += 1
                yield result
                nxt = next(upcoming, _END)
                if nxt is not _END:
                    pending.append(pool.submit(fn, nxt))
    except BrokenProcessPool:
        # A worker died (e.g. the system ran out of memory): finish one at a time.
        yield from map(fn, items[done:])


_END = object()
