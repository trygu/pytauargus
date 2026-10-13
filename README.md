# pytauargus

Python front-end for the τ-ARGUS statistical disclosure control engine:
the `pytauargus` package, the `tauargus` CLI and the TUI
(`pytauargus-tui`).

The native engine (C/C++, in-process HiGHS) and the thin Python/R
bindings live in the sibling repo
[`tauargus-engine`](https://github.com/trygu/tauargus-engine). The R
front-end is [`rtauargus`](https://github.com/trygu/rtauargus).

## Status

This repo is being populated from `tauargus-engine` as the repository
split proceeds. Until the first release from this repo, install
`pytauargus` and `pytauargus-tui` from PyPI as before; plain
`uvx pytauargus-tui` keeps working.
