"""Native audit (Intervalle port) contract tests.

Encodes the legacy ``intervalle.exe`` contract (reference/intervalle/) and the
manual example tau-argus-4.1-agent-reference.md section 2.15:

    X11 + X12 = 7      X11 + X21 = 6
    X21 + X22 = 3      X12 + X22 = 4

With bounds [0, 100] the LP is feasible and the realized feasibility
intervals are:

    X11 in [3, 6]      X12 in [1, 4]
    X21 in [0, 3]      X22 in [0, 3]

The legacy audit is ``dateirechnen=primsec``: variables are all cells with
status ``u`` (primary) or ``m`` (secondary) in the JJ file, bounds are
``Int(LBound)..Int(UBound)`` taken from the per-cell JJ line, and the active
equations are the JJ restrictions containing at least one suppressed cell,
with the safe-cell contributions moved to the right-hand side.
"""

from pathlib import Path

# JJ file replicating the manual section 2.15 table (3x2 with totals,
# row 3 published: (3, 3)).
#
# Layout: dim0 = rows {0=Total, 1=r1, 2=r2, 3=r3}, dim1 = cols {0=T, 1=c1, 2=c2}
# cell index = row * 3 + col
#
#   cell  0  (T,T)  16  s      cell  4  (r1,c1)  X11  u
#   cell  1  (T,c1) 9   s      cell  5  (r1,c2)  X12  u
#   cell  2  (T,c2) 7   s      cell  6  (r2,T)   3   s
#   cell  3  (r1,T) 7   s      cell  7  (r2,c1)  X21  u
#   cell  8  (r2,c2)  X22  u   cell  9  (r3,T)   6   s
#   cell 10  (r3,c1) 3   s     cell 11  (r3,c2)  3   s
#
# Cell line:   index value cost status lo hi lpl upl sliding
# Restriction: 0 n : parent (-1) child1 (1) ...  (rhs = 0)
MANUAL_EXAMPLE_JJ = """\
0
12
0 16 1 s 0 100 0.000 0.000 0
1 9 1 s 0 100 0.000 0.000 0
2 7 1 s 0 100 0.000 0.000 0
3 7 1 s 0 100 0.000 0.000 0
4 6 1 u 0 100 1.000 1.000 0
5 1 1 u 0 100 1.000 1.000 0
6 3 1 s 0 100 0.000 0.000 0
7 0 1 u 0 100 1.000 1.000 0
8 3 1 u 0 100 1.000 1.000 0
9 6 1 s 0 100 0.000 0.000 0
10 3 1 s 0 100 0.000 0.000 0
11 3 1 s 0 100 0.000 0.000 0
7
0 4 : 0 (-1) 3 (1) 6 (1) 9 (1)
0 4 : 1 (-1) 4 (1) 7 (1) 10 (1)
0 4 : 2 (-1) 5 (1) 8 (1) 11 (1)
0 3 : 0 (-1) 1 (1) 2 (1)
0 3 : 3 (-1) 4 (1) 5 (1)
0 3 : 6 (-1) 7 (1) 8 (1)
0 3 : 9 (-1) 10 (1) 11 (1)
"""


def _write_jj(tmp_path: Path, content: str) -> str:
    p = tmp_path / "Anneke.JJ"
    p.write_text(content, encoding="ascii")
    return str(p)


class TestNativeAudit:
    def test_manual_example_intervals(self, tmp_path):
        """Manual section 2.15 example: X11 in [3,6] and companions."""
        from pytauargus._tauargus import audit_jj

        jj = _write_jj(tmp_path, MANUAL_EXAMPLE_JJ)
        rows = audit_jj(jj)

        # All four interior cells are suppressed (primsec) and audited.
        assert sorted(r["cell"] for r in rows) == [4, 5, 7, 8]
        assert all(r["status"] == "u" for r in rows)

        got = {r["cell"]: (r["min"], r["max"]) for r in rows}
        assert got[4] == (3.0, 6.0), f"X11 interval: {got[4]} != [3, 6]"
        assert got[5] == (1.0, 4.0), f"X12 interval: {got[5]} != [1, 4]"
        assert got[7] == (0.0, 3.0), f"X21 interval: {got[7]} != [0, 3]"
        assert got[8] == (0.0, 3.0), f"X22 interval: {got[8]} != [0, 3]"

        # Reported values match the JJ response values.
        vals = {r["cell"]: r["value"] for r in rows}
        assert vals[4] == 6.0 and vals[5] == 1.0
        assert vals[7] == 0.0 and vals[8] == 3.0

    def test_protection_check_flags(self, tmp_path):
        """Legacy unsafe flag: (max - value) < UPL or (max - min) < LPL.

        With UPL = LPL = 1 on all suppressed cells:
        - X11 (cell 4) value 6, [3,6]:  (6-6)=0 < 1       -> unsafe
        - X12 (cell 5) value 1, [1,4]:  (4-1)=3, width 3  -> safe
        - X21 (cell 7) value 0, [0,3]:  (3-0)=3, width 3  -> safe
        - X22 (cell 8) value 3, [0,3]:  (3-3)=0 < 1       -> unsafe
        """
        from pytauargus._tauargus import audit_jj

        jj = _write_jj(tmp_path, MANUAL_EXAMPLE_JJ)
        rows = audit_jj(jj)
        flag = {r["cell"]: r["unsafe"] for r in rows}
        assert flag[4] is True
        assert flag[5] is False
        assert flag[7] is False
        assert flag[8] is True

    def test_pinned_cell_degenerate_interval(self, tmp_path):
        """A single suppressed cell pinned by an equality gets min == max."""
        # 1D table: total=10 (s), a=6 (u), b=4 (s): a = 10 - 4 = 6 exactly.
        jj_text = """\
0
3
0 10 1 s 0 100 0.000 0.000 0
1 6 1 u 0 100 1.000 1.000 0
2 4 1 s 0 100 0.000 0.000 0
1
0 3 : 0 (-1) 1 (1) 2 (1)
"""
        from pytauargus._tauargus import audit_jj

        jj = _write_jj(tmp_path, jj_text)
        rows = audit_jj(jj)
        assert len(rows) == 1
        assert rows[0]["cell"] == 1
        assert rows[0]["min"] == 6.0
        assert rows[0]["max"] == 6.0
