"""Tau-Argus ``.rda`` (metadata) file writer for microdata.

Faithful port of the pure-R ``.rda`` generator in ``references/rtauargus``
(``R/micro_asc_rda.R::write_rda_1var`` + ``write_rda``). Given the per-variable
formatting information (position, width, type, totcode, codelist, hierarchy,
digits) it emits the ``.rda`` text our engine's ``.rda`` parser consumes, so it
is both a fixture generator for the suite and a round-trip validator for the
parser.

Tech-neutral and dependency-free: each variable is a ``dict`` with the keys
``colname``, ``position``, ``width``, ``digits``, ``type_var`` and the optional
(possibly ``None`` / R ``NA``) ``missing``, ``totcode``, ``codelist``,
``hierarchical``, ``hierleadstring``.

Only the ``.rda`` text writer is ported. The surrounding ``micro_asc_rda()``
orchestrator -- which also emits the fixed-width ``.asc`` via ``gdata::write.fwf``
and computes positions/widths/digits from the microdata -- is *not* ported (see
the module docstring note in ``PROGRESS.md``); it is a separate ``.asc``
generator, not part of the ``.rda`` writer.

Function names mirror the R source so the ported tests read as a direct
translation of ``test_micro_asc_rda.R`` (the ``write_rda`` block).
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from pytauargus.util import norm_path

__all__ = [
    "write_rda_1var",
    "write_rda",
]

#: The set of variable types that emit a ``<DECIMALS>`` line.
_NUMERIC_TYPES = ("NUMERIC", "WEIGHT")

#: R: ``grepl("^(\\d+ +)+\\d+$", hierarchical)`` -- a hierarchy-level sequence
#: such as ``"1 1"`` or ``"2 3 0 0 0"``.
_HIERLEVELS_RE = re.compile(r"(\d+ +)+\d+")


def write_rda_1var(info: Dict[str, object]) -> str:
    """Generate the ``.rda`` block for a single variable.

    Port of ``micro_asc_rda.R::write_rda_1var``. Returns a single string whose
    lines (joined by ``\\n``) are, in order:

    * the header ``<colname> <position> <width>[ <missing>]`` (``<missing>``
      only when it is neither ``None`` nor ``""``),
    * ``  <TYPE>`` where ``TYPE`` is ``info["type_var"]``,
    * ``  <TOTCODE> "..."`` when ``totcode`` is not ``None``,
    * ``  <CODELIST> "..."`` when ``codelist`` is not ``None``,
    * ``  <HIERARCHICAL>`` when ``hierarchical`` is not ``None``,
    * ``  <HIERCODELIST> "..."`` and ``  <HIERLEADSTRING> "..."`` when
      ``hierarchical`` ends in ``.hrc``,
    * ``  <HIERLEVELS> ...`` when ``hierarchical`` is a level sequence,
    * ``  <DECIMALS> n`` when the type is ``NUMERIC`` or ``WEIGHT``.
    """
    parts = [str(info["colname"]), str(info["position"]), str(info["width"])]
    missing = info.get("missing")
    if missing is not None and missing != "":
        parts.append(str(missing))
    # R: trimws(paste(colname, position, width, <missing>))
    lines = [" ".join(parts).strip()]

    type_var = info["type_var"]
    lines.append("  <%s>" % type_var)

    totcode = info.get("totcode")
    if totcode is not None:
        lines.append('  <TOTCODE> "%s"' % totcode)

    codelist = info.get("codelist")
    if codelist is not None:
        lines.append('  <CODELIST> "%s"' % codelist)

    hierarchical = info.get("hierarchical")
    if hierarchical is not None:
        lines.append("  <HIERARCHICAL>")
    if hierarchical is not None and hierarchical.endswith(".hrc"):
        lines.append('  <HIERCODELIST> "%s"' % hierarchical)
    if hierarchical is not None and hierarchical.endswith(".hrc"):
        lines.append('  <HIERLEADSTRING> "%s"' % info.get("hierleadstring"))
    if hierarchical is not None and _HIERLEVELS_RE.fullmatch(hierarchical):
        lines.append("  <HIERLEVELS> %s" % hierarchical)

    if type_var in _NUMERIC_TYPES:
        lines.append("  <DECIMALS> %s" % info["digits"])

    return "\n".join(lines)


def write_rda(info_vars: List[Dict[str, object]]) -> List[str]:
    """Generate the ``.rda`` blocks for all variables.

    Port of ``micro_asc_rda.R::write_rda``. Each ``codelist`` path is normalised
    to an absolute path (``normPath2`` / :func:`pytauargus.util.norm_path`); the
    per-variable blocks are then returned as a list of strings (one per
    variable, each with embedded ``\\n``).
    """
    normalized = []
    for info in info_vars:
        info = dict(info)
        codelist = info.get("codelist")
        if codelist is not None:
            info["codelist"] = norm_path(codelist)
        normalized.append(info)

    res = [write_rda_1var(info) for info in normalized]
    # R: gsub("(\\n)+", "\\n", res) then sub("\\n$", "", res) -- collapse any
    # run of newlines and strip a trailing one (no-ops for well-formed blocks,
    # kept for fidelity to the R source).
    res = [re.sub(r"(\n)+", "\n", s) for s in res]
    res = [re.sub(r"\n$", "", s) for s in res]
    return res


def rda_text(info_vars: List[Dict[str, object]]) -> str:
    """Render the full ``.rda`` file text.

    Equivalent to R's ``writeLines(write_rda(info_vars), file)``: each
    per-variable block is written on its own line, so blocks (which contain
    embedded newlines) concatenate into the flat file.
    """
    return "\n".join(write_rda(info_vars)) + "\n"
