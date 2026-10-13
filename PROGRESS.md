# pytauargus: Progress

Branches: `master` (mainline) + short-lived feature branches for fixes.

## In flight

- [ ] Repo split (finish): verify the new repo's CI (tests, wheels) on
  GitHub; PyPI trusted publishers + `PYPI_ENABLED` variable on this
  repo; repoint the PyPI project URLs of `pytauargus` and
  `pytauargus-tui` to this repo.

## Verified

- [x] Populated from `tauargus-engine`: package, CLI, TUI, notebooks,
  docs, package tests, release workflow; engine pinned at `bc40c89`;
  wheel build + 273 package tests verified locally (macOS arm64).
