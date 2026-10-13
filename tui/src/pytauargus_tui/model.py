"""Pure-Python view model over the ``pytauargus`` engine.

The TUI widgets never import the native bindings directly — they read from
this module only. Every engine call goes through the documented Python API
(``run_batch`` / ``Engine`` methods) exactly as the CLI does, so the TUI is a
thin presentation layer and re-implements no engine behaviour (design §2,
§B.2, §B.4).

One ``Session`` wraps one ``Engine`` (the design's "one Engine per open
session"). The grid caches per-table cell reads after compute and is
invalidated on any mutating action.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

from pytauargus.batch import Apriory, Suppress
from pytauargus.engine import Engine, _status_symbol, run_batch
from pytauargus import cli as _cli

from pytauargus_tui import workspace as _ws

# Full status names (1-14) live in the CLI module (design §B.1 references them).
_STATUS_NAMES: Dict[int, str] = dict(_cli._STATUS_NAMES)

_CS_EMPTY = 14


# ===========================================================================
# Dataclasses (plain Python — safe for any widget layer)
# ===========================================================================
@dataclass(frozen=True)
class Cell:
    """One grid cell: the published value plus its cell-status code."""

    value: float
    status: int

    @property
    def symbol(self) -> str:
        """Category symbol: S/U/P/M/E (``_status_symbol`` contract)."""
        return _status_symbol(self.status)

    @property
    def status_name(self) -> str:
        return _STATUS_NAMES.get(self.status, "unknown")


@dataclass(frozen=True)
class TableMeta:
    """Navigator row for one specified table."""

    index: int
    exp_vars: Tuple[str, ...]
    resp_var: str
    n_cells: int
    rules: Tuple[str, ...] = ()


@dataclass
class TableGrid:
    """A computed table as a 2-D grid (rows x cols of :class:`Cell`).

    For a two-explanatory-var table, ``rows`` = first var's codes and
    ``cols`` = second var's codes. For a one-var table there is a single
    column (``cols == []``). For three or more vars the grid falls back to a
    flat single-column listing (see :meth:`Session.grid`).
    """

    tab: TableMeta
    row_var: str
    col_var: Optional[str]
    rows: List[str]
    cols: List[str]
    cells: List[List[Cell]]


# ===========================================================================
# Draft cell instructions (design §B.4)
# ===========================================================================
@dataclass(frozen=True)
class CellInstruction:
    """One draft cell instruction: a stable cell identity plus an apriori
    action (design §B.4).

    Instructions live in the TUI draft and are applied on the preview
    (scratch) run only — never on the accepted run. ``dim`` holds the ordered
    categorical code labels (as strings, one per explanatory variable) of
    ``table``; ``kind`` is ``U`` (require suppression / manually unsafe),
    ``S`` (override as safe) or ``P`` (retain from secondary suppression).
    Screen coordinates and native indices are display/run details, not the
    identity — the code tuple is what survives navigation and recoding.
    """

    table: int  # 1-based table number
    dim: Tuple[str, ...]  # code labels, one per explanatory variable
    kind: str  # "U" | "S" | "P"


def apriory_lines(instructions: Sequence[CellInstruction],
                  sep: str = ";") -> str:
    """Render one table's cell instructions as apriori file text.

    Each line is ``<code1>;<code2>;...;<cmd>`` — dimension codes then the
    action — the legacy apriori row format read by ``Engine.apply_apriori``
    (legacy source: agent reference §"Apriori file", ``_parse_apriory``).
    """
    lines = [sep.join(list(instr.dim) + [instr.kind]) for instr in instructions]
    return "\n".join(lines) + ("\n" if lines else "")


# ===========================================================================
# Draft edit history — undo/redo (design §B.4 / §B.5)
# ===========================================================================
@dataclass(frozen=True)
class CellEdit:
    """One undoable draft edit: a cell's before/after instruction kind.

    ``key`` is the stable cell identity (0-based table index, ordered code
    tuple) — the same key the draft's pending-instruction map uses. ``before``
    and ``after`` are the cell's instruction kind (``"U"``/``"S"``/``"P"``) or
    ``None`` when the cell is unmarked. Undo restores ``before``; redo
    re-applies ``after``. Each is a semantic edit and advances the draft
    revision (the caller observes that).
    """

    key: Tuple[int, Tuple[str, ...]]
    before: Optional[str]
    after: Optional[str]


class EditHistory:
    """Undo/redo over draft cell-instruction edits (design §B.4/§B.5).

    Pure state: two command stacks. A fresh edit is recorded on the undo stack
    and clears the redo branch (a new edit makes later redos stale). ``undo``
    / ``redo`` pop a command and return it so the caller applies the
    appropriate state. The revision and preview-eligibility effects (each undo
    or redo is a semantic edit that advances the draft revision, invalidating
    any preview's eligibility) are the caller's to observe.
    """

    def __init__(self) -> None:
        self._undo: List[CellEdit] = []
        self._redo: List[CellEdit] = []

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def record(self, key: Tuple[int, Tuple[str, ...]],
               before: Optional[str], after: Optional[str]) -> None:
        """Record a fresh edit (it becomes the top of the undo stack)."""
        self._undo.append(CellEdit(key=key, before=before, after=after))
        self._redo.clear()

    def undo(self) -> Optional[CellEdit]:
        """Return the last edit (the caller applies its ``before``)."""
        if not self._undo:
            return None
        cmd = self._undo.pop()
        self._redo.append(cmd)
        return cmd

    def redo(self) -> Optional[CellEdit]:
        """Return the last undone edit (the caller applies its ``after``)."""
        if not self._redo:
            return None
        cmd = self._redo.pop()
        self._undo.append(cmd)
        return cmd

    def reset(self) -> None:
        """Drop all history (e.g. when a preview is accepted and the draft is
        committed, starting a fresh draft cycle)."""
        self._undo.clear()
        self._redo.clear()


# ===========================================================================
# Staged suppression (design §B.3)
# ===========================================================================
@dataclass(frozen=True)
class SuppressionOp:
    """One staged suppression, materialised as a batch ``<SUPPRESS>`` line.

    The batch grammar is the legacy source of truth (``batch.py``):
    ``OPT(n)``, ``MOD(n)``, ``RND(n,base)`` — table numbers are 1-based.
    """

    kind: str  # "OPT" | "MOD" | "RND"
    tab: int  # 1-based table number
    rnd_base: int = 0

    def arb_line(self) -> str:
        if self.kind == "RND":
            return f"<SUPPRESS> RND({self.tab},{self.rnd_base})"
        return f"<SUPPRESS> {self.kind}({self.tab})"

    def suppress_cmd(self) -> Suppress:
        """The equivalent ``pytauargus.batch.Suppress`` command.

        The preview applies the method through the engine API on a scratch
        run (design §B.6) rather than by rewriting the spec text; this is the
        same command the batch parser would build for ``arb_line()``.
        """
        if self.kind == "RND":
            return Suppress(kind="RND", tab_no=self.tab, rnd_base=self.rnd_base)
        return Suppress(kind=self.kind, tab_no=self.tab)


def arb_with_ops(arb_text: str, ops: Sequence[SuppressionOp]) -> str:
    """The batch text with one ``<SUPPRESS>`` line per op.

    Suppression is inserted before the first ``<WRITETABLE>`` (the legacy
    order: suppress, then write) or appended when the spec has no
    ``<WRITETABLE>``.
    """
    lines = arb_text.splitlines()
    insert_at = len(lines)
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("<WRITETABLE>"):
            insert_at = i
            break
    extra = [op.arb_line() for op in ops]
    return "\n".join(lines[:insert_at] + extra + lines[insert_at:]) + "\n"


def replay_batch(arb_path: Union[str, Path],
                 ops: Sequence[SuppressionOp],
                 instructions: Sequence[CellInstruction]) -> Path:
    """Materialise the accepted recipe as a headless-runnable batch
    (design §B.7: "generated .arb + apriori files should replay the recorded
    operator intent through the headless engine").

    Writes ``<stem>.replay.arb`` next to the input: the original batch with
    one ``<APRIORI>`` line per table of cell instructions (each pointing at a
    sibling ``<stem>.tab<n>.apri`` file) and one ``<SUPPRESS>`` line per op.
    ``tauargus run <stem>.replay.arb`` then reproduces the recorded intent —
    without promising identical optimiser tie-breaking, and without
    substituting for the saved accepted result.
    """
    arb_path = Path(arb_path)
    lines = arb_path.read_text().splitlines()
    insert_at = len(lines)
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("<WRITETABLE>"):
            insert_at = i
            break
    extra: List[str] = []
    by_table: Dict[int, List[CellInstruction]] = {}
    for instr in instructions:
        by_table.setdefault(instr.table, []).append(instr)
    for tab_no in sorted(by_table):
        name = f"{arb_path.stem}.tab{tab_no}.apri"
        (arb_path.parent / name).write_text(apriory_lines(by_table[tab_no]))
        # Full legacy form with explicit flags: strict (ignore_error=0, the
        # TUI preview applies marks with errors surfaced) and no
        # expand-trivial.
        extra.append(f'<APRIORI> "{name}",{tab_no},";",0,0')
    extra.extend(op.arb_line() for op in ops)
    replay = arb_path.with_name(arb_path.stem + ".replay.arb")
    replay.write_text("\n".join(lines[:insert_at] + extra
                                 + lines[insert_at:]) + "\n")
    return replay


def format_status_delta(before: Dict[int, int],
                        after: Dict[int, int]) -> str:
    """Per-status ``name before→after`` pairs, ascending status code.

    The delta the preview shows (design §B.3 step 3), e.g.
    ``safe 104→101  secondary_unsafe 0→3``.
    """
    codes = sorted(set(before) | set(after))
    return "  ".join(
        f"{_STATUS_NAMES.get(c, str(c))} {before.get(c, 0)}→{after.get(c, 0)}"
        for c in codes
    )


# ===========================================================================
# Safety-rule display
# ===========================================================================
def _rules_display(srs) -> Tuple[str, ...]:
    """Render a ``SafetyRuleSet`` as short human-readable rule strings.

    Mirrors the batch tokens (``NK(n,k)``, ``P(p,q,n)``) so the navigator can
    show the same rules the ``.arb`` declared.
    """
    out: List[str] = []
    for n, k in zip(srs.dom_n, srs.dom_k):
        out.append(f"NK({n},{k})")
    for p, q, n in zip(srs.pq_p, srs.pq_q, srs.pq_n):
        if q == 100 and p == 0 and n == 0:
            out.append("P(0,0,0)")
        else:
            out.append(f"P({p},{q},{n})")
    for f, m in zip(srs.min_freq, srs.freq_marge):
        out.append(f"FREQ({f},{m})")
    if srs.zero_rule:
        out.append(f"ZERO({srs.zero_range:g})")
    if srs.apply_weight:
        out.append("WGT")
    if srs.manual_perc:
        out.append(f"MAN({srs.manual_perc})")
    return tuple(out)


# ===========================================================================
# Session
# ===========================================================================
class Session:
    """One open Tau-Argus session: a view over a single computed ``Engine``."""

    def __init__(self, eng: Engine,
                 arb_path: Optional[Union[str, Path]] = None) -> None:
        self._eng = eng
        self._arb_path = Path(arb_path) if arb_path else None
        self._grid_cache: Dict[int, TableGrid] = {}
        self._audit_rows: Dict[int, List[dict]] = {}

    # -- construction --------------------------------------------------------
    @classmethod
    def from_arb(cls, arb_path: Union[str, Path]) -> "Session":
        """Run a ``.arb`` batch in a fresh engine and wrap it."""
        return cls(run_batch(Path(arb_path)), arb_path)

    @classmethod
    def from_workspace(cls, ws_path: Union[str, Path]) -> "Session":
        """Rebuild the working session from a saved workspace (design §B.7).

        The saved outcome is not loaded from disk: the recorded recipe is
        re-run on a fresh engine — the original spec, then the confirmed
        cell instructions (apriori), then the confirmed method ops — which
        is the design's "rebuild native execution state when needed". A
        replayable recipe does not promise identical optimiser tie-breaking.
        Raises :class:`workspace.WorkspaceError` for an incompatible
        manifest version and :class:`workspace.WorkspaceStale` when the
        input is missing or changed (explicit reconciliation required).
        """
        m = _ws.load_workspace(ws_path)
        if m.version != _ws.WORKSPACE_VERSION:
            raise _ws.WorkspaceError(
                f"unsupported workspace version {m.version} "
                f"(this build reads {_ws.WORKSPACE_VERSION})")
        if _ws.input_changed(m, m.arb):
            raise _ws.WorkspaceStale(m.arb)
        base = cls.from_arb(m.arb)
        if not m.confirmed_ops and not m.confirmed_instructions:
            return base  # empty recipe: the bare spec session
        ops = [SuppressionOp(kind=o.kind, tab=o.tab, rnd_base=o.rnd_base)
               for o in m.confirmed_ops]
        instrs = [CellInstruction(table=i.table, dim=i.dim, kind=i.kind)
                  for i in m.confirmed_instructions]
        return base.preview(ops, instrs)

    @property
    def engine(self) -> Engine:
        return self._eng

    @property
    def arb_path(self) -> Optional[Path]:
        """The ``.arb`` this session is bound to (None when constructed
        ad hoc). Re-previewing and workspace save/restore use it."""
        return self._arb_path

    # -- navigator -----------------------------------------------------------
    @property
    def n_tables(self) -> int:
        return self._eng._n_tables

    def table(self, i: int) -> TableMeta:
        eng = self._eng
        if i < 0 or i >= len(eng._tables):
            raise IndexError(f"table {i} out of range")
        spec, srs = eng._tables[i]
        n, _ = eng._tau.get_total_table_size(i)
        return TableMeta(
            index=i,
            exp_vars=tuple(spec.exp_vars),
            resp_var=spec.resp_var,
            n_cells=n,
            rules=_rules_display(srs),
        )

    def tables(self) -> List[TableMeta]:
        return [self.table(i) for i in range(self.n_tables)]

    # -- grid ----------------------------------------------------------------
    def grid(self, i: int) -> TableGrid:
        """Return (and cache) the computed grid for table ``i``."""
        cached = self._grid_cache.get(i)
        if cached is not None:
            return cached
        grid = self._build_grid(i)
        self._grid_cache[i] = grid
        return grid

    def _codes_for(self, var_name: str) -> List[Tuple[int, str]]:
        """Active code index + display label for a categorical variable."""
        eng = self._eng
        vi = eng.metadata.index_of(var_name)
        var = eng.metadata.variables[vi]
        _n_codes, n_active = eng._tau.get_var_number_of_codes(vi)
        out: List[Tuple[int, str]] = []
        for ci in range(n_active):
            ok, _ct, cs, _miss, _level = eng._tau.get_var_code(vi, ci)
            if not ok or cs == "":
                cs = var.tot_code if var.tot_code else "Total"
            out.append((ci, cs))
        return out

    def _cell(self, i: int, dim: List[int]) -> Cell:
        eng = self._eng
        res = eng._tau.get_table_cell(i, dim, 0)
        if not res or len(res) < 11:
            return Cell(value=0.0, status=_CS_EMPTY)
        return Cell(value=float(res[1]), status=int(res[10]))

    def _build_grid(self, i: int) -> TableGrid:
        meta = self.table(i)
        exp = list(meta.exp_vars)
        dims_codes = [self._codes_for(name) for name in exp]

        if len(exp) >= 2:
            row_var, col_var = exp[0], exp[1]
            rows = [label for _ci, label in dims_codes[0]]
            cols = [label for _ci, label in dims_codes[1]]
            cells: List[List[Cell]] = []
            for r, (rci, _rl) in enumerate(dims_codes[0]):
                row_cells: List[Cell] = []
                for cci, _cl in dims_codes[1]:
                    row_cells.append(self._cell(i, [rci, cci]))
                cells.append(row_cells)
        else:
            # Single explanatory var: one column.
            row_var = exp[0] if exp else ""
            col_var = None
            rows = [label for _ci, label in dims_codes[0]] if exp else [""]
            cells = [
                [self._cell(i, [rci])] for rci, _label in dims_codes[0]
            ] if exp else [[self._cell(i, [])]]
        return TableGrid(
            tab=meta,
            row_var=row_var,
            col_var=col_var,
            rows=rows,
            cols=cols if col_var is not None else [],
            cells=cells,
        )

    # -- cache invalidation --------------------------------------------------
    def invalidate(self) -> None:
        """Drop cached grids after any mutation of the running tables."""
        self._grid_cache.clear()

    # -- per-status cell counts (preview delta, design §B.3) ------------------
    def status_counts(self, i: int) -> Dict[int, int]:
        """Count of cells per status code for table ``i`` (via the grid)."""
        counts: Dict[int, int] = {}
        for row in self.grid(i).cells:
            for cell in row:
                counts[cell.status] = counts.get(cell.status, 0) + 1
        return counts

    def is_total(self, i: int, dim: Sequence[str]) -> bool:
        """True if any code in ``dim`` is its variable's total code.

        Total (marginal) cells are shown in the grid but are not valid
        apriori targets in this iteration: the grid shows the display label
        (``get_total_code()``, which falls back to ``"Total"``) while the
        apriori parser matches only the variable's real total code. Marking a
        total cell is a refinement (it needs the real total code, which the
        display label does not preserve).
        """
        meta = self.table(i)
        for var_name, label in zip(meta.exp_vars, dim):
            var = self._eng.metadata.variables[
                self._eng.metadata.index_of(var_name)]
            if label == var.get_total_code():
                return True
        return False

    # -- throwaway preview (design §B.3) --------------------------------------
    def adopt_spec(self, arb_path: Union[str, Path]) -> None:
        """Re-bind this session to a spec file so later previews can re-run
        it. Called when a previewed session is adopted as the working
        session: the preview's own temp spec is gone, but the original
        ``.arb`` plus the app's confirmed ops re-materialize the same state
        (design §B.3 step 4)."""
        self._arb_path = Path(arb_path)

    def preview(self, ops: Sequence[SuppressionOp],
                instructions: Sequence[CellInstruction] = ()) -> "Session":
        """Fresh, throwaway run of this session's ``.arb`` (design §B.3/§B.6).

        The execution recipe is reconstructed on a scratch engine: the
        original preparation/compute spec runs first, then the draft's cell
        instructions (apriori) are applied, then the chosen method ops
        (confirmed + staged) run. The engine is stateful with no in-place
        undo, so a "preview" is a fresh ``run_batch`` — the same mechanism the
        CLI uses. The returned session is for inspection/diffing only; it is
        not meant to be re-previewed.
        """
        if self._arb_path is None or not self._arb_path.is_file():
            raise RuntimeError("this session has no .arb file to re-run")
        eng = run_batch(self._arb_path)
        # Apply the draft's cell instructions before solving (design §B.6),
        # one apriori file per table. The apriori path resolves relative to
        # the arb's directory, so the temp file is written there.
        by_table: Dict[int, List[CellInstruction]] = {}
        for instr in instructions:
            by_table.setdefault(instr.table, []).append(instr)
        for tab_no in sorted(by_table):
            fd, tmp = tempfile.mkstemp(prefix=".apri-", suffix=".apri",
                                        dir=str(self._arb_path.parent))
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(apriory_lines(by_table[tab_no]))
                # ignore_error=False: a bad code/command is a real problem
                # (e.g. an edit invalidated by recoding) - surface it rather
                # than silently retarget (design §B.4/§B.8).
                eng.apply_apriori(Apriory(file=tmp, tab_no=tab_no,
                                           separator=";", ignore_error=False))
            finally:
                Path(tmp).unlink(missing_ok=True)
        # Then apply the method ops (confirmed first, staged last).
        for op in ops:
            eng.suppress(op.suppress_cmd())
        return type(self)(eng, self._arb_path)

    # -- pass-through actions (mutate -> invalidate) -------------------------
    def _invalidate_audit(self) -> None:
        """Drop cached audit rows after a mutation (design §B.5: audit is
        owned by the result it was computed on, so a changed result has no
        stale audit). Called by in-place mutations, not by ``audit`` itself."""
        self._audit_rows.clear()

    def suppress(self, cmd) -> None:
        self._eng.suppress(cmd)
        self.invalidate()
        self._invalidate_audit()

    def audit(self, i: int) -> List[dict]:
        rows = self._eng.audit(i)
        self._audit_rows[i] = rows
        self.invalidate()
        return rows

    def audit_rows(self, i: int) -> List[dict]:
        """Rows returned by the last audit of table ``i`` (empty if none)."""
        return self._audit_rows.get(i, [])

    def apply_apriori(self, cmd) -> Dict[str, int]:
        res = self._eng.apply_apriori(cmd)
        self.invalidate()
        self._invalidate_audit()
        return res

    def write_intermediate_table(self, i: int, path: str, **kw) -> None:
        self._eng.write_intermediate_table(i, path, **kw)
