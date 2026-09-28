"""The proposed planning segment bounds fit the declared representative inputs."""

import asyncio
from pathlib import Path

import pytest

from scryntic.application.archive import ArchiveLimits, RawRecordRef
from scryntic.archive.raw_parquet import ParquetRawArchive
from scryntic.benchmarks.workloads import WorkloadSpec, build_workload
from tests.archive.helpers import installation


@pytest.mark.parametrize(
    "kind,levels",
    [
        ("recorded-candle", 100),
        ("trade", 100),
        ("orderbook", 1000),
    ],
)
def test_measured_profile_fits_proposed_segment_limits(
    tmp_path: Path, kind: str, levels: int
) -> None:
    records = build_workload(
        WorkloadSpec(kind=kind, records=256, instruments=20, levels=levels)
    )
    assert max(len(r.envelope.payload) for r in records) <= 65536
    archive = ParquetRawArchive(installation(tmp_path))
    limits = ArchiveLimits(256, 4 * 1024 * 1024, 8 * 1024 * 1024)
    segment = asyncio.run(archive.seal(records, limits))
    segment.require_within(limits)
    assert (
        asyncio.run(
            archive.read(
                RawRecordRef(segment.sha256, 255, records[-1].identity), limits
            )
        )
        == records[-1]
    )
