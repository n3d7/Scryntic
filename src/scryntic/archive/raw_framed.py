"""F10 candidate: independent bounded zlib frames of canonical raw evidence.

The format is fixed locally: magic, big-endian count/logical size, then one
length-prefixed zlib stream per record. No plugin name or executable metadata.
Logical decoded bytes use exactly the same canonical projection as Parquet.
"""

import json
import os
import stat
import struct
import zlib
from collections.abc import Iterator
from hashlib import sha256
from pathlib import Path
from typing import Literal, cast

from scryntic.application.archive import ArchiveLimits, RawRecordRef, RawSegment
from scryntic.archive.canonical import canonical_json_bytes, raw_projection
from scryntic.archive.raw_parquet import ArchiveError
from scryntic.archive.storage import ImmutableArchiveStorage
from scryntic.configuration.paths import Installation
from scryntic.domain.identity import EntityId, InstrumentId, SchemaRef, Version
from scryntic.domain.raw import IngestionId, RawEnvelope, RawRecord
from scryntic.domain.time import ClockSample, SourceTime, TimeQuality, TimeUnit

FRAMED_CODEC = "framed-zlib-v1"
RAW_FRAMED_SCHEMA = SchemaRef("scryntic.raw-record.framed", Version(1, 0))
_MAGIC = b"SCRAWZ1\n"
_HEADER = struct.Struct(">8sQQ")
_FRAME = struct.Struct(">II")


def _object(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError("Expected raw object")
    return cast(dict[str, object], value)


def _text(value: object) -> str:
    if type(value) is not str:
        raise ValueError("Expected raw text")
    return value


def _integer(value: object) -> int:
    if type(value) is not int:
        raise ValueError("Expected raw integer")
    return value


def _optional_integer(value: object) -> int | None:
    return None if value is None else _integer(value)


def _decode(data: bytes, payload_limit: int) -> RawRecord:
    raw = _object(_object(json.loads(data))["raw"])
    identity = _object(raw["identity"])
    receipt = _object(raw["receipt"])
    quality = _object(receipt["quality"])
    status = _text(quality["status"])
    if status not in ("unknown", "degraded", "healthy"):
        raise ValueError("Invalid raw quality")
    subject_value = raw["subject"]
    subject: InstrumentId | EntityId | None = None
    if subject_value is not None:
        subject_data = _object(subject_value)
        if subject_data["type"] == "instrument":
            subject = InstrumentId(
                _text(subject_data["venue"]),
                _text(subject_data["category"]),
                _text(subject_data["symbol"]),
            )
        elif subject_data["type"] == "entity":
            subject = EntityId(
                _text(subject_data["kind"]),
                _text(subject_data["namespace"]),
                _text(subject_data["value"]),
            )
        else:
            raise ValueError("Invalid raw subject")
    source_value = raw["source_time"]
    source_time = None
    if source_value is not None:
        source = _object(source_value)
        source_time = SourceTime(
            _integer(source["value"]), TimeUnit(_text(source["unit"]))
        )
    event_id = raw["source_event_id"]
    envelope = RawEnvelope(
        source=_text(raw["source"]),
        stream=_text(raw["stream"]),
        channel=_text(raw["channel"]),
        adapter_version=_text(raw["adapter_version"]),
        receipt=ClockSample(
            _integer(receipt["wall_time_ns"]),
            _integer(receipt["monotonic_ns"]),
            _text(receipt["session_id"]),
            TimeQuality(
                _text(quality["epoch"]),
                cast(Literal["unknown", "degraded", "healthy"], status),
                _optional_integer(quality["offset_ns"]),
                _optional_integer(quality["uncertainty_ns"]),
                _optional_integer(quality["evidence_age_ns"]),
            ),
        ),
        payload=bytes.fromhex(_text(raw["payload_hex"])),
        payload_limit=payload_limit,
        subject=subject,
        source_time=source_time,
        source_event_id=None if event_id is None else _text(event_id),
        source_sequence=_optional_integer(raw["source_sequence"]),
    )
    record = RawRecord(
        IngestionId(
            _text(identity["producer"]),
            _text(identity["epoch"]),
            _integer(identity["offset"]),
        ),
        envelope,
    )
    # Also rejects unknown keys, duplicate keys, noncanonical numbers/hex and
    # payload hash mismatch, without accepting executable serialization.
    if canonical_json_bytes({"raw": raw_projection(record)}) != data:
        raise ValueError("Invalid canonical raw record")
    return record


class FramedZlibRawArchive:
    """Locally approved F10 codec candidate; same durable RawArchive contract."""

    def __init__(self, installation: Installation) -> None:
        self._owner_uid = installation.owner_uid
        self._storage = ImmutableArchiveStorage(installation)
        self.staging_path = self._storage.staging_path

    def object_path(self, object_sha256: str) -> Path:
        return self._storage.object_path(object_sha256)

    async def seal(
        self, records: tuple[RawRecord, ...], limits: ArchiveLimits
    ) -> RawSegment:
        try:
            if (
                type(records) is not tuple
                or not records
                or len(records) > limits.max_records
            ):
                raise ArchiveError("Archive limits exceeded")
            logical = 0
            previous = 0
            producer = records[0].identity.producer
            for record in records:
                if (
                    record.identity.producer != producer
                    or record.identity.offset <= previous
                ):
                    raise ArchiveError("Invalid archive record order")
                previous = record.identity.offset
                logical += len(canonical_json_bytes({"raw": raw_projection(record)}))
                if logical > limits.max_decoded_bytes:
                    raise ArchiveError("Archive limits exceeded")
            staging = self._storage.create_staging("raw-framed")
            try:
                with staging.open("wb") as stream:
                    stream.write(_HEADER.pack(_MAGIC, len(records), logical))
                    for record in records:
                        data = canonical_json_bytes({"raw": raw_projection(record)})
                        compressed = zlib.compress(data, level=3)
                        if (
                            stream.tell() + _FRAME.size + len(compressed)
                            > limits.max_encoded_bytes
                        ):
                            raise ArchiveError("Archive limits exceeded")
                        stream.write(_FRAME.pack(len(data), len(compressed)))
                        stream.write(compressed)
                encoded, object_hash = self._storage.prepare_staging(staging)
                if encoded > limits.max_encoded_bytes:
                    raise ArchiveError("Archive limits exceeded")
                if self._read_all(staging, object_hash, limits) != records:
                    raise ArchiveError("Raw archive validation mismatch")
                target = self._storage.install_staging(staging, object_hash)
                if self._read_all(target, object_hash, limits) != records:
                    raise ArchiveError("Raw archive validation mismatch")
            finally:
                if staging.exists():
                    staging.unlink()
            return RawSegment(
                object_hash,
                RAW_FRAMED_SCHEMA,
                FRAMED_CODEC,
                encoded,
                logical,
                len(records),
            )
        except Exception:
            raise ArchiveError("Unable to seal raw archive") from None

    def _walk(
        self, path: Path, expected_sha256: str, limits: ArchiveLimits
    ) -> Iterator[RawRecord]:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != self._owner_uid
                or stat.S_IMODE(info.st_mode) != 0o400
                or info.st_size > limits.max_encoded_bytes
                or info.st_size < _HEADER.size
            ):
                raise ArchiveError("Invalid raw archive object")
            hashed = sha256()
            while chunk := stream.read(1024 * 1024):
                hashed.update(chunk)
            if hashed.hexdigest() != expected_sha256:
                raise ArchiveError("Archive object hash mismatch")
            stream.seek(0)
            magic, count, logical = _HEADER.unpack(stream.read(_HEADER.size))
            if (
                magic != _MAGIC
                or not 0 < count <= limits.max_records
                or not 0 < logical <= limits.max_decoded_bytes
            ):
                raise ArchiveError("Archive limits or format mismatch")
            observed = 0
            previous = 0
            producer = None
            for _ in range(count):
                decoded_size, encoded_size = _FRAME.unpack(stream.read(_FRAME.size))
                if (
                    not 0 < decoded_size <= logical - observed
                    or not 0 < encoded_size <= info.st_size - stream.tell()
                ):
                    raise ArchiveError("Invalid raw frame length")
                compressed = stream.read(encoded_size)
                decoder = zlib.decompressobj()
                data = decoder.decompress(compressed, decoded_size + 1)
                if (
                    len(data) != decoded_size
                    or not decoder.eof
                    or decoder.unused_data
                    or decoder.unconsumed_tail
                ):
                    raise ArchiveError("Invalid bounded raw frame")
                record = _decode(data, limits.max_decoded_bytes)
                if producer is None:
                    producer = record.identity.producer
                if (
                    record.identity.producer != producer
                    or record.identity.offset <= previous
                ):
                    raise ArchiveError("Invalid archive record order")
                previous = record.identity.offset
                observed += decoded_size
                yield record
            if observed != logical or stream.read(1):
                raise ArchiveError("Raw archive logical size mismatch")

    def _read_all(
        self, path: Path, expected_sha256: str, limits: ArchiveLimits
    ) -> tuple[RawRecord, ...]:
        try:
            return tuple(self._walk(path, expected_sha256, limits))
        except Exception:
            raise ArchiveError("Invalid raw archive object") from None

    async def read(self, reference: RawRecordRef, limits: ArchiveLimits) -> RawRecord:
        try:
            selected = None
            # Validate the whole immutable segment before returning any record.
            # Retain only the selected record, keeping point-read memory bounded.
            for index, record in enumerate(
                self._walk(
                    self.object_path(reference.segment_sha256),
                    reference.segment_sha256,
                    limits,
                )
            ):
                if index == reference.record_index:
                    selected = record
            if selected is None or selected.identity != reference.identity:
                raise ArchiveError("Archive reference mismatch")
            return selected
        except Exception:
            raise ArchiveError("Unable to read raw archive") from None
