"""Tests for the headless command line interface (:mod:`pytauargus.cli`).

The native engine is fragile when many solvers are created in a single process
(the HiGHS LP teardown can segfault — see ``test_suppress.py``). So every CLI
command that drives the native engine is run in a *fresh subprocess* (one
engine per process), mirroring how the suppression tests isolate themselves.
Pure-Python commands (version / help / bad args) run in-process.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from pytauargus.cli import main

from conftest import DATA
ARB = DATA / "TestRecode.arb"
ASC = DATA / "tau_testW.asc"
RDA = DATA / "tau_testW.rda"
SRC = Path(__file__).resolve().parent.parent / "src"


def _run_cli(argv):
    """Run ``pytauargus.cli.main(argv)`` in a fresh subprocess; return the proc.

    A separate process gives the native engine a clean solver/thread state,
    which is required because several solvers in one process can crash. The
    ``src`` package (Python + compiled extension) is put on PYTHONPATH since
    it is not installed in the venv for the test run.
    """
    code = (
        "from pytauargus.cli import main;"
        f"import json,sys;sys.exit(main(json.loads({json.dumps(argv)!r})) or 0)"
    )
    env = dict(os.environ, PYTHONPATH=str(SRC) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(Path(__file__).resolve().parent),
    )
    return proc


# ===========================================================================
# Pure-Python commands (no native engine) — in process
# ===========================================================================
def test_version_returns_zero():
    assert main(["version"]) == 0


def test_version_flag():
    assert main(["--version"]) == 0


def test_no_args_returns_zero():
    assert main([]) == 0


def test_missing_batch_file_returns_two():
    assert main(["run", str(DATA / "does_not_exist.arb")]) == 2


def test_unknown_suppress_method_exits():
    import pytest

    # "NOPE" is not a valid --method choice; argparse exits 2 (in process,
    # no native engine is started).
    with pytest.raises(SystemExit):
        main(["suppress", str(ARB), "--method", "NOPE", "--tab", "1"])


def test_specify_requires_table():
    import pytest

    with pytest.raises(SystemExit):
        main(["specify", str(ASC), str(RDA)])


# ===========================================================================
# Native-driven commands — one subprocess each
# ===========================================================================
def test_run_succeeds():
    assert _run_cli(["run", str(ARB)]).returncode == 0


def test_explore_lists_variables():
    p = _run_cli(["explore", str(ARB)])
    assert p.returncode == 0
    assert "variables: 14" in p.stdout
    assert "Year" in p.stdout
    assert "Var2" in p.stdout


def test_compute_reports_cell_counts():
    p = _run_cli(["compute", str(ARB)])
    assert p.returncode == 0
    assert "table 1:" in p.stdout
    assert "table 2:" in p.stdout
    assert "cells" in p.stdout


def test_tables_summary():
    p = _run_cli(["tables", str(ARB)])
    assert p.returncode == 0
    assert "Size, Region | Var2" in p.stdout
    assert "Region, IndustryCode | Var2" in p.stdout


def test_audit_reports_intervals():
    p = _run_cli(["audit", str(ARB)])
    assert p.returncode == 0
    # Safety-rule unsafe (u) cells are audited per table with realized bounds.
    assert "cells audited" in p.stdout
    assert "under-protected" in p.stdout


def test_save_csv(tmp_path):
    out = tmp_path / "out.csv"
    p = _run_cli(["save", str(ARB), "--format", "csv", "--out", str(out)])
    assert p.returncode == 0
    assert (tmp_path / "out_1.csv").is_file()
    assert (tmp_path / "out_2.csv").is_file()
    assert (tmp_path / "out_1.csv").stat().st_size > 0


def test_specify_adhoc():
    p = _run_cli(
        [
            "specify",
            str(ASC),
            str(RDA),
            "--table", '"Size""Region"|"Var2"||',
            "--safety", "NK(2,75)",
        ]
    )
    assert p.returncode == 0


def test_suppress_dispatch(monkeypatch):
    """``suppress`` should dispatch to ``Engine.suppress`` with the right args.

    The native HiGHS LP teardown is non-deterministic (~40% segfault, engine
    bug — see ``test_suppress.py``), so the solver is stubbed here to test the
    CLI's dispatch deterministically.
    """
    import pytauargus.cli as cli

    captured = {}

    class StubEngine:
        _n_tables = 2
        _tables = [(object(), None), (object(), None)]

        def suppress(self, cmd):
            self.calls = [(cmd.kind, cmd.tab_no)]

    def factory(_p):
        e = StubEngine()
        captured["eng"] = e
        return e

    monkeypatch.setattr(cli, "run_batch", factory)
    assert cli.main(["suppress", str(ARB), "--method", "MOD", "--tab", "1"]) == 0
    assert captured["eng"].calls == [("MOD", 1)]


def test_suppress_bad_table_number(monkeypatch):
    import pytauargus.cli as cli

    class StubEngine:
        _n_tables = 2

    monkeypatch.setattr(cli, "run_batch", lambda _p: StubEngine())
    assert cli.main(["suppress", str(ARB), "--method", "MOD", "--tab", "99"]) == 2


def test_round_dispatch(monkeypatch):
    """``round`` should dispatch RND to ``Engine.suppress`` with a base."""
    import pytauargus.cli as cli

    class StubEngine:
        _n_tables = 2
        _tables = [(object(), None), (object(), None)]

        def _min_round_base(self, _t):
            return 100

        def suppress(self, cmd):
            self.calls.append((cmd.kind, cmd.tab_no, cmd.rnd_base))

        calls = []

    eng_holder = {}

    def factory(_p):
        e = StubEngine()
        eng_holder["eng"] = e
        return e

    monkeypatch.setattr(cli, "run_batch", factory)
    assert cli.main(["round", str(ARB), "--tab", "1"]) == 0
    assert eng_holder["eng"].calls == [("RND", 1, 100)]
