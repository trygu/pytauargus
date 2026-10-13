"""Ported tests for the microdata ``.asc`` + ``.rda`` writer.

Direct port of ``references/rtauargus/tests/testthat/test_micro_asc_rda.R``.
Expected values were captured by running the R source in base R (with ``gdata``
installed for the fixed-width ``.asc`` half). The R ``data.frame`` recycles short
columns to the frame length, so the Python fixtures pass pre-recycled columns.
"""

import os
import re

import pytest

from pytauargus.micro import write_rda, write_fwf, micro_asc_rda


def read_lines(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


# --- the R test's `input` fixture (a list of per-variable info dicts) ------
# Note V2 is RECODEABLE (not NUMERIC) in the authoritative R fixture.

INPUT = [
    dict(type_var="RECODEABLE", colname="V1", position=1, width=1, digits=0,
         missing="?", totcode="Total", codelist=None, hierarchical=None,
         hierleadstring=None),
    dict(type_var="RECODEABLE", colname="V2", position=3, width=1, digits=0,
         missing=None, totcode="Total", codelist=None, hierarchical="V2.hrc",
         hierleadstring="@"),
    dict(type_var="RECODEABLE", colname="V3", position=5, width=2, digits=0,
         missing="#", totcode="Total", codelist="V3.cdl", hierarchical="1 1",
         hierleadstring=None),
    dict(type_var="NUMERIC", colname="VAL", position=8, width=3, digits=0,
         missing="9999", totcode=None, codelist=None, hierarchical=None,
         hierleadstring=None),
    dict(type_var="WEIGHT", colname="POIDS", position=12, width=4, digits=2,
         missing="#", totcode=None, codelist=None, hierarchical=None,
         hierleadstring=None),
    dict(type_var="HOLDING", colname="HOLD", position=17, width=2, digits=0,
         missing="#", totcode="Total", codelist=None, hierarchical=None,
         hierleadstring=None),
]

# The R test's `attendu`: the .rda split on newline+2-space+plus.
ATTENDU = [
    "V1 1 1 ?",
    "<RECODEABLE>",
    '<TOTCODE> "Total"',
    "V2 3 1",
    "<RECODEABLE>",
    '<TOTCODE> "Total"',
    "<HIERARCHICAL>",
    '<HIERCODELIST> "V2.hrc"',
    '<HIERLEADSTRING> "@"',
    "V3 5 2 #",
    "<RECODEABLE>",
    '<TOTCODE> "Total"',
    '<CODELIST> "V3.cdl"',
    "<HIERARCHICAL>",
    "<HIERLEVELS> 1 1",
    "VAL 8 3 9999",
    "<NUMERIC>",
    "<DECIMALS> 0",
    "POIDS 12 4 #",
    "<WEIGHT>",
    "<DECIMALS> 2",
    "HOLD 17 2 #",
    "<HOLDING>",
    '<TOTCODE> "Total"',
]


# --- write_rda (pure) ------------------------------------------------------

def test_write_rda(tmp_path, monkeypatch):
    # run in an empty dir so codelist/hrc paths stay relative (mustWork=FALSE)
    monkeypatch.chdir(tmp_path)
    blocks = write_rda(INPUT)
    # the R contract: strsplit on the *vector* of blocks (one split per
    # block, pattern = regex "\n  +"), then unlist (flatten in order)
    split_blocks = [re.split(r"\n  +", b) for b in blocks]
    assert [x for b in split_blocks for x in b] == ATTENDU


# --- micro_asc_rda (asc + rda) --------------------------------------------

# pre-recycled to 8 rows (mirrors R data.frame recycling of short columns)
DONNEES = {
    "V1": ["A", "A", "A", "A", "B", "B", "B", "C"],
    "V2": ["Y", "Z", "Y", "Z", "Y", "Z", "Y", "Z"],
    "V3": ["T1", "T2", "T1", "S_", "T1", "T1", "T1", "S_"],
    "VAL": [100, 0, 7, 25, 0, 4, 0, 5],
    "POIDS": [1, 2.71, 4.2, 1, 1, 2.71, 4.2, 1],
    "HOLD": ["H1", "H2", "H3", "H4", "H1", "H2", "H3", "H4"],
}

ASC_LINES = [
    "A Y T1 100 1.00 H1",
    "A Z T2   0 2.71 H2",
    "A Y T1   7 4.20 H3",
    "A Z S_  25 1.00 H4",
    "B Y T1   0 1.00 H1",
    "B Z T1   4 2.71 H2",
    "B Y T1   0 4.20 H3",
    "C Z S_   5 1.00 H4",
]


def test_micro_asc_rda(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tmp = micro_asc_rda(
        DONNEES,
        weight_var="POIDS",
        holding_var="HOLD",
        hrc={"V2": "V2.hrc", "V3": "1 1"},
        missing={"_": "#", "V1": "?", "V2": None, "VAL": "9999"},
        codelist={"V3": "V3.cdl"},
    )

    assert read_lines(tmp["asc_filename"]) == ASC_LINES
    # rda lines are 2-space indented (except var headers); trimws in R
    assert [ln.strip() for ln in read_lines(tmp["rda_filename"])] == ATTENDU


def test_write_fwf_layout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    asc, info = write_fwf(DONNEES)
    # positions must match the R test's `input` layout exactly
    pos = {r["colname"]: r["position"] for r in info}
    width = {r["colname"]: r["width"] for r in info}
    assert pos == {"V1": 1, "V2": 3, "V3": 5, "VAL": 8, "POIDS": 12, "HOLD": 17}
    assert width == {"V1": 1, "V2": 1, "V3": 2, "VAL": 3, "POIDS": 4, "HOLD": 2}
    assert asc.splitlines() == ASC_LINES
