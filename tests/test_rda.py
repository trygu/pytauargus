"""Ported tests for the ``.rda`` metadata writer.

Direct port of the ``write_rda`` block of
``references/rtauargus/tests/testthat/test_micro_asc_rda.R``. The input is the
R test's ``input`` data frame (transposed to one ``dict`` per variable, R
``NA`` -> ``None``) and the expected output is the R ``attendu`` vector. The
``<CODELIST>`` line uses ``norm_path("V3.cdl")`` so the test is self-consistent
regardless of whether the file resolves (mirrors R's
``normalizePath("V3.cdl", mustWork = FALSE)``).
"""

import re

from pytauargus.rda import rda_text, write_rda
from pytauargus.util import norm_path

# One dict per variable (R `input`, transposed). R NA -> None.
INPUT = [
    dict(type_var="RECODEABLE", colname="V1", position=1, width=1, digits=0,
         missing="?", totcode="Total", codelist=None,
         hierarchical=None, hierleadstring=None),
    dict(type_var="RECODEABLE", colname="V2", position=3, width=1, digits=0,
         missing="", totcode="Total", codelist=None,
         hierarchical="V2.hrc", hierleadstring="@"),
    dict(type_var="RECODEABLE", colname="V3", position=5, width=2, digits=0,
         missing="#", totcode="Total", codelist="V3.cdl",
         hierarchical="1 1", hierleadstring=None),
    dict(type_var="NUMERIC", colname="VAL", position=8, width=3, digits=0,
         missing="9999", totcode=None, codelist=None,
         hierarchical=None, hierleadstring=None),
    dict(type_var="WEIGHT", colname="POIDS", position=12, width=4, digits=2,
         missing="#", totcode=None, codelist=None,
         hierarchical=None, hierleadstring=None),
    dict(type_var="HOLDING", colname="HOLD", position=17, width=2, digits=0,
         missing="#", totcode="Total", codelist=None,
         hierarchical=None, hierleadstring=None),
]

CDL_FULL = norm_path("V3.cdl")

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
    '<CODELIST> "%s"' % CDL_FULL,
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


def _split_blocks(blocks):
    # R: `write_rda(input) %>% strsplit("\n  +") %>% unlist()`
    out = []
    for block in blocks:
        out.extend(re.split(r"\n  +", block))
    return out


def test_write_rda():
    assert _split_blocks(write_rda(INPUT)) == ATTENDU


def test_rda_text_flat_file():
    # writeLines(write_rda(...)) -> flat file: each block's sub-lines become
    # their own file line (the 2-space indent is preserved, not stripped)
    text = rda_text(INPUT)
    assert text.endswith("\n")
    assert text.splitlines() == [
        l for b in write_rda(INPUT) for l in b.split("\n")
    ]


def test_write_rda_numeric_only():
    # a bare NUMERIC variable: header + type + decimals, no other tags
    # (write_rda returns ONE string per variable, newlines embedded)
    assert write_rda([
        dict(type_var="NUMERIC", colname="CA", position=1, width=3,
             digits=2, missing=None, totcode=None, codelist=None,
             hierarchical=None, hierleadstring=None),
    ]) == ["CA 1 3\n  <NUMERIC>\n  <DECIMALS> 2"]
