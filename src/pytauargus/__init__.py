"""Tau-Argus: Statistical Disclosure Control with open-source solvers (HiGHS).

High-level API:
    from pytauargus import Engine
    eng = Engine()
    ...

The native engine (C++ core + HiGHS-backed solvers) is exposed through the
compiled extension ``pytauargus._tauargus``.
"""

from importlib.metadata import PackageNotFoundError, version as dist_version

from ._tauargus import TauArgus, HiTaSCtrl, RounderCtrl, engine_meta  # noqa: F401
from .protect import protect, ProtectResult  # noqa: F401

try:
    # The installed distribution is the source of truth (pyproject.toml at
    # build time), so a stale hardcoded version can no longer ship.
    __version__ = dist_version("pytauargus")
except PackageNotFoundError:
    # Uninstalled source checkout with no distribution metadata.
    __version__ = "0.5.7"
__all__ = [
    "TauArgus", "HiTaSCtrl", "RounderCtrl", "Engine", "engine_meta",
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
