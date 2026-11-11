# pytauargus-mcp

A Model Context Protocol (MCP) server for
[Tau-Argus](https://github.com/trygu/tauargus-engine), the statistical
disclosure control engine. It is the agent front-end of the D2.6
architecture: an LLM or agent (for example OpenCode) drives the stateless
engine over MCP, one tool call per task, with no server-side session.
The `.arb` batch file is the state carrier of the batch flow. It is a
thin wrapper over the `pytauargus` Python API; it re-implements no engine
behaviour. Installing it also gives you the `pytauargus` package.

## What it does

This is the first slice: the core loop, six tools.

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

Each tool call carries its inputs and returns a full result, so long
solves can be backgrounded by the client. The server runs on the stdio
transport.

Later slices add the expertise layer (MCP prompts for the standard SDC
procedures, `tauargus://doc/...` resources pointing at the 4.1 manual
and the D2.6 vision) and the plain-language reporting contract.

## Install

```bash
pip install pytauargus-mcp
# or, as an extra of the engine package:
pip install "pytauargus[mcp]"
```

## Run

```bash
tauargus-mcp   # starts the server on stdio
```

Point an MCP client at the console script, for example:

```json
{
  "mcpServers": {
    "tauargus": { "command": "tauargus-mcp" }
  }
}
```

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
