# pytauargus-mcp

A Model Context Protocol (MCP) server for
[Tau-Argus](https://github.com/trygu/tauargus-engine), the statistical
disclosure control engine. It lets an AI assistant (for example
OpenCode, or any other MCP client) work on your data: you point it at a
microdata file, it explores the variables, protects the tables and
reports what was suppressed and why. Each tool call is one
self-contained task with its full result, so a long solve can be
backgrounded by the client. It is a thin wrapper over the `pytauargus`
Python API; it re-implements no engine behaviour. Installing it also
gives you the `pytauargus` package and the `tauargus` CLI.

The server runs locally on stdio: the microdata, the protection run
and the results stay on the machine that hosts it, and the server makes
no network calls. You can run it air-gapped, for example in a datacenter
with an MCP client backed by a local LLM.

## What it does

Nine tools cover the workflow.

- **`tauargus_version`** reports the `pytauargus` package version and the
  native engine version.
- **`tauargus_explore`** inspects a microdata dataset and returns its
  variables with their types. It reads `.parquet` files directly and
  `.asc`/`.rda` files through the engine metadata parser.
- **`tauargus_build_arb`** writes a `.arb` batch file from the dataset
  description (wraps `micro_arb`).
- **`tauargus_run_arb`** runs a `.arb` batch and reports the table files
  it writes.
- **`tauargus_protect`** protects one or more tables from microdata in a
  single call (wraps `protect`). The suppression method is selectable
  through the `suppress` spec (default `OPT(1)`); hypercube (`GH`)
  secondary suppression runs in this build for flat, non-weighted
  tables. It returns per-table cell counts, status counts, the unsafe
  and secondary cells, and all produced file paths.
- **`tauargus_audit`** audits the suppressed cells of a table computed
  by a batch and returns their realised feasibility intervals.
- **`tauargus_report`** writes up this session as a markdown report:
  the datasets explored, the method advice given, the assessments
  (which cells the rules flag and why), the protection runs with their
  rules and method in plain words and the cells withheld, the audit
  intervals of the suppressed cells, and a publish or hold verdict per
  run plus an overall one. Call it at the end of the work; pass
  `path` to also save the report to a file.
- **`tauargus_method_advice`** recommends a protection method for the
  table structure (dimensions, hierarchy, count response) and explains
  the trade-offs of the applicable alternatives. Each method is marked
  `available` when this build implements it; a method the engine cannot
  run is never recommended, and the result says so. This build marks
  global recoding, optimal, modular (HiTaS), hypercube (flat tables)
  and controlled rounding as available; network and CTA are not.
- **`tauargus_assess`** runs the safety rules without suppressing and
  returns the cells the rules flag, with their values, contributor
  counts and the plain-language reason each is sensitive (safety rule,
  frequency, singleton, zero, manual, ...), plus the applied rules in
  plain words. It previews what a protection run would withhold, so it
  runs first.

The server runs on the stdio transport.

## Basis

The method advice, rule meanings and status legend describe standard
statistical disclosure control practice as documented in the legacy
tau-ARGUS 4.1 manual. The method trade-offs follow the overview in
Meindl (2025), *Statistical Disclosure Control. Improving tools and
methods to facilitate the application*, doctoral thesis, Johannes
Kepler University Linz.

## Install

Python 3.11 to 3.14, on the platforms the `pytauargus` wheel covers
(macOS arm64, Linux x86_64 and aarch64, Windows x86_64). The wheel
bundles the native engine and HiGHS, so no compiler, CMake, R or Java
is needed.

```bash
pip install pytauargus-mcp
# or, as an extra of the engine package:
pip install "pytauargus[mcp]"
```

To try it without installing anything (requires uv), run it with
`uvx`:

```bash
uvx pytauargus-mcp
```

## Run

```bash
tauargus-mcp   # starts the server on stdio
```

The server is a local process that the client starts for you. Point the
client at the `tauargus-mcp` console script. With a pip install it is on
the `PATH`; without one, use `uvx pytauargus-mcp` instead.

**OpenCode.** Add a `tauargus` server to the project or user config:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "tauargus": {
        "type": "local",
        "command": ["uvx", "pytauargus-mcp"]
      }
    }
  }
}
```

**Claude Desktop.** Add a `tauargus` entry to `claude_desktop_config.json`
(the app opens it from Settings, Developer, Edit Config) and restart it:

```json
{
  "mcpServers": {
    "tauargus": { "command": "tauargus-mcp" }
  }
}
```

Desktop starts the command without your shell environment; if it does not
find the script, use its absolute path (`which tauargus-mcp`).

**Claude Code.**

```bash
claude mcp add tauargus -- tauargus-mcp
```

**Gemini CLI.**

```bash
gemini mcp add -s user tauargus tauargus-mcp
```

or add the same `mcpServers` entry to `~/.gemini/settings.json`.

**ChatGPT.** Its custom connectors connect to a remote server over
HTTPS and cannot start a local one, so this server does not work with
ChatGPT.

When the server connects, the client lists its eight tools. In OpenCode,
`/mcps` shows `✓ tauargus connected`.

## Develop (monorepo)

```bash
cd mcp
uv sync --extra test
.venv/bin/python -m pytest tests -q
```

The `uv sync` step installs the parent package editable and builds its
native extension, so the pinned engine checkout's superbuild output
(`engine/build/lib`) must exist first. `scripts/sync-venv.sh` builds it
if needed (it reads the sibling `../tauargus-engine` checkout; point
`TAUARGUS_ENGINE_DIR` at it for the mcp venv too, so the editable build
uses the same checkout). The tests read their fixtures from
`$TAUARGUS_ENGINE_DIR/data` (default: the sibling checkout).
