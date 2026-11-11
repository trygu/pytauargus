"""Phase 5.1 (0.7.0 error contract): the package checks native returns it
used to ignore.

Each test pins one previously-silent failure channel:

- ``set_variable`` / ``set_number_tab`` / ``set_number_var`` failures
- ``set_hierarchical_codelist`` file errors (HC_* codes from defines.h)
- ``undo_recode`` / ``set_var_code_active`` failures in the recode flow
- the cell-status restore setters
- an Apriori C/W cost or PL level that is not finite (legacy Java:
  ``x1 <= 0`` and ``x1 < 0`` comparisons let NaN/Inf reach the native)
- ``_apply_apriori_change`` no longer swallows exceptions
- the CLI ``save`` command surfaces a failed native table write
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pytauargus.batch import BatchError
from pytauargus.engine import (
    CS_UNSAFE_RULE,
    Engine,
    Metadata,
    Variable,
    run_batch,
)

from conftest import DATA

SRC = Path(__file__).resolve().parent.parent / "src"
ARB = DATA / "TestRecode.arb"


@pytest.fixture()
def engine():
    """Computed tables from the standard fixture (before suppression)."""
    return run_batch(ARB)


def _fresh_engine() -> Engine:
    e = Engine()
    e._tau.clean_all()
    e._tau.set_in_file_info(True, "")
    return e


def _stats0() -> dict:
    return {
        "lines_read": 0, "lines_error": 0,
        "status_ok": 0, "status_err": 0,
        "cost_ok": 0, "cost_err": 0,
        "protlevel_ok": 0, "protlevel_err": 0,
    }


def _fake_cell(status: int = CS_UNSAFE_RULE):
    """get_table_cell result with the status at index 10."""
    return (True,) + (0.0,) * 9 + (status,)


class TauProxy:
    """Delegate to the real native object, overriding selected methods.

    pybind11 instances are read-only, so monkeypatch.setattr cannot
    replace a native method in place.
    """

    def __init__(self, inner, overrides=None):
        self._inner = inner
        self._overrides = overrides or {}

    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._inner, name)


def _patch_tau(engine, monkeypatch, **overrides):
    monkeypatch.setattr(engine, "_tau", TauProxy(engine._tau, overrides))


# -- variable / table registration -------------------------------------------

def test_set_variable_failure_raises(engine, monkeypatch):
    _patch_tau(engine, monkeypatch, set_variable=lambda *a, **k: False)
    with pytest.raises(BatchError, match="SetVariable"):
        engine._set_variable(engine._metadata.variables[0])


def test_set_hierarchical_codelist_bad_file_raises(tmp_path):
    e = _fresh_engine()
    e._tau.set_number_var(1)
    hrc = tmp_path / "bad.hrc"
    # Level-1 line without a level-0 parent: native returns 1503.
    hrc.write_text("1.1\n", encoding="utf-8")
    var = Variable(name="X", index=0, b_pos=1, var_len=2, hierarchical=2,
                   hier_file_name=str(hrc), leading_string="1.")
    with pytest.raises(BatchError, match="SetHierarchicalCodelist"):
        e._set_variable(var)


def test_set_number_tab_failure_raises(engine):
    # nTab = 0 is rejected by the native (nTab < 1).
    engine._tables.clear()
    engine._safety_rules_buf.clear()
    with pytest.raises(BatchError, match="SetNumberTab"):
        engine._finalize_tables()


def test_set_number_var_failure_raises(monkeypatch):
    e = _fresh_engine()
    _patch_tau(e, monkeypatch, set_number_var=lambda *a, **k: False)
    meta = Metadata()
    meta.variables = []
    with pytest.raises(BatchError, match="SetNumberVar"):
        e._init_table_engine(meta)


# -- recode flow ---------------------------------------------------------------

def test_undo_recode_failure_raises(engine, monkeypatch):
    _patch_tau(engine, monkeypatch, undo_recode=lambda *a, **k: False)
    with pytest.raises(BatchError, match="UndoRecode"):
        engine._recode_truncate(0, 1)


def test_set_var_code_active_failure_truncate(engine, monkeypatch):
    _patch_tau(engine, monkeypatch,
               undo_recode=lambda *a, **k: True,
               get_var_number_of_codes=lambda *a, **k: (True, 2, 0),
               get_var_code_properties=lambda *a, **k: (True, "", "", 0, 1, 3, ""),
               set_var_code_active=lambda *a, **k: False)
    with pytest.raises(BatchError, match="SetVarCodeActive"):
        engine._recode_truncate(0, 1)


def test_set_var_code_active_failure_tree(engine, tmp_path, monkeypatch):
    _patch_tau(engine, monkeypatch,
               undo_recode=lambda *a, **k: True,
               get_var_number_of_codes=lambda *a, **k: (True, 1, 0),
               get_var_code=lambda *a, **k: (True, 0, "A", 0, 0),
               set_var_code_active=lambda *a, **k: False)
    path = tmp_path / "tree.grc"
    path.write_text("<TREERECODE>\nA\n", encoding="utf-8")
    with pytest.raises(BatchError, match="SetVarCodeActive"):
        engine._recode_tree(0, path)


# -- cell status restore --------------------------------------------------------

def test_restore_cell_statuses_status_failure(engine, monkeypatch):
    _patch_tau(engine, monkeypatch,
               set_table_cell_status_cell=lambda *a, **k: False)
    backup = ([CS_UNSAFE_RULE], [0.0], [0.0])  # statuses, lpls, upls
    with pytest.raises(BatchError, match="status"):
        engine._restore_cell_statuses(0, backup)


def test_restore_cell_statuses_protection_levels_failure(engine, monkeypatch):
    _patch_tau(engine, monkeypatch,
               set_table_cell_status_cell=lambda *a, **k: True,
               set_table_cell_protection_levels=lambda *a, **k: False)
    backup = ([CS_UNSAFE_RULE], [1.0], [2.0])
    with pytest.raises(BatchError, match="protection"):
        engine._restore_cell_statuses(0, backup)


# -- apriori ---------------------------------------------------------------------

def test_apriori_cost_inf_defaults_to_one(engine, monkeypatch):
    """Legacy ``x1 <= 0`` is False for Inf: an infinite cost must not
    reach the native setter; the silly-value default (1.0) applies."""
    calls = []
    _patch_tau(engine, monkeypatch,
               get_table_cell=lambda *a, **k: _fake_cell(),
               set_table_cell_cost=lambda *a, **k: calls.append(a) or True)
    stats = _stats0()
    engine._apply_apriori_change(0, (0, 0), "C", float("inf"), 0.0,
                                 -1, engine._tau, stats)
    assert stats["cost_ok"] == 1
    assert calls and calls[0][2] == 1.0


def test_apriori_cost_nan_defaults_to_one(engine, monkeypatch):
    calls = []
    _patch_tau(engine, monkeypatch,
               get_table_cell=lambda *a, **k: _fake_cell(),
               set_table_cell_cost=lambda *a, **k: calls.append(a) or True)
    stats = _stats0()
    engine._apply_apriori_change(0, (0, 0), "W", float("nan"), 0.0,
                                 -1, engine._tau, stats)
    assert stats["cost_ok"] == 1
    assert calls and calls[0][2] == 1.0


def test_apriori_pl_nan_rejected(engine, monkeypatch):
    """``NaN < 0`` is False: a NaN protection level must count as an
    illegal value and must not reach the native setter."""
    calls = []
    _patch_tau(engine, monkeypatch,
               get_table_cell=lambda *a, **k: _fake_cell(),
               set_protection_levels_dim=lambda *a, **k: calls.append(a) or True)
    stats = _stats0()
    engine._apply_apriori_change(0, (0, 0), "PL", float("nan"), 5.0,
                                 -2, engine._tau, stats)
    assert stats["protlevel_err"] == 1
    assert calls == []


def test_apriori_pl_inf_rejected(engine, monkeypatch):
    calls = []
    _patch_tau(engine, monkeypatch,
               get_table_cell=lambda *a, **k: _fake_cell(),
               set_protection_levels_dim=lambda *a, **k: calls.append(a) or True)
    stats = _stats0()
    engine._apply_apriori_change(0, (0, 0), "PL", float("inf"), 5.0,
                                 -2, engine._tau, stats)
    assert stats["protlevel_err"] == 1
    assert calls == []


def test_apply_apriori_change_propagates_native_errors(engine, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("native exploded")

    _patch_tau(engine, monkeypatch, get_table_cell=boom)
    with pytest.raises(RuntimeError, match="native exploded"):
        engine._apply_apriori_change(0, (0, 0), "S", 0.0, 0.0,
                                     1, engine._tau, _stats0())


# -- CLI save ---------------------------------------------------------------------

def _run_cli(argv):
    code = (
        "from pytauargus.cli import main;"
        f"import json,sys;sys.exit(main(json.loads({json.dumps(argv)!r})) or 0)"
    )
    env = dict(os.environ,
               PYTHONPATH=str(SRC) + os.pathsep
               + os.environ.get("PYTHONPATH", ""))
    return subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, env=env)


def test_cli_save_write_failure_exits_three(tmp_path):
    # Phase 6.1 contract: a failed table write is an engine failure
    # (BatchError), mapped to exit 3 (docs/binding-error-contract.md);
    # 0.6.x returned a flat exit 2 for every BatchError.
    out = tmp_path / "no-such-dir" / "table.csv"
    proc = _run_cli(["save", str(ARB), "--out", str(out)])
    assert proc.returncode == 3
    assert "wrote table" not in proc.stdout
    assert "error:" in proc.stderr
