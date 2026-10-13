"""Tau-Argus: Statistical Disclosure Control with open-source solvers (HiGHS).

High-level API:
    from pytauargus import Engine
    eng = Engine()
    ...

The native engine (C++ core + HiGHS-backed solvers) is exposed through the
compiled extension ``pytauargus._tauargus``.
"""

from ._tauargus import TauArgus, HiTaSCtrl, RounderCtrl  # noqa: F401
from .protect import protect, ProtectResult  # noqa: F401

__version__ = "0.5.1"
__all__ = [
    "TauArgus", "HiTaSCtrl", "RounderCtrl", "Engine",
    "write_hrc", "HrcError", "protect", "ProtectResult", "TableResult",
    "to_microdata", "make_frame", "__version__",
]


def __getattr__(name):
    # Lazy so `import pytauargus` works even before engine.py is imported.
    if name == "Engine":
        from .engine import Engine

        return Engine
    if name in ("write_hrc", "HrcError"):
        from . import hrc

        return getattr(hrc, name)
    if name == "TableResult":
        from . import result

        return result.TableResult
    if name in ("to_microdata", "make_frame"):
        from . import dataframe

        return getattr(dataframe, name)
    raise AttributeError(name)
