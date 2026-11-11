"""Consultant-layer tests: method advice, status legend, instructions, resource.

These encode the agent-front-end intent from ADR 0001 and the 4.1 manual:
the server is an expert SDC consultant. It articulates the trade-offs of
the protection methods for a given table, and carries the context (status
legend, rule grammar, the questions to ask) the agent needs to explain
*what* and *why* to a non-expert. Pure logic + registration: no native
solve is required, so these run fast and deterministically.
"""

import asyncio

import pytest

pytest.importorskip("mcp")

from pytauargus_mcp import server  # noqa: E402


# ===========================================================================
# tauargus_method_advice: the "pros and cons" capability
# ===========================================================================

def test_method_advice_small_table_recommends_optimal():
    out = server.method_advice(n_dimensions=2)
    assert out["recommended"] == "Optimal"
    by = {m["method"]: m for m in out["methods"]}
    assert by["Optimal"]["applies"] is True
    assert by["Network"]["applies"] is False  # needs a hierarchy too


def test_method_advice_high_dim_recommends_hypercube():
    out = server.method_advice(n_dimensions=6)
    assert out["recommended"] == "Hypercube / GHMITER"
    by = {m["method"]: m for m in out["methods"]}
    assert by["Optimal"]["applies"] is False
    assert by["Modular / HiTaS"]["applies"] is False
    assert by["Hypercube / GHMITER"]["applies"] is True


def test_method_advice_network_only_two_d_hierarchical():
    two_d = server.method_advice(n_dimensions=2, hierarchical=True)
    assert {m["method"]: m["applies"] for m in two_d["methods"]}["Network"] is True
    three_d = server.method_advice(n_dimensions=3, hierarchical=True)
    assert {m["method"]: m["applies"] for m in three_d["methods"]}["Network"] is False


def test_method_advice_frequency_recommends_rounding():
    out = server.method_advice(n_dimensions=2, frequency=True)
    assert out["recommended"] == "Controlled rounding"


def test_method_advice_every_method_has_pros_and_cons():
    out = server.method_advice(n_dimensions=2)
    assert len(out["methods"]) >= 5
    for m in out["methods"]:
        assert set(m) >= {"method", "applies", "pros", "cons", "use"}
        assert m["pros"], f"{m['method']} has no pros"
        assert m["cons"], f"{m['method']} has no cons"
        assert m["use"], f"{m['method']} has no use"


def test_method_advice_recommended_is_applicable():
    for nd in (1, 2, 3, 4, 5, 6):
        out = server.method_advice(n_dimensions=nd)
        by = {m["method"]: m for m in out["methods"]}
        assert by[out["recommended"]]["applies"] is True, (
            f"n_dimensions={nd}: recommended {out['recommended']!r} is not applicable")


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
    for needle in ("explore", "protect", "audit"):
        assert needle in low, f"instructions miss workflow step {needle!r}"
    # the plain-language status legend
    for code in ("U", "M", "S"):
        assert code in instructions, f"instructions miss status code {code!r}"


# ===========================================================================
# the protection-methods reference resource
# ===========================================================================

def test_protection_methods_resource_registered():
    resources = asyncio.run(server.mcp.list_resources())
    uris = {str(r.uri) for r in resources}
    assert "tauargus://doc/protection-methods" in uris
