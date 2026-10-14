"""Regression tests for legacy SPECIFYTABLE cost/lambda defaults.

Legacy tau-ARGUS: shadow and cost default to the response variable;
lambda defaults to 1.  Omitted cost must therefore map to the response
variable (not frequency, -1) and omitted lambda must be 1 (not 0).
"""

import textwrap
import tempfile
from pathlib import Path

import pytest

from pytauargus.batch import SpecifyTable, parse_batch
from pytauargus.engine import Variable


def _parse_specify(specify_line: str) -> SpecifyTable:
    """Parse a minimal .arb file containing one SPECIFYTABLE command."""
    arb = textwrap.dedent(f"""\
        <OPENTABLEDATA> "data.tab"
        <OPENMETADATA> "meta.rda"
        <SPECIFYTABLE> {specify_line}
    """)
    with tempfile.NamedTemporaryFile("w", suffix=".arb", delete=False) as f:
        f.write(arb)
        path = Path(f.name)
    try:
        cmds = parse_batch(path)
    finally:
        path.unlink()
    for c in cmds:
        if isinstance(c, SpecifyTable):
            return c
    raise AssertionError("no SpecifyTable command found")


class TestOmittedCostDefaults:
    def test_omitted_cost_and_lambda(self):
        """<SPECIFYTABLE> "A"|"R"|  -> cost_var == "", lam == 1.0."""
        t = _parse_specify('"A"|"R"|')
        assert t.exp_vars == ["A"]
        assert t.resp_var == "R"
        assert t.shadow_var == ""
        assert t.cost_var == ""
        assert t.lam == 1.0

    def test_omitted_cost_double_pipe(self):
        """<SPECIFYTABLE> "A"|"R"||  -> cost_var == "", lam == 1.0."""
        t = _parse_specify('"A"|"R"||')
        assert t.cost_var == ""
        assert t.lam == 1.0

    def test_omitted_cost_explicit_lambda_zero(self):
        """<SPECIFYTABLE> "A"|"R"||,0  -> cost_var == "", lam == 0.0."""
        t = _parse_specify('"A"|"R"||,0')
        assert t.cost_var == ""
        assert t.lam == 0.0

    def test_explicit_response_cost(self):
        """<SPECIFYTABLE> "A"|"R"||"R",1  -> cost_var == "R", lam == 1.0."""
        t = _parse_specify('"A"|"R"||"R",1')
        assert t.cost_var == "R"
        assert t.lam == 1.0

    def test_explicit_frequency_cost(self):
        """<SPECIFYTABLE> "A"|"R"||-1,1  -> cost_var == "-1"."""
        t = _parse_specify('"A"|"R"||-1,1')
        assert t.cost_var == "-1"
        assert t.lam == 1.0

    def test_explicit_unity_cost(self):
        """<SPECIFYTABLE> "A"|"R"||-2,1  -> cost_var == "-2"."""
        t = _parse_specify('"A"|"R"||-2,1')
        assert t.cost_var == "-2"
        assert t.lam == 1.0


class TestEngineCostMapping:
    """The engine must map omitted cost to the response variable index."""

    def _make_engine_stub(self):
        from pytauargus.engine import Engine

        calls = {}

        class _Meta:
            variables = [
                Variable(name="A", index=0, type="CATEGORICAL"),
                Variable(name="R", index=1, type="RESPONSE"),
            ]
            def index_of(self, name):
                for v in self.variables:
                    if v.name == name:
                        return v.index
                return -1
            def contains(self, _):
                return False

        class _Tau:
            def set_table(self, **kw):
                calls.update(kw)
                return True
            def error_string(self, _):
                return ""

        eng = Engine.__new__(Engine)
        eng._tau = _Tau()
        eng._metadata = _Meta()
        return eng, calls

    def test_omitted_cost_maps_to_response(self):
        eng, calls = self._make_engine_stub()
        spec = SpecifyTable(exp_vars=["A"], resp_var="R",
                            shadow_var="", cost_var="", lam=1.0)
        eng._call_set_table(0, spec)
        assert calls["cost_var"] == 1

    def test_explicit_frequency_cost_maps_to_minus_one(self):
        eng, calls = self._make_engine_stub()
        spec = SpecifyTable(exp_vars=["A"], resp_var="R",
                            shadow_var="", cost_var="-1", lam=1.0)
        eng._call_set_table(0, spec)
        assert calls["cost_var"] == -1

    def test_explicit_unity_cost_maps_to_minus_two(self):
        eng, calls = self._make_engine_stub()
        spec = SpecifyTable(exp_vars=["A"], resp_var="R",
                            shadow_var="", cost_var="-2", lam=1.0)
        eng._call_set_table(0, spec)
        assert calls["cost_var"] == -2
