"""Framed candidate must preserve the same immutable raw evidence as Parquet."""

import asyncio
import os
import struct
import zlib
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from scryntic.application.archive import ArchiveLimits, RawRecordRef
from scryntic.archive.canonical import canonical_json_bytes, raw_projection
from scryntic.archive.raw_parquet import ArchiveError
from tests.archive.helpers import installation
from tests.archive.test_raw_parquet import _rich_record
from tests.normalization.helpers import raw_record


def _archive(root: Path):  # type: ignore[no-untyped-def]
    from scryntic.archive.raw_framed import FramedZlibRawArchive

    return FramedZlibRawArchive(installation(root))


LIMITS = ArchiveLimits(100, 2_000_000, 2_000_000)


def test_framed_preserves_binary_evidence_and_immutable_identity(
    tmp_path: Path,
) -> None:
    from scryntic.domain.identity import EntityId, InstrumentId

    records = (
        _rich_record(2, b"", InstrumentId("fake", "spot", "BTC-USDT")),
        _rich_record(5, b"\x00\xffnot-utf8", EntityId("document", "pub", "story")),
    )
    archive = _archive(tmp_path)
    segment = asyncio.run(archive.seal(records, LIMITS))
    assert segment.decoded_bytes == sum(
        len(canonical_json_bytes({"raw": raw_projection(r)})) for r in records
    )
    path = archive.object_path(segment.sha256)
    inode = path.stat().st_ino
    assert path.stat().st_mode & 0o777 == 0o400
    assert sha256(path.read_bytes()).hexdigest() == segment.sha256
    assert asyncio.run(archive.seal(records, LIMITS)) == segment
    assert path.stat().st_ino == inode
    for index, record in enumerate(records):
        assert (
            asyncio.run(
                archive.read(
                    RawRecordRef(segment.sha256, index, record.identity), LIMITS
                )
            )
            == record
        )
    assert not tuple(archive.staging_path.iterdir())


def test_framed_limits_order_and_reference(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    record = raw_record()
    records = (record, replace(record, identity=replace(record.identity, offset=2)))
    segment = asyncio.run(archive.seal(records, LIMITS))
    exact = ArchiveLimits(2, segment.encoded_bytes, segment.decoded_bytes)
    assert asyncio.run(archive.seal(records, exact)) == segment
    for small in (
        replace(exact, max_records=1),
        replace(exact, max_encoded_bytes=segment.encoded_bytes - 1),
        replace(exact, max_decoded_bytes=segment.decoded_bytes - 1),
    ):
        with pytest.raises(ArchiveError):
            asyncio.run(archive.seal(records, small))
        with pytest.raises(ArchiveError):
            asyncio.run(
                archive.read(RawRecordRef(segment.sha256, 0, record.identity), small)
            )
    for invalid in ((), records[::-1], (record, record)):
        with pytest.raises(ArchiveError):
            asyncio.run(archive.seal(invalid, LIMITS))
    for reference in (
        RawRecordRef(segment.sha256, 2, record.identity),
        RawRecordRef(segment.sha256, 0, replace(record.identity, offset=9)),
    ):
        with pytest.raises(ArchiveError):
            asyncio.run(archive.read(reference, LIMITS))


@pytest.mark.parametrize("cut", [0, 8, 24, 30, -1])
def test_framed_truncated_objects_never_return_partial_success(
    tmp_path: Path, cut: int
) -> None:
    archive = _archive(tmp_path)
    record = raw_record()
    segment = asyncio.run(archive.seal((record,), LIMITS))
    path = archive.object_path(segment.sha256)
    data = path.read_bytes()[:cut]
    path.chmod(0o600)
    path.write_bytes(data)
    path.chmod(0o400)
    with pytest.raises(ArchiveError):
        asyncio.run(
            archive.read(RawRecordRef(segment.sha256, 0, record.identity), LIMITS)
        )
    # Rehashing cannot convert a malformed stream to valid archived evidence.
    with pytest.raises(ArchiveError):
        archive._read_all(path, sha256(data).hexdigest(), LIMITS)


def test_framed_rejects_bounded_decompression_bomb_and_trailing_data(
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path)
    record = raw_record()
    segment = asyncio.run(archive.seal((record,), LIMITS))
    path = archive.object_path(segment.sha256)
    original = path.read_bytes()
    compressed = zlib.compress(b"x" * 100_000)
    # Advertised logical size is tiny; decoder must cap inflation before parsing.
    bomb = original[:8] + struct.pack(">QQII", 1, 10, 10, len(compressed)) + compressed
    for data in (bomb, original + b"trailing"):
        path.chmod(0o600)
        path.write_bytes(data)
        path.chmod(0o400)
        with pytest.raises(ArchiveError):
            archive._read_all(path, sha256(data).hexdigest(), LIMITS)


def test_framed_corruption_does_not_change_committed_object(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    record = raw_record()
    segment = asyncio.run(archive.seal((record,), LIMITS))
    path = archive.object_path(segment.sha256)
    data = path.read_bytes()
    path.chmod(0o600)
    path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    with path.open("rb") as stream:
        os.fsync(stream.fileno())
    path.chmod(0o400)
    with pytest.raises(ArchiveError):
        asyncio.run(
            archive.read(RawRecordRef(segment.sha256, 0, record.identity), LIMITS)
        )
