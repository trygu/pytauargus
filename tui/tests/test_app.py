"""TUI app tests (headless, via textual's Pilot).

Drive the real app against ``data/TestRecode.arb``: load, navigator,
grid shape, cell detail, table switching, and the action keys.
"""

import json
import shutil
import time
from pathlib import Path

import pytest
from textual.widgets import DataTable, DirectoryTree, ListView, Static

from pytauargus_tui import workspace as ws
from pytauargus_tui.app import (
    TauArgusApp,
    _ArbDirectoryTree,
    _BusySpinner,
    _FileBrowser,
    _HelpScreen,
)

DATA = Path(__file__).resolve().parent.parent.parent.parent / "data"
ARB = DATA / "TestRecode.arb"


async def wait_for(pilot, pred, timeout=30.0, what="condition"):
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await pilot.pause()


@pytest.fixture
def app() -> TauArgusApp:
    return TauArgusApp(ARB)


def grid(app) -> DataTable:
    return app.query_one("#grid", DataTable)


async def test_app_loads_batch(app):
    async with app.run_test(size=(120, 35)) as pilot:
        await wait_for(pilot, lambda: app.session is not None, what="session load")
        assert app.session.n_tables == 2
        nav = app.query_one("#nav", ListView)
        await wait_for(pilot, lambda: len(nav.children) == 2, what="nav rows")
        # Table 0 = Size(7 rows) x Region(18 cols) + 1 label column.
        await wait_for(pilot, lambda: grid(app).row_count == 7, what="grid")
        assert len(grid(app).columns) == 19
        assert app.current_table == 0


async def test_grid_cell_status_colors(app):
    async with app.run_test(size=(120, 35)) as pilot:
        await wait_for(pilot, lambda: app.session is not None)
        await wait_for(pilot, lambda: grid(app).row_count == 7, what="grid")
        # Every data cell is a rich Text with a style (status colour).
        from rich.text import Text

        ncols = len(grid(app).columns)
        for r in range(grid(app).row_count):
            for c in range(1, ncols):
                val = grid(app).get_cell(f"r{r}", f"c{c - 1}")
                assert isinstance(val, Text)
                assert val.style in ("green", "red", "cyan", "yellow", "dim")


async def test_table_switch_jk(app):
    async with app.run_test(size=(120, 35)) as pilot:
        await wait_for(pilot, lambda: app.session is not None)
        await wait_for(pilot, lambda: grid(app).row_count == 7, what="grid")
        await pilot.press("j")
        assert app.current_table == 1
        # Table 1 = Region(18 rows) x IndustryCode(707 cols) + label column.
        await wait_for(pilot, lambda: grid(app).row_count == 18, what="grid 1")
        assert len(grid(app).columns) == 708
        await pilot.press("k")
        assert app.current_table == 0
        await wait_for(pilot, lambda: grid(app).row_count == 7, what="grid 0")


async def test_cell_detail(app):
    async with app.run_test(size=(120, 35)) as pilot:
        await wait_for(pilot, lambda: app.session is not None)
        await wait_for(pilot, lambda: grid(app).row_count == 7, what="grid")
        g = grid(app)
        g.move_cursor(row=1, column=2)
        await wait_for(
            pilot,
            lambda: "value=" in app.detail_text and "status=" in app.detail_text,
            what="cell detail",
        )
        assert "cell (" in app.detail_text


async def _loaded(pilot, app) -> None:
    await wait_for(pilot, lambda: app.session is not None, what="session load")
    await wait_for(pilot, lambda: grid(app).row_count == 7, what="grid")


# ===========================================================================
# Staged suppress/round: preview before committing (design §B.3)
# ===========================================================================
async def test_suppress_stages_preview_not_live(app):
    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)
        before = app.session.status_counts(0)
        # NB: textual 8.x Pilot.press waits for process CPU to idle, which
        # includes the engine worker thread — so the busy window is not
        # observable around press. The spinner contract is covered by
        # test_spinner_tracks_busy_flag instead.
        await pilot.press("s")
        await wait_for(pilot, lambda: app.preview is not None, what="preview")
        assert app.busy is False  # spinner down once the preview lands
        # The live session is untouched while a preview is pending.
        assert app.session.status_counts(0) == before
        assert app.pending is not None
        assert "→" in app.preview_delta
        assert app.last_action == "OPT previewed on table 1"


async def test_spinner_tracks_busy_flag(app):
    """The busy flag drives the spinner: visible + animated while True."""
    async with app.run_test(size=(120, 35)) as pilot:
        await wait_for(pilot, lambda: app.session is not None, what="load")
        assert app.busy is False  # settled after the load worker
        spin = app.query_one("#spin", _BusySpinner)
        assert spin.visible is False
        app._set_busy(True)
        assert app.busy is True
        assert spin.visible is True
        assert str(spin.render()).strip() != ""  # a frame is drawn
        app._set_busy(False)
        assert app.busy is False
        assert spin.visible is False


async def test_round_stages_preview(app):
    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)
        await pilot.press("r")
        await wait_for(pilot, lambda: app.preview is not None, what="RND preview")
        assert "→" in app.preview_delta
        assert app.last_action == "RND previewed on table 1"


async def test_confirm_adopts_preview(app):
    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)
        before = app.session.status_counts(0)
        await pilot.press("s")
        await wait_for(pilot, lambda: app.preview is not None, what="preview")
        preview_sess = app.preview
        await pilot.press("y")
        assert app.preview is None and app.pending is None
        # The previewed run becomes the working session (design §B.3).
        assert app.session is preview_sess
        assert app.session.status_counts(0) != before
        assert app.last_action == "OPT confirmed on table 1"
        # The grid shows the adopted (suppressed) table.
        assert grid(app).row_count == 7


async def test_cancel_keeps_live(app):
    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)
        before = app.session.status_counts(0)
        await pilot.press("s")
        await wait_for(pilot, lambda: app.preview is not None, what="preview")
        await pilot.press("n")
        assert app.preview is None and app.pending is None
        assert app.session.status_counts(0) == before
        assert app.last_action == "OPT preview cancelled"


async def test_preview_result_shown_in_grid(app):
    """While a preview is pending the grid shows the *preview's* result so the
    operator can inspect its secondary suppressions; cancel restores the
    accepted grid (design §B.5: "the operator inspects its secondary
    suppressions"). Suppressed cells carry status M (secondary) = yellow."""
    from rich.text import Text

    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)

        def secondary_count(sess, tab=0):
            g = sess.grid(tab)
            return sum(1 for row in g.cells for c in row if c.status in (11, 12))

        def yellow_cells(g):
            n = 0
            for r in range(g.row_count):
                for c in range(1, len(g.columns)):
                    t = g.get_cell(f"r{r}", f"c{c - 1}")
                    if isinstance(t, Text) and t.style == "yellow":
                        n += 1
            return n

        g = grid(app)
        accepted_sec = secondary_count(app.session)
        before_yellow = yellow_cells(g)
        assert before_yellow == accepted_sec  # grid reflects the accepted run

        await pilot.press("s")
        await wait_for(pilot, lambda: app.preview is not None, what="preview")
        preview_sec = secondary_count(app.preview)
        assert preview_sec != accepted_sec  # OPT actually suppressed cells
        # The grid now renders the preview's secondary suppressions.
        assert yellow_cells(g) == preview_sec

        await pilot.press("n")
        assert app.preview is None and app.pending is None
        # Cancelling restores the accepted grid.
        assert yellow_cells(g) == accepted_sec == before_yellow


async def test_second_opt_preview_surfaces_5008(app):
    """A second OPT on an already-optimised table fails with legacy code
    5008; the TUI surfaces it as an error (no crash) and drops the pending
    preview. This also proves confirmed ops carry into the next preview:
    without the confirmed OPT(1) in the previewed .arb, the second preview
    would have succeeded."""
    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)
        await pilot.press("s")
        await wait_for(pilot, lambda: app.preview is not None, what="preview 1")
        await pilot.press("y")  # adopt the first OPT
        assert app.last_action == "OPT confirmed on table 1"
        assert [op.arb_line() for op in app.confirmed_ops] == ["<SUPPRESS> OPT(1)"]
        await pilot.press("s")  # preview a second OPT on the same table
        await wait_for(pilot, lambda: "5008" in app.log_text, what="5008 error")
        assert app.preview is None and app.pending is None
        assert app.busy is False


async def test_save_action_writes_tab(app, tmp_path):
    # Point the app at a copy of the batch so `out/` lands in tmp_path.
    import shutil

    arb = tmp_path / "TestRecode.arb"
    shutil.copy(ARB, arb)
    # The batch references data files relative to the arb's directory.
    for f in ("tau_testW.asc", "tau_testW.rda", "GK.grc", "IndCode2.grc",
              "region2.hrc"):
        shutil.copy(DATA / f, tmp_path / f)
    app = TauArgusApp(arb)
    async with app.run_test(size=(120, 35)) as pilot:
        await wait_for(pilot, lambda: app.session is not None, what="load")
        await wait_for(pilot, lambda: grid(app).row_count == 7, what="grid")
        await pilot.press("o")
        await wait_for(pilot, lambda: app.log_text.startswith("saved "),
                       what="save log")
        out = tmp_path / "out" / "table_1.tab"
        assert out.is_file()
        content = out.read_text()
        assert len(content.splitlines()) > 7


# ===========================================================================
# Draft cell instructions (design §B.4 / §B.5)
# ===========================================================================
async def test_cell_mark_updates_draft_only(app):
    """Marking a cell updates the draft immediately: no solver run, the
    live (accepted) session is untouched."""
    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)
        g = grid(app)
        g.move_cursor(row=1, column=2)
        await wait_for(pilot, lambda: app.cursor == (1, 2), what="cursor")
        before = app.session.status_counts(0)
        await pilot.press("u")
        # Draft updated: one pending instruction, kind U.
        assert len(app.pending_instructions) == 1
        assert app.pending_instructions[0].kind == "U"
        # No preview / solver run was started.
        assert app.preview is None and app.pending is None
        # Live session untouched.
        assert app.session.status_counts(0) == before


async def test_cell_mark_toggle_off(app):
    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)
        g = grid(app)
        g.move_cursor(row=1, column=2)
        await wait_for(pilot, lambda: app.cursor == (1, 2), what="cursor")
        await pilot.press("u")
        assert len(app.pending_instructions) == 1
        await pilot.press("u")  # the same mark again clears it
        assert len(app.pending_instructions) == 0


async def test_cell_mark_replaces_kind(app):
    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)
        g = grid(app)
        g.move_cursor(row=1, column=2)
        await wait_for(pilot, lambda: app.cursor == (1, 2), what="cursor")
        await pilot.press("u")
        await pilot.press("p")  # a different mark on the same cell replaces it
        assert len(app.pending_instructions) == 1
        assert app.pending_instructions[0].kind == "P"


async def test_cell_mark_safe_shift_s(app):
    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)
        g = grid(app)
        g.move_cursor(row=1, column=2)
        await wait_for(pilot, lambda: app.cursor == (1, 2), what="cursor")
        await pilot.press("shift+s")
        assert len(app.pending_instructions) == 1
        assert app.pending_instructions[0].kind == "S"


async def test_preview_batches_marks_and_accept(app):
    """Two marks + OPT: one preview run applies both; accepting commits both
    and promotes them to the confirmed draft."""
    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)
        g = grid(app)
        g.move_cursor(row=1, column=2)
        await wait_for(pilot, lambda: app.cursor == (1, 2), what="cursor 1")
        await pilot.press("u")
        g.move_cursor(row=2, column=3)
        await wait_for(pilot, lambda: app.cursor == (2, 3), what="cursor 2")
        await pilot.press("u")
        assert len(app.pending_instructions) == 2
        before = app.session.status_counts(0)
        await pilot.press("s")  # OPT preview batches the two marks
        await wait_for(pilot, lambda: app.preview is not None, what="preview")
        await pilot.press("y")  # accept
        assert app.preview is None and app.pending is None
        assert app.session.status_counts(0) != before
        assert len(app.confirmed_instructions) == 2


async def test_discard_keeps_marks_and_live(app):
    """Discarding a preview keeps the draft marks and the accepted run."""
    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)
        g = grid(app)
        g.move_cursor(row=1, column=2)
        await wait_for(pilot, lambda: app.cursor == (1, 2), what="cursor")
        await pilot.press("u")
        assert len(app.pending_instructions) == 1
        before = app.session.status_counts(0)
        await pilot.press("s")
        await wait_for(pilot, lambda: app.preview is not None, what="preview")
        await pilot.press("n")  # discard
        assert app.preview is None and app.pending is None
        # Draft kept, live run untouched.
        assert len(app.pending_instructions) == 1
        assert app.session.status_counts(0) == before


# ===========================================================================
# Discoverability: spec'd status line + help overlay (design §B.1)
# ===========================================================================

def logline(app) -> str:
    """The bottom status line's widget content (what the user actually sees)."""
    return str(app.query_one("#logtext", Static).content)


async def test_status_line_shows_state_and_hint(app):
    """The bottom line carries the spec state facts + keymap hint (§B.1)."""
    async with app.run_test(size=(200, 35)) as pilot:
        await _loaded(pilot, app)
        line = logline(app)
        assert "draft r1" in line
        assert "preview —" in line
        assert "acc —" in line
        # The hint makes clear that s runs OPT on the whole table, not the
        # cell under the cursor (the exact confusion this wording fixes).
        assert "s=OPT table" in line
        assert "?=help" in line
        # the widget content is the composed string the app reports
        assert line == app.status_text


async def test_status_line_widget_is_on_screen(app):
    """Regression: the status line must actually *render* on-screen (§B.1).

    Previously #logtext was squeezed to width 1 and pushed off-screen by
    the sibling spinner (a Textual 8.x auto-width quirk in horizontal
    layouts), so the state facts and contextual hint were never visible
    even though the widget's ``content`` string (asserted above) was right.
    """
    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)
        lt = app.query_one("#logtext", Static)
        # One row, near the left edge, spanning (almost) the terminal width.
        assert lt.region.height == 1
        assert lt.region.x <= 2
        assert lt.region.width >= 100
        # The composed line is present in the widget that is on screen.
        assert lt.content == app.status_text
        assert app.status_text.startswith("draft r1")


async def test_status_line_marks_bump_revision_and_edits(app):
    """A draft mark advances the revision and the pending edit count."""
    async with app.run_test(size=(200, 35)) as pilot:
        await _loaded(pilot, app)
        g = grid(app)
        g.move_cursor(row=1, column=2)
        await wait_for(pilot, lambda: app.cursor == (1, 2), what="cursor")
        await pilot.press("u")
        assert app.draft_revision == 2
        line = logline(app)
        assert "draft r2" in line
        assert "1 edit(s)" in line
        assert line == app.status_text


async def test_status_line_preview_fresh_and_pending_hint(app):
    """A live preview reads fresh; the hint switches to y/n while pending."""
    async with app.run_test(size=(200, 35)) as pilot:
        await _loaded(pilot, app)
        await pilot.press("s")
        await wait_for(pilot, lambda: app.preview is not None, what="preview")
        assert app.preview_freshness == "fresh"
        line = logline(app)
        assert "preview fresh" in line
        assert "y=accept" in line and "n=cancel" in line


async def test_status_line_accepted_revision_after_confirm(app):
    """Accepting records the accepted revision and clears the preview."""
    async with app.run_test(size=(200, 35)) as pilot:
        await _loaded(pilot, app)
        await pilot.press("s")
        await wait_for(pilot, lambda: app.preview is not None, what="preview")
        await pilot.press("y")  # accept
        assert app.preview is None and app.pending is None
        assert app.accepted_revision == app.draft_revision
        line = logline(app)
        assert "preview —" in line
        assert f"acc r{app.draft_revision}" in line


async def test_contextual_hint_by_cell_status(app):
    """The status-line hint adapts to the cell under the cursor (design §B.1):
    on an unsafe (red) cell it points at u (hide it); on a secondary (yellow)
    cell it points at p (protect it); elsewhere it shows the base hint."""
    async with app.run_test(size=(200, 35)) as pilot:
        await _loaded(pilot, app)
        g = grid(app)
        sess_grid = app.session.grid(0)

        def find_cell(symbol):
            for r in range(g.row_count):
                for c in range(1, len(g.columns)):
                    if sess_grid.cells[r][c - 1].symbol == symbol:
                        return (r, c)
            return None

        # Unsafe (red) cell -> the hint points at u.
        unsafe = find_cell("U")
        assert unsafe is not None, "expected an unsafe (red) cell in table 0"
        g.move_cursor(row=unsafe[0], column=unsafe[1])
        await wait_for(pilot, lambda: "red=sensitive value" in app.status_text,
                       what="U hint")

        # Safe (green) cell -> the u=flag-as-unsafe hint.
        safe = find_cell("S")
        assert safe is not None, "expected a safe (green) cell in table 0"
        g.move_cursor(row=safe[0], column=safe[1])
        await wait_for(pilot, lambda: "u=flag as unsafe" in app.status_text,
                       what="S hint")

        # Secondary (yellow) cell -> the hint points at p. Accept an OPT first
        # so the session has suppressed (yellow) cells.
        await pilot.press("s")
        await wait_for(pilot, lambda: app.preview is not None, what="preview")
        await pilot.press("y")  # accept -> yellow (M) cells now in the session
        await wait_for(pilot, lambda: app.preview is None, what="accepted")
        sess_grid = app.session.grid(0)

        def find_secondary():
            for r in range(g.row_count):
                for c in range(1, len(g.columns)):
                    if sess_grid.cells[r][c - 1].symbol == "M":
                        return (r, c)
            return None

        secondary = find_secondary()
        assert secondary is not None, "expected a yellow cell after OPT"
        g.move_cursor(row=secondary[0], column=secondary[1])
        await wait_for(pilot, lambda: "p=keep this cell" in app.status_text,
                       what="M hint")


async def test_help_overlay_opens_and_closes(app):
    """? opens the help overlay; esc closes it. No engine involvement."""
    async with app.run_test(size=(200, 40)) as pilot:
        await _loaded(pilot, app)
        await pilot.press("?")
        await wait_for(pilot, lambda: isinstance(app.screen, _HelpScreen),
                       what="help overlay")
        body = str(app.screen.query_one("#help", Static).content)
        assert "how it works" in body
        assert "s = suppress" in body
        # The help explains that s runs OPT on the whole table, not one cell.
        assert "whole table" in body
        assert "safe" in body and "unsafe" in body
        await pilot.press("escape")
        await wait_for(pilot, lambda: not isinstance(app.screen, _HelpScreen),
                       what="help closed")


# ===========================================================================
# No-arg launch: file browser (design §B.7)
# ===========================================================================

@pytest.fixture
def app_none() -> TauArgusApp:
    return TauArgusApp(None)


def test_arb_directory_tree_filters_to_arb_and_dirs(tmp_path):
    """Only subdirectories and .arb files are listed/openable (no others)."""
    (tmp_path / "good.arb").write_text("x")
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / ".hidden.arb").write_text("x")
    tree = _ArbDirectoryTree(tmp_path)
    shown = {p.name for p in tree.filter_paths(list(tmp_path.iterdir()))}
    assert "good.arb" in shown
    assert "sub" in shown
    assert "notes.txt" not in shown
    assert ".hidden.arb" not in shown


async def test_noarg_shows_filebrowser(app_none):
    """Running with no batch shows the file browser (not an empty app)."""
    async with app_none.run_test(size=(100, 35)) as pilot:
        await wait_for(pilot, lambda: isinstance(app_none.screen, _FileBrowser),
                       what="file browser")
        assert app_none.session is None  # nothing loaded yet
        assert app_none.screen.query_one("#fbtree", DirectoryTree) is not None


async def test_filebrowser_open_arb_loads(app_none):
    """Picking a .arb (the browser's callback) loads it, same path as arg mode."""
    async with app_none.run_test(size=(100, 35)) as pilot:
        await wait_for(pilot, lambda: isinstance(app_none.screen, _FileBrowser),
                       what="file browser")
        app_none._on_arb_chosen(ARB)  # what the browser does on Enter over a .arb
        await wait_for(pilot, lambda: app_none.session is not None,
                       what="load after pick")
        assert app_none.session.n_tables == 2


async def test_filebrowser_handler_opens_arb_only(app_none):
    """The tree's file-selected handler routes .arb to the load callback, and
    ignores anything else (only files that make sense to open are openable)."""
    async with app_none.run_test(size=(100, 35)) as pilot:
        await wait_for(pilot, lambda: isinstance(app_none.screen, _FileBrowser),
                       what="file browser")
        fb = app_none.screen
        chosen = []
        fb.app._on_arb_chosen = lambda p: chosen.append(p)
        fb.dismiss = lambda *a, **k: None  # don't actually pop in this test

        class Event:
            pass
        e = Event(); e.path = ARB
        fb.on_directory_tree_file_selected(e)
        assert chosen == [ARB]                      # .arb opens

        e2 = Event(); e2.path = ARB.with_suffix(".txt")
        fb.on_directory_tree_file_selected(e2)
        assert chosen == [ARB]                      # non-.arb ignored


# ===========================================================================
# Draft edit history — undo/redo (design §B.4 / §B.5)
# ===========================================================================
async def test_undo_reverses_mark(app):
    """Undo removes the last draft mark; the revision advances (spec: a
    revert is still a semantic edit)."""
    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)
        g = grid(app)
        g.move_cursor(row=1, column=2)
        await wait_for(pilot, lambda: app.cursor == (1, 2), what="cursor")
        rev0 = app.draft_revision
        await pilot.press("u")
        assert len(app.pending_instructions) == 1
        assert app.can_undo and not app.can_redo
        await pilot.press("ctrl+z")                 # undo
        assert len(app.pending_instructions) == 0   # the mark is gone
        assert app.can_redo and not app.can_undo
        assert app.draft_revision == rev0 + 2       # mark + undo


async def test_redo_reapplies_mark(app):
    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)
        g = grid(app)
        g.move_cursor(row=1, column=2)
        await wait_for(pilot, lambda: app.cursor == (1, 2), what="cursor")
        await pilot.press("u")
        await pilot.press("ctrl+z")
        assert len(app.pending_instructions) == 0
        await pilot.press("ctrl+y")                 # redo
        assert len(app.pending_instructions) == 1
        assert app.pending_instructions[0].kind == "U"
        assert app.can_undo and not app.can_redo


async def test_undo_walks_back_replacements(app):
    """A mark then a kind-replacement: undo steps back to the prior kind,
    then to unmarked (before/after are captured per edit)."""
    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)
        g = grid(app)
        g.move_cursor(row=1, column=2)
        await wait_for(pilot, lambda: app.cursor == (1, 2), what="cursor")
        await pilot.press("u")                       # mark U
        await pilot.press("p")                       # replace with P
        assert app.pending_instructions[0].kind == "P"
        await pilot.press("ctrl+z")                  # undo replacement -> U
        assert len(app.pending_instructions) == 1
        assert app.pending_instructions[0].kind == "U"
        await pilot.press("ctrl+z")                  # undo original mark -> cleared
        assert len(app.pending_instructions) == 0


async def test_new_mark_clears_redo(app):
    """A fresh edit makes the redo branch stale (standard undo/redo)."""
    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)
        g = grid(app)
        g.move_cursor(row=1, column=2)
        await wait_for(pilot, lambda: app.cursor == (1, 2), what="cursor")
        await pilot.press("u")
        await pilot.press("ctrl+z")
        assert app.can_redo
        await pilot.press("p")                       # a new edit
        assert not app.can_redo                      # redo branch cleared


async def test_undo_reset_after_accept(app):
    """Accept commits the draft, so the edit history is reset: a subsequent
    undo cannot reach back into the just-accepted marks."""
    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)
        g = grid(app)
        g.move_cursor(row=1, column=2)
        await wait_for(pilot, lambda: app.cursor == (1, 2), what="cursor")
        await pilot.press("u")
        assert app.can_undo
        await pilot.press("s")                        # stage OPT preview
        await wait_for(pilot, lambda: app.preview is not None, what="preview")
        await pilot.press("y")                        # accept
        await wait_for(pilot, lambda: app.pending is None, what="confirmed")
        assert app.can_undo is False                  # history reset
        await pilot.press("ctrl+z")                   # undo: nothing to do
        assert len(app.pending_instructions) == 0


# ===========================================================================
# Audit ownership — one result, one revision (design §B.5)
# ===========================================================================
async def test_audit_not_carried_into_accepted_result(app):
    """Audit belongs to the result it was computed on (design §B.5): the
    accepted result's audit may stay available while editing a draft, but
    accepting a *new* result must not carry the old result's audit into it.
    The new result has no audit until it is audited itself."""
    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)
        await pilot.press("a")                         # audit table 1
        await wait_for(pilot, lambda: len(app.session.audit_rows(0)) > 0,
                       what="audit")
        # Preview + accept a new result (OPT suppress on table 1).
        await pilot.press("s")
        await wait_for(pilot, lambda: app.preview is not None, what="preview")
        await pilot.press("y")                         # accept
        await wait_for(pilot, lambda: app.pending is None, what="confirmed")
        # The new (accepted) result has no audit yet — the old one didn't leak.
        assert app.session.audit_rows(0) == []


# ===========================================================================
# Workspace save (design §B.7)
# ===========================================================================
async def test_workspace_save_writes_manifest(app, tmp_path):
    """``w`` persists a versioned manifest of the logical draft state (recipe,
    revisions, accepted outcome) next to the arb — no solver run. A pending
    mark is captured as a pending instruction."""
    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)
        g = grid(app)
        g.move_cursor(row=1, column=2)
        await wait_for(pilot, lambda: app.cursor == (1, 2), what="cursor")
        await pilot.press("u")                       # a pending draft mark
        target = tmp_path / "ws.workspace.json"
        app._workspace_path = lambda: target         # keep the data dir clean
        await pilot.press("w")
        await wait_for(pilot, lambda: target.exists(), what="workspace file")
        m = ws.load_workspace(target)
        assert m.version == ws.WORKSPACE_VERSION
        assert m.arb == str(ARB)
        assert len(m.pending_instructions) == 1
        assert m.pending_instructions[0].kind == "U"
        assert m.draft_revision == app.draft_revision
        # The input fingerprint matches the (unmodified) arb.
        assert not ws.input_changed(m, ARB)


async def test_workspace_save_before_any_preview(app, tmp_path):
    """Workspace saving must work before a valid preview exists: a freshly
    loaded session (no ops, no marks) saves a valid empty-recipe manifest."""
    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)
        target = tmp_path / "ws.workspace.json"
        app._workspace_path = lambda: target
        await pilot.press("w")
        await wait_for(pilot, lambda: target.exists(), what="workspace file")
        m = ws.load_workspace(target)
        assert m.pending_instructions == []
        assert m.confirmed_ops == []
        # A pre-preview save records no accepted revision yet.
        assert m.accepted_revision == app.accepted_revision


# ===========================================================================
# Workspace reopen — restore (design §B.7 / §B.8)
# ===========================================================================
# The batch's input files (restore tests work on an isolated copy so they
# may mutate the input without touching data/).
# ``region2.hrc`` is referenced unquoted by the numeric ``RECODE 1, Region, 2``.
ARB_INPUTS = ("tau_testW.asc", "tau_testW.rda", "GK.grc", "IndCode2.grc",
              "region2.hrc")


@pytest.fixture
def tmp_arb(tmp_path) -> Path:
    """A working copy of the batch + its input files in an isolated dir."""
    for name in ("TestRecode.arb", *ARB_INPUTS):
        shutil.copy(DATA / name, tmp_path / name)
    return tmp_path / "TestRecode.arb"


async def test_restore_rebuilds_accepted_and_draft(tmp_arb):
    """Save (accepted OPT + a pending draft mark) in one session; a new app
    opened on the .workspace.json rebuilds the same accepted result and
    restores the pending draft (design §B.7/§B.8: 'save/reopen preserves
    pending intent and the exact accepted outcome')."""
    target = tmp_arb.parent / "t.workspace.json"
    app = TauArgusApp(tmp_arb)
    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)
        g = grid(app)
        g.move_cursor(row=1, column=2)
        await wait_for(pilot, lambda: app.cursor == (1, 2), what="cursor 1")
        await pilot.press("u")
        await pilot.press("s")
        await wait_for(pilot, lambda: app.preview is not None, what="preview")
        await pilot.press("y")                       # accept OPT (commits mark)
        g.move_cursor(row=2, column=3)
        await wait_for(pilot, lambda: app.cursor == (2, 3), what="cursor 2")
        await pilot.press("u")                       # a pending draft mark
        app._workspace_path = lambda: target
        await pilot.press("w")
        await wait_for(pilot, lambda: target.exists(), what="workspace file")
        final_counts = app.session.status_counts(0)
        pending = [(i.table, i.dim, i.kind)
                   for i in app.pending_instructions]
        draft_rev = app.draft_revision
        acc_rev = app.accepted_revision
        n_confirmed = len(app.confirmed_ops)

    app2 = TauArgusApp(workspace_path=target)
    async with app2.run_test(size=(120, 35)) as pilot2:
        await wait_for(pilot2, lambda: app2.session is not None, what="restore")
        # The exact accepted outcome, rebuilt through the engine.
        assert app2.session.status_counts(0) == final_counts
        # The pending intent is restored, and the recipe is re-adopted.
        assert [(i.table, i.dim, i.kind)
                for i in app2.pending_instructions] == pending
        assert len(app2.confirmed_ops) == n_confirmed
        assert app2.accepted_revision == acc_rev
        assert app2.draft_revision == draft_rev
        assert app2._arb == tmp_arb  # bound to the manifest's input


async def test_restore_pending_mark_is_undoable(tmp_arb):
    """A restored draft mark is a real draft edit: undo clears it and the
    undo is itself a semantic edit (design §B.5/§B.8)."""
    target = tmp_arb.parent / "t.workspace.json"
    app = TauArgusApp(tmp_arb)
    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)
        g = grid(app)
        g.move_cursor(row=1, column=2)
        await wait_for(pilot, lambda: app.cursor == (1, 2), what="cursor")
        await pilot.press("u")
        app._workspace_path = lambda: target
        await pilot.press("w")
        await wait_for(pilot, lambda: target.exists(), what="workspace file")
        saved_rev = app.draft_revision

    app2 = TauArgusApp(workspace_path=target)
    async with app2.run_test(size=(120, 35)) as pilot2:
        await wait_for(pilot2, lambda: app2.session is not None, what="restore")
        assert len(app2.pending_instructions) == 1
        await pilot2.press("ctrl+z")
        assert app2.pending_instructions == ()
        assert app2.draft_revision == saved_rev + 1  # undo advances the draft


async def test_restore_stale_input_opens_fresh(tmp_arb):
    """Changed input requires explicit reconciliation (design §B.7): the
    saved outcome is not treated as current — the current input is opened
    fresh, with no recipe or draft restored."""
    target = tmp_arb.parent / "t.workspace.json"
    app = TauArgusApp(tmp_arb)
    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)
        g = grid(app)
        g.move_cursor(row=1, column=2)
        await wait_for(pilot, lambda: app.cursor == (1, 2), what="cursor")
        await pilot.press("u")
        app._workspace_path = lambda: target
        await pilot.press("w")
        await wait_for(pilot, lambda: target.exists(), what="workspace file")
    # The operator edits the input after the save.
    tmp_arb.write_text("// operator edit\n" + tmp_arb.read_text())

    app2 = TauArgusApp(workspace_path=target)
    async with app2.run_test(size=(120, 35)) as pilot2:
        await wait_for(pilot2, lambda: app2.session is not None,
                       what="fresh session of the current input")
        assert app2.pending_instructions == ()
        assert app2.confirmed_ops == ()
        assert app2.accepted_revision == 0
        assert app2._arb == tmp_arb
        assert app2.last_action.startswith("workspace stale")


def test_arb_directory_tree_lists_workspace_files(tmp_path):
    """Saved workspaces are openable from the startup browser (design §B.7);
    other .json files are not."""
    (tmp_path / "t.workspace.json").write_text("{}")
    (tmp_path / "other.json").write_text("{}")
    tree = _ArbDirectoryTree(tmp_path)
    shown = {p.name for p in tree.filter_paths(list(tmp_path.iterdir()))}
    assert "t.workspace.json" in shown
    assert "other.json" not in shown


async def test_filebrowser_pick_workspace_restores(app_none, tmp_arb):
    """Picking a .workspace.json in the startup browser restores it (the
    same callback path as picking a .arb)."""
    target = tmp_arb.parent / "t.workspace.json"
    app = TauArgusApp(tmp_arb)
    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)
        app._workspace_path = lambda: target
        await pilot.press("w")
        await wait_for(pilot, lambda: target.exists(), what="workspace file")

    async with app_none.run_test(size=(100, 35)) as pilot:
        await wait_for(pilot, lambda: isinstance(app_none.screen, _FileBrowser),
                       what="file browser")
        app_none._on_arb_chosen(target)  # what the browser does on Enter
        await wait_for(pilot, lambda: app_none.session is not None,
                       what="restore after pick")
        assert app_none._arb == tmp_arb


async def test_restore_unknown_version_refused(tmp_arb):
    """An incompatible manifest version is refused explicitly (design §B.7):
    no session, no crash — back to the file browser to pick something else."""
    target = tmp_arb.parent / "t.workspace.json"
    app = TauArgusApp(tmp_arb)
    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)
        app._workspace_path = lambda: target
        await pilot.press("w")
        await wait_for(pilot, lambda: target.exists(), what="workspace file")
    d = json.loads(target.read_text())
    d["version"] = 99
    target.write_text(json.dumps(d))

    app2 = TauArgusApp(workspace_path=target)
    async with app2.run_test(size=(100, 35)) as pilot2:
        await wait_for(pilot2,
                       lambda: isinstance(app2.screen, _FileBrowser),
                       what="file browser after refused restore")
        assert app2.session is None
        assert "restore failed" in app2.last_action


async def test_replay_writes_runnable_batch(tmp_arb):
    """R materialises the accepted recipe as a headless-runnable batch next
    to the input (design §B.7): the confirmed op and the committed mark are
    encoded; the still-pending draft mark is not included."""
    app = TauArgusApp(tmp_arb)
    async with app.run_test(size=(120, 35)) as pilot:
        await _loaded(pilot, app)
        g = grid(app)
        g.move_cursor(row=1, column=2)
        await wait_for(pilot, lambda: app.cursor == (1, 2), what="cursor 1")
        await pilot.press("u")
        await pilot.press("s")
        await wait_for(pilot, lambda: app.preview is not None, what="preview")
        await pilot.press("y")                       # commits the mark
        g.move_cursor(row=2, column=3)
        await wait_for(pilot, lambda: app.cursor == (2, 3), what="cursor 2")
        await pilot.press("u")                       # pending: not replayed
        await pilot.press("shift+r")
        replay = tmp_arb.parent / "TestRecode.replay.arb"
        assert replay.exists()
        assert "<SUPPRESS> OPT(1)" in replay.read_text()
        apri_lines = [ln for ln in
                      (tmp_arb.parent / "TestRecode.tab1.apri").read_text()
                      .splitlines() if ln.endswith(";U")]
        assert len(apri_lines) == 1  # only the committed mark
        assert "replay batch written" in app.log_text
