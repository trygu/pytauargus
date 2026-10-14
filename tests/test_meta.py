"""The wheel records the engine provenance it was built from."""

import re

from pytauargus import engine_meta


def test_engine_meta_reports_provenance():
    meta = engine_meta()
    assert re.fullmatch(r"[0-9a-f]{40}", meta["engine_sha"])
    assert re.fullmatch(r"v\d+\.\d+\.\d+", meta["highs_version"])
