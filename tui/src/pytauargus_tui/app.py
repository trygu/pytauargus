r"""tauargus-tui — Textual terminal UI over the pytauargus engine.

Design: ``docs/ui-design.md`` (Option B). The app is a thin view over one
``pytauargus`` Engine (via the :mod:`pytauargus_tui.model` view-model); every
action calls the same Python API the headless CLI uses, and the TUI
re-implements no engine behaviour.

Layout (design §B.1)::

    ┌ tables & vars ┬──────────────────────── grid ────────────────────────┐
    │ 1 SIZE|REGION ▸│ SIZE\REGION  R1    R2    R3    TOTAL                 │
    │ 2 REG|IND      │  R1        S     U     S     S                       │
    ├───────────────┴───────────────────────────────────────────────────────┤
    │ cell (R2, R3): value=12  status=U (unsafe_rule)  interval=[4, 30]     │
    ├───────────────────────────────────────────────────────────────────────┤
    │ ▸ log: table 1: 126 cells   ▸ hint: j/k=table s=suppress r=round ...  │
    └───────────────────────────────────────────────────────────────────────┘

Keymap: ``j``/``k`` or the navigator select a table; ``u``/``p``/``S`` mark
the cursor cell (require suppression / protect / override safe — immediate
draft edits, design §B.4); ``s``=OPT suppress, ``r``=RND round, ``a``=audit,
``o``=save intermediate ``.tab``, ``w``=save workspace, ``R``=write replay
batch, ``q``=quit. Launching on a ``.workspace.json`` restores a saved
session (design §B.7).

Cell marks update the pending instruction set with a visible marker and run
no solver. Suppress/round are **staged, preview before committing**
(design §B.3):
``s``/``r`` run a throwaway ``run_batch`` of the same ``.arb`` with the
suppression appended and show the per-status delta. ``y`` adopts the
previewed run as the working session; ``n`` cancels it. Confirmed ops are
materialised into the spec, so every later preview re-runs the spec plus
all confirmed ops plus the staged one.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    DataTable,
    DirectoryTree,
    Footer,
    Label,
    ListView,
    Static,
)
from textual.widgets._list_view import ListItem
from textual.worker import WorkerState

from . import workspace as _ws
from .model import (
    Cell,
    CellInstruction,
    EditHistory,
    Session,
    SuppressionOp,
    TableGrid,
    TableMeta,
    format_status_delta,
    replay_batch,
)

# Status symbol → colour (design §B.1).
_STATUS_COLOR = {
    "S": "green",
    "U": "red",
    "P": "cyan",
    "M": "yellow",
    "E": "dim",
    "?": "default",
}

# Contextual keymap hints for the status line (design §B.1: one-line keymap
# hint). The base hint is the default; the pending hint replaces it while a
# preview awaits confirm/cancel (the only time y/n are live). When the cursor
# sits on a cell, a per-status hint replaces the vague "u/p/S=mark" with the
# mark that is actually useful *on that kind of cell* (legacy §5.6: the
# apriori status letters are u=unsafe, p=protected, s=safe):
#   - unsafe (red)   -> it is a sensitive value; s (OPT) protects it by
#                       hiding the other cells that would reconstruct it
#   - secondary (yellow) -> this cell is hidden; p keeps it (do not suppress)
#   - safe (green)   -> u flags it as a risk the standard rules missed
_HINT_BASE = "u/p/S=mark s=OPT table r=round a=audit o=export w=save ?=help"
_HINT_PENDING = "y=accept  n=cancel  ?=help"
_HINT_UNSAFE = "red=sensitive value s=OPT table r=round a=audit o=export w=save ?=help"
_HINT_SECONDARY = "p=keep this cell s=OPT table r=round a=audit o=export w=save ?=help"
_HINT_SAFE = "u=flag as unsafe s=OPT table r=round a=audit o=export w=save ?=help"

# Help overlay body (press ? or F1). Plain explanation of the workflow, the
# keys, and the cell-status colours — the TUI has no other on-screen legend.
_HELP_TEXT = """
[bold]Tau-Argus TUI — how it works[/bold]

[bold]Workflow:[/bold] mark cells → preview a method → accept or cancel

  1. [bold]Mark[/bold] a cell (instant draft edit, no solve, nothing changes yet):
       u = U mark as unsafe (sensitive value)
       p = P protect (keep this cell; do not suppress it)
       S = safe (override a false positive)
     press the same mark again on that cell to remove it.
     ctrl+z / ctrl+y undo / redo your marks (a new mark clears the redo list).
  2. [bold]Preview[/bold] a method (runs on a throwaway copy; the live table is
     untouched until you accept):
       s = suppress (OPT)    r = round (RND)
     [bold]s runs OPT on the whole table[/bold], not the cell under the cursor:
     the solver chooses the minimum set of cells to hide so that no unsafe
     (red) cell can be reconstructed from the others. Hidden cells turn
     [yellow]yellow[/yellow]. Mark cells [red]u[/red] (unsafe) and [cyan]p[/cyan]
     (protect) before you run it so the solver knows which values to guard.
  3. [bold]Decide:[/bold]  y = accept (adopt this preview)    n = cancel (discard)

[bold]Cell status colours[/bold] (the number's colour is its status):
  [green]S[/green] safe · [red]U[/red] unsafe · [cyan]P[/cyan] protect · [yellow]M[/yellow] secondary unsafe · E empty

[bold]Draft markers[/bold] on the cursor cell (bold magenta):  * = U   + = S   # = P

[bold]Keys:[/bold]  j/k next/prev table · arrows move the cursor ·
  a = audit · o = export the accepted table (.tab) · w = save workspace
  R = write replay batch (headless .arb of the accepted recipe)
  ? = help · q = quit · ctrl+z = undo edit · ctrl+y = redo edit

[bold]Workspace:[/bold]  w saves a versioned manifest (the recipe, revisions
  and the accepted outcome) next to the .arb — no solver run. Reopen it
  later: launch tauargus-tui on the .workspace.json (or pick it in the
  file browser) — the accepted result is rebuilt through the engine and the
  pending draft is restored (undoable). If the .arb changed since the save,
  the old outcome is NOT treated as current: the current input opens fresh
  instead. Export (o) writes the accepted table only.
"""


def _fmt(value: float) -> str:
    """Compact number for terminal display (ints without a decimal point)."""
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}"


class _EngineLogHandler(logging.Handler):
    """Forward the pytauargus engine log stream into the TUI status line."""

    def __init__(self, app: "TauArgusApp") -> None:
        super().__init__()
        self._app = app

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._app.call_from_thread(self._app.push_log, msg)
        except Exception:
            pass


class _BusySpinner(Static):
    """Self-animating spinner shown while an engine call runs.

    textual 8.x ships no built-in ``Spinner`` widget, so the TUI carries
    this stand-in. Pure UI decoration — no engine logic.
    """

    _FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._frame = 0
        self._timer = None

    def start(self) -> None:
        self.styles.display = "block"
        self.visible = True
        if self._timer is None:
            self._frame = 0
            self._timer = self.set_interval(0.1, self._tick)
            self._tick()

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._frame = 0
        self.update("")
        # display:none removes the (idle) spinner from the layout entirely,
        # so the status line starts at column 0.
        self.styles.display = "none"
        self.visible = False

    def _tick(self) -> None:
        self._frame += 1
        self.update(self._FRAMES[self._frame % len(self._FRAMES)])


class _HelpScreen(ModalScreen[None]):
    """Overlay explaining the workflow, keys and cell-status colours.

    The TUI has no persistent legend, so this is the single place that says
    out loud what everything on screen means. Pure UI — no engine logic.
    """

    BINDINGS = [
        Binding("question_mark", "close", "Close"),
        Binding("f1", "close", "Close", show=False),
        Binding("escape", "close", "Close", show=False),
    ]

    CSS = """
    _HelpScreen { align-horizontal: center; align-vertical: middle; }
    _HelpScreen #help { width: 80; height: auto; border: round $accent;
                        padding: 0 1; }
    """

    def compose(self) -> ComposeResult:
        yield Static(_HELP_TEXT, id="help")

    def action_close(self) -> None:
        self.app.pop_screen()


class _ArbDirectoryTree(DirectoryTree):
    """A DirectoryTree that lists only subdirectories, ``.arb`` files and
    saved ``.workspace.json`` files — i.e. only files that make sense to
    open (design §B.7)."""

    def filter_paths(self, paths):
        for path in paths:
            name = path.name
            if name.startswith("."):
                continue
            if (path.is_dir() or path.suffix.lower() == ".arb"
                    or name.endswith(".workspace.json")):
                yield path


class _FileBrowser(ModalScreen[Path]):
    """Startup picker for a no-argument launch: choose a ``.arb`` batch file.

    Wraps the standard textual ``DirectoryTree`` (filtered to ``.arb``).
    Pure UI — on a pick it calls ``app._on_arb_chosen(path)``; on cancel,
    ``app._on_arb_cancelled()``.
    """

    BINDINGS = [
        Binding("q", "cancel", "Cancel"),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    CSS = """
    _FileBrowser { align-horizontal: center; align-vertical: middle; }
    _FileBrowser #fb { width: 72; height: 32; }
    #fbtree { height: 1fr; border: round $primary; }
    #fbhint { height: 1; }
    """

    def __init__(self, start: Path) -> None:
        super().__init__()
        self._start = start

    def compose(self) -> ComposeResult:
        with Vertical(id="fb"):
            yield _ArbDirectoryTree(self._start, id="fbtree")
            yield Static("choose a .arb batch or .workspace.json — "
                         "Enter opens · q cancels", id="fbhint")

    def on_mount(self) -> None:
        self.query_one("#fbtree", DirectoryTree).focus()

    def on_directory_tree_file_selected(self, event) -> None:
        # The tree is filtered to .arb / .workspace.json files; this guard
        # makes Enter open a real openable file (and ignore anything
        # unexpected).
        if (event.path is not None
                and (event.path.suffix.lower() == ".arb"
                     or event.path.name.endswith(".workspace.json"))):
            self.app._on_arb_chosen(event.path)
            self.dismiss()

    def action_cancel(self) -> None:
        self.app._on_arb_cancelled()
        self.dismiss()


@dataclass(frozen=True)
class _Restored:
    """The rebuilt state returned by the restore worker (design §B.7)."""

    session: Session
    confirmed_ops: Tuple[SuppressionOp, ...]
    confirmed_instructions: Tuple[CellInstruction, ...]
    pending_instructions: Tuple[CellInstruction, ...]
    accepted_revision: int
    draft_revision: int
    tab: int


class TauArgusApp(App):
    """Tau-Argus TUI: navigator + grid + detail + log over one Engine."""

    TITLE = "tau-argus"
    CSS = """
    Screen { layout: vertical; }
    #body { layout: horizontal; height: 1fr; }
    #nav { width: 30; border: round $primary; }
    #grid { width: 1fr; border: round $primary; }
    #detail { height: 3; border: round $primary; }
    #logline { height: 1; layout: horizontal; align-vertical: middle; }
    #logline #spin { width: 1; margin-right: 1; }
    #logtext { width: 1fr; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("j", "table_next", "Next table"),
        Binding("k", "table_prev", "Prev table"),
        Binding("u", "mark_suppress", "Mark U (require suppression)",
                show=False),
        Binding("p", "mark_protect", "Mark P (protect)", show=False),
        Binding("shift+s", "mark_safe", "Mark S (override safe)", show=False),
        Binding("s", "suppress", "Run OPT on this table"),
        Binding("r", "round", "Round (RND)"),
        Binding("a", "audit", "Audit"),
        Binding("o", "save", "Export table (.tab)"),
        Binding("w", "workspace_save", "Save workspace"),
        Binding("shift+r", "replay", "Write replay batch", show=False),
        Binding("y", "confirm", "Confirm preview", show=False),
        Binding("n", "cancel", "Cancel preview", show=False),
        Binding("ctrl+z", "undo", "Undo edit", show=False),
        Binding("ctrl+y", "redo", "Redo edit", show=False),
        Binding("question_mark", "help", "Help"),
        Binding("f1", "help", "Help", show=False),
    ]

    def __init__(self, arb_path: Optional[Path] = None,
                 workspace_path: Optional[Path] = None) -> None:
        super().__init__()
        # None => no-arg launch: show the file browser first (design §B.7).
        self._arb: Optional[Path] = Path(arb_path) if arb_path is not None else None
        # A saved workspace to restore on startup (design §B.7). Mutually
        # exclusive with arb_path; the manifest's .arb wins if both given.
        self._ws_path: Optional[Path] = (Path(workspace_path)
                                         if workspace_path is not None
                                         else None)
        self._session: Optional[Session] = None
        self._tab: int = 0
        self._grid: Optional[TableGrid] = None
        self._detail = ""
        self._log = "ready"
        self._log_handler: Optional[_EngineLogHandler] = None
        # Staged preview state (design §B.3): _pending is the staged op,
        # _preview the throwaway session it produced, _confirmed the ops
        # already adopted (re-run into every later preview).
        self._pending: Optional[SuppressionOp] = None
        self._preview: Optional[Session] = None
        self._delta: str = ""
        self._confirmed: List[SuppressionOp] = []
        self._busy: bool = False
        self._last_action: str = ""
        # Draft cell instructions (design §B.4): _pending_instr is the current
        # draft keyed by stable cell identity (table, dim); _confirmed_instr
        # are the marks adopted by a previous accept (re-applied into every
        # later preview).
        self._pending_instr: Dict[Tuple[int, Tuple[str, ...]],
                                  CellInstruction] = {}
        self._confirmed_instr: List[CellInstruction] = []
        # Undo/redo command history over the draft's cell edits (design §B.4).
        self._history = EditHistory()
        self._cursor: Optional[Tuple[int, int]] = None
        # Authoritative state (design §B.4: separate facts, not one dirty
        # flag). _draft_revision advances on every draft edit; _accepted_rev
        # records the draft revision an accepted result was promoted from
        # (0 = none yet); _preview_revision is the draft revision the live
        # preview was built from (None = no preview) → drives "fresh"/"outdated".
        self._draft_revision: int = 1
        self._accepted_revision: int = 0
        self._preview_revision: Optional[int] = None
        self._status_line: str = ""

    # -- inspection surface (used by tests / embedders) ----------------------
    @property
    def session(self) -> Optional[Session]:
        return self._session

    @property
    def current_table(self) -> int:
        return self._tab

    @property
    def detail_text(self) -> str:
        return self._detail

    @property
    def log_text(self) -> str:
        return self._log

    @property
    def preview(self) -> Optional[Session]:
        """The throwaway session of the pending preview (None if none)."""
        return self._preview

    @property
    def pending(self) -> Optional[SuppressionOp]:
        """The staged suppression awaiting confirm/cancel (None if none)."""
        return self._pending

    @property
    def preview_delta(self) -> str:
        """Formatted per-status delta of the pending preview ("" if none)."""
        return self._delta

    @property
    def busy(self) -> bool:
        """True while an engine worker runs (spinner visible)."""
        return self._busy

    @property
    def last_action(self) -> str:
        """Human-readable outcome of the last preview/confirm/cancel or
        workspace restore/stale reconciliation."""
        return self._last_action

    @property
    def confirmed_ops(self) -> Tuple[SuppressionOp, ...]:
        """Suppressions confirmed so far, in order (feed later previews)."""
        return tuple(self._confirmed)

    @property
    def cursor(self) -> Optional[Tuple[int, int]]:
        """Grid (row, column) of the highlighted cell (column 0 = label)."""
        return self._cursor

    @property
    def pending_instructions(self) -> Tuple[CellInstruction, ...]:
        """The draft's uncommitted cell instructions (design §B.4)."""
        return tuple(self._pending_instr.values())

    @property
    def confirmed_instructions(self) -> Tuple[CellInstruction, ...]:
        """Cell instructions adopted by a previous accept (feed previews)."""
        return tuple(self._confirmed_instr)

    @property
    def can_undo(self) -> bool:
        """Whether a draft edit is available to undo (design §B.5)."""
        return self._history.can_undo()

    @property
    def can_redo(self) -> bool:
        """Whether an undone draft edit is available to redo (design §B.5)."""
        return self._history.can_redo()

    @property
    def draft_revision(self) -> int:
        """Current draft revision (1 = spec as loaded; +1 per draft edit)."""
        return self._draft_revision

    @property
    def accepted_revision(self) -> int:
        """Draft revision the accepted result was promoted from (0 = none)."""
        return self._accepted_revision

    @property
    def preview_freshness(self) -> str:
        """— (no preview) / fresh / outdated, vs the current draft revision."""
        if self._preview_revision is None:
            return "—"
        return ("fresh" if self._preview_revision == self._draft_revision
                else "outdated")

    @property
    def status_text(self) -> str:
        """The composed bottom status line — what the user actually sees."""
        return self._status_line

    # -- compose ---------------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            yield ListView(id="nav")
            yield DataTable(id="grid", cursor_type="cell")
        yield Static("", id="detail")
        with Horizontal(id="logline"):
            yield _BusySpinner(id="spin")
            yield Static("", id="logtext")
        yield Footer()

    # -- lifecycle ---------------------------------------------------------------
    def on_mount(self) -> None:
        self._log_handler = _EngineLogHandler(self)
        self._log_handler.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger("pytauargus").addHandler(self._log_handler)
        if self._ws_path is not None:
            self._start_restore()
        elif self._arb is not None:
            self._start_load()
        else:
            # No batch: show the picker; it calls back with the choice.
            self._set_log("no batch given — choose a .arb file (or q to quit)")
            self.push_screen(_FileBrowser(start=Path.cwd()))

    def on_resize(self, event) -> None:
        # The status line is composed to the terminal width; re-compose on
        # resize so it never wraps.
        self._refresh_status()

    def _start_load(self) -> None:
        """Kick off the (exclusive) load worker for the chosen batch file."""
        self._set_log(f"loading {self._arb.name} ...")
        self._set_busy(True)
        # exit_on_error=False: engine errors are expected, displayable
        # conditions (e.g. legacy 5008 on re-OPT) — log them, don't panic.
        self.run_worker(self._load, group="engine", exclusive=True, thread=True,
                        description="load", exit_on_error=False)

    def _on_arb_chosen(self, path: Path) -> None:
        """The file browser returned a file: load a ``.arb`` batch, or
        restore a ``.workspace.json`` (design §B.7)."""
        if path.name.endswith(".workspace.json"):
            self._ws_path = Path(path)
            self._start_restore()
        else:
            self._arb = Path(path)
            self._start_load()

    def _on_arb_cancelled(self) -> None:
        """The file browser was cancelled: quit (nothing to load)."""
        self.exit()

    def on_unmount(self) -> None:
        if self._log_handler is not None:
            logging.getLogger("pytauargus").removeHandler(self._log_handler)
            self._log_handler = None

    def _load(self) -> Session:
        return Session.from_arb(self._arb)

    # -- workspace restore (design §B.7) ------------------------------------
    def _start_restore(self) -> None:
        """Kick off the (exclusive) restore worker for a saved workspace."""
        self._set_log(f"restoring {self._ws_path.name} ...")
        self._set_busy(True)
        self.run_worker(self._restore, group="engine", exclusive=True,
                        thread=True, description="restore", exit_on_error=False)

    def _restore(self) -> "_Restored":
        """Worker body: rebuild the session from the saved workspace.

        The saved outcome is not read from disk: the recorded recipe is
        re-run on a fresh engine (spec + confirmed instructions + confirmed
        ops), and the manifest's revisions/draft are returned for the UI to
        re-adopt. Raises ``WorkspaceError``/``WorkspaceStale`` — the app
        reconciles explicitly (design §B.7).
        """
        m = _ws.load_workspace(self._ws_path)
        if m.version != _ws.WORKSPACE_VERSION:
            raise _ws.WorkspaceError(
                f"unsupported workspace version {m.version} "
                f"(this build reads {_ws.WORKSPACE_VERSION})")
        if _ws.input_changed(m, m.arb):
            raise _ws.WorkspaceStale(m.arb)
        session = Session.from_workspace(self._ws_path)
        return _Restored(
            session=session,
            confirmed_ops=tuple(SuppressionOp(kind=o.kind, tab=o.tab,
                                               rnd_base=o.rnd_base)
                                 for o in m.confirmed_ops),
            confirmed_instructions=tuple(
                CellInstruction(table=i.table, dim=i.dim, kind=i.kind)
                for i in m.confirmed_instructions),
            pending_instructions=tuple(
                CellInstruction(table=i.table, dim=i.dim, kind=i.kind)
                for i in m.pending_instructions),
            accepted_revision=m.accepted_revision,
            draft_revision=m.draft_revision,
            tab=m.tab,
        )

    def _apply_restore(self, st: "_Restored") -> None:
        """Adopt the rebuilt session and re-adopt its logical state
        (design §B.7/§B.8): confirmed ops/instructions, the pending draft
        (each restored mark is an undoable edit), and the saved revisions."""
        self._session = st.session
        self._arb = st.session.arb_path
        self._ws_path = None  # restored; further saves go next to the .arb
        self._confirmed = list(st.confirmed_ops)
        self._confirmed_instr = list(st.confirmed_instructions)
        self._pending = None
        self._preview = None
        self._preview_revision = None
        self._history.reset()
        for instr in st.pending_instructions:
            self._record_edit((instr.table - 1, instr.dim), None, instr.kind)
        # The saved revision is authoritative (it counts every edit made
        # since the spec, including ones whose marks were later cleared).
        self._draft_revision = st.draft_revision
        self._accepted_revision = st.accepted_revision
        self._populate_nav()
        self._select_table(min(st.tab, self._session.n_tables - 1))
        note = (f", {len(st.confirmed_ops)} confirmed op(s)"
                if st.confirmed_ops else "")
        self._set_log(f"restored workspace: {st.session.n_tables} "
                      f"table(s){note}")
        self._last_action = "workspace restored"

    def _on_restore_failed(self, err: Exception) -> None:
        """Restore failed (design §B.7). A stale/missing input is reconciled
        explicitly: the saved outcome is NOT treated as current, and the
        current input is opened fresh when it still exists. Anything else
        (bad version, corrupt file) is reported and the user is sent back to
        the file browser."""
        if isinstance(err, _ws.WorkspaceStale):
            arb = Path(err.args[0]) if err.args else None
            if arb is not None and arb.is_file():
                self._last_action = (f"workspace stale: {arb.name} changed "
                                     f"since save — current input opened fresh")
                self._arb = arb
                self._ws_path = None
                self._start_load()
                return
        self._set_busy(False)
        self._last_action = f"workspace restore failed: {err}"
        start = self._ws_path.parent if self._ws_path is not None else Path.cwd()
        self.push_screen(_FileBrowser(start=start))

    # -- worker events --------------------------------------------------------------
    def on_worker_state_changed(self, event) -> None:
        w = event.worker
        if w.group != "engine":
            return
        if w.state is WorkerState.SUCCESS:
            if w.description == "load":
                self._session = w.result
                self._populate_nav()
                self._set_log(f"loaded {self._session.n_tables} table(s)")
                self._select_table(0)
            elif w.description == "restore":
                self._apply_restore(w.result)
            elif w.description == "grid":
                self._grid = w.result
                self._populate_grid()
                self._set_log(f"table {self._tab + 1}: {self._grid.tab.n_cells} cells")
            elif w.description == "action":
                self._grid = self._session.grid(self._tab)
                self._populate_grid()
                self._set_log(w.result)
            elif w.description == "preview":
                if self._pending is not None:
                    self._preview, self._delta = w.result
                    self._preview_revision = self._draft_revision
                    self._last_action = (f"{self._pending.kind} previewed "
                                          f"on table {self._pending.tab}")
                    self._show_preview_pending()
        elif w.state is WorkerState.ERROR:
            if w.description == "restore":
                self._on_restore_failed(w.error)
                return  # busy + log handled by the reconciliation
            if w.description == "preview" and self._pending is not None:
                # A failed preview (e.g. legacy 5008 on re-OPT) is dropped;
                # the live session is untouched.
                self._pending = None
                self._preview = None
                self._preview_revision = None
                self._set_detail(self._table_detail(
                    self._session.table(self._tab)))
            self._set_log(f"error: {w.error}")
        if w.state in (WorkerState.SUCCESS, WorkerState.ERROR,
                       WorkerState.CANCELLED):
            self._set_busy(False)

    # -- navigator --------------------------------------------------------------------
    def _populate_nav(self) -> None:
        nav = self.query_one("#nav", ListView)
        for i, t in enumerate(self._session.tables()):
            label = f"{i + 1}  {'|'.join(t.exp_vars)} | {t.resp_var}"
            nav.append(ListItem(Label(label)))

    def on_list_view_selected(self, event) -> None:
        self._select_table(event.index)

    def action_table_next(self) -> None:
        if self._session is not None and self._tab < self._session.n_tables - 1:
            self._select_table(self._tab + 1)

    def action_table_prev(self) -> None:
        if self._session is not None and self._tab > 0:
            self._select_table(self._tab - 1)

    def _select_table(self, i: int) -> None:
        if self._pending is not None:
            return  # table switching is locked while a preview is pending
        self._tab = i
        meta = self._session.table(i)
        self.query_one("#grid", DataTable).clear(columns=True)
        self._set_detail(self._table_detail(meta))
        self._set_log(f"table {i + 1}: loading grid ...")
        self._set_busy(True)
        self.run_worker(lambda: self._session.grid(i), group="engine",
                        exclusive=True, thread=True, description="grid",
                        exit_on_error=False)

    # -- grid --------------------------------------------------------------------------
    # Pending-mark glyphs per instruction kind (design §B.5: a separate
    # marker, kept distinct from the status colour).
    _MARK = {"U": "*", "S": "+", "P": "#"}

    @staticmethod
    def _cell_text(cell: Cell, marker: Optional[str] = None) -> Text:
        color = _STATUS_COLOR.get(cell.symbol, "default")
        text = Text(_fmt(cell.value), style=color)
        if marker:
            text.append(marker, style="bold magenta")
        return text

    def _pending_marker(self, tab_index: int,
                        dim: Tuple[str, ...]) -> Optional[str]:
        instr = self._pending_instr.get((tab_index, tuple(dim)))
        if instr is None:
            return None
        return self._MARK.get(instr.kind)

    def _populate_grid(self, reset_cursor: bool = True) -> None:
        g = self._grid
        if g is None:
            return
        grid = self.query_one("#grid", DataTable)
        grid.clear(columns=True)
        header = f"{g.row_var}\\{g.col_var}" if g.col_var else g.row_var
        grid.add_column(header, key="rl")
        if g.col_var is not None:
            for ci in range(len(g.cols)):
                grid.add_column(g.cols[ci], key=f"c{ci}")
        else:
            grid.add_column(g.tab.resp_var, key="c0")
        for ri, row in enumerate(g.cells):
            values: List[object] = [Text(g.rows[ri], style="bold")]
            for ci, cell in enumerate(row):
                dim = ((g.rows[ri], g.cols[ci]) if g.col_var is not None
                       else (g.rows[ri],))
                marker = self._pending_marker(g.tab.index, dim)
                values.append(self._cell_text(cell, marker))
            grid.add_row(*values, key=f"r{ri}")
        if reset_cursor:
            grid.move_cursor(row=0, column=1)

    def _repopulate_preserving_cursor(self) -> None:
        """Re-render the grid without moving the cursor off the cell the
        operator is inspecting. Used when swapping the grid between the
        accepted session and a pending preview (design §B.5: the operator
        inspects the preview's secondary suppressions in place)."""
        saved = self._cursor
        self._populate_grid(reset_cursor=False)
        if saved is not None:
            self.query_one("#grid", DataTable).move_cursor(
                row=saved[0], column=saved[1])

    def _current_cell(self) -> Optional[Cell]:
        """The cell under the cursor, for the contextual status-line hint.

        Returns None when there is no grid, no cursor yet, or the cursor is on
        the row-label column / out of range (the hint then falls back to base).
        """
        if self._grid is None or self._cursor is None:
            return None
        r, c = self._cursor
        if r < 0 or r >= len(self._grid.cells):
            return None
        row = self._grid.cells[r]
        ci = c - 1  # column 0 of the DataTable is the row label
        if ci < 0 or ci >= len(row):
            return None
        return row[ci]

    def on_data_table_cell_selected(self, event) -> None:
        self._on_cell_coordinate(event.coordinate)

    def on_data_table_cell_highlighted(self, event) -> None:
        # Follow the cursor (hover / keyboard) so the detail pane stays live.
        self._on_cell_coordinate(event.coordinate)

    def _on_cell_coordinate(self, coordinate) -> None:
        if self._grid is None:
            return
        r, c = coordinate.row, coordinate.column
        if r < 0 or r >= len(self._grid.cells):
            return
        self._cursor = (r, c)
        row = self._grid.cells[r]
        if c < 1 or c - 1 >= len(row):
            self._refresh_status()  # hint may fall back to base (label column)
            return
        self._set_cell_detail(r, c - 1)
        self._refresh_status()  # the contextual hint depends on this cell

    # -- detail pane -----------------------------------------------------------------------
    def _table_detail(self, meta: TableMeta) -> str:
        rules = " ".join(meta.rules) if meta.rules else "no safety rules"
        return (f"table {meta.index + 1}: {' | '.join(meta.exp_vars)} | "
                f"{meta.resp_var}   {meta.n_cells} cells  {rules}")

    def _set_cell_detail(self, r: int, c: int) -> None:
        g = self._grid
        cell = g.cells[r][c]
        pos = f"({g.rows[r]}"
        if g.col_var is not None:
            pos += f", {g.cols[c]}"
        pos += ")"
        text = (f"cell {pos}: value={_fmt(cell.value)}  "
                f"status={cell.symbol} ({cell.status_name})")
        # Pending draft instruction on this cell (design §B.4).
        dim = (g.rows[r], g.cols[c]) if g.col_var is not None else (g.rows[r],)
        instr = self._pending_instr.get((g.tab.index, tuple(dim)))
        if instr is not None:
            names = {"U": "require suppression", "S": "override safe",
                     "P": "protect"}
            text += f"  pending: {instr.kind} ({names[instr.kind]})"
        # Realised feasibility interval, when this table has been audited
        # (row-major cell index matches the audit row's ``cell`` key).
        n_cols = len(g.cols) if g.col_var is not None else 1
        idx = r * n_cols + c
        for row in self._session.audit_rows(g.tab.index):
            if row.get("cell") == idx:
                text += f"  interval=[{_fmt(row['min'])}, {_fmt(row['max'])}]"
                break
        self._set_detail(text)

    # -- actions ---------------------------------------------------------------------------
    def action_suppress(self) -> None:
        self._stage("OPT")

    def action_round(self) -> None:
        self._stage("RND")

    def action_mark_suppress(self) -> None:
        self._mark_cell("U")

    def action_mark_protect(self) -> None:
        self._mark_cell("P")

    def action_mark_safe(self) -> None:
        self._mark_cell("S")

    def action_undo(self) -> None:
        """Reverse the last draft edit (design §B.5). Reverting an edit still
        advances the draft revision, so any preview is no longer eligible."""
        if self._session is None or self._pending is not None:
            return  # no draft, or locked while a preview is pending
        cmd = self._history.undo()
        if cmd is None:
            self._set_log("nothing to undo")
            return
        self._apply_instr(cmd.key, cmd.before)
        self._draft_revision += 1
        self._set_log(f"undo: cell {cmd.key[1]} -> {cmd.before or 'cleared'}")
        self._redraft()

    def action_redo(self) -> None:
        """Re-apply the last undone draft edit (design §B.5)."""
        if self._session is None or self._pending is not None:
            return
        cmd = self._history.redo()
        if cmd is None:
            self._set_log("nothing to redo")
            return
        self._apply_instr(cmd.key, cmd.after)
        self._draft_revision += 1
        self._set_log(f"redo: cell {cmd.key[1]} -> {cmd.after or 'cleared'}")
        self._redraft()

    def action_audit(self) -> None:
        self._run_action("AUDIT")

    def action_save(self) -> None:
        self._run_action("SAVE")

    def _workspace_path(self) -> Path:
        """Default manifest location, next to the input ``.arb``."""
        return self._arb.with_name(self._arb.stem + ".workspace.json")

    def action_workspace_save(self) -> None:
        """Persist the logical draft state + accepted-result references as a
        versioned manifest (design §B.7). Works before any preview exists and
        runs no solver — it only records the current logical state. The write
        is atomic, so a failed save leaves the last workspace intact."""
        if self._session is None or self._arb is None:
            return
        try:
            summary = {str(i): self._session.status_counts(i)
                       for i in range(self._session.n_tables)}
        except Exception:
            summary = {}
        try:
            m = _ws.build_manifest(
                self._arb,
                draft_revision=self._draft_revision,
                accepted_revision=self._accepted_revision,
                confirmed_ops=self._confirmed,
                confirmed_instructions=self._confirmed_instr,
                pending_instructions=list(self._pending_instr.values()),
                accepted_summary=summary,
                tab=self._tab,
            )
            path = _ws.save_workspace(m, self._workspace_path())
            self._set_log(f"workspace saved: {path.name}")
        except OSError as e:
            self._set_log(f"workspace save failed: {e}")

    def action_replay(self) -> None:
        """Write the accepted recipe as a replayable headless batch next to
        the input (design §B.7): a ``<stem>.replay.arb`` with the confirmed
        ops' ``<SUPPRESS>`` lines and the confirmed marks' apriori files.
        Pure file I/O — no solver run. The pending draft is NOT included:
        the replay encodes the accepted recipe only (a different outcome
        needs a new preview and acceptance)."""
        if self._session is None or self._arb is None:
            return
        if self._pending is not None:
            return  # locked while a preview is pending
        try:
            path = replay_batch(self._arb, self._confirmed,
                                self._confirmed_instr)
            note = (f" — {len(self._pending_instr)} draft edit(s) not included"
                    if self._pending_instr else "")
            self._set_log(f"replay batch written: {path.name}{note}")
        except OSError as e:
            self._set_log(f"replay write failed: {e}")

    def action_help(self) -> None:
        """Show the on-screen explanation of keys, colours and workflow."""
        self.push_screen(_HelpScreen())

    def action_confirm(self) -> None:
        """Adopt the pending preview as the working session (design §B.3)."""
        if self._pending is None or self._preview is None:
            return
        op = self._pending
        self._session = self._preview
        # The adopted session already ran from the original spec (plus the
        # draft instructions); keep it bound to that .arb so later previews
        # re-run the spec plus the (now longer) confirmed lists.
        self._session.adopt_spec(self._arb)
        self._confirmed.append(op)
        # Promote the draft's cell instructions to confirmed (design §B.5:
        # accept commits the marks; the preview already applied them, so the
        # live grid reflects them and future previews re-apply them).
        for instr in self._pending_instr.values():
            self._confirmed_instr.append(instr)
        self._pending_instr.clear()
        self._history.reset()  # the draft is committed; start a fresh history
        self._pending = None
        self._preview = None
        self._preview_revision = None  # the preview is consumed into accepted
        self._accepted_revision = self._draft_revision  # result = this revision
        self._last_action = f"{op.kind} confirmed on table {op.tab}"
        # The preview worker already cached this table's grid (it computed
        # the status counts from it), so the refresh is a pure cache read.
        self._grid = self._session.grid(self._tab)
        self._populate_grid()
        self._set_log(f"table {op.tab}: {op.kind} confirmed — grid updated")

    def action_cancel(self) -> None:
        """Discard the pending preview; the live session is untouched."""
        if self._pending is None:
            return
        op = self._pending
        self._pending = None
        self._preview = None
        self._preview_revision = None  # the preview is discarded
        self._last_action = f"{op.kind} preview cancelled"
        # The preview's grid was shown for inspection only; restore the live
        # (accepted) grid. It is a cache read — the table's grid was built on
        # selection and the session is untouched while a preview is pending.
        self._grid = self._session.grid(self._tab)
        self._repopulate_preserving_cursor()
        self._set_detail(self._table_detail(self._session.table(self._tab)))
        self._set_log(f"table {op.tab}: preview cancelled")

    def _stage(self, kind: str) -> None:
        """Stage a suppression: preview it on a throwaway run, show the
        per-status delta, wait for confirm (y) / cancel (n)."""
        if self._session is None or self._pending is not None:
            return
        tab = self._tab
        base = 0
        if kind == "RND":
            base = self._session.engine._min_round_base(tab)
        op = SuppressionOp(kind=kind, tab=tab + 1, rnd_base=base)
        self._pending = op
        self._set_log(f"table {tab + 1}: previewing {kind} ...")
        self._set_busy(True)
        self.run_worker(lambda: self._run_preview(op), group="engine",
                        exclusive=True, thread=True, description="preview",
                        exit_on_error=False)

    def _run_preview(self, op: SuppressionOp) -> Tuple[Session, str]:
        """Worker body: throwaway run of the spec + the draft's cell
        instructions + confirmed ops + staged op, and the per-status delta of
        the staged table (design §B.3/§B.5 — one preview batches the complete
        instruction set)."""
        s = self._session
        tab = op.tab - 1
        prev = s.preview(list(self._confirmed) + [op],
                         self._all_instructions())
        delta = format_status_delta(s.status_counts(tab),
                                    prev.status_counts(tab))
        return prev, delta

    def _all_instructions(self) -> List[CellInstruction]:
        """The full instruction set for a preview: confirmed marks plus the
        current draft, with a pending mark overriding a confirmed one on the
        same cell (latest intent wins)."""
        merged: Dict[Tuple[int, Tuple[str, ...]], CellInstruction] = {}
        for instr in self._confirmed_instr:
            merged[(instr.table, instr.dim)] = instr
        for instr in self._pending_instr.values():
            merged[(instr.table, instr.dim)] = instr
        return list(merged.values())

    def _mark_cell(self, kind: str) -> None:
        """Set (or toggle/replace) a draft cell instruction on the cursor
        cell. Immediate: updates the draft and the grid marker, runs no
        solver and mutates no accepted run (design §B.5)."""
        if self._session is None or self._grid is None or self._cursor is None:
            return
        if self._pending is not None:
            return  # locked while a preview is pending
        g = self._grid
        r, c = self._cursor
        if c < 1 or c - 1 >= len(g.cells[r]):
            return  # not on a data cell
        data_c = c - 1
        dim = ((g.rows[r], g.cols[data_c]) if g.col_var is not None
               else (g.rows[r],))
        if self._session.is_total(g.tab.index, dim):
            self._set_log("total cells cannot be marked in this iteration")
            return
        key = (g.tab.index, tuple(dim))
        existing = self._pending_instr.get(key)
        before = existing.kind if existing is not None else None
        # Same mark again clears it; otherwise set/replace with ``kind``.
        after = (None if (existing is not None and existing.kind == kind)
                 else kind)
        self._record_edit(key, before, after)
        msg = (f"cell {dim}: mark removed" if after is None
               else f"cell {dim}: marked {after}")
        self._set_log(msg)
        self._redraft()

    def _apply_instr(self, key: Tuple[int, Tuple[str, ...]],
                     kind: Optional[str]) -> None:
        """Set (or clear) the draft instruction for a cell by its identity."""
        if kind is None:
            self._pending_instr.pop(key, None)
        else:
            self._pending_instr[key] = CellInstruction(
                table=key[0] + 1, dim=key[1], kind=kind)

    def _record_edit(self, key: Tuple[int, Tuple[str, ...]],
                     before: Optional[str], after: Optional[str]) -> None:
        """Apply a draft edit and enter it in the undoable history
        (design §B.4). Every edit advances the draft revision."""
        self._history.record(key, before, after)
        self._apply_instr(key, after)
        self._draft_revision += 1

    def _redraft(self) -> None:
        """Re-render the grid after a draft edit, restoring the cursor to its
        previous cell (clear+re-add drops the highlight)."""
        r, c = self._cursor
        self._populate_grid(reset_cursor=False)
        if r is not None and c >= 1 and c - 1 < len(self._grid.cells[r]):
            self.query_one("#grid", DataTable).move_cursor(row=r, column=c)
            self._set_cell_detail(r, c - 1)

    def _run_action(self, what: str) -> None:
        if self._session is None or self._pending is not None:
            return
        self._set_log(f"table {self._tab + 1}: {what.lower()} ...")
        self._set_busy(True)
        self.run_worker(lambda: self._action(what), group="engine",
                        exclusive=True, thread=True, description="action",
                        exit_on_error=False)

    def _action(self, what: str) -> str:
        s = self._session
        tab = self._tab
        if what == "AUDIT":
            rows = s.audit(tab)
            return f"table {tab + 1}: audited {len(rows)} cell(s)"
        if what == "SAVE":
            out_dir = self._arb.parent / "out"
            out_dir.mkdir(exist_ok=True)
            path = out_dir / f"table_{tab + 1}.tab"
            s.write_intermediate_table(tab, str(path),
                                       with_audit=bool(s.audit_rows(tab)))
            return f"saved {path}"
        raise ValueError(what)

    # -- status line --------------------------------------------------------------------------
    def _set_detail(self, text: str) -> None:
        self._detail = text
        self.query_one("#detail", Static).update(text)

    def _set_log(self, text: str) -> None:
        self._log = text
        self._refresh_status()

    def _refresh_status(self) -> None:
        """Compose the bottom status line (design §B.1) and publish it.

        Carries the distinct state facts the spec requires rather than one
        dirty flag: draft revision, pending edit count, preview freshness,
        accepted revision — plus the log message and a contextual keymap hint.
        """
        edits = len(self._pending_instr)
        acc = ("—" if self._accepted_revision == 0
               else f"r{self._accepted_revision}")
        if self._pending is not None:
            hint = _HINT_PENDING
        else:
            # Contextual hint: show the mark that is useful on this kind of
            # cell (legacy §5.6). A red cell is a sensitive value that s
            # protects; a hidden (yellow) cell can be kept with p; a green
            # cell can be flagged as a risk with u.
            cell = self._current_cell()
            sym = cell.symbol if cell is not None else None
            if sym == "U":
                hint = _HINT_UNSAFE
            elif sym == "M":
                hint = _HINT_SECONDARY
            elif sym == "S":
                hint = _HINT_SAFE
            else:
                hint = _HINT_BASE
        state = (f"draft r{self._draft_revision} · {edits} edit(s) · "
                 f"preview {self.preview_freshness} · acc {acc}")
        # One line, always (§B.1: the status line must never wrap): the
        # state facts are left-anchored, the contextual hint right-anchored,
        # and the transient log takes the middle — truncated when the
        # terminal is narrow. The finished line is clipped to the terminal
        # width as a final safety.
        sep = "   ·   "
        width = max(self.size.width, 10)
        log = self._log
        budget = width - len(state) - len(hint) - 2 * len(sep)
        if log and budget >= 2:
            if len(log) > budget:
                log = log[: budget - 1] + "…"
            line = f"{state}{sep}{log}{sep}{hint}"
        else:
            line = f"{state}{sep}{hint}"
        self._status_line = line[:width]
        try:
            self.query_one("#logtext", Static).update(self._status_line)
        except Exception:
            pass  # widget not composed yet

    def push_log(self, msg: str) -> None:
        self._set_log(msg)

    def _set_busy(self, busy: bool) -> None:
        """Track engine-worker activity; the spinner mirrors the flag."""
        self._busy = busy
        try:
            spin = self.query_one("#spin", _BusySpinner)
        except Exception:
            return  # widget not composed yet
        if busy:
            spin.start()
        else:
            spin.stop()

    def _show_preview_pending(self) -> None:
        op = self._pending
        assert op is not None
        # Show the preview's result in the grid so the operator can inspect the
        # secondary suppressions before deciding (design §B.5: "the operator
        # inspects its secondary suppressions"). The preview worker already
        # built this table's grid (it computed the status delta from it), so
        # this is a pure cache read, not an engine call.
        if self._preview is not None:
            self._grid = self._preview.grid(op.tab - 1)
            self._repopulate_preserving_cursor()
        self._detail = (f"preview {op.kind} on table {op.tab}:\n"
                        f"{self._delta}\n"
                        f"y=confirm (adopt this run)   n=cancel")
        self.query_one("#detail", Static).update(self._detail)
        self._set_log(f"table {op.tab}: {op.kind} previewed — confirm? (y/n)")


def main(argv: Optional[List[str]] = None) -> int:
    """Console entry point: ``tauargus-tui [batch.arb | x.workspace.json]``
    (browse if omitted). A ``.workspace.json`` reopens a saved session
    (design §B.7)."""
    p = argparse.ArgumentParser(
        prog="tauargus-tui",
        description="Tau-Argus terminal UI over the pytauargus engine")
    p.add_argument("path", nargs="?", default=None,
                   help="path to a .arb batch file or a saved "
                        ".workspace.json (omit to browse)")
    args = p.parse_args(argv)
    path = Path(args.path) if args.path else None
    if path is not None and path.name.endswith(".workspace.json"):
        app = TauArgusApp(workspace_path=path)
    else:
        app = TauArgusApp(path)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
