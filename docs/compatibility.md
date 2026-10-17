# Compatibility and validation

[README](../README.md) · [Python API](python-api.md) · [Batch files and CLI](batch-files.md)

- **OPT, MOD and RND** are implemented and regression-tested.
- **CKM** has a dispatch path, but no dedicated end-to-end CKM regression suite.
- **GH, NET and CTA** are rejected by the headless engine, even though the
  parser/generators accept their legacy syntax.
- **Suppression audit** runs in-process and has known-interval contract tests.

Regression tests do not establish comprehensive numerical equivalence with
original Tau-Argus. Compare protection requirements, realised audit intervals,
information loss and failure behaviour on representative tables before adopting
a workflow; solver changes can produce different valid suppression patterns.
Audit is explicit: `engine.audit(tab)` checks the supplied table's suppression
constraints. `TableResult.safe()` masks cells; it does not run the audit.

Run independent jobs in separate processes and working/output directories.
The native solver retains process-global state, and concurrent engine calls in
the same process are not validated. OPT/MOD limits are in minutes (`0`: no
limit); the positive-limit tests do not cover deadline expiry or partial results.

See [Compatibility and validation](https://github.com/trygu/tauargus-engine#compatibility-and-validation)
for the project scope and the distinction between diagnostics and publication.

## Results compared with legacy Tau-Argus

The port targets legacy Tau-Argus 4.1 batch grammar, file I/O and protection
mathematics for supported workflows. This is a compatibility target, not a
guarantee of complete feature coverage or numerical equivalence. Two
important differences:

- **Secondary suppression can differ cell by cell.** OPT and MOD
  solve an integer program. When several suppression patterns are
  near-optimal, the legacy CPLEX backend and the HiGHS backend can
  pick different ones. In the sy1 reference batch the two runs differ in 3 of
  1654 cells (0.2%), the suppressed weight differs by 9 units (0.09%)
  and the secondary response by 149777 (0.0016%), and the audit
  reports 0 unsafe cells for both patterns. Primary suppressions
  (apriori and manual) are deterministic and match exactly.
- **WRITETABLE types 6 (JJ) and 7 (CKM) are not implemented.** The
  legacy writers for these formats were GUI-only Java with no native
  equivalent, so the command fails with a clear error. Types 1 to 5
  follow the legacy argument mapping. In 0.5.4 and earlier, types 2
  to 4 were written with a single argument set that did not match
  legacy (suppressed cells masked as `x`, empty rows written); that
  was fixed in 0.5.5.

## Protection regression tests

`uv run pytest tests/test_protection_e2e.py` runs OPT and MOD on two cases.
The `ex1` tabular case checks the legacy 20% manual protection margin,
confirms that the original cell is recoverable before secondary suppression,
and checks its realised lower and upper protection after suppression.
The microdata case adapts the basic dominance example from
[GaussSuppression's magnitude-table vignette](https://statisticsnorway.github.io/ssb-gausssuppression/articles/Magnitude_table_suppression.html),
by Statistics Norway. Contributions are scaled by ten to give integer input;
each row is treated as an independent contribution, without company IDs or
hierarchies. It checks the four cells selected by the 80% dominance rule.

Both cases run the feasibility audit and read back a CSV containing only
dimensions and masked publication values. Primary and secondary suppressed
values must be masked, and unmasked values must be retained. The tests do
not require a particular secondary suppression pattern and do not compare
results against the Gauss algorithm. They cover these two examples, not
singleton knowledge, linked-table disclosure or general statistical safety.

Run test commands from the repository root. See [development](development.md)
for the source-build setup.
