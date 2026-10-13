"""pytauargus-tui — a Textual terminal UI over the pytauargus engine.

A thin presentation layer: it drives the documented ``pytauargus`` Python API
and re-implements no engine behaviour. See ``docs/ui-design.md`` (Option B).
"""

from .model import Cell, Session, TableGrid, TableMeta

__version__ = "0.5.1"
__all__ = ["Session", "TableMeta", "TableGrid", "Cell", "__version__"]
