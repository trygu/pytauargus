"""pytauargus-mcp: the agent front-end for tau-ARGUS over MCP.

A thin wrapper over the pytauargus Python API: it exposes the stateless
engine behind MCP tools (the D2.6 agent front-end, ADR 0001) and
re-implements no engine behaviour. The ``.arb`` batch file is the state
carrier of the batch flow; the server keeps no session.
"""

from importlib.metadata import PackageNotFoundError, version as dist_version

try:
    # The installed distribution is the source of truth (pyproject.toml at
    # build time), so a stale hardcoded version can no longer ship.
    __version__ = dist_version("pytauargus-mcp")
except PackageNotFoundError:
    # Uninstalled source checkout with no distribution metadata.
    __version__ = "0.1.0"

__all__ = ["__version__"]
