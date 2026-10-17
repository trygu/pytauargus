# Batch files and CLI

[README](../README.md#command-line-interface) · [Python API](python-api.md) ·
[Compatibility and validation](compatibility.md)

## Batch example

Put the data (`.asc`), its metadata (`.rda`) and a batch file in one folder.
Run from that folder to keep relative paths unambiguous.

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
Sample inputs are in the engine repository's
[data/](https://github.com/trygu/tauargus-engine/tree/master/data) directory.

## Path resolution

The engine sets its working/output directory to the `.arb` file's parent.
For referenced input files, it first accepts an existing path relative to
the caller's current directory, then tries the batch directory. Run from
the batch directory to avoid collisions between same-named input files.
Keep data, metadata, hierarchies and apriori files with the batch when
moving or archiving a workflow.

## Batch file commands

| Command | Purpose |
| --------- | --------- |
| `<OPENMICRODATA>` / `<OPENTABLEDATA>` | Input data file (microdata or a ready table) |
| `<OPENMETADATA>` | `.rda` metadata describing the file layout and variables |
| `<SPECIFYTABLE>` | Explanatory variables, response, shadow and cost variables |
| `<SAFETYRULE>` | Sensitivity rules, e.g. `P(p,n)`, `NK(n,k)`, `FREQ(min,safety)` |
| `<READMICRODATA>` / `<READTABLE>` | Build or read the tables |
| `<RECODE>` | Recode a variable, e.g. via a `.grc` file |
| `<SUPPRESS>` | Implemented and regression-tested: `MOD`, `OPT`, `RND`; `CKM` has limited validation. Legacy `GH`, `NET`, `CTA` syntax is parsed but execution is rejected |
| `<WRITETABLE>` | Export: `(TabNo,Type,Options,"file")`; implemented types: 1 CSV, 2 pivot CSV, 3 code/value, 4 SBS, 5 intermediate; 6 JJ and 7 CKM are rejected |
| `<SOLVER>` | Accepted for compatibility; only HiGHS is used. Other names log a warning |
| `<LOGBOOK>` | Write a log file |

The grammar and parameters follow the legacy Tau-Argus 4.1 manual.

Accepted grammar does not imply engine support. See
[compatibility and validation](compatibility.md) for method and writer limits.

## CLI commands

Each invocation starts a new engine run. Batch-based commands, except
`explore` and `specify`, execute the batch before taking their additional
action. They do not share state with previous CLI invocations. In
particular, `compute` runs the batch as written, including any suppression
or export commands it contains.

| Command | Action |
| --- | --- |
| `tauargus run demo.arb` | Execute the batch |
| `tauargus explore demo.arb` | Load input/metadata references and list variables |
| `tauargus compute demo.arb` | Execute the batch and report table cell counts |
| `tauargus specify data.asc meta.rda --table '"Size","Region"|Var2'` | Specify tables from microdata and compute (no batch file) |
| `tauargus tables demo.arb` | Execute the batch and print table summaries |
| `tauargus suppress demo.arb --method OPT --tab 1` | Execute the batch, then apply OPT to table 1 |
| `tauargus round demo.arb --tab 1 --base 5` | Execute the batch, then round table 1 |
| `tauargus apriori demo.arb --file marks.apr --tab 1` | Execute the batch, then apply an apriori file |
| `tauargus audit demo.arb` | Execute the batch, then audit suppressed cells |
| `tauargus save demo.arb --format csv --out protected.csv` | Execute the batch, then write CSV |
| `tauargus save demo.arb --audit --format intermediate --out audit.tab` | Execute the batch, audit, then write diagnostic INTERMEDIATE output |
| `tauargus version` or `tauargus --version` | Print the engine version |

`--tab` is one-based. Additional suppression, rounding or apriori changes
made by these individual commands are in-memory actions. They are not
carried into a later `save` invocation. For a reproducible sequence with
export, put the operations in a batch, use the Python engine API in one
process, or save a TUI replay batch.

### Protect Parquet without a batch

Install `pytauargus[parquet]`, then run:

```bash
tauargus protect micro.parquet \
    --tables 'Region,Sector' --response Turnover \
    --safety 'NK(2,75)' --suppress 'OPT(1)' --out protected.parquet
```

The microdata argument is a Parquet path or cloud URL. Repeat `--tables`
for multiple tables; `--safety` and `--suppress` take the same rule bodies
as batch commands. A `.parquet` output suffix writes masked Parquet;
other output names write published CSV. Multiple tables add `_1`, `_2`,
etc. to the supplied output name. Without `--out`, outputs are
`<input_stem>_table1.csv`, etc. in the current directory.

A runnable sample is in
[the engine's Parquet examples](https://github.com/trygu/tauargus-engine/tree/master/data/examples/parquet).
From an engine checkout:

```bash
tauargus protect data/examples/parquet/micro.parquet \
    --tables 'Sector,Geo' --response Value \
    --safety 'NK(1,80)' --suppress 'OPT(1)' --out protected.parquet
```

### Help and exit codes

```bash
tauargus --help
tauargus protect --help
tauargus save --help
```

Successful execution returns 0. Errors are written to stderr with a
non-zero exit code. `audit` reports under-protected cells in its output
but still returns 0 when the audit itself completes successfully.

Audit reports and INTERMEDIATE exports contain original values.
Keep them with analyst artifacts; select masked publication output explicitly.
