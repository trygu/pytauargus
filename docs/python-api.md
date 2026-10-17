# Python API guide

[README](../README.md#python-library) · [Batch files and CLI](batch-files.md) ·
[Compatibility and validation](compatibility.md)

Start with the [DataFrame example](../README.md#protect-a-dataframe).
The following examples reuse its `micro` DataFrame.

## Accepted inputs

Pandas is optional for the core API: column dicts, lists of row dicts and
polars DataFrames are accepted too. `dataframe()` returns a pandas DataFrame
when available, otherwise polars, otherwise a list of row dicts.
Column labels become strings and must remain unique after conversion. Column
dicts require equal lengths; row dicts use the ordered union of all keys, with
missing fields represented by `None`.

With the `parquet` extra, `protect()` also accepts a local Parquet path or
an `s3://`, `gs://` or `https://` URL. See
[Parquet input and output](../README.md#read-and-write-parquet).

## Protecting tables

```python
from pytauargus import protect, ProtectResult, TableResult

# Multiple tables; scalar options are recycled across them.
result = protect(
    micro, [["Region", "Sector"], ["Region"]], response="Turnover",
    safety_rules="NK(2,75)", suppress="OPT(1)", workdir="multi_table_run",
)
assert isinstance(result, ProtectResult)
assert all(isinstance(table, TableResult) for table in result.tables)
```

| Argument | Meaning |
| ---------- | --------- |
| `tables` | A flat list of explanatory names for one table; a nested list for several |
| `response` | Required numeric response column; a scalar or one name per table |
| `safety_rules` | A rule string or per-table list; `None` supplies no rules |
| `suppress` | Default `"OPT(1)"`; use `"MOD(1)"` for modular suppression. A single string gets each table's correct number |
| `run=False` | Write `.asc`, `.rda`, `.arb` and return planned output paths without running the engine |
| `workdir` | Retained directory, resolved from the caller's working directory; returned paths are absolute. Defaults to a new temporary directory |

Explanatory and response names must be unique within each table. The magnitude
result reserves `freq`, `cost`, `status`, `lower` and `upper` for diagnostics;
rename colliding source columns before calling `protect()`. Invalid names and
suppression sequences whose length is neither 1 nor the table count raise
`ValueError` before any input files are written.

Metadata and table options include `weight_var`, `weighted`, `holding_var`,
`decimals`, `hrc`, `totcode`, `missing` and `codelist`; see `help(protect)`.
The high-level API requires a numeric response column; frequency-only tables
using the batch `<freq>` response use the lower-level API.

`ProtectResult` contains `.tables` in input order, `.engine` after the batch,
`.files` (`asc`, `rda`, `arb`, `tab1`, …) and `.workdir`. With `run=False`,
`.tables` is empty and `.engine` is `None`; the planned `.tab` files are not
created by that call. Files persist so callers can inspect them and choose
when to remove them.

| `TableResult` accessor | Returns |
| ------------------------ | --------- |
| `safe(unsafe_marker="x")` | Response values with primary `U` and secondary `M` cells masked |
| `status()` | One `S`, `U`, `P`, `M` or `E` symbol per cell |
| `unsafe()` | Original, unmasked responses for analysis |
| `dataframe()` | Full diagnostic rows, including original responses |
| `to_parquet(path)` | Write the published table to Parquet, suppressed cells as null (needs the `parquet` extra) |
| `n_cells`, `columns`, `rows`, `response`, `path` | Result shape, rows and response name |

Use the explanatory columns plus `safe()` to construct the publication table,
as in the quickstart. `dataframe()` does not mask the response automatically.
`safe(None)` uses missing values instead of `x`.

Audit is explicit and engine indices are zero-based:

```python
rows = result.engine.audit(0)  # realized min/max and insufficient-protection flag
result.engine.write_intermediate_table(0, "audit.tab", with_audit=True)
```

The `lower` / `upper` columns in the default result are protection levels;
realized bounds are returned by `audit()`. `TableResult` is a parsed snapshot
and does not refresh when the engine is audited or modified. Raw intermediates
and audit reports contain original values and belong with analyst artifacts.
The list-of-dicts fallback from `dataframe()` is a defensive copy, so editing
it does not change the result's subsequent `safe()` or `status()` values.

## Existing batches and individual commands

```python
from pytauargus.engine import run_batch

engine = run_batch("demo.arb")   # parse and execute; returns the Engine
```

Lower-level pieces: `pytauargus.batch.parse_batch(path)` returns the parsed
command list, and `pytauargus.engine.Engine` executes commands one at a time.

## Generating batch inputs

`pytauargus` also generates the batch inputs. The generator is a port of
[rtauargus](https://github.com/InseeFrLab/rtauargus), so it emits the exact
formats the engine reads:

```python
from pytauargus.arb import micro_arb

micro_arb(
    asc_filename="donnees.asc",
    explanatory_vars=[["REGION"], ["SEXE"]],
    response_var="CA",
    safety_rules=["NK(1,85)", "FREQ(3,10)"],
    suppress="OPT(.)",
    output_names=["tab1.csv", "tab2.csv"],
    output_type="1",  # CSV; the generator default is SBS/type 4
)
```

`pytauargus.micro.micro_asc_rda` writes the fixed-width `.asc` file and its
`.rda` metadata. `micro_arb` references those files and writes a `.arb` batch.
`pytauargus.hrc.write_hrc` writes `.hrc` hierarchy files; the separate
`pytauargus.rda.write_rda` writer handles metadata text. `protect()` coordinates
these steps automatically; `protect(..., run=False)` previews the complete
generated pipeline without running it.

## Audit details

A suppressed cell has a range of values it could take while staying consistent
with the published totals, its **realized lower/upper bounds**, the
*feasibility interval*. Protection requires sufficient uncertainty to meet the
configured protection requirements. A narrow interval can reveal the original
value or constrain it too closely; interval width alone does not establish
protection. The audit checks for insufficient protection.

In legacy Tau-Argus this ran in a separate `intervalle.exe` (standalone
Delphi/Pascal executable); Tau-Argus wrote a `.JJ` file, launched it externally
and read the intervals back. The rewrite ports that logic into the engine
(`TauAuditJj` in the `csp` module), so it runs in-process over a temporary
`.JJ` file with nothing external to install:

```python
from pytauargus.engine import run_batch

engine = run_batch("demo.arb")
rows = engine.audit(0)   # one dict per suppressed cell:
# {"cell", "min", "max", "value", "status", "unsafe"}
```

The realized bounds are stored on the table (readable via `get_table_cell`).
`tauargus save demo.arb --audit --format intermediate` writes the legacy
INTERMEDIATE (type-5) audit file with the realized-interval columns.

The high-level `TableResult` remains a parsed snapshot after an engine audit;
use the returned audit rows for realised intervals.
