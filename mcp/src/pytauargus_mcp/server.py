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
            "n_tables": len(res.tables), "tables": out_tables,
            "status_legend": status_legend()}


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
# Consultant layer: the context an agent needs to advise a non-expert
# ===========================================================================

# Plain words for the numeric cell status codes the engine reports. The
# agent uses these to explain *what* a cell is, in words a statistician
# understands, rather than raw single letters.
_STATUS_LEGEND = {
    "S": "safe: published as is",
    "U": "unsafe (primary): the value is sensitive and is withheld",
    "M": "secondary: withheld so a primary cell cannot be reconstructed",
    "P": "protected: a publication constraint; not selected for suppression",
    "E": "empty: no contributing records",
}


def status_legend() -> Dict[str, str]:
    """Plain words for the numeric cell status codes (S, U, M, P, E)."""
    return dict(_STATUS_LEGEND)


# The protection methods of the 4.1 manual, with their trade-offs. ``applies``
# and the recommendation are computed per table structure in
# :func:`method_advice`; the pros/cons/use are the fixed reference text.
_METHODS = [
    {"method": "Global recoding",
     "pros": ["Reduces sensitivity by design before any suppression",
              "Can cut the number of cells that must be suppressed"],
     "cons": ["Loses classification detail",
              "Not available for ready-made (pre-tabulated) tables"],
     "use": "A first move when the table is more detailed than the release "
            "needs."},
    {"method": "Optimal",
     "pros": ["Globally optimal: affects the fewest cells and least "
              "information",
              "Models the whole table at once"],
     "cons": ["Can take substantial time on large tables",
              "Limited to about four dimensions"],
     "use": "The default for most microdata tables of up to four dimensions."},
    {"method": "Modular / HiTaS",
     "pros": ["Fast: processes a hierarchy level by level",
              "Handles hierarchies top-down"],
     "cons": ["A sub-table optimum is not the global optimum",
              "A full-table audit can still find narrow intervals"],
     "use": "A faster alternative for hierarchical tables."},
    {"method": "Hypercube / GHMITER",
     "pros": ["The documented route for 5- and 6-dimensional tables",
              "Supports linked table sets"],
     "cons": ["Heuristic; can over-suppress",
              "Ignores the user's cost variable"],
     "use": "Required when a table has five or six dimensions."},
    {"method": "Network",
     "pros": ["Fast network-flow heuristic",
              "No commercial solver needed"],
     "cons": ["Two dimensions only, with one hierarchy in the first variable",
              "Gives a feasible, not necessarily optimal, pattern"],
     "use": "A fast option for a two-way table with a single hierarchy."},
    {"method": "Controlled tabular adjustment (CTA)",
     "pros": ["Adjusts values instead of suppressing, so more cells are "
              "published",
              "Preserves additive relations"],
     "cons": ["Publishes values that differ from the true ones",
              "Protection means moving a value, not a suppression interval"],
     "use": "When you prefer complete, adjusted tables to suppressed ones."},
    {"method": "Controlled rounding",
     "pros": ["Particularly suited to frequency (count) tables",
              "Keeps totals and subtotals adding up"],
     "cons": ["Values become multiples of a base",
              "Base, steps and stopping rule affect distortion"],
     "use": "The natural choice for count or frequency tables."},
]


def _method_applies(name: str, n_dimensions: int, hierarchical: bool) -> bool:
    if name in ("Optimal", "Modular / HiTaS"):
        return n_dimensions <= 4
    if name == "Network":
        return n_dimensions == 2 and hierarchical
    # Global recoding, Hypercube, CTA and Controlled rounding are always
    # available as a method choice for the documented structures.
    return True


def _recommend(n_dimensions: int, frequency: bool) -> str:
    if n_dimensions >= 5:
        return "Hypercube / GHMITER"
    if frequency:
        return "Controlled rounding"
    return "Optimal"


def method_advice(n_dimensions: int,
                  hierarchical: bool = False,
                  frequency: bool = False) -> Dict[str, Any]:
    """Which protection methods fit a table, with their trade-offs.

    ``n_dimensions`` is the number of explanatory (dimension) variables in
    the table, 1 to 6. ``hierarchical`` marks a table with a hierarchy
    dimension. ``frequency`` marks a count table (the response is a
    frequency). Returns each method with ``applies``/``pros``/``cons``/
    ``use`` and the ``recommended`` method for this structure.
    """
    if isinstance(n_dimensions, bool) or not isinstance(n_dimensions, int):
        raise TypeError("n_dimensions must be an int")
    if not 1 <= n_dimensions <= 6:
        raise ValueError("n_dimensions must be between 1 and 6")
    methods = [{
        "method": m["method"],
        "applies": _method_applies(m["method"], n_dimensions, hierarchical),
        "pros": list(m["pros"]),
        "cons": list(m["cons"]),
        "use": m["use"],
    } for m in _METHODS]
    return {
        "n_dimensions": n_dimensions,
        "hierarchical": hierarchical,
        "frequency": frequency,
        "recommended": _recommend(n_dimensions, frequency),
        "methods": methods,
    }


# Agent-facing context sent at MCP initialization: the consultant workflow,
# the questions to ask before protecting, the status legend, the rule-string
# grammar, and the cite-the-manual rule.
INSTRUCTIONS = """\
tau-ARGUS statistical disclosure control (SDC) for tabular data.

You are an expert SDC consultant for a statistician who wants to publish a
table from microdata safely. The engine is stateless: each tool call carries
its inputs and returns a full result. The `.arb` batch file is the state
carrier of the batch flow.

Workflow (consult the statistician as you go):
1. tauargus_explore the microdata to learn its variables and types.
2. Ask the questions you need before protecting:
   - which table(s) to publish: the explanatory (dimension) variables and
     the numeric response variable;
   - why the data is sensitive: dominance (a few large contributors), small
     frequency, zero cells, or a respondent request;
   - which policy: use the standard policy unless the statistician names one.
3. tauargus_method_advice with the table's structure to see which protection
   methods apply, their pros and cons, and the recommendation. Read
   tauargus://doc/protection-methods for the full discussion. Explain the
   trade-off in plain words and confirm the choice.
4. tauargus_protect to build, suppress and write the tables.
5. tauargus_audit to verify the suppressed cells keep their values from being
   reconstructed.
6. Report the outcome in plain words using the status legend below. If the
   audit shows a value can still be reconstructed, hold and flag it rather
   than present a leaking table as safe.

Cell status codes (also returned as status_legend in protect results):
- S  safe: published as is
- U  unsafe (primary): sensitive, value withheld
- M  secondary: withheld so a primary cell cannot be reconstructed
- P  protected: a publication constraint; not selected for suppression
- E  empty: no contributing records

Rule-string grammar (expert path): P(p,n) p%-rule with n intruders (default
n=1); FREQ(min,range) minimum frequency; ZERO(range) zero cells; OPT(1)
optimal suppression; MOD(1) modular; NET(1) network. Cite the 4.1 manual for
thresholds; do not invent them.
"""

# The protection-methods reference the agent reads for the full trade-off
# discussion (from the 4.1 manual, "Choosing and understanding protection
# methods").
_PROTECTION_METHODS_DOC = """\
# tau-ARGUS protection methods

How to choose a protection method, from the 4.1 manual. Each method changes
the table in a different way; the right one depends on the table's structure
and on whether you would rather suppress cells or adjust values.

## Suppression methods (withhold cell values)

### Optimal
Models the complete table and its additive relations, then suppresses the
fewest cells needed. Globally optimal: minimal information loss. Limited to
about four dimensions and can take substantial time on large tables. This is
the default for most microdata tables.

### Modular / HiTaS
Processes a hierarchy level by level, protecting higher levels and fixing
their margins before the lower interior. Fast, but a sub-table optimum is not
the global optimum; a full-table audit can still find intervals that are too
narrow.

### Hypercube / GHMITER
Suppresses the corners of a hypercube around each primary to block exact
reconstruction. The documented route for 5- and 6-dimensional tables and for
linked sets. Heuristic: it can over-suppress and ignores the user's cost
variable.

### Network
A shortest-path / network-flow heuristic for a two-way table with one
hierarchy in the first variable. Fast and solver-free, but gives a feasible,
not necessarily optimal, pattern.

## Adjustment methods (change cell values, keep the table complete)

### Controlled tabular adjustment (CTA)
Moves each sensitive value at least its required distance in a chosen
direction and minimally adjusts the other values so the table still adds up.
More cells stay published, but the published values differ from the true
ones; protection means moving a value, not a suppression interval.

### Controlled rounding
Rounds every value to a multiple of a base while keeping totals and
subtotals adding up. Particularly suited to frequency (count) tables. The
base, permitted steps, partitioning and stopping rule all affect how much the
margins move.

## A design move, not a solver method

### Global recoding (table redesign)
Merge row, column or hierarchy categories before suppressing. Reduces
sensitivity by design and can cut the number of suppressions, at the cost of
classification detail. Not available for ready-made (pre-tabulated) tables.

## Choosing

- Up to four dimensions, value table: **Optimal**.
- Five or six dimensions: **Hypercube / GHMITER** (the only documented route).
- A two-way table with one hierarchy: **Network** is a fast option; Optimal
  still applies.
- A count or frequency table: **Controlled rounding**.
- When you would rather publish adjusted values than suppress: **CTA**.
- When the table is finer than the release needs: consider **Global
  recoding** first.

Whichever method you use, run the audit afterwards and inspect any interval
that is too narrow. The method report and the log record what was actually
established (feasible, optimal-proven, or time-limited).
"""


# ===========================================================================
# MCP registration: thin wrappers, the core logic above does the work
# ===========================================================================

mcp = FastMCP("tauargus", instructions=INSTRUCTIONS)


@mcp.resource("tauargus://doc/protection-methods",
              name="protection-methods",
              description="How to choose a tau-ARGUS protection method, from "
                          "the 4.1 manual.")
def protection_methods_doc() -> str:
    """The trade-offs of the protection methods, as markdown."""
    return _PROTECTION_METHODS_DOC


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


@mcp.tool()
def tauargus_method_advice(n_dimensions: int,
                           hierarchical: bool = False,
                           frequency: bool = False) -> Dict[str, Any]:
    """Advise which protection method fits a table, with its trade-offs.

    Call this before protecting, to explain the choice to the statistician.
    ``n_dimensions`` is the number of dimension variables in the table
    (1 to 6). ``hierarchical`` is true when a dimension is a hierarchy.
    ``frequency`` is true when the response is a count. Returns each method
    with ``applies``/``pros``/``cons``/``use`` and the ``recommended``
    method for this structure. Read ``tauargus://doc/protection-methods``
    for the full discussion.
    """
    return method_advice(n_dimensions, hierarchical, frequency)


def main() -> None:
    """Entry point of the ``tauargus-mcp`` console script (stdio)."""
    mcp.run()


if __name__ == "__main__":
    main()
