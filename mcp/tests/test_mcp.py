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
# Remote microdata: s3://, gs://, https:// .parquet URLs are read natively
# by pyarrow, so the MCP must pass them through to to_microdata instead of a
# local is_file() check. The agent must not be sent to download the file
# with an aws client.
# ===========================================================================

def test_explore_remote_parquet_url_reads_natively(monkeypatch):
    pytest.importorskip("pyarrow")
    import pytauargus.dataframe as dfmod
    seen = []

    def fake(path):
        seen.append(path)
        return {"Region": ["Os", "Ws"], "Size": ["1", "2"],
                "Value": [1.0, 2.0]}
    monkeypatch.setattr(dfmod, "to_microdata", fake)
    url = "s3://bucket/key.parquet"
    out = server.tauargus_explore(url)
    assert out["format"] == "parquet"
    assert out["n_rows"] == 2
    assert seen == [url]  # the URL passed through, not a local path
    by = {v["name"]: v["type"] for v in out["variables"]}
    assert by == {"Region": "categorical", "Size": "categorical",
                  "Value": "numeric"}


def test_explore_remote_non_parquet_url_raises_not_fileerror(monkeypatch):
    # A remote .csv must give a clear "only .parquet" error, not
    # FileNotFoundError (which sends the agent to download the file).
    import pytauargus.dataframe as dfmod

    def fake(path):
        raise AssertionError("to_microdata not expected for a remote .csv")
    monkeypatch.setattr(dfmod, "to_microdata", fake)
    with pytest.raises(ValueError, match="parquet"):
        server.tauargus_explore("s3://bucket/key.csv")


def test_to_asc_rda_remote_parquet_url(tmp_path, monkeypatch):
    pytest.importorskip("pyarrow")
    import pytauargus.dataframe as dfmod
    seen = []

    def fake(path):
        seen.append(path)
        return {"Region": ["Os", "Ws"], "Size": ["1", "2"],
                "Value": [1.0, 2.0]}
    monkeypatch.setattr(dfmod, "to_microdata", fake)
    monkeypatch.chdir(tmp_path)
    url = "s3://bucket/key.parquet"
    out = server.tauargus_to_asc_rda(url, response_var="Value")
    assert seen == [url]
    # default output lands in the CWD, named after the URL basename
    assert Path(out["asc"]).name == "key.asc"
    assert Path(out["asc"]).is_file()
    assert Path(out["rda"]).is_file()


def test_validate_table_spec_accepts_remote_parquet_url(monkeypatch):
    import pytauargus.dataframe as dfmod
    seen = []

    def fake(path):
        seen.append(path)
        return {"Region": ["Os", "Ws"], "Size": ["1", "2"],
                "Value": [1.0, 2.0]}
    monkeypatch.setattr(dfmod, "to_microdata", fake)
    url = "s3://bucket/key.parquet"
    specs = server._validate_table_spec(url, ["Region", "Size"], "Value")
    assert specs == [["Region", "Size"]]
    assert url in seen


def test_tool_docstrings_advertise_remote_urls():
    # The model reads these docstrings to decide whether it can hand the
    # server an s3:// URL directly. Without this, it downloads the file
    # itself with an aws client.
    import inspect
    for name in ("tauargus_explore", "tauargus_to_asc_rda",
                 "tauargus_protect", "tauargus_assess"):
        doc = (inspect.getdoc(getattr(server, name)) or "").lower()
        assert "s3" in doc, f"{name} docstring does not mention s3 support"


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


def test_build_arb_relative_output_resolves_to_intended_target(tmp_path, monkeypatch):
    # issue #57: the engine resolves relative <WRITETABLE> names against
    # the batch file's directory, so a CWD-relative name with the batch in
    # a subdirectory doubled the path at run time (data/data/tab1.tab).
    # All MCP path arguments are CWD-relative, so the written target must
    # resolve (engine semantics) to the CWD-relative location intended.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    out = server.tauargus_build_arb(
        asc_filename=str(DATA / "tau_testW.asc"),
        rda_filename=str(DATA / "tau_testW.rda"),
        explanatory_vars=["Size"],
        response_var="Var2",
        safety_rules="P(25,1)",
        suppress="OPT(1)",
        output_names=["data/out.tab"],
        arb_filename="data/batch.arb",
    )
    arb = Path(out["arb"])
    assert arb == tmp_path / "data" / "batch.arb"
    targets = server._write_table_paths(arb)
    assert [t["path"] for t in targets] == [str(tmp_path / "data" / "out.tab")]
    assert out["output_names"] == [str(tmp_path / "data" / "out.tab")]


def test_build_arb_relative_output_default_arb(tmp_path, monkeypatch):
    # with the default temp batch (system temp dir), a relative output
    # name must still land CWD-relative, not in the temp dir
    monkeypatch.chdir(tmp_path)
    out = server.tauargus_build_arb(
        asc_filename=str(DATA / "tau_testW.asc"),
        rda_filename=str(DATA / "tau_testW.rda"),
        explanatory_vars=["Size"],
        response_var="Var2",
        safety_rules="P(25,1)",
        suppress="OPT(1)",
        output_names=["out.tab"],
    )
    targets = server._write_table_paths(Path(out["arb"]))
    assert [t["path"] for t in targets] == [str(tmp_path / "out.tab")]


def test_build_arb_multi_table(tmp_path):
    # issue #57 part 1: a list of per-table variable lists builds a
    # multi-table batch (micro_arb accepts this; the tool passed a flat
    # list only)
    out = server.tauargus_build_arb(
        asc_filename=str(DATA / "tau_testW.asc"),
        rda_filename=str(DATA / "tau_testW.rda"),
        explanatory_vars=[["Size"], ["Size", "Region"]],
        response_var="Var2",
        safety_rules="P(25,1)",
        suppress="OPT(1)",
        output_names=[str(tmp_path / "t1.tab"), str(tmp_path / "t2.tab")],
        output_type="5",
    )
    arb = Path(out["arb"])
    assert arb.exists()
    text = arb.read_text(encoding="utf-8")
    assert text.count("<SPECIFYTABLE>") == 2
    assert text.count("<SAFETYRULE>") == 2
    assert '<WRITETABLE> (1,' in text
    assert '<WRITETABLE> (2,' in text
    assert out["output_names"] == [str(tmp_path / "t1.tab"),
                                   str(tmp_path / "t2.tab")]


def test_build_arb_multi_table_per_table_args(tmp_path):
    # per-table response var and safety rule pass through to micro_arb
    # (R recycling: a length-1 value covers all tables, a per-table
    # vector must have one entry per table)
    out = server.tauargus_build_arb(
        asc_filename=str(DATA / "tau_testW.asc"),
        rda_filename=str(DATA / "tau_testW.rda"),
        explanatory_vars=[["Size"], ["Size", "Region"]],
        response_var=["Var2", "Var3"],
        safety_rules=["P(25,1)", "P(50,1)"],
        suppress=["OPT(1)", "OPT(1)"],
        output_names=[str(tmp_path / "t1.tab"), str(tmp_path / "t2.tab")],
    )
    text = Path(out["arb"]).read_text(encoding="utf-8")
    assert '"Size"|"Var2"|' in text
    assert '"Size""Region"|"Var3"|' in text
    assert "<SAFETYRULE> P(25,1)" in text
    assert "<SAFETYRULE> P(50,1)" in text


def test_build_arb_recode_truncation(tmp_path):
    # the engine's <RECODE> command (legacy GUI batch); the level form
    # truncates a hierarchical variable
    out = server.tauargus_build_arb(
        asc_filename=str(DATA / "tau_testW.asc"),
        rda_filename=str(DATA / "tau_testW.rda"),
        explanatory_vars=["Region"],
        response_var="Var2",
        safety_rules="P(25,1)",
        suppress="OPT(1)",
        output_names=[str(tmp_path / "out.tab")],
        recodes=[{"var": "Region", "level": 2}],
    )
    text = Path(out["arb"]).read_text(encoding="utf-8")
    lines = text.splitlines()
    i = lines.index("<READMICRODATA>")
    assert lines[i + 1] == "<RECODE> 1, Region, 2"
    assert lines[i + 2] == "<SUPPRESS> OPT(1)"


def test_build_arb_recode_file_resolves_against_cwd(tmp_path, monkeypatch):
    # a CWD-relative .grc path is resolved up front (like every other
    # path argument) and written absolute into the batch
    monkeypatch.chdir(tmp_path)
    (tmp_path / "GK.grc").write_text("1:1-5", encoding="utf-8")
    out = server.tauargus_build_arb(
        asc_filename=str(DATA / "tau_testW.asc"),
        rda_filename=str(DATA / "tau_testW.rda"),
        explanatory_vars=["Size"],
        response_var="Var2",
        safety_rules="P(25,1)",
        suppress="OPT(1)",
        output_names=["out.tab"],
        arb_filename="batch.arb",
        recodes=[{"var": "Size", "file": "GK.grc"}],
    )
    lines = Path(out["arb"]).read_text(encoding="utf-8").splitlines()
    i = lines.index("<READMICRODATA>")
    assert lines[i + 1] == '<RECODE> 1, Size, "%s"' % (tmp_path / "GK.grc")


def test_build_arb_recode_missing_file_fails_before_writing(tmp_path,
                                                            monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="recode file not found"):
        server.tauargus_build_arb(
            asc_filename=str(DATA / "tau_testW.asc"),
            rda_filename=str(DATA / "tau_testW.rda"),
            explanatory_vars=["Size"],
            response_var="Var2",
            safety_rules="P(25,1)",
            suppress="OPT(1)",
            output_names=["out.tab"],
            arb_filename="batch.arb",
            recodes=[{"var": "Size", "file": "nope.grc"}],
        )
    assert not (tmp_path / "batch.arb").exists()


def test_build_arb_schema_exposes_recodes():
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    schema = tools["tauargus_build_arb"].inputSchema
    # Optional[List[Dict]] renders as anyOf [array of objects, null]
    recodes = schema["properties"]["recodes"]
    assert recodes["default"] is None
    arr = next(b for b in recodes["anyOf"] if b.get("type") == "array")
    assert arr["items"]["type"] == "object"


def test_instructions_cover_global_recode():
    # the agent must know recoding is an engine move with a concrete
    # tool path, not an improvise-a-script situation
    text = server.INSTRUCTIONS.lower()
    assert "global recoding" in text
    assert "recodes" in text


def test_build_arb_schema_exposes_multi_table():
    # the runtime passes nested lists through regardless of the
    # annotation, so pin the client-facing JSON schema: each
    # explanatory_vars entry may be a variable name (one flat table)
    # or a list of variable names (one table per entry)
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    schema = tools["tauargus_build_arb"].inputSchema
    items = schema["properties"]["explanatory_vars"]["items"]
    item_types = {x.get("type") for x in items.get("anyOf", [])}
    assert item_types == {"string", "array"}


# ===========================================================================
# tauargus_to_asc_rda (issue #56: bootstrap the batch flow from
# parquet/dict, with up-front column type validation)
# ===========================================================================

def test_to_asc_rda_from_parquet(tmp_path):
    src = DATA / "examples" / "parquet" / "micro.parquet"
    p = tmp_path / "micro.parquet"
    p.write_bytes(src.read_bytes())
    out = server.tauargus_to_asc_rda(str(p), response_var="Value")
    asc, rda = Path(out["asc"]), Path(out["rda"])
    # default: the pair is written next to the input
    assert asc == p.with_suffix(".asc") and asc.exists()
    assert rda == p.with_suffix(".rda") and rda.exists()
    types = {v["name"]: v["type"] for v in out["variables"]}
    assert types == {"Sector": "RECODEABLE", "Geo": "RECODEABLE",
                     "Value": "NUMERIC"}


def test_to_asc_rda_from_dict(tmp_path):
    out = server.tauargus_to_asc_rda(
        MICRO,
        response_var="Var2",
        explanatory_vars=["Region", "Size"],
        asc_filename=str(tmp_path / "m.asc"),
    )
    assert out["asc"] == str(tmp_path / "m.asc")
    assert out["rda"] == str(tmp_path / "m.rda")
    assert Path(out["asc"]).exists() and Path(out["rda"]).exists()
    types = {v["name"]: v["type"] for v in out["variables"]}
    assert types == {"Region": "RECODEABLE", "Size": "RECODEABLE",
                     "Var2": "NUMERIC"}


def test_to_asc_rda_string_response_fails_upfront(tmp_path):
    # the issue #56 failure mode: a response variable read in as strings
    # (e.g. from a CSV) would be typed RECODEABLE in the .rda and only
    # fail deep in the engine as "SetTable failed for table 1"
    micro = {"m": ["a", "b", "a"], "v": ["10", "20", "30"]}
    with pytest.raises(ValueError, match="must be numeric"):
        server.tauargus_to_asc_rda(micro, response_var="v",
                                   asc_filename=str(tmp_path / "m.asc"))
    assert not (tmp_path / "m.asc").exists()


def test_to_asc_rda_numeric_explanatory_fails(tmp_path):
    # a numeric dimension is rejected by the engine too (SetTable failed)
    micro = {"m": [1, 2, 1], "v": [10, 20, 30]}
    with pytest.raises(ValueError, match="categorical"):
        server.tauargus_to_asc_rda(micro, response_var="v",
                                   explanatory_vars=["m"],
                                   asc_filename=str(tmp_path / "m.asc"))
    assert not (tmp_path / "m.asc").exists()


def test_to_asc_rda_unknown_response_lists_columns(tmp_path):
    with pytest.raises(ValueError, match="nope"):
        server.tauargus_to_asc_rda(MICRO, response_var="nope",
                                   asc_filename=str(tmp_path / "m.asc"))


def test_to_asc_rda_non_numeric_weight_fails(tmp_path):
    micro = dict(MICRO)
    micro["W"] = ["a", "b", "c", "d"]
    with pytest.raises(ValueError, match="weight"):
        server.tauargus_to_asc_rda(micro, response_var="Var2", weight_var="W",
                                   asc_filename=str(tmp_path / "m.asc"))


def test_to_asc_rda_rejects_non_parquet_path(tmp_path):
    csv = tmp_path / "x.csv"
    csv.write_text("a,v\nx,1\ny,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="parquet"):
        server.tauargus_to_asc_rda(str(csv))


def test_to_asc_rda_missing_parquet_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        server.tauargus_to_asc_rda(str(tmp_path / "nope.parquet"))


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
# tauargus_report (the session write-up)
# ===========================================================================

def test_report_empty_session():
    server._session.clear()
    out = server.tauargus_report()
    assert out["verdict"] == "none"
    assert "Nothing to report" in out["markdown"]


def test_report_writes_up_the_session(tmp_path):
    server._session.clear()
    # a legacy batch on .asc/.rda files: the report must be able to
    # write it up without any parquet or dict conversion
    files = _files(tmp_path)
    run = server.tauargus_run_arb(files["arb"])
    assert run["suppress"] == ["OPT(1)"]
    assert run["rules"]
    audit = server.tauargus_audit(files["arb"], 0)
    out = server.tauargus_report()
    md = out["markdown"]
    assert "# tau-ARGUS session report" in md
    assert "## Data" in md
    assert "No dataset was explored" in md
    assert "## Protection runs" in md
    assert "Optimal (`OPT(1)`)" in md
    assert "P(25,1)" in md
    assert "p% dominance rule" in md
    assert "## Audit" in md
    # the withheld cells of the batch run are in the report
    withheld = [c for t in run["tables"] for c in t["withheld"]]
    assert withheld  # every MICRO body cell is a single record
    for c in withheld:
        assert f"cell {c['cell']}" in md
    # the overall verdict is consistent with the audit
    expected = "hold" if any(c["unsafe"] for c in audit["cells"]) \
        else "publish"
    assert out["verdict"] == expected


def test_report_writes_up_assess_session():
    # a session that assesses must not crash the report: assess used to
    # log the table spec (a list of variable names) where the renderer
    # expects the result (a dict with n_cells and sensitive_cells), so
    # any session with an assess call died in tauargus_report
    server._session.clear()
    assess = server.tauargus_assess(MICRO, ["Region", "Size"], "Var2")
    out = server.tauargus_report()
    md = out["markdown"]
    assert "## Assessment (safety rules, nothing withheld)" in md
    # every MICRO cell is a single record, so P(25,1) flags them all
    flagged = [c for t in assess["tables"]
               for c in t["sensitive_cells"] if c["status"] == "U"]
    assert flagged
    for c in flagged:
        assert f"cell {c['cell']}" in md
        assert f"value {server._num(c['value'])}" in md
    assert "p% dominance rule" in md


def test_report_notes_runs_without_audit(tmp_path):
    server._session.clear()
    files = _files(tmp_path)
    server.tauargus_run_arb(files["arb"])
    out = server.tauargus_report()
    assert out["verdict"] == "not-audited"
    assert "not audited" in out["markdown"]


def test_report_public_copy_redacts_suppressed_values(tmp_path):
    server._session.clear()
    files = _files(tmp_path)
    run = server.tauargus_run_arb(files["arb"])
    server.tauargus_audit(files["arb"], 0)
    withheld = [c for t in run["tables"] for c in t["withheld"]]
    assert withheld
    out = server.tauargus_report(public=True)
    md = out["markdown"]
    assert "Distribution copy" in md
    assert "value [redacted]" in md
    assert "| [redacted] |" in md
    # no suppressed value leaks into the distribution copy, but the
    # cell identities, the status counts and the audit intervals stay
    for c in withheld:
        v = server._num(c["value"])
        assert f"value {v}" not in md
        assert f"| {v} |" not in md
    assert "## Verdict" in md
    # the default (internal) copy still carries the values
    internal = server.tauargus_report()["markdown"]
    for c in withheld:
        assert f"value {server._num(c['value'])}" in internal


def test_report_writes_to_path(tmp_path):
    server._session.clear()
    files = _files(tmp_path)
    server.tauargus_run_arb(files["arb"])
    target = tmp_path / "report.md"
    out = server.tauargus_report(str(target))
    assert out["path"] == str(target)
    assert target.read_text() == out["markdown"]


def test_suppress_text_maps_specs_to_method_names():
    assert server._suppress_text("OPT(1)") == "Optimal (`OPT(1)`)"
    assert server._suppress_text("MOD(1)") == "Modular / HiTaS (`MOD(1)`)"
    assert server._suppress_text("GH") == "Hypercube / GHMITER (`GH`)"
    assert server._suppress_text("RND") == "Controlled rounding (`RND`)"
    # unknown spec: the raw spec, no invented name
    assert server._suppress_text("FOO(1)") == "`FOO(1)`"


# ===========================================================================
# server construction and entry point
# ===========================================================================

def test_server_constructs_with_tools():
    from mcp.server.fastmcp import FastMCP
    assert isinstance(server.mcp, FastMCP)
    tools = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert tools == {"tauargus_version", "tauargus_explore",
                     "tauargus_to_asc_rda",
                     "tauargus_build_arb", "tauargus_run_arb",
                     "tauargus_protect", "tauargus_assess",
                     "tauargus_audit", "tauargus_report",
                     "tauargus_method_advice"}
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
