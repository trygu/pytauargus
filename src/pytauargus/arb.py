"""Tau-Argus ``.arb`` (batch) file writer for microdata.

Faithful port of the pure-R ``.arb`` generator in ``references/rtauargus``
(``R/micro_arb.R`` + the ``specif_safety``/``suppr_writetable``/``apriori_batch``
helpers). It produces the batch text our engine's ``.arb`` parser consumes, so it
is both a fixture generator for the suite and a round-trip validator for the
parser.

Dependency-free and tech-neutral:

* ``explanatory_vars`` is a ``list`` of one list of variable names per table
  (a table crosses its explanatory variables); a single table may be passed as a
  plain list of names.
* Named entries in ``explanatory_vars`` become a ``// <TABLE_ID> "..."`` comment.
* ``suppress``/``linked``/``output_*`` mirror the R argument semantics,
  including the special rule that a *single* ``suppress`` has its table number
  (the first parenthesised argument) recalculated per table.

Function names mirror the R source so the ported tests read as a direct
translation of ``test_micro_arb.R``.
"""

import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Union

from pytauargus.util import (
    cite,
    df_param_defaut,
    norm_path,
    output_extensions,
)

__all__ = [
    "ArbError",
    "suppr_writetable",
    "specif_safety",
    "apriori_batch",
    "norm_apriori_params",
    "micro_arb",
]

# A variable list per table; each inner list is the explanatory (row/col) vars.
ExplanatoryVars = Union[Sequence[str], Sequence[Sequence[str]]]

# R: `paste0("<SPECIFYTABLE>", ...)`; the explanatory vars are quoted and joined
# with no separator (mapply recycling is irrelevant for a proper 1-table-1-vector
# layout). A scalar value is passed straight through ``cite``.


class ArbError(ValueError):
    """Raised on an invalid ``.arb`` request (matches R ``stop`` messages)."""


def specif_safety(explanatory_vars: Sequence[Sequence[str]],
                  response_var: Sequence[str],
                  shadow_var: Optional[Sequence[str]],
                  cost_var: Optional[Sequence[str]],
                  safety_rules: Sequence[str],
                  weighted: Sequence[bool]) -> List[str]:
    """Generate the ``<SPECIFYTABLE>`` + ``<SAFETYRULE>`` blocks.

    Port of ``micro_arb.R::specif_safety``. Each table contributes::

        <SPECIFYTABLE> <expl>|<resp>|<shadow>|<cost>
        <SAFETYRULE> <rule>[|Wgt(1)]

    where ``<expl>`` is each explanatory variable quoted and concatenated with no
    separator. If a table's explanatory vars are *named* (a ``dict`` with names),
    a ``// <TABLE_ID> "<name>"`` comment precedes the ``<SPECIFYTABLE>``.
    ``Wgt(1)`` is appended to the safety rule when the table is weighted.
    """
    def _at(i, seq, default=""):
        # R recycles a length-1 argument across tables; longer ones must align.
        if seq is None or len(seq) == 0:
            return default
        if len(seq) == 1:
            return seq[0]
        if len(seq) != len(explanatory_vars):
            raise ArbError("longueur arguments")
        return seq[i]

    out: List[str] = []
    for i, expl in enumerate(explanatory_vars):
        if isinstance(expl, dict):
            # named var list: {"TABLE_ID": [vars...]} -> // <TABLE_ID> "..."
            table_id = next(iter(expl))
            expl_names = list(expl[table_id])
            head = '// <TABLE_ID> "%s"\n' % table_id
        else:
            head = ""
            expl_names = list(expl)

        specify = (
            "<SPECIFYTABLE> "
            + "".join(cite(expl_names))
            + "|" + cite([_at(i, response_var)])[0] + "|"
            + cite([_at(i, shadow_var)])[0] + "|"
            + cite([_at(i, cost_var)])[0]
        )
        rule = _at(i, safety_rules)
        if (weighted[0] if len(weighted) == 1 else weighted[i]):
            rule = rule + "|Wgt(1)"
        out.append(head + specify + "\n<SAFETYRULE> " + rule)
    return out


def suppr_writetable(suppress: Union[str, Sequence[str]],
                     linked: bool,
                     output_names: Sequence[str],
                     output_type: Union[str, Sequence[str]],
                     output_options: Union[str, Sequence[str]]) -> List[str]:
    """Generate the ``<SUPPRESS>`` + ``<WRITETABLE>`` commands.

    Port of ``micro_arb.R::suppr_writetable``.

    * ``linked=True`` allows a single ``suppress`` (applied once, table number
      forced to ``0``) followed by one ``<WRITETABLE>`` per table on its own line.
    * ``linked=False`` emits a ``<SUPPRESS>`` + ``<WRITETABLE>`` pair (joined with
      ``\\n``) per table. A *single* ``suppress`` has its first parenthesised
      argument (the table number) recalculated to 1,2,3... per table; ``###``/``.``
      /any placeholder is replaced the same way.
    """
    n = len(output_names)
    num_table = list(range(1, n + 1))

    if linked and isinstance(suppress, (list, tuple)) and len(suppress) > 1:
        raise ArbError("un seul suppress permis quand linked = TRUE")

    if isinstance(suppress, str):
        suppress = [suppress]

    if len(suppress) == 1:
        if linked:
            suppr_n = [0]
        else:
            suppr_n = num_table
        no_space = suppress[0].replace(" ", "")
        # replace the first parenthesised argument with the table number
        fmt = re.sub(r"\([^),]+(,*.*)\)", r"(%i\1)", no_space)
        suppress = [fmt % i for i in suppr_n]

    out_names = [norm_path(p) for p in output_names]
    if isinstance(output_type, str):
        output_type = [output_type] * n
    if isinstance(output_options, str):
        output_options = [output_options] * n

    suppr_cmd = ["<SUPPRESS> " + s for s in suppress]
    write_cmd = [
        '<WRITETABLE> (%d,%s,%s,"%s")' % (num_table[i], output_type[i],
                                           output_options[i], out_names[i])
        for i in range(n)
    ]

    if linked:
        return [suppr_cmd[0]] + write_cmd
    return [s + "\n" + w for s, w in zip(suppr_cmd, write_cmd)]


def norm_apriori_params(params: Union[Sequence[str], Dict[str, object]]) -> Dict[str, object]:
    """Normalise the ``apriori`` parameter.

    Port of ``micro_arb.R::norm_apriori_params``. A list of hst filenames is
    wrapped with default options; a ``dict`` may supply ``sep``/``ignore_err``/
    ``exp_triv`` (defaults ``,``/``0``/``0``) with the hst files under ``"hst"``.
    """
    def _hst(x):
        return [x] if isinstance(x, str) else list(x)

    if isinstance(params, (list, tuple)):
        return {"hst": _hst(params), "sep": ",", "ignore_err": 0, "exp_triv": 0}
    if isinstance(params, dict):
        return {
            "hst": _hst(params["hst"]),
            "sep": params.get("sep", ","),
            "ignore_err": params.get("ignore_err", 0),
            "exp_triv": params.get("exp_triv", 0),
        }
    raise ArbError("apriori doit etre une liste de fichiers hst ou un dict")


def apriori_batch(ntab: int,
                  hst_names: Sequence[str],
                  sep: str = ",",
                  ignore_err: int = 0,
                  exp_triv: int = 0) -> List[str]:
    """Generate one ``<APRIORI>`` line per table.

    Port of ``micro_arb.R::apriori_batch``. A single ``hst_names`` entry is used
    for every table; otherwise there must be one per table. ``sep`` /
    ``ignore_err`` / ``exp_triv`` are scalars (recycled across tables).
    """
    hst_names = list(hst_names)
    if len(hst_names) != 1 and len(hst_names) != ntab:
        raise ArbError("longueur arguments")

    out = []
    for i in range(1, ntab + 1):
        hst = norm_path(hst_names[0] if len(hst_names) == 1 else hst_names[i - 1])
        out.append('<APRIORI> "%s",%d,"%s",%s,%s' % (hst, i, sep, ignore_err, exp_triv))
    return out


def micro_arb(arb_filename: Optional[str] = None,
              asc_filename: Optional[str] = None,
              rda_filename: Optional[str] = None,
              explanatory_vars: Optional[ExplanatoryVars] = None,
              response_var: Union[str, Sequence[str]] = "<freq>",
              shadow_var: Optional[Union[str, Sequence[str]]] = None,
              cost_var: Optional[Union[str, Sequence[str]]] = None,
              safety_rules: Union[str, Sequence[str], None] = None,
              weighted: Union[bool, Sequence[bool]] = False,
              suppress: Optional[Union[str, Sequence[str]]] = None,
              linked: bool = False,
              output_names: Optional[Sequence[str]] = None,
              output_type: Union[str, Sequence[str]] = "4",
              output_options: Union[str, Sequence[str]] = "",
              apriori: Optional[Union[Sequence[str], Dict[str, object]]] = None,
              gointeractive: bool = False) -> Dict[str, object]:
    """Write a Tau-Argus ``.arb`` batch file for microdata.

    Port of ``micro_arb.R::micro_arb``. Returns ``{"arb_filename", "output_names"}``.
    """
    for name, val in (("asc_filename", asc_filename),
                      ("explanatory_vars", explanatory_vars),
                      ("safety_rules", safety_rules),
                      ("suppress", suppress)):
        if val is None:
            raise ArbError("missing argument: %s" % name)

    # a single tabulation may be passed as a flat list of var names (all str);
    # a list of per-table lists/dicts is already multiple tables.
    if all(isinstance(t, str) for t in explanatory_vars):
        explanatory_vars = [list(explanatory_vars)]
    nb_tabul = len(explanatory_vars)

    def _vec(x, default):
        return [x] if isinstance(x, (str, int, float, bool)) else list(x)

    response_var = _vec(response_var, "<freq>")
    safety_rules = _vec(safety_rules, safety_rules)
    weighted = _vec(weighted, False)
    shadow_var = _vec(shadow_var, "") if shadow_var is not None else None
    cost_var = _vec(cost_var, "") if cost_var is not None else None

    if arb_filename is None:
        import tempfile
        fd, arb_filename = tempfile.mkstemp(prefix="RTA_", suffix=".arb")
        os.close(fd)
    if rda_filename is None:
        rda_filename = re.sub("asc$", "rda", asc_filename)
    if isinstance(shadow_var, list) and all(s is None for s in shadow_var):
        shadow_var = [""] * nb_tabul
    if isinstance(cost_var, list) and all(c is None for c in cost_var):
        cost_var = [""] * nb_tabul

    if output_names is None:
        if isinstance(output_type, str):
            ext = [output_extensions[output_type]] * nb_tabul
        else:
            if len(output_type) != nb_tabul:
                raise ArbError("output_type doit etre une valeur par table")
            ext = [output_extensions[t] for t in output_type]
        import tempfile
        output_names = [
            tempfile.mkstemp(prefix="RTA_", suffix=e)[1] for e in ext
        ]

    if isinstance(output_options, str):
        output_options = [output_options] * nb_tabul
    if isinstance(output_type, str):
        output_type = [output_type] * nb_tabul

    if len(explanatory_vars) != len(output_names):
        raise ArbError("renseigner autant de noms de fichiers que de tabulations")

    if any("WGT" in r.upper() for r in safety_rules):
        raise ArbError(
            "ne pas renseigner WGT dans 'safety_rules', "
            "utiliser le parametre 'weighted' pour la ponderation"
        )

    if not all("." in os.path.basename(p) for p in output_names):
        raise ArbError("output_names doivent comporter une extension de fichier")

    asc_full = norm_path(asc_filename)
    rda_full = norm_path(rda_filename)

    res: List[str] = []
    # commentaire (header mirrors the R generator)
    res.append("// Batch generated by package *rtauargus*")
    # astimezone(): on macOS the naive now() yields an empty %Z (libiconv),
    # while R's Sys.time() always carries the zone name (e.g. "CEST").
    ts = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    res.append("// (%s)" % ts)

    # open...
    res.append('<OPENMICRODATA> "%s"' % asc_full)
    res.append('<OPENMETADATA> "%s"' % rda_full)

    # tabulations + secret primaire
    res.extend(specif_safety(explanatory_vars, response_var, shadow_var,
                             cost_var, safety_rules, weighted))

    # read...
    res.append("<READMICRODATA>")

    # apriori (before suppress)
    if apriori is not None:
        std = norm_apriori_params(apriori)
        res.extend(apriori_batch(nb_tabul, std["hst"], std["sep"],
                                 std["ignore_err"], std["exp_triv"]))

    # suppress + writetable
    res.extend(suppr_writetable(suppress, linked, output_names,
                                output_type, output_options))

    if gointeractive:
        res.append("<GOINTERACTIVE>")

    # final blank line
    res.append("")

    with open(arb_filename, "w", encoding="utf-8") as fh:
        fh.write("\n".join(res) + "\n")

    return {
        "arb_filename": norm_path(arb_filename),
        "output_names": [norm_path(p) for p in output_names],
    }
