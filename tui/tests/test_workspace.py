"""Workspace manifest tests (design §B.7) — pure, no engine run.

The workspace persists the logical draft state and accepted-result
references as a versioned JSON manifest: the recipe (instructions + method
ops), input fingerprint, revisions and provenance. Saving is atomic and must
work before any preview exists; a changed input is detected, not silently
treated as current.
"""

from pathlib import Path

import pytest

from pytauargus_tui.model import CellInstruction, SuppressionOp
from pytauargus_tui import workspace as ws

DATA = Path(__file__).resolve().parent.parent.parent.parent / "data"
ARB = DATA / "TestRecode.arb"


def _manifest(tmp_path, **kw) -> "ws.WorkspaceManifest":
    defaults = dict(
        draft_revision=3,
        accepted_revision=2,
        confirmed_ops=[SuppressionOp("OPT", 1)],
        confirmed_instructions=[CellInstruction(1, ("A", "B"), "U")],
        pending_instructions=[CellInstruction(1, ("C",), "P")],
        accepted_summary={"0": {1: 104, 2: 1, 5: 21}},
        tab=0,
    )
    defaults.update(kw)
    return ws.build_manifest(ARB, **defaults)


def test_save_load_roundtrip(tmp_path):
    m = _manifest(tmp_path)
    p = tmp_path / "w.workspace.json"
    ws.save_workspace(m, p)
    assert ws.load_workspace(p) == m


def test_save_before_preview_empty_recipe(tmp_path):
    # A fresh session (no ops, no instructions) is a valid workspace.
    m = _manifest(tmp_path, confirmed_ops=[], confirmed_instructions=[],
                  pending_instructions=[], draft_revision=1, accepted_revision=0)
    p = tmp_path / "w.workspace.json"
    ws.save_workspace(m, p)
    loaded = ws.load_workspace(p)
    assert loaded.confirmed_ops == []
    assert loaded.pending_instructions == []
    assert loaded.version == ws.WORKSPACE_VERSION


def test_fingerprint_stable_and_sensitive(tmp_path):
    a = ws.fingerprint(ARB)
    assert a == ws.fingerprint(ARB)  # stable for the same content
    modified = tmp_path / "m.arb"
    modified.write_text(ARB.read_text() + "\n# edited\n")
    assert ws.fingerprint(modified) != a  # sensitive to content change


def test_input_changed_detection(tmp_path):
    m = _manifest(tmp_path)
    assert not ws.input_changed(m, ARB)  # unmodified input
    stale = ws.WorkspaceManifest(**{**m.__dict__,
                                    "input_fingerprint": "0" * 64})
    assert ws.input_changed(stale, ARB)  # fingerprint mismatch
    assert ws.input_changed(m, tmp_path / "missing.arb")  # missing input


def test_atomic_save_preserves_on_failure(tmp_path, monkeypatch):
    p = tmp_path / "w.workspace.json"
    good = _manifest(tmp_path)
    ws.save_workspace(good, p)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(ws.json, "dump", boom)
    with pytest.raises(OSError):
        ws.save_workspace(_manifest(tmp_path), p)

    # The previous (good) manifest is intact and still loadable.
    assert ws.load_workspace(p) == good
    # No stray temp files were left behind.
    assert list(tmp_path.glob("*.tmp")) == []
