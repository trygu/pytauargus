"""Apriori-file smoke tests.

An apriori file pre-sets cell status / cost / protection levels for a computed
table before suppression. Each line is a data row (there is **no header**):

    <code1>;<code2>;<TYPE> [;value1 [;value2]]

- S / U / P / M / ML  -> set the cell status
- C / W <cost>        -> set the cell cost
- PL <lpl> <upl>      -> set the protection levels (primary-unsafe cells only)

The first line is validated for separator + field count *and* applied. Empty
cells (status 14/13) are never modified ("met status EMPTY mag niks gebeuren").

The test discovers codes and their dims dynamically from the computed table so
it does not depend on the specific (whitespace-padded) code strings.

Cell status codes (native/core/src/defines.h):
    CS_SAFE=1  CS_SAFE_MANUAL=2  CS_UNSAFE_RULE=3  CS_UNSAFE_MANUAL=9
    CS_PROTECT_MANUAL=10  CS_SECONDARY_UNSAFE=11  CS_SECONDARY_UNSAFE_MANUAL=12
    CS_EMPTY=14
"""

from pathlib import Path

import pytest

from pytauargus.batch import Apriory
from pytauargus.engine import run_batch

from conftest import DATA

# Status constants (define.h)
CS_SAFE = 1
CS_SAFE_MANUAL = 2
CS_UNSAFE_RULE = 3
CS_UNSAFE_MANUAL = 9
CS_PROTECT_MANUAL = 10
CS_EMPTY = 14


@pytest.fixture()
def engine():
    """Fresh computed tables per test (mutation-safe, like test_suppress)."""
    return run_batch(DATA / "TestRecode.arb")


def _status(tau, tab, dim):
    res = tau.get_table_cell(tab, list(dim), 0)
    return res[10] if res and res[0] else -1


def _code(tau, vi, idx):
    """Active code string at ``idx`` (stripped) for variable ``vi``."""
    ok, _ct, cs, _m, _l = tau.get_var_code(vi, idx)
    return (cs or "").strip()


def _exp_var_indices(engine):
    spec, _ = engine._tables[0]
    return [engine._metadata.index_of(n) for n in spec.exp_vars]


def _find_cell(engine, want_status):
    """Return ``(dim, [code1, code2])`` of the first cell with ``want_status``."""
    tau = engine._tau
    vi0, vi1 = _exp_var_indices(engine)
    n0, _ = tau.get_var_number_of_codes(vi0)
    n1, _ = tau.get_var_number_of_codes(vi1)
    for d0 in range(n0):
        for d1 in range(n1):
            if _status(tau, 0, [d0, d1]) == want_status:
                return [d0, d1], [_code(tau, vi0, d0), _code(tau, vi1, d1)]
    raise AssertionError(f"no cell with status {want_status} found")


def _apply(engine, tmp_path, *lines, strict=False):
    p = tmp_path / "apriori.txt"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return engine.apply_apriori(
        Apriory(file=str(p), tab_no=1, separator=";",
                ignore_error=not strict))


# ===================================================================
# Status changes
# ===================================================================


def test_s_marks_cell_safe_manual(engine, tmp_path):
    """S on a known-unsafe cell -> safe_manual (2)."""
    dim, (c0, c1) = _find_cell(engine, CS_UNSAFE_RULE)
    stats = _apply(engine, tmp_path, f"{c0};{c1};S")
    assert stats["status_ok"] == 1
    assert _status(engine._tau, 0, dim) == CS_SAFE_MANUAL


def test_u_marks_safe_cell_unsafe_manual(engine, tmp_path):
    """U on a safe cell -> unsafe_manual (9)."""
    dim, (c0, c1) = _find_cell(engine, CS_SAFE)
    stats = _apply(engine, tmp_path, f"{c0};{c1};U")
    assert stats["status_ok"] == 1
    assert _status(engine._tau, 0, dim) == CS_UNSAFE_MANUAL


def test_p_marks_cell_protected(engine, tmp_path):
    """P on a safe cell -> protected (10)."""
    dim, (c0, c1) = _find_cell(engine, CS_SAFE)
    _apply(engine, tmp_path, f"{c0};{c1};P")
    assert _status(engine._tau, 0, dim) == CS_PROTECT_MANUAL


def test_empty_cell_unchanged(engine, tmp_path):
    """An empty (structurally-zero) cell is never modified."""
    dim, (c0, c1) = _find_cell(engine, CS_EMPTY)
    stats = _apply(engine, tmp_path, f"{c0};{c1};S")
    assert stats["status_ok"] == 0
    assert _status(engine._tau, 0, dim) == CS_EMPTY


# ===================================================================
# Cost changes
# ===================================================================


def test_c_changes_cost(engine, tmp_path):
    """C <cost> is accepted by the native engine on a non-empty cell.

    Native quirk: ``SetTableCellCost`` returns True but ``GetTableCell``'s
    cost field is not the store it writes to (the legacy Java read the
    cost from the Java-side cell cache, which we do not keep). The honest
    observable is the setter's success, surfaced as ``cost_ok``.
    """
    dim, (c0, c1) = _find_cell(engine, CS_SAFE)
    stats = _apply(engine, tmp_path, f"{c0};{c1};C;5")
    assert stats["cost_ok"] == 1
    assert stats["cost_err"] == 0


# ===================================================================
# Error handling
# ===================================================================


def test_unknown_code_ignored_by_default(engine, tmp_path):
    """A code not in the codelist is skipped (ignore_error=True).

    Java counts one ``lines_error`` per bad code; ``XX`` (size) and a valid
    region code yield exactly one error.
    """
    _dim, (_c0, c1) = _find_cell(engine, CS_SAFE)
    stats = _apply(engine, tmp_path, f"XX;{c1};S")
    assert stats["lines_error"] == 1
    assert stats["status_ok"] == 0


def test_unknown_code_raises_when_strict(engine, tmp_path):
    """ignore_error=False raises on the first bad code."""
    with pytest.raises(Exception):
        _apply(engine, tmp_path, "XX;YY;S", strict=True)


def test_illegal_command_raises_when_strict(engine, tmp_path):
    """An unknown change type raises when ignore_error=False."""
    dim, (c0, c1) = _find_cell(engine, CS_SAFE)
    with pytest.raises(Exception):
        _apply(engine, tmp_path, f"{c0};{c1};ZZ", strict=True)


def test_wrong_field_count_raises(engine, tmp_path):
    """A row whose code-field count != n_exp raises on the first line."""
    with pytest.raises(Exception):
        _apply(engine, tmp_path, "A;B;C;S", strict=True)
