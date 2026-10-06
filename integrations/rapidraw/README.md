# RapidRAW integration (milestone 2)

Not built yet. Blocked on one thing: the exposure field inside a real `.rrdata` sidecar.

How it will work: take `FlickerCorrections` from the core, convert stops to
RapidRAW's exposure units, write them into each frame's `.rrdata` (creating the
sidecar where RapidRAW hasn't yet), then export through `rapidraw export`.

The core library stays MIT. This folder talks to RapidRAW (AGPL-3.0) only
through its sidecar files and command line. See `docs/SCAFFOLD.md` §5.
