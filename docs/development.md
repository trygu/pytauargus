# Development

[README](../README.md) · [Python API](python-api.md) · [Compatibility](compatibility.md)

## Building from source

Published wheels bundle the engine and HiGHS. A source build needs a C/C++
toolchain, CMake, uv and an installed HiGHS CMake package.
The engine is pinned by the `tauargus-engine` submodule gitlink.

Clone the package with its pinned engine and nested submodules:

```bash
git clone --recurse-submodules https://github.com/trygu/pytauargus.git
cd pytauargus
uv venv .venv
```

Set these paths in the same shell before building. `HIGHS_DIR` must point
to the directory containing your installed HiGHS CMake configuration:

```bash
export TAUARGUS_ENGINE_DIR="$(pwd)/tauargus-engine"
export HIGHS_DIR="/absolute/path/to/lib/cmake/Highs"
scripts/sync-venv.sh .venv
.venv/bin/tauargus --version
```

This uses the checked-out submodule consistently for the engine build and
wheel build, then installs the wheel into the existing virtual environment.
The shell script uses Unix-style paths and tools; it is not a native
Windows build recipe. For platform-specific builds and wheel repair/bundling,
see the [wheel workflow](../.github/workflows/wheels.yml).

Without `TAUARGUS_ENGINE_DIR`, the script uses a sibling `../tauargus-engine`
checkout and exports the variable, so the engine build and the wheel build
always use the same checkout. Outside the script, the package's CMake build
defaults to the in-repository `tauargus-engine/` submodule; set the variable
explicitly when building against a custom engine checkout. The sync script
checks the engine revision against the recorded gitlink and checks nested
submodule alignment.

The engine's `engine/HiGHS-VERSION` records the HiGHS version used by the
release workflow. Test data lives in that engine checkout's `data/` directory;
tests use `TAUARGUS_ENGINE_DIR` to find it.

## Repository layout

```text
.
├── src/pytauargus/      Python library and tauargus CLI
├── tui/                optional pytauargus-tui package
├── tests/              package, CLI and end-to-end tests
├── notebooks/          executed examples
├── docs/               user guides and reference documentation
├── scripts/            development helpers
└── tauargus-engine/    pinned engine submodule
```

## Tests

After building and installing the local wheel, install test dependencies
into the same environment:

```bash
uv pip install --python .venv/bin/python pytest pandas pyarrow
.venv/bin/python -m pytest
```

For the protection regression cases alone:

```bash
.venv/bin/python -m pytest tests/test_protection_e2e.py
```

See [regression coverage](compatibility.md#protection-regression-tests)
for what these tests establish.

## Example notebooks

`notebooks/` contains three executed, output-saved notebooks that double
as API documentation (every code cell runs against the verified public API):

| Notebook | Covers |
| ---------- | -------- |
| [`01_quickstart`](../notebooks/01_quickstart.ipynb) | A DataFrame through `protect()` to a `TableResult`, a masked CSV, then metadata and existing batches |
| [`02_protection_and_audit`](../notebooks/02_protection_and_audit.ipynb) | OPT/MOD DataFrame protection, explicit audit and masked export; batch OPT/MOD/RND |
| [`03_generators`](../notebooks/03_generators.ipynb) | `run=False` file preview, multiple tables, individual writers and custom batch round-trip |

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

These notebook commands assume the source-build setup above. Keep
`TAUARGUS_ENGINE_DIR` set so uv builds against the same engine checkout.
