"""Installed-distribution identity checks.

Run in a fresh environment where the native wheel and the TUI wheel from
the current build are installed. Every check that touches the native
engine or a console script runs in a subprocess; the process must exit
zero *after* full termination (a shutdown crash is a failure).
"""

import importlib.metadata
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytauargus
from pytauargus import engine_meta

from conftest import _require_env


def _venv_python():
    return sys.executable


def _console(name):
    venv = Path(_require_env("TAUARGUS_TEST_VENV"))
    scripts = venv / ("Scripts" if os.name == "nt" else "bin")
    exe = scripts / (name + (".exe" if os.name == "nt" else ""))
    assert exe.exists(), f"console script {exe} missing from the venv"
    return str(exe)


def test_distribution_versions_match_the_build():
    expected = _require_env("TAUARGUS_EXPECTED_VERSION")
    expected_tui = _require_env("TAUARGUS_EXPECTED_TUI_VERSION")
    got = importlib.metadata.version("pytauargus")
    got_tui = importlib.metadata.version("pytauargus-tui")
    print(f"pytauargus={got} pytauargus-tui={got_tui} "
          f"(expected {expected}, {expected_tui})")
    assert got == expected
    assert got_tui == expected_tui


def test_engine_meta_matches_the_pinned_engine():
    meta = engine_meta()
    expected_sha = _require_env("TAUARGUS_EXPECTED_ENGINE_SHA")
    expected_highs = _require_env("TAUARGUS_EXPECTED_HIGHS")
    print(f"engine_sha={meta['engine_sha']} highs={meta['highs_version']}")
    assert meta["engine_sha"] == expected_sha, (
        f"installed engine {meta['engine_sha']} != pinned {expected_sha}")
    assert meta["highs_version"] == expected_highs


def test_modules_load_from_the_installation():
    venv = Path(_require_env("TAUARGUS_TEST_VENV")).resolve()
    pkg = Path(pytauargus.__file__).resolve()
    native = Path(pytauargus._tauargus.__file__).resolve()
    print(f"pytauargus: {pkg}")
    print(f"native extension: {native}")
    for path in (pkg, native):
        assert str(path).startswith(str(venv)), (
            f"{path} is not inside the installation environment {venv}")
        for marker in ("src/pytauargus", "engine/build", "/build/"):
            assert marker not in str(path), (
                f"{path} looks like a source/build path")


def test_no_development_engine_path():
    assert not os.environ.get("TAUARGUS_ENGINE_DIR", ""), (
        "TAUARGUS_ENGINE_DIR is set; the tests would load a dev engine")


def _run_script(cmd, **kwargs):
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                          **kwargs)
    if proc.returncode != 0:
        raise AssertionError(
            f"{cmd} exited {proc.returncode}:\n"
            + (proc.stdout + proc.stderr)[-4000:])
    return proc


def test_cli_version_exits_cleanly():
    _run_script([_console("tauargus"), "--version"])


def test_tui_help_exits_cleanly():
    proc = _run_script([_console("tauargus-tui"), "--help"])
    assert "usage" in (proc.stdout + proc.stderr).lower()
    # The alias entry point (the one `uvx pytauargus-tui` resolves).
    _run_script([_console("pytauargus-tui"), "--help"])
