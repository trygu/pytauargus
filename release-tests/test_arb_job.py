"""The complete ex1 .arb job through the public API, installed package.

The batch stops at GOINTERACTIVE; suppression is then driven through the
public ``Engine.suppress`` API and the protection audit through the
native ``Engine.audit`` (no audit logic is reimplemented here). Native
runs are isolated in fresh subprocesses, mirroring tests/test_protection_
e2e.py; the subprocess must exit zero after full termination.

Validation is invariant-based: exact cell keys and response values from
the hand-derived expectations file, the preserved manual primary
suppression, and adequate protection in the audit. The secondary
suppression pattern itself is not pinned (alternative optima exist).
"""

import json
import subprocess
import sys

import pytest

from conftest import FIXTURE

TOL = 1e-6

PIPELINE_CODE = r"""
import json, shutil
from pathlib import Path

workdir = Path(__WORKDIR__)
method = __METHOD__
fixture = __FIXTURE__

from pytauargus.batch import Suppress
from pytauargus.engine import run_batch
from pytauargus.result import TableResult

fx = workdir / "fx"
shutil.rmtree(fx, ignore_errors=True)
shutil.copytree(fixture, fx)
engine = run_batch(fx / "ex1.arb")

def read(name):
    path = workdir / name
    engine.write_intermediate_table(0, str(path), simple=True)
    return TableResult(path, ["Sto", "Reg"], "Var2")

before = read("before.tab")
primary_idx = None
for i, row in enumerate(before.rows):
    if row["Sto"].strip() == "Stor" and row["Reg"].strip() == "Nord":
        primary_idx = i
assert primary_idx is not None, "the (Stor, Nord) cell is missing"

lpl, upl = engine._tau.get_table_cell_protection_levels(0, primary_idx)

# Negative control before suppression: the manual cell's value is fully
# recoverable from the published margins, so a skipped solver is visible.
initial = {r["cell"]: r for r in engine.audit(0)}[primary_idx]

engine.suppress(Suppress(kind=method, tab_no=1))
audited = {r["cell"]: r for r in engine.audit(0)}
protected = audited[primary_idx]

after = read("after.tab")

summary = {
    "before_rows": before.rows,
    "before_status": before.status(),
    "after_rows": after.rows,
    "after_status": after.status(),
    "primary_idx": primary_idx,
    "protection_levels": [lpl, upl],
    "initial": {k: initial[k] for k in ("value", "min", "max", "unsafe")},
    "protected": {k: protected[k] for k in ("value", "min", "max", "unsafe")},
    "audited_cells": sorted(audited),
    "any_unsafe_after": any(r["unsafe"] for r in audited.values()),
}
print(json.dumps(summary))
"""


def _run_pipeline(method, workdir):
    code = (PIPELINE_CODE
            .replace("__WORKDIR__", repr(str(workdir)))
            .replace("__METHOD__", repr(method))
            .replace("__FIXTURE__", repr(str(FIXTURE))))
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=180, cwd=str(workdir),
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"{method} pipeline exited {proc.returncode}:\n"
            + (proc.stdout + proc.stderr)[-6000:])
    return json.loads(proc.stdout)


def _cells(result, name):
    """(row, col) -> (response, status) from a pipeline summary section."""
    out = {}
    for i, row in enumerate(result[name + "_rows"]):
        key = (row["Sto"].strip(), row["Reg"].strip())
        out[key] = (row["Var2"], result[name + "_status"][i])
    return out


@pytest.mark.parametrize("method", ["OPT", "MOD"])
def test_pipeline(method, expectations, tmp_path):
    workdir = tmp_path / "run"
    workdir.mkdir()
    summary = _run_pipeline(method, workdir)
    cells, primaries = expectations

    # --- output files exist -------------------------------------------------
    for name in ("before.tab", "after.tab"):
        assert (workdir / name).is_file(), f"{name} was not written"

    # --- cell keys and response values (before suppression) -----------------
    before = _cells(summary, "before")
    assert set(before) == set(cells), (
        f"cell keys differ: missing={set(cells) - set(before)} "
        f"extra={set(before) - set(cells)}")
    for key, exp in cells.items():
        got_resp, got_status = before[key]
        assert got_resp == pytest.approx(exp["response"], abs=TOL), (
            f"{key}: response {got_resp} != expected {exp['response']}")
        assert got_status == exp["status"], (
            f"{key}: status {got_status} != expected {exp['status']}")

    # --- the manual primary suppression and its requirement -----------------
    (row, col, response, required) = primaries[0]
    lpl, upl = summary["protection_levels"]
    assert (lpl, upl) == pytest.approx((required, required)), (
        f"protection levels {(lpl, upl)} != ({required}, {required})")
    assert summary["before_rows"][summary["primary_idx"]]["Var2"] == \
        pytest.approx(response, abs=TOL)

    # negative control: before suppression the value is exactly recoverable
    init = summary["initial"]
    assert init["unsafe"]
    assert init["value"] == pytest.approx(response, abs=TOL)
    assert init["min"] == pytest.approx(response, abs=TOL)
    assert init["max"] == pytest.approx(response, abs=TOL)

    # --- after suppression: primary kept, adequately protected --------------
    protected = summary["protected"]
    assert protected["value"] == pytest.approx(response, abs=TOL)
    assert protected["value"] - protected["min"] >= required - TOL
    assert protected["max"] - protected["value"] >= required - TOL
    assert not protected["unsafe"], \
        "the primary cell is still unprotected after suppression"

    after = _cells(summary, "after")
    after_status = {k: v[1] for k, v in after.items()}
    assert after_status[(row, col)] == "U", \
        "the manual primary suppression was not preserved"
    withheld = {i for i, s in enumerate(summary["after_status"])
                if s in ("U", "M")}
    secondary = withheld - {summary["primary_idx"]}
    assert secondary, "secondary suppression was expected"
    assert set(summary["audited_cells"]) == withheld, \
        "every withheld cell must be audited"
    assert not summary["any_unsafe_after"], \
        "the audit found an unprotected withheld cell"

    # suppression preserves the original response values
    for key, exp in cells.items():
        assert after[key][0] == pytest.approx(exp["response"], abs=TOL), (
            f"{key}: response changed by suppression")
