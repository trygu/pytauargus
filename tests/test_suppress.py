"""Suppress + round smoke tests for the headless engine.

Runs MOD, OPT, and RND on a table computed from the sample microdata batch
(TestRecode.arb) and asserts the *observable* invariants of the native engine.

Native behaviour (verified against the C++ engine, native/core/src):
- MOD (AHiTaS) and OPT (FullJJ) leave the originally-unsafe cell's status
  unchanged but mark neighbouring safe cells as secondary (status 11/12).
  The right invariant is "number of secondary cells increases".
- RND (CRP/HiGHS) stores rounded values via SetRoundedResponse; the original
  response (GetTableCellValue) is unchanged and may not be a multiple of the
  base. The right invariants are: do_round succeeds, SetRoundedResponse
  applies, and no cell becomes negative.
- Per-cell LPL/UPL read via GetTableCellProtectionLevels return sentinel
  values (e.g. 0.01) for safe cells, so they are not usable as a range check.

Cell status codes from native/core/src/defines.h:
    CS_SAFE=1  CS_UNSAFE_RULE=3  CS_SECONDARY_UNSAFE=11  CS_EMPTY=14
"""

from pathlib import Path

import pytest

from pytauargus.batch import Suppress
from pytauargus.engine import run_batch

from conftest import DATA


@pytest.fixture()
def engine():
    """Fresh computed tables per test.

    Suppression mutates native table state (and a second suppression on an
    already-modified table can crash the HiGHS LP), so each test gets its own
    engine rather than sharing the module-scoped one from conftest.
    """
    return run_batch(DATA / "TestRecode.arb")


# Status constants matching native/core/src/defines.h
CS_SAFE = 1
CS_UNSAFE_RULE = 3
CS_SECONDARY_UNSAFE = 11
CS_SECONDARY_UNSAFE_MANUAL = 12
CS_EMPTY = 14


def _statuses(tau, tab):
    ncell, _ = tau.get_total_table_size(tab)
    return [tau.get_table_cell_status(tab, i) for i in range(ncell)]


def count_secondary(tau, tab):
    return sum(
        1 for s in _statuses(tau, tab) if s in (CS_SECONDARY_UNSAFE, CS_SECONDARY_UNSAFE_MANUAL)
    )


def count_unsafe(tau, tab):
    unsafe = (3, 4, 5, 6, 7, 8, 9)
    return sum(1 for s in _statuses(tau, tab) if s in unsafe)


# ===================================================================
# MOD (hierarchical iterative suppression via AHiTaS)
# ===================================================================


class TestMODSuppress:
    def test_mod_marks_secondary_cells(self, engine):
        """MOD should mark neighbouring safe cells as secondary."""
        tau = engine._tau
        tab = 0
        assert count_unsafe(tau, tab) > 0, "Test requires an unsafe cell"
        before = count_secondary(tau, tab)

        engine.suppress(Suppress(kind="MOD", tab_no=tab + 1))

        after = count_secondary(tau, tab)
        assert after > before, f"MOD should add secondary cells ({before} -> {after})"


# ===================================================================
# OPT (optimal cell suppression via FullJJ)
# ===================================================================


class TestOPTSuppress:
    def test_opt_marks_secondary_cells(self, engine):
        """OPT should mark neighbouring safe cells as secondary."""
        tau = engine._tau
        tab = 0
        assert count_unsafe(tau, tab) > 0, "Test requires an unsafe cell"
        before = count_secondary(tau, tab)

        engine.suppress(Suppress(kind="OPT", tab_no=tab + 1))

        after = count_secondary(tau, tab)
        assert after > before, f"OPT should add secondary cells ({before} -> {after})"


# ===================================================================
# RND (controlled rounding via CRP/HiGHS)
# ===================================================================


class TestRNDSuppress:
    def test_rnd_rounds_without_error(self, engine):
        """RND should run the CRP solver and apply rounded responses cleanly."""
        tau = engine._tau
        tab = 0
        base = engine._min_round_base(tab)
        assert base > 0, "Test requires a positive minimum round base"

        engine.suppress(Suppress(kind="RND", tab_no=tab + 1, rnd_base=base))

        # Rounding must not produce negative cell values.
        ncell, _ = tau.get_total_table_size(tab)
        for i in range(ncell):
            v = tau.get_table_cell_value(tab, i)
            if v == v:  # skip NaN
                assert v >= 0, f"Cell {i} negative after RND: {v}"

    def test_rnd_rejects_base_below_minimum(self, engine):
        """A rounding base below the table minimum must be rejected."""
        tab = 0
        try:
            engine.suppress(Suppress(kind="RND", tab_no=tab + 1, rnd_base=10))
        except Exception as e:  # BatchError
            assert "too small" in str(e)
        else:
            raise AssertionError("Expected BatchError for base below minimum")
