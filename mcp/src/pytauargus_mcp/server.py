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

import re
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


# Result diagnostic names reserved by the engine (see
# ``pytauargus.result._validate_columns``); a source column must not use one.
_TABLE_RESERVED = ("cost", "freq", "lower", "status", "upper")


def _validate_table_spec(microdata, tables, response) -> List[List[str]]:
    """Validate ``tables`` and ``response`` before any engine work.

    Returns the per-table variable names (the canonical list-of-lists
    shape). An MCP client gets a clear, self-correcting error naming the
    offending variable and the available columns, instead of an obscure
    failure deep in the native engine. Accepts the same shapes as
    :func:`pytauargus.protect.protect`: a flat list of variable names
    (one table) or a list of per-table lists (several tables).
    """
    from pytauargus.dataframe import to_microdata

    if isinstance(microdata, str):
        mp = Path(microdata)
        if not mp.is_file():
            raise FileNotFoundError(f"microdata file not found: {mp}")
        if mp.suffix.lower() != ".parquet":
            raise ValueError(
                f"unsupported microdata format {mp.suffix!r} "
                "(expected a .parquet path or a dict of column to list)")
        columns = list(to_microdata(str(mp)))
    else:
        columns = list(to_microdata(microdata))

    if not isinstance(tables, (list, tuple)):
        raise TypeError(
            "tables must be a list: a flat list of variable names for one "
            'table (e.g. ["Region", "Size"]) or a list of per-table lists '
            'for several tables (e.g. [["Region"], ["Size"]]); '
            f"got {type(tables).__name__}")
    if not tables:
        raise ValueError(
            "tables must name at least one explanatory variable "
            "(a flat list of names, or a list of per-table lists)")
    if all(isinstance(t, str) for t in tables):
        specs = [list(tables)]
    else:
        specs = []
        for t in tables:
            if isinstance(t, str):
                specs.append([t])
            elif isinstance(t, (list, tuple)) and t and all(
                    isinstance(v, str) for v in t):
                specs.append(list(t))
            else:
                raise TypeError(
                    "each table must be a non-empty list of variable "
                    f"names; got {t!r}")
    if any(not spec for spec in specs):
        raise ValueError("every table must name at least one variable")

    names = [v for spec in specs for v in spec]
    bad = sorted({v for v in names if v not in columns})
    if bad:
        raise ValueError(
            f"unknown table variable(s) {', '.join(repr(v) for v in bad)}; "
            f"available columns: {', '.join(columns)}")
    reserved = sorted({v for v in names if v in _TABLE_RESERVED})
    if reserved:
        raise ValueError(
            f"table variable(s) {', '.join(reserved)} use a reserved "
            "diagnostic name "
            f"({', '.join(_TABLE_RESERVED)}); rename the source column")
    if not isinstance(response, str) or response not in columns:
        raise ValueError(
            f"unknown response variable {response!r}; "
            f"available columns: {', '.join(columns)}")
    if response in _TABLE_RESERVED:
        raise ValueError(
            f"response {response!r} uses a reserved diagnostic name; "
            "rename the source column")
    if any(response in spec for spec in specs):
        raise ValueError(
            f"the response {response!r} cannot also be a table variable")
    return specs


def protect_tables(microdata: Union[str, Dict[str, List[Any]]],
                   tables: list, response: str,
                   safety_rules: str = "P(25,1)", suppress: str = "OPT(1)",
                   workdir: Optional[str] = None) -> Dict[str, Any]:
    """One-shot protection (wraps :func:`pytauargus.protect`).

    ``tables`` is a flat list of explanatory variable names (one table)
    or a list of per-table lists (several tables); ``response`` is a
    numeric column. Variable and response names are validated against
    the microdata columns before the engine runs. Returns per-table cell
    counts, status counts, the unsafe (``U``) and secondary (``M``)
    cells, and all produced file paths.
    """
    from pytauargus.protect import protect
    specs = _validate_table_spec(microdata, tables, response)
    res = protect(microdata, specs, response,
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


def assess_table(microdata: Union[str, Dict[str, List[Any]]],
                 tables: list, response: str,
                 safety_rules: str = "P(25,1)",
                 workdir: Optional[str] = None) -> Dict[str, Any]:
    """Run the safety rules without suppressing: what is sensitive, and why.

    Builds the same ``<SPECIFYTABLE>`` + ``<SAFETYRULE>`` blocks as
    :func:`protect_tables` but no ``<SUPPRESS>`` step, so the engine
    classifies every cell and withholds nothing. Returns, per table, the
    cells the rules flag (``U`` primary sensitive, ``E`` empty) with their
    value and contributor count (``freq``), the applied rules explained in
    plain words (see :func:`rule_meanings`), and the status legend. The
    generated batch (``files["arb"]``) is kept for inspection.
    """
    import tempfile

    from pytauargus.dataframe import to_microdata
    from pytauargus.micro import micro_asc_rda
    from pytauargus.arb import specif_safety
    from pytauargus.engine import run_batch
    from pytauargus.result import TableResult

    tab_specs = _validate_table_spec(microdata, tables, response)
    micro = to_microdata(microdata)
    n = len(tab_specs)

    if workdir is None:
        workdir = tempfile.mkdtemp(prefix="tauargus_assess_")
    wd = Path(workdir).resolve()
    wd.mkdir(parents=True, exist_ok=True)
    asc = str(wd / "micro.asc")
    rda = str(wd / "micro.rda")
    arb = str(wd / "assess.arb")
    micro_asc_rda(micro, asc_filename=asc, rda_filename=rda)
    output_names = [str(wd / f"tab{i + 1}.tab") for i in range(n)]

    sr = "" if safety_rules is None else safety_rules
    rules = [sr] if isinstance(sr, str) else list(sr)

    lines = ['<OPENMICRODATA> "%s"' % asc,
             '<OPENMETADATA> "%s"' % rda]
    lines += specif_safety(tab_specs, [response], None, None, rules, [False])
    lines.append("<READMICRODATA>")
    for i, out in enumerate(output_names, 1):
        lines.append('<WRITETABLE> (%d,5,SO+,"%s")' % (i, out))
    Path(arb).write_text("\n".join(lines) + "\n")

    eng = run_batch(arb)

    files = {"asc": asc, "rda": rda, "arb": arb}
    out_tables: List[Dict[str, Any]] = []
    for i, out in enumerate(output_names):
        files[f"tab{i + 1}"] = out
        t = TableResult(path=out, exp_vars=tab_specs[i],
                        response=response, is_freq=False,
                        statuses=eng.table_cell_statuses(i))
        rows = t.rows
        statuses = t.status()
        status_counts: Dict[str, int] = {}
        for s in statuses:
            status_counts[s] = status_counts.get(s, 0) + 1
        sensitive: List[Dict[str, Any]] = []
        for idx, row in enumerate(rows):
            if statuses[idx] in ("U", "E"):
                sensitive.append({
                    "cell": idx,
                    "dims": {dim: row[dim] for dim in t._exp},
                    "value": row[response],
                    "freq": row["freq"],
                    "status": statuses[idx],
                    "reason": t.reason()[idx],
                })
        out_tables.append({
            "n_cells": t.n_cells,
            "status_counts": status_counts,
            "sensitive_cells": sensitive,
            "columns": t.columns,
            "path": out,
        })
    return {"workdir": str(wd), "files": files,
            "n_tables": n, "tables": out_tables,
            "rules": rule_meanings(rules),
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


def rule_meanings(safety_rules: Union[str, List[str]]) -> List[Dict[str, str]]:
    """Plain words for the safety rules (from the 4.1 manual).

    ``safety_rules`` is one rule string or a per-table list; a rule string
    may join several rules with ``|`` (e.g. ``"P(25,1)|FREQ(3,20)"``).
    Each distinct token comes back as ``{"rule", "meaning"}``; a token
    without a known meaning says so instead of guessing.
    """
    per_table = [safety_rules] if isinstance(safety_rules, str) else list(safety_rules)
    out: List[Dict[str, str]] = []
    seen = set()
    for rules in per_table:
        for token in (r.strip() for r in rules.split("|")):
            if token and token not in seen:
                seen.add(token)
                out.append({"rule": token,
                            "meaning": _rule_meaning(token)})
    return out


def _rule_meaning(token: str) -> str:
    """Plain words for one rule token, grounded in the 4.1 manual."""
    m = re.match(r"^([A-Za-z]+)\s*\((.*)\)$", token)
    if not m:
        return "no plain description; see the 4.1 manual"
    name = m.group(1).upper()
    args = [a.strip() for a in m.group(2).split(",")]

    def arg(i: int) -> str:
        return args[i] if i < len(args) else "?"

    if name == "P":
        try:
            n_in = int(arg(1))
        except ValueError:
            n_in = 1
        return ("p%% dominance rule: a cell is sensitive when the largest "
                "contribution dominates it. The cell passes only if the "
                "total of all but the %d largest contributors is at least "
                "%s%% of the largest contribution (n = %d intruder%s in "
                "the coalition)." % (n_in + 1, arg(0), n_in,
                                     "" if n_in == 1 else "s"))
    if name == "NK":
        return ("dominance rule: a cell is sensitive when the %s largest "
                "contributors together account for more than %s%% of the "
                "cell total." % (arg(0), arg(1)))
    if name == "FREQ":
        return ("minimum frequency: a cell is sensitive when fewer than %s "
                "respondents contribute to it." % arg(0))
    if name == "ZERO":
        return "zero rule: cells whose value is zero are sensitive."
    if name == "REQ":
        return ("request rule: a cell is sensitive when a respondent who "
                "asked to be protected holds one of the configured shares "
                "(%s%% or %s%%) of it." % (arg(0), arg(1)))
    if name == "MAN":
        return ("manual rule: cells marked unsafe in the input data are "
                "protected with a %s%% safety margin." % arg(0))
    if name == "WGT":
        if arg(0) == "1":
            return ("weighting: contributions are summed with the weight "
                    "variable.")
        return "weighting: contributions are summed as recorded (no weights)."
    if name == "MIS":
        if arg(0) == "1":
            return ("missing values: a cell is treated as safe when its "
                    "spanning code is a declared missing value.")
        return "missing values: the ordinary rules apply to cells with " \
               "missing codes."
    return "no plain description in the 4.1 manual; check the batch reference."


# The protection methods of the 4.1 manual, with their trade-offs. ``applies``
# and the recommendation are computed per table structure in
# :func:`method_advice`; the pros/cons/use are the fixed reference text.
_METHODS = [
    {"method": "Global recoding",
     "available": True,
     "pros": ["Reduces sensitivity by design before any suppression",
              "Can cut the number of cells that must be suppressed"],
     "cons": ["Loses classification detail",
              "Not available for ready-made (pre-tabulated) tables"],
     "use": "A first move when the table is more detailed than the release "
            "needs."},
    {"method": "Optimal",
     "available": True,
     "pros": ["Globally optimal: affects the fewest cells and least "
              "information",
              "Models the whole table at once"],
     "cons": ["Can take substantial time on large tables",
              "Limited to about four dimensions"],
     "use": "The default for most microdata tables of up to four dimensions."},
    {"method": "Modular / HiTaS",
     "available": True,
     "pros": ["Fast: processes a hierarchy level by level",
              "Handles hierarchies top-down"],
     "cons": ["A sub-table optimum is not the global optimum",
              "A full-table audit can still find narrow intervals"],
     "use": "A faster alternative for hierarchical tables."},
    {"method": "Hypercube / GHMITER",
     "available": True,
     "pros": ["The documented route for 5- and 6-dimensional tables",
              "Supports linked table sets"],
     "cons": ["Heuristic; can over-suppress",
              "Ignores the user's cost variable",
              "This build covers flat tables only: hierarchical, linked and "
              "weighted tables are not yet supported"],
     "use": "Required when a flat table has five or six dimensions."},
    {"method": "Network",
     "available": False,
     "pros": ["Fast network-flow heuristic",
              "No commercial solver needed"],
     "cons": ["Two dimensions only, with one hierarchy in the first variable",
              "Gives a feasible, not necessarily optimal, pattern"],
     "use": "A fast option for a two-way table with a single hierarchy."},
    {"method": "Controlled tabular adjustment (CTA)",
     "available": False,
     "pros": ["Adjusts values instead of suppressing, so more cells are "
              "published",
              "Preserves additive relations"],
     "cons": ["Publishes values that differ from the true ones",
              "Protection means moving a value, not a suppression interval"],
     "use": "When you prefer complete, adjusted tables to suppressed ones."},
    {"method": "Controlled rounding",
     "available": True,
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


def _available(name: str) -> bool:
    return next(m for m in _METHODS if m["method"] == name)["available"]


def _recommend(n_dimensions: int, frequency: bool) -> Optional[str]:
    """The preferred method for the structure, if this build implements it.

    Returns ``None`` when the preferred method is not available, so the
    advice never points at a method the engine cannot run.
    """
    if n_dimensions >= 5:
        preferred = "Hypercube / GHMITER"
    elif frequency:
        preferred = "Controlled rounding"
    else:
        preferred = "Optimal"
    if _available(preferred) and _method_applies(preferred, n_dimensions, False):
        return preferred
    return None


def method_advice(n_dimensions: int,
                  hierarchical: bool = False,
                  frequency: bool = False) -> Dict[str, Any]:
    """Which protection methods fit a table, with their trade-offs.

    ``n_dimensions`` is the number of explanatory (dimension) variables in
    the table, 1 to 6. ``hierarchical`` marks a table with a hierarchy
    dimension. ``frequency`` marks a count table (the response is a
    frequency). Returns each method with ``applies`` (fits the structure),
    ``available`` (implemented by this build), ``pros``/``cons``/``use``; the
    list of ``available`` method names; and the ``recommended`` method for
    this structure (``None`` with a ``recommended_note`` when no available
    method fits).
    """
    if isinstance(n_dimensions, bool) or not isinstance(n_dimensions, int):
        raise TypeError("n_dimensions must be an int")
    if not 1 <= n_dimensions <= 6:
        raise ValueError("n_dimensions must be between 1 and 6")
    methods = [{
        "method": m["method"],
        "applies": _method_applies(m["method"], n_dimensions, hierarchical),
        "available": m["available"],
        "pros": list(m["pros"]),
        "cons": list(m["cons"]),
        "use": m["use"],
    } for m in _METHODS]
    recommended = _recommend(n_dimensions, frequency)
    note = ("No protection method is available for this table in this "
            "build."
            if recommended is None else "")
    return {
        "n_dimensions": n_dimensions,
        "hierarchical": hierarchical,
        "frequency": frequency,
        "recommended": recommended,
        "recommended_note": note,
        "available": [m["method"] for m in _METHODS if m["available"]],
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
   methods apply, which are available in this build, their pros and cons, and
   the recommendation. Read tauargus://doc/protection-methods for the full
   discussion. If the recommended route is not available in this build, say
   so in plain words and offer what is available; never promise a method the
   engine cannot run. Explain the trade-off and confirm the choice.
4. tauargus_assess (the same table and safety rules, but no suppression) to
   see exactly which cells the rules flag and with what values and
   contributor counts. Use the plain rule meanings in the result to explain
   in words *why* each sensitive cell is sensitive, before anything is
   withheld.
5. tauargus_protect to build, suppress and write the tables. The
   suppression solve is one blocking call with no progress in between:
   announce it before starting, and warn when the table is large or
   high-dimensional (several hundred cells, three or more dimensions)
   that it can take a long time. Step 4 is the quick preview: assess
   runs without the solver and returns fast, so show the statistician
   what would be flagged before the long call.
6. tauargus_audit to verify the suppressed cells keep their values from being
   reconstructed.
7. Report the outcome in plain words using the status legend below. If the
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

## Availability in this build

Not every method above is implemented by the current engine. Available now:
**Optimal**, **Modular / HiTaS**, **Controlled rounding**, **Global
recoding** and **Hypercube / GHMITER** for **flat** tables (its
trade-offs list the flat-table scope). **Network** and **CTA** are
documented in the manual but not yet implemented in this build;
`tauargus_method_advice` marks unavailable methods ``available: false``
and never recommends them. The "Choosing" list below is the manual's
guidance; check ``available`` before offering a method.

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
    response column. The variable and response names are validated
    against the microdata columns before the engine runs, with the
    available columns named in the error. Returns per-table cell
    counts, status counts, the unsafe and secondary cells, and all
    produced file paths.
    """
    return protect_tables(microdata, tables, response,
                          safety_rules=safety_rules, suppress=suppress,
                          workdir=workdir)


@mcp.tool()
def tauargus_assess(microdata: Union[str, Dict[str, List[Any]]],
                    tables: list, response: str,
                    safety_rules: str = "P(25,1)",
                    workdir: Optional[str] = None) -> Dict[str, Any]:
    """Run the safety rules without suppressing: what is sensitive, and why.

    Same inputs as tauargus_protect except the suppression spec; the
    variable and response names are validated the same way. Returns,
    per table, the cells the rules flag (``U`` sensitive, ``E`` empty)
    with their value, contributor count and the plain-language reason
    each is sensitive (safety rule, frequency, singleton, zero,
    manual, ...), the applied rules explained in plain words, and the
    status legend. Nothing is withheld, so the statistician sees
    exactly what a protection would act on before it happens.
    """
    return assess_table(microdata, tables, response,
                        safety_rules=safety_rules, workdir=workdir)


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
    with ``applies`` (fits the structure), ``available`` (implemented by this
    build), ``pros``/``cons``/``use``, and the ``recommended`` method for
    this structure (``None`` with a ``recommended_note`` when no available
    method fits). Read ``tauargus://doc/protection-methods`` for the full
    discussion.
    """
    return method_advice(n_dimensions, hierarchical, frequency)


def main() -> None:
    """Entry point of the ``tauargus-mcp`` console script (stdio)."""
    mcp.run()


if __name__ == "__main__":
    main()
