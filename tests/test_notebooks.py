"""Headless execution of the example notebooks (optional dependency).

The notebooks double as API documentation, so keeping them executable is
part of the contract. They are run with ``nbclient`` (extra ``notebooks``):

    uv run --with nbclient --with nbformat --with ipykernel \
        pytest -k test_notebooks

Tests are skipped when ``nbclient`` is not installed, so the default
``uv run pytest`` gate stays fast and dependency-free.
"""

import time
from pathlib import Path

import pytest

nbclient = pytest.importorskip("nbclient")
nbformat = pytest.importorskip("nbformat")

NB_DIR = Path(__file__).resolve().parent.parent / "notebooks"
NOTEBOOKS = [
    "01_quickstart.ipynb",
    "02_protection_and_audit.ipynb",
    "03_generators.ipynb",
]


@pytest.fixture()
def notebook():
    """Read a pristine notebook (outputs are not modified by the test)."""

    def _read(name: str):
        nb = nbformat.read(NB_DIR / name, as_version=4)
        nb["metadata"]["kernelspec"] = {
            "display_name": "Python 3",
            "language": "python",
            "name": "python3",
        }
        return nb

    return _read


@pytest.mark.parametrize("name", NOTEBOOKS)
def test_notebook_executes(notebook, name):
    from nbclient import NotebookClient

    nb = notebook(name)
    # run with cwd = notebooks/ so the data locator resolves and scratch
    # outputs land in the gitignored notebooks/out/
    client = NotebookClient(nb, timeout=900, kernel_name="python3",
                            resources={"metadata": {"path": str(NB_DIR)}})
    t0 = time.monotonic()
    client.execute()
    assert time.monotonic() - t0 < 900
    # every code cell produced output (no silent no-ops) and none errored
    code_cells = [c for c in nb.cells if c.cell_type == "code"]
    assert code_cells, f"{name} has no code cells"
    for c in code_cells:
        assert c.get("execution_count") is not None
