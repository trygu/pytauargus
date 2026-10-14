#!/usr/bin/env bash
# Sync a Python venv with the pinned engine: engine -> wheel -> venv.
#
# Run this after ANY change to the package (or after bumping the pin) so
# the fix lands in the venv:
#
#   scripts/sync-venv.sh .venv
#
# The venv must run a locally built wheel, never the PyPI one: PyPI lags
# behind in-tree fixes by a full release cycle.
#
# The engine is read from the sibling ../tauargus-engine checkout, which
# must be at the pinned revision (the tauargus-engine submodule gitlink in
# this repo is the pin) with submodules in sync. Point TAUARGUS_ENGINE_DIR
# at a different checkout (e.g. the submodule dir) for local engine work;
# TAUARGUS_ALLOW_ENGINE_DRIFT=1 skips the pin gate.
set -euo pipefail
cd "$(dirname "$0")/.."

VENV="${1:-}"
if [[ -z "$VENV" || ! -x "$VENV/bin/python" ]]; then
    echo "usage: scripts/sync-venv.sh <path-to-venv>" >&2
    exit 2
fi
VENV="$(cd "$VENV" && pwd)"
VENV_PY="$VENV/bin/python"

REPO="$(pwd)"
ENGINE="${TAUARGUS_ENGINE_DIR:-$(cd "$REPO/.." && pwd)/tauargus-engine}"
PIN="$(git -C "$REPO" ls-tree HEAD tauargus-engine | awk '{print $3}')"
HEAD="$(git -C "$ENGINE" rev-parse HEAD)"
if [[ -z "${TAUARGUS_ALLOW_ENGINE_DRIFT:-}" && "$HEAD" != "$PIN" ]]; then
    echo "engine checkout at $HEAD, pin is $PIN" >&2
    echo "sync with: git -C \"$ENGINE\" checkout $PIN" >&2
    echo "            git -C \"$ENGINE\" submodule update --init --recursive" >&2
    echo "(override: TAUARGUS_ALLOW_ENGINE_DRIFT=1)" >&2
    exit 1
fi
drift="$(git -C "$ENGINE" submodule status | grep '^+' || true)"
if [[ -n "$drift" ]]; then
    echo "submodule worktree drift in $ENGINE (worktree != recorded pin):" >&2
    echo "$drift" >&2
    echo "restore with: git -C \"$ENGINE\" submodule update --checkout" >&2
    exit 1
fi

JOBS="$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)"
HIGHS_DIR="${HIGHS_DIR:-$(brew --prefix)/lib/cmake/Highs}"

# 1) Release engine (in the pinned checkout).
cmake -S "$ENGINE/engine" -B "$ENGINE/engine/build" -DHighs_DIR="$HIGHS_DIR" > /dev/null
cmake --build "$ENGINE/engine/build" --parallel "$JOBS" > /dev/null

# 2) Wheel matching the venv's Python (uv build runs in the package dir;
# the freshest wheel in dist/ is the one just built).
if ! WHEEL="$(uv build --wheel --python "$VENV_PY" -q && ls -t dist/*.whl | head -n1)"; then
    echo "wheel build failed (see: uv build --wheel --python \"$VENV_PY\")" >&2
    exit 1
fi

# 3) Force-install into the venv (never touch its other packages).
uv pip install --python "$VENV_PY" --force-reinstall --no-deps "$REPO/$WHEEL" > /dev/null

echo "synced: $WHEEL -> $VENV (engine $PIN)"
