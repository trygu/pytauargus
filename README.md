# pytauargus

Statistical disclosure control (SDC) for tabular data, as a Python package and
a headless `tauargus` command. Protect tables directly from DataFrames with
`protect()`, or run supported legacy Tau-Argus `.arb` batches without the GUI.
The 4.1 manual is the batch/file-format reference, not a complete feature
compatibility guarantee.
The engine solves with the open-source
[HiGHS](https://github.com/ERGO-Code/HiGHS) solver. The native engine and
the thin bindings live in
[tauargus-engine](https://github.com/trygu/tauargus-engine); every release
builds the wheel from a pinned engine revision (see
[Building from source](#building-from-source)). The R front-end is
[rtauargus](https://github.com/trygu/rtauargus).

## Supported workflows and validation

- **OPT, MOD and RND** are implemented and regression-tested.
- **CKM** has a dispatch path, but no dedicated end-to-end CKM regression suite.
- **GH, NET and CTA** are rejected by the headless engine, even though the
  parser/generators accept their legacy syntax.
- **Suppression audit** runs in-process and has known-interval contract tests.

Regression tests do not establish comprehensive numerical equivalence with
original Tau-Argus. Compare protection requirements, realised audit intervals,
information loss and failure behaviour on representative tables before adopting
a workflow; solver changes can produce different valid suppression patterns.
Audit is explicit: `engine.audit(tab)` checks the supplied table's suppression
constraints. `TableResult.safe()` masks cells; it does not run the audit.

Run independent jobs in separate processes and working/output directories.
The native solver retains process-global state, and concurrent engine calls in
the same process are not validated. OPT/MOD limits are in minutes (`0`: no
limit); the positive-limit tests do not cover deadline expiry or partial results.

See [Compatibility and validation](https://github.com/trygu/tauargus-engine#compatibility-and-validation)
for the project scope and the distinction between diagnostics and publication.

## Install

```bash
pip install --upgrade pytauargus
tauargus --version
```

These commands install the current release. To keep a specific version, use
`pytauargus==<version>` from [PyPI](https://pypi.org/project/pytauargus/#history).

Wheels bundle HiGHS and the native engine; no other installation is needed.
Available for Python 3.10-3.15 on macOS arm64, Linux x86_64 and aarch64 (manylinux_2_28) and
Windows x64. Other platforms: build from source (see the repository).

## Cloud native

It runs unattended in containers, CI and batch services:

- **Headless:** no GUI, no display, no interactive prompts.
- **Self-contained:** one `pip install`; the solver (HiGHS) and native libraries
  are bundled in the wheel. No licensed solver, license server, system packages
  or Windows registry.
- **Stateless:** input and output are plain files in the working directory;
  scratch files go to the system temp directory.
- **Scriptable:** success is exit code 0; errors go to stderr with a non-zero
  exit code.
- **Portable:** wheels for Linux (manylinux_2_28, so any glibc 2.28+ image such
  as `python:3.x-slim`), macOS arm64 and Windows x64.

## What problem it solves

Publishing a table can disclose information about individual respondents: a
cell with few contributors, or one dominated by a single contributor. Such
*primary* cells are suppressed, but totals would still reveal them, so *secondary*
cells are suppressed as well, chosen to minimise information loss. Alternatively
the table can be controlled-rounded. `tauargus` does this from a batch file.

## Quick start

### From a DataFrame

`protect()` is available from version 0.3.0. Install with
`pip install --upgrade "pytauargus[dataframe]>=0.3.0"` to include pandas support.
For a source checkout, follow the
[Building from source](#building-from-source) instructions,
then run `uv sync --extra dataframe`.

```python
import pandas as pd
from pytauargus import protect

micro = pd.DataFrame({
    "Region": ["North"] * 6 + ["South"] * 6,
    "Sector": ["Retail"] * 3 + ["Services"] * 3
              + ["Retail"] * 3 + ["Services"] * 3,
    "Turnover": [100., 5., 5., 10., 10., 10., 20., 20., 20., 30., 30., 30.],
})
result = protect(
    micro, ["Region", "Sector"], response="Turnover",
    safety_rules="NK(2,75)", suppress="OPT(1)", workdir="tauargus_run",
)
table = result.tables[0]
publication = table.dataframe()[["Region", "Sector"]].copy()
publication["Turnover"] = table.safe()
publication.to_csv("protected.csv", index=False)
print(publication)
```

Each row is one respondent. `NK(2,75)` flags cells whose two largest
contributors exceed 75% of the response. The engine computes the crossing
and totals, identifies primary unsafe cells, and adds secondary suppression.
`protect()` coordinates microdata, metadata, batch generation and result parsing.

Pandas is optional for the core API: column dicts, lists of row dicts and
polars DataFrames are accepted too. `dataframe()` returns a pandas DataFrame
when available, otherwise polars, otherwise a list of row dicts.
Column labels become strings and must remain unique after conversion. Column
dicts require equal lengths; row dicts use the ordered union of all keys, with
missing fields represented by `None`.

### Parquet files and cloud URLs

Install the optional dependency with `pip install "pytauargus[parquet]"`
(pyarrow). Then `protect()` also accepts a path to a `.parquet` file, or an
`s3://`, `gs://` or `https://` URL to one, instead of a DataFrame:

```python
result = protect("s3://bucket/microdata.parquet",
                 ["Region", "Sector"], response="Turnover",
                 safety_rules="NK(2,75)", suppress="OPT(1)")
result.tables[0].to_parquet("s3://bucket/protected.parquet")
```

`to_parquet` writes the published table: the explanatory columns plus the
response, with suppressed cells (`U` and `M` status) as null. Cloud
credentials come from the environment, as pyarrow requires them. The same
inputs and outputs work from the command line (`protect` verb below).

A runnable example lives in
[`data/examples/parquet/`](../../data/examples/parquet/): `micro.parquet`
holds eight records crossing Sector and Geo, and

```bash
tauargus protect data/examples/parquet/micro.parquet \
    --tables 'Sector,Geo' --response Value \
    --safety 'NK(1,80)' --suppress 'OPT(1)' --out protected.parquet
```

protects it. The output `protected.parquet` keeps every published value and
writes the unsafe `(Alpha, East)` cell, plus the cells OPT suppresses to hide
it, as null.

### Existing batch files

Put the data (`.asc`), its metadata (`.rda`) and a batch file in one folder.
Paths in a batch file are relative to the working directory.

```text
<OPENMICRODATA> "tau_testW.asc"
<OPENMETADATA>  "tau_testW.rda"
<SPECIFYTABLE>  "Size""Region"|"Var2"||
<SAFETYRULE>    NK(2,75)|NK(0,0)
<READMICRODATA>
<SUPPRESS>      MOD(1)
<WRITETABLE>    (1,3,AS-,"safe-table.txt")
```

```bash
tauargus run demo.arb
```

This reads the microdata, builds the Size x Region table of `Var2`, flags cells
failing the dominance rule `NK(2,75)` (two largest contributors may not make up
more than 75 %), applies secondary suppression with the modular method, and
writes the protected table as code/value pairs, with suppressed values masked.
The sample inputs are in the repository's
[data/](../../data/) folder.

## Batch file commands

| Command | Purpose |
| --------- | --------- |
| `<OPENMICRODATA>` / `<OPENTABLEDATA>` | Input data file (microdata or a ready table) |
| `<OPENMETADATA>` | `.rda` metadata describing the file layout and variables |
| `<SPECIFYTABLE>` | Explanatory variables, response, shadow and cost variables |
| `<SAFETYRULE>` | Sensitivity rules, e.g. `P(p,n)`, `NK(n,k)`, `FREQ(min,safety)` |
| `<READMICRODATA>` / `<READTABLE>` | Build or read the tables |
| `<RECODE>` | Recode a variable, e.g. via a `.grc` file |
| `<SUPPRESS>` | Protection method: `GH`, `MOD`, `OPT`, `NET`, `RND` (rounding), `CTA` |
| `<WRITETABLE>` | Export: `(TabNo,Type,Options,"file")`; types 1 CSV, 2 pivot CSV, 3 code/value, 4 SBS, 5 intermediate, 6 JJ |
| `<SOLVER>` | Accepted for compatibility; only HiGHS is used. Other names log a warning |
| `<LOGBOOK>` | Write a log file |

The grammar and parameters follow the legacy Tau-Argus 4.1 manual.

## Command line

```bash
tauargus run      batch.arb   # execute the whole batch
tauargus explore  batch.arb   # load data + metadata, list variables
tauargus compute  batch.arb   # table computation only
tauargus suppress batch.arb   # compute, then apply suppression
tauargus round    batch.arb   # compute, then round
tauargus audit    batch.arb   # feasibility intervals of suppressed cells
tauargus save     batch.arb   # compute, then write tables
tauargus tables   batch.arb   # print a table summary
tauargus protect  micro.parquet --tables 'Region,Sector' --response Turnover
version
```

`protect` runs the whole pipeline on microdata without a batch file: it
specifies the tables, applies the safety rules and the suppression method,
and writes the protected table. `--out` names the output; a `.parquet` suffix
writes a masked Parquet file (requires the `parquet` extra), any other name
writes the published CSV. `--tables` is repeated for multiple tables;
`--safety` and `--suppress` take the same rule bodies as the batch file.
The microdata argument is a `.parquet` path or a cloud URL (`s3://`, `gs://`,
`https://`).

```bash
tauargus protect micro.parquet --tables 'Region,Sector' --response Turnover \
    --safety 'NK(2,75)' --suppress 'OPT(1)' --out protected.parquet
```

Exit code is 0 on success and non-zero on an error, with a message on stderr,
so it fits into scripts and pipelines.

## Audit (Intervalle)

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

## Python API

### DataFrame protection

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

### Existing batches and individual commands

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

## Example notebooks

`notebooks/` contains three executed, output-saved notebooks that double
as API documentation (every code cell runs against the verified public API):

| Notebook | Covers |
| ---------- | -------- |
| [`01_quickstart`](notebooks/01_quickstart.ipynb) | A DataFrame through `protect()` to a `TableResult`, a masked CSV, then metadata and existing batches |
| [`02_protection_and_audit`](notebooks/02_protection_and_audit.ipynb) | OPT/MOD DataFrame protection, explicit audit and masked export; batch OPT/MOD/RND |
| [`03_generators`](notebooks/03_generators.ipynb) | `run=False` file preview, multiple tables, individual writers and custom batch round-trip |

Run them interactively, or headless (the same command CI could use).
The `notebooks` extra includes pandas for the DataFrame examples:

```bash
# interactive
uv sync --extra notebooks
uv run --with jupyterlab jupyter lab notebooks/

# headless: execute every notebook and re-save its outputs
uv run --with nbclient --with nbformat --with ipykernel python notebooks/_run.py
```

`uv run --with nbclient --with nbformat --with ipykernel pytest -k notebooks`
executes them as tests too (the test skips when `nbclient` is absent, so the
default `uv run pytest` gate stays dependency-free).

## Building from source

The wheel is built from this repo plus a pinned `tauargus-engine` checkout
(the native engine and the `_tauargus` extension; the pin is the commit
recorded in `tauargus-engine-rev`). Build the engine first, then the wheel:

```bash
# sibling checkout at the pinned revision (see tauargus-engine-rev)
git clone --recurse-submodules https://github.com/trygu/tauargus-engine.git
scripts/sync-venv.sh .venv     # engine -> wheel -> venv, one command
uv build --wheel               # dist/pytauargus-*.whl
```

Point `TAUARGUS_ENGINE_DIR` at a different engine checkout for local engine
work. Test data (`.arb` batches, microdata, example tables) lives in the
engine checkout's `data/` directory; the tests read it through
`TAUARGUS_ENGINE_DIR` (default: the sibling checkout).

## Repository layout

```text
.
├── src/pytauargus/    the package (the `tauargus` CLI entry point)
├── tui/               pytauargus-tui, the interactive Textual terminal UI
├── tests/             package, CLI and end-to-end tests
├── notebooks/         example notebooks
├── docs/              TUI user guide and design notes
└── tauargus-engine-rev   pinned tauargus-engine commit (wheel build input)
```

## Protection regression tests

`uv run pytest tests/test_protection_e2e.py` runs OPT and MOD on two cases.
The `ex1` tabular case checks the legacy 20% manual protection margin,
confirms that the original cell is recoverable before secondary suppression,
and checks its realised lower and upper protection after suppression.
The microdata case adapts the basic dominance example from
[GaussSuppression's magnitude-table vignette](https://statisticsnorway.github.io/ssb-gausssuppression/articles/Magnitude_table_suppression.html),
by Statistics Norway. Contributions are scaled by ten to give integer input;
each row is treated as an independent contribution, without company IDs or
hierarchies. It checks the four cells selected by the 80% dominance rule.

Both cases run the feasibility audit and read back a CSV containing only
dimensions and masked publication values. Primary and secondary suppressed
values must be masked, and unmasked values must be retained. The tests do
not require a particular secondary suppression pattern and do not compare
results against the Gauss algorithm. They cover these two examples, not
singleton knowledge, linked-table disclosure or general statistical safety.

## Acknowledgements

This project builds on Tau-Argus, its statistical methods and native
libraries, and the R and Python tools around it, developed by the people
named below.

### Original Tau-Argus and native libraries

The original Tau-Argus authors and contributors include Statistics
Netherlands (CBS), Peter-Paul de Wolf, Anco Hundepool, Sarah Giessing
(GHMITER and audit), Juan José Salazar González (optimisation methods),
and Jordi Castro (network solutions and CTA).

| Component | Authors and contributors |
| --- | --- |
| `libtauargus` (core) | Statistics Netherlands and Peter-Paul de Wolf. |
| `CSP` (cell suppression) | Matteo Fischetti, Juan José Salazar González, and M. D. Montes de Oca. |
| `CRP` (controlled rounding) | Juan José Salazar González. |
| `tauhitas` (modular suppression) | Statistics Netherlands and Peter-Paul de Wolf. |
| `taurounder` (rounding interface) | Statistics Netherlands and Peter-Paul de Wolf. |

The engine uses [HiGHS](https://highs.dev/) as its open-source LP/MIP solver.

### R and Python interfaces

[rtauargus](https://github.com/InseeFrLab/rtauargus) is developed at
INSEE / InseeFrLab by Pierre-Yves Berrard, Julien Jamme, Nathanaël Rastout,
Jeanne Pointet, Félix Beroud, André-Raymond Socard, Wistan Pomel,
Clara Baudry, and contributors.
Its metadata, batch and hierarchy generation logic was ported to
`pytauargus`; its `micro_rtauargus()` workflow also informed the high-level
`protect()` pipeline.

[PiArgus (`piargus`)](https://github.com/lverweijen/piargus), by
[Laurent Verweijen (`lverweijen`)](https://github.com/lverweijen) and
contributors, inspired the DataFrame-to-table workflow and `TableResult`
with its `safe()`, `status()`, `unsafe()` and `dataframe()` accessors.

[GaussSuppression](https://github.com/statisticsnorway/ssb-gausssuppression)
is developed at Statistics Norway by Øyvind Langsrud, Daniel Lupp,
Hege Bøvelstad and contributors. The protection regression tests adapt
the dominance example from its magnitude-table vignette.

## License

EUPL-1.2. Tau-Argus is (c) Statistics Netherlands.
