"""Tests for the legacy ``<COVER>`` command (protect-cover-table mode).

``<COVER>`` (batch.java:281) is an internal, "not in the manual" keyword the
GUI writes into the cover-table sub-batch: it sets a global
``protectCoverTable`` flag and flips the (first) table's additivity to
``ADDITIVITY_NOT_REQUIRED``. Its practical effect on the solve contract is that
``CompletedTable(..., ForCoverTable=true)`` skips the additivity check, so a
non-additive cover table is read without raising ``TABLENOTADDITIVE``.

The heavy ``LinkedTables`` GUI machinery (which generates cover tables by
shelling out to ``intervalle.exe``) is out of scope for the headless CLI; only
the solve-side contract is ported here.
"""

import shutil
from pathlib import Path

import pytest

from pytauargus.batch import Cover, parse_batch
from pytauargus.engine import BatchError, Engine, run_batch

from conftest import DATA as _DATA

DATA = _DATA / "tableinput"


def _make_fixtures(tmp_path: Path) -> Path:
    """Copy the ``tableinput`` fixture and make its table non-additive.

    The grand-total row's response is doubled so the marginal/total sums no
    longer match the leaves — i.e. the table is not additive, exactly the
    situation a real (pre-aggregated) cover table can be in.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    for f in DATA.iterdir():
        if f.is_file():
            shutil.copy(f, tmp_path / f.name)
    lines = (tmp_path / "pp.tab").read_text().splitlines()
    fields = lines[0].split(";")
    # response is the 3rd field (Size;Region;Var2;...)
    fields[2] = str(float(fields[2].replace(",", ".")) * 2)
    lines[0] = ";".join(fields)
    (tmp_path / "pp.tab").write_text("\n".join(lines))
    return tmp_path


@pytest.fixture(scope="module")
def cover_fixtures(tmp_path_factory) -> Path:
    d = _make_fixtures(tmp_path_factory.mktemp("cover"))
    base = (
        '<OPENTABLEDATA>  "pp.tab"\n'
        '<OPENMETADATA>   "pp.rda"\n'
        '<SPECIFYTABLE>   "Size""Region"|"Var2"|"Var2_Shadow"|"Var2"\n'
    )
    (d / "nocover.arb").write_text(base + "<READTABLE>\n")
    (d / "cover.arb").write_text(base + "<COVER>\n<READTABLE>\n")
    return d


class TestCoverParse:
    def test_cover_token(self):
        text = (
            '<OPENTABLEDATA> "a.tab"\n'
            '<OPENMETADATA> "a.rda"\n'
            '<SPECIFYTABLE> "A"|"R"\n'
            "<COVER>\n"
            "<READTABLE>\n"
        )
        parsed = parse_batch_from_string(text)
        kinds = [type(c).__name__ for c in parsed]
        assert kinds == ["OpenTableData", "OpenMetadata", "SpecifyTable",
                         "Cover", "ReadTable"]
        assert isinstance(parsed[3], Cover)


def parse_batch_from_string(text: str):
    """Parse .arb text directly (helper for unit tests)."""
    import tempfile

    from pytauargus.batch import parse_batch as _pb

    with tempfile.NamedTemporaryFile("w", suffix=".arb", delete=False) as fh:
        fh.write(text)
        name = fh.name
    try:
        return _pb(Path(name))
    finally:
        Path(name).unlink(missing_ok=True)


class TestCoverSolveContract:
    def test_non_cover_non_additive_raises(self, cover_fixtures):
        # Without <COVER>, a non-additive tabular table fails at the
        # CompletedTable step (native TABLENOTADDITIVE = 1024).
        with pytest.raises(BatchError) as ei:
            run_batch(cover_fixtures / "nocover.arb")
        assert "CompletedTable" in str(ei.value)

    def test_cover_non_additive_succeeds(self, cover_fixtures):
        e = run_batch(cover_fixtures / "cover.arb")
        assert e._protect_cover_table is True
        assert e._n_tables == 1
        assert e._is_table
        ncell, _ = e._tau.get_total_table_size(0)
        assert ncell > 0
        total = e._tau.get_table_cell_value(0, 0)
        assert total == total  # not NaN

    def test_clear_resets_cover_flag(self, cover_fixtures):
        e = run_batch(cover_fixtures / "cover.arb")
        from pytauargus.batch import Clear

        e._execute(Clear())
        assert e._protect_cover_table is False
