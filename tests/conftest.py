"""Pytest bootstrap: make the `src/` package importable without a wheel build.

The native extension (``_tauargus``) is built into ``src/pytauargus/`` by
``cmake --build``; putting ``src`` on ``sys.path`` lets ``import pytauargus``
find both the Python sources and the compiled module.

Test data (``.arb`` batches, microdata, example tables) lives in the pinned
``tauargus-engine`` checkout. Point ``TAUARGUS_ENGINE_DIR`` at it; the
default is the sibling ``../tauargus-engine`` directory.
"""

import os
import sys
from pathlib import Path

import pytest

from pytauargus.engine import Engine, run_batch

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
DATA = (
    Path(os.environ.get("TAUARGUS_ENGINE_DIR", str(ROOT.parent / "tauargus-engine")))
    / "data"
)
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(scope="module")
def engine() -> Engine:
    """Computed tables from TestRecode.arb (pre-suppression)."""
    return run_batch(DATA / "TestRecode.arb")
