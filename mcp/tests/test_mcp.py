"""MCP server tool tests (first slice: the core loop).

The pytauargus-mcp package is a separate distribution that wraps the
pytauargus engine behind MCP tools. The tools are plain functions
registered on a FastMCP server, so the tests call them directly without
an MCP transport. ``mcp`` is a hard dependency of this distribution, so
the tests always run in the mcp dev venv.

The native tools run against the engine data of the pinned engine
checkout (``$TAUARGUS_ENGINE_DIR/data``, default the sibling
``../tauargus-engine`` checkout).
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from pytauargus_mcp import server  # noqa: E402

DATA = (
    Path(os.environ.get("TAUARGUS_ENGINE_DIR",
                        str(Path(__file__).resolve().parents[3] / "tauargus-engine")))
    / "data"
)
SRC = Path(__file__).resolve().parents[2] / "src"

# The same small 2 x 2 microdata as test_protect.py: fast for a single
# OPT solve.
MICRO = {
    "Region": ["Os", "Os", "Ws", "Ws"],
    "Size": ["1", "2", "1", "2"],
    "Var2": [10.0, 20.0, 30.0, 40.0],
}


def _files(tmp_path):
    """Write the .asc/.rda/.arb for MICRO via protect(run=False)."""
    from pytauargus.protect import protect
    res = protect(MICRO, ["Region", "Size"], response="Var2",
                  safety_rules="P(25,1)", suppress="OPT(1)",
                  run=False, workdir=str(tmp_path))
    return res.files


# ===========================================================================
# tauargus_version
# ===========================================================================

def test_version_returns_dict():
    out = server.tauargus_version()
    assert set(out) == {"pytauargus", "engine"}
    assert isinstance(out["pytauargus"], str) and out["pytauargus"]
    assert isinstance(out["engine"], str) and out["engine"]


# ===========================================================================
# tauargus_explore
# ===========================================================================

def test_explore_parquet():
    pytest.importorskip("pyarrow")
    out = server.tauargus_explore(
        str(DATA / "examples" / "parquet" / "micro.parquet"))
    assert out["format"] == "parquet"
    assert out["n_rows"] == 8
    by = {v["name"]: v["type"] for v in out["variables"]}
    assert by == {"Sector": "categorical", "Geo": "categorical",
                  "Value": "numeric"}


def test_explore_asc_uses_sibling_rda():
    out = server.tauargus_explore(str(DATA / "tau_testW.asc"))
    assert out["format"] == "rda"
    assert Path(out["rda"]).name == "tau_testW.rda"
    names = [v["name"] for v in out["variables"]]
    assert "Region" in names and "Size" in names and "Var2" in names
    for v in out["variables"]:
        assert set(v) >= {"name", "type"}
        assert isinstance(v["type"], str) and v["type"]


def test_explore_rda_direct():
    out = server.tauargus_explore(str(DATA / "tau_testW.rda"))
    assert out["format"] == "rda"
    assert [v["name"] for v in out["variables"]][0] == "Year"


def test_explore_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        server.tauargus_explore("/nonexistent/micro.parquet")


def test_explore_unsupported_format_raises():
    with pytest.raises(ValueError):
        server.tauargus_explore(str(DATA / "TestRecode.arb"))


# ===========================================================================
# tauargus_build_arb
# ===========================================================================

def test_build_arb_writes_file(tmp_path):
    out = server.tauargus_build_arb(
        asc_filename=str(DATA / "tau_testW.asc"),
        rda_filename=str(DATA / "tau_testW.rda"),
        explanatory_vars=["Size", "Region"],
        response_var="Var2",
        safety_rules="P(25,1)",
        suppress="OPT(1)",
        output_names=[str(tmp_path / "out.tab")],
        output_type="5",
        output_options="SO+",
    )
    arb = Path(out["arb"])
    assert arb.exists(), f"batch file not written: {arb}"
    assert out["output_names"] == [str(tmp_path / "out.tab")]
    text = arb.read_text(encoding="utf-8")
    assert '<OPENMICRODATA>' in text and str(DATA / "tau_testW.asc") in text
    assert '<OPENMETADATA>' in text and str(DATA / "tau_testW.rda") in text
    assert '<SPECIFYTABLE> "Size""Region"|"Var2"||' in text
    assert "<SAFETYRULE> P(25,1)" in text
    assert "<SUPPRESS> OPT(1)" in text
    assert "<WRITETABLE>" in text


def test_build_arb_default_temp_filename(tmp_path):
    out = server.tauargus_build_arb(
        asc_filename=str(DATA / "tau_testW.asc"),
        rda_filename=str(DATA / "tau_testW.rda"),
        explanatory_vars=["Size"],
        response_var="Var2",
        safety_rules="P(25,1)",
        suppress="OPT(1)",
        output_names=[str(tmp_path / "out.tab")],
    )
    assert Path(out["arb"]).suffix == ".arb"
    assert Path(out["arb"]).exists()


# ===========================================================================
# tauargus_run_arb (native run, small fixture)
# ===========================================================================

def test_run_arb_produces_tables(tmp_path):
    files = _files(tmp_path)
    out = server.tauargus_run_arb(files["arb"])
    assert out["arb"] == files["arb"]
    assert len(out["tables"]) == 1
    entry = out["tables"][0]
    assert entry["path"] == files["tab1"]
    assert entry["exists"] is True
    assert Path(entry["path"]).exists()


def test_run_arb_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        server.tauargus_run_arb("/nonexistent/batch.arb")


# ===========================================================================
# tauargus_protect (native run, small fixture)
# ===========================================================================

def test_protect_small_fixture(tmp_path):
    out = server.tauargus_protect(
        MICRO, ["Region", "Size"], "Var2",
        safety_rules="P(25,1)", suppress="OPT(1)", workdir=str(tmp_path))
    assert out["n_tables"] == 1
    assert Path(out["files"]["asc"]).exists()
    assert Path(out["files"]["rda"]).exists()
    assert Path(out["files"]["arb"]).exists()
    t = out["tables"][0]
    # 2 x 2 crossing plus totals: 9 cells
    assert t["n_cells"] == 9
    assert sum(t["status_counts"].values()) == 9
    # every cell in MICRO is a single record, so P(25,1) flags the body
    # cells primary unsafe
    assert t["status_counts"].get("U", 0) >= 1
    assert any(c["status"] == "U" for c in t["unsafe_cells"])
    for cell in t["unsafe_cells"] + t["secondary_cells"]:
        assert set(cell) >= {"cell", "value", "status", "dims"}
        assert isinstance(cell["cell"], int)
    assert Path(t["path"]).exists()
    assert "Region" in t["columns"] and "Var2" in t["columns"]


def test_protect_missing_parquet_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        server.tauargus_protect(
            "/nonexistent/micro.parquet", ["Region"], "Var2",
            workdir=str(tmp_path))


# ===========================================================================
# tables/response validation at the MCP boundary
# ===========================================================================

def test_protect_unknown_table_var_lists_available_columns():
    with pytest.raises(ValueError) as exc:
        server.tauargus_protect(MICRO, ["Region", "Bogus"], "Var2")
    msg = str(exc.value)
    assert "Bogus" in msg
    # the available columns are listed so the caller can self-correct
    for col in ("Region", "Size", "Var2"):
        assert col in msg


def test_protect_multi_table_unknown_var():
    with pytest.raises(ValueError, match="Bogus"):
        server.tauargus_protect(MICRO, [["Region"], ["Bogus"]], "Var2")


def test_protect_unknown_response_lists_available_columns():
    with pytest.raises(ValueError) as exc:
        server.tauargus_protect(MICRO, ["Region"], "Nope")
    msg = str(exc.value)
    assert "Nope" in msg
    assert "Var2" in msg


def test_protect_tables_must_be_a_list():
    with pytest.raises(TypeError, match="tables"):
        server.tauargus_protect(MICRO, "Region", "Var2")


def test_protect_empty_tables_rejected():
    with pytest.raises(ValueError, match="tables"):
        server.tauargus_protect(MICRO, [], "Var2")


def test_protect_response_already_in_table_rejected():
    with pytest.raises(ValueError, match="Var2"):
        server.tauargus_protect(MICRO, ["Region", "Var2"], "Var2")


def test_protect_reserved_column_name_rejected():
    # "freq" is a result diagnostic name (see _validate_columns)
    with pytest.raises(ValueError, match="freq"):
        server.tauargus_protect(MICRO, ["Region", "freq"], "Var2")


def test_assess_unknown_table_var():
    with pytest.raises(ValueError, match="Bogus"):
        server.tauargus_assess(MICRO, ["Region", "Bogus"], "Var2")


def test_assess_tables_must_be_a_list():
    with pytest.raises(TypeError, match="tables"):
        server.tauargus_assess(MICRO, "Region", "Var2")


# ===========================================================================
# tauargus_audit (native run, small fixture)
# ===========================================================================

def test_audit_returns_reveal_structure(tmp_path):
    files = _files(tmp_path)
    out = server.tauargus_audit(files["arb"], 0)
    assert out["arb"] == files["arb"]
    assert out["tab"] == 0
    # suppression leaves at least one suppressed cell to audit
    assert out["n_cells"] >= 1
    assert out["n_cells"] == len(out["cells"])
    for row in out["cells"]:
        assert set(row) >= {"cell", "min", "max", "value", "status", "unsafe"}
        assert row["status"] in ("u", "m")
        assert row["min"] <= row["max"]


# ===========================================================================
# server construction and entry point
# ===========================================================================

def test_server_constructs_with_tools():
    from mcp.server.fastmcp import FastMCP
    assert isinstance(server.mcp, FastMCP)
    tools = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert tools == {"tauargus_version", "tauargus_explore",
                     "tauargus_build_arb", "tauargus_run_arb",
                     "tauargus_protect", "tauargus_assess",
                     "tauargus_audit", "tauargus_method_advice"}
    assert callable(server.main)


def test_base_import_does_not_require_mcp():
    """`import pytauargus` succeeds with the mcp package blocked."""
    code = (
        "import sys\n"
        "class _Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'mcp' or name.startswith('mcp.'):\n"
        "            raise ImportError('mcp blocked for test')\n"
        "sys.meta_path.insert(0, _Block())\n"
        "import pytauargus\n"
        "assert not hasattr(pytauargus, 'mcp_server')\n"
        "print(pytauargus.__version__)\n"
    )
    env = dict(os.environ, PYTHONPATH=str(SRC)
               + os.pathsep + os.environ.get("PYTHONPATH", ""))
    proc = subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, env=env,
                          timeout=60)
    assert proc.returncode == 0, (
        f"base import with mcp blocked failed (rc={proc.returncode}):\n"
        f"STDERR:\n{proc.stderr}")
