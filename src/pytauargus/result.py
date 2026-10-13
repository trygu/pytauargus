"""``TableResult`` — a protected table read back as a DataFrame-friendly result.

The data source is the *simple* legacy WRITETABLE type-5 (INTERMEDIATE) audit
file written by the engine for a ``<WRITETABLE> (n, 5, "+SO", ...)` ` command.
Its row layout (one line per cell, ``;``-separated, row-major) is::

    magnitude table:  code1;...;codel; value; freq; cost; status; lower; upper
    frequency table:  code1;...;codel; value; cost; status; lower; upper

The trailing four fields are always ``cost; status; lower; upper``; before them
is the published ``value``; before that (magnitude only) is ``freq``; the rest
are the crossed explanatory codes. Because the trailing shape is fixed, the
parser is robust to any number of explanatory dimensions.

Status symbols follow the intermediate format (:func:`engine._status_symbol`):
``S`` safe, ``U`` primary unsafe, ``P`` protected, ``M`` secondary unsafe,
``E`` empty.

The result exposes a piargus-style API:

* ``.dataframe()``  -> the full result as a frame (or list of dicts)
* ``.status()``     -> one status symbol per cell
* ``.unsafe()``     -> the original (unprotected) response per cell
* ``.safe(marker)`` -> the published value, with unsafe/secondary cells masked
* ``.rows`` / ``.columns`` -> the parsed rows / column names
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Sequence, Union

from pytauargus.dataframe import make_frame

__all__ = ["TableResult", "parse_simple_tab"]

# Status symbols (see engine._status_symbol).
ST_SAFE = "S"
ST_UNSAFE = "U"
ST_PROTECTED = "P"
ST_SECONDARY = "M"
ST_EMPTY = "E"

# Symbols whose value is withheld from publication.
_WITHHOLD = (ST_UNSAFE, ST_SECONDARY)


def _validate_columns(exp_vars, response, is_freq=False):
    names = [str(name) for name in exp_vars] + [str(response)]
    if len(set(names)) != len(names):
        raise ValueError("Explanatory and response column names must be unique")
    reserved = {"cost", "status", "lower", "upper"}
    if not is_freq:
        reserved.add("freq")
    collisions = sorted(reserved.intersection(names))
    if collisions:
        raise ValueError(
            f"Table column names are reserved for result diagnostics: {collisions}; "
            "rename these source columns before protecting the table")


def parse_simple_tab(path: Union[str, Path],
                     exp_vars: Sequence[str],
                     response: str,
                     is_freq: bool = False) -> List[Dict[str, object]]:
    """Parse a simple ``+SO`` intermediate ``.tab`` into a list of row dicts.

    ``exp_vars`` names the crossed explanatory columns (order = file order);
    ``response`` names the published-value column; ``is_freq`` tells the parser
    whether the response is a frequency table (no ``freq`` column).
    """
    _validate_columns(exp_vars, response, is_freq)
    n_exp = len(exp_vars)
    rows: List[Dict[str, object]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line == "":
                continue
            f = line.split(";")
            upper = float(f[-1])
            lower = float(f[-2])
            status = f[-3]
            cost = float(f[-4])
            if is_freq:
                # codes; value; cost; status; lower; upper
                value = float(f[-5])
                freq = None
                codes = f[:-5]
            else:
                # codes; value; freq; cost; status; lower; upper
                freq = int(float(f[-5]))
                value = float(f[-6])
                codes = f[:-6]
            if len(codes) != n_exp:
                raise ValueError(
                    f"simple .tab line has {len(codes)} code fields, "
                    f"expected {n_exp}: {line!r}")
            rec: Dict[str, object] = {}
            for name, code in zip(exp_vars, codes):
                rec[name] = code.strip().strip('"')
            rec[response] = value
            if not is_freq:
                rec["freq"] = freq
            rec["cost"] = cost
            rec["status"] = status
            rec["lower"] = lower
            rec["upper"] = upper
            rows.append(rec)
    return rows


class TableResult:
    """A protected table, parsed from a simple intermediate ``.tab`` file.

    Parameters
    ----------
    path:
        Path to the ``.tab`` file (simple ``+SO`` layout).
    exp_vars:
        Ordered explanatory-variable names (the crossed dimensions).
    response:
        Name for the published-value column.
    is_freq:
        Whether the response is a frequency table (drops the ``freq`` column).
    """

    def __init__(self,
                 path: Union[str, Path],
                 exp_vars: Sequence[str],
                 response: str,
                 is_freq: bool = False):
        self._path = Path(path)
        self._exp = [str(v) for v in exp_vars]
        self._response = str(response)
        self._is_freq = is_freq
        self._rows = parse_simple_tab(self._path, self._exp,
                                      self._response, self._is_freq)

    @classmethod
    def from_rows(cls,
                  rows: Sequence[dict],
                  exp_vars: Sequence[str],
                  response: str,
                  is_freq: bool = False) -> "TableResult":
        """Build a result from already-parsed row dicts (e.g. for tests)."""
        _validate_columns(exp_vars, response, is_freq)
        obj = cls.__new__(cls)
        obj._path = None
        obj._exp = [str(v) for v in exp_vars]
        obj._response = str(response)
        obj._is_freq = is_freq
        obj._rows = [dict(r) for r in rows]
        return obj

    # -- shape ---------------------------------------------------------------
    @property
    def rows(self) -> List[Dict[str, object]]:
        return [dict(r) for r in self._rows]

    @property
    def columns(self) -> List[str]:
        cols = list(self._exp) + [self._response]
        if not self._is_freq:
            cols.append("freq")
        cols += ["cost", "status", "lower", "upper"]
        return cols

    @property
    def n_cells(self) -> int:
        return len(self._rows)

    @property
    def response(self) -> str:
        return self._response

    @property
    def path(self) -> Union[str, Path, None]:
        """The ``.tab`` file this result was parsed from, if any."""
        return self._path

    # -- values --------------------------------------------------------------
    def _col(self, name: str) -> List[object]:
        return [r[name] for r in self._rows]

    def unsafe(self) -> List[float]:
        """The original (unprotected) response value per cell."""
        return self._col(self._response)

    def status(self) -> List[str]:
        """One status symbol per cell (``S``/``U``/``P``/``M``/``E``)."""
        return self._col("status")

    def safe(self, unsafe_marker: object = "x") -> List[object]:
        """The published value per cell, unsafe/secondary cells masked.

        Cells whose status is unsafe (``U``) or secondary (``M``) are replaced
        by ``unsafe_marker``; all other cells keep their published value.
        """
        out: List[object] = []
        for r in self._rows:
            if r["status"] in _WITHHOLD:
                out.append(unsafe_marker)
            else:
                out.append(r[self._response])
        return out

    def dataframe(self):
        """The full result as a DataFrame (pandas/polars) or list of dicts.

        Returns a ``pandas.DataFrame`` if pandas is importable, a
        ``polars.DataFrame`` if only polars is, else the ``rows`` list.
        """
        return make_frame(self.rows)

    def to_parquet(self, path) -> None:
        """Write the published table to a Parquet file (requires pyarrow).

        The file contains the explanatory columns and the response. Withheld
        cells (status ``U`` or ``M``) are written as null; all other cells
        keep their published value. ``path`` may also be an ``s3://``,
        ``gs://`` or ``https://`` URL; pyarrow handles the transfer.
        """
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise ImportError(
                "to_parquet requires pyarrow; install it with "
                "`pip install pytauargus[parquet]`") from exc
        records = []
        for r in self._rows:
            rec = {name: r[name] for name in self._exp}
            rec[self._response] = (None if r["status"] in _WITHHOLD
                                   else r[self._response])
            records.append(rec)
        pq.write_table(pa.Table.from_pylist(records), str(path))

    def __len__(self) -> int:
        return len(self._rows)

    def __repr__(self) -> str:
        return (f"TableResult(n_cells={self.n_cells}, "
                f"response={self._response!r}, "
                f"exp={self._exp!r}, is_freq={self._is_freq})")
