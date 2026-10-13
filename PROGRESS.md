# pytauargus: Progress

Branches: `master` (mainline) + short-lived feature branches for fixes.

## In flight

- [ ] Repo split (finish): first `py-v*` release tag to verify
  wheels.yml end to end (wheels, GitHub Release, PyPI, GHCR); set up
  PyPI trusted publishers + `PYPI_ENABLED` variable on this repo;
  repoint the PyPI project URLs of `pytauargus` and `pytauargus-tui`
  to this repo.

## Verified

- [x] Populated from `tauargus-engine`: package, CLI, TUI, notebooks,
  docs, package tests, release workflow; engine pinned at `bc40c89`;
  wheel build + 273 package tests verified locally (macOS arm64).
- [x] tests CI green on GitHub (ubuntu, ubuntu-arm, macos): pinned
  engine checkout, HiGHS v1.15.1, engine build, package suite.
