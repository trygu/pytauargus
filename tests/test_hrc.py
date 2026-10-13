"""Ported tests for the ``.hrc`` hierarchy writer.

Direct port of ``references/rtauargus/tests/testthat/test_hrc.R``. Each test
uses a fresh microdata frame (mirroring R testthat's per-test environment
isolation, which the R ``write_hrc`` tests rely on — in particular the
"validité arbre" case only errors when ``df`` is otherwise clean).

Expected values were captured by running the pure-R source
(``rtauargus/R/hrc.R``) in base R, so the port is validated against the
authoritative generator contract, not by re-derivation.
"""

import pytest

from pytauargus.hrc import (
    HrcError,
    following_dup,
    is_hrc,
    sublevels,
    imbrique,
    hrc_list,
    prof_list,
    check_seq_prof,
    fill_na_hrc,
    df_hierlevels,
    normalise_hrc,
    write_hrc,
)


# --- test data (from test_hrc.R) -----------------------------------------

@pytest.fixture()
def df():
    return {
        "niv1": _rep(["A", "B"], [27, 23]),
        "niv2": _rep(["A1", "A2", "B1", "B2"], [17, 10, 10, 13]),
        "niv3": _rep(
            ["A1x", "A1z", "A2y", "A2z", "B1x", "B1y", "B2x", "B2z"],
            [16, 1, 8, 2, 9, 1, 10, 3],
        ),
        "niv4": _rep(
            ["A1x6", "A1x7", "A1z6", "A2y7", "A2y6", "A2z7", "A2z6",
             "B1x7", "B1x6", "B1y6", "B2x7", "B2x6", "B2z6"],
            [7, 9, 1, 5, 3, 1, 1, 6, 3, 1, 7, 3, 3],
        ),
    }


def _rep(values, counts):
    return [v for v, c in zip(values, counts) for _ in range(c)]


def read_lines(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


# --- fill_na_hrc ----------------------------------------------------------

def test_fill_na_hrc_configs_and_order():
    df_na = {
        "niv1": ["A", "A", "B", None, None, None, None],
        "niv2": ["A1", "A2", None, None, "D", None, "F1"],
        "niv3": ["A1x", None, None, None, None, "E", "F"],
    }

    assert fill_na_hrc(df_na, ["niv1", "niv2", "niv3"]) == {
        "niv1": ["A", "A", "B", None, None, None, None],
        "niv2": ["A1", "A2", "B", None, "D", None, "F1"],
        "niv3": ["A1x", "A2", "B", None, "D", "E", "F"],
    }

    # inverted order
    assert fill_na_hrc(df_na, ["niv3", "niv2", "niv1"]) == {
        "niv3": ["A1x", None, None, None, None, "E", "F"],
        "niv2": ["A1", "A2", None, None, "D", "E", "F1"],
        "niv1": ["A", "A", "B", None, "D", "E", "F1"],
    }


# --- sublevels ------------------------------------------------------------

SUBL12 = {"A": {"A1": None, "A2": None}, "B": {"B1": None, "B2": None}}
SUBL13 = {
    "A": {"A1x": None, "A1z": None, "A2y": None, "A2z": None},
    "B": {"B1x": None, "B1y": None, "B2x": None, "B2z": None},
}
SUBL14 = {
    "A": {"A1x6": None, "A1x7": None, "A1z6": None, "A2y6": None,
          "A2y7": None, "A2z6": None, "A2z7": None},
    "B": {"B1x6": None, "B1x7": None, "B1y6": None, "B2x6": None,
          "B2x7": None, "B2z6": None},
}
SUBL23 = {
    "A1": {"A1x": None, "A1z": None},
    "A2": {"A2y": None, "A2z": None},
    "B1": {"B1x": None, "B1y": None},
    "B2": {"B2x": None, "B2z": None},
}
SUBL24 = {
    "A1": {"A1x6": None, "A1x7": None, "A1z6": None},
    "A2": {"A2y6": None, "A2y7": None, "A2z6": None, "A2z7": None},
    "B1": {"B1x6": None, "B1x7": None, "B1y6": None},
    "B2": {"B2x6": None, "B2x7": None, "B2z6": None},
}
SUBL34 = {
    "A1x": {"A1x6": None, "A1x7": None},
    "A1z": {"A1z6": None},
    "A2y": {"A2y6": None, "A2y7": None},
    "A2z": {"A2z6": None, "A2z7": None},
    "B1x": {"B1x6": None, "B1x7": None},
    "B1y": {"B1y6": None},
    "B2x": {"B2x6": None, "B2x7": None},
    "B2z": {"B2z6": None},
}


def test_sublevels_normal(df):
    assert sublevels(df["niv2"], df["niv1"]) == SUBL12
    assert sublevels(df["niv3"], df["niv1"]) == SUBL13
    assert sublevels(df["niv4"], df["niv1"]) == SUBL14
    assert sublevels(df["niv3"], df["niv2"]) == SUBL23
    assert sublevels(df["niv4"], df["niv2"]) == SUBL24
    assert sublevels(df["niv4"], df["niv3"]) == SUBL34


def test_sublevels_presence_nas():
    with pytest.raises(HrcError, match="aucun croisement exploitable"):
        sublevels([None, None], [None, None])
    with pytest.raises(HrcError, match="aucun croisement exploitable"):
        sublevels(["A", None], [None, None])
    with pytest.raises(HrcError, match="aucun croisement exploitable"):
        sublevels(["A1", None], [None, "B"])

    res = {"A": {"A1": None}}

    assert sublevels(["A1", None], ["A", None]) == res
    # B disappears because of the NA at the lower level
    assert sublevels(["A1", None], ["A", "B"]) == res
    # B1 disappears because of the NA at the upper level
    assert sublevels(["A1", "B1"], ["A", None]) == res
    assert sublevels(["A1", "A1"], ["A", None]) == res
    # several cases combined
    assert sublevels(["A1", "B2", None, None], ["A", None, "B", None]) == res


def test_sublevels_detects_non_hierarchical(df):
    for fine, agr in [
        (df["niv1"], df["niv2"]),
        (df["niv1"], df["niv3"]),
        (df["niv1"], df["niv4"]),
        (df["niv2"], df["niv3"]),
        (df["niv2"], df["niv4"]),
        (df["niv3"], df["niv4"]),
    ]:
        with pytest.raises(HrcError, match="variables non hierarchiques"):
            sublevels(fine, agr)


# --- imbrique -------------------------------------------------------------

P12 = SUBL12
P23 = SUBL23
I13 = {
    "A": {"A1": {"A1x": None, "A1z": None}, "A2": {"A2y": None, "A2z": None}},
    "B": {"B1": {"B1x": None, "B1y": None}, "B2": {"B2x": None, "B2z": None}},
}


def test_imbrique_normal():
    assert imbrique(P23, P12) == I13


def test_imbrique_melange():
    # melange input 1 (reorder fine levels)
    p23_mel = {k: P23[k] for k in ("B1", "A2", "B2", "A1")}
    assert imbrique(p23_mel, P12) == I13
    # melange input 2 (reorder aggregated levels)
    p12_mel = {k: P12[k] for k in ("B", "A")}
    # R: imbrique(p_23, p_12[2:1]) == i_13[2:1] (output follows input 2 order)
    assert imbrique(P23, p12_mel) == {k: I13[k] for k in ("B", "A")}
    # melange inputs 1 and 2
    assert imbrique(p23_mel, p12_mel) == {k: I13[k] for k in ("B", "A")}


# --- hrc_list -------------------------------------------------------------

HRC123 = I13
HRC124 = {
    "A": {
        "A1": {"A1x6": None, "A1x7": None, "A1z6": None},
        "A2": {"A2y6": None, "A2y7": None, "A2z6": None, "A2z7": None},
    },
    "B": {
        "B1": {"B1x6": None, "B1x7": None, "B1y6": None},
        "B2": {"B2x6": None, "B2x7": None, "B2z6": None},
    },
}
HRC1234 = {
    "A": {
        "A1": {"A1x": {"A1x6": None, "A1x7": None}, "A1z": {"A1z6": None}},
        "A2": {"A2y": {"A2y6": None, "A2y7": None},
               "A2z": {"A2z6": None, "A2z7": None}},
    },
    "B": {
        "B1": {"B1x": {"B1x6": None, "B1x7": None}, "B1y": {"B1y6": None}},
        "B2": {"B2x": {"B2x6": None, "B2x7": None}, "B2z": {"B2z6": None}},
    },
}


def test_hrc_list_2_input(df):
    # 2 inputs is equivalent to sublevels
    assert hrc_list(df, ["niv2", "niv1"]) == SUBL12
    assert hrc_list(df, ["niv4", "niv1"]) == SUBL14
    assert hrc_list(df, ["niv4", "niv2"]) == SUBL24
    assert hrc_list(df, ["niv3", "niv2"]) == SUBL23


def test_hrc_list_3_input(df):
    assert hrc_list(df, ["niv3", "niv2", "niv1"]) == HRC123
    assert hrc_list(df, ["niv4", "niv2", "niv1"]) == HRC124


def test_hrc_list_4_input(df):
    assert hrc_list(df, ["niv4", "niv3", "niv2", "niv1"]) == HRC1234


# --- prof_list ------------------------------------------------------------

def test_prof_list_2_levels():
    assert prof_list(SUBL12) == [
        ("A", 0), ("A1", 1), ("A2", 1), ("B", 0), ("B1", 1), ("B2", 1),
    ]


def test_prof_list_3_levels():
    assert prof_list(HRC124) == [
        ("A", 0), ("A1", 1), ("A1x6", 2), ("A1x7", 2), ("A1z6", 2),
        ("A2", 1), ("A2y6", 2), ("A2y7", 2), ("A2z6", 2), ("A2z7", 2),
        ("B", 0), ("B1", 1), ("B1x6", 2), ("B1x7", 2), ("B1y6", 2),
        ("B2", 1), ("B2x6", 2), ("B2x7", 2), ("B2z6", 2),
    ]


def test_prof_list_4_levels():
    assert prof_list(HRC1234) == [
        ("A", 0), ("A1", 1), ("A1x", 2), ("A1x6", 3), ("A1x7", 3),
        ("A1z", 2), ("A1z6", 3), ("A2", 1), ("A2y", 2), ("A2y6", 3),
        ("A2y7", 3), ("A2z", 2), ("A2z6", 3), ("A2z7", 3),
        ("B", 0), ("B1", 1), ("B1x", 2), ("B1x6", 3), ("B1x7", 3),
        ("B1y", 2), ("B1y6", 3), ("B2", 1), ("B2x", 2), ("B2x6", 3),
        ("B2x7", 3), ("B2z", 2), ("B2z6", 3),
    ]


# --- is_hrc ---------------------------------------------------------------

def test_is_hrc_from_precomputed():
    assert is_hrc([[1, 0], [0, 2]])
    assert is_hrc([[1, 0], [0, 2], [8, 0]])
    # two aggregated levels for a single fine level
    assert not is_hrc([[7, 1], [0, 2]])
    assert is_hrc([[1, 0], [0, 0]])


def test_is_hrc_from_table():
    # from a contingency table of (fine, aggregated) pairs, mirroring R table()
    # which keeps fine levels with zero counts but drops empty factor levels.
    def table(fin, agr):
        cols = sorted({a for a in agr if a is not None})
        fines = []
        for f in fin:
            if f is not None and f not in fines:
                fines.append(f)
        mat = []
        for f in fines:
            row = [0] * len(cols)
            for fi, ai in zip(fin, agr):
                if fi == f and ai is not None:
                    row[cols.index(ai)] += 1
            mat.append(row)
        return mat

    assert is_hrc(table(["A1", "A2"], ["A", "A"]))
    assert is_hrc(table(["A1", "A2", "B1"], ["A", "A", "B"]))
    assert is_hrc(table(["A1", "A2"], ["A", None]))
    assert is_hrc(table(["A1", None], ["A", "B"]))
    assert is_hrc(table(["A1", "A2", "B1"], ["A", "A", "B"]))
    assert is_hrc(table(["A1", "A2", "B1", None], ["A", "A", "B", "B"]))
    assert is_hrc(table(["A1", "A2", "B1", None], ["A", "A", "B", "C"]))
    # row of 0 for B2 (caused by NA) is accepted
    assert is_hrc(table(["A1", "B1", "B2"], ["A", "B", None]))

    # FALSE
    # two aggregated levels for a single fine level
    assert not is_hrc(table(["A1", "A1"], ["A", "B"]))
    assert not is_hrc(table(["A1", "A2"], [None, None]))
    assert not is_hrc(table([None, None], ["A", "A"]))


# --- check_seq_prof -------------------------------------------------------

def test_check_seq_prof_invalid():
    assert not check_seq_prof([])
    assert not check_seq_prof([1, 2])
    assert not check_seq_prof([0, -1])
    # non-integer depths are rejected
    assert not check_seq_prof([0.0, 0.5])


def test_check_seq_prof_valid():
    assert check_seq_prof([0, 1, 2])
    assert not check_seq_prof([0, 2])
    assert check_seq_prof([0, 1, 1, 0, 1, 2])
    assert check_seq_prof([0, 1, 2, 3, 2])
    assert check_seq_prof([0, 1, 2, 3, 1])
    assert check_seq_prof([0, 1, 1, 2, 3, 3, 2, 3, 3, 2, 0, 1, 0])


# --- following_dup --------------------------------------------------------

def test_following_dup():
    assert following_dup(["A", "A", "B"]) == [False, True, False]
    assert following_dup(["A", "B"]) == [False, False]
    with pytest.raises(HrcError):
        following_dup(["A", None])


# --- normalise_hrc --------------------------------------------------------

def test_normalise_hrc_nothing_to_do():
    assert normalise_hrc(None) is None
    assert normalise_hrc(["1 2", "3 4 5"]) == ["1 2", "3 4 5"]


def test_normalise_hrc_invalid_value():
    with pytest.raises(HrcError, match="Parametres hrc incorrects"):
        normalise_hrc(["fich"])
    with pytest.raises(HrcError, match="Parametres hrc incorrects"):
        normalise_hrc(["3 Q 5"])


def test_normalise_hrc_missing_microdata():
    with pytest.raises(HrcError, match="specifier microdata"):
        normalise_hrc(["V1>V2"])


# --- df_hierlevels --------------------------------------------------------

def test_df_hierlevels_base(df):
    assert df_hierlevels(df["niv2"], "1 1") == {
        "var_hrc": ["A1", "A2", "B1", "B2"],
        "V2": ["A", "A", "B", "B"],
    }
    assert df_hierlevels(df["niv3"], "1 1 1") == {
        "var_hrc": ["A1x", "A1z", "A2y", "A2z", "B1x", "B1y", "B2x", "B2z"],
        "V2": ["A1", "A1", "A2", "A2", "B1", "B1", "B2", "B2"],
        "V3": ["A", "A", "A", "A", "B", "B", "B", "B"],
    }


def test_df_hierlevels_error(df):
    with pytest.raises(HrcError, match="chiffres separes par des espaces"):
        df_hierlevels(df["niv2"], "1 un")
    with pytest.raises(HrcError, match="somme de hierlevels"):
        df_hierlevels(df["niv4"], "1 2 3")
    with pytest.raises(HrcError, match="nombre de caracteres"):
        df_hierlevels(df["niv3"] + df["niv2"], "1 3")


# --- write_hrc ------------------------------------------------------------

def test_write_hrc_hierlevels(df):
    with pytest.raises(HrcError, match="une seule variable hierarchique"):
        write_hrc(df, ["niv1", "niv2"], hierlevels="2 1")

    assert read_lines(write_hrc(df, "niv2", hierlevels="1 1")) == [
        "A", "@A1", "@A2", "B", "@B1", "@B2",
    ]


def test_write_hrc_column_absent(df):
    with pytest.raises(HrcError, match=r"introuvable.* niv0$"):
        write_hrc(df, ["niv1", "niv0"])


def test_write_hrc_single_level(df):
    with pytest.warns(UserWarning, match="hierarchie d'un seul niveau"):
        res = write_hrc(df, "niv2")
    assert read_lines(res) == ["A1", "A2", "B1", "B2"]


def test_write_hrc_fill_na(df):
    df["niv2"] = [None if v == "A1" else v for v in df["niv2"]]

    # fill_na "up"
    with pytest.warns(UserWarning, match="valeurs manquantes imputees"):
        res = write_hrc(df, ["niv2", "niv1"], fill_na="up", compact=False)
    assert read_lines(res) == ["A", "@A", "@A2", "B", "@B1", "@B2"]
    # ex-A1 imputed from above become A

    # fill_na "down"
    with pytest.warns(UserWarning, match="valeurs manquantes imputees"):
        res = write_hrc(df, ["niv2", "niv1"], fill_na="down", compact=False)
    assert read_lines(res) == ["A", "@A2", "B", "@B1", "@B2"]
    # no level below niv2: down imputation has no effect, A x NA ignored

    # keep only A rows, then impute down on niv3/niv2
    df_a = {k: [v for v, n1 in zip(df[k], df["niv1"]) if n1 == "A"]
            for k in df}
    with pytest.warns(UserWarning, match="valeurs manquantes imputees"):
        res = write_hrc(df_a, ["niv3", "niv2"], fill_na="down", compact=False)
    assert read_lines(res) == [
        "A1x", "@A1x", "A1z", "@A1z", "A2", "@A2y", "@A2z",
    ]  # ex-A1 imputed from below become A1x and A1z


def test_write_hrc_compact(df):
    df["niv3"] = [None if n1 == "A" else v for v, n1 in zip(df["niv3"], df["niv1"])]

    with pytest.warns(UserWarning, match="valeurs manquantes imputees"):
        res = write_hrc(df, ["niv3", "niv2", "niv1"], compact=True)
    assert read_lines(res) == [
        "A", "@A1", "@A2", "B", "@B1", "@@B1x", "@@B1y", "@B2", "@@B2x", "@@B2z",
    ]

    df["niv2"] = [None if n1 == "A" else v for v, n1 in zip(df["niv2"], df["niv1"])]

    with pytest.warns(UserWarning, match="valeurs manquantes imputees"):
        res = write_hrc(df, ["niv3", "niv2", "niv1"], compact=True)
    assert read_lines(res) == [
        "A", "B", "@B1", "@@B1x", "@@B1y", "@B2", "@@B2x", "@@B2z",
    ]


def test_write_hrc_tree_validity(df):
    # empty intermediate level (will error when compacting)
    df["niv2"] = [None] * len(df["niv2"])
    with pytest.raises(HrcError, match="Niveaux de hierarchie incoherents"):
        with pytest.warns(UserWarning):
            write_hrc(df, ["niv3", "niv2", "niv1"], compact=True)


def test_write_hrc_hierleadstring(df):
    assert read_lines(write_hrc(df, ["niv2", "niv1"], hierleadstring=":")) == [
        "A", ":A1", ":A2", "B", ":B1", ":B2",
    ]


# --- round trip with our parser -------------------------------------------

def test_roundtrip_hrc_matches_parser_leadstring():
    """A hierarchy we write is read back with the same lead string.

    Our engine parser strips the lead string repeatedly (engine.py:1041-1047),
    so the written file must encode depth by repeating the lead. This guards
    the generator<->parser contract without invoking the native engine.
    """
    micro = {
        "reg": ["A", "A", "B", "B"],
        "dep": ["A1", "A2", "B1", "B2"],
        "com": ["A1x", "A1y", "B1x", "B2x"],
    }
    path = write_hrc(micro, ["com", "dep", "reg"], hierleadstring="@")
    lines = read_lines(path)
    # every line's depth is encoded by the number of leading "@"
    for line in lines:
        depth = len(line) - len(line.lstrip("@"))
        assert line.lstrip("@")  # a name remains after stripping the lead
    assert lines == [
        "A", "@A1", "@@A1x", "@A2", "@@A1y",
        "B", "@B1", "@@B1x", "@B2", "@@B2x",
    ]
