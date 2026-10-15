"""WRITETABLE output formats must match the legacy argument mapping.

The legacy batch processor (recovered at engine commit ``307b977^``,
``src/tauargus/model/batch.java`` writeTableBatch and
``src/tauargus/model/SaveTable.java`` writeTable) maps each WRITETABLE
output type to a specific native call with specific flags::

    type 1 CSV:         WriteCSV(tab, file, embed, identity, resp)
    type 2 pivot:       WriteCellRecords(tab, file, 0, false, SE,
                                         firstLine, AS, embed, resp)
    type 3 code value:  WriteCellRecords(tab, file, 0, false, SE,
                                         firstLine or "", AS, embed, resp)
    type 4 SBS:         WriteCellRecords(tab, file, 1, HL, true,
                                         "", true, embed, resp)

Per-command defaults (batch.java): embed=on, add-status/suppress-empty/
hierarchical off; varnames-on-first-line on for pivot only.  The flags
can be overridden by ``XX+``/``XX-`` option tokens.  ``firstLine`` is the
variable-name header built by ``SaveTable.makeFirstLine``.

Before the fix the port collapsed types 2-7 into one
``WriteCellRecords(tab, file, 1, false, false, "", false, true, resp)``
call: the SBS export masked suppressed cells as ``x`` and wrote the
empty rows, and the pivot and code value exports carried no header.
Types 6 (JJ) and 7 (CKM) had no native equivalent of the legacy
writers and are not implemented.
"""

from pytauargus.batch import WriteTable
from pytauargus.engine import (
    IDENTITY_DIM_SEQUENCE,
    BatchError,
    Engine,
    run_batch,
)

from conftest import DATA

ARB = DATA / "tableinput" / "TestTable.arb"

# SaveTable.makeFirstLine for the TestTable table (Size x Region,
# response Var2, Var2 is a plain numeric variable, not the frequency
# variable).
PIVOT_HEADER = '"Size","Region","Var2"'


def _engine() -> Engine:
    return run_batch(ARB)


def test_sbs_suppressed_cells_carry_values(tmp_path):
    """Type 4 (SBS) writes the response value of unsafe and secondary
    cells (ShowUnsafe=true), never the ``x`` mask."""
    eng = _engine()
    total = eng._tau.get_table_cell_value(0, 0)
    assert total > 0
    eng._tau.set_table_cell_status_cell(0, 0, 9)  # CS_UNSAFE_MANUAL
    out = tmp_path / "sbs.csv"
    eng.write_table(WriteTable(tab_no=1, output_type=4, file=str(out)))
    rows = [row for row in out.read_text().splitlines() if row]
    assert rows, "SBS export must not be empty"
    for row in rows:
        assert not (row == "x" or row.endswith(",x") or ",x," in row), (
            f"masked cell in SBS export: {row}")
    # The (now unsafe) grand total row keeps its numeric value.
    total_row = [r for r in rows if r.startswith('"Total"')]
    assert total_row, f"grand total row missing: {rows[:3]}"


def test_sbs_matches_legacy_argument_vector(tmp_path):
    """Type 4 must call WriteCellRecords with the legacy vector:
    SBS=1, SBSLevel=false, SuppressEmpty=true, no first line,
    ShowUnsafe=true, embed on."""
    eng = _engine()
    out = tmp_path / "eng.csv"
    eng.write_table(WriteTable(tab_no=1, output_type=4, file=str(out)))
    oracle = tmp_path / "oracle.csv"
    eng._tau.write_cell_records(0, str(oracle), 1, False, True, "", True,
                                True, 0)
    assert out.read_bytes() == oracle.read_bytes()


def test_pivot_first_line_and_arguments(tmp_path):
    """Type 2 (pivot) writes the variable-name header and uses
    SBS=0, ShowUnsafe=add-status (off by default)."""
    eng = _engine()
    out = tmp_path / "eng.csv"
    eng.write_table(WriteTable(tab_no=1, output_type=2, file=str(out)))
    lines = [ln for ln in out.read_text().splitlines() if ln]
    assert lines[0] == PIVOT_HEADER
    oracle = tmp_path / "oracle.csv"
    eng._tau.write_cell_records(0, str(oracle), 0, False, False,
                                PIVOT_HEADER, False, True, 0)
    assert out.read_bytes() == oracle.read_bytes()


def test_pivot_add_status_option(tmp_path):
    """Type 2 with ``+AS`` adds the Status column name to the header
    and switches on ShowUnsafe."""
    eng = _engine()
    out = tmp_path / "eng.csv"
    eng.write_table(WriteTable(tab_no=1, output_type=2,
                               options=["+AS"], file=str(out)))
    lines = [ln for ln in out.read_text().splitlines() if ln]
    assert lines[0] == PIVOT_HEADER + ',"Status"'
    oracle = tmp_path / "oracle.csv"
    eng._tau.write_cell_records(0, str(oracle), 0, False, False,
                                PIVOT_HEADER + ',"Status"', True, True, 0)
    assert out.read_bytes() == oracle.read_bytes()


def test_code_value_no_header_by_default(tmp_path):
    """Type 3 (code value) has no header unless ``+FL`` is given."""
    eng = _engine()
    out = tmp_path / "eng.csv"
    eng.write_table(WriteTable(tab_no=1, output_type=3, file=str(out)))
    lines = [ln for ln in out.read_text().splitlines() if ln]
    assert lines[0].startswith('"Total"'), (
        f"first line must be a data row, got {lines[0]!r}")
    oracle = tmp_path / "oracle.csv"
    eng._tau.write_cell_records(0, str(oracle), 0, False, False,
                                "", False, True, 0)
    assert out.read_bytes() == oracle.read_bytes()


def test_code_value_fl_option_adds_header(tmp_path):
    """Type 3 with ``+FL`` writes the variable-name header."""
    eng = _engine()
    out = tmp_path / "eng.csv"
    eng.write_table(WriteTable(tab_no=1, output_type=3,
                               options=["+FL"], file=str(out)))
    lines = [ln for ln in out.read_text().splitlines() if ln]
    assert lines[0] == PIVOT_HEADER
    oracle = tmp_path / "oracle.csv"
    eng._tau.write_cell_records(0, str(oracle), 0, False, False,
                                PIVOT_HEADER, False, True, 0)
    assert out.read_bytes() == oracle.read_bytes()


def test_csv_unquoted_option(tmp_path):
    """Type 1 (CSV) honours ``-QU`` (no embedded quotes)."""
    eng = _engine()
    out = tmp_path / "eng.csv"
    eng.write_table(WriteTable(tab_no=1, output_type=1,
                               options=["-QU"], file=str(out)))
    assert '"' not in out.read_text()
    oracle = tmp_path / "oracle.csv"
    eng._tau.write_csv(0, str(oracle), False, IDENTITY_DIM_SEQUENCE, 0)
    assert out.read_bytes() == oracle.read_bytes()


def test_jj_and_ckm_not_implemented(tmp_path):
    """Types 6 (JJ) and 7 (CKM) have no native equivalent of the
    legacy writers; they must fail with a clear error rather than
    write a wrong format."""
    eng = _engine()
    for output_type in (6, 7):
        try:
            eng.write_table(WriteTable(tab_no=1, output_type=output_type,
                                       file=str(tmp_path / "out")))
        except BatchError as exc:
            assert "not implemented" in str(exc)
        else:
            raise AssertionError(f"type {output_type} must not be written")
