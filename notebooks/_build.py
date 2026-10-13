"""Build the pytauargus example notebooks.

Run:  uv run --with nbformat python notebooks/_build.py [name.ipynb ...]

Each notebook is written as a clean .ipynb (empty outputs). Run
`python notebooks/_run.py` to execute them and save real outputs.
"""

import sys
from pathlib import Path

try:
    import nbformat
except ImportError:
    sys.exit("nbformat not installed. Run: uv run --with nbformat python notebooks/_build.py")

NB_DIR = Path(__file__).resolve().parent


# ── cell helpers ──────────────────────────────────────────────────────────────

def md(*lines):
    """Markdown cell. Pass raw text lines (no trailing newline needed)."""
    cell = nbformat.v4.new_markdown_cell("\n".join(lines))
    return cell


def code(src: str):
    """Code cell."""
    return nbformat.v4.new_code_cell(src.rstrip("\n"))


def write_notebook(name: str, cells: list):
    path = NB_DIR / name
    if path.exists():
        previous = nbformat.read(path, as_version=4)
        ids = {}
        for cell in previous.cells:
            ids.setdefault((cell.cell_type, cell.source), []).append(cell.id)
        for cell in cells:
            matching = ids.get((cell.cell_type, cell.source), [])
            if matching:
                cell.id = matching.pop(0)
    nb = nbformat.v4.new_notebook()
    nb["cells"] = cells
    nb["metadata"] = {
        "kernelspec": {
            "display_name": "Python 3",
            "language": "python",
            "name": "python3",
        },
        "language_info": {"name": "python", "version": "3"},
    }
    nbformat.validate(nb)
    nbformat.write(nb, path)
    print(f"  wrote {path}")


# ── shared setup cell (used in all notebooks) ─────────────────────────────────

SETUP = code('''\
from pathlib import Path
import pytauargus

# Locate the repo's data/ fixtures (works from notebooks/ or repo root)
_here = Path.cwd()
DATA = next(
    (p / "data") for p in [_here, *_here.parents]
    if (p / "data" / "tau_testW.asc").exists()
)
OUT = _here / "out"          # scratch dir for notebook outputs (gitignored)
OUT.mkdir(exist_ok=True)

def display_paths(text):
    """Use portable paths in displayed text; engine inputs keep real paths."""
    text = str(text)
    for path, label in ((OUT, "./out"), (DATA, "<repo>/data")):
        text = text.replace(str(path), label).replace(path.as_posix(), label)
    return text

print(f"pytauargus {pytauargus.__version__}")
print("data : <repo>/data")
print("out  : ./out")
''')

DATAFRAME = code('''\
import pandas as pd
from pytauargus import protect

# Twelve synthetic respondents; North/Retail has one dominant contributor.
micro = pd.DataFrame({
    "Region": ["North"] * 6 + ["South"] * 6,
    "Sector": ["Retail"] * 3 + ["Services"] * 3
              + ["Retail"] * 3 + ["Services"] * 3,
    "Turnover": [100., 5., 5., 10., 10., 10., 20., 20., 20., 30., 30., 30.],
})
micro
''')


# ═══════════════════════════════════════════════════════════════════════════════
# NOTEBOOK 1 — Quick Start
# ═══════════════════════════════════════════════════════════════════════════════

nb1 = [
    md(
        "# pytauargus — Quick Start",
        "",
        "**Statistical Disclosure Control (SDC) for tabular data**, as a",
        "Python package and a headless `tauargus` command. This notebook",
        "starts with a DataFrame: `protect()` → `TableResult` → masked output.",
        "It then shows how existing metadata and `.arb` batches fit the same engine.",
        "",
        "## How to run",
        "",
        "```bash",
        "# from the bindings/python/ directory",
        "uv sync --extra notebooks",
        "uv run --with jupyterlab jupyter lab notebooks/01_quickstart.ipynb",
        "```",
        "",
        "Or headless (no Jupyter needed):",
        "",
        "```bash",
        "uv run --with nbclient --with nbformat --with ipykernel \\",
        "  python notebooks/_run.py 01_quickstart.ipynb",
        "```",
        "",
        "## What you need",
        "",
        "- `pytauargus>=0.3.0` for the DataFrame `protect()` API; published",
        "  wheels include the native engine and HiGHS. For source checkouts,",
        "  follow the [build instructions](../../../README.md#building-from-source).",
        "- The repository's `data/` fixtures (included in the repo)",
        "- Python 3.10–3.13; macOS arm64 / Linux x86_64+aarch64 / Windows x64",
        "- **No licensed solver, no SPSS, no external executable** — the LP/MIP",
        "  solver (HiGHS) is bundled in the wheel",
        "",
        "---",
    ),
    SETUP,

    md(
        "## 1. Protect a DataFrame",
        "",
        "Each row below is one respondent. `NK(2,75)` marks a cell as unsafe",
        "when its two largest contributors exceed 75% of the response.",
        "`protect()` writes `.asc`, `.rda` and `.arb`, computes the crossing,",
        "applies OPT suppression by default and reads the result back.",
    ),
    DATAFRAME,
    code('''\
protected = protect(
    micro, ["Region", "Sector"], response="Turnover",
    safety_rules="NK(2,75)", workdir=str(OUT / "01_protect"),
)
table = protected.tables[0]
assert table.n_cells == 9  # 2 x 2 crossing, including row/column totals
print(table)
print("status:", table.status())
'''),
    md(
        "## 2. Build the masked publication table",
        "",
        "`TableResult.dataframe()` is the full diagnostic result and includes",
        "original responses. `unsafe()` returns those unmasked values.",
        "For publication, select the explanatory codes and use `safe()` for",
        "the response: primary (`U`) and secondary (`M`) cells become `x`.",
        "Use `safe(None)` if your downstream format expects missing values.",
    ),
    code('''\
published = table.dataframe()[["Region", "Sector"]].copy()
published["Turnover"] = table.safe()
assert all(value == "x" for status, value in zip(table.status(), table.safe())
           if status in {"U", "M"})
published
'''),
    md(
        "### Results and intermediate files",
        "",
        "`ProtectResult.tables` holds one result per table; `.engine` gives",
        "access to audit and other engine operations. `.files` and `.workdir`",
        "locate the generated inputs and type-5 `.tab` results.",
        "The files and diagnostic values are analyst artifacts. Keep them",
        "separate from the masked publication table.",
    ),
    code('''\
print("files:", {key: Path(path).name for key, path in protected.files.items()})
print("original response:", table.unsafe())  # synthetic analyst data only
published.to_csv(OUT / "01_dataframe_publication.csv", index=False)
print("wrote 01_dataframe_publication.csv")
'''),

    md(
        "## 3. Parquet input and output",
        "",
        "`protect()` also accepts a **path to a `.parquet` file** (or an",
        "`s3://`, `gs://` or `https://` URL) in place of a DataFrame, and",
        "`TableResult.to_parquet(path)` writes the published table back out:",
        "the explanatory codes plus the response, with suppressed cells",
        "(`U` and `M`) as null. Both need the `parquet` extra (pyarrow);",
        "cloud credentials come from the environment, as pyarrow requires them.",
    ),
    code('''\
import pyarrow.parquet as pq

# The repo ships a small parquet microdata fixture (see data/README.md):
# eight Sector x Geo records where only (Alpha, East) fails NK(1,80).
micro_pq = DATA / "examples" / "parquet" / "micro.parquet"
pq_result = protect(
    micro_pq, ["Sector", "Geo"], response="Value",
    safety_rules="NK(1,80)", workdir=str(OUT / "01_protect_parquet"),
)
pq_table = pq_result.tables[0]
print("status:", pq_table.status())

out_pq = OUT / "01_protected.parquet"
pq_table.to_parquet(out_pq)
print("wrote", out_pq.name)
pq.read_table(out_pq).to_pylist()
'''),

    md(
        "## Working with existing batches",
        "",
        "pytauargus has **zero runtime dependencies**; the native engine",
        "(C/C++ port of the Tau-Argus core, built against HiGHS) ships inside",
        "the wheel. The main entry points:",
        "",
        "| Symbol | Source | Purpose |",
        "|--------|--------|---------|",
        "| `protect(…)` / `ProtectResult` | `pytauargus` | DataFrame/dict → protected tables + files + engine |",
        "| `TableResult` | `pytauargus` | `safe()`, `status()`, `unsafe()`, `dataframe()`, `to_parquet(path)` |",
        "| `Engine` | `pytauargus.engine` | High-level driver for the native engine |",
        "| `run_batch(arb)` | `pytauargus.engine` | Parse + execute a `.arb` batch file → `Engine` |",
        "| `parse_rda(path)` | `pytauargus.engine` | Parse `.rda` metadata → `Metadata` |",
        "| `parse_batch(path)` | `pytauargus.batch` | Parse `.arb` → list of `Command` dataclasses |",
        "| `micro_arb(…)` | `pytauargus.arb` | **Generate** a `.arb` batch file |",
        "| `write_rda` / `rda_text` | `pytauargus.rda` | **Generate** `.rda` metadata text |",
        "| `write_hrc(…)` | `pytauargus.hrc` | **Generate** a `.hrc` hierarchy file |",
        "| `Suppress`, `WriteTable`, `SpecifyTable`, … | `pytauargus.batch` | Parsed command dataclasses |",
    ),

    md(
        "### Repository fixtures",
        "",
        "The repo ships the legacy Tau-Argus 4.1 test dataset (the same files",
        "used by the legacy product's documentation):",
        "",
        "- `tau_testW.asc` — fixed-width microdata (one record per line)",
        "- `tau_testW.rda` — metadata: positions, widths, types, codelists",
        "- `TestRecode.arb` — a ready-made batch file (2 tables + recodes)",
    ),

    md(
        "### The microdata file (first 4 lines)",
        "",
        "Each record is a fixed-width ASCII line. Column positions and widths",
        "are described in the `.rda` file — the engine parses each record",
        "without any schema inference.",
    ),
    code('''\
with open(DATA / "tau_testW.asc") as f:
    for i, line in enumerate(f):
        if i >= 4: break
        print(f"{i+1:3d}  {line.rstrip()}")
'''),

    md(
        "### The metadata file (first 14 lines)",
        "",
        "The `.rda` describes each variable's position, width, missing code,",
        "type, codelist and hierarchy. Note `Var2` — the numeric response",
        "variable with 2 decimals that our batch tabulates.",
    ),
    code('''\
with open(DATA / "tau_testW.rda") as f:
    for i, line in enumerate(f):
        if i >= 14: break
        print(f"{i+1:3d}  {line.rstrip()}")
'''),

    md(
        "### Parse the metadata",
        "",
        "`parse_rda` reads the `.rda` file and returns a `Metadata` object —",
        "a list of `Variable` records plus file-level options.",
    ),
    code('''\
from pytauargus.engine import parse_rda

meta = parse_rda(DATA / "tau_testW.rda")
print(f"{len(meta.variables)} variables")
print()
for i, v in enumerate(meta.variables):
    print(f"  {v.name:<16} type={v.type:<14}  pos={v.b_pos:>2}  width={v.var_len:>2}  dec={v.n_decimals}")
'''),

    md(
        "### `Variable` attributes (the ones you'll use)",
        "",
        "| Attribute | Meaning |",
        "|-----------|---------|",
        "| `name` | Variable name (lookup is case-insensitive via `Metadata.find`) |",
        "| `type` | `CATEGORICAL`, `RECODEABLE`, `NUMERIC`, `WEIGHT`, `HOLDING`, `REQUEST`, … |",
        "| `b_pos` | 1-based byte position in the `.asc` record |",
        "| `var_len` | Field width in bytes |",
        "| `n_decimals` | Decimal places (NUMERIC / WEIGHT) |",
        "| `missing` | Missing-value codes (up to two, padded strings) |",
        "| `tot_code` | Total code, e.g. `\"99\"` |",
        "| `code_list_file` | Path to a `.cdl` codelist file |",
        "| `hierarchical` | `0` = none, `1` = levels, `2` = `.hrc` file |",
        "",
        "Helper predicates: `is_categorical()`, `is_numeric()`, `is_weight()`,",
        "`is_holding()`, `is_total_code(code)`, `is_missing(code)`.",
    ),

    md(
        "### Run a batch",
        "",
        "`run_batch` is the main entry point. It parses the `.arb` file,",
        "executes every command in order, and returns the configured",
        "`Engine` with tables computed and ready for inspection.",
    ),
    code('''\
from pytauargus.engine import run_batch

eng = run_batch(DATA / "TestRecode.arb")
print(f"engine version: {eng.version}")
print(f"tables: {eng._n_tables}")
for i in range(eng._n_tables):
    ncell, _ = eng._tau.get_total_table_size(i)
    spec, _ = eng._tables[i]
    print(f"  table {i+1}: {' x '.join(spec.exp_vars):<24} | {spec.resp_var}  →  {ncell} cells")
'''),

    md(
        "### What `TestRecode.arb` contains",
        "",
        "Two tables with NK safety rules, then hierarchical recodes — this is",
        "the same file the legacy documentation ships:",
    ),
    code('''\
print((DATA / "TestRecode.arb").read_text())
'''),

    md(
        "### The batch grammar, parsed",
        "",
        "`parse_batch` returns one `Command` dataclass per line —",
        "`OpenMicrodata`, `OpenMetadata`, `SpecifyTable`, `SafetyRuleSet`,",
        "`ReadMicrodata`, `Recode`, … This is the exact grammar the engine",
        "executes, and the dataclasses are what the generators (notebook 3)",
        "and the Python API accept.",
    ),
    code('''\
from pytauargus.batch import parse_batch

cmds = parse_batch(DATA / "TestRecode.arb")
for c in cmds:
    print(f"  {type(c).__name__:<16} {str(c)[:78]}")
'''),

    md(
        "### Inspect native cells",
        "",
        "After `run_batch`, every cell in every table has a **status** and a",
        "**value**. Status codes (from the native engine):",
        "",
        "| Code | Status | Meaning |",
        "|------|--------|---------|",
        "| 1 | `safe` | Publishable as-is |",
        "| 2 | `safe_manual` | Manually marked safe |",
        "| 3–9 | `unsafe_*` | Failed a safety rule (rule / peep / freq / zero / singleton) |",
        "| 10 | `protect_manual` | Manually marked for protection |",
        "| 11 | `secondary_unsafe` | Suppressed secondarily to protect others |",
        "| 13 | `empty_nonstructural` | |",
        "| 14 | `empty` | No contributing records |",
    ),
    code('''\
from collections import Counter

STATUS_NAMES = {
    1: "safe", 2: "safe_manual", 3: "unsafe_rule", 4: "unsafe_peep",
    5: "unsafe_freq", 6: "unsafe_zero", 7: "unsafe_singleton",
    8: "unsafe_singleton_manual", 9: "unsafe_manual", 10: "protect_manual",
    11: "secondary_unsafe", 12: "secondary_unsafe_manual",
    13: "empty_nonstructural", 14: "empty",
}

tau = eng._tau
n = tau.get_total_table_size(0)[0]
statuses = Counter(tau.get_table_cell_status(0, j) for j in range(n))
print(f"Table 1 — {n} cells:")
for code_, count in sorted(statuses.items()):
    print(f"  {STATUS_NAMES.get(code_, code_):<24} {count}")
'''),

    md(
        "### A few individual cells",
        "",
        "`get_table_cell(tab, dim, top_n)` returns a tuple: the response",
        "value at index 1, the status at index 10, and (after an audit) the",
        "realized lower/upper bounds as the last two entries. Walk the first",
        "row-major cells of table 1:",
    ),
    code('''\
spec, _ = eng._tables[0]
exp = [eng.metadata.index_of(nm) for nm in spec.exp_vars]
max_dim = [tau.get_var_number_of_codes(vi)[1] for vi in exp]   # active codes
print(f"table 1 dims: {max_dim}")
print()
dim = [0, 0]
for idx in range(6):
    res = tau.get_table_cell(0, dim, 0)
    val, status = res[1], res[10]
    print(f"  cell {idx:2d}  dims={dim}  value={val:>12.2f}  "
          f"status={STATUS_NAMES.get(status, status)}")
    dim[1] += 1
    if dim[1] >= max_dim[1]:
        dim[0] += 1
        dim[1] = 0
'''),

    md(
        "### Try it yourself",
        "",
        "- Change `DATA / \"TestRecode.arb\"` to another `.arb` in `data/`.",
        "- Swap table index `0` for `1` and count its statuses.",
        "- `tau.get_var_code(vi, ci)` → `(ok, code_type, code, missing, level)`",
        "  decodes any categorical code.",
    ),

    md(
        "## Summary",
        "",
        "You've now:",
        "",
        "1. Protected a DataFrame with `protect()` and inspected `TableResult`",
        "2. Exported masked responses with `safe()`",
        "3. Parsed the `.rda` metadata and listed the variables",
        "4. Run a `.arb` batch through the engine",
        "5. Inspected computed table cells and their safety statuses",
        "6. Protected a `.parquet` microdata file and exported it with `to_parquet()`",
        "",
        "**Next:** [02_protection_and_audit.ipynb](02_protection_and_audit.ipynb)",
        "— suppress the unsafe cells and run the audit (Intervalle).",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════════
# NOTEBOOK 2 — Protection & Audit
# ═══════════════════════════════════════════════════════════════════════════════

nb2 = [
    md(
        "# Protection & Audit (Intervalle)",
        "",
        "The core SDC workflow, end to end:",
        "",
        "1. **Compute** tables from microdata",
        "2. **Suppress** unsafe cells (OPT / MOD / RND)",
        "3. **Audit** — compute the *realized feasibility intervals* for each",
        "   suppressed cell (the legacy `intervalle.exe`, now in-process)",
        "4. **Export** the protected table (CSV or legacy INTERMEDIATE)",
        "",
        "Start with the DataFrame API, then inspect the equivalent lower-level",
        "batch workflow. `protect()` suppresses; audit is an explicit next step.",
        "Build this development branch first; the new API awaits release.",
        "",
        "## The audit in one paragraph",
        "",
        "A suppressed cell has a range of values it could take while staying",
        "consistent with the published totals — its **realized lower/upper",
        "bounds** (the *feasibility interval*). Protection requires sufficient",
        "uncertainty to meet the configured protection requirements; a narrow",
        "interval can reveal the value. In legacy Tau-Argus the audit ran in a **separate**",
        "`intervalle.exe`; the rewrite ports it into the engine (`TauAuditJj`",
        "in the `csp` submodule), so it runs **in-process** over a temporary",
        "`.JJ` file with nothing external to install.",
        "",
        "## Setup",
    ),
    SETUP,

    md(
        "## DataFrame protection, audit and export",
        "",
        "Use the same synthetic respondents as the quickstart. OPT chooses",
        "secondary cells so published totals cannot reconstruct primary cells.",
    ),
    DATAFRAME,
    code('''\
from collections import Counter

protected = protect(
    micro, ["Region", "Sector"], response="Turnover",
    safety_rules="NK(2,75)", suppress="OPT(1)",
    workdir=str(OUT / "02_protect"),
)
table = protected.tables[0]
assert "U" in table.status() and "M" in table.status()
print("status counts:", dict(Counter(table.status())))
table.dataframe()[["Region", "Sector"]].assign(Turnover=table.safe())
'''),
    md(
        "### Audit the returned engine",
        "",
        "`safe()` masks the suppressed cells; it does not run an audit.",
        "Call `protected.engine.audit(0)` explicitly (engine table indices",
        "are zero-based). `min`/`max` are the realized feasibility bounds.",
        "The `lower`/`upper` columns in the default result hold protection levels,",
        "and the result is a snapshot: it does not refresh when audit runs.",
    ),
    code('''\
audit_rows = protected.engine.audit(0)
audit_frame = pd.DataFrame(audit_rows)
assert len(audit_frame) > 0 and not audit_frame["unsafe"].any()
assert ((audit_frame["min"] <= audit_frame["value"])
        & (audit_frame["value"] <= audit_frame["max"])).all()
audit_frame[["cell", "value", "min", "max", "unsafe"]]
'''),
    md(
        "### Publish masked values; retain the audit separately",
        "",
        "The full DataFrame and `.tab` intermediates contain original values.",
        "Export the selected codes with `safe()` as the response. Keep the",
        "INTERMEDIATE file with realized bounds for internal audit review.",
    ),
    code('''\
release = table.dataframe()[["Region", "Sector"]].assign(Turnover=table.safe())
release.to_csv(OUT / "02_dataframe_publication.csv", index=False)
protected.engine.write_intermediate_table(
    0, str(OUT / "02_dataframe_audit.tab"), with_audit=True,
)
print("wrote 02_dataframe_publication.csv and 02_dataframe_audit.tab")
release
'''),
    md(
        "### Choose another suppression method",
        "",
        "Switch to `MOD(1)` for modular suppression. Each call computes its",
        "own tables; use a separate working directory for each result.",
    ),
    code('''\
modular = protect(
    micro, ["Region", "Sector"], response="Turnover",
    safety_rules="NK(2,75)", suppress="MOD(1)",
    workdir=str(OUT / "02_modular"),
)
print("MOD status counts:", dict(Counter(modular.tables[0].status())))
modular.tables[0].dataframe()[["Region", "Sector"]].assign(
    Turnover=modular.tables[0].safe(),
)
'''),

    md(
        "## 1. Batch API: compute the tables",
        "",
        "Run the batch. `NK(n,k)` flags cells whose n largest contributors",
        "exceed k percent of the response — these start as `unsafe_rule`.",
    ),
    code('''\
from pytauargus.engine import run_batch
from pytauargus.batch import Suppress
from collections import Counter

STATUS_NAMES = {
    1: "safe", 2: "safe_manual", 3: "unsafe_rule", 4: "unsafe_peep",
    5: "unsafe_freq", 6: "unsafe_zero", 7: "unsafe_singleton",
    8: "unsafe_singleton_manual", 9: "unsafe_manual", 10: "protect_manual",
    11: "secondary_unsafe", 12: "secondary_unsafe_manual",
    13: "empty_nonstructural", 14: "empty",
}

eng = run_batch(DATA / "TestRecode.arb")
tau = eng._tau
n = tau.get_total_table_size(0)[0]
before = Counter(tau.get_table_cell_status(0, j) for j in range(n))
print(f"Table 1 — {n} cells, pre-suppression:")
for code_, cnt in sorted(before.items()):
    print(f"  {STATUS_NAMES.get(code_, code_):<24} {cnt}")
'''),

    md(
        "## 2. Suppress",
        "",
        "Suppression methods available in headless mode:",
        "",
        "| Method | Call | Notes |",
        "|--------|------|-------|",
        "| **OPT** | `eng.suppress(Suppress(kind=\"OPT\", tab_no=N))` | Optimal, LP-based. `opt_max_time=0` = no limit (default). |",
        "| **MOD** | `eng.suppress(Suppress(kind=\"MOD\", tab_no=N))` | Modular, heuristic. `mod_max_time=0` (default). |",
        "| **RND** | `eng.suppress(Suppress(kind=\"RND\", tab_no=N, rnd_base=B))` | Controlled rounding; `B` must be ≥ 10 000 000. |",
        "",
        "> `opt_max_time`/`mod_max_time` are in **minutes**; `0` means no limit.",
        "> Positive limits are covered by repeated-run regression tests.",
        "> These examples use the default `0` so they can finish without a deadline.",
        ">",
        "> `GH` (global hiding), `NET` (network) and `CTA` need external",
        "> executables and are **not** available headless.",
    ),
    code('''\
# Suppress table 1 (tab_no is 1-based in the API, as in the .arb grammar)
eng.suppress(Suppress(kind="OPT", tab_no=1))

after = Counter(tau.get_table_cell_status(0, j) for j in range(n))
print("Table 1 — after OPT suppression:")
for code_, cnt in sorted(after.items()):
    print(f"  {STATUS_NAMES.get(code_, code_):<24} {cnt}")
'''),

    md(
        "### RND (controlled rounding) — alternate flow",
        "",
        "Controlled rounding needs a base ≥ 10 000 000. The engine computes a",
        "suitable default from the table's cell sizes:",
    ),
    code('''\
# Suppression mutates state — use a fresh engine for the alternate flow
eng2 = run_batch(DATA / "TestRecode.arb")
base = eng2._min_round_base(0)
print(f"rounding base: {base}")
eng2.suppress(Suppress(kind="RND", tab_no=1, rnd_base=base))
print("RND applied OK")
'''),

    md(
        "## 3. Audit (Intervalle)",
        "",
        "`eng.audit(tab)` (tab is 0-based) computes the **realized",
        "feasibility interval** for every suppressed cell of `tab`. For each",
        "it solves two LPs — minimize the cell, then maximize it, subject to",
        "the published totals and cell bounds — and stores the bounds on the",
        "cell (readable via `get_table_cell` as the last two entries).",
        "",
        "Returns a list of dicts, one per suppressed cell:",
        "",
        "| Key | Meaning |",
        "|-----|---------|",
        "| `cell` | Cell index (row-major) |",
        "| `min` / `max` | Realized lower / upper bound |",
        "| `value` | Original cell value, withheld from the release table |",
        "| `status` | `u` = primary, `m` = secondary |",
        "| `unsafe` | `True` if the ported audit reports insufficient protection |",
    ),
    code('''\
rows = eng.audit(0)
print(f"{len(rows)} suppressed cells audited")
print()
for r in rows:
    flag = "   ⚠ UNDER-PROTECTED" if r["unsafe"] else ""
    print(f"  cell {r['cell']:3d}  interval=[{r['min']:>10.1f}, {r['max']:>10.1f}]  "
          f"value={r['value']:>10.1f}  {r['status']}{flag}")
'''),

    md(
        "### Reading the result",
        "",
        "`unsafe=False` means the cell passes the ported audit's protection-level",
        "check. The feasibility interval normally contains the original value;",
        "protection comes from sufficient uncertainty, not from excluding it.",
        "",
        "`unsafe=True` means the audit reports insufficient protection. Review",
        "the suppression pattern and protection requirements before publishing.",
        "",
        "### Try it yourself",
        "",
        "- Suppress table 2 instead: `eng.suppress(Suppress(kind=\"OPT\", tab_no=2))`",
        "  then `eng.audit(1)` — a 12 726-cell table.",
        "- Compare OPT vs MOD on table 1 and diff the status counts.",
    ),

    md(
        "## 4. Export",
        "",
        "Two public export paths:",
        "",
        "| Format | Call | Notes |",
        "|--------|------|-------|",
        "| CSV (published values) | `eng.write_table(WriteTable(tab_no=1, output_type=1, file=…))` | `output_type=1` is CSV |",
        "| **INTERMEDIATE** (legacy audit file) | `eng.write_intermediate_table(tab, path, with_audit=True)` | One row per cell + realized bounds |",
        "",
        "The INTERMEDIATE (WRITETABLE type-5) file is the legacy audit",
        "format: values, statuses, and — with `with_audit=True` (the legacy",
        "`AR+` option) — the realized feasibility bounds and relative-width",
        "percentages for every cell.",
    ),
    code('''\
from pytauargus.batch import WriteTable

csv_path = str(OUT / "table1.csv")
eng.write_table(WriteTable(tab_no=1, output_type=1, file=csv_path))
print(f"wrote {display_paths(csv_path)} ({(OUT / 'table1.csv').stat().st_size} bytes)")
print()
print("\\n".join((OUT / "table1.csv").read_text().splitlines()[:5]))
'''),
    code('''\
tab_path = OUT / "table1.tab"
eng.write_intermediate_table(0, str(tab_path), with_audit=True)
lines = tab_path.read_text().splitlines()
print(f"wrote {display_paths(tab_path)} ({tab_path.stat().st_size} bytes, {len(lines) - 1} rows)")
print()
print(f"header: {lines[0][:100]}…")
print(f"row 1:  {lines[1][:100]}…")
'''),

    md(
        "## Gotchas & known limitations",
        "",
        "- **OPT/MOD time limits:** `opt_max_time` / `mod_max_time` are in",
        "  minutes; `0` means no limit. Repeated fresh-engine runs with a",
        "  one-minute limit pass; deadline expiry is not covered by that test.",
        "- **GH / NET / CTA** require external executables and are not",
        "  available in headless mode.",
        "- **`audit()` mutates state** (it stores the realized bounds on",
        "  the table); run it after your final suppression pass.",
        "- The CLI mirrors every step above: `tauargus run`, `tables`,",
        "  `suppress`, `round`, `audit`, `save`.",
    ),

    md(
        "## Summary",
        "",
        "You've now:",
        "",
        "1. Protected a DataFrame with OPT/MOD and inspected masked responses",
        "2. Computed batch tables and inspected the safety-rule results",
        "3. Applied batch OPT suppression (and RND as an alternate flow)",
        "4. Audited suppressed cells — realized feasibility intervals",
        "5. Exported masked CSVs and retained INTERMEDIATE audit files",
        "",
        "**Next:** [03_generators.ipynb](03_generators.ipynb) — generate",
        "`.arb` / `.rda` / `.hrc` input files from Python.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════════
# NOTEBOOK 3 — Generators
# ═══════════════════════════════════════════════════════════════════════════════

nb3 = [
    md(
        "# Generating Batch Inputs",
        "",
        "Start with `protect(..., run=False)` to generate a complete pipeline",
        "from a DataFrame, inspect its files, then protect multiple tables.",
        "This API is new on the development branch; build the current source.",
        "",
        "pytauargus can also **write** the batch input files, as a port of",
        "[rtauargus](https://github.com/InseeFrLab/rtauargus) (the R wrapper).",
        "The generators emit exactly the file formats the engine reads, so",
        "you can script the full *input → batch → run* flow in pure Python.",
        "",
        "| Function | Output | Ported from (R) |",
        "|----------|--------|-----------------|",
        "| `protect(…, run=False)` | `.asc` + `.rda` + `.arb` and planned `.tab` paths | Python pipeline |",
        "| `micro_asc_rda(…)` | fixed-width `.asc` + `.rda` | `micro_asc_rda.R` |",
        "| `micro_arb(…)` | `.arb` batch file | `micro_arb.R::micro_arb` |",
        "| `write_rda` / `rda_text` | `.rda` metadata text | `micro_asc_rda.R::write_rda` |",
        "| `write_hrc(…)` | `.hrc` hierarchy file | `hrc.R::write_hrc` |",
        "",
        "`protect()` coordinates these generators and runs the engine when",
        "`run=True` (the default). Use individual writers for custom batches.",
        "",
        "## Setup",
    ),
    SETUP,

    md(
        "## 1. Preview the pipeline without running the engine",
        "",
        "A flat list of explanatory variables specifies one table; a nested",
        "list specifies several. `run=False` writes inputs and returns planned",
        "output paths, with empty `.tables` and `.engine=None`.",
    ),
    DATAFRAME,
    code('''\
tables = [["Region", "Sector"], ["Region"]]
draft = protect(
    micro, tables, response="Turnover", safety_rules="NK(2,75)",
    suppress="OPT(1)", run=False, workdir=str(OUT / "03_draft"),
)
assert draft.tables == [] and draft.engine is None
assert all(Path(draft.files[key]).exists() for key in ("asc", "rda", "arb"))
print("files:", {key: Path(path).name for key, path in draft.files.items()})
print("\\n".join(Path(draft.files["rda"]).read_text().splitlines()[:16]))
'''),
    md(
        "### Inspect the generated batch",
        "",
        "The response and safety rule are recycled across tables. A single",
        "suppression specification gets the correct table number (`OPT(1)`,",
        "then `OPT(2)`). The batch uses type-5 `SO+` output for `TableResult`.",
    ),
    code('''\
# Display relative paths so this preview is portable.
batch_text = Path(draft.files["arb"]).read_text()
print(batch_text.replace(str(Path(draft.workdir)), "<workdir>"))
'''),
    md(
        "## 2. Run the multi-table pipeline",
        "",
        "The default `run=True` executes the generated batch and returns",
        "one `TableResult` per table. Use a separate directory to retain both",
        "the file-only preview and the executed run.",
    ),
    code('''\
from collections import Counter

protected = protect(
    micro, tables, response="Turnover", safety_rules="NK(2,75)",
    suppress="OPT(1)", workdir=str(OUT / "03_protect"),
)
assert [table.n_cells for table in protected.tables] == [9, 3]
for i, table in enumerate(protected.tables, 1):
    print(f"table {i}: {table.n_cells} cells; {dict(Counter(table.status()))}")
region = protected.tables[1]
region.dataframe()[["Region"]].assign(Turnover=region.safe())
'''),

    md(
        "## 3. `micro_arb` — write a custom `.arb` batch file",
        "",
        "The main generator. Signature:",
        "",
        "```python",
        "micro_arb(",
        '    arb_filename: str,          # output .arb path (default: temp file)',
        '    asc_filename: str,          # input .asc (referenced, not created)',
        '    rda_filename: str | None,  # defaults to asc_filename with .rda',
        '    explanatory_vars: list,    # ["A","B"] = 1 table; [["A","B"],["C"]] = 2',
        '    response_var: str | list,  # response variable name(s)',
        '    safety_rules: str | list,  # e.g. "NK(1,85)"; recycled if shorter',
        '    suppress: str | list,      # e.g. "OPT(.,0)" — "." = table number',
        '    output_names: list,        # one per table',
        ') -> {"arb_filename": str, "output_names": list}',
        "```",
    ),
    code('''\
from pytauargus.arb import micro_arb

res = micro_arb(
    arb_filename=str(OUT / "demo.arb"),
    asc_filename=str(DATA / "tau_testW.asc"),
    rda_filename=str(DATA / "tau_testW.rda"),
    explanatory_vars=[
        ["Size", "Region"],         # table 1
        ["Region", "IndustryCode"], # table 2
    ],
    response_var="Var2",
    safety_rules=["NK(2,75)"],     # recycled across both tables
    suppress="OPT(.,0)",           # "." -> table number; 0 = no time limit
    output_names=["out1.csv", "out2.csv"],
    output_type="1",             # CSV (the generator default is SBS/type 4)
)
print({**res, "arb_filename": display_paths(res["arb_filename"])})
print()
print("=== generated demo.arb ===")
print(display_paths((OUT / "demo.arb").read_text()))
'''),

    md(
        "### Per-table parameters",
        "",
        "`safety_rules`, `suppress`, `output_type`, … all accept a single",
        "value (recycled) or a per-table list:",
    ),
    code('''\
res2 = micro_arb(
    arb_filename=str(OUT / "demo2.arb"),
    asc_filename=str(DATA / "tau_testW.asc"),
    rda_filename=str(DATA / "tau_testW.rda"),
    explanatory_vars=[["Size"], ["Region"]],
    response_var="Var2",
    safety_rules=["NK(1,85)", "FREQ(3,10)"],   # different rule per table
    suppress="MOD(.,0)",
    output_names=["s1.csv", "s2.csv"],
)
print(display_paths((OUT / "demo2.arb").read_text()))
'''),

    md(
        "## 4. `write_rda` / `rda_text` — write `.rda` metadata",
        "",
        "Each variable is a dict describing its position, width, type, and",
        "optional codelist / hierarchy:",
        "",
        "| Key | Required | Meaning |",
        "|-----|----------|---------|",
        "| `colname` | yes | Variable name |",
        "| `position` | yes | 1-based byte position |",
        "| `width` | yes | Field width |",
        "| `type_var` | yes | `NUMERIC`, `RECODEABLE`, `WEIGHT`, `HOLDING` |",
        "| `digits` | yes* | Decimal places (*NUMERIC / WEIGHT only) |",
        "| `missing` | no | Missing-value code (`""`/`None` = none) |",
        "| `totcode` | no | Total-code string |",
        "| `codelist` | no | `.cdl` path (normalised to absolute) |",
        "| `hierarchical` | no | `.hrc` file, level string like `2 1`, or `None` |",
        "| `hierleadstring` | no | Hierarchy lead string (`.hrc` files) |",
        "",
        "`write_rda` returns one block string per variable; `rda_text`",
        "renders the whole file.",
    ),
    code('''\
from pytauargus.rda import write_rda, rda_text

info_vars = [
    dict(type_var="RECODEABLE", colname="V1", position=1, width=1,
         digits=0, missing="?", totcode="Total", codelist=None,
         hierarchical=None, hierleadstring=None),
    dict(type_var="RECODEABLE", colname="V2", position=3, width=1,
         digits=0, missing="", totcode="Total", codelist=None,
         hierarchical="V2.hrc", hierleadstring="@"),
    dict(type_var="NUMERIC", colname="VAL", position=5, width=3,
         digits=2, missing="9999", totcode=None, codelist=None,
         hierarchical=None, hierleadstring=None),
    dict(type_var="WEIGHT", colname="POIDS", position=9, width=4,
         digits=1, missing="#", totcode=None, codelist=None,
         hierarchical=None, hierleadstring=None),
]

blocks = write_rda(info_vars)
print(f"{len(blocks)} variable blocks")
for b in blocks:
    print(repr(b))

print()
print("=== full .rda text ===")
print(rda_text(info_vars))
'''),

    md(
        "## 5. `write_hrc` — write a `.hrc` hierarchy file",
        "",
        "Builds a hierarchy from microdata columns ordered **finest →",
        "coarsest** and writes it as a `.hrc` file (child levels are",
        "prefixed with the lead string, default `@`).",
        "",
        "```python",
        "write_hrc(",
        '    microdata: dict,      # {var: [value, …]}  (values: str | None)',
        '    vars_hrc: str | list, # column name(s), finest → coarsest',
        '    hierleadstring: str = "@",',
        '    hrc_filename: str | None,   # default: temp file',
        '    fill_na: str = "up",        # "up"/"down" NA imputation',
        '    compact: bool = True,       # prune single-value branches',
        '    hierlevels: str | None,     # split one code into positional levels',
        ') -> str  # the written path',
        "```",
    ),
    code('''\
from pytauargus.hrc import write_hrc
import warnings

# A small hierarchy: city (finest) → country → continent
microdata = {
    "city":      ["PAR", "LYO", "BER", "MUC", "NYC", "TOR", "TYO"],
    "country":   ["FR",  "FR",  "DE",  "DE",  "US",  "CA",  "JP"],
    "continent": ["EU",  "EU",  "EU",  "EU",  "NA",  "NA",  "AS"],
}

with warnings.catch_warnings():
    warnings.simplefilter("ignore")   # silence single-level/NA warnings in demos
    hrc_path = write_hrc(
        microdata,
        vars_hrc=["city", "country", "continent"],
        hrc_filename=str(OUT / "geo.hrc"),
    )

print(f"wrote {display_paths(hrc_path)}")
print()
print((OUT / "geo.hrc").read_text())
'''),

    md(
        "### `hierlevels` — split one code into positional levels",
        "",
        "When a single column encodes several levels positionally (e.g.",
        "`\"123\"` = level 1 `1`, level 2 `2`, level 3 `3`), `hierlevels`",
        "splits it — exactly one `vars_hrc` column is allowed:",
    ),
    code('''\
import warnings

microdata2 = {"code": ["111", "112", "121", "122", "211", "212"]}

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    hrc2 = write_hrc(
        microdata2,
        vars_hrc="code",
        hierlevels="1 1 1",           # 3 levels, 1 character each
        hrc_filename=str(OUT / "pos.hrc"),
    )

print((OUT / "pos.hrc").read_text())
'''),

    md(
        "## 6. Custom batch round-trip: generate → run → inspect",
        "",
        "A generated `.arb` feeds straight back into the engine. This is",
        "the full scripted pipeline — note the in-arb `<SUPPRESS> OPT(…)`",
        "runs during `run_batch`:",
    ),
    code('''\
from pytauargus.engine import run_batch

arb_path = OUT / "demo.arb"
print(f"Running {arb_path.name}…\\n")

eng = run_batch(arb_path)
for i in range(eng._n_tables):
    ncell, _ = eng._tau.get_total_table_size(i)
    spec, _ = eng._tables[i]
    print(f"  table {i+1}: {' x '.join(spec.exp_vars):<24} | {spec.resp_var}  →  {ncell} cells")
'''),

    md(
        "### Where did the outputs go?",
        "",
        "`WRITETABLE` output files are written relative to the **`.arb`",
        "file's directory** — here, the `out/` scratch dir:",
    ),
    code('''\
for p in sorted(OUT.iterdir()):
    if p.is_file():
        print(f"  {p.name:<16} {p.stat().st_size:>8} bytes")
'''),

    md(
        "## Gotchas & known limitations",
        "",
        "- `suppress=\"OPT(.,0)\"` uses max-time `0` (no limit), so this example",
        "  can finish without a deadline. Positive time limits are in minutes.",
        "- `protect()` and `micro_asc_rda()` write the `.asc` file too;",
        "  `micro_arb()` only references an existing `.asc` / `.rda` pair.",
        "- Working directories and original values are retained for analysis;",
        "  publish the masked `safe()` response, and clean up intermediates yourself.",
        "- `write_hrc` with a **single** column emits a flat sorted list and",
        "  warns; `fill_na` warns when NAs are imputed.",
    ),

    md(
        "## Summary",
        "",
        "You've now:",
        "",
        "1. Previewed `.asc` / `.rda` / `.arb` with `protect(run=False)`",
        "2. Protected multiple tables and read their `TableResult` objects",
        "3. Generated custom `.arb` batch files with `micro_arb`",
        "4. Generated `.rda` metadata text with `write_rda` / `rda_text`",
        "5. Generated `.hrc` hierarchy files with `write_hrc`",
        "6. Round-tripped: generated batch → engine → tables + CSVs",
        "",
        "**Back to:** [01_quickstart.ipynb](01_quickstart.ipynb) ·",
        "[02_protection_and_audit.ipynb](02_protection_and_audit.ipynb)",
    ),
]


# ── write all notebooks ───────────────────────────────────────────────────────

ALL = {
    "01_quickstart.ipynb": nb1,
    "02_protection_and_audit.ipynb": nb2,
    "03_generators.ipynb": nb3,
}
names = sys.argv[1:] or list(ALL)
for name in names:
    if name not in ALL:
        sys.exit(f"Unknown notebook: {name}")
    write_notebook(name, ALL[name])
print("done")
