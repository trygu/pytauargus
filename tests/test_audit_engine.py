"""Engine-level audit tests: Engine.audit, +AR intermediate columns, CLI.

Legacy contract (OptiSuppress.RunAudit, TableSet.java:1373-1387): after
secondary suppression, the audit (external ``intervalle.exe`` in legacy,
native ``audit_jj`` here) solves two LPs per suppressed cell and stores the
realized feasibility bounds on the cell.  Intermediate (type-5) output with
the ``+AR`` option appends six columns:

    ;rlower;rupper;(rupper-rlower)   at the table's nDec
    ;0;0;0                            if response==0 or safe-not-protected
                                      (status in {1, 2, 13})
    ;p1;p2;p3                         otherwise: two-decimal percentages
                                      100*(response-rlower)/response,
                                      100*(rupper-response)/response,
                                      100*(rupper-rlower)/response

The batch grammar carries the flag as ``<WRITETABLE> (1, 5, AR+,"file")``.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pytauargus.batch import Apriory, Suppress
from pytauargus.engine import (
    CS_EMPTY_NONSTRUCT,
    CS_SAFE,
    CS_SAFE_MANUAL,
    _status_symbol,
    run_batch,
)

from conftest import DATA
SRC = Path(__file__).resolve().parent.parent / "src"
ARB = DATA / "TestRecode.arb"


@pytest.fixture()
def engine():
    """Fresh computed tables per test (suppression + audit mutate state)."""
    return run_batch(DATA / "TestRecode.arb")


# ===================================================================
# Helpers
# ===================================================================

def count_unsafe(tau, tab):
    ncell, _ = tau.get_total_table_size(tab)
    return sum(
        1 for i in range(ncell)
        if tau.get_table_cell_status(tab, i) in (3, 4, 5, 6, 7, 8, 9)
    )


def _cell_info(engine, tab):
    """cell idx -> (resp, status, rlower, rupper) over the full table."""
    tau = engine._tau
    spec, _ = engine._tables[tab]
    exp = [engine._metadata.index_of(n) for n in spec.exp_vars]
    max_dim = []
    for vi in exp:
        n_codes, n_active = tau.get_var_number_of_codes(vi)
        max_dim.append(n_active)
    out = {}
    dim = [0] * len(exp)
    idx = 0
    while True:
        res = tau.get_table_cell(tab, dim, 0)
        if len(res) > 2:
            out[idx] = (res[1], res[10], res[-2], res[-1])
        idx += 1
        k = len(dim) - 1
        while k >= 0:
            dim[k] += 1
            if dim[k] < max_dim[k]:
                break
            dim[k] = 0
            k -= 1
        if k == -1:
            break
    return out


def _run_cli(argv):
    """Run the CLI in a fresh subprocess (clean native solver state)."""
    code = (
        "from pytauargus.cli import main;"
        f"import json,sys;sys.exit(main(json.loads({json.dumps(argv)!r})) or 0)"
    )
    env = dict(os.environ, PYTHONPATH=str(SRC) + os.pathsep
               + os.environ.get("PYTHONPATH", ""))
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env=env,
        cwd=str(Path(__file__).resolve().parent),
    )
    return proc


def _build_arb_no_unsafe(tmp_path: Path) -> Path:
    """Tabular-input batch whose file has no unsafe cells.

    A copy of ``data/tableinput`` with every ``U`` status in the table file
    rewritten to ``S`` — the tabular flow reads statuses from the file, so
    the result is a fully-safe table with nothing for the audit to solve.
    """
    src = DATA / "tableinput"
    (tmp_path / "pp.rda").write_bytes((src / "pp.rda").read_bytes())
    (tmp_path / "region.cdl").write_bytes((src / "region.cdl").read_bytes())
    (tmp_path / "region2.hrc").write_bytes((src / "region2.hrc").read_bytes())
    rows = (src / "pp.tab").read_text(encoding="utf-8").splitlines()
    out = []
    n_u = 0
    for line in rows:
        parts = line.split(";")
        if parts[-3] == "U":
            parts[-3] = "S"
            # drop the protection levels (no longer meaningful for a safe cell)
            parts[-2], parts[-1] = "0,00", "0,00"
            n_u += 1
        out.append(";".join(parts))
    assert n_u > 0, "expected unsafe cells in the source table file"
    (tmp_path / "pp_safe.tab").write_text("\n".join(out) + "\n",
                                          encoding="utf-8")
    arb = (tmp_path / "audit_none.arb")
    arb.write_text(
        f'<OPENTABLEDATA>  "{tmp_path / "pp_safe.tab"}"\n'
        f'<OPENMETADATA>   "{tmp_path / "pp.rda"}"\n'
        '<SPECIFYTABLE>   "Size""Region"|"Var2"|"Var2_Shadow"|"Var2"\n'
        '<READTABLE> 1T\n'
        '<GOINTERACTIVE>\n',
        encoding="utf-8",
    )
    return arb


def _build_arb_with_suppress_ar(tmp_path: Path) -> Path:
    """Insert ``<SUPPRESS> MOD(1)`` + ``<WRITETABLE> (1, 5, AR+,...)``."""
    text = ARB.read_text(encoding="utf-8")
    assert "<GOINTERACTIVE>" in text
    wt = '<WRITETABLE> (1, 5, AR+,"%s")' % (tmp_path / "int_ar.tab").as_posix()
    text = text.replace(
        "<GOINTERACTIVE>", f"<SUPPRESS> MOD(1)\n{wt}\n<GOINTERACTIVE>", 1
    )
    for name in ("tau_testW.asc", "tau_testW.rda"):
        text = text.replace('"' + name + '"', '"%s"' % (DATA / name).as_posix())
    arb = tmp_path / "audit_ar.arb"
    arb.write_text(text, encoding="utf-8")
    return arb


# ===================================================================
# Engine.audit
# ===================================================================

class TestEngineAudit:
    def test_audit_sets_realized_bounds(self, engine):
        """Engine.audit stores intervals that round-trip via GetTableCell."""
        tau = engine._tau
        tab = 0
        assert count_unsafe(tau, tab) > 0, "Test requires an unsafe cell"
        engine.suppress(Suppress(kind="MOD", tab_no=tab + 1))

        rows = engine.audit(tab)
        assert rows, "MOD-suppressed table must have audited cells"
        for r in rows:
            assert r["status"] in ("u", "m")
            # The published value must be feasible for the cell's LP.
            assert r["min"] <= r["value"] + 1e-6
            assert r["value"] <= r["max"] + 1e-6
        # The fixed fixture has a non-degenerate interval.
        assert any(r["max"] > r["min"] for r in rows)

        # Realized bounds round-trip through the native table.
        info = _cell_info(engine, tab)
        for r in rows:
            _resp, _st, rlo, rup = info[r["cell"]]
            assert abs(rlo - r["min"]) < 1e-9, f"cell {r['cell']} lower"
            assert abs(rup - r["max"]) < 1e-9, f"cell {r['cell']} upper"

    def test_audit_no_suppressed_cells(self, engine, tmp_path):
        """A table with no u/m cells audits to zero rows.

        The NK(2,75) safety rule leaves primary-unsafe cells even in a
        compute-only batch, so first neutralize them all via apriori
        (manual 2.15's workflow: set a cell's status to safe) and check the
        audit finds nothing to solve.
        """
        tau = engine._tau
        tab = 0
        spec, _ = engine._tables[tab]
        vi = [engine._metadata.index_of(n) for n in spec.exp_vars]
        n0 = tau.get_var_number_of_codes(vi[0])[1]
        n1 = tau.get_var_number_of_codes(vi[1])[1]
        lines = []
        for d0 in range(n0):
            for d1 in range(n1):
                res = tau.get_table_cell(tab, [d0, d1], 0)
                st = res[10]
                if st in (3, 4, 5, 6, 7, 8, 9):
                    c0 = tau.get_var_code(vi[0], d0)[2].strip()
                    c1 = tau.get_var_code(vi[1], d1)[2].strip()
                    lines.append(f"{c0};{c1};S")
        assert lines, "test requires primary-unsafe cells to neutralize"
        p = tmp_path / "ap.txt"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        engine.apply_apriori(Apriory(file=str(p), tab_no=tab + 1,
                                     separator=";"))

        assert engine.audit(tab) == []


# ===================================================================
# +AR intermediate columns (in-process writer)
# ===================================================================

class TestIntermediatePlusAR:
    def test_with_audit_columns(self, engine, tmp_path):
        """+AR appends 6 columns with legacy semantics and values."""
        tau = engine._tau
        tab = 0
        assert count_unsafe(tau, tab) > 0, "Test requires an unsafe cell"
        engine.suppress(Suppress(kind="MOD", tab_no=tab + 1))
        rows = engine.audit(tab)
        assert rows

        spec, srs = engine._tables[tab]
        n_exp = len(spec.exp_vars)
        resp_var = engine._metadata.variables[
            engine._metadata.index_of(spec.resp_var)]
        n_dec = resp_var.n_decimals
        topn = engine._top_n_needed(srs, False)
        ncell, _ = tau.get_total_table_size(tab)
        n_cols = n_exp + 1 + 1 + 1 + 1 + topn + 1 + 1 + 1 + 6

        out = tmp_path / "int_ar.tab"
        engine.write_intermediate_table(tab, str(out), with_audit=True)
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == ncell

        info = _cell_info(engine, tab)
        for j, line in enumerate(lines):
            cols = line.split(";")
            assert len(cols) == n_cols, (
                f"line {j}: expected {n_cols} cols, got {len(cols)}:\n{line}"
            )
            resp, status, rlo, rup = info[j]
            assert cols[-9] == _status_symbol(status), line
            assert cols[-6] == f"{rlo:.{n_dec}f}", line
            assert cols[-5] == f"{rup:.{n_dec}f}", line
            assert cols[-4] == f"{rup - rlo:.{n_dec}f}", line
            if resp == 0 or status in (CS_SAFE, CS_SAFE_MANUAL, CS_EMPTY_NONSTRUCT):
                assert cols[-3:] == ["0", "0", "0"], line
            else:
                assert abs(float(cols[-3]) - 100 * (resp - rlo) / resp) <= 0.01
                assert abs(float(cols[-2]) - 100 * (rup - resp) / resp) <= 0.01
                assert abs(float(cols[-1]) - 100 * (rup - rlo) / resp) <= 0.01

    def test_without_audit_unchanged(self, engine, tmp_path):
        """Default output has no audit columns (no regression)."""
        tau = engine._tau
        tab = 0
        engine.suppress(Suppress(kind="MOD", tab_no=tab + 1))

        spec, srs = engine._tables[tab]
        n_exp = len(spec.exp_vars)
        topn = engine._top_n_needed(srs, False)
        ncell, _ = tau.get_total_table_size(tab)
        n_cols = n_exp + 1 + 1 + 1 + 1 + topn + 1 + 1 + 1

        out = tmp_path / "int_plain.tab"
        engine.write_intermediate_table(tab, str(out))
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == ncell
        for line in lines:
            assert len(line.split(";")) == n_cols, line


# ===================================================================
# Batch +AR wiring (fresh subprocess: legacy grammar end-to-end)
# ===================================================================

def test_writetable_ar_option(tmp_path):
    """``<WRITETABLE> (1, 5, AR+,...)`` audits then writes the +AR columns."""
    arb = _build_arb_with_suppress_ar(tmp_path)
    code = (
        "import json\n"
        "from pytauargus.engine import run_batch\n"
        f"eng = run_batch({str(arb)!r})\n"
        "tab = 0\n"
        "spec, srs = eng._tables[tab]\n"
        "tau = eng._tau\n"
        "ncell, _ = tau.get_total_table_size(tab)\n"
        "n_exp = len(spec.exp_vars)\n"
        "topn = eng._top_n_needed(srs, False)\n"
        "n_dec = eng._metadata.variables"
        "[eng._metadata.index_of(spec.resp_var)].n_decimals\n"
        "print(json.dumps([ncell, n_exp, topn, n_dec]))\n"
    )
    env = dict(os.environ, PYTHONPATH=str(SRC) + os.pathsep
               + os.environ.get("PYTHONPATH", ""))
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
    )
    assert proc.returncode == 0, (
        f"rc={proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    ncell, n_exp, topn, n_dec = [int(x) for x in
                                 json.loads(proc.stdout.strip().splitlines()[-1])]

    fpath = tmp_path / "int_ar.tab"
    assert fpath.is_file(), "AR+ write did not produce the file"
    lines = fpath.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == ncell
    n_cols = n_exp + 1 + 1 + 1 + 1 + topn + 1 + 1 + 1 + 6
    n_audited_lines = 0
    for j, line in enumerate(lines):
        cols = line.split(";")
        assert len(cols) == n_cols, f"line {j}: {line}"
        # The audit ran: at least one unsafe line must carry a non-zero
        # realized bound somewhere in the +AR block.
        if cols[-9] in ("U", "M") and (cols[-6] != "0" or cols[-5] != "0"):
            n_audited_lines += 1
    assert n_audited_lines > 0, "no audited (U/M) line with realized bounds"


# ===================================================================
# CLI `audit`
# ===================================================================

def test_cli_audit_reports_intervals(tmp_path):
    arb = _build_arb_with_suppress_ar(tmp_path)
    proc = _run_cli(["audit", str(arb)])
    assert proc.returncode == 0, (
        f"rc={proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    out = proc.stdout
    # Table 1 is MOD-suppressed: intervals are reported per cell.
    assert "table 1:" in out
    assert "cells audited" in out
    assert "under-protected" in out
    assert "UNSAFE" in out or "under-protected" in out
    # Table 2 is computed only, but its NK(2,75) rule leaves primary-unsafe
    # (u) cells, which the audit reports as well.
    assert "table 2:" in out
    assert out.count("cells audited") == 2


def test_cli_audit_no_unsafe_cells(tmp_path):
    """With a no-op safety rule the audit has nothing to solve."""
    arb = _build_arb_no_unsafe(tmp_path)
    proc = _run_cli(["audit", str(arb)])
    assert proc.returncode == 0, (
        f"rc={proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    # One table (tabular input), and no suppressed cells in it.
    assert proc.stdout.count("no suppressed cells") == 1
    assert "cells audited" not in proc.stdout
