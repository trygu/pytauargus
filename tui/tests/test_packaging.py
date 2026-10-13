"""Packaging contract: ``uvx pytauargus-tui`` must work.

``uvx <pkg>`` runs the console script that matches the package name, so
the wheel must ship a ``pytauargus-tui`` entry point in addition to the
human-facing ``tauargus-tui`` command (both wrap the same ``main``).
"""

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_uv_runnable_scripts():
    data = tomllib.loads(PYPROJECT.read_text())
    scripts = data["project"]["scripts"]
    assert scripts.get("pytauargus-tui") == "pytauargus_tui.app:main"
    assert scripts.get("tauargus-tui") == "pytauargus_tui.app:main"
