"""Consultant-layer tests: method advice, assess, legend, instructions.

These encode the agent-front-end intent from ADR 0001 and the 4.1 manual:
the server is an expert SDC consultant. It articulates the trade-offs of
the protection methods for a given table, explains what the safety rules
flag and why (assess, without suppressing), and carries the context
(status legend, rule grammar, the questions to ask) the agent needs to
explain *what* and *why* to a non-expert.

The method-advice, legend, instructions and resource tests are pure logic
plus registration. The assess tests run a native solve on a tiny 4-record
fixture, so they need the engine of the pinned checkout.
"""

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from pytauargus_mcp import server  # noqa: E402

# The same small 2 x 2 microdata as test_mcp.py: fast for a single solve.
MICRO = {
    "Region": ["Os", "Os", "Ws", "Ws"],
    "Size": ["1", "2", "1", "2"],
    "Var2": [10.0, 20.0, 30.0, 40.0],
}


# ===========================================================================
# tauargus_method_advice: the "pros and cons" capability
# ===========================================================================

def test_method_advice_small_table_recommends_optimal():
    out = server.method_advice(n_dimensions=2)
    assert out["recommended"] == "Optimal"
    by = {m["method"]: m for m in out["methods"]}
    assert by["Optimal"]["applies"] is True
    assert by["Network"]["applies"] is False  # needs a hierarchy too


def test_method_advice_high_dim_recommendation_tracks_gh_availability():
    # Five/six-dim tables point at Hypercube; the recommendation honours
    # whether this build actually implements it.
    out = server.method_advice(n_dimensions=6)
    by = {m["method"]: m for m in out["methods"]}
    gh = by["Hypercube / GHMITER"]
    assert gh["applies"] is True  # the documented route for 5-6 dims
    assert by["Optimal"]["applies"] is False
    assert by["Modular / HiTaS"]["applies"] is False
    if gh["available"]:
        assert out["recommended"] == "Hypercube / GHMITER"
    else:
        assert out["recommended"] is None
        assert "Hypercube" in out["recommended_note"]


def test_method_advice_network_only_two_d_hierarchical():
    two_d = server.method_advice(n_dimensions=2, hierarchical=True)
    assert {m["method"]: m["applies"] for m in two_d["methods"]}["Network"] is True
    three_d = server.method_advice(n_dimensions=3, hierarchical=True)
    assert {m["method"]: m["applies"] for m in three_d["methods"]}["Network"] is False


@pytest.mark.parametrize("n_dimensions", (2, 5, 6))
def test_method_advice_never_offers_gh_for_hierarchies(n_dimensions):
    out = server.method_advice(n_dimensions=n_dimensions, hierarchical=True)
    gh = next(m for m in out["methods"] if m["method"] == "Hypercube / GHMITER")
    assert gh["applies"] is False
    assert out["recommended"] != "Hypercube / GHMITER"
    if n_dimensions >= 5:
        assert out["recommended"] is None
        assert out["recommended_note"]


def test_method_advice_frequency_recommends_rounding():
    out = server.method_advice(n_dimensions=2, frequency=True)
    assert out["recommended"] == "Controlled rounding"


def test_method_advice_every_method_has_pros_and_cons():
    out = server.method_advice(n_dimensions=2)
    assert len(out["methods"]) >= 5
    for m in out["methods"]:
        assert set(m) >= {"method", "applies", "available", "pros", "cons",
                          "use"}
        assert m["pros"], f"{m['method']} has no pros"
        assert m["cons"], f"{m['method']} has no cons"
        assert m["use"], f"{m['method']} has no use"


def test_method_advice_recommended_is_available_and_applicable():
    # The recommendation must name a method that both fits the structure and
    # is implemented by the engine; otherwise it is None with an explanation.
    for nd in (1, 2, 3, 4, 5, 6):
        for freq in (False, True):
            out = server.method_advice(n_dimensions=nd, frequency=freq)
            rec = out["recommended"]
            if rec is None:
                assert out["recommended_note"]
                continue
            by = {m["method"]: m for m in out["methods"]}
            assert by[rec]["applies"] is True, (
                f"nd={nd} freq={freq}: {rec!r} is not applicable")
            assert by[rec]["available"] is True, (
                f"nd={nd} freq={freq}: {rec!r} is not available in this build")


def test_method_advice_marks_engine_unavailable_methods():
    out = server.method_advice(n_dimensions=2)
    by = {m["method"]: m for m in out["methods"]}
    # implemented by the engine on this build
    assert by["Optimal"]["available"] is True
    assert by["Modular / HiTaS"]["available"] is True
    assert by["Controlled rounding"]["available"] is True
    assert by["Global recoding"]["available"] is True
    # hypercube: implemented for flat tables since 0.8.0
    assert by["Hypercube / GHMITER"]["available"] is True
    # not yet implemented: network, CTA
    assert by["Network"]["available"] is False
    assert by["Controlled tabular adjustment (CTA)"]["available"] is False
    assert out["available"] == [
        "Global recoding", "Optimal", "Modular / HiTaS",
        "Hypercube / GHMITER", "Controlled rounding"]


def test_method_advice_hypercube_notes_flat_table_scope():
    # the shipped hypercube implementation covers flat tables; the advice
    # must say so instead of promising hierarchical/linked/weighted support
    out = server.method_advice(n_dimensions=6)
    by = {m["method"]: m for m in out["methods"]}
    gh = by["Hypercube / GHMITER"]
    joined = " ".join(gh["cons"]).lower()
    assert "flat" in joined
    assert "hierarchical" in joined
    assert "linked" in joined


def test_method_advice_recode_names_its_tool_path():
    # global recoding is available (the engine's <RECODE> command), so the
    # advice must say HOW an agent applies it, not just that it exists
    out = server.method_advice(n_dimensions=2)
    by = {m["method"]: m for m in out["methods"]}
    rc = by["Global recoding"]
    assert rc["available"] is True
    assert "recodes" in rc["use"].lower()


def test_method_advice_rejects_bad_dimension_count():
    with pytest.raises(ValueError):
        server.method_advice(n_dimensions=0)
    with pytest.raises(ValueError):
        server.method_advice(n_dimensions=7)


# ===========================================================================
# plain-language status legend in the protect output
# ===========================================================================

def test_status_legend_has_plain_words_for_every_code():
    legend = server.status_legend()
    for code, word in (("S", "safe"), ("U", "unsafe"), ("M", "secondary"),
                       ("P", "protected"), ("E", "empty")):
        assert code in legend, f"status {code!r} missing from legend"
        assert legend[code].lower().find(word) != -1, (
            f"status {code!r} legend {legend[code]!r} does not say {word!r}")


# ===========================================================================
# server-level instructions: the consultant context the agent reads
# ===========================================================================

def test_server_has_instructions_with_workflow_and_legend():
    instructions = server.mcp.instructions
    assert instructions, "server must carry agent-facing instructions"
    low = instructions.lower()
    # the consultant workflow and the questions to ask
    for needle in ("explore", "assess", "protect", "audit"):
        assert needle in low, f"instructions miss workflow step {needle!r}"
    # the plain-language status legend
    for code in ("U", "M", "S"):
        assert code in instructions, f"instructions miss status code {code!r}"


def test_server_instructions_guardrail_for_long_solves():
    """The agent must warn before a long blocking solve, not mid-silence.

    A suppression solve is one blocking call with no progress; the
    instructions must make the agent announce it and gauge the table
    size first, with assess as the quick preview.
    """
    low = (server.mcp.instructions or "").lower()
    for needle in ("announce", "long", "cells", "dimension"):
        assert needle in low, f"instructions miss long-solve guardrail {needle!r}"


# ===========================================================================
# the protection-methods reference resource
# ===========================================================================

def test_protection_methods_resource_registered():
    resources = asyncio.run(server.mcp.list_resources())
    uris = {str(r.uri) for r in resources}
    assert "tauargus://doc/protection-methods" in uris


# ===========================================================================
# rule meanings: the plain words for the safety-rule grammar
# ===========================================================================

def test_rule_meanings_split_compound_rules():
    out = server.rule_meanings("P(25,1)|FREQ(3,20)|ZERO(10)")
    assert [r["rule"] for r in out] == ["P(25,1)", "FREQ(3,20)", "ZERO(10)"]
    for r in out:
        assert r["meaning"]


def test_rule_meanings_p_rule_cites_threshold_and_contributors():
    out = {r["rule"]: r["meaning"] for r in server.rule_meanings("P(25,1)")}
    m = out["P(25,1)"].lower()
    assert "25" in m and "largest" in m


def test_rule_meanings_unknown_token_is_honest():
    out = server.rule_meanings("FROB(1)")
    assert len(out) == 1
    assert "4.1" in out[0]["meaning"]


# ===========================================================================
# tauargus_assess: run the safety rules without suppressing (the "why")
# ===========================================================================

def test_assess_flags_sensitive_cells_without_suppressing(tmp_path):
    out = server.assess_table(MICRO, ["Region", "Size"], "Var2",
                              safety_rules="P(25,1)", workdir=str(tmp_path))
    assert out["n_tables"] == 1
    t = out["tables"][0]
    assert t["n_cells"] == 9
    assert t["status_counts"].get("U", 0) >= 1
    # no suppression ran: no secondary cells can exist
    assert t["status_counts"].get("M", 0) == 0
    assert len(t["sensitive_cells"]) >= 1
    for c in t["sensitive_cells"]:
        assert set(c) >= {"cell", "dims", "value", "freq", "status"}
        assert c["value"] is not None  # assess withholds nothing
    assert out["status_legend"]["U"]
    assert {r["rule"] for r in out["rules"]} == {"P(25,1)"}


def test_assess_publishes_the_raw_values(tmp_path):
    out = server.assess_table(MICRO, ["Region", "Size"], "Var2",
                              workdir=str(tmp_path))
    t = out["tables"][0]
    by = {(c["dims"]["Region"], c["dims"]["Size"]): c["value"]
          for c in t["sensitive_cells"]}
    # single-record cells keep exactly their record's value
    assert by[("Os", "1")] == 10.0
    assert by[("Ws", "2")] == 40.0


def test_assess_batch_has_no_suppression_step(tmp_path):
    out = server.assess_table(MICRO, ["Region", "Size"], "Var2",
                              workdir=str(tmp_path))
    arb = Path(out["files"]["arb"])
    assert arb.is_file()
    assert "<SUPPRESS>" not in arb.read_text()


def test_assess_sensitive_cells_carry_the_contributor_count(tmp_path):
    out = server.assess_table(MICRO, ["Region", "Size"], "Var2",
                              workdir=str(tmp_path))
    t = out["tables"][0]
    by = {(c["dims"]["Region"], c["dims"]["Size"]): c["freq"]
          for c in t["sensitive_cells"]}
    assert by[("Os", "1")] == 1  # one record in that cell


def test_assess_no_rules_flags_nothing(tmp_path):
    out = server.assess_table(MICRO, ["Region", "Size"], "Var2",
                              safety_rules="", workdir=str(tmp_path))
    t = out["tables"][0]
    assert t["status_counts"].get("U", 0) == 0
    assert t["sensitive_cells"] == []
