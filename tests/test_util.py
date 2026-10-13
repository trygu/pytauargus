"""Ported tests for the shared rtauargus generators.

Direct port of ``references/rtauargus/tests/testthat/test_util.R`` (``cite``,
``df_param_defaut``, ``following_dup``) plus a contract for ``norm_path``
(``normalizePath(mustWork = FALSE)`` semantics, which the ``.arb`` and ``.rda``
writers rely on to produce absolute paths). Expected values were captured by
running the pure-R source in base R.
"""

import os

import pytest

from pytauargus.util import (
    cite,
    df_param_defaut,
    following_dup,
    norm_path,
)
from pytauargus.hrc import HrcError


# --- cite -----------------------------------------------------------------

def test_cite_default():
    assert cite(["A", "", "C"]) == ['"A"', "", '"C"']


def test_cite_custom_quote():
    assert cite(["A", "", "C"], guillemet="*") == ["*A*", "", "*C*"]


def test_cite_keep_empty():
    assert cite(["A", "", "C"], ignore_vide=False) == ['"A"', '""', '"C"']


def test_cite_single():
    assert cite(["REGION"]) == ['"REGION"']
    assert cite([""]) == [""]


# --- norm_path (normalizePath mustWork=FALSE) -----------------------------

def test_norm_path_relative_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # relative, target missing -> returned as given
    assert norm_path("t1.csv") == "t1.csv"
    assert norm_path("tmp/t2.csv") == "tmp/t2.csv"
    # relative with an existing intermediate dir but missing leaf -> as given
    (tmp_path / "sub").mkdir()
    assert norm_path("sub/x.csv") == "sub/x.csv"
    # "./" prefix preserved when the leaf is missing
    assert norm_path("./sub/x.csv") == "./sub/x.csv"
    # unresolved ".." (the resolved leaf would exist, but the path can't be)
    assert norm_path("a/../t1.csv") == "a/../t1.csv"


def test_norm_path_absolute():
    # absolute, missing -> as given
    assert norm_path("/nonexist/a.b") == "/nonexist/a.b"
    # absolute, existing -> as given (already absolute)
    assert norm_path(os.path.dirname(os.path.abspath(__file__))) == \
        os.path.dirname(os.path.abspath(__file__))


def test_norm_path_tilde(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    home = os.path.expanduser("~")
    assert norm_path("~/t3.csv") == os.path.join(home, "t3.csv")
    assert norm_path("~") == home


def test_norm_path_existing_becomes_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "realfile.txt").write_text("x")
    assert norm_path("realfile.txt") == str(tmp_path / "realfile.txt")
    assert norm_path("./realfile.txt") == str(tmp_path / "realfile.txt")


def test_norm_path_dot_dirs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert norm_path(".") == os.path.abspath(str(tmp_path))
    assert norm_path("..") == os.path.abspath(os.path.dirname(str(tmp_path)))


# --- df_param_defaut ------------------------------------------------------

VN = ["V1", "V2", "V3", "V4"]


def _expect(values):
    """Build the expected {colname: value} mapping."""
    return dict(zip(VN, values))


def test_df_param_defaut_no_default():
    assert df_param_defaut(VN, "totcode", None) == _expect([None, None, None, None])
    assert df_param_defaut(VN, "param1", {"V1": "T1", "V3": "T3"}) == \
        _expect(["T1", None, "T3", None])


def test_df_param_defaut_single_default():
    assert df_param_defaut(VN, "totcode", {"_": "---"}) == \
        _expect(["---"] * 4)


def test_df_param_defaut_default_and_exceptions():
    # unnamed default applies to all; named entries override
    assert df_param_defaut(VN, "totcode", {"_": "---", "V1": "T1", "V3": "T3"}) == \
        _expect(["T1", "---", "T3", "---"])


# NOTE: R's ``df_param_defaut`` also warns when the input vector has *multiple*
# unnamed (default) elements, keeping the first. A Python dict cannot hold two
# ``"_"`` keys, so that defensive branch is unreachable here; the real rtauargus
# generators always pass at most one default, so behaviour matches.


def test_df_param_defaut_coerces_to_str():
    assert df_param_defaut(VN, "param1", {"V1": 1, "V3": 3}) == \
        _expect(["1", None, "3", None])


# --- following_dup (mirror hrc.py; here the R integer case) ---------------

def test_following_dup_int():
    assert following_dup([1, 2, 3]) == [False, False, False]
    assert following_dup([1, 1, 2, 1, 1, 3, 2, 2, 2]) == [
        False, True, False, False, True, False, False, True, True,
    ]


def test_following_dup_missing():
    with pytest.raises(HrcError, match="manquante"):
        following_dup([1, None, None])
