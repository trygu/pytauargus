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

## What it does

Six tools cover the core workflow.

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
  single call (wraps `protect`). It returns per-table cell counts,
  status counts, the unsafe and secondary cells, and all produced file
  paths.
- **`tauargus_audit`** audits the suppressed cells of a table computed
  by a batch and returns their realised feasibility intervals.

The server runs on the stdio transport.

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

Point an MCP client at the console script. For OpenCode, add a
`tauargus` server to the project or user config:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "tauargus": {
        "type": "local",
        "command": ["tauargus-mcp"]
      }
    }
  }
}
```

With a pip install the console script is on the `PATH`; without one,
point the command at uv instead (`"command": ["uvx", "pytauargus-mcp"]`).

Other MCP clients use the standard `mcpServers` shape:

```json
{
  "mcpServers": {
    "tauargus": { "command": "tauargus-mcp" }
  }
}
```

When the server connects, the client lists its six tools. In OpenCode,
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
