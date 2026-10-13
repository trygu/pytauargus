"""End-to-end smoke test for the tabular (pre-aggregated) table flow.

Runs the sample batch ``tableinput/TestTable.arb`` (``<OPENTABLEDATA>`` +
``<READTABLE> 1T``) to completion and asserts invariants for a correctly read
tabular table: one table, the right explanatory variables, a non-empty cell
grid, and the grand-total cell matching the first (``"Total";"Total"``) row of
``pp.tab``.
"""

from pathlib import Path

import pytest

from pytauargus.engine import CS_SECONDARY_UNSAFE_MANUAL, Engine, run_batch

from conftest import DATA


@pytest.fixture(scope="module")
def engine() -> Engine:
    return run_batch(DATA / "tableinput" / "TestTable.arb")


def test_runs_without_error(engine):
    assert engine._n_tables == 1
    assert engine._is_table


def test_explanatory_variables(engine):
    got = [tuple(spec.exp_vars) for spec, _ in engine._tables]
    assert got == [("Size", "Region")]


def test_response_variable(engine):
    assert all(spec.resp_var == "Var2" for spec, _ in engine._tables)


def test_metadata_is_tabular(engine):
    assert engine._metadata is not None
    assert engine._metadata.is_table
    by = {v.name: v for v in engine._metadata.variables}
    assert by["Size"].type == "CATEGORICAL"
    assert by["Region"].type == "CATEGORICAL"
    assert by["Var2"].type == "RESPONSE"
    assert by["Var2_Shadow"].type == "SHADOW"
    assert by["Var2_Cost"].type == "COST"


def test_table_has_cells(engine):
    ncell, _ = engine._tau.get_total_table_size(0)
    assert ncell > 0


def test_grand_total_cell(engine):
    """Cell 0 is the grand total (Total;Total).

    The batch uses ``<READTABLE> 1T`` (additivity=1), so marginal/total cells
    are recomputed from the leaves; the value is therefore only checked to be
    of the right order of magnitude (the file's grand total is ~16.8M).
    """
    total = engine._tau.get_table_cell_value(0, 0)
    assert total == total  # not NaN
    assert 1e6 < total < 2e8


def test_min_cell_value_finite(engine):
    mn, mx = engine._tau.get_minimum_cell_value(0)
    assert mn == mn  # not NaN
    assert mn >= 0
    assert mx >= mn


# ===========================================================================
# Manual-unsafe default margin (legacy TableSet.manualMarge = 20; manual p.123)
# ===========================================================================

_EX1 = DATA / "examples" / "ex1"


def _code_index(eng, var_name, code):
    vi = eng.metadata.index_of(var_name)
    _n, n_active = eng._tau.get_var_number_of_codes(vi)
    for ci in range(n_active):
        ok, _ct, cs, _miss, _level = eng._tau.get_var_code(vi, ci)
        if ok and cs.strip() == code:
            return ci
    raise LookupError(f"code {code!r} not found for {var_name}")


def _cell(eng, tab, row, col):
    res = eng._tau.get_table_cell(tab, [_code_index(eng, row[0], row[1]),
                                        _code_index(eng, col[0], col[1])], 0)
    return float(res[1]), int(res[10])


def test_manual_unsafe_default_margin_is_20_percent():
    """A manually unsafe cell without a ``MAN`` rule gets the legacy default
    20% protection margin (legacy ``TableSet.manualMarge = 20``; manual
    p.123: "The default value = 20")."""
    eng = run_batch(_EX1 / "ex1.arb")
    value, status = _cell(eng, 0, ("Sto", "Stor"), ("Reg", "Nord"))
    assert (value, status) == (150.0, 9)  # single-company cell, manual U
    n_cols = eng._tau.get_var_number_of_codes(
        eng.metadata.index_of("Reg"))[1]
    ncell = _code_index(eng, "Sto", "Stor") * n_cols + _code_index(eng, "Reg", "Nord")
    lpl, upl = eng._tau.get_table_cell_protection_levels(0, ncell)
    assert (lpl, upl) == (30.0, 30.0), f"expected 20% margin, got {(lpl, upl)}"


def test_opt_protects_manual_unsafe_with_secondary_pattern():
    """OPT is cell suppression (manual 4.2.3.5): it never rewrites a cell
    value — the manually unsafe cell stays published, unsafe and unmodified —
    and marks a non-empty set of safe cells secondary (M) so the unsafe value
    cannot be isolated by differencing."""
    from pytauargus.batch import Suppress

    eng = run_batch(_EX1 / "ex1.arb")
    n, _ = eng._tau.get_total_table_size(0)
    before = [eng._tau.get_table_cell_value(0, i) for i in range(n)]

    eng.suppress(Suppress(kind="OPT", tab_no=1))

    after = [eng._tau.get_table_cell_value(0, i) for i in range(n)]
    assert before == after, "cell suppression must not rewrite cell values"
    value, status = _cell(eng, 0, ("Sto", "Stor"), ("Reg", "Nord"))
    assert (value, status) == (150.0, 9), "unsafe cell stays published"
    secondary = sum(
        1 for i in range(n)
        if eng._tau.get_table_cell_status(0, i) == CS_SECONDARY_UNSAFE_MANUAL)
    assert secondary >= 1, "expected a non-empty secondary pattern"
