"""The solver must not write scratch files into the working directory.

The solver writes its scratch files (CSPlogfile.txt, cspSCIP.*, HiTaS.log,
FullJJ.log, JJUit.dat, ...) into the out dir the front-end passes to
AHiTaS/FullJJ. That out dir must be a temp dir the front-end owns, never
the process working directory, or every MOD/OPT solve pollutes the user's
working area.

Legacy tau-ARGUS pointed the solver at the OS temp dir
(OptiSuppress.java: ``AHiTaS(..., getTempDir()+"/")``) and deleted the
previous run's leftovers. The native front-ends do better: they pass a
fresh per-call temp dir and delete it when the call ends.
"""

import os
from pathlib import Path

import pytest

from pytauargus.engine import run_batch

try:
    from conftest import DATA
except ImportError:  # curated installed-package copy has no engine data
    DATA = None

ARB = DATA / "TestRecode.arb" if DATA else None

# Every scratch file name the solver layer can produce (HiTaS PrepFile
# names plus the CSP PPCSPSetFileNames names).
SCRATCH_NAMES = (
    "CSPlogfile.txt", "cspSCIP.sol", "cspSCIP.heu", "cspSCIP.sta",
    "cspSCIP.bra", "HiTaS.log", "FullJJ.log", "JJUit.dat", "hierinfo.dat",
    "BT.dat", "BTTabs.dat", "InFeas.dat", "hitassec.txt", "XUit.dat",
    "Tricks.dat", "sdcnet.lp", "sdc.lp", "problem.lp",
)


def _build_arb(tmp_path, suppress):
    text = ARB.read_text(encoding="utf-8")
    assert "<GOINTERACTIVE>" in text
    text = text.replace(
        "<GOINTERACTIVE>",
        "<SUPPRESS> %s\n<GOINTERACTIVE>" % suppress,
        1,
    )
    for name in ("tau_testW.asc", "tau_testW.rda"):
        text = text.replace('"' + name + '"', '"%s"' % (DATA / name).as_posix())
    arb = tmp_path / ("scratch_%s.arb" % suppress.split("(")[0].lower())
    arb.write_text(text, encoding="utf-8")
    return arb


def _run_and_check(tmp_path, suppress):
    # Remove stale leftovers from earlier runs so the check is not masked
    # by pre-existing files.
    cwd = Path(os.getcwd())
    for name in SCRATCH_NAMES:
        stale = cwd / name
        if stale.exists():
            stale.unlink()
    run_batch(_build_arb(tmp_path, suppress))
    leaked = [n for n in SCRATCH_NAMES if (cwd / n).exists()]
    assert not leaked, (
        "solver scratch files leaked into the working directory: %s"
        % leaked
    )


@pytest.mark.skipif(DATA is None, reason="engine data not available")
class TestScratchFiles:
    def test_mod_suppression_keeps_scratch_out_of_cwd(self, tmp_path):
        _run_and_check(tmp_path, "MOD(1)")

    def test_opt_suppression_keeps_scratch_out_of_cwd(self, tmp_path):
        _run_and_check(tmp_path, "OPT(1)")
