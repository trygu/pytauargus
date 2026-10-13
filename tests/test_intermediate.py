"""End-to-end test for the legacy WRITETABLE type-5 (INTERMEDIATE) audit file.

Builds a temp ``.arb`` from the sample batch (inserting a
``<WRITETABLE> (n, 5, ,"file")`` line for every table before
``<GOINTERACTIVE>``), runs the batch in a *fresh subprocess*, and asserts the
resulting audit file has one line per cell (row-major) with a consistent,
legacy-shaped column layout.

The batch is run in a single subprocess because the native HiGHS teardown is
non-deterministic when many solvers share one process (see ``test_cli.py`` /
``test_suppress.py``).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import DATA
SRC = Path(__file__).resolve().parent.parent / "src"
ARB = DATA / "TestRecode.arb"


def _build_arb_with_writetable(tmp_path: Path) -> Path:
    """Insert a WRITETABLE (type 5) line for every table before GOINTERACTIVE."""
    text = ARB.read_text(encoding="utf-8")
    assert "<GOINTERACTIVE>" in text
    # The sample batch has two tables.
    wt1 = '<WRITETABLE> (1, 5, ,"%s")' % (tmp_path / "int1.tab").as_posix()
    wt2 = '<WRITETABLE> (2, 5, ,"%s")' % (tmp_path / "int2.tab").as_posix()
    text = text.replace(
        "<GOINTERACTIVE>", wt1 + "\n" + wt2 + "\n<GOINTERACTIVE>", 1
    )
    # Data files are referenced relative to the .arb dir; the temp .arb lives in
    # tmp_path, so rewrite them to absolute paths under the real data dir.
    for name in ("tau_testW.asc", "tau_testW.rda"):
        text = text.replace('"' + name + '"', '"%s"' % (DATA / name).as_posix())
    arb = tmp_path / "test_intermediate.arb"
    arb.write_text(text, encoding="utf-8")
    return arb


def _run_batch_probe(arb: Path, tmp_path: Path, n_tables: int):
    """Run the batch once; return per-table (ncells, n_exp, topn) as JSON."""
    code = (
        "import json\n"
        "from pytauargus.engine import run_batch\n"
        f"eng = run_batch({str(arb)!r})\n"
        "out = []\n"
        "for i in range(eng._n_tables):\n"
        "    ncell, _ = eng._tau.get_total_table_size(i)\n"
        "    spec, srs = eng._tables[i]\n"
        "    n_exp = len(spec.exp_vars)\n"
        "    topn = eng._top_n_needed(srs, False)\n"
        "    out.append((ncell, n_exp, topn))\n"
        "print(json.dumps(out))\n"
    )
    env = dict(os.environ, PYTHONPATH=str(SRC) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
    )
    assert proc.returncode == 0, (
        f"rc={proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    return [tuple(x) for x in json.loads(proc.stdout.strip().splitlines()[-1])]


def test_write_intermediate(tmp_path):
    arb = _build_arb_with_writetable(tmp_path)
    per_table = _run_batch_probe(arb, tmp_path, 2)
    assert len(per_table) == 2, per_table

    for i, (ncell, n_exp, topn) in enumerate(per_table, start=1):
        fpath = tmp_path / f"int{i}.tab"
        assert fpath.is_file(), f"{fpath} missing"
        # Var2 is a VALUE response -> resp, freq, shadow, cost; non-holding,
        # non-simple -> topn maxscore columns; then status, lower, upper.
        n_cols = n_exp + 1 + 1 + 1 + 1 + topn + 1 + 1 + 1
        lines = fpath.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == ncell, (
            f"table {i}: expected {ncell} lines, got {len(lines)}"
        )
        for j, line in enumerate(lines):
            cols = line.split(";")
            assert len(cols) == n_cols, (
                f"table {i} line {j}: expected {n_cols} cols, got {len(cols)}:\n{line}"
            )
            # Status symbol is the 3rd token from the end.
            assert cols[-3] in {"S", "U", "P", "M", "E"}, f"bad status: {line}"


def test_csv_still_works(tmp_path):
    """Microdata CSV output is unchanged (no regression from the port)."""
    out = tmp_path / "micro.csv"
    code = (
        "from pytauargus.engine import run_batch, IDENTITY_DIM_SEQUENCE\n"
        f"eng = run_batch({str(ARB)!r})\n"
        f"eng._tau.write_csv(0, {str(out)!r}, True, IDENTITY_DIM_SEQUENCE, 1)\n"
        "print('ok')\n"
    )
    env = dict(os.environ, PYTHONPATH=str(SRC) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
    )
    assert proc.returncode == 0, (
        f"rc={proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    assert out.is_file() and out.stat().st_size > 0
