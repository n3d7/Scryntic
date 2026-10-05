"""Measure real bounded local archives and committed journal reads in-process."""

import asyncio
from pathlib import Path

import pytest

from scripts.benchmark_raw import (
    _archive,
    _installation,
    _measure_ingestion,
    _truncated_reads,
)
from scryntic.application.archive import ArchiveLimits
from scryntic.benchmarks.workloads import WorkloadSpec, build_workload


@pytest.mark.parametrize("codec", ["parquet", "framed-zlib"])
def test_measurement_rejects_corruption_and_reads_committed_sample(
    tmp_path: Path, codec: str
) -> None:
    spec = WorkloadSpec(kind="mixed", records=6, instruments=2, levels=2)
    installation = _installation(tmp_path)
    archive = _archive(codec, installation)
    records = build_workload(spec)
    limits = ArchiveLimits(6, 1_048_576, 1_048_576)
    segment = asyncio.run(archive.seal(records, limits))
    path = archive.object_path(segment.sha256)
    outcomes = _truncated_reads(archive, path, segment.sha256, limits, tmp_path)
    assert len(outcomes) == 6
    assert all(outcomes.values())
    assert archive._read_all(path, segment.sha256, limits) == records
    result = _measure_ingestion(spec, installation, 3, 0, 5)
    assert result["committed_records"] == 5
    assert result["verified_records_after"] == 5
    sqlite_bytes = result["sqlite_bytes_after_close"]
    assert isinstance(sqlite_bytes, int)
    assert sqlite_bytes > 0
