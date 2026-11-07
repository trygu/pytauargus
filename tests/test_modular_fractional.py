"""Run Øyvind's aggregated Arrow table through the Python batch frontend.

Lockstep twin of the R regression
(``rtauargus.native`` tests/testthat/test_engine_modular_fractional.R): the
same SSB hierarchical table, the same ``MOD(1,5,0,0,0)`` suppression, the
same 429 a priori secrets.

``input.parquet`` is the original Arrow export. The other fixtures
(``input.tab`` / ``input.rda`` / ``input.hst`` / ``hier*.hrc``) were written
by ``rtauargus.native`` from that same Parquet, so the native batch consumes
the exact table input the R test uses -- no R runtime is needed here.

The native run happens in a fresh subprocess: this is a crash regression.
A native abort or solver hang must fail only this test, not take down the
whole suite.

The engine's CSP exit-propagation fix (the separation error path that
could spin in a MAX_TIME-ignoring retry loop on this fractional table)
landed in the pinned engine, so the native run completes and is bounded
on every platform and the test runs ungated.
"""

import csv
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
FIXTURE = Path(__file__).parent / "data" / "modular"


def _run_batch(code: str, workdir: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC) + os.pathsep
               + os.environ.get("PYTHONPATH", ""))
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=workdir, env=env, capture_output=True, text=True, timeout=600,
    )


def test_modular_fractional_fixtures():
    """The tabular fixtures are the fractional Arrow data, not a control.

    Runs on every platform: pure Python, no engine.
    """
    pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    table = pq.read_table(FIXTURE / "input.parquet")
    assert table.num_rows == 1653
    assert sum(table.column("primary").to_pylist()) == 429

    # The .tab is the R fixed-decimal writer's text view of this Parquet:
    # same rows and frequencies, and fractional (not a rounded integer).
    rows = list(csv.reader((FIXTURE / "input.tab").open()))
    assert len(rows) == table.num_rows
    assert [int(row[3]) for row in rows] == table.column("n_contr").to_pylist()
    assert abs(float(rows[0][2]) - table.column("response")[0].as_py()) < 1e-6
    assert "." in rows[0][2]


def test_modular_fractional_batch(tmp_path):
    pytest.importorskip("pyarrow")

    # Point the metadata's hierarchy lists at the local copies.
    rda = (FIXTURE / "input.rda").read_text().replace(
        '"hier1.hrc"', f'"{tmp_path / "hier1.hrc"}"'
    ).replace('"hier2.hrc"', f'"{tmp_path / "hier2.hrc"}"')
    (tmp_path / "input.rda").write_text(rda)
    for name in ("input.tab", "input.hst", "hier1.hrc", "hier2.hrc"):
        (tmp_path / name).write_bytes((FIXTURE / name).read_bytes())

    batch = tmp_path / "input.arb"
    batch.write_text(
        '<OPENTABLEDATA> "input.tab"\n'
        '<OPENMETADATA> "input.rda"\n'
        '<SPECIFYTABLE> "var1""var2"|"response"||\n'
        '<SAFETYRULE> MAN(10)\n'
        '<READTABLE> 1\n'
        '<APRIORI> "input.hst",1,",",0,0\n'
        '<SUPPRESS> MOD(1,5,0,0,0)\n'
        '<WRITETABLE> (1,4,,"result.csv")\n'
    )

    # The cap is a hang guard for the CSP retry loop, not a perf budget:
    # a healthy Windows run finishes in a few minutes.
    proc = _run_batch(
        "from pytauargus.engine import run_batch; "
        f"run_batch({str(batch)!r})",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr

    with (tmp_path / "result.csv").open() as output:
        result = list(csv.DictReader(output))
    assert len(result) == 1653
    statuses = Counter(row["Status"] for row in result)
    # The 429 a priori secrets stay secret; the remaining 1224 split into
    # visible and secondary, and suppression actually suppresses some.
    assert statuses["B"] == 429
    assert statuses["D"] >= 100
    assert statuses["D"] + statuses["V"] == 1224
