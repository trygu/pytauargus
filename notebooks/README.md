# pytauargus example notebooks

Three executed, output-saved notebooks that double as API documentation. Every
code cell runs against the verified public API; the markdown explains the
surface it exercises.

1. [`01_quickstart.ipynb`](01_quickstart.ipynb): DataFrame → `protect()` → `TableResult` → masked CSV/Parquet; existing batches and native cell inspection
2. [`02_protection_and_audit.ipynb`](02_protection_and_audit.ipynb): OPT/MOD DataFrame protection, explicit audit and masked export; batch OPT/MOD/RND
3. [`03_generators.ipynb`](03_generators.ipynb): `protect(run=False)` file preview, multiple tables, custom writers and batch round-trip

All three use twelve synthetic respondents to demonstrate the new DataFrame
API. `dataframe()` and `unsafe()` expose original values for analysis; the
publication examples use `safe()` to mask primary and secondary suppression.
Audit runs explicitly on the returned engine.

## Running

The DataFrame API requires `pytauargus>=0.3.0`; published wheels include the
native engine and HiGHS. To work from this checkout, follow the repository's
[source-build instructions](../../../README.md#building-from-source).
Then, from `bindings/python/`:

```bash
# interactive
uv sync --extra notebooks
uv run --with jupyterlab jupyter lab notebooks/

# headless: execute every notebook and re-save its outputs
uv run python notebooks/_run.py
```

The notebooks locate the repo `data/` fixtures by walking up from the current
directory, and write their scratch outputs to `notebooks/out/` (gitignored).

The `notebooks` extra includes pandas, pyarrow (the Parquet cells) and the
execution tools.
`tests/test_notebooks.py` executes the same notebooks under pytest:

```bash
uv sync --extra test --extra notebooks
uv run pytest -k notebooks
```

It skips when `nbclient` is absent from a basic test environment.

## Helpers

- `_build.py` — regenerates the `.ipynb` files from the cells defined in the
  script (the single source of truth for notebook content). Pass notebook
  filenames to regenerate only those; unchanged cells retain their IDs.
- `_run.py` — executes the notebooks with `nbclient` and saves outputs.
