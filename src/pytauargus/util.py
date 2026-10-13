"""Shared helpers for the rtauargus generator-side writers (``.arb`` / ``.rda``).

Port of the small utility functions in ``references/rtauargus/R/util.R`` that
the batch (``.arb``) and metadata (``.rda``) generators rely on. Kept
dependency-free and tech-neutral so they can be tested in isolation.

Function names mirror the R source so the ported tests read as a direct
translation of ``test_util.R``.
"""

import os
import warnings
from typing import Dict, List, Optional, Sequence

from pytauargus.hrc import HrcError

__all__ = [
    "cite",
    "norm_path",
    "df_param_defaut",
    "following_dup",
    "output_extensions",
]


def cite(x: Sequence[str],
         guillemet: str = '"',
         ignore_vide: bool = True) -> List[str]:
    """Wrap each element in quotes, unless it is an empty string.

    Port of ``util.R::cite``. With ``ignore_vide`` (default) empty strings are
    left bare; otherwise every element, including ``""``, is quoted.
    """
    return [
        ("" if (v == "" and ignore_vide) else guillemet + v + guillemet)
        for v in x
    ]


def norm_path(path: str) -> str:
    """Mirror R ``normalizePath(path, mustWork = FALSE)``.

    The generator writers emit absolute paths so a batch runs regardless of the
    working directory. ``normalizePath(mustWork = FALSE)`` returns the path
    unchanged when it does not resolve to an existing location, and the absolute
    resolved path when it does (``.``/``..`` always resolve; ``~`` expands).

    This matches the observed R behaviour exactly:

    ===============================  ================================
    input (cwd ``/w``)               result
    ===============================  ================================
    ``t1.csv`` (missing)             ``t1.csv``
    ``./sub/x.csv`` (missing)        ``./sub/x.csv``
    ``a/../t1.csv`` (missing)        ``a/../t1.csv``
    ``realfile.txt`` (exists)        ``/w/realfile.txt``
    ``/nonexist/a.b``                ``/nonexist/a.b``
    ``~/t3.csv``                     ``/home/user/t3.csv``
    ``.`` / ``..``                   ``/w`` / parent of ``/w``
    ===============================  ================================
    """
    p = os.path.expanduser(path)
    if not os.path.exists(p):
        return p
    return os.path.abspath(p)


def df_param_defaut(varnames: Sequence[str],
                    param_name: str,
                    valvar: Optional[Dict[str, str]]) -> Dict[str, Optional[str]]:
    """Build per-variable parameter values from a default + named exceptions.

    Port of ``util.R::df_param_defaut``. ``valvar`` maps a special key ``"_"``
    to the default value (applies to every variable) and/or variable names to
    per-variable overrides. With no default every non-overridden variable is
    ``None`` (R ``NA``). Multiple ``"_"`` entries keep the first and warn.

    Returns ``{colname: value}`` for every name in ``varnames``. Values are
    coerced to ``str`` (mirrors R recycling a factor/numeric column as character
    once a string is mixed in). ``param_name`` is only used in the warning.
    """
    valvar = dict(valvar or {})
    overrides = {k: v for k, v in valvar.items() if k != "_"}
    defaults = [v for k, v in valvar.items() if k == "_"]

    if not defaults:
        default: Optional[str] = None
    elif len(defaults) > 1:
        default = str(defaults[0])
        import warnings
        warnings.warn(
            'plusieurs valeurs par defaut pour "%s", '
            'premiere valeur prise en compte ("%s")' % (param_name, default),
            UserWarning, stacklevel=2,
        )
    else:
        default = str(defaults[0])

    return {
        name: (None if (v := overrides.get(name, default)) is None else str(v))
        for name in varnames
    }


def following_dup(x: Sequence[object]) -> List[bool]:
    """Mark each element that duplicates the one immediately before it.

    Port of ``util.R::following_dup``. Raises on a missing value (R ``stop``).
    Kept here so the ``.arb``/``.rda`` generators and the HRC writer share the
    exact R contract (``hrc.py`` carries its own copy for independence).
    """
    if any(v is None for v in x):
        raise HrcError("impossible : valeur(s) manquante(s)")
    return [False] + [x[i] == x[i - 1] for i in range(1, len(x))]


#: Tau-Argus output-type code -> file extension (``util.R::output_extensions``).
output_extensions: Dict[str, str] = {
    "1": ".csv",
    "2": ".csv",
    "3": ".txt",
    "4": ".sbs",
    "5": ".tab",
    "6": ".jj",
}
