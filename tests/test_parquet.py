"""Parquet I/O for the high-level ``protect()`` pipeline (optional dependency).

Skipped when pyarrow is absent (``uv sync --extra parquet``). Native runs
are isolated in fresh subprocesses, like ``test_protect.py``.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pyarrow")
import pyarrow.parquet as pq

SRC = Path(__file__).resolve().parent.parent / "src"
# Checked-in fixture (see data/README.md): eight records, Sector x Geo.
# NK(1,80) makes (Alpha, East) the only primary unsafe cell: 90/100 = 90%
# > 80%. Every other cell and margin has its largest contributor under 80%
# (worst margin: Total|East at 90/130 = 69%).
from conftest import DATA as _DATA

MICRO_PQ = _DATA / "examples" / "parquet" / "micro.parquet"
TOTAL = 200.0


def _run_code(code: str, workdir: Path):
    env = dict(os.environ, PYTHONPATH=str(SRC) + os.pathsep
               + os.environ.get("PYTHONPATH", ""))
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=workdir, env=env, capture_output=True, text=True, timeout=120,
    )
    return proc


# --- TableResult.to_parquet (pure Python, no engine) -----------------------

def test_to_parquet_masks_withheld_cells(tmp_path):
    from pytauargus.result import TableResult

    rows = [
        {"Sector": "Alpha", "Geo": "East", "Value": 100.0, "status": "U"},
        {"Sector": "Alpha", "Geo": "West", "Value": 10.0, "status": "S"},
        {"Sector": "Beta", "Geo": "East", "Value": 50.0, "status": "M"},
        {"Sector": "Beta", "Geo": "West", "Value": 50.0, "status": "S"},
    ]
    result = TableResult.from_rows(rows, ["Sector", "Geo"], "Value")
    out = tmp_path / "protected.parquet"
    result.to_parquet(out)

    table = pq.read_table(out)
    assert table.column_names == ["Sector", "Geo", "Value"]
    got = table.to_pylist()
    assert len(got) == 4
    # withheld cells (U, M) are null, published values are retained
    assert got[0]["Value"] is None
    assert got[1]["Value"] == pytest.approx(10.0)
    assert got[2]["Value"] is None
    assert got[3]["Value"] == pytest.approx(50.0)
    assert [r["Sector"] for r in got] == ["Alpha", "Alpha", "Beta", "Beta"]
    assert [r["Geo"] for r in got] == ["East", "West", "East", "West"]


# --- protect() accepts a .parquet path (subprocess, native engine) ---------

def test_protect_accepts_parquet_path(tmp_path):
    micro = MICRO_PQ
    code = (
        "import json,sys;"
        f"micro={str(micro)!r}; workdir={str(tmp_path)!r};"
        "from pytauargus import protect;"
        "out=protect(micro, ['Sector','Geo'], response='Value',"
        " safety_rules='NK(1,80)', suppress='OPT(1)', workdir=workdir);"
        "t=out.tables[0];"
        "prim={i for i,s in enumerate(t.status()) if s=='U'};"
        "rows=t.rows;"
        "primary={rows[i]['Sector'].strip()+'|'+rows[i]['Geo'].strip()"
        " for i in prim};"
        "total=next(r['Value'] for r in rows"
        " if r['Sector'].strip()=='Total' and r['Geo'].strip()=='Total');"
        "print(json.dumps({'primary': sorted(primary), 'total': total}))"
    )
    proc = _run_code(code, tmp_path)
    assert proc.returncode == 0, (
        f"protect(parquet) subprocess failed (rc={proc.returncode}):\n"
        + (proc.stdout + proc.stderr)[-4000:])
    out = json.loads(proc.stdout)
    assert out["primary"] == ["Alpha|East"]
    assert out["total"] == pytest.approx(TOTAL)


# --- CLI protect verb, parquet in and out (subprocess) ---------------------

def test_cli_protect_parquet_roundtrip(tmp_path):
    micro = MICRO_PQ
    out_file = tmp_path / "protected.parquet"
    argv = ["protect", str(micro), "--tables", "Sector,Geo",
            "--response", "Value", "--safety", "NK(1,80)",
            "--suppress", "OPT(1)", "--out", str(out_file)]
    code = (
        "from pytauargus.cli import main;"
        f"import json,sys;sys.exit(main(json.loads({json.dumps(argv)!r})) or 0)"
    )
    proc = _run_code(code, tmp_path)
    assert proc.returncode == 0, (
        f"tauargus protect failed (rc={proc.returncode}):\n"
        + (proc.stdout + proc.stderr)[-4000:])
    assert out_file.exists()
    table = pq.read_table(out_file)
    assert table.column_names == ["Sector", "Geo", "Value"]
    got = table.to_pylist()
    # the protected (Alpha, East) cell is masked, its margins keep the total
    primary = next(r for r in got
                   if r["Sector"] == "Alpha" and r["Geo"] == "East")
    assert primary["Value"] is None
    total = next(r for r in got if r["Sector"] == "Total" and r["Geo"] == "Total")
    assert total["Value"] == pytest.approx(TOTAL)
    withheld = [r for r in got if r["Value"] is None]
    assert len(withheld) >= 1  # primary plus any secondary suppression
