"""The native solver calls must release the GIL.

The TUI runs engine work (load, preview) in a background worker thread while
the event loop keeps rendering and accepting keys. If a C++ solver call holds
the GIL for its whole duration, the entire Python process freezes with it —
the UI hangs for the whole solve (legacy symptom: start the TUI, find a red
cell, press ``s`` → the whole app freezes).

This test runs ``run_batch`` in a worker thread and heartbeats from the main
thread. The longest gap between heartbeats inside the run window must stay
well below the length of the native solves: if the GIL were held through a
solve, the heartbeat would be starved for its entire duration.
"""

import threading
import time
from pathlib import Path

from pytauargus.engine import run_batch

from conftest import DATA

# A 100 ms stall is already perceived as a freeze in a TUI. On the dev
# fixture the unfixed build stalls ~340 ms inside one 450 ms run; the fixed
# build keeps gaps at or below the GIL switch interval (~5 ms), so this
# threshold sits ~20x from either side.
MAX_ALLOWED_GAP = 0.1


def test_solve_releases_gil():
    window = {}

    def worker():
        t0 = time.monotonic()
        run_batch(DATA / "TestRecode.arb")
        window["t0"], window["t1"] = t0, time.monotonic()

    th = threading.Thread(target=worker)
    th.start()
    stamps = []
    try:
        while th.is_alive():
            stamps.append(time.monotonic())
            time.sleep(0.0002)
    finally:
        th.join()

    t0, t1 = window["t0"], window["t1"]
    inside = [t for t in stamps if t0 <= t <= t1]
    assert inside, "heartbeat produced no ticks inside the run window"
    gaps = [inside[0] - t0]
    gaps += [b - a for a, b in zip(inside, inside[1:])]
    gaps.append(t1 - inside[-1])
    assert max(gaps) < MAX_ALLOWED_GAP, (
        f"GIL held for {max(gaps) * 1000:.0f} ms inside a {(t1 - t0) * 1000:.0f} ms "
        "run: the native solver calls must release the GIL so other threads "
        "(e.g. the TUI event loop) stay responsive"
    )
