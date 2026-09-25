"""Immutable raw Parquet archive behavior."""

import asyncio
import os
from dataclasses import replace
from pathlib import Path

import pytest

from scryntic.application.archive import ArchiveLimits, RawRecordRef
from scryntic.archive.canonical import canonical_json_bytes, raw_projection
from scryntic.archive.raw_parquet import ArchiveError, ParquetRawArchive
from scryntic.domain.identity import EntityId, InstrumentId
from scryntic.domain.raw import RawEnvelope, RawRecord
from scryntic.domain.time import ClockSample, SourceTime, TimeQuality, TimeUnit
from tests.archive.helpers import installation
from tests.normalization.helpers import raw_record


def _limits(
    *, records: int = 20, encoded: int = 2_000_000, decoded: int = 2_000_000
) -> ArchiveLimits:
    return ArchiveLimits(records, encoded, decoded)


def _rich_record(offset: int, payload: bytes, subject: object) -> RawRecord:
    base = raw_record(offset=offset)
    envelope = RawEnvelope(
        source="fake-source",
        stream="market-events",
        channel="public",
        adapter_version="1.2.3",
        receipt=ClockSample(
            1_700_000_000_000_000_000 + offset,
            42 + offset,
            "session-a",
            TimeQuality(
                "clock-a",
                status="healthy",
                offset_ns=-12,
                uncertainty_ns=50,
                evidence_age_ns=10,
            ),
        ),
        payload=payload,
        payload_limit=max(1, len(payload)),
        subject=subject,  # type: ignore[arg-type]
        source_time=SourceTime(1_700_000_000_123, TimeUnit.MILLISECOND),
        source_event_id="event-7",
        source_sequence=99,
    )
    return replace(base, envelope=envelope)


def test_raw_binary_round_trip_preserves_every_field(tmp_path: Path) -> None:
    records = (
        _rich_record(2, b"", InstrumentId("fake", "spot", "BTC-USDT")),
        _rich_record(
            5,
            b"\x00\xffnot-utf8",
            EntityId("document", "publisher", "story-7"),
        ),
    )
    archive = ParquetRawArchive(installation(tmp_path))

    segment = asyncio.run(archive.seal(records, _limits()))
    recovered = tuple(
        asyncio.run(
            archive.read(
                RawRecordRef(segment.sha256, index, record.identity), _limits()
            )
        )
        for index, record in enumerate(records)
    )

    assert recovered == records
    assert segment.record_count == 2
    assert segment.sha256 == archive.object_path(segment.sha256).stem


def test_repeated_payloads_keep_distinct_ingestion_identities(tmp_path: Path) -> None:
    first = raw_record(offset=2, payload=b"same")
    second = raw_record(offset=7, payload=b"same")
    archive = ParquetRawArchive(installation(tmp_path))

    segment = asyncio.run(archive.seal((first, second), _limits()))

    assert (
        asyncio.run(
            archive.read(RawRecordRef(segment.sha256, 0, first.identity), _limits())
        )
        == first
    )
    assert (
        asyncio.run(
            archive.read(RawRecordRef(segment.sha256, 1, second.identity), _limits())
        )
        == second
    )


def test_seal_accepts_exact_logical_limit_and_rejects_one_below(
    tmp_path: Path,
) -> None:
    record = raw_record(payload=b"\x00\xff")
    logical = len(canonical_json_bytes({"raw": raw_projection(record)}))
    archive = ParquetRawArchive(installation(tmp_path))

    segment = asyncio.run(archive.seal((record,), _limits(decoded=logical)))
    assert segment.decoded_bytes == logical
    with pytest.raises(ArchiveError, match="limits"):
        asyncio.run(archive.seal((record,), _limits(decoded=logical - 1)))


def test_seal_rejects_record_limit_before_writing(tmp_path: Path) -> None:
    archive = ParquetRawArchive(installation(tmp_path))
    records = (raw_record(offset=2), raw_record(offset=5))

    with pytest.raises(ArchiveError, match="limits"):
        asyncio.run(archive.seal(records, _limits(records=1)))

    assert not tuple(archive.staging_path.iterdir())


def test_read_enforces_exact_encoded_limit_and_rejects_one_below(
    tmp_path: Path,
) -> None:
    record = raw_record()
    archive = ParquetRawArchive(installation(tmp_path))
    segment = asyncio.run(archive.seal((record,), _limits()))

    assert (
        asyncio.run(
            archive.read(
                RawRecordRef(segment.sha256, 0, record.identity),
                _limits(encoded=segment.encoded_bytes),
            )
        )
        == record
    )
    with pytest.raises(ArchiveError, match="limits"):
        asyncio.run(
            archive.read(
                RawRecordRef(segment.sha256, 0, record.identity),
                _limits(encoded=segment.encoded_bytes - 1),
            )
        )


def test_read_rejects_reference_index_or_identity_mismatch(tmp_path: Path) -> None:
    record = raw_record()
    archive = ParquetRawArchive(installation(tmp_path))
    segment = asyncio.run(archive.seal((record,), _limits()))

    with pytest.raises(ArchiveError, match="reference"):
        asyncio.run(
            archive.read(RawRecordRef(segment.sha256, 1, record.identity), _limits())
        )
    with pytest.raises(ArchiveError, match="reference"):
        asyncio.run(
            archive.read(
                RawRecordRef(
                    segment.sha256,
                    0,
                    replace(record.identity, offset=record.identity.offset + 1),
                ),
                _limits(),
            )
        )


def test_read_rejects_corrupted_object_bytes(tmp_path: Path) -> None:
    record = raw_record()
    archive = ParquetRawArchive(installation(tmp_path))
    segment = asyncio.run(archive.seal((record,), _limits()))
    path = archive.object_path(segment.sha256)
    path.chmod(0o600)
    with path.open("r+b") as stream:
        stream.seek(10)
        original = stream.read(1)
        stream.seek(10)
        stream.write(bytes([original[0] ^ 0xFF]))
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o400)

    with pytest.raises(ArchiveError, match="hash"):
        asyncio.run(
            archive.read(RawRecordRef(segment.sha256, 0, record.identity), _limits())
        )


def test_seal_rejects_non_increasing_or_cross_producer_records(tmp_path: Path) -> None:
    archive = ParquetRawArchive(installation(tmp_path))
    first = raw_record(offset=5)
    lower = raw_record(offset=2)
    other = replace(
        raw_record(offset=7), identity=replace(first.identity, producer="b")
    )

    with pytest.raises(ArchiveError, match="order"):
        asyncio.run(archive.seal((first, lower), _limits()))
    with pytest.raises(ArchiveError, match="order"):
        asyncio.run(archive.seal((first, other), _limits()))
