"""Suppression, feasibility audit and masked CSV for two documented examples.

The manually unsafe (Stor, Nord) cell is 150. The legacy default MAN margin
is 20%, so its lower and upper protection requirements are both 30.
Published margins initially determine the cell exactly. After OPT or MOD,
its feasible interval must extend at least 30 in each direction.

The dominance case adapts GaussSuppression's public magnitude example.
These are regression contracts for two tables, not general SDC certification.
Do not pin a secondary suppression pattern: equally valid patterns can differ
between solver versions. Native runs are isolated in fresh subprocesses.
"""

import csv
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import pytauargus

from pytauargus.batch import Suppress
from pytauargus.engine import run_batch
from pytauargus import protect
from pytauargus.result import TableResult


from conftest import DATA as _DATA

EXAMPLE = _DATA / "examples" / "ex1"
MARKER = "x"
TOL = 1e-6

# Adapted from GaussSuppression's "Magnitude table suppression", basic
# dominance example (Statistics Norway). Values are multiplied by ten to
# make exact integer input; each row is an independent contribution, as in
# that example. No company IDs or hierarchies are carried over.
# https://statisticsnorway.github.io/ssb-gausssuppression/articles/Magnitude_table_suppression.html
DOMINANCE_INPUT = [
    ("Agriculture", "Portugal", 759), ("Agriculture", "Portugal", 245),
    ("Agriculture", "Spain", 966), ("Agriculture", "Spain", 432),
    ("Entertainment", "Iceland", 168),
    ("Entertainment", "Portugal", 71), ("Entertainment", "Portugal", 23),
    ("Entertainment", "Spain", 774), ("Entertainment", "Spain", 115),
    ("Entertainment", "Spain", 164),
    ("Governmental", "Portugal", 216), ("Governmental", "Portugal", 20),
    ("Governmental", "Spain", 65), ("Governmental", "Spain", 27),
    ("Industry", "Iceland", 96), ("Industry", "Iceland", 88),
    ("Industry", "Iceland", 19),
    ("Industry", "Portugal", 257), ("Industry", "Portugal", 34),
    ("Industry", "Spain", 84),
]


def _run_dominance(method, workdir):
    data = {"Sector": [r[0] for r in DOMINANCE_INPUT],
            "Geo": [r[1] for r in DOMINANCE_INPUT],
            "Value": [r[2] for r in DOMINANCE_INPUT]}
    outcome = protect(data, ["Sector", "Geo"], response="Value",
                      safety_rules="NK(1,80)", suppress=f"{method}(1)",
                      decimals=0, workdir=str(workdir))
    result = outcome.tables[0]
    expected = {("Entertainment", "Iceland"), ("Governmental", "Portugal"),
                ("Industry", "Portugal"), ("Industry", "Spain")}
    primary = {i for i, status in enumerate(result.status()) if status == "U"}
    actual = {(result.rows[i]["Sector"].strip(), result.rows[i]["Geo"].strip())
              for i in primary}
    assert actual == expected, "largest contribution exceeds 80% of these cells"
    total = next(row for row in result.rows
                 if (row["Sector"].strip(), row["Geo"].strip()) == ("Total", "Total"))
    assert total["Value"] == 4623
    audit = {row["cell"]: row for row in outcome.engine.audit(0)}
    for i in primary:
        row = audit[i]
        lpl, upl = outcome.engine._tau.get_table_cell_protection_levels(0, i)
        assert lpl > 0 and upl > 0
        assert row["value"] - row["min"] >= lpl - TOL
        assert row["max"] - row["value"] >= upl - TOL
        assert not row["unsafe"]
    statuses = result.status()
    withheld = {i for i, status in enumerate(statuses) if status in ("U", "M")}
    assert any(status == "M" for status in statuses)
    assert set(audit) == withheld
    _check_publication(result, workdir, ["Sector", "Geo"], "Value")


def _run_pipeline(method, workdir):
    # Keep fixture inputs and all generated outputs inside the test directory.
    inputs = workdir / "inputs"
    shutil.copytree(EXAMPLE, inputs)
    engine = run_batch(inputs / "ex1.arb")

    def read_result(name):
        path = workdir / name
        engine.write_intermediate_table(0, str(path), simple=True)
        return TableResult(path, ["Sto", "Reg"], "Var2")

    before = read_result("before.tab")
    primary = next(i for i, row in enumerate(before.rows)
                   if (row["Sto"].strip(), row["Reg"].strip()) == ("Stor", "Nord"))
    assert before.status()[primary] == "U"
    assert before.unsafe()[primary] == pytest.approx(150)
    required = 30.0  # 20% of 150, the legacy default manual margin.
    lpl, upl = engine._tau.get_table_cell_protection_levels(0, primary)
    assert (lpl, upl) == pytest.approx((required, required))

    # Negative control: suppressing only this cell leaves its value recoverable
    # from the published margins. This makes a skipped solver detectable.
    initial_audit = {row["cell"]: row for row in engine.audit(0)}
    initial = initial_audit[primary]
    assert initial["unsafe"]
    assert initial["min"] == pytest.approx(150, abs=TOL)
    assert initial["max"] == pytest.approx(150, abs=TOL)

    engine.suppress(Suppress(kind=method, tab_no=1))
    audited = {row["cell"]: row for row in engine.audit(0)}
    protected = audited[primary]
    assert protected["value"] == pytest.approx(150)
    assert protected["value"] - protected["min"] >= required - TOL
    assert protected["max"] - protected["value"] >= required - TOL
    assert not protected["unsafe"]

    result = read_result("protected.tab")
    statuses = result.status()
    withheld = {i for i, status in enumerate(statuses) if status in ("U", "M")}
    assert primary in withheld
    assert any(status == "M" for status in statuses)
    assert set(audited) == withheld, "every withheld cell must be audited"
    assert not any(row["unsafe"] for row in audited.values())
    assert result.unsafe() == before.unsafe(), "suppression preserves originals"

    _check_publication(result, workdir, ["Sto", "Reg"], "Var2")


def _check_publication(result, workdir, dimensions, response):
    withheld = {i for i, status in enumerate(result.status()) if status in ("U", "M")}
    published = result.safe(MARKER)
    originals = result.unsafe()
    assert {i for i, value in enumerate(published) if value == MARKER} == withheld
    for i, value in enumerate(published):
        if i not in withheld:
            assert value == originals[i]

    # Write only dimensions and masked values. Original responses and the
    # diagnostic columns must not be included in the release CSV.
    release = workdir / "published.csv"
    columns = dimensions + [response]
    with release.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows({**{name: row[name] for name in dimensions}, response: value}
                         for row, value in zip(result.rows, published))
    with release.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == columns
        released = list(reader)
    assert len(released) == len(result.rows)
    assert {i for i, row in enumerate(released) if row[response] == MARKER} == withheld
    for i, row in enumerate(released):
        for name in dimensions:
            assert row[name] == result.rows[i][name]
        if i not in withheld:
            assert float(row[response]) == pytest.approx(originals[i])


@pytest.mark.parametrize("method", ["OPT", "MOD"])
@pytest.mark.parametrize("scenario", ["manual", "dominance"])
def test_suppression_audit_and_publication(method, scenario, tmp_path):
    package_root = str(Path(pytauargus.__file__).resolve().parent.parent)
    env = dict(os.environ, PYTHONPATH=package_root)
    script = (
        "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
        "from test_protection_e2e import _run_pipeline, _run_dominance; "
        "run = _run_pipeline if sys.argv[4] == 'manual' else _run_dominance; "
        "run(sys.argv[2], Path(sys.argv[3]))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, str(Path(__file__).parent), method, str(tmp_path), scenario],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=90,
    )
    assert proc.returncode == 0, (
        f"{method} pipeline exited {proc.returncode}:\n"
        + (proc.stdout + proc.stderr)[-6000:]
    )
