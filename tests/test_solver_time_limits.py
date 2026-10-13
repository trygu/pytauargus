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
            ncell, _ = tau.get_total_table_size(0)
            before = [tau.get_table_cell_status(0, i) for i in range(ncell)]
            assert any(status in range(3, 10) for status in before)
            limits = {method.lower() + "_max_time": 1}
            eng.suppress(Suppress(kind=method, tab_no=1, **limits))
            after = [tau.get_table_cell_status(0, i) for i in range(ncell)]
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
