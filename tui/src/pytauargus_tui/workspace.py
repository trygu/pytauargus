"""Versioned TUI workspace manifest (design §B.7).

The workspace persists the *logical* draft state and accepted-result
references as a small JSON manifest. It never pickles a native engine or a
memory address: it records the recipe (cell instructions + method ops), the
input fingerprint, the draft/accepted revisions, engine provenance and the
accepted outcome's summary. Native execution state is rebuilt on reopen when
needed — a replayable recipe does not promise identical optimiser
tie-breaking.

Saving works before any preview exists (an empty recipe is a valid
workspace). The write is atomic: a temp file is written and then replaced
with ``os.replace`` so a failed save leaves the previous workspace intact.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple, Union

WORKSPACE_VERSION = 1


class WorkspaceError(Exception):
    """A workspace manifest cannot be used (design §B.7)."""


class WorkspaceStale(WorkspaceError):
    """The input ``.arb`` is missing or differs from the fingerprint recorded
    at save time. The saved outcome must not be treated as current: explicit
    reconciliation is required before reopening restores it (design §B.7)."""


@dataclass(frozen=True)
class InstructionRef:
    """A cell instruction recorded in the manifest (stable cell identity)."""

    table: int  # 1-based table number
    dim: Tuple[str, ...]  # ordered categorical code labels
    kind: str  # "U" | "S" | "P"


@dataclass(frozen=True)
class OpRef:
    """A method op (suppression/rounding) recorded in the manifest recipe."""

    kind: str  # "OPT" | "MOD" | "RND"
    tab: int  # 1-based table number
    rnd_base: int = 0


@dataclass
class WorkspaceManifest:
    """The full logical workspace state (design §B.7)."""

    version: int
    arb: str  # path to the input .arb, as recorded at save time
    input_fingerprint: str  # content hash of the .arb (change detection)
    draft_revision: int
    accepted_revision: int
    confirmed_ops: List[OpRef]
    confirmed_instructions: List[InstructionRef]
    pending_instructions: List[InstructionRef]
    accepted_summary: Dict[str, Any]  # per-table status counts of the outcome
    provenance: Dict[str, str]  # engine/build provenance
    tab: int = 0  # convenience: the table in focus at save time


def fingerprint(path: Union[str, Path]) -> str:
    """A stable content fingerprint (sha256) of the input ``.arb`` file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def provenance() -> Dict[str, str]:
    """Engine/build provenance recorded with the workspace (best effort)."""
    try:
        import pytauargus

        ver = getattr(pytauargus, "__version__", "unknown")
    except Exception:  # pragma: no cover - native import unavailable
        ver = "unknown"
    return {"pytauargus": str(ver)}


def _instr_to_dict(instr) -> dict:
    return {"table": instr.table, "dim": list(instr.dim), "kind": instr.kind}


def _instr_from_dict(d: dict) -> InstructionRef:
    return InstructionRef(table=d["table"], dim=tuple(d["dim"]), kind=d["kind"])


def _op_to_dict(op) -> dict:
    return {"kind": op.kind, "tab": op.tab, "rnd_base": op.rnd_base}


def _op_from_dict(d: dict) -> OpRef:
    return OpRef(kind=d["kind"], tab=d["tab"], rnd_base=d.get("rnd_base", 0))


def _normalize_summary(summary: Dict[str, Any] | None) -> Dict[str, Any]:
    """Coerce the accepted-outcome summary to JSON-stable string keys.

    The summary (``{table: {status_code: count}}``) is display/reference data,
    not re-fed to the engine, so normalising its integer keys to strings keeps
    a save/reopen round-trip exact.
    """
    out: Dict[str, Any] = {}
    for k, v in (summary or {}).items():
        if isinstance(v, dict):
            out[str(k)] = {str(sk): sv for sk, sv in v.items()}
        else:
            out[str(k)] = v
    return out


def build_manifest(
    arb_path: Union[str, Path],
    *,
    draft_revision: int,
    accepted_revision: int,
    confirmed_ops: Sequence = (),
    confirmed_instructions: Sequence = (),
    pending_instructions: Sequence = (),
    accepted_summary: Dict[str, Any] | None = None,
    tab: int = 0,
) -> WorkspaceManifest:
    """Build a manifest from the current logical state (no engine run)."""
    arb_path = Path(arb_path)
    return WorkspaceManifest(
        version=WORKSPACE_VERSION,
        arb=str(arb_path),
        input_fingerprint=fingerprint(arb_path),
        draft_revision=draft_revision,
        accepted_revision=accepted_revision,
        confirmed_ops=[_op_from_dict(_op_to_dict(o)) for o in confirmed_ops],
        confirmed_instructions=[_instr_from_dict(_instr_to_dict(i))
                                for i in confirmed_instructions],
        pending_instructions=[_instr_from_dict(_instr_to_dict(i))
                              for i in pending_instructions],
        accepted_summary=_normalize_summary(accepted_summary),
        provenance=provenance(),
        tab=tab,
    )


def _to_json(m: WorkspaceManifest) -> dict:
    return {
        "version": m.version,
        "arb": m.arb,
        "input_fingerprint": m.input_fingerprint,
        "draft_revision": m.draft_revision,
        "accepted_revision": m.accepted_revision,
        "confirmed_ops": [_op_to_dict(o) for o in m.confirmed_ops],
        "confirmed_instructions": [_instr_to_dict(i)
                                   for i in m.confirmed_instructions],
        "pending_instructions": [_instr_to_dict(i)
                                 for i in m.pending_instructions],
        "accepted_summary": m.accepted_summary,
        "provenance": m.provenance,
        "tab": m.tab,
    }


def save_workspace(manifest: WorkspaceManifest,
                   path: Union[str, Path]) -> Path:
    """Atomically write ``manifest`` to ``path`` (temp file + ``os.replace``).

    A failure at any point leaves any previous manifest at ``path`` intact.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                               dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(_to_json(manifest), f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def load_workspace(path: Union[str, Path]) -> WorkspaceManifest:
    """Read a manifest previously written by :func:`save_workspace`."""
    with open(path) as f:
        d = json.load(f)
    return WorkspaceManifest(
        version=d["version"],
        arb=d["arb"],
        input_fingerprint=d["input_fingerprint"],
        draft_revision=d["draft_revision"],
        accepted_revision=d["accepted_revision"],
        confirmed_ops=[_op_from_dict(o) for o in d["confirmed_ops"]],
        confirmed_instructions=[_instr_from_dict(i)
                                for i in d["confirmed_instructions"]],
        pending_instructions=[_instr_from_dict(i)
                              for i in d["pending_instructions"]],
        accepted_summary=d.get("accepted_summary", {}),
        provenance=d.get("provenance", {}),
        tab=d.get("tab", 0),
    )


def input_changed(manifest: WorkspaceManifest,
                  arb_path: Union[str, Path]) -> bool:
    """True if the input ``.arb`` differs from the fingerprint recorded at
    save time (or is missing) — such a workspace needs explicit
    reconciliation before its saved outcome is treated as current."""
    try:
        return fingerprint(arb_path) != manifest.input_fingerprint
    except OSError:
        return True
