"""Smoke-test the installed collector wheel and pinned Parquet runtime."""

import asyncio
import os
import tempfile
from pathlib import Path

import pyarrow  # type: ignore[import-untyped]

from scryntic.application.archive import ArchiveLimits, RawRecordRef
from scryntic.archive.raw_parquet import ParquetRawArchive
from scryntic.configuration.paths import Installation
from scryntic.domain.raw import IngestionId, RawEnvelope, RawRecord
from scryntic.domain.time import ClockSample, TimeQuality


def main() -> None:
    assert pyarrow.__version__ == "25.0.1"
    with tempfile.TemporaryDirectory(prefix="scryntic-archive-smoke-") as scratch:
        root = Path(scratch)
        config = root / "config"
        state = root / "state"
        runtime = root / "runtime"
        credentials = config / "credentials"
        for path in (config, state, runtime, credentials):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.chmod(0o700)
        uid = os.geteuid()
        installation = Installation(
            config, state, runtime, credentials, uid, uid, False
        )
        identity = IngestionId("wheel-smoke", "epoch-a", 1)
        record = RawRecord(
            identity,
            RawEnvelope(
                source="fake",
                stream="binary",
                channel="public",
                adapter_version="v1",
                receipt=ClockSample(
                    1_700_000_000_000_000_000,
                    1,
                    "wheel-smoke",
                    TimeQuality("clock-a"),
                ),
                payload=b"\x00\xffPAR1\x80",
                payload_limit=64,
            ),
        )
        limits = ArchiveLimits(1, 1_000_000, 1_000_000)
        archive = ParquetRawArchive(installation)
        segment = asyncio.run(archive.seal((record,), limits))
        restored = asyncio.run(
            archive.read(RawRecordRef(segment.sha256, 0, identity), limits)
        )
        assert restored == record
    print("Verified installed collector Parquet raw-byte round trip")


if __name__ == "__main__":
    main()
