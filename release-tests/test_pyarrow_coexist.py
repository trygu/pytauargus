"""pyarrow + native package in one process, both import orders.

Regression for the Linux wheel history: two embedded C++ runtimes in one
process corrupt the heap unless the bundled HiGHS keeps its symbols local
(observed as ``free(): invalid pointer`` with pyarrow). The native run
inside the subprocess exercises the engine; the process must terminate
normally (a crash at shutdown still fails via the exit code).
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pyarrow")

from conftest import FIXTURE

CODE = r"""
import json, shutil, sys
from pathlib import Path
{arrow_first}
import pytauargus
import pytauargus._tauargus as native
{arrow_last}
workdir = Path({workdir!r})
fx = workdir / "fx"
shutil.rmtree(fx, ignore_errors=True)
shutil.copytree({fixture!r}, fx)
from pytauargus.engine import run_batch
engine = run_batch(fx / "ex1.arb")
from pytauargus.batch import Suppress
engine.suppress(Suppress(kind="OPT", tab_no=1))
rows = engine.audit(0)
assert rows, "the audit returned no suppressed cells"
print(json.dumps({{"ok": True,
                   "native": native.__file__,
                   "pyarrow": pyarrow.__version__}}))
"""


def _run(order, workdir):
    code = CODE.format(
        arrow_first="import pyarrow" if order == "arrow-first" else "pass",
        arrow_last="pass" if order == "arrow-first" else "import pyarrow",
        workdir=str(workdir), fixture=str(FIXTURE))
    return subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, timeout=180,
                          cwd=str(workdir))


@pytest.mark.parametrize("order", ["arrow-first", "tauargus-first"])
def test_pyarrow_coexists(order, tmp_path):
    workdir = tmp_path / "coexist"
    workdir.mkdir()
    proc = _run(order, workdir)
    assert proc.returncode == 0, (
        f"pyarrow coexistence ({order}) exited {proc.returncode}:\n"
        + (proc.stdout + proc.stderr)[-4000:])
    assert json.loads(proc.stdout)["ok"]
