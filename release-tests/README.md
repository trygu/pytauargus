# Installed-distribution tests

This directory is the test resource for the installed-artifact jobs in
`.github/workflows/wheels.yml`. It is uploaded as the `py-install-tests`
artifact by the `test-resource` job and consumed by the `install-*`
jobs, which never check out the product repo.

Layout:

- `conftest.py` - bootstrap. Unlike `tests/conftest.py` it never puts
  the source tree on `sys.path`: `import pytauargus` must resolve to
  the installed distribution.
- `fixture/` - the portable ex1 fixture (copied from the engine repo's
  `data/examples/ex1/`): a 3x3 table with totals, one manually unsafe
  cell, relative paths only.
- `expectations/ex1.expected` - hand-derived cell keys, response
  values, statuses, the manual primary suppression and its required
  protection. Shared with the R installed tests; not generated from a
  run of the code under test.
- `test_installed.py` - identity checks: distribution versions,
  engine SHA and HiGHS version against the build manifest, module
  locations, CLI and TUI entry points in subprocesses.
- `test_arb_job.py` - the complete `.arb` job through the public API
  (OPT and MOD), validated against the expectations; the protection
  audit is the native `Engine.audit`, run on the actual result.
- `test_pyarrow_coexist.py` - pyarrow plus the native package in one
  process, both import orders, normal termination required.
- `existing/` - curated existing tests that are self-contained
  (no engine-repo data, no source-tree imports). The `test-resource`
  job fills this directory from `tests/` of the commit under test;
  for a local run, copy the same files here.

Environment contract (set by the workflow from the build manifests,
or set manually for a local run):

- `TAUARGUS_TEST_VENV` - the virtual environment the wheels were
  installed into; module locations must be inside it.
- `TAUARGUS_EXPECTED_VERSION` - product version to assert.
- `TAUARGUS_EXPECTED_TUI_VERSION` - TUI version to assert.
- `TAUARGUS_EXPECTED_ENGINE_SHA` - pinned engine commit the wheels
  were built from.
- `TAUARGUS_EXPECTED_HIGHS` - pinned HiGHS version (e.g. v1.15.1).

Local run (after copying the curated tests into `existing/`):

    TAUARGUS_TEST_VENV=/path/to/venv \
    TAUARGUS_EXPECTED_VERSION=0.5.7 \
    TAUARGUS_EXPECTED_TUI_VERSION=0.5.7 \
    TAUARGUS_EXPECTED_ENGINE_SHA=<full sha> \
    TAUARGUS_EXPECTED_HIGHS=v1.15.1 \
    /path/to/venv/bin/python -m pytest release-tests -q -rs
