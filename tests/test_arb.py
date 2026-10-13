"""Ported tests for the ``.arb`` batch writer.

Direct port of ``references/rtauargus/tests/testthat/test_micro_arb.R``
(``suppr_writetable``), plus ``specif_safety``/``apriori_batch`` cases and a
full ``micro_arb`` run whose expected output was captured by running the R
source in base R. ``norm_path`` semantics make the written paths absolute when
they resolve (see ``test_util.py``); here the fixtures use names that do not
resolve, so paths are echoed as-is.
"""

import os
import re

import pytest

from pytauargus.arb import (
    ArbError,
    apriori_batch,
    micro_arb,
    specif_safety,
    suppr_writetable,
)


def read_lines(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


# --- suppr_writetable (port of test_micro_arb.R) ---------------------------

OUT = ["t1.csv", "tmp/t2.csv", "~/t3.csv"]


def _h(p):
    return os.path.join(os.path.expanduser("~"), p.lstrip("~/"))


def test_suppr_unique():
    assert suppr_writetable("GH(###, 100)", linked=False,
                            output_names=OUT[:2], output_type="2",
                            output_options="") == [
        "<SUPPRESS> GH(1,100)\n<WRITETABLE> (1,2,,\"t1.csv\")",
        "<SUPPRESS> GH(2,100)\n<WRITETABLE> (2,2,,\"tmp/t2.csv\")",
    ]

    assert suppr_writetable("MOD(0,3)", linked=False,
                            output_names=OUT, output_type="2",
                            output_options="AS+") == [
        "<SUPPRESS> MOD(1,3)\n<WRITETABLE> (1,2,AS+,\"t1.csv\")",
        "<SUPPRESS> MOD(2,3)\n<WRITETABLE> (2,2,AS+,\"tmp/t2.csv\")",
        '<SUPPRESS> MOD(3,3)\n<WRITETABLE> (3,2,AS+,"%s")' % _h("t3.csv"),
    ]

    # a single parameter (just the table number) + per-table options
    assert suppr_writetable("MOD(n)", linked=False,
                            output_names=OUT, output_type="2",
                            output_options=["", "AS+", "AS+SE+"]) == [
        '<SUPPRESS> MOD(1)\n<WRITETABLE> (1,2,,"t1.csv")',
        '<SUPPRESS> MOD(2)\n<WRITETABLE> (2,2,AS+,"tmp/t2.csv")',
        '<SUPPRESS> MOD(3)\n<WRITETABLE> (3,2,AS+SE+,"%s")' % _h("t3.csv"),
    ]


def test_suppr_multiple():
    assert suppr_writetable(["GH(1,100)", "MOD(2)", "GH(###,100)"],
                            linked=False, output_names=OUT,
                            output_type="2", output_options="") == [
        "<SUPPRESS> GH(1,100)\n<WRITETABLE> (1,2,,\"t1.csv\")",
        "<SUPPRESS> MOD(2)\n<WRITETABLE> (2,2,,\"tmp/t2.csv\")",
        '<SUPPRESS> GH(###,100)\n<WRITETABLE> (3,2,,"%s")' % _h("t3.csv"),
    ]


def test_suppr_linked():
    with pytest.raises(ArbError, match="un seul suppress permis"):
        suppr_writetable(["GH(1,100)", "MOD(2)", "GH(###,100)"],
                         linked=True, output_names=OUT,
                         output_type="2", output_options="")

    assert suppr_writetable("GH(.,100)", linked=True,
                            output_names=OUT,
                            output_type=["2", "4", "1"],
                            output_options="") == [
        "<SUPPRESS> GH(0,100)",
        "<WRITETABLE> (1,2,,\"t1.csv\")",
        "<WRITETABLE> (2,4,,\"tmp/t2.csv\")",
        '<WRITETABLE> (3,1,,"%s")' % _h("t3.csv"),
    ]


# --- specif_safety ---------------------------------------------------------

def test_specif_single_var():
    assert specif_safety(
        [["REGION"]], ["CA"], None, None, ["NK(1,85)"], [False]
    ) == ['<SPECIFYTABLE> "REGION"|"CA"||\n<SAFETYRULE> NK(1,85)']


def test_specif_multi_var():
    # the R generator concatenates quoted explanatory vars with no separator
    assert specif_safety(
        [["CJ", "A21"]], ["CA"], None, None, ["NK(1,85)|FREQ(3,10)"], [False]
    ) == [
        '<SPECIFYTABLE> "CJ""A21"|"CA"||\n<SAFETYRULE> NK(1,85)|FREQ(3,10)'
    ]


def test_specif_named_table_id():
    # named explanatory vars -> // <TABLE_ID> comment (R: list with names)
    assert specif_safety(
        [{"REGION": ["CJ", "A21"]}], ["CA"], [""], [""],
        ["NK(1,85)|FREQ(3,10)"], [True]
    ) == [
        '// <TABLE_ID> "REGION"\n'
        '<SPECIFYTABLE> "CJ""A21"|"CA"||\n'
        "<SAFETYRULE> NK(1,85)|FREQ(3,10)|Wgt(1)"
    ]


def test_specif_freq_and_shadow():
    assert specif_safety(
        [["REGION"]], ["<freq>"], ["SH"], [""], ["FREQ(3,10)"], [False]
    ) == ['<SPECIFYTABLE> "REGION"|"<freq>"|"SH"|\n<SAFETYRULE> FREQ(3,10)']


# --- apriori_batch ---------------------------------------------------------

def test_apriori_single_hst():
    assert apriori_batch(2, ["info.hst"]) == [
        '<APRIORI> "info.hst",1,",",0,0',
        '<APRIORI> "info.hst",2,",",0,0',
    ]


def test_apriori_per_table_options():
    assert apriori_batch(2, ["a.hst", "b.hst"], sep=";",
                         ignore_err=1, exp_triv=2) == [
        '<APRIORI> "a.hst",1,";",1,2',
        '<APRIORI> "b.hst",2,";",1,2',
    ]


def test_apriori_length_mismatch():
    with pytest.raises(ArbError, match="longueur arguments"):
        apriori_batch(3, ["a.hst", "b.hst"])


# --- micro_arb (full generator) --------------------------------------------

def test_micro_arb_basic(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "d.asc").write_text("x")
    (tmp_path / "d.rda").write_text("x")
    res = micro_arb(
        asc_filename="d.asc", rda_filename="d.rda",
        explanatory_vars=[["REGION"], ["SEXE"]],
        response_var="CA",
        safety_rules=["NK(1,85)", "FREQ(3,10)"],
        weighted=False,
        suppress="GH(.,100)",
        output_names=["t1.csv", "t2.csv"],
        output_type="2",
    )
    lines = read_lines(res["arb_filename"])
    assert lines[0] == "// Batch generated by package *rtauargus*"
    assert re.fullmatch(r"// \(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}( \S+| )?\)", lines[1])
    # asc/rda resolve (exist relative to cwd) -> absolute
    assert lines[2] == '<OPENMICRODATA> "%s"' % (tmp_path / "d.asc")
    assert lines[3] == '<OPENMETADATA> "%s"' % (tmp_path / "d.rda")
    assert lines[4:8] == [
        '<SPECIFYTABLE> "REGION"|"CA"||',
        "<SAFETYRULE> NK(1,85)",
        '<SPECIFYTABLE> "SEXE"|"CA"||',
        "<SAFETYRULE> FREQ(3,10)",
    ]
    assert lines[8] == "<READMICRODATA>"
    # non-linked: each <SUPPRESS>/<WRITETABLE> pair is a separate file line
    # (R writes the paste-sep="\n" result via writeLines)
    assert lines[9:13] == [
        "<SUPPRESS> GH(1,100)",
        '<WRITETABLE> (1,2,,"t1.csv")',
        "<SUPPRESS> GH(2,100)",
        '<WRITETABLE> (2,2,,"t2.csv")',
    ]
    assert lines[13] == ""  # final blank line


def test_micro_arb_apriori(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = micro_arb(
        asc_filename="d.asc", rda_filename="d.rda",
        explanatory_vars=[["REGION"], ["SEXE"]],
        response_var="CA",
        safety_rules=["NK(1,85)", "FREQ(3,10)"],
        suppress="GH(.,100)",
        output_names=["t1.csv", "t2.csv"],
        output_type="2",
        apriori=["info.hst"],
    )
    lines = read_lines(res["arb_filename"])
    assert lines[8] == "<READMICRODATA>"
    assert lines[9] == '<APRIORI> "info.hst",1,",",0,0'
    assert lines[10] == '<APRIORI> "info.hst",2,",",0,0'


def test_micro_arb_gointeractive(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = micro_arb(
        asc_filename="d.asc", rda_filename="d.rda",
        explanatory_vars=[["REGION"]],
        response_var="CA",
        safety_rules="FREQ(3,10)",
        suppress="GH(.,100)",
        output_names=["t1.csv"],
        output_type="2",
        gointeractive=True,
    )
    lines = read_lines(res["arb_filename"])
    assert "<GOINTERACTIVE>" in lines


def test_micro_arb_wgt_rejected(tmp_path):
    with pytest.raises(ArbError, match="ne pas renseigner WGT"):
        micro_arb(
            asc_filename="d.asc", rda_filename="d.rda",
            explanatory_vars=[["REGION"]],
            response_var="CA",
            safety_rules="NK(1,85)|WGT(1)",
            suppress="GH(.,100)",
            output_names=["t1.csv"],
            output_type="2",
        )


def test_micro_arb_tab_output_mismatch(tmp_path):
    with pytest.raises(ArbError, match="autant de noms de fichiers"):
        micro_arb(
            asc_filename="d.asc", rda_filename="d.rda",
            explanatory_vars=[["REGION"], ["SEXE"]],
            response_var="CA",
            safety_rules=["NK(1,85)", "FREQ(3,10)"],
            suppress="GH(.,100)",
            output_names=["t1.csv"],  # only one name for two tables
            output_type="2",
        )


def test_micro_arb_single_tab_as_vector(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # a single tabulation may be passed as a plain list of var names
    res = micro_arb(
        asc_filename="d.asc", rda_filename="d.rda",
        explanatory_vars=["REGION", "CJ"],
        response_var="CA",
        safety_rules="FREQ(3,10)",
        suppress="GH(.,100)",
        output_names=["t1.csv"],
        output_type="2",
    )
    lines = read_lines(res["arb_filename"])
    assert '<SPECIFYTABLE> "REGION""CJ"|"CA"||' in lines
