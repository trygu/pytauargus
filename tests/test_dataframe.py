"""Tests for the DataFrame <-> microdata adapters (pure Python, no engine).

The adapters are duck-typed so the core is testable without pandas/polars
installed: a *stub* frame exposes the same ``.columns`` / ``df[col]`` /
``to_dict`` surface. Real-library paths are guarded with ``importorskip``.
"""

import math

import pytest

from pytauargus.dataframe import (
    to_microdata,
    make_frame,
    has_frame_lib,
    frame_lib,
)


# ===========================================================================
# dict of lists / list of dicts — no dependency required
# ===========================================================================
def test_dict_of_lists_passthrough():
    md = to_microdata({"A": [1, 2, 3], "B": ["x", "y", "z"]})
    assert md == {"A": [1, 2, 3], "B": ["x", "y", "z"]}
    assert isinstance(md, dict)


def test_dict_numeric_types_preserved():
    md = to_microdata({"i": [1, 2], "f": [1.5, 2.0], "b": [True, False]})
    assert md["i"] == [1, 2]
    assert md["f"] == [1.5, 2.0]
    # bools become ints (microdata contract)
    assert md["b"] == [1, 0]
    assert all(isinstance(v, int) for v in md["b"])


def test_dict_nan_to_none():
    md = to_microdata({"V": [1.0, float("nan"), 3.0], "S": ["a", None, "c"]})
    assert md["V"] == [1.0, None, 3.0]
    assert md["S"] == ["a", None, "c"]


def test_list_of_dicts_transposed():
    rows = [{"A": 1, "B": "x"}, {"A": 2, "B": "y"}]
    md = to_microdata(rows)
    assert md == {"A": [1, 2], "B": ["x", "y"]}


def test_empty_inputs():
    assert to_microdata({}) == {}
    assert to_microdata([]) == {}


def test_bad_inputs_raise():
    with pytest.raises(TypeError):
        to_microdata(None)
    with pytest.raises(TypeError):
        to_microdata(42)
    with pytest.raises(TypeError):
        to_microdata({"A": "not a list"})
    with pytest.raises(TypeError):
        to_microdata([1, 2, 3])  # list must be list of dicts
    with pytest.raises(TypeError):
        to_microdata("a string")


def test_non_string_values_become_categorical_strings():
    md = to_microdata({"V": [object()]})
    # fallback: unknown scalar -> str
    assert isinstance(md["V"][0], str)


# ===========================================================================
# duck-typed frame (stub) — no pandas needed
# ===========================================================================
class _StubFrame:
    """Mimics the minimal DataFrame surface the adapter uses."""

    def __init__(self, cols):
        self._cols = {str(k): list(v) for k, v in cols.items()}

    @property
    def columns(self):
        return list(self._cols)

    def __getitem__(self, key):
        class _Col:
            def __init__(self, vals):
                self._vals = vals

            def to_list(self):
                return list(self._vals)

        return _Col(self._cols[key])

    def to_dict(self, orient="list"):
        assert orient == "list"
        return {k: list(v) for k, v in self._cols.items()}


def test_stub_frame_columns_and_values():
    df = _StubFrame({"A": [1, 2, 3], "B": ["p", "q", "r"]})
    md = to_microdata(df)
    assert md == {"A": [1, 2, 3], "B": ["p", "q", "r"]}


def test_stub_frame_nan_and_missing():
    df = _StubFrame({"V": [1.0, float("nan")], "S": ["a", None]})
    md = to_microdata(df)
    assert md["V"] == [1.0, None]
    assert md["S"] == ["a", None]


def test_stub_frame_bool_coerced():
    df = _StubFrame({"B": [True, False]})
    md = to_microdata(df)
    assert md["B"] == [1, 0]


# ===========================================================================
# real pandas (guarded)
# ===========================================================================
def test_pandas_to_microdata():
    pd = pytest.importorskip("pandas")
    import numpy as np

    df = pd.DataFrame({"A": [1, 2, 3], "B": ["x", None, "z"],
                       "F": [1.0, np.nan, 3.0]})
    md = to_microdata(df)
    assert md["A"] == [1, 2, 3]
    assert md["B"] == ["x", None, "z"]
    assert md["F"] == [1.0, None, 3.0]


def test_pandas_int64_and_float64():
    pd = pytest.importorskip("pandas")
    import numpy as np

    df = pd.DataFrame({"I": np.array([1, 2, 3], dtype=np.int64),
                       "F": np.array([1.5, 2.5, 3.5], dtype=np.float64)})
    md = to_microdata(df)
    assert md["I"] == [1, 2, 3]
    assert all(isinstance(v, int) for v in md["I"])
    assert md["F"] == [1.5, 2.5, 3.5]


# ===========================================================================
# real polars (guarded)
# ===========================================================================
def test_polars_to_microdata():
    pl = pytest.importorskip("polars")
    df = pl.DataFrame({"A": [1, 2, 3], "B": ["x", "y", "z"]})
    md = to_microdata(df)
    assert md["A"] == [1, 2, 3]
    assert md["B"] == ["x", "y", "z"]


def test_polars_null_to_none():
    pl = pytest.importorskip("polars")
    df = pl.DataFrame({"A": [1, None, 3]})
    md = to_microdata(df)
    assert md["A"] == [1, None, 3]


# ===========================================================================
# make_frame (output side)
# ===========================================================================
def test_make_frame_fallback_to_list_of_dicts():
    records = [{"A": 1, "B": "x"}, {"A": 2, "B": "y"}]
    lib = frame_lib()
    out = make_frame(records)
    if lib is None:
        # no frame lib -> list of dicts
        assert out == records
        assert isinstance(out, list)
    else:
        # a frame with the same data
        if lib == "pandas":
            assert list(out.columns) == ["A", "B"]
            assert out["A"].tolist() == [1, 2]
        else:  # polars
            assert list(out.columns) == ["A", "B"]
            assert out["A"].to_list() == [1, 2]


def test_make_frame_empty():
    assert make_frame([]) == {}


def test_frame_lib_consistency():
    lib = frame_lib()
    assert (has_frame_lib() is True) == (lib is not None)
    if lib is not None:
        assert lib in ("pandas", "polars")


@pytest.mark.parametrize("columns", [
    {"A": [1], "B": [2, 3]}, {"B": [2, 3], "A": [1]},
    {"A": [], "B": [1]}, {"B": [1], "A": []},
])
def test_dict_rejects_unequal_column_lengths(columns):
    with pytest.raises(ValueError, match="same length"):
        to_microdata(columns)


@pytest.mark.parametrize("records", [
    [{"A": 1}, {"B": 2, "A": 3}],
    [{"B": 2, "A": 3}, {"A": 1}],
])
def test_records_preserve_later_keys_and_missing_fields(records):
    result = to_microdata(records)
    assert list(result) == list(dict.fromkeys(k for row in records for k in row))
    for column in ("A", "B"):
        assert result[column] == [row.get(column) for row in records]


@pytest.mark.parametrize("kind", ["pandas", "generic"])
def test_frame_reads_original_non_string_labels(kind):
    columns = {1: [10, 20], ("group", 2): ["a", "b"]}
    if kind == "pandas":
        frame = pytest.importorskip("pandas").DataFrame(columns)
    else:
        class Frame:
            def __init__(self):
                self.columns = list(columns)

            def __getitem__(self, key):
                return columns[key]
        frame = Frame()
    assert to_microdata(frame) == {"1": [10, 20], "('group', 2)": ["a", "b"]}


@pytest.mark.parametrize("data", [{1: [10], "1": [20]}, [{1: 10, "1": 20}]])
def test_rejects_names_colliding_after_string_conversion(data):
    with pytest.raises(ValueError, match="unique.*string"):
        to_microdata(data)


@pytest.mark.parametrize("labels", [[1, "1"], ["A", "A"]])
def test_pandas_rejects_duplicate_normalized_names(labels):
    pd = pytest.importorskip("pandas")
    with pytest.raises(ValueError, match="unique.*string"):
        to_microdata(pd.DataFrame([[10, 20]], columns=labels))
