"""Execute the example notebooks headlessly and save their outputs.

Run (from bindings/python/):

    uv run --with nbclient --with nbformat --with ipykernel \
        python notebooks/_run.py [name.ipynb ...]

With no arguments, all three notebooks are executed in order. Any cell
raising an error fails the run with a non-zero exit code.
"""

import sys
import time
from pathlib import Path

try:
    import nbformat
    from nbclient import NotebookClient
    from nbclient.exceptions import CellExecutionError
except ImportError:
    sys.exit(
        "Missing dependencies. Run:\n"
        "  uv run --with nbclient --with nbformat --with ipykernel "
        "python notebooks/_run.py"
    )

NB_DIR = Path(__file__).resolve().parent
ALL = [
    "01_quickstart.ipynb",
    "02_protection_and_audit.ipynb",
    "03_generators.ipynb",
]


def run_one(name: str) -> bool:
    path = NB_DIR / name
    nb = nbformat.read(path, as_version=4)
    nb["metadata"]["kernelspec"] = {
        "display_name": "Python 3",
        "language": "python",
        "name": "python3",
    }
    t0 = time.monotonic()
    try:
        client = NotebookClient(
            nb, timeout=900, kernel_name="python3",
            resources={"metadata": {"path": str(NB_DIR)}},
        )
        client.execute()
    except CellExecutionError as e:
        print(f"  FAIL {name}: {e}")
        # save the failed notebook for inspection
        nbformat.write(nb, path)
        return False
    nbformat.write(nb, path)  # save with outputs
    print(f"  ok   {name} ({time.monotonic() - t0:.1f}s)")
    return True


def main() -> int:
    names = sys.argv[1:] or ALL
    failed = [n for n in names if not run_one(n)]
    if failed:
        print(f"\n{len(failed)} notebook(s) failed: {', '.join(failed)}")
        return 1
    print("\nall notebooks executed cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
