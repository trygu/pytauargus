# pytauargus: Agent Rules

Python front-end for τ-ARGUS: the `pytauargus` package, the `tauargus`
CLI and the TUI. The native engine (C/C++, HiGHS) and the thin
Python/R bindings live in the sibling repo `tauargus-engine`; the R
front-end is the separate `rtauargus` package.

## 1. Relationship to tauargus-engine

- This repo ships one self-contained wheel per release, bundling the
  bindings, the engine and HiGHS, built from a pinned engine revision.
  The pin is the commit in `tauargus-engine-rev`; no separate bindings
  wheel for now.
- The wheel build (scikit-build + `CMakeLists.txt`) compiles the
  `_tauargus` extension from the engine checkout's `bindings/python/`
  against that checkout's pre-built `engine/build/lib`. Local dev reads
  the sibling `../tauargus-engine`; CI checks out the pinned revision at
  `./tauargus-engine`. `TAUARGUS_ENGINE_DIR` overrides the checkout
  location (build and tests both honor it).
- Engine behavior is defined by the legacy τ-ARGUS 4.1 manual, kept in
  `tauargus-engine/docs/tau-argus-4.1-markdown-bundle/`.
- Engine bugs are fixed in `tauargus-engine`. Here you bump the pin,
  never patch engine code. The HiGHS version is pinned in the engine
  repo (`engine/HiGHS-VERSION`, the single source of truth); the
  workflows read it from the pinned engine checkout, since the wheel
  bundles HiGHS.
- To advance the pin: the engine change must be committed and pushed in
  `tauargus-engine` first, then update `tauargus-engine-rev` here and
  release.
- The public Python API stays unchanged: this is a drop-in
  replacement for legacy τ-ARGUS, and plain `uvx pytauargus-tui` must
  keep working.

## 2. Context and token preservation

- Redirect verbose test and build output to a log file and inspect
  only the relevant lines. Never print unbounded logs into chat.
- Read at most 60 lines per tool call.
- No speculative debug prints: reproduce, trace, fix the flagged
  site, verify.

## 3. Checkpoint discipline

- Keep `PROGRESS.md` under 35 lines. Move completed items to
  `ARCHIVE.md` immediately.

## 4. Verification

- TDD: write the failing test first, then implement until green.
- Dev loop: `scripts/sync-venv.sh <venv>` (pin gate, engine build, wheel,
  force-install). Then `uv run pytest -q` with `TAUARGUS_ENGINE_DIR`
  pointing at the engine checkout (tests read the `data/` fixtures from
  there; default: the sibling checkout).
- Before a release, test the installed wheel on the supported platforms
  (the wheels workflow's smoke steps do this per OS).

## 5. Commits and releases

- Conventional Commits with imperative subjects; release commits
  `chore(release): X.Y.Z`. Authored as the user only, no
  `Co-authored-by` trailer.
- To release (only when the user asks): bump `version` in `pyproject.toml`
  and `tui/pyproject.toml` in lockstep, `uv lock && uv run pytest` must
  pass, commit `chore(release): X.Y.Z`, push, then
  `git tag py-vX.Y.Z && git push origin py-vX.Y.Z` and watch
  `gh run list --workflow wheels --limit 1`.
- The tag runs `wheels.yml`: per-platform wheels, GitHub Release, PyPI
  via trusted publishing (gated on the `PYPI_ENABLED` repo variable),
  and the GHCR image. PyPI versions are immutable: the workflow fails
  if the tag differs from either package version.
- The container image keeps the name
  `ghcr.io/trygu/tauargus-engine:<version>` (and `:latest`); it is built
  by this repo.

## 6. Communication and docs

- Always respond in English.
- Plain prose: active voice, short sentences, no promotional
  vocabulary, no em or en dashes in prose.
- User-facing docs state shipped, verified behaviour only.
