"""Tests for :class:`pytauargus.result.TableResult` (pure Python, no engine).

The parser consumes the *simple* ``+SO`` intermediate ``.tab`` layout::

    magnitude: code1;...;codel; value; freq; cost; status; lower; upper
    frequency: code1;...;codel; value; cost; status; lower; upper

Fixtures are hand-written so the tests exercise parsing + the value/status/safe
views without a native engine.
"""

import pytest

import pytauargus.dataframe as dataframe_module

from pytauargus.result import (
    TableResult,
    parse_simple_tab,
    ST_SAFE,
    ST_UNSAFE,
    ST_PROTECTED,
    ST_SECONDARY,
    ST_EMPTY,
)


MAG2 = (
    '"A1";"B1";100.00;10;1.00;S;0.90;1.10\n'
    '"A1";"B2";20.00;2;1.00;U;0.00;0.00\n'
    '"A2";"B1";5.00;1;1.00;M;0.00;0.00\n'
    '"A2";"B2";0.00;0;1.00;E;0.00;0.00\n'
)

FREQ1 = (
    '"C1";5.00;1.00;S;0.50;5.50\n'
    '"C2";3.00;1.00;S;0.30;3.30\n'
)


@pytest.mark.parametrize("name", ["cost", "status", "lower", "upper", "freq"])
@pytest.mark.parametrize("role", ["response", "explanatory"])
def test_parser_rejects_diagnostic_name_collisions(tmp_path, name, role):
    path = _write(tmp_path, "m.tab", MAG2)
    explanatory = [name, "Y"] if role == "explanatory" else ["X", "Y"]
    response = name if role == "response" else "Val"
    with pytest.raises(ValueError, match="reserved"):
        parse_simple_tab(path, explanatory, response)


@pytest.mark.parametrize("explanatory,response", [
    (["X", "X"], "Val"), (["X", "Y"], "X"),
])
def test_parser_rejects_duplicate_source_names(tmp_path, explanatory, response):
    path = _write(tmp_path, "m.tab", MAG2)
    with pytest.raises(ValueError, match="unique"):
        parse_simple_tab(path, explanatory, response)


def test_frequency_response_can_be_named_freq(tmp_path):
    result = TableResult(_write(tmp_path, "f.tab", FREQ1), ["X"], "freq", is_freq=True)
    assert result.unsafe() == [5.0, 3.0]
    assert result.columns.count("freq") == 1


def test_from_rows_rejects_reserved_names():
    with pytest.raises(ValueError, match="reserved"):
        TableResult.from_rows([], ["X"], "status")


def test_dataframe_fallback_does_not_mutate_result(monkeypatch, tmp_path):
    monkeypatch.setattr(dataframe_module, "frame_lib", lambda: None)
    result = TableResult(_write(tmp_path, "m.tab", MAG2), ["X", "Y"], "Val")
    frame = result.dataframe()
    frame[0]["Val"] = -999
    frame[1]["status"] = "S"
    assert result.unsafe() == [100.0, 20.0, 5.0, 0.0]
    assert result.status() == ["S", "U", "M", "E"]
    assert result.safe() == [100.0, "x", "x", 0.0]


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


# ===========================================================================
# parse_simple_tab — magnitude (2 dims)
# ===========================================================================
def test_parse_magnitude_2d(tmp_path):
    path = _write(tmp_path, "m.tab", MAG2)
    rows = parse_simple_tab(path, ["X", "Y"], "Val", is_freq=False)
    assert len(rows) == 4
    r0 = rows[0]
    assert r0["X"] == "A1"
    assert r0["Y"] == "B1"
    assert r0["Val"] == 100.0
    assert r0["freq"] == 10
    assert r0["cost"] == 1.0
    assert r0["status"] == "S"
    assert r0["lower"] == pytest.approx(0.90)
    assert r0["upper"] == pytest.approx(1.10)


def test_parse_magnitude_status_column(tmp_path):
    path = _write(tmp_path, "m.tab", MAG2)
    rows = parse_simple_tab(path, ["X", "Y"], "Val")
    assert [r["status"] for r in rows] == ["S", "U", "M", "E"]


def test_parse_magnitude_codes_stripped(tmp_path):
    path = _write(tmp_path, "m.tab", MAG2)
    rows = parse_simple_tab(path, ["X", "Y"], "Val")
    # quoted code fields are unquoted + stripped of padding
    assert rows[0]["X"] == "A1"
    assert rows[2]["X"] == "A2"


# ===========================================================================
# parse_simple_tab — frequency (1 dim, no freq column)
# ===========================================================================
def test_parse_frequency_1d(tmp_path):
    path = _write(tmp_path, "f.tab", FREQ1)
    rows = parse_simple_tab(path, ["X"], "N", is_freq=True)
    assert len(rows) == 2
    assert "freq" not in rows[0]
    assert rows[0]["X"] == "C1"
    assert rows[0]["N"] == 5.0
    assert rows[0]["cost"] == 1.0
    assert rows[0]["status"] == "S"


def test_parse_wrong_code_count_raises(tmp_path):
    path = _write(tmp_path, "bad.tab", '"A1";"B1";"C1";100.00;10;1.00;S;0.9;1.1\n')
    with pytest.raises(ValueError):
        parse_simple_tab(path, ["X", "Y"], "Val")  # expects 2 codes, has 3


def test_parse_skips_blank_lines(tmp_path):
    text = MAG2 + "\n\n" + '"A3";"B3";1.00;1;1.00;S;0.1;1.9\n'
    path = _write(tmp_path, "m.tab", text)
    rows = parse_simple_tab(path, ["X", "Y"], "Val")
    assert len(rows) == 5


# ===========================================================================
# TableResult value / status / safe views
# ===========================================================================
def test_result_views(tmp_path):
    path = _write(tmp_path, "m.tab", MAG2)
    tr = TableResult(path, ["X", "Y"], "Val")

    assert tr.n_cells == 4
    assert len(tr) == 4
    assert tr.response == "Val"

    # unsafe = original response per cell
    assert tr.unsafe() == [100.0, 20.0, 5.0, 0.0]

    # status symbols
    assert tr.status() == [ST_SAFE, ST_UNSAFE, ST_SECONDARY, ST_EMPTY]

    # safe = published value with U and M masked
    assert tr.safe() == [100.0, "x", "x", 0.0]

    # custom marker
    assert tr.safe(unsafe_marker=0) == [100.0, 0, 0, 0.0]


def test_result_columns(tmp_path):
    path = _write(tmp_path, "m.tab", MAG2)
    tr = TableResult(path, ["X", "Y"], "Val")
    assert tr.columns == ["X", "Y", "Val", "freq", "cost", "status", "lower", "upper"]


def test_result_columns_frequency(tmp_path):
    path = _write(tmp_path, "f.tab", FREQ1)
    tr = TableResult(path, ["X"], "N", is_freq=True)
    assert tr.columns == ["X", "N", "cost", "status", "lower", "upper"]


def test_result_rows_copy(tmp_path):
    path = _write(tmp_path, "m.tab", MAG2)
    tr = TableResult(path, ["X", "Y"], "Val")
    rows = tr.rows
    rows[0]["X"] = "MUTATED"
    assert tr.rows[0]["X"] == "A1"  # defensive copy


def test_result_dataframe_fallback(tmp_path):
    from pytauargus.dataframe import frame_lib

    path = _write(tmp_path, "m.tab", MAG2)
    tr = TableResult(path, ["X", "Y"], "Val")
    out = tr.dataframe()
    if frame_lib() is None:
        # no frame lib -> list of dicts
        assert isinstance(out, list)
        assert out[0]["X"] == "A1"
        assert out[0]["Val"] == 100.0
    else:
        # a frame with the expected columns
        cols = list(out.columns)
        assert cols == tr.columns


# ===========================================================================
# from_rows (no file needed)
# ===========================================================================
def test_from_rows():
    rows = [
        {"X": "A", "Val": 1.0, "freq": 1, "cost": 1.0,
         "status": "S", "lower": 0.0, "upper": 1.0},
        {"X": "B", "Val": 2.0, "freq": 2, "cost": 1.0,
         "status": "U", "lower": 0.0, "upper": 0.0},
    ]
    tr = TableResult.from_rows(rows, ["X"], "Val")
    assert tr.n_cells == 2
    assert tr.status() == ["S", "U"]
    assert tr.safe() == [1.0, "x"]
    assert tr.unsafe() == [1.0, 2.0]


def test_repr():
    rows = [{"X": "A", "Val": 1.0, "status": "S"}]
    tr = TableResult.from_rows(rows, ["X"], "Val")
    assert "n_cells=1" in repr(tr)
    assert "Val" in repr(tr)
