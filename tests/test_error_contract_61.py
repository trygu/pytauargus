"""Phase 6.1: stable CLI exit codes (0.7.0 contract).

``docs/binding-error-contract.md`` maps failure classes to stable exit
codes for the ``tauargus`` CLI:

    0  success
    1  unexpected internal error (a bug)
    2  usage error (bad CLI invocation)
    3  engine failure (bad batch file, native core error, I/O failure)
    4  solver failure (HiTaS suppression, rounder)
    5  audit failure (CSP realized-interval audit)

Solver and audit failures surface as ``SolverError`` and ``AuditError``
(subclasses of ``TauArgusNativeError``); all other engine failures as
``BatchError``. The mapping is unit-tested in process, the solver and
audit raise sites are exercised through TauProxy patches (native
pybind11 instances are read-only), and the engine-failure path is
verified end-to-end in a subprocess.
"""

import pytest

from pytauargus.batch import BatchError
from pytauargus.cli import exit_code_for, main
from pytauargus.engine import (
    AuditError,
    SolverError,
    Suppress,
    TauArgusNativeError,
)

from conftest import DATA
from test_cli import _run_cli
from test_error_contract_51 import TauProxy


ARB = DATA / "TestRecode.arb"


def _patch(engine, monkeypatch, attr, overrides):
    monkeypatch.setattr(engine, attr, TauProxy(getattr(engine, attr), overrides))


# -- the mapping ---------------------------------------------------------------

def test_engine_failures_map_to_three():
    assert exit_code_for(BatchError("bad batch file")) == 3
    assert exit_code_for(TauArgusNativeError("native", code=100)) == 3


def test_solver_failures_map_to_four():
    assert exit_code_for(SolverError("AHiTaS failed", code=4)) == 4


def test_audit_failures_map_to_five():
    assert exit_code_for(AuditError("audit_jj failed", code=7)) == 5


def test_unexpected_errors_map_to_one():
    assert exit_code_for(RuntimeError("bug")) == 1


def test_solver_and_audit_are_native_errors():
    assert issubclass(SolverError, TauArgusNativeError)
    assert issubclass(AuditError, TauArgusNativeError)
    assert issubclass(TauArgusNativeError, BatchError)


# -- raise sites (in process, TauProxy) -----------------------------------------

def test_a_hitas_failure_raises_solver_error(engine, monkeypatch):
    _patch(engine, monkeypatch, "_hitas", {"a_hitas": lambda **k: 4})
    with pytest.raises(SolverError) as ei:
        engine.suppress(Suppress(kind="MOD", tab_no=1))
    assert ei.value.code == 4


def test_full_jj_failure_raises_solver_error(engine, monkeypatch):
    _patch(engine, monkeypatch, "_hitas", {"full_jj": lambda **k: 2})
    with pytest.raises(SolverError) as ei:
        engine.suppress(Suppress(kind="OPT", tab_no=1))
    assert ei.value.code == 2


def test_do_round_failure_raises_solver_error(engine, monkeypatch):
    _patch(engine, monkeypatch, "_rounder",
           {"do_round": lambda **k: (1, 0, 0, 0, 3)})
    base = max(engine._min_round_base(0), 1000)
    with pytest.raises(SolverError) as ei:
        engine.suppress(Suppress(kind="RND", tab_no=1, rnd_base=base))
    assert ei.value.code == 3


def test_audit_failure_raises_audit_error(engine, monkeypatch):
    import pytauargus.engine as engmod

    def boom(_jj):
        raise RuntimeError("audit_jj failed (rc=7): /tmp/Anneke.JJ")

    monkeypatch.setattr(engmod, "audit_jj", boom)
    with pytest.raises(AuditError) as ei:
        engine.audit(0)
    assert ei.value.code == 7


# -- end-to-end CLI ---------------------------------------------------------------

def test_malformed_batch_file_exits_three(tmp_path):
    bad = tmp_path / "bad.arb"
    bad.write_text("<BOGUSCOMMAND> x\n", encoding="utf-8")
    proc = _run_cli(["run", str(bad)])
    assert proc.returncode == 3
    assert "Unknown keyword" in proc.stderr


def test_missing_batch_file_exits_two():
    assert main(["run", str(DATA / "does_not_exist.arb")]) == 2
