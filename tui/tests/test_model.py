"""View-model tests for the TUI.

These encode the legacy contract the TUI reads from the engine: the same
tables, codes, cell values and status symbols the CLI and notebooks already
produce for ``TestRecode.arb``. The model layer must expose them as plain
Python so the textual widgets never touch the native bindings directly.
"""

import shutil
from pathlib import Path

import pytest

from pytauargus_tui import workspace as ws
from pytauargus_tui.model import (
    CellEdit,
    CellInstruction,
    EditHistory,
    Session,
    SuppressionOp,
    apriory_lines,
    arb_with_ops,
    format_status_delta,
    replay_batch,
)

DATA = Path(__file__).resolve().parent.parent.parent.parent / "data"
ARB = DATA / "TestRecode.arb"


@pytest.fixture(scope="module")
def session() -> Session:
    return Session.from_arb(ARB)


def test_table_count(session):
    assert session.n_tables == 2


def test_table_meta(session):
    t0 = session.table(0)
    assert t0.exp_vars == ("Size", "Region")
    assert t0.resp_var == "Var2"
    assert t0.n_cells == 126
    t1 = session.table(1)
    assert t1.exp_vars == ("Region", "IndustryCode")
    assert t1.n_cells == 12726


def test_grid_shape(session):
    g = session.grid(0)
    assert g.row_var == "Size"
    assert g.col_var == "Region"
    assert len(g.rows) == 7
    assert len(g.cols) == 18
    assert len(g.cells) == 7
    assert all(len(row) == 18 for row in g.cells)


def test_grid_cell_values(session):
    g = session.grid(0)
    # Every populated cell has an int status in the known range and a float value.
    for row in g.cells:
        for cell in row:
            assert isinstance(cell.status, int)
            assert 1 <= cell.status <= 14
            assert isinstance(cell.value, float)
            assert cell.symbol in ("S", "U", "P", "M", "E", "?")


def test_grid_total_cell_count(session):
    g = session.grid(0)
    assert sum(len(row) for row in g.cells) == g.tab.n_cells


def test_status_names(session):
    g = session.grid(0)
    # The pre-suppression sample batch is fully safe, so the first cell is safe.
    assert g.cells[0][0].status_name in ("safe", "safe_manual")


def test_rule_display(session):
    t0 = session.table(0)
    # The batch declares NK(2,75) for table 1 (0-based index 0).
    assert any("NK" in r and "2" in r and "75" in r for r in t0.rules)


# ===========================================================================
# Staged suppression + preview (design §B.3)
# ===========================================================================
def test_suppression_op_arb_lines():
    assert SuppressionOp("OPT", 2).arb_line() == "<SUPPRESS> OPT(2)"
    assert SuppressionOp("MOD", 1).arb_line() == "<SUPPRESS> MOD(1)"
    assert (SuppressionOp("RND", 3, rnd_base=1000000).arb_line()
            == "<SUPPRESS> RND(3,1000000)")


def test_arb_with_ops_appends():
    # No <WRITETABLE> in the spec: lines are appended at the end.
    out = arb_with_ops("a\nb\n", [SuppressionOp("OPT", 1)])
    assert out == "a\nb\n<SUPPRESS> OPT(1)\n"


def test_arb_with_ops_before_writetable():
    out = arb_with_ops('a\n<WRITETABLE> (1,1,0,"x")\n',
                       [SuppressionOp("RND", 2, 10)])
    assert out == 'a\n<SUPPRESS> RND(2,10)\n<WRITETABLE> (1,1,0,"x")\n'


def test_format_status_delta():
    d = format_status_delta({1: 104, 3: 1}, {1: 101, 3: 1, 11: 3})
    assert "safe 104→101" in d
    assert "unsafe_rule 1→1" in d
    assert "secondary_unsafe 0→3" in d
    # Stable ordering: ascending status code.
    assert d.index("safe") < d.index("unsafe_rule") < d.index("secondary_unsafe")


def test_status_counts(session):
    counts = session.status_counts(0)
    assert sum(counts.values()) == 126
    assert all(1 <= k <= 14 for k in counts)
    # The pre-suppression sample batch has safe cells.
    assert counts.get(1, 0) + counts.get(2, 0) > 0


@pytest.fixture(scope="module")
def preview_session(session):
    """Throwaway run of the same .arb with OPT(1) appended (design §B.3)."""
    return session.preview([SuppressionOp("OPT", 1)])


def test_preview_is_a_full_session(preview_session):
    assert preview_session.n_tables == 2
    assert preview_session.table(0).n_cells == 126


def test_preview_changes_table_statuses(session, preview_session):
    before = session.status_counts(0)
    after = preview_session.status_counts(0)
    assert sum(after.values()) == 126
    # The previewed OPT(1) changes table 1's cell statuses.
    assert after != before


# ===========================================================================
# Draft cell instructions + apriori preview (design §B.4 / §B.5)
# ===========================================================================
def test_apriory_lines_render(session):
    g = session.grid(0)
    instrs = [
        CellInstruction(table=1, dim=(g.rows[1], g.cols[2]), kind="U"),
        CellInstruction(table=1, dim=(g.rows[2], g.cols[3]), kind="P"),
    ]
    assert apriory_lines(instrs).splitlines() == [
        f"{g.rows[1]};{g.cols[2]};U",
        f"{g.rows[2]};{g.cols[3]};P",
    ]


def test_apriory_lines_empty():
    assert apriory_lines([]) == ""


def test_suppression_op_suppress_cmd():
    cmd = SuppressionOp("OPT", 1).suppress_cmd()
    assert (cmd.kind, cmd.tab_no) == ("OPT", 1)
    rcmd = SuppressionOp("RND", 2, rnd_base=7).suppress_cmd()
    assert (rcmd.kind, rcmd.tab_no, rcmd.rnd_base) == ("RND", 2, 7)


def _find_cells_by_symbol(session, i, symbol, n):
    """First ``n`` non-total cells of table ``i`` with the given symbol."""
    g = session.grid(i)
    out = []
    for ri, row in enumerate(g.cells):
        for ci, cell in enumerate(row):
            if cell.symbol != symbol:
                continue
            dim = (g.rows[ri], g.cols[ci]) if g.col_var is not None else (g.rows[ri],)
            if session.is_total(i, dim):
                continue  # total cells are not apriori targets (this iteration)
            out.append((ri, ci))
            if len(out) == n:
                return out
    return out


def test_preview_applies_instruction_to_marked_cell(session):
    """Marking a safe cell ``U`` and previewing OPT changes that cell in the
    scratch run, and leaves the live (accepted) run untouched."""
    targets = _find_cells_by_symbol(session, 0, "S", 1)
    assert targets, "no safe cell to mark"
    r, c = targets[0]
    g = session.grid(0)
    dim = (g.rows[r], g.cols[c])
    before_live = dict(session.status_counts(0))
    before_status = g.cells[r][c].status
    instr = CellInstruction(table=1, dim=dim, kind="U")
    prev = session.preview([SuppressionOp("OPT", 1)], [instr])
    after_status = prev.grid(0).cells[r][c].status
    assert after_status != before_status, "marked cell should change after OPT"
    # The live session is untouched (no native mutation of the accepted run).
    assert session.status_counts(0) == before_live
    assert session.grid(0).cells[r][c].status == before_status


def test_preview_batches_multiple_instructions(session):
    """One preview run applies the complete set of pending instructions."""
    targets = _find_cells_by_symbol(session, 0, "S", 2)
    assert len(targets) == 2, "need two safe cells"
    g = session.grid(0)
    instrs = [
        CellInstruction(table=1, dim=(g.rows[r], g.cols[c]), kind="U")
        for r, c in targets
    ]
    before = {(r, c): g.cells[r][c].status for r, c in targets}
    prev = session.preview([SuppressionOp("OPT", 1)], instrs)
    pg = prev.grid(0)
    for r, c in targets:
        assert pg.cells[r][c].status != before[(r, c)], (
            f"marked cell {(r, c)} should change")


# ===========================================================================
# Draft edit history — undo/redo (design §B.4 / §B.5)
# ===========================================================================
def test_history_starts_empty():
    h = EditHistory()
    assert not h.can_undo()
    assert not h.can_redo()
    assert h.undo() is None
    assert h.redo() is None


def test_history_undo_redo_roundtrip():
    h = EditHistory()
    key = (0, ("A", "B"))
    h.record(key, None, "U")
    assert h.can_undo() and not h.can_redo()
    cmd = h.undo()
    assert cmd == CellEdit(key=key, before=None, after="U")
    assert not h.can_undo() and h.can_redo()
    cmd2 = h.redo()
    assert cmd2 == cmd
    assert h.can_undo() and not h.can_redo()


def test_history_replacement_and_removal_edits():
    """before/after capture kind-replacement (U->S) and toggle-off (U->None)."""
    h = EditHistory()
    h.record((0, ("A",)), None, "U")      # mark U on an unmarked cell
    h.record((0, ("A",)), "U", "S")       # replace U with S
    h.record((0, ("A",)), "S", None)      # toggle S off
    c3 = h.undo()                          # last edit is the toggle-off
    assert c3.before == "S" and c3.after is None
    c2 = h.undo()                          # the replacement
    assert c2.before == "U" and c2.after == "S"
    c1 = h.undo()                          # the original mark
    assert c1.before is None and c1.after == "U"
    assert not h.can_undo()


def test_history_new_edit_clears_redo_branch():
    h = EditHistory()
    h.record((0, ("A",)), None, "U")
    h.undo()
    assert h.can_redo()
    h.record((1, ("B",)), None, "P")      # a new edit
    assert not h.can_redo()               # the redo branch is stale
    assert h.can_undo()


def test_history_multiple_cells_lifo_order():
    h = EditHistory()
    h.record((0, ("A",)), None, "U")
    h.record((1, ("B",)), "U", "S")
    first = h.undo()
    assert first.key == (1, ("B",))       # last-in, first-out
    second = h.undo()
    assert second.key == (0, ("A",))
    # redo restores in the reverse (LIFO of the redo stack) order
    h.redo()
    again = h.redo()
    assert again.key == (1, ("B",))


def test_history_reset_drops_both_stacks():
    h = EditHistory()
    h.record((0, ("A",)), None, "U")
    h.undo()
    assert h.can_redo()
    h.reset()
    assert not h.can_undo() and not h.can_redo()


# ===========================================================================
# Audit ownership — audit belongs to one result (design §B.5)
# ===========================================================================
def test_inplace_mutation_invalidates_audit():
    """An in-place mutation changes the result, so its cached audit evidence
    is stale and must be dropped (design §B.5: 'Grid/audit caches must be
    invalidated accordingly'). A fresh result has no audit until re-audited."""
    s = Session.from_arb(ARB)
    s.audit(0)
    assert len(s.audit_rows(0)) > 0
    # An in-place mutation: the result is now different, the audit is stale.
    s.suppress(SuppressionOp("OPT", 1).suppress_cmd())
    assert s.audit_rows(0) == []


# ===========================================================================
# Workspace reopen — Session.from_workspace + replay (design §B.7)
# ===========================================================================
# The batch's input files (tests that mutate/replay need an isolated copy).
# ``region2.hrc`` is referenced unquoted by the numeric ``RECODE 1, Region, 2``.
ARB_INPUTS = ("tau_testW.asc", "tau_testW.rda", "GK.grc", "IndCode2.grc",
              "region2.hrc")


@pytest.fixture
def tmp_arb(tmp_path) -> Path:
    """A working copy of the batch + its input files in an isolated dir."""
    for name in ("TestRecode.arb", *ARB_INPUTS):
        shutil.copy(DATA / name, tmp_path / name)
    return tmp_path / "TestRecode.arb"


def _first_data_dim(s: Session, i: int = 0):
    """A non-total (row, col) code label pair of table ``i``."""
    g = s.grid(i)
    return next((g.rows[r], g.cols[c])
                for r in range(len(g.rows)) for c in range(len(g.cols))
                if not s.is_total(i, (g.rows[r], g.cols[c])))


def test_from_workspace_replays_recipe(tmp_arb):
    """Reopening rebuilds the accepted result by re-running the recorded
    recipe (spec + confirmed instructions + confirmed ops) on a fresh engine
    (design §B.7: 'rebuild native execution state when needed')."""
    s = Session.from_arb(tmp_arb)
    dim = _first_data_dim(s)
    instrs = [CellInstruction(1, dim, "U")]
    ops = [SuppressionOp("OPT", 1)]
    m = ws.build_manifest(tmp_arb, draft_revision=2, accepted_revision=2,
                          confirmed_ops=ops, confirmed_instructions=instrs)
    target = tmp_arb.parent / "t.workspace.json"
    ws.save_workspace(m, target)
    restored = Session.from_workspace(target)
    expected = s.preview(ops, instrs)
    assert restored.status_counts(0) == expected.status_counts(0)
    assert restored.status_counts(1) == expected.status_counts(1)
    assert restored.arb_path == tmp_arb  # re-previewable from the same .arb


def test_from_workspace_empty_recipe_is_fresh(tmp_arb):
    """A workspace with no confirmed recipe restores a bare spec session."""
    m = ws.build_manifest(tmp_arb, draft_revision=1, accepted_revision=0)
    target = tmp_arb.parent / "t.workspace.json"
    ws.save_workspace(m, target)
    restored = Session.from_workspace(target)
    fresh = Session.from_arb(tmp_arb)
    assert restored.status_counts(0) == fresh.status_counts(0)


def test_from_workspace_stale_input(tmp_arb):
    """A changed input raises WorkspaceStale — the saved outcome must not be
    treated as current (design §B.7: explicit reconciliation)."""
    m = ws.build_manifest(tmp_arb, draft_revision=1, accepted_revision=0)
    target = tmp_arb.parent / "t.workspace.json"
    ws.save_workspace(m, target)
    tmp_arb.write_text("// operator edit\n" + tmp_arb.read_text())
    with pytest.raises(ws.WorkspaceStale):
        Session.from_workspace(target)


def test_from_workspace_missing_input(tmp_arb):
    """A missing input raises WorkspaceStale (it cannot be reconciled)."""
    m = ws.build_manifest(tmp_arb, draft_revision=1, accepted_revision=0)
    target = tmp_arb.parent / "t.workspace.json"
    ws.save_workspace(m, target)
    tmp_arb.unlink()
    with pytest.raises(ws.WorkspaceStale):
        Session.from_workspace(target)


def test_from_workspace_unknown_version(tmp_arb):
    """An incompatible manifest version is refused (design §B.7)."""
    m = ws.build_manifest(tmp_arb, draft_revision=1, accepted_revision=0)
    m.version = 99
    target = tmp_arb.parent / "t.workspace.json"
    ws.save_workspace(m, target)
    with pytest.raises(ws.WorkspaceError):
        Session.from_workspace(target)


def test_replay_batch_writes_runnable_recipe(tmp_arb):
    """The replay batch materialises the accepted recipe as a headless-
    runnable .arb next to the input (design §B.7): one <SUPPRESS> line per
    confirmed op, one apriori file per table of confirmed instructions.
    Running it reproduces the accepted outcome."""
    s = Session.from_arb(tmp_arb)
    dim = _first_data_dim(s)
    instrs = [CellInstruction(1, dim, "U")]
    ops = [SuppressionOp("OPT", 1)]
    path = replay_batch(tmp_arb, ops, instrs)
    assert path == tmp_arb.parent / "TestRecode.replay.arb"
    text = path.read_text()
    assert "<SUPPRESS> OPT(1)" in text
    # Full legacy APRIORI form, strict (ignore_error=0) like the TUI preview.
    assert '<APRIORI> "TestRecode.tab1.apri",1,";",0,0' in text
    apri = (tmp_arb.parent / "TestRecode.tab1.apri").read_text()
    assert apri == f"{dim[0]};{dim[1]};U\n"
    replayed = Session.from_arb(path)
    expected = s.preview(ops, instrs)
    assert replayed.status_counts(0) == expected.status_counts(0)


def test_replay_batch_empty_recipe(tmp_arb):
    """With no confirmed ops or marks the replay is a verbatim copy of the
    spec (an empty recipe is a valid workspace)."""
    path = replay_batch(tmp_arb, (), ())
    assert path.read_text() == tmp_arb.read_text()
