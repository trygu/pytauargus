"""Model Context Protocol server: the agent front-end for tau-ARGUS.

An LLM client drives the stateless engine over MCP, one tool call per
task (the D2.6 agent front-end, ADR 0001). This is the first slice, the
core loop: version, explore, build_arb, run_arb, protect, audit. The
``.arb`` file is the state carrier of the batch flow; the server keeps
no session.

This package is a separate distribution that depends on pytauargus; it
is the only place that imports ``mcp``. Start the server with the
``tauargus-mcp`` console script, which runs it on the stdio transport.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from mcp.server.fastmcp import FastMCP

from pytauargus import __version__


# ===========================================================================
# Core tool logic: plain functions, directly callable without MCP
# ===========================================================================

def version_info() -> Dict[str, str]:
    """The version strings of the Python package and the native engine."""
    from pytauargus.engine import Engine
    return {"pytauargus": __version__, "engine": Engine().version}


def _parquet_column_type(values: List[Any]) -> str:
    for v in values:
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return "categorical"
    return "numeric"


def explore_dataset(microdata: str) -> Dict[str, Any]:
    """Inspect a dataset and return its variables with their types.

    ``.parquet`` files are read with :func:`to_microdata` (column types
    inferred from the values); ``.asc``/``.rda`` files are read through
    the engine metadata parser, so variable types are the engine ones
    (CATEGORICAL, RESPONSE, WEIGHT, ...).
    """
    path = Path(microdata)
    if not path.is_file():
        raise FileNotFoundError(f"microdata file not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        from pytauargus.dataframe import to_microdata
        micro = to_microdata(str(path))
        variables = [{"name": name, "type": _parquet_column_type(vals)}
                     for name, vals in micro.items()]
        n_rows = len(next(iter(micro.values()))) if micro else 0
        return {"path": str(path), "format": "parquet", "n_rows": n_rows,
                "variables": variables}
    if suffix == ".asc":
        rda = path.with_name(path.name[:-4] + ".rda")
    elif suffix == ".rda":
        rda = path
    else:
        raise ValueError(
            f"unsupported microdata format {suffix!r} "
            "(expected .parquet, .asc or .rda)")
    if not rda.is_file():
        raise FileNotFoundError(f"metadata file not found: {rda}")
    from pytauargus.engine import parse_rda
    meta = parse_rda(str(rda))
    variables = [{"name": v.name, "type": v.type} for v in meta.variables]
    return {"path": str(path), "format": "rda", "rda": str(rda),
            "variables": variables}


def build_arb(asc_filename: str, rda_filename: str,
              explanatory_vars: List[str], response_var: str,
              safety_rules: str, suppress: str, output_names: List[str],
              output_type: str = "4", output_options: str = "",
              weighted: bool = False,
              shadow_var: Optional[List[str]] = None,
              cost_var: Optional[List[str]] = None,
              arb_filename: Optional[str] = None) -> Dict[str, Any]:
    """Write a ``.arb`` batch file (wraps :func:`pytauargus.arb.micro_arb`)."""
    from pytauargus.arb import micro_arb
    res = micro_arb(arb_filename=arb_filename,
                    asc_filename=asc_filename,
                    rda_filename=rda_filename,
                    explanatory_vars=explanatory_vars,
                    response_var=response_var,
                    shadow_var=shadow_var,
                    cost_var=cost_var,
                    safety_rules=safety_rules,
                    weighted=weighted,
                    suppress=suppress,
                    output_names=output_names,
                    output_type=output_type,
                    output_options=output_options)
    return {"arb": res["arb_filename"], "output_names": res["output_names"]}


def _write_table_paths(arb_path: Path) -> List[Dict[str, Any]]:
    """The ``<WRITETABLE>`` targets of a batch, resolved like the engine."""
    from pytauargus.batch import WriteTable, parse_batch
    out: List[Dict[str, Any]] = []
    for cmd in parse_batch(str(arb_path)):
        if isinstance(cmd, WriteTable):
            p = Path(cmd.file)
            if not p.is_absolute():
                p = arb_path.parent / p
            out.append({"table": cmd.tab_no, "path": str(p), "exists": False})
    return out


def run_arb(arb_path: str) -> Dict[str, Any]:
    """Run a ``.arb`` batch and report the table files it writes."""
    p = Path(arb_path)
    if not p.is_file():
        raise FileNotFoundError(f"batch file not found: {p}")
    tables = _write_table_paths(p)
    from pytauargus.engine import Engine
    Engine().run_batch(p)
    for entry in tables:
        entry["exists"] = Path(entry["path"]).is_file()
    return {"arb": str(p), "tables": tables}


def protect_tables(microdata: Union[str, Dict[str, List[Any]]],
                   tables: list, response: str,
                   safety_rules: str = "P(25,1)", suppress: str = "OPT(1)",
                   workdir: Optional[str] = None) -> Dict[str, Any]:
    """One-shot protection (wraps :func:`pytauargus.protect`).

    Returns per-table cell counts, status counts, the unsafe (``U``)
    and secondary (``M``) cells, and all produced file paths.
    """
    from pytauargus.protect import protect
    if isinstance(microdata, str):
        mp = Path(microdata)
        if not mp.is_file():
            raise FileNotFoundError(f"microdata file not found: {mp}")
    res = protect(microdata, tables, response,
                  safety_rules=safety_rules, suppress=suppress,
                  workdir=workdir)
    out_tables: List[Dict[str, Any]] = []
    for t in res.tables:
        rows = t.rows
        statuses = t.status()
        status_counts: Dict[str, int] = {}
        for s in statuses:
            status_counts[s] = status_counts.get(s, 0) + 1
        unsafe_cells: List[Dict[str, Any]] = []
        secondary_cells: List[Dict[str, Any]] = []
        for i, row in enumerate(rows):
            if statuses[i] in ("U", "M"):
                cell = {"cell": i,
                        "value": row[t.response],
                        "status": statuses[i],
                        "dims": {dim: row[dim] for dim in t._exp}}
                if statuses[i] == "U":
                    unsafe_cells.append(cell)
                else:
                    secondary_cells.append(cell)
        out_tables.append({
            "n_cells": t.n_cells,
            "status_counts": status_counts,
            "unsafe_cells": unsafe_cells,
            "secondary_cells": secondary_cells,
            "columns": t.columns,
            "path": str(t.path) if t.path is not None else None,
        })
    return {"workdir": res.workdir, "files": dict(res.files),
            "n_tables": len(res.tables), "tables": out_tables}


def audit_table(arb_path: str, tab: int = 0) -> Dict[str, Any]:
    """Audit a suppressed table of a batch (wraps :meth:`Engine.audit`).

    Runs the batch to reconstruct the table state, then computes the
    realized feasibility intervals of the suppressed cells. Returns the
    ``{cell, min, max, value, status, unsafe}`` rows in ascending
    cell-index order (empty when the table has no suppressed cells).
    """
    p = Path(arb_path)
    if not p.is_file():
        raise FileNotFoundError(f"batch file not found: {p}")
    from pytauargus.engine import run_batch
    eng = run_batch(p)
    rows = eng.audit(tab)
    return {"arb": str(p), "tab": tab, "n_cells": len(rows),
            "cells": [dict(r) for r in rows]}


# ===========================================================================
# MCP registration: thin wrappers, the core logic above does the work
# ===========================================================================

mcp = FastMCP("tauargus")


@mcp.tool()
def tauargus_version() -> Dict[str, str]:
    """Report the pytauargus package version and the native engine version.

    Returns a dict with the keys ``pytauargus`` and ``engine``.
    """
    return version_info()


@mcp.tool()
def tauargus_explore(microdata: str) -> Dict[str, Any]:
    """Inspect a microdata dataset and return its variables with types.

    ``microdata`` is a path to a ``.parquet`` file, or to the engine
    ``.asc`` fixed-width file (its sibling ``.rda`` metadata is used),
    or directly to a ``.rda`` metadata file. Returns the variable names
    with their types, and the row count for ``.parquet``.
    """
    return explore_dataset(microdata)


@mcp.tool()
def tauargus_build_arb(asc_filename: str, rda_filename: str,
                       explanatory_vars: List[str], response_var: str,
                       safety_rules: str, suppress: str,
                       output_names: List[str],
                       output_type: str = "4", output_options: str = "",
                       weighted: bool = False,
                       shadow_var: Optional[List[str]] = None,
                       cost_var: Optional[List[str]] = None,
                       arb_filename: Optional[str] = None) -> Dict[str, Any]:
    """Write a ``.arb`` batch file for microdata (wraps ``micro_arb``).

    ``explanatory_vars`` is the flat variable list of one table,
    ``response_var`` its response, ``safety_rules`` a rule string such
    as ``"P(25,1)"``, ``suppress`` a suppression spec such as
    ``"OPT(1)"``, and ``output_names`` the target table files. Returns
    the written ``arb`` path and the output names.
    """
    return build_arb(asc_filename, rda_filename, explanatory_vars,
                     response_var, safety_rules, suppress, output_names,
                     output_type, output_options, weighted, shadow_var,
                     cost_var, arb_filename)


@mcp.tool()
def tauargus_run_arb(arb_path: str) -> Dict[str, Any]:
    """Run a ``.arb`` batch file and report the table files it writes.

    Returns the ``<WRITETABLE>`` targets resolved relative to the
    batch file, each with an ``exists`` flag after the run.
    """
    return run_arb(arb_path)


@mcp.tool()
def tauargus_protect(microdata: Union[str, Dict[str, List[Any]]],
                     tables: list, response: str,
                     safety_rules: str = "P(25,1)", suppress: str = "OPT(1)",
                     workdir: Optional[str] = None) -> Dict[str, Any]:
    """Protect one or more tables from microdata in a single call.

    ``microdata`` is a ``.parquet`` path or a dict of column to value
    lists. ``tables`` is a flat list of explanatory variables (one
    table) or a list of per-table lists. ``response`` names the numeric
    response column. Returns per-table cell counts, status counts, the
    unsafe and secondary cells, and all produced file paths.
    """
    return protect_tables(microdata, tables, response,
                          safety_rules=safety_rules, suppress=suppress,
                          workdir=workdir)


@mcp.tool()
def tauargus_audit(arb_path: str, tab: int = 0) -> Dict[str, Any]:
    """Audit the suppressed cells of a table computed by a batch.

    Runs ``arb_path`` to reconstruct the table state, then returns the
    realized feasibility intervals of the suppressed cells of table
    ``tab`` (0-based): one ``{cell, min, max, value, status, unsafe}``
    row per suppressed cell, empty when none are suppressed.
    """
    return audit_table(arb_path, tab)


def main() -> None:
    """Entry point of the ``tauargus-mcp`` console script (stdio)."""
    mcp.run()


if __name__ == "__main__":
    main()
