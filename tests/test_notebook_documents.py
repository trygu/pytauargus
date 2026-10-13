"""Saved examples must use portable displayed paths, independent of nbclient."""

import json
import re
from pathlib import Path

import pytest


NOTEBOOKS = Path(__file__).resolve().parents[1] / "notebooks"
LOCAL_PATH = re.compile(
    r"/(?:Users|home|tmp|private|var|workspace)/|(?<![A-Za-z0-9])[A-Za-z]:[\\/]"
)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


@pytest.mark.parametrize("name", [
    "01_quickstart.ipynb", "02_protection_and_audit.ipynb", "03_generators.ipynb",
])
def test_saved_outputs_do_not_include_workstation_paths(name):
    notebook = json.loads((NOTEBOOKS / name).read_text())
    for index, cell in enumerate(notebook["cells"]):
        for text in _strings(cell.get("outputs", [])):
            assert not LOCAL_PATH.search(text), f"{name}, cell {index}: local path in output"
