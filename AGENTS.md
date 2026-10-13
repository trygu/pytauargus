# pytauargus: Agent Rules

Python front-end for τ-ARGUS: the `pytauargus` package, the `tauargus`
CLI and the TUI. The native engine (C/C++, HiGHS) and the thin
Python/R bindings live in the sibling repo `tauargus-engine`; the R
front-end is the separate `rtauargus` package.

## 1. Relationship to tauargus-engine

- This repo ships one self-contained wheel per release, bundling the
  bindings, the engine and HiGHS, built from a pinned engine revision
  (a commit in `tauargus-engine`). No separate bindings wheel for now.
- Engine behavior is defined by the legacy τ-ARGUS 4.1 manual, kept in
  `tauargus-engine/docs/tau-argus-4.1-markdown-bundle/`.
- Engine bugs are fixed in `tauargus-engine`. Here you bump the pin,
  never patch engine code.
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
- `uv run pytest` in the package. Before a release, test the installed
  wheel on the supported platforms.

## 5. Commits and releases

- Conventional Commits with imperative subjects; release commits
  `chore(release): X.Y.Z`. Authored as the user only, no
  `Co-authored-by` trailer.
- Tag-driven releases build wheels, publish a GitHub Release and push
  to PyPI via trusted publishing. PyPI versions are immutable: the tag
  must match the package version.

## 6. Communication and docs

- Always respond in English.
- Plain prose: active voice, short sentences, no promotional
  vocabulary, no em or en dashes in prose.
- User-facing docs state shipped, verified behaviour only.
