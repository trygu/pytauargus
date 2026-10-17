"""Bootstrap for the installed-distribution tests.

Unlike the dev ``tests/conftest.py``, this one never puts the source tree
on ``sys.path``: ``import pytauargus`` must resolve to the *installed*
distribution. The fixture, expectations and curated existing tests ship
with this resource, so no product checkout is needed.

Environment contract (set by the workflow; also used for local runs):

  TAUARGUS_TEST_VENV              the virtual environment the wheels were
                                  installed into (module locations must be
                                  inside it)
  TAUARGUS_EXPECTED_VERSION       product version to assert
  TAUARGUS_EXPECTED_TUI_VERSION   TUI version to assert
  TAUARGUS_EXPECTED_ENGINE_SHA    pinned engine commit the wheels were
                                  built from
  TAUARGUS_EXPECTED_HIGHS         pinned HiGHS version (e.g. v1.15.1)
"""

import os
from pathlib import Path

import pytest

import pytauargus
import pytauargus._tauargus  # noqa: F401  (the native extension loads)

RES = Path(__file__).resolve().parent
FIXTURE = RES / "fixture"
EXPECTATIONS = RES / "expectations" / "ex1.expected"


def _require_env(name):
    value = os.environ.get(name, "")
    if not value:
        pytest.exit(f"{name} must be set by the test runner", 1)
    return value


def parse_expectations():
    """Parse the shared expectations file.

    Returns (cells, primaries): cells maps (row, col) -> dict(response,
    status); primaries is a list of (row, col, response, protection).
    """
    cells = {}
    primaries = []
    for line in EXPECTATIONS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        kind, rest = line.split(None, 1)
        parts = rest.split()
        row, col = (part.strip() for part in parts[0].split(";"))
        if kind == "CELL":
            cells[(row, col)] = {"response": float(parts[1]),
                                 "status": parts[2]}
        elif kind == "PRIMARY":
            primaries.append((row, col, float(parts[1]), float(parts[2])))
    assert cells, "no CELL expectations parsed"
    assert primaries, "no PRIMARY expectations parsed"
    return cells, primaries


@pytest.fixture(scope="session")
def expectations():
    return parse_expectations()
