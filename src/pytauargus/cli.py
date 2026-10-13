"""Headless Tau-Argus command line interface.

This is the product entry point that replaces the legacy Java/Swing front-end.
It drives the native C++ engine (``pytauargus.engine.Engine``) from the command
line, so a ``.arb`` batch — or an ad-hoc table specification — can be run
end-to-end with no GUI.

Sub-commands
------------
run        Parse and execute a legacy ``.arb`` batch file.
explore    Load the data + metadata of a batch and list the variables.
specify    Specify tables/safety-rules ad hoc and compute them.
compute    Parse a batch and run the computation (up to ReadMicrodata).
suppress   Compute, then apply one suppression method to a table.
round      Compute, then round a table (RND).
audit      Compute, then audit: report realized feasibility intervals.
save       Compute, then write tables to file (CSV or cell records).
tables     Compute, then print a per-table summary.
protect    Protect microdata in one call: file in, protected table out.
version    Print the engine version and exit.

Example:
    tauargus run data/TestRecode.arb
    tauargus explore data/TestRecode.arb
    tauargus specify data/tau_testW.asc data/tau_testW.rda \\
        --table '"Size","Region"|Var2' --safety 'NK(2,75)' --save out.csv
    tauargus audit data/TestRecode.arb
    tauargus save data/TestRecode.arb --format csv --out tables.csv
    tauargus suppress data/TestRecode.arb --method MOD --tab 1
    tauargus protect micro.parquet --tables 'Region,Sector' \\
        --response Turnover --safety 'NK(2,75)' --suppress 'OPT(1)' \\
        --out protected.parquet
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from .batch import (
    BatchError,
    Apriory,
    Suppress,
    WriteTable,
    OpenMicrodata,
    OpenMetadata,
    _parse_safety_rule,
    _parse_specify_table,
    _parse_suppress,
    Tokenizer,
    parse_batch,
)
from .engine import IDENTITY_DIM_SEQUENCE, Engine, parse_rda, run_batch
from .protect import protect


# Cell status codes (native/core/src/defines.h + model/CellStatus.java).
_STATUS_NAMES = {
    1: "safe",
    2: "safe_manual",
    3: "unsafe_rule",
    4: "unsafe_peep",
    5: "unsafe_freq",
    6: "unsafe_zero",
    7: "unsafe_singleton",
    8: "unsafe_singleton_manual",
    9: "unsafe_manual",
    10: "protect_manual",
    11: "secondary_unsafe",
    12: "secondary_unsafe_manual",
    13: "empty_nonstructural",
    14: "empty",
}


# ===========================================================================
# Helpers
# ===========================================================================
def _tok(spec: str) -> Tokenizer:
    """Build a Tokenizer with ``line`` pre-loaded to ``spec``.

    In the batch flow the command keyword (``<SPECIFYTABLE>`` etc.) is consumed
    first, leaving the argument body in ``tok.line``. Reproduce that by
    advancing past the (single) line so ``next_token``/``next_field`` read the
    body, exactly as the in-file parser does.
    """
    tok = Tokenizer(spec)
    tok.next_line()
    return tok


def _parse_table_spec(spec: str):
    """Parse a SPECIFYTABLE body string into a :class:`SpecifyTable`."""
    return _parse_specify_table(_tok(spec))


def _parse_safety_rules(spec: str):
    """Parse a SAFETYRULE body string (e.g. ``NK(2,75)|P(25,100,1)``)."""
    return _parse_safety_rule(_tok(spec))


def _parse_suppress_spec(spec: str) -> Suppress:
    """Parse a SUPPRESS body string (e.g. ``MOD(1)``, ``RND(1,1000000)``)."""
    return _parse_suppress(_tok(spec))


def _resolve_relative_to(base: Path, name: str) -> Path:
    p = Path(name)
    if p.is_file():
        return p
    cand = base / name
    if cand.is_file():
        return cand
    return p


def _load_meta_for(arb: Path):
    """Load metadata for a microdata batch (OpenMicrodata + OpenMetadata)."""
    eng = Engine()
    eng._work_dir = arb.parent
    commands = parse_batch(arb)
    for cmd in commands:
        if isinstance(cmd, OpenMicrodata):
            eng._data_file = eng._resolve_arb_path(cmd.file)
        elif isinstance(cmd, OpenMetadata):
            meta_path = eng._resolve_arb_path(cmd.file)
            eng._metadata = parse_rda(meta_path)
            for i, v in enumerate(eng._metadata.variables):
                v.index = i
            break
    return eng


def _compute_from_arb(arb: Path) -> Engine:
    """Run a microdata batch to the ReadMicrodata step (tables computed)."""
    eng = run_batch(arb)
    # If the batch already passed ReadMicrodata the tables are computed; if it
    # stopped earlier (e.g. a <GOINTERACTIVE> only) there is nothing to do.
    return eng


def _default_out_name(eng: Engine, i: int, suffix: str) -> Path:
    stem = "table"
    if eng._metadata and eng._metadata.data_file:
        stem = Path(eng._metadata.data_file).stem
    return eng._work_dir / f"{stem}_table{i + 1}{suffix}"


def _default_csv_name(eng: Engine, i: int) -> Path:
    return _default_out_name(eng, i, ".csv")


# Remote URL forms pyarrow reads and writes natively (S3/GCS/HTTPS).
_URL_PREFIXES = ("s3://", "s3a://", "gs://", "http://", "https://", "file://")


def _is_url(name: str) -> bool:
    return name.lower().startswith(_URL_PREFIXES)


def _is_parquet_name(name: str) -> bool:
    return name.rsplit("/", 1)[-1].lower().endswith(".parquet")


def _per_table_out(out: str, i: int, n: int) -> str:
    """Per-table output name: index-suffixed when writing several tables."""
    if n == 1:
        return out
    if _is_url(out):
        base, dot, ext = out.rpartition(".")
        return f"{base}_{i + 1}.{ext}" if dot else out
    p = Path(out)
    return str(p.with_name(f"{p.stem}_{i + 1}{p.suffix}"))


def _default_protect_out(micro: str, i: int) -> str:
    """Default CSV output name in the current directory."""
    name = micro.rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return str(Path.cwd() / f"{stem}_table{i + 1}.csv")


# ===========================================================================
# Sub-command handlers (each returns an exit code)
# ===========================================================================
def cmd_run(args) -> int:
    arb = Path(args.batch)
    if not arb.is_file():
        print(f"error: batch file not found: {arb}", file=sys.stderr)
        return 2
    run_batch(arb)
    print(f"ok: ran {arb}")
    return 0


def cmd_explore(args) -> int:
    arb = Path(args.batch)
    eng = _load_meta_for(arb)
    meta = eng._metadata
    if meta is None:
        print("error: batch has no <OPENMETADATA>", file=sys.stderr)
        return 2
    print(f"variables: {len(meta.variables)}")
    for v in meta.variables:
        print(f"  {v.name:<16} {v.type:<22} {v.missing[0]!r}")
    return 0


def cmd_specify(args) -> int:
    data = Path(args.data)
    meta_file = Path(args.metadata)
    base = data.parent
    data = data if data.is_file() else _resolve_relative_to(base, args.data)
    meta_file = meta_file if meta_file.is_file() else _resolve_relative_to(base, args.metadata)

    eng = Engine()
    eng._work_dir = base
    eng._data_file = str(data)
    eng._metadata = parse_rda(meta_file)
    for i, v in enumerate(eng._metadata.variables):
        v.index = i

    for tspec in args.table:
        eng._tables.append((_parse_table_spec(tspec), None))
        eng._safety_rules_buf.append([])
    if args.safety:
        if not eng._safety_rules_buf:
            print("error: --safety given but no --table", file=sys.stderr)
            return 2
        eng._safety_rules_buf[-1].extend(_parse_safety_rules(args.safety))

    eng._apply_safety_rules()
    eng.open_microdata(str(data), eng._metadata)
    eng.read_microdata()

    if args.save:
        for i in range(eng._n_tables):
            eng.write_table(WriteTable(tab_no=i + 1, output_type=1,
                                       file=f"table_{i + 1}.csv"))
    return 0


def cmd_compute(args) -> int:
    arb = Path(args.batch)
    eng = run_batch(arb)
    for i in range(eng._n_tables):
        ncell, _ = eng._tau.get_total_table_size(i)
        print(f"table {i + 1}: {ncell} cells")
    return 0


def cmd_suppress(args) -> int:
    arb = Path(args.batch)
    eng = run_batch(arb)
    if args.tab < 1 or args.tab > eng._n_tables:
        print(f"error: table {args.tab} out of range (1..{eng._n_tables})",
              file=sys.stderr)
        return 2
    eng.suppress(Suppress(kind=args.method.upper(), tab_no=args.tab))
    print(f"ok: {args.method.upper()} applied to table {args.tab}")
    return 0


def cmd_round(args) -> int:
    arb = Path(args.batch)
    eng = run_batch(arb)
    if args.tab < 1 or args.tab > eng._n_tables:
        print(f"error: table {args.tab} out of range (1..{eng._n_tables})",
              file=sys.stderr)
        return 2
    tab = args.tab - 1
    base = args.base if args.base else eng._min_round_base(tab)
    eng.suppress(Suppress(kind="RND", tab_no=args.tab, rnd_base=base))
    print(f"ok: table {args.tab} rounded (base {base})")
    return 0


def cmd_apriori(args) -> int:
    arb = Path(args.batch)
    eng = run_batch(arb)
    if args.tab < 1 or args.tab > eng._n_tables:
        print(f"error: table {args.tab} out of range (1..{eng._n_tables})",
              file=sys.stderr)
        return 2
    ap = Apriory(file=args.file, tab_no=args.tab, separator=args.separator,
                 ignore_error=not args.strict, expand_bogus=args.expand_bogus)
    stats = eng.apply_apriori(ap)
    print(f"ok: apriori applied to table {args.tab} — "
          f"{stats['lines_read']} lines read, "
          f"{stats['status_ok']} status, {stats['cost_ok']} cost, "
          f"{stats['protlevel_ok']} prot-level changes")
    if stats["lines_error"] or stats["status_err"] or stats["cost_err"] \
            or stats["protlevel_err"]:
        print(f"warning: {stats['lines_error']} line / "
              f"{stats['status_err']} status / {stats['cost_err']} cost / "
              f"{stats['protlevel_err']} prot-level errors (ignored)",
              file=sys.stderr)
    return 0


def cmd_audit(args) -> int:
    arb = Path(args.batch)
    eng = run_batch(arb)
    for i in range(eng._n_tables):
        rows = eng.audit(i)
        if not rows:
            print(f"table {i + 1}: no suppressed cells to audit")
            continue
        n_unsafe = sum(1 for r in rows if r["unsafe"])
        print(f"table {i + 1}: {len(rows)} cells audited, "
              f"{n_unsafe} under-protected")
        for r in rows:
            print(f"  cell {r['cell']}: [{r['min']:g}, {r['max']:g}] "
                  f"value={r['value']:g} status={r['status']}"
                  + ("  UNSAFE" if r["unsafe"] else ""))
    return 0


def cmd_save(args) -> int:
    arb = Path(args.batch)
    eng = run_batch(arb)
    if args.audit:
        for i in range(eng._n_tables):
            eng.audit(i)
    out = Path(args.out) if args.out else None
    default_suffix = ".tab" if args.format == "intermediate" else ".csv"
    for i in range(eng._n_tables):
        if out is not None and eng._n_tables > 1:
            fpath = out.with_name(f"{out.stem}_{i + 1}{out.suffix}")
        elif out is not None:
            fpath = out
        else:  # default: <data_stem>_table<n>.{csv|tab} next to the data file
            fpath = _default_out_name(eng, i, default_suffix)
        if args.format == "csv":
            eng._tau.write_csv(i, str(fpath), True, IDENTITY_DIM_SEQUENCE, 1)
        elif args.format == "cell":
            eng._tau.write_cell_records(
                i, str(fpath), False, args.status, False, "", args.unsafe, True, 1)
        elif args.format == "intermediate":
            eng.write_intermediate_table(i, str(fpath), with_audit=args.audit)
        else:
            print(f"error: unknown format {args.format}", file=sys.stderr)
            return 2
        print(f"wrote table {i + 1} -> {fpath}")
    return 0


def cmd_tables(args) -> int:
    arb = Path(args.batch)
    eng = run_batch(arb)
    for i in range(eng._n_tables):
        ncell, _ = eng._tau.get_total_table_size(i)
        mn, mx = eng._tau.get_minimum_cell_value(i)
        spec, _ = eng._tables[i] if i < len(eng._tables) else (None, None)
        exp = ", ".join(spec.exp_vars) if spec else "?"
        resp = spec.resp_var if spec else "?"
        print(f"table {i + 1}: {exp} | {resp}  cells={ncell} min={mn} max={mx}")
    return 0


def cmd_protect(args) -> int:
    micro = args.microdata
    if not _is_url(micro) and not Path(micro).is_file():
        print(f"error: microdata file not found: {micro}", file=sys.stderr)
        return 2
    tab_specs = [[v.strip() for v in t.split(",") if v.strip()]
                 for t in args.tables]
    if any(not t for t in tab_specs):
        print("error: --tables must name at least one variable per table",
              file=sys.stderr)
        return 2

    result = protect(micro, tab_specs, response=args.response,
                     safety_rules=args.safety,
                     suppress=args.suppress,
                     workdir=args.workdir)

    n = len(result.tables)
    for i, rt in enumerate(result.tables):
        fpath = _per_table_out(args.out, i, n) if args.out \
            else _default_protect_out(micro, i)
        if _is_parquet_name(fpath):
            rt.to_parquet(fpath)
        else:
            result.engine._tau.write_csv(i, fpath, True,
                                         IDENTITY_DIM_SEQUENCE, 1)
        print(f"wrote table {i + 1} -> {fpath}")
    return 0


def cmd_version(args) -> int:
    from ._tauargus import TauArgus

    print(TauArgus().version())
    return 0


# ===========================================================================
# Argument parser
# ===========================================================================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tauargus",
        description="Headless Tau-Argus: SDC with open-source solvers (HiGHS).",
    )
    p.add_argument("-v", "--version", action="store_true",
                   help="print the engine version and exit")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("run", help="parse and execute a .arb batch file")
    sp.add_argument("batch")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("explore", help="load data + metadata and list variables")
    sp.add_argument("batch")
    sp.set_defaults(func=cmd_explore)

    sp = sub.add_parser("specify", help="specify tables and compute from microdata")
    sp.add_argument("data", help="microdata (.asc) file")
    sp.add_argument("metadata", help="metadata (.rda) file")
    sp.add_argument("--table", action="append", required=True,
                    help="SPECIFYTABLE body, e.g. '\"Size\",\"Region\"|Var2'")
    sp.add_argument("--safety",
                    help="SAFETYRULE body, e.g. 'NK(2,75)|P(25,100,1)'")
    sp.add_argument("--save", action="store_true",
                    help="write each computed table to a CSV next to the data")
    sp.set_defaults(func=cmd_specify)

    sp = sub.add_parser("compute", help="run a batch through table computation")
    sp.add_argument("batch")
    sp.set_defaults(func=cmd_compute)

    sp = sub.add_parser("suppress", help="compute, then apply a suppression method")
    sp.add_argument("batch")
    sp.add_argument("--method", required=True,
                    choices=["MOD", "OPT", "RND", "CKM"], help="suppression method")
    sp.add_argument("--tab", type=int, required=True, help="1-based table number")
    sp.set_defaults(func=cmd_suppress)

    sp = sub.add_parser("round", help="compute, then round a table (RND)")
    sp.add_argument("batch")
    sp.add_argument("--tab", type=int, required=True, help="1-based table number")
    sp.add_argument("--base", type=int,
                    help="rounding base (default: table minimum base)")
    sp.set_defaults(func=cmd_round)

    sp = sub.add_parser("apriori",
                        help="compute, then apply an apriori file to a table")
    sp.add_argument("batch")
    sp.add_argument("--file", required=True,
                    help="apriori file (paths relative to the .arb dir)")
    sp.add_argument("--tab", type=int, required=True,
                    help="1-based table number")
    sp.add_argument("--separator", default=";",
                    help="field separator in the apriori file (default ';')")
    sp.add_argument("--strict", action="store_true",
                    help="raise on the first error instead of ignoring it")
    sp.add_argument("--expand-bogus", action="store_true",
                    help="apply each change to the bogus range (single-child chain)")
    sp.set_defaults(func=cmd_apriori)

    sp = sub.add_parser("audit",
                        help="compute, then audit: realized feasibility "
                             "intervals of all suppressed cells")
    sp.add_argument("batch")
    sp.set_defaults(func=cmd_audit)

    sp = sub.add_parser("save", help="compute, then write tables to file")
    sp.add_argument("batch")
    sp.add_argument("--audit", action="store_true",
                    help="run the audit first; with --format intermediate, "
                         "append the realized-bounds (+AR) columns")
    sp.add_argument("--format", choices=["csv", "cell", "intermediate"], default="csv")
    sp.add_argument("--out", help="output file (one per table if multiple)")
    sp.add_argument("--status", action="store_true",
                    help="include the cell status column (cell format)")
    sp.add_argument("--unsafe", action="store_true",
                    help="include unsafe cells (cell format)")
    sp.set_defaults(func=cmd_save)

    sp = sub.add_parser("tables", help="compute, then print a table summary")
    sp.add_argument("batch")
    sp.set_defaults(func=cmd_tables)

    sp = sub.add_parser("protect", help="protect microdata in one call "
                                         "(file in, protected table out)")
    sp.add_argument("microdata",
                    help="microdata file (.parquet) or URL "
                         "(s3://, s3a://, gs://, https://)")
    sp.add_argument("--tables", action="append", required=True,
                    help="explanatory variables for one table, comma-separated; "
                         "repeat the flag for multiple tables")
    sp.add_argument("--response", required=True,
                    help="response variable name")
    sp.add_argument("--safety",
                    help="SAFETYRULE body, e.g. 'NK(1,80)' (default: none)")
    sp.add_argument("--suppress", default="OPT(1)",
                    help="SUPPRESS body (default 'OPT(1)')")
    sp.add_argument("--workdir",
                    help="directory for intermediate files (default: tempdir)")
    sp.add_argument("--out",
                    help="output file; a .parquet suffix writes a masked "
                         "Parquet, any other name writes the published CSV")
    sp.set_defaults(func=cmd_protect)

    sp = sub.add_parser("version", help="print the engine version")
    sp.set_defaults(func=cmd_version)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        return cmd_version(args)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except BatchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
