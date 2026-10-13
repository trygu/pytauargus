"""Tau-Argus ``.hrc`` (hierarchy) file writer.

This is a faithful port of the pure-R hierarchy logic in ``rtauargus``
(``R/hrc.R``). It sits on the *generator* side of the file-format boundary:
it produces ``.hrc`` files that our own parser (``engine.py`` /
``set_hierarchical_codelist``) consumes. That makes it both a fixture generator
for our suite and a round-trip validator for the parser.

The module is dependency-free and tech-neutral:

* ``microdata`` is a ``dict[str, list]`` — column name -> list of values
  (strings, or ``None`` for a missing value / R ``NA``).
* The hierarchy is represented as a nested ``dict`` (coarsest level outermost,
  finest innermost). A leaf node is ``None``.

Function names and error/warning messages intentionally mirror the R source so
the ported tests read as a direct translation of ``test_hrc.R``.
"""

from __future__ import annotations

import os
import re
import tempfile
import warnings
from typing import Dict, List, Optional, Sequence, Union

__all__ = [
    "HrcError",
    "following_dup",
    "is_hrc",
    "sublevels",
    "imbrique",
    "hrc_list",
    "prof_list",
    "check_seq_prof",
    "fill_na_hrc",
    "df_hierlevels",
    "normalise_hrc",
    "write_hrc",
]

#: A node in the hierarchy: either a leaf (``None``) or a mapping of
#: child-name -> node.
Node = Optional[Dict[str, "Node"]]

#: A microdata frame: column name -> list of cell values (str or None).
Microdata = Dict[str, List[Optional[str]]]

#: A sequence of (name, depth) pairs in DFS pre-order.
ProfSeq = List[tuple]


class HrcError(ValueError):
    """Raised when a hierarchy cannot be built (matches R ``stop`` messages)."""


def _coerce_cols(microdata: Microdata, var: str) -> List[Optional[str]]:
    if var not in microdata:
        raise KeyError(var)
    return [None if v is None else str(v) for v in microdata[var]]


# --- validation -----------------------------------------------------------

def following_dup(x: Sequence[str]) -> List[bool]:
    """Mark each element that is a duplicate of the one immediately before it.

    Port of ``util.R::following_dup``.
    """
    if any(v is None for v in x):
        raise HrcError("impossible : valeur(s) manquante(s)")
    return [False] + [x[i] == x[i - 1] for i in range(1, len(x))]


def is_hrc(crois_fin_agr: Sequence[Sequence[int]]) -> bool:
    """True iff each fine level maps to at most one aggregated level.

    Port of ``hrc.R::is_hrc``. ``crois_fin_agr`` is a contingency whose rows are
    fine levels and columns are aggregated levels; a row filled with zeros
    (caused by NAs) is also accepted. An empty contingency (no fine levels or
    no aggregated levels, as produced by an all-NA crossing) is not a
    hierarchy and returns ``False``.
    """
    if not crois_fin_agr:
        return False
    # empty crossing (no columns) -> not a hierarchy (R: !length())
    if any(len(row) == 0 for row in crois_fin_agr):
        return False
    for row in crois_fin_agr:
        if sum(1 for x in row if x) not in (0, 1):
            return False
    return True


def check_seq_prof(x: Sequence[int]) -> bool:
    """Validate an ordered vector of depths written to an ``.hrc`` file.

    Port of ``hrc.R::check_seq_prof``: the first depth must be 0, all depths
    non-negative integers, and a depth may never rise by more than one or fall
    below zero (no missing levels).
    """
    if not x:
        return False
    if x[0] != 0:
        return False
    if any(v < 0 for v in x):
        return False
    if any(int(v) != v for v in x):
        return False
    for prev, cur in zip(x, x[1:]):
        ecart = cur - prev
        if cur > 0 and ecart > 1:
            return False
        if cur == 0 and ecart > 0:
            return False
    return True


# --- hierarchy construction ----------------------------------------------

def sublevels(fin: Sequence[Optional[str]], agr: Sequence[Optional[str]]) -> Node:
    """Build a two-level hierarchy from a fine vector and an aggregated vector.

    Port of ``hrc.R::sublevels``. Crosses are dropped when either side is a
    missing value. Raises if there is no usable crossing or if the fine ->
    aggregated relation is not a function (a fine value under two different
    aggregated values).
    """
    counts: Dict[tuple, int] = {}
    for f, a in zip(fin, agr):
        if f is None or a is None:
            continue
        key = (str(a), str(f))
        counts[key] = counts.get(key, 0) + 1

    if not counts:
        raise HrcError("aucun croisement exploitable (valeurs manquantes ?)")

    # fine -> unique aggregated (a fine value may also appear only with NAs)
    fin_to_agr: Dict[str, str] = {}
    for a, f in counts:
        if f in fin_to_agr and fin_to_agr[f] != a:
            raise HrcError(
                "variables non hierarchiques "
                "(meme niveau fin dans plusieurs niveaux agreges differents)"
            )
        fin_to_agr[f] = a

    res: Node = {}
    for a in {key[0] for key in counts}:
        res[a] = {}
    for a, f in counts:
        res[a][f] = None
    return {a: {f: None for f in sorted(res[a])} for a in sorted(res)}


def imbrique(fin: Node, agr: Node) -> Node:
    """Nest the fine hierarchy inside the aggregated one.

    Port of ``hrc.R::imbrique``: for each aggregated level keep only the fine
    children that actually occur beneath it.
    """
    res: Node = {}
    for agr_name, agr_children in agr.items():
        res[agr_name] = {c: fin[c] for c in agr_children if c in fin}
    return {k: res[k] for k in sorted(res)}


def hrc_list(df: Microdata, vars_hrc: Sequence[str]) -> Node:
    """Build the full nested hierarchy from several (finest -> coarsest) vars.

    Port of ``hrc.R::hrc_list``. ``vars_hrc`` is ordered from the finest level
    to the most aggregated.
    """
    levs = [
        sublevels(_coerce_cols(df, vars_hrc[i]), _coerce_cols(df, vars_hrc[i + 1]))
        for i in range(len(vars_hrc) - 1)
    ]
    res = levs[0]
    for i in range(1, len(levs)):
        res = imbrique(res, levs[i])
    return res


def prof_list(z: Node, level: int = 0) -> ProfSeq:
    """Depth-first pre-order traversal producing ``[(name, depth), ...]``.

    Port of ``hrc.R::prof_list`` (the R version accumulates in reverse loop
    order but yields the same pre-order sequence).
    """
    out: ProfSeq = []
    for name, child in (z or {}).items():
        out.append((name, level))
        out.extend(prof_list(child, level + 1))
    return out


# --- data preparation -----------------------------------------------------

def fill_na_hrc(microdata: Microdata, vars: Sequence[str]) -> Microdata:
    """Impute missing values by coalescing variables two at a time.

    Port of ``hrc.R::fill_na_hrc``. ``vars`` is ordered so that each element is
    filled from the one before it (R ``dplyr::coalesce``). Only the selected
    columns are returned, coerced to character.
    """
    cols = {v: _coerce_cols(microdata, v) for v in vars}
    for i in range(len(vars) - 1):
        ref = cols[vars[i]]
        cible = cols[vars[i + 1]]
        cols[vars[i + 1]] = [c if c is not None else r for c, r in zip(cible, ref)]
    return cols


def df_hierlevels(var_hrc: Sequence[Optional[str]], hierlevels: str) -> Microdata:
    """Build a frame readable by :func:`hrc_list` from a code + positions.

    Port of ``hrc.R::df_hierlevels``. ``hierlevels`` is a space-separated list
    of digit-widths whose sum equals the code length; each prefix becomes a
    coarser level (columns ``V2``, ``V3``, ...). The first column is the full
    code, named ``var_hrc``.
    """
    hierlevels = hierlevels.strip()
    if not re.fullmatch(r"(\d+ +)+\d+", hierlevels):
        raise HrcError(
            "hierlevels doit contenir plusieurs chiffres separes par des espaces"
        )

    values = [str(v) for v in var_hrc if v is not None]
    # R does unique() on the vector, preserving first-seen order.
    seen: List[str] = []
    for v in values:
        if v not in seen:
            seen.append(v)
    values = seen

    n1 = len(values[0])
    if any(len(v) != n1 for v in values):
        raise HrcError(
            "le nombre de caracteres doit etre identique pour tous les elements"
        )

    lev = [int(t) for t in hierlevels.split(" ")]
    lev = [t for t in lev if t != 0]
    if sum(lev) != n1:
        raise HrcError("la somme de hierlevels doit etre egale au nombre de caracteres")

    # cumsum -> rev -> drop first, mirroring R: `cumsum() %>% rev() %>% `[`(-1)`
    cum: List[int] = []
    s = 0
    for t in lev:
        s += t
        cum.append(s)
    lev = list(reversed(cum))[1:]

    res: Microdata = {"var_hrc": list(values)}
    for i, cut in enumerate(lev):
        res[f"V{i + 2}"] = [v[:cut] for v in values]
    return res


def normalise_hrc(params_hrc: Optional[Sequence[str]],
                  microdata: Optional[Microdata] = None,
                  hierleadstring: Optional[str] = None) -> Optional[List[str]]:
    """Normalise hierarchy parameters passed to a microdata writer.

    Port of ``hrc.R::normalise_hrc``. Three syntaxes are recognised:
    ``"v1 > v2 > ..."`` (build a temporary ``.hrc`` from ``microdata``),
    an existing ``"*.hrc"`` file (normalised path), and a ``"1 2 3"`` level
    spec (left as-is). Anything else raises.
    """
    if params_hrc is None:
        return None

    validrname = r"[.\]?[a-zA-Z][.\w_]*"
    var_re = re.compile(r"^(%s *> *)+%s$" % (validrname, validrname))
    params = list(params_hrc)

    var_idx = [i for i, p in enumerate(params) if var_re.match(p)]
    fich_idx = [i for i, p in enumerate(params) if re.search(r".+\.hrc$", p)]
    lvls_idx = [i for i, p in enumerate(params) if re.fullmatch(r"(\d+ +)+\d+", p)]

    known = set(var_idx) | set(fich_idx) | set(lvls_idx)
    err_idx = [i for i in range(len(params)) if i not in known]
    if err_idx:
        raise HrcError(
            "Parametres hrc incorrects :\n   "
            + "\n   ".join(str(params[i]) for i in err_idx)
        )

    if var_idx:
        if microdata is None:
            raise HrcError(
                "specifier microdata pour construire une hierarchie 'v1 > v2 > ...'"
            )
        if hierleadstring is None:
            raise HrcError("specifier hierleadstring")
        for i in var_idx:
            list_vars = re.split(r" *> *", params[i])
            params[i] = write_hrc(microdata, list_vars,
                                  hierleadstring=hierleadstring)

    for i in fich_idx:
        params[i] = os.path.normpath(params[i])

    return params


# --- file writer ----------------------------------------------------------

def write_hrc(microdata: Microdata,
              vars_hrc: Union[str, Sequence[str]],
              hierleadstring: str = "@",
              hrc_filename: Optional[str] = None,
              fill_na: str = "up",
              compact: bool = True,
              hierlevels: Optional[str] = None) -> str:
    """Write a Tau-Argus ``.hrc`` hierarchy file and return its path.

    Port of ``hrc.R::write_hrc``. ``vars_hrc`` is ordered finest -> coarsest.
    Missing values in the hierarchy variables are imputed with
    :func:`fill_na_hrc` (``"up"`` default) when present, which emits a warning.
    With ``compact=True`` (default) branches that repeat a single value to the
    leaf are pruned.
    """
    if isinstance(vars_hrc, str):
        vars_hrc = [vars_hrc]
    vars_hrc = list(vars_hrc)

    if hrc_filename is None:
        fd, hrc_filename = tempfile.mkstemp(prefix="RTA_", suffix=".hrc")
        os.close(fd)

    if hierleadstring is None:
        hierleadstring = "@"

    # hierlevels: split a single code into positional levels
    if hierlevels is not None:
        if len(vars_hrc) != 1:
            raise HrcError(
                "avec hierlevels, une seule variable hierarchique a specifier"
            )
        microdata = df_hierlevels(microdata[vars_hrc[0]], hierlevels)
        vars_hrc = list(microdata.keys())

    if len(vars_hrc) == 0:
        raise HrcError("length(vars_hrc) > 0 is not TRUE")

    absents = [v for v in vars_hrc if v not in microdata]
    if absents:
        raise HrcError("colonne(s) introuvable(s) : " + ", ".join(absents))

    # single level: sorted unique values, no hierarchy
    if len(vars_hrc) == 1:
        col = _coerce_cols(microdata, vars_hrc[0])
        vals = sorted({v for v in col if v is not None})
        with open(hrc_filename, "w", encoding="utf-8") as fh:
            fh.write("\n".join(vals) + "\n")
        warnings.warn("hierarchie d'un seul niveau", UserWarning, stacklevel=2)
        return hrc_filename

    # impute NAs (only the hierarchy variables are considered, as in R)
    has_na = any(
        any(cell is None for cell in _coerce_cols(microdata, v))
        for v in vars_hrc
    )
    if has_na:
        if fill_na not in ("up", "down"):
            raise HrcError("fill_na must be 'up' or 'down'")
        vars_fill = list(reversed(vars_hrc)) if fill_na == "up" else list(vars_hrc)
        microdata = fill_na_hrc(microdata, vars_fill)
        warnings.warn(
            "valeurs manquantes imputees pour construire la hierarchie",
            UserWarning, stacklevel=2,
        )

    list_hrc = hrc_list(microdata, vars_hrc)
    val_prof = prof_list(list_hrc)

    if compact:
        names = [n for n, _ in val_prof]
        keep = [not d for d in following_dup(names)]
        val_prof = [p for p, k in zip(val_prof, keep) if k]

    res = [hierleadstring * d + name for name, d in val_prof]

    if not check_seq_prof([d for _, d in val_prof]):
        vars_str = " > ".join(vars_hrc)
        res_str = "\n".join(res)
        raise HrcError(
            "Niveaux de hierarchie incoherents '" + vars_str + "'\n"
            "   (essayer avec compact = FALSE ?)\n" + res_str
        )

    with open(hrc_filename, "w", encoding="utf-8") as fh:
        fh.write("\n".join(res) + "\n")
    return hrc_filename
