"""Finite-time suppression regressions, isolated from the pytest process."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest
import pytauargus


from conftest import DATA


@pytest.mark.parametrize("method", ["OPT", "MOD"])
def test_positive_time_limit_across_fresh_engines(method, tmp_path):
    """Accept a one-minute limit and protect cells on repeated fresh runs.

    This checks the previous nonzero-limit crash, not deadline expiry. A
    subprocess turns a native crash into a failure without killing pytest.
    """
    script = textwrap.dedent("""\
        import sys
        from pytauargus.batch import Suppress
        from pytauargus.engine import run_batch

        batch, method = sys.argv[1:]
        for _ in range(3):
            eng = run_batch(batch)
            tau = eng._tau
            _, ncell, _ = tau.get_total_table_size(0)
            before = [tau.get_table_cell_status(0, i)[1] for i in range(ncell)]
            assert any(status in range(3, 10) for status in before)
            limits = {method.lower() + "_max_time": 1}
            eng.suppress(Suppress(kind=method, tab_no=1, **limits))
            after = [tau.get_table_cell_status(0, i)[1] for i in range(ncell)]
            assert sum(s in (11, 12) for s in after) > sum(
                s in (11, 12) for s in before
            )
        """)
    env = os.environ.copy()
    # Use the same package as the parent, including in source/venv installs.
    package_root = str(Path(pytauargus.__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [package_root, env.get("PYTHONPATH")])
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(DATA / "TestRecode.arb"), method],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=90,
    )
    output = (result.stdout + result.stderr)[-6000:]
    assert result.returncode == 0, f"{method} exited {result.returncode}:\n{output}"


def test_opt_large_table_completes_with_time_limit(tmp_path):
    """OPT on a large table must terminate under a finite time limit.

    Regression: the per-solve simplex iteration cap (the legacy
    CPX_PARAM_ITERATIONS contract behind JJlpiterlimit) was a no-op in
    the HiGHS port, so on tables with >500 active cells/sums/
    protection levels the constructive phase drove thousands of
    degenerate min-cost-flow LPs to full optimality and OPT never
    finished (>45 min, unfinished, on the 12,798-cell tau_testW
    table). A 2-minute MaxComputingTime must bound the run at the
    B&B loop boundary and return the best suppression found so far.
    """
    batch = tmp_path / "large.arb"
    batch.write_text(textwrap.dedent(f"""\
        <OPENMICRODATA> "{DATA}/tau_testW.asc"
        <OPENMETADATA> "{DATA}/tau_testW.rda"
        <SPECIFYTABLE> "Size""Region"|"Var2"||
        <SAFETYRULE> NK(2,75)
        <SPECIFYTABLE> "Region""IndustryCode"|"Var2"||
        <SAFETYRULE> NK(2,75)
        <READMICRODATA>
        """))
    script = textwrap.dedent("""\
        import sys
        from pytauargus.batch import Suppress
        from pytauargus.engine import run_batch

        eng = run_batch(sys.argv[1])
        tau = eng._tau
        _, ncell, _ = tau.get_total_table_size(1)
        before = [tau.get_table_cell_status(1, i)[1] for i in range(ncell)]
        assert any(s in range(3, 10) for s in before)
        eng.suppress(Suppress(kind="OPT", tab_no=2, opt_max_time=2))
        after = [tau.get_table_cell_status(1, i)[1] for i in range(ncell)]
        assert sum(s in (11, 12) for s in after) > sum(
            s in (11, 12) for s in before
        )
        """)
    env = os.environ.copy()
    package_root = str(Path(pytauargus.__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [package_root, env.get("PYTHONPATH")])
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(batch)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=600,
    )
    output = (result.stdout + result.stderr)[-6000:]
    assert result.returncode == 0, f"OPT exited {result.returncode}:\n{output}"
