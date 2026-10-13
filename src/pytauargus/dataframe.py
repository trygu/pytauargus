"""DataFrame <-> microdata adapters (input + output).

Tau-Argus microdata is a ``dict[str, list]`` (column name -> list of values).
This module converts between that canonical form and DataFrame-like objects,
so users can feed a ``pandas``/``polars`` DataFrame straight in and read the
protected result straight back out.

The core is dependency-free: the adapters *duck-type* the frame. If ``pandas``
or ``polars`` is installed the native fast paths are used; otherwise a
dict-of-lists / list-of-dicts is produced. Nothing here imports a frame library
at module scope, so ``import pytauargus`` never pulls one in.

Column-type contract (matches ``micro_asc_rda`` inference):

* ``int`` / ``float`` values are kept numeric -> the variable is ``NUMERIC``.
* ``bool`` values are coerced to ``int``.
* ``str`` values stay ``str`` -> the variable is categorical (``RECODEABLE``).
* missing values (``None`` / NaN / NA / NaT) become ``None``.

An integer-coded categorical (plain ``int``/``float``) therefore stays numeric;
to make a variable categorical, pass its codes as strings.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Sequence, Union

__all__ = [
    "to_microdata",
    "make_frame",
    "has_frame_lib",
    "frame_lib",
]

# numpy scalar type names -> canonical python type
_NP_INTS = {
    "int8", "int16", "int32", "int64",
    "uint8", "uint16", "uint32", "uint64",
}
_NP_FLOATS = {"float16", "float32", "float64", "longdouble", "half"}
_NP_STRS = {"str_", "string_", "bytes_"}


def _norm_scalar(v):
    """Normalise a single cell value to the microdata contract.

    int->int, float->float (NaN->None), str->str, bool->int,
    NA/NaT/None->None, anything else->str.
    """
    if v is None:
        return None
    t = type(v)
    mod = t.__module__ or ""
    name = t.__name__

    # booleans -> int (python bool, numpy bool_)
    if name == "bool" or (mod == "numpy" and name == "bool_"):
        return int(v)
    # integers
    if name == "int" or (mod == "numpy" and name in _NP_INTS):
        return int(v)
    # floats (NaN -> None)
    if name == "float" or (mod == "numpy" and name in _NP_FLOATS):
        f = float(v)
        return None if f != f else f
    # strings (python str, numpy str_/string_/bytes_)
    if name == "str" or (mod == "numpy" and name in _NP_STRS):
        return str(v)
    # pandas / pyarrow missing sentinels
    if name in ("NAType", "NaTType", "NAType", "PandasNA"):
        return None
    # fallback: categorical (character) value
    return str(v)


def _is_sequence(v):
    return isinstance(v, (list, tuple)) or (
        hasattr(v, "__iter__") and not isinstance(v, (str, bytes, dict))
    )


def _column_names(columns):
    names = [str(column) for column in columns]
    if len(set(names)) != len(names):
        raise ValueError("Column names must be unique after string conversion")
    return names


def _checked_microdata(columns):
    if len({len(values) for values in columns.values()}) > 1:
        raise ValueError("Microdata columns must all have the same length")
    return columns


def _read_parquet_microdata(path) -> Dict[str, list]:
    """Read a Parquet file into the microdata dict (requires pyarrow)."""
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise ImportError(
            "reading .parquet microdata requires pyarrow; install it with "
            "`pip install pytauargus[parquet]`") from exc
    table = pq.read_table(str(path))
    out = {name: [_norm_scalar(v) for v in table[name].to_pylist()]
           for name in table.column_names}
    return _checked_microdata(out)


def to_microdata(df) -> Dict[str, list]:
    """Convert a DataFrame-like object to the microdata ``dict[str, list]``.

    Accepts:

    * a ``pandas.DataFrame`` (``to_dict(orient="list")`` fast path),
    * a ``polars.DataFrame`` (``{col: df[col].to_list()}``),
    * a ``dict`` of column -> list/tuple (returned after normalising values),
    * a ``list`` of row ``dict`` (transposed to column -> list),
    * a path (or ``s3://``, ``gs://`` or ``https://`` URL) to a ``.parquet``
      file (requires the ``parquet`` extra; cloud credentials come from the
      environment, as pyarrow requires them).

    Column names are converted to strings and must remain unique. Column
    lengths must match; row dicts use all keys, in first-seen order, with
    missing fields set to ``None``. Invalid shapes raise ``ValueError``.
    Raises ``TypeError`` for unsupported inputs.
    """
    if df is None:
        raise TypeError("microdata must not be None")

    # Parquet file path (requires pyarrow, the ``parquet`` extra)
    if isinstance(df, (str, os.PathLike)) and str(df).lower().endswith(".parquet"):
        return _read_parquet_microdata(df)

    # dict of columns -> values
    if isinstance(df, dict):
        cols = list(df)
        out: Dict[str, list] = {}
        for c, name in zip(cols, _column_names(cols)):
            vals = df[c]
            if not _is_sequence(vals):
                raise TypeError(
                    "microdata dict values must be list/tuple per column; "
                    f"column {c!r} has {type(vals).__name__}")
            out[name] = [_norm_scalar(v) for v in vals]
        return _checked_microdata(out)

    # list of row dicts
    if isinstance(df, (list, tuple)):
        if not df:
            return {}
        if not all(isinstance(r, dict) for r in df):
            raise TypeError(
                "a list microdata must be a list of row dicts; got "
                f"{type(df[0]).__name__}")
        keys = list(dict.fromkeys(k for row in df for k in row))
        names = _column_names(keys)
        out = {name: [] for name in names}
        for r in df:
            for k, name in zip(keys, names):
                out[name].append(_norm_scalar(r.get(k)))
        return out

    # DataFrame duck-type: has .columns and a per-column accessor
    columns = getattr(df, "columns", None)
    if columns is None:
        raise TypeError(
            "microdata must be a dict of lists, a list of dicts, "
            f"or a DataFrame with .columns (got {type(df).__name__})")

    columns = list(columns)
    colnames = _column_names(columns)
    mod = type(df).__module__ or ""
    if mod.startswith("polars"):
        out = {}
        for c, name in zip(columns, colnames):
            series = df[c]
            out[name] = [_norm_scalar(v) for v in series.to_list()]
        return _checked_microdata(out)
    if hasattr(df, "to_dict"):
        raw = df.to_dict(orient="list")
        out = {}
        for c, name in zip(columns, colnames):
            out[name] = [_norm_scalar(v) for v in raw[c]]
        return _checked_microdata(out)

    # last resort: generic column accessor df[col]
    out = {}
    for c, name in zip(columns, colnames):
        col = df[c]
        if not _is_sequence(col):
            col = list(col)
        out[name] = [_norm_scalar(v) for v in col]
    return _checked_microdata(out)


def has_frame_lib() -> bool:
    """True if ``pandas`` or ``polars`` is importable."""
    return frame_lib() is not None


def frame_lib() -> Union[str, None]:
    """Return the first available frame library name (``"pandas"``/``"polars"``).

    Prefers ``pandas`` when both are installed. Returns ``None`` if neither is
    present. Never raises.
    """
    for name in ("pandas", "polars"):
        try:
            __import__(name)
            return name
        except Exception:
            continue
    return None


def make_frame(records: Sequence[dict]) -> Union[object, List[dict]]:
    """Build a DataFrame (or list-of-dicts) from ``records`` (list of row dicts).

    Uses ``pandas`` if importable, else ``polars``, else returns the
    ``records`` unchanged (a list of dicts). This is the output-side mirror of
    :func:`to_microdata`.
    """
    if not records:
        return {}
    keys = list(records[0])
    for lib in (frame_lib(),):
        if lib == "pandas":
            import pandas as pd
            cols = {k: [r.get(k) for r in records] for k in keys}
            return pd.DataFrame(cols)
        if lib == "polars":
            import polars as pl
            return pl.DataFrame(records)
    return list(records)
