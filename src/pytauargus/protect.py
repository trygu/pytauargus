"""One-call protection pipeline: DataFrame in -> protected ``TableResult`` out.

This is the thin "job" that mirrors rtauargus' ``micro_rtauargus()`` (write
``.asc``/``.rda`` -> write ``.arb`` -> run the batch -> read the results back).
It deliberately stays a *function*, not a heavy ``Job`` object: the user passes
microdata + table specs and gets back one :class:`~pytauargus.result.TableResult`
per table.

The pipeline writes a *simple* (``SO+``) intermediate ``.tab`` per table so the
result can be parsed back into a :class:`~pytauargus.result.TableResult`
(``safe()``/``status()``/``unsafe()``/``dataframe()``).

Usage::

    from pytauargus import protect
    res = protect(df, tables=[["Region", "Size"]], response="Var2",
                  safety_rules="P(25)")
    res.tables[0].safe()
    res.tables[0].status()
    res.tables[0].dataframe()
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

from pytauargus.dataframe import to_microdata
from pytauargus.micro import micro_asc_rda
from pytauargus.arb import micro_arb
from pytauargus.result import TableResult, _validate_columns

__all__ = ["protect", "ProtectResult"]


@dataclass
class ProtectResult:
    """Outcome of a :func:`protect` run.

    * ``tables``  -- one :class:`TableResult` per specified table (empty when
      ``run=False``).
    * ``files``   -- paths of every file the pipeline wrote
      (``asc``/``rda``/``arb`` plus each ``.tab`` output).
    * ``engine``  -- the :class:`~pytauargus.engine.Engine` after the batch ran
      (``None`` when ``run=False``).
    * ``workdir`` -- the directory all files were written to.
    """

    tables: List["object"] = field(default_factory=list)
    files: Dict[str, str] = field(default_factory=dict)
    engine: Optional["object"] = None
    workdir: Optional[str] = None


def _normalise_tables(tables) -> List[List[str]]:
    """Normalise the ``tables`` argument to a per-table list of var names.

    A *flat* list of variable names is a single table (``["Region", "Size"]``);
    a list of per-table lists is multiple tables (``[["Region"], ["Size"]]``).
    """
    if tables is None:
        raise TypeError("tables is required (list of explanatory vars per table)")
    if all(isinstance(t, str) for t in tables):
        # a single table: flat list of var names
        return [list(tables)]
    # multiple tables: a sequence of per-table var lists
    out = []
    for t in tables:
        if isinstance(t, str):
            out.append([t])
        else:
            out.append([str(v) for v in t])
    return out


def protect(microdata,
            tables,
            response,
            safety_rules: Union[str, Sequence[str], None] = None,
            suppress: Union[str, Sequence[str]] = "OPT(1)",
            *,
            shadow_var: Optional[Union[str, Sequence[str]]] = None,
            cost_var: Optional[Union[str, Sequence[str]]] = None,
            weighted: Union[bool, Sequence[bool]] = False,
            weight_var: Optional[str] = None,
            holding_var: Optional[str] = None,
            decimals: Optional[int] = None,
            hrc: Optional[Dict[str, str]] = None,
            hierleadstring: Optional[str] = None,
            totcode: Optional[str] = None,
            missing: Optional[Dict[str, str]] = None,
            codelist: Optional[Dict[str, str]] = None,
            workdir: Optional[str] = None,
            run: bool = True) -> ProtectResult:
    """Protect one or more tables built from microdata, in a single call.

    Parameters
    ----------
    microdata:
        A DataFrame (pandas/polars), a ``dict`` of column -> list, or a list of
        row ``dict``. Converted with :func:`~pytauargus.dataframe.to_microdata`.
    tables:
        Explanatory variables per table. A single table may be passed as a flat
        list of variable names, e.g. ``["Region", "Size"]``; multiple tables as
        a list of lists, e.g. ``[["Region"], ["Size"]]``.
    response:
        The response variable name (a numeric column). Recycled across tables
        unless a per-table list is given. Required: the high-level API does not
        support the ``<freq>`` magic response.
        Explanatory/response names must be unique within each table and cannot
        use the result diagnostic names ``freq``, ``cost``, ``status``,
        ``lower`` or ``upper``. Rename colliding source columns first.
    safety_rules:
        Safety rule string (e.g. ``"P(25)"``) or per-table list. ``None`` -> no
        rules (``""`` -> "use given status").
    suppress:
        Suppression spec (e.g. ``"OPT(1)"``) or per-table list. A *single*
        string has its table number (first parenthesised argument) recalculated
        to 1, 2, 3... per table. Default ``"OPT(1)"`` (optimal suppression).
        Sequence length must be 1 or the table count; invalid lengths fail
        before generating any files.
    workdir:
        Directory for all intermediate files. Defaults to a fresh tempdir (kept,
        so the result ``.tab`` files stay readable; delete it when done).
        Relative paths use the caller's working directory; returned paths are absolute.
    run:
        If ``False``, write the ``.asc``/``.rda``/``.arb`` only and return
        without running the engine (``tables``/``engine`` are empty/``None``).
        Useful to inspect the generated files without invoking the native
        solver.

    Returns a :class:`ProtectResult`.
    """
    if response is None:
        raise TypeError("response is required (a numeric variable name)")

    micro = to_microdata(microdata)
    tab_specs = _normalise_tables(tables)
    n = len(tab_specs)
    resp_per_tab = _recycle(response, n)
    for explanatory, response_name in zip(tab_specs, resp_per_tab):
        _validate_columns(explanatory, response_name)
    # Preserve the generator's single-spec table-number rewriting semantics.
    suppr = "OPT(1)" if suppress is None else suppress
    if isinstance(suppr, (list, tuple)) and len(suppr) not in (1, n):
        raise ValueError("suppress sequence length must be 1 or the number of tables")

    if workdir is None:
        workdir = tempfile.mkdtemp(prefix="pytauargus_")
    # Batch output paths are resolved relative to the .arb directory. Use
    # absolute paths throughout so a relative workdir is not prefixed twice.
    wd = Path(workdir).resolve()
    wd.mkdir(parents=True, exist_ok=True)

    asc = str(wd / "micro.asc")
    rda = str(wd / "micro.rda")
    arb = str(wd / "batch.arb")

    micro_asc_rda(micro,
                  asc_filename=asc,
                  rda_filename=rda,
                  weight_var=weight_var,
                  holding_var=holding_var,
                  decimals=decimals,
                  hrc=hrc,
                  hierleadstring=hierleadstring,
                  totcode=totcode,
                  missing=missing,
                  codelist=codelist)

    # tabular output names (simple intermediate .tab)
    output_names = [str(wd / f"tab{i + 1}.tab") for i in range(n)]
    # safety_rules: None -> "" (empty rule = use given status); a single string
    # is recycled across tables by micro_arb.
    sr = "" if safety_rules is None else safety_rules
    micro_arb(arb_filename=arb,
              asc_filename=asc,
              rda_filename=rda,
              explanatory_vars=tab_specs,
              response_var=response,
              shadow_var=shadow_var,
              cost_var=cost_var,
              safety_rules=sr,
              weighted=weighted,
              suppress=suppr,
              output_names=output_names,
              output_type="5",
              output_options="SO+")

    out_files: Dict[str, str] = {"asc": asc, "rda": rda, "arb": arb}
    for i, p in enumerate(output_names):
        out_files[f"tab{i + 1}"] = p

    result = ProtectResult(files=out_files, workdir=str(wd))
    if not run:
        return result

    # Run the batch in-process. The generated .arb suppresses + writes the
    # .tab files; the batch itself performs the protection.
    from pytauargus.engine import run_batch
    eng = run_batch(arb)
    result.engine = eng

    for i in range(n):
        rt = TableResult(path=output_names[i],
                         exp_vars=tab_specs[i],
                         response=resp_per_tab[i],
                         is_freq=False)
        result.tables.append(rt)
    return result


def _recycle(x, n) -> list:
    """Recycle a scalar/sequence across ``n`` tables (R semantics)."""
    if isinstance(x, (list, tuple)):
        if len(x) == 1:
            return list(x) * n
        if len(x) == n:
            return list(x)
        raise ValueError("per-table argument length must match the number of tables")
    return [x] * n
