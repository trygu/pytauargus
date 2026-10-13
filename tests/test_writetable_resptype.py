"""WRITETABLE response type must follow the legacy SaveTable state machine.

The legacy batch processor (sdcTools/tauargus, src/tauargus/model/
SaveTable.java, writeTable) derives the WriteCSV/WriteCellRecords response
type from the table state::

    respType = 0                      # raw values
    respType = 1 if tableSet.rounded  # after RND
    respType = 2 if tableSet.ctaProtect
    respType = 3 if tableSet.ckmProtect

The port originally hard-coded ``respType=1``.  Before RND the rounded
response is 0 for every cell (DataCell constructor), so the pre-RND CSV
came out as an all-zero table.  Regression: the pre-RND CSV must carry the
raw values (with unsafe cells masked as "x" and empty cells as "-", exactly
as native WriteCSVCell does for RespType 0), and the post-RND CSV must
carry the rounded values.
"""

import csv
from pathlib import Path

from pytauargus.batch import Suppress, WriteTable
from pytauargus.engine import run_batch

from conftest import DATA
ARB = DATA / "TestRecode.arb"

# Cell status codes from native/core/src/defines.h (1-based).
CS_SAFE = 1
CS_SAFE_MANUAL = 2
CS_UNSAFE_FIRST = 3
CS_UNSAFE_LAST = 9
CS_PROTECT_MANUAL = 10
CS_SECONDARY_FIRST = 11
CS_SECONDARY_LAST = 12
CS_EMPTY_NONSTRUCTURAL = 13
CS_EMPTY = 14

# Statuses for which native WriteCSVCell writes the numeric value
# (RespType 0: GetResp; RespType 1: GetRoundedResponse).
NUMERIC_STATUSES = frozenset(
    {CS_SAFE, CS_SAFE_MANUAL, CS_PROTECT_MANUAL, CS_EMPTY_NONSTRUCTURAL}
)


def _read_rows(path: Path):
    with open(path, newline="") as fh:
        return [row for row in csv.reader(fh) if row]


def _cell_grid(rows):
    """Data cells only: drop the header row and the row-label column."""
    return [row[1:] for row in rows[1:]]


def _linear_index(row, col, ncol):
    return row * ncol + col


def _expect_raw_csv(eng, tab, out: Path):
    """Assert the CSV matches the native RespType-0 output for every cell."""
    eng.write_table(WriteTable(tab_no=tab + 1, output_type=1, file=str(out)))
    rows = _read_rows(out)
    grid = _cell_grid(rows)

    ncell, _ = eng._tau.get_total_table_size(tab)
    ncol = len(grid[0])
    assert len(grid) * ncol == ncell, "CSV grid shape != table size"

    for r in range(len(grid)):
        for c in range(ncol):
            i = _linear_index(r, c, ncol)
            status = eng._tau.get_table_cell_status(tab, i)
            cell = grid[r][c]
            if status == CS_EMPTY:
                assert cell == "-", f"cell {i}: expected '-', got {cell!r}"
            elif status in NUMERIC_STATUSES:
                expected = eng._tau.get_table_cell_value(tab, i)
                assert cell not in ("", "-", "x"), f"cell {i}: empty raw value"
                # The native writer prints nDec decimals, so compare with a
                # small relative tolerance rather than exact equality.
                assert abs(float(cell) - expected) <= 1e-9 * max(1.0, abs(expected)), (
                    f"cell {i}: CSV {cell!r} != raw response {expected} "
                    "(hardcoded respType=1 writes 0 before RND)"
                )
            else:
                # unsafe / secondary: masked as "x" (ShowUnsafe = false)
                assert cell == "x", f"cell {i} (status {status}): expected 'x', got {cell!r}"


def _expect_rounded_csv(eng, tab, base, out: Path):
    """After RND the CSV carries rounded responses (RespType 1)."""
    eng.write_table(WriteTable(tab_no=tab + 1, output_type=1, file=str(out)))
    rows = _read_rows(out)
    grid = _cell_grid(rows)

    ncell, _ = eng._tau.get_total_table_size(tab)
    ncol = len(grid[0])
    assert len(grid) * ncol == ncell, "CSV grid shape != table size"
    for r in range(len(grid)):
        for c in range(ncol):
            # Fixture table 1 is 2-dimensional (Size x Region), so (r, c)
            # is a valid dim_index for the native cell getter.
            t = eng._tau.get_table_cell(tab, (r, c))
            assert t[0] is True, f"cell ({r},{c}): native cell getter failed"
            rresp, status = t[2], t[10]
            cell = grid[r][c]
            if status == CS_EMPTY:
                assert cell == "-"
                continue
            assert float(cell) == rresp, f"cell ({r},{c}): CSV {cell!r} != rounded {rresp}"
            if rresp != 0:
                assert round(rresp / base) * base == rresp, (
                    f"cell ({r},{c}): rounded value {rresp} is not a multiple of {base}"
                )


class TestWriteTableRespType:
    def test_csv_before_rnd_writes_raw_values(self, tmp_path):
        eng = run_batch(ARB)
        out = tmp_path / "raw.csv"
        _expect_raw_csv(eng, 0, out)

    def test_csv_after_rnd_writes_rounded_values(self, tmp_path):
        eng = run_batch(ARB)
        tab = 0
        base = eng._min_round_base(tab)
        assert base > 0, "Test requires a positive minimum round base"
        eng.suppress(Suppress(kind="RND", tab_no=tab + 1, rnd_base=base))
        out = tmp_path / "rnd.csv"
        _expect_rounded_csv(eng, tab, base, out)
