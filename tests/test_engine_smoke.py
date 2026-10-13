"""End-to-end smoke test for the headless engine.

Runs the sample microdata batch ``TestRecode.arb`` to completion and asserts
invariants that must hold for a correctly computed (pre-suppression) table:
the right number of tables, the right explanatory variables per table, and a
finite minimum cell value on every table.
"""

from pathlib import Path

import pytest

from pytauargus.engine import Engine, run_batch

from conftest import DATA


@pytest.fixture(scope="module")
def engine() -> Engine:
    return run_batch(DATA / "TestRecode.arb")


def test_runs_without_error(engine):
    # A successful run leaves the engine populated.
    assert engine._n_tables == 2


def test_table_count(engine):
    assert len(engine._tables) == 2


def test_explanatory_variables(engine):
    got = [tuple(spec.exp_vars) for spec, _ in engine._tables]
    assert got == [("Size", "Region"), ("Region", "IndustryCode")]


def test_response_variable(engine):
    assert all(spec.resp_var == "Var2" for spec, _ in engine._tables)


def test_tables_computed(engine):
    """Both tables must be computed (cells present) before any suppression."""
    tau = engine._tau
    for i in range(engine._n_tables):
        ncell, _ = tau.get_total_table_size(i)
        assert ncell > 0, f"table {i} has no cells"
        mn, mx = tau.get_minimum_cell_value(i)
        assert mn == mn  # not NaN
        assert mn >= 0
        assert mx >= mn


def test_metadata_loaded(engine):
    assert engine._metadata is not None
    assert len(engine._metadata.variables) == 14


def test_recodes_applied(engine):
    by = {v.name: v for v in engine._metadata.variables}
    assert by["Size"].recoded
    assert by["Region"].recoded
    assert by["IndustryCode"].recoded


def test_engine_instance_reusable_interface():
    e = Engine()
    assert e._metadata is None
    assert e._n_tables == 0
