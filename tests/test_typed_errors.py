"""Typed native engine errors (0.7.0, phase 3).

``TauArgusNativeError`` (a ``BatchError`` subclass) is raised at every
checked native call site in ``engine.py``.  It carries the native error
code (0 when the call reports none) and, when a code is reported, the
catalog text for that code in the message.

Native return-code contracts pinned here:
- ``HiTaSCtrl.a_hitas`` / ``full_jj``: a non-zero / >1 return is a HiTaS
  error code (5000 file not found, 5001 can not write, 6000+ JJ codes).
- ``RounderCtrl.do_round``: a positive result plus the CRP error code
  (7100+) in the last tuple slot.
- ``audit_jj``: a non-zero rc raises ``RuntimeError`` in the binding
  (rc -2 = read/parse failure); ``Engine.audit`` must re-raise it typed.
- ``TauArgus.set_in_table`` / ``set_table_safety_info``: ``ok=False``
  with a core error code (e.g. TABLENOTSET for an out-of-range index).
"""

import pytest

from pytauargus.batch import BatchError, Suppress
from pytauargus.engine import Engine, SafetyRuleSet, TauArgusNativeError, run_batch
from pytauargus._tauargus import HiTaSCtrl, RounderCtrl, audit_jj

from conftest import DATA


@pytest.fixture()
def engine():
    """Fresh computed tables per test (suppression mutates table state)."""
    return run_batch(DATA / "TestRecode.arb")


# ===================================================================
# The error class itself
# ===================================================================


class TestNativeErrorClass:
    def test_is_batch_error_subclass(self):
        assert issubclass(TauArgusNativeError, BatchError)

    def test_carries_code(self):
        e = TauArgusNativeError("AHiTaS failed: x", code=5001)
        assert e.code == 5001
        assert str(e) == "AHiTaS failed: x"

    def test_code_defaults_to_zero(self):
        assert TauArgusNativeError("no code reported").code == 0

    def test_exported_from_package_root(self):
        import pytauargus

        assert pytauargus.TauArgusNativeError is TauArgusNativeError


# ===================================================================
# Binding-level native contracts (real native calls)
# ===================================================================


class TestBindingContracts:
    def test_a_hitas_missing_files_returns_hitas_code(self):
        h = HiTaSCtrl()
        code = h.a_hitas(pars_file="/nonexistent/NPF.txt",
                         files_file="/nonexistent/NFS.txt", max_time=0)
        assert code in (5000, 5001), f"unexpected a_hitas code {code}"

    def test_audit_jj_missing_file_raises_runtime_error(self):
        with pytest.raises(RuntimeError, match="rc=-2"):
            audit_jj("/nonexistent/ANNEKE.JJ")


# ===================================================================
# Engine.audit re-raises the binding RuntimeError typed
# ===================================================================


class TestAuditFailure:
    def test_audit_native_failure_typed(self, engine, monkeypatch):
        import pytauargus.engine as eng_mod

        def _boom(jj):
            raise RuntimeError("audit_jj failed (rc=-2): " + jj)

        monkeypatch.setattr(eng_mod, "audit_jj", _boom)
        with pytest.raises(TauArgusNativeError) as ei:
            engine.audit(0)
        assert ei.value.code == -2
        assert "audit_jj failed (rc=-2)" in str(ei.value)


# ===================================================================
# Suppress paths: native failure code becomes the typed error
# ===================================================================


class TestSuppressFailures:
    def test_mod_a_hitas_failure_typed(self, engine, monkeypatch):
        real = HiTaSCtrl()

        class _FakeHitas:
            def a_hitas(self, **kw):
                return 5001

            def error_string(self, code):
                return real.error_string(code)

        monkeypatch.setattr(engine, "_hitas", _FakeHitas())
        with pytest.raises(TauArgusNativeError) as ei:
            engine.suppress(Suppress(kind="MOD", tab_no=1))
        assert ei.value.code == 5001
        assert "AHiTaS failed" in str(ei.value)
        assert real.error_string(5001) in str(ei.value)

    def test_opt_full_jj_failure_typed(self, engine, monkeypatch):
        real = HiTaSCtrl()

        class _FakeHitas:
            def full_jj(self, **kw):
                return 6001

            def error_string(self, code):
                return real.error_string(code)

        monkeypatch.setattr(engine, "_hitas", _FakeHitas())
        with pytest.raises(TauArgusNativeError) as ei:
            engine.suppress(Suppress(kind="OPT", tab_no=1))
        assert ei.value.code == 6001
        assert "FullJJ failed (code 6001)" in str(ei.value)
        assert real.error_string(6001) in str(ei.value)

    def test_rnd_do_round_failure_typed(self, engine, monkeypatch):
        real = RounderCtrl()

        class _FakeRounder:
            def set_double_constant(self, v, x):
                pass

            def do_round(self, **kw):
                return (1, 0.0, 0, 0.0, 7100)

            def error_string(self, code):
                return real.error_string(code)

        monkeypatch.setattr(engine, "_rounder", _FakeRounder())
        base = engine._min_round_base(0)
        assert base > 0, "Test requires a positive minimum round base"
        with pytest.raises(TauArgusNativeError) as ei:
            engine.suppress(Suppress(kind="RND", tab_no=1, rnd_base=base))
        assert ei.value.code == 7100
        assert "DoRound failed" in str(ei.value)
        assert real.error_string(7100) in str(ei.value)


# ===================================================================
# Core setter failures: ok=False plus the core error code
# ===================================================================


class TestCoreSetterFailures:
    def test_set_in_table_bad_index_typed(self, engine):
        cell = {"codes": ["0"], "shadow": 1.0, "cost": 0.0, "response": 1.0,
                "freq": 0, "max_score": [0.0], "status": 3,
                "lower": 0.0, "upper": 0.0}
        with pytest.raises(TauArgusNativeError) as ei:
            engine._set_in_table(999, cell)
        assert ei.value.code != 0
        assert "SetInTable failed for table 1000" in str(ei.value)
        assert engine._tau.error_string(ei.value.code) in str(ei.value)

    def test_set_table_safety_info_bad_index_typed(self, engine):
        with pytest.raises(TauArgusNativeError) as ei:
            engine._call_set_table_safety_info(999, SafetyRuleSet())
        assert ei.value.code != 0
        assert "SetTableSafetyInfo failed for table 1000" in str(ei.value)
        assert engine._tau.error_string(ei.value.code) in str(ei.value)
