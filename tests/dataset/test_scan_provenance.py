"""Workload labels cannot substitute for exact registered bytes and semantics."""

from pathlib import Path

import pytest

from scryntic.analytics.fixtures import prepare_fixture
from scryntic.analytics.provenance import fixture_identity


def test_different_workload_directory_cannot_qualify_the_wrong_fixture(
    tmp_path: Path,
) -> None:
    path = tmp_path / "hourly-4096-gaps"
    prepare_fixture(path, count=512, gaps=False)
    with pytest.raises(ValueError, match="workload configuration"):
        fixture_identity(path, {"rows": 4096, "gaps": True})
    digest = fixture_identity(path, {"rows": 512, "gaps": False})
    assert len(digest) == 64
    leaf = path / "dataset.parquet"
    leaf.chmod(0o600)
    leaf.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="fixture identity"):
        fixture_identity(path, {"rows": 512, "gaps": False})
