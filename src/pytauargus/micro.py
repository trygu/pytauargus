"""Tau-Argus microdata writer: fixed-width ``.asc`` + ``.rda`` metadata.

Faithful port of the pure-R ``.rda`` generator in ``references/rtauargus``
(``R/micro_asc_rda.R::write_rda`` / ``write_rda_1var``) and of the fixed-width
``.asc`` writer it delegates to (``gdata::write.fwf``).

The ``.rda`` half is fully self-contained and is validated against the R test
contract (``tests/testthat/test_micro_asc_rda.R``). The ``.asc`` half re-implements
``gdata::write.fwf`` for the microdata use-case (right-justified, single-space
separated, per-column auto decimals) and is validated against the exact bytes
produced by R. See :func:`write_fwf` for the documented scope.

``microdata`` is a ``dict[str, list]`` — column name -> list of values (``str``
or ``None``/missing for character columns, ``int``/``float`` for numeric).

Function names mirror the R source so the ported tests read as a direct
translation of ``test_micro_asc_rda.R``.
"""

import os
import re
import tempfile
import warnings
from typing import Dict, List, Optional, Sequence, Union

from pytauargus.util import df_param_defaut, norm_path
from pytauargus.hrc import HrcError, normalise_hrc

__all__ = [
    "write_rda_1var",
    "write_rda",
    "write_fwf",
    "micro_asc_rda",
]

# Package defaults (options.R::op.rtauargus)
_DEFAULTS = {
    "decimals": 0,
    "hierleadstring": "@",
    "totcode": "Total",
    "missing": "",
}


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _decimals_of(s: str) -> int:
    return len(s.split(".", 1)[1]) if "." in s else 0


def _num_str(v: float) -> str:
    # 15 significant figures, general (no exponent) — mirrors R digits=15.
    return f"{v:.15g}"


def write_fwf(microdata: Dict[str, list],
              decimals: int = 0) -> tuple:
    """Fixed-width writer mirroring ``gdata::write.fwf`` for microdata.

    Returns ``(asc_text, fwf_info)`` where ``fwf_info`` is a list of
    ``{colname, position, width, digits, is_num}`` in column order.

    Documented scope (matches ``gdata::write.fwf`` for the common microdata
    case — ``colnames=FALSE``, ``quote=FALSE``, ``justify="right"``,
    ``scientific=FALSE``, single-space separator, no row names):

    * character column: width = max cell length; cells right-justified.
    * numeric column: decimals = ``max(decimals, max decimal places of the
      values at 15 significant figures)``; each cell formatted to that many
      decimals; width = max formatted length; cells right-justified.
    * columns are joined by a single space.

    Edge cases beyond this (very large / scientific numbers, quoting, explicit
    ``width``) are not covered and should be exercised against R before use.
    """
    cols = list(microdata)
    info: List[dict] = []
    rendered: Dict[str, List[str]] = {}

    for c in cols:
        vals = microdata[c]
        non_na = [v for v in vals if v is not None]
        is_num = bool(non_na) and all(_is_number(v) for v in non_na)

        if is_num:
            dig = max(decimals, max((_decimals_of(_num_str(v)) for v in non_na),
                                    default=0))
            cells = ["" if v is None else f"{v:.{dig}f}" for v in vals]
            width = max(len(x) for x in cells)
            digits = dig
        else:
            cells = ["" if v is None else str(v) for v in vals]
            width = max(len(x) for x in cells) if cells else 0
            digits = 0

        rendered[c] = [x.rjust(width) for x in cells]
        info.append({"colname": c, "width": width, "digits": digits,
                     "is_num": is_num})

    # positions: 1-based start column of each field
    pos = 1
    for row in info:
        row["position"] = pos
        pos += row["width"] + 1

    n = len(next(iter(microdata.values()), []))
    lines = []
    for i in range(n):
        lines.append(" ".join(rendered[c][i] for c in cols))
    asc_text = "\n".join(lines) + ("\n" if lines else "")
    return asc_text, info


def write_rda_1var(info_var: dict) -> str:
    """Render one variable's ``.rda`` block.

    Port of ``micro_asc_rda.R::write_rda_1var``. ``info_var`` carries
    ``type_var``, ``colname``, ``position``, ``width``, ``digits``,
    ``missing``, ``totcode``, ``codelist``, ``hierarchical``,
    ``hierleadstring`` (``None`` = R ``NA``).
    """
    colname = info_var["colname"]
    position = info_var["position"]
    width = info_var["width"]
    missing = info_var.get("missing")
    type_var = info_var["type_var"]
    totcode = info_var.get("totcode")
    codelist = info_var.get("codelist")
    hierarchical = info_var.get("hierarchical")
    hierleadstring = info_var.get("hierleadstring")

    head = [str(colname), str(position), str(width)]
    if missing is not None and missing != "":
        head.append(str(missing))
    ligne1 = " ".join(head)

    body: List[str] = []
    body.append("  <%s>" % type_var)
    if totcode is not None:
        body.append('  <TOTCODE> "%s"' % totcode)
    if codelist is not None:
        body.append('  <CODELIST> "%s"' % codelist)
    if hierarchical is not None:
        body.append("  <HIERARCHICAL>")
        if re.search(r"\.hrc$", hierarchical):
            body.append('  <HIERCODELIST> "%s"' % hierarchical)
            body.append('  <HIERLEADSTRING> "%s"' % hierleadstring)
        if re.fullmatch(r"(\d+ +)+\d+", hierarchical):
            body.append("  <HIERLEVELS> %s" % hierarchical)
    if type_var in ("NUMERIC", "WEIGHT"):
        body.append("  <DECIMALS> %s" % info_var["digits"])

    # R: paste(sep = "\n", ligne1, body...) — newline before every line
    return "\n".join([ligne1] + body)


def write_rda(info_vars: Sequence[dict]) -> List[str]:
    """Render the full ``.rda`` text as a list of per-variable blocks.

    Port of ``micro_asc_rda.R::write_rda``. ``codelist`` paths are normalised
    (``normalizePath``-like) before rendering. The ``.rda`` file written by
    :func:`micro_asc_rda` is ``"\\n".join(blocks) + "\\n"``.
    """
    blocks = []
    for iv in info_vars:
        iv = dict(iv)
        if iv.get("codelist") is not None:
            iv["codelist"] = norm_path(iv["codelist"])
        blocks.append(write_rda_1var(iv))
    return blocks


def micro_asc_rda(microdata: Dict[str, list],
                  asc_filename: Optional[str] = None,
                  rda_filename: Optional[str] = None,
                  weight_var: Optional[str] = None,
                  holding_var: Optional[str] = None,
                  decimals: Optional[int] = None,
                  hrc: Optional[Dict[str, str]] = None,
                  hierleadstring: Optional[str] = None,
                  totcode: Optional[str] = None,
                  missing: Optional[Dict[str, str]] = None,
                  codelist: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Write a fixed-width ``.asc`` and a ``.rda`` metadata file from microdata.

    Port of ``micro_asc_rda.R::micro_asc_rda``. ``hrc``/``missing``/``codelist``
    use the named-default syntax (``{"_": default, "VAR": override}``); an
    ``hrc`` entry may be ``"var.hrc"`` (a file), ``"1 1"`` (hierlevels) or
    ``"v1 > v2"`` (build a temp hrc from ``microdata``).

    Returns ``{"asc_filename", "rda_filename"}``.
    """
    decimals = _DEFAULTS["decimals"] if decimals is None else decimals
    hierleadstring = _DEFAULTS["hierleadstring"] if hierleadstring is None else hierleadstring
    totcode = _DEFAULTS["totcode"] if totcode is None else totcode
    missing = dict(missing) if missing is not None else {"_": _DEFAULTS["missing"]}

    # drop empty columns (all NA or "")
    cols = list(microdata)
    colvides = [c for c in cols if all(v is None or v == "" for v in microdata[c])]
    if colvides:
        warnings.warn("Colonnes vides non exportees en asc : " + ", ".join(colvides),
                      UserWarning, stacklevel=2)
        microdata = {c: microdata[c] for c in cols if c not in colvides}
        cols = [c for c in cols if c not in colvides]

    if asc_filename is None:
        asc_filename = tempfile.mkstemp(prefix="RTA_", suffix=".asc")[1]
    if rda_filename is None:
        rda_filename = re.sub(r"asc$", "rda", asc_filename)

    # fixed-width asc + per-column layout
    asc_text, info = write_fwf(microdata, decimals=decimals)
    with open(asc_filename, "w", encoding="utf-8") as fh:
        fh.write(asc_text)

    by_col = {r["colname"]: r for r in info}
    names = list(microdata)
    num = {c: by_col[c]["is_num"] for c in names}
    var_quanti = [c for c in names if not num[c]]  # categorical (character)

    # per-variable parameters (named-default syntax)
    missing_map = df_param_defaut(names, "missing", missing)
    codelist_map = df_param_defaut(var_quanti, "codelist", codelist)
    totcode_map = df_param_defaut(var_quanti, "totcode",
                                  None if totcode is None else {"_": totcode})

    # hierarchical
    if hrc is not None:
        if not hrc:
            hrc = {}
        norm_hrc = normalise_hrc(list(hrc.values()) if len(hrc) else None,
                                 microdata=microdata,
                                 hierleadstring=hierleadstring)
        hrc_list = dict(zip(list(hrc.keys()), norm_hrc)) if norm_hrc else {}
    else:
        hrc_list = {}
    hierarchical_map = {c: hrc_list.get(c) for c in var_quanti}
    hierleadstring_map = {
        c: (hierleadstring if hierarchical_map.get(c) and
            re.search(r"\.hrc$", hierarchical_map[c]) else None)
        for c in var_quanti
    }

    # assemble per-variable rda info, in column order
    info_vars: List[dict] = []
    for c in names:
        r = by_col[c]
        t = "NUMERIC" if num[c] else "RECODEABLE"
        if weight_var == c:
            t = "WEIGHT"
        if holding_var == c:
            t = "HOLDING"
        info_vars.append({
            "type_var": t,
            "colname": c,
            "position": r["position"],
            "width": r["width"],
            "digits": r["digits"],
            "missing": missing_map.get(c),
            "totcode": totcode_map.get(c),
            "codelist": codelist_map.get(c),
            "hierarchical": hierarchical_map.get(c),
            "hierleadstring": hierleadstring_map.get(c),
        })

    blocks = write_rda(info_vars)
    with open(rda_filename, "w", encoding="utf-8") as fh:
        fh.write("\n".join(blocks) + "\n")

    return {
        "asc_filename": norm_path(asc_filename),
        "rda_filename": norm_path(rda_filename),
    }
