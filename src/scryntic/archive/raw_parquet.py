"""Simple bounded Parquet implementation of the F03 raw archive contract."""

import os
import stat
from pathlib import Path
from typing import Literal, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from scryntic.application.archive import ArchiveLimits, RawRecordRef, RawSegment
from scryntic.archive.canonical import canonical_json_bytes, raw_projection
from scryntic.archive.storage import ImmutableArchiveStorage
from scryntic.configuration.paths import Installation
from scryntic.domain.identity import (
    EntityId,
    InstrumentId,
    SchemaRef,
    SubjectId,
    Version,
)
from scryntic.domain.raw import IngestionId, RawEnvelope, RawRecord
from scryntic.domain.time import ClockSample, SourceTime, TimeQuality, TimeUnit

RAW_PARQUET_SCHEMA = SchemaRef("scryntic.raw-record.parquet", Version(1, 0))
PARQUET_CODEC = "parquet-pyarrow-v1"
_BATCH_ROWS = 1024
_METADATA_KEYS = frozenset(
    {
        b"scryntic.format",
        b"scryntic.version",
        b"scryntic.codec",
        b"scryntic.record_count",
        b"scryntic.logical_decoded_bytes",
    }
)


class ArchiveError(RuntimeError):
    """Fixed-message raw archive failure."""


def _schema(record_count: int, logical_bytes: int) -> pa.Schema:
    return pa.schema(
        [
            pa.field("offset", pa.int64(), nullable=False),
            pa.field("producer", pa.string(), nullable=False),
            pa.field("epoch", pa.string(), nullable=False),
            pa.field("source", pa.string(), nullable=False),
            pa.field("stream", pa.string(), nullable=False),
            pa.field("channel", pa.string(), nullable=False),
            pa.field("adapter_version", pa.string(), nullable=False),
            pa.field("receipt_wall_time_ns", pa.int64(), nullable=False),
            pa.field("receipt_monotonic_ns", pa.int64(), nullable=False),
            pa.field("receipt_session_id", pa.string(), nullable=False),
            pa.field("quality_epoch", pa.string(), nullable=False),
            pa.field("quality_status", pa.string(), nullable=False),
            pa.field("quality_offset_ns", pa.int64()),
            pa.field("quality_uncertainty_ns", pa.int64()),
            pa.field("quality_evidence_age_ns", pa.int64()),
            pa.field("subject_type", pa.string()),
            pa.field("subject_first", pa.string()),
            pa.field("subject_second", pa.string()),
            pa.field("subject_third", pa.string()),
            pa.field("source_time_value", pa.int64()),
            pa.field("source_time_unit", pa.string()),
            pa.field("source_event_id", pa.string()),
            pa.field("source_sequence", pa.int64()),
            pa.field("payload", pa.binary(), nullable=False),
            pa.field("content_sha256", pa.string(), nullable=False),
        ],
        metadata={
            b"scryntic.format": RAW_PARQUET_SCHEMA.name.encode("ascii"),
            b"scryntic.version": b"1.0",
            b"scryntic.codec": PARQUET_CODEC.encode("ascii"),
            b"scryntic.record_count": str(record_count).encode("ascii"),
            b"scryntic.logical_decoded_bytes": str(logical_bytes).encode("ascii"),
        },
    )


def _row(record: RawRecord) -> dict[str, object]:
    envelope = record.envelope
    subject = envelope.subject
    if isinstance(subject, InstrumentId):
        subject_values: tuple[str | None, str | None, str | None, str | None] = (
            "instrument",
            subject.venue,
            subject.category,
            subject.symbol,
        )
    elif isinstance(subject, EntityId):
        subject_values = (
            "entity",
            subject.kind,
            subject.namespace,
            subject.value,
        )
    else:
        subject_values = (None, None, None, None)
    source_time = envelope.source_time
    quality = envelope.receipt.quality
    return {
        "offset": record.identity.offset,
        "producer": record.identity.producer,
        "epoch": record.identity.epoch,
        "source": envelope.source,
        "stream": envelope.stream,
        "channel": envelope.channel,
        "adapter_version": envelope.adapter_version,
        "receipt_wall_time_ns": envelope.receipt.wall_time_ns,
        "receipt_monotonic_ns": envelope.receipt.monotonic_ns,
        "receipt_session_id": envelope.receipt.session_id,
        "quality_epoch": quality.epoch,
        "quality_status": quality.status,
        "quality_offset_ns": quality.offset_ns,
        "quality_uncertainty_ns": quality.uncertainty_ns,
        "quality_evidence_age_ns": quality.evidence_age_ns,
        "subject_type": subject_values[0],
        "subject_first": subject_values[1],
        "subject_second": subject_values[2],
        "subject_third": subject_values[3],
        "source_time_value": None if source_time is None else source_time.value,
        "source_time_unit": None if source_time is None else source_time.unit.value,
        "source_event_id": envelope.source_event_id,
        "source_sequence": envelope.source_sequence,
        "payload": envelope.payload,
        "content_sha256": envelope.content_sha256,
    }


def _required_text(row: dict[str, object], name: str) -> str:
    value = row[name]
    if type(value) is not str:
        raise ValueError("Expected stored text")
    return value


def _optional_text(row: dict[str, object], name: str) -> str | None:
    value = row[name]
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError("Expected optional stored text")
    return value


def _required_int(row: dict[str, object], name: str) -> int:
    value = row[name]
    if type(value) is not int:
        raise ValueError("Expected stored integer")
    return value


def _optional_int(row: dict[str, object], name: str) -> int | None:
    value = row[name]
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError("Expected optional stored integer")
    return value


def _decode_row(row: dict[str, object], payload_limit: int) -> RawRecord:
    subject: SubjectId | None
    subject_type = _optional_text(row, "subject_type")
    parts = tuple(
        _optional_text(row, name)
        for name in ("subject_first", "subject_second", "subject_third")
    )
    if subject_type is None and parts == (None, None, None):
        subject = None
    elif subject_type == "instrument" and all(part is not None for part in parts):
        subject = InstrumentId(
            cast(str, parts[0]), cast(str, parts[1]), cast(str, parts[2])
        )
    elif subject_type == "entity" and all(part is not None for part in parts):
        subject = EntityId(
            cast(str, parts[0]), cast(str, parts[1]), cast(str, parts[2])
        )
    else:
        raise ValueError("Invalid stored subject")
    source_value = _optional_int(row, "source_time_value")
    source_unit = _optional_text(row, "source_time_unit")
    if source_value is None and source_unit is None:
        source_time = None
    elif source_value is not None and source_unit is not None:
        source_time = SourceTime(source_value, TimeUnit(source_unit))
    else:
        raise ValueError("Invalid stored source time")
    status = _required_text(row, "quality_status")
    if status not in ("unknown", "degraded", "healthy"):
        raise ValueError("Invalid stored quality status")
    payload = row["payload"]
    if type(payload) is not bytes:
        raise ValueError("Expected stored bytes")
    envelope = RawEnvelope(
        source=_required_text(row, "source"),
        stream=_required_text(row, "stream"),
        channel=_required_text(row, "channel"),
        adapter_version=_required_text(row, "adapter_version"),
        receipt=ClockSample(
            _required_int(row, "receipt_wall_time_ns"),
            _required_int(row, "receipt_monotonic_ns"),
            _required_text(row, "receipt_session_id"),
            TimeQuality(
                _required_text(row, "quality_epoch"),
                cast(Literal["unknown", "degraded", "healthy"], status),
                _optional_int(row, "quality_offset_ns"),
                _optional_int(row, "quality_uncertainty_ns"),
                _optional_int(row, "quality_evidence_age_ns"),
            ),
        ),
        payload=payload,
        payload_limit=payload_limit,
        subject=subject,
        source_time=source_time,
        source_event_id=_optional_text(row, "source_event_id"),
        source_sequence=_optional_int(row, "source_sequence"),
    )
    if envelope.content_sha256 != _required_text(row, "content_sha256"):
        raise ValueError("Stored payload hash mismatch")
    return RawRecord(
        IngestionId(
            _required_text(row, "producer"),
            _required_text(row, "epoch"),
            _required_int(row, "offset"),
        ),
        envelope,
    )


def _canonical_decimal_metadata(value: bytes | None) -> int:
    if value is None:
        raise ValueError("Missing archive metadata")
    text = value.decode("ascii")
    converted = int(text)
    if converted < 1 or str(converted) != text:
        raise ValueError("Invalid archive metadata")
    return converted


class ParquetRawArchive:
    """Locally trusted codec-v1 archive using generated immutable paths."""

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
            if type(records) is not tuple or not records:
                raise ArchiveError("Archive records must be non-empty")
            if len(records) > limits.max_records:
                raise ArchiveError("Archive limits exceeded")
            producer = records[0].identity.producer
            previous = 0
            logical_bytes = 0
            for record in records:
                if (
                    not isinstance(record, RawRecord)
                    or record.identity.producer != producer
                    or record.identity.offset <= previous
                ):
                    raise ArchiveError("Invalid archive record order")
                previous = record.identity.offset
                logical_bytes += len(
                    canonical_json_bytes({"raw": raw_projection(record)})
                )
                if logical_bytes > limits.max_decoded_bytes:
                    raise ArchiveError("Archive limits exceeded")
            schema = _schema(len(records), logical_bytes)
            table = pa.Table.from_pylist(
                [_row(record) for record in records], schema=schema
            )
            staging = self._storage.create_staging("raw")
            try:
                pq.write_table(
                    table,
                    staging,
                    version="2.6",
                    compression="NONE",
                    use_dictionary=False,
                    row_group_size=len(records),
                    write_statistics=False,
                    use_deprecated_int96_timestamps=False,
                )
                encoded_bytes, object_sha256 = self._storage.prepare_staging(staging)
                if encoded_bytes > limits.max_encoded_bytes:
                    raise ArchiveError("Archive limits exceeded")
                decoded = self._read_all(staging, object_sha256, limits)
                if decoded != records:
                    raise ArchiveError("Raw archive validation mismatch")
                target = self._storage.install_staging(staging, object_sha256)
                installed = self._read_all(target, object_sha256, limits)
                if installed != records:
                    raise ArchiveError("Raw archive validation mismatch")
            finally:
                if staging.exists():
                    staging.unlink()
            return RawSegment(
                object_sha256,
                RAW_PARQUET_SCHEMA,
                PARQUET_CODEC,
                encoded_bytes,
                logical_bytes,
                len(records),
            )
        except BaseException as error:
            if isinstance(error, ArchiveError):
                raise
            if isinstance(error, Exception):
                raise ArchiveError("Unable to seal raw archive") from None
            raise

    async def read(self, reference: RawRecordRef, limits: ArchiveLimits) -> RawRecord:
        try:
            records = self._read_all(
                self._storage.object_path(reference.segment_sha256),
                reference.segment_sha256,
                limits,
            )
            if reference.record_index >= len(records):
                raise ArchiveError("Archive reference mismatch")
            record = records[reference.record_index]
            if record.identity != reference.identity:
                raise ArchiveError("Archive reference mismatch")
            return record
        except BaseException as error:
            if isinstance(error, ArchiveError):
                raise
            if isinstance(error, Exception):
                raise ArchiveError("Unable to read raw archive") from None
            raise

    def _read_all(
        self, path: Path, expected_sha256: str, limits: ArchiveLimits
    ) -> tuple[RawRecord, ...]:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                info = os.fstat(fd)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != self._owner_uid
                    or stat.S_IMODE(info.st_mode) != 0o400
                ):
                    raise ArchiveError("Unsafe raw archive object")
                if info.st_size > limits.max_encoded_bytes:
                    raise ArchiveError("Archive limits exceeded")
                if info.st_size < 12:
                    raise ArchiveError("Invalid raw archive footer")
                trailer = os.pread(fd, 8, info.st_size - 8)
                footer_length = int.from_bytes(trailer[:4], "little")
                if trailer[4:] != b"PAR1" or footer_length > info.st_size - 12:
                    raise ArchiveError("Invalid raw archive footer")
            finally:
                os.close(fd)
            if self._storage.file_sha256(path) != expected_sha256:
                raise ArchiveError("Archive object hash mismatch")
            parquet = pq.ParquetFile(path, memory_map=False, pre_buffer=False)
            metadata = parquet.metadata
            schema_metadata = parquet.schema_arrow.metadata or {}
            if frozenset(schema_metadata) != _METADATA_KEYS:
                raise ArchiveError("Invalid raw archive schema")
            record_count = _canonical_decimal_metadata(
                schema_metadata.get(b"scryntic.record_count")
            )
            logical_bytes = _canonical_decimal_metadata(
                schema_metadata.get(b"scryntic.logical_decoded_bytes")
            )
            if (
                record_count > limits.max_records
                or logical_bytes > limits.max_decoded_bytes
            ):
                raise ArchiveError("Archive limits exceeded")
            if (
                schema_metadata.get(b"scryntic.format")
                != RAW_PARQUET_SCHEMA.name.encode("ascii")
                or schema_metadata.get(b"scryntic.version") != b"1.0"
                or schema_metadata.get(b"scryntic.codec")
                != PARQUET_CODEC.encode("ascii")
                or not parquet.schema_arrow.equals(
                    _schema(record_count, logical_bytes), check_metadata=True
                )
                or metadata.num_row_groups != 1
                or metadata.num_rows != record_count
            ):
                raise ArchiveError("Invalid raw archive schema")
            row_group = metadata.row_group(0)
            encoded_size = path.stat().st_size
            for index in range(row_group.num_columns):
                column = row_group.column(index)
                if (
                    column.compression != "UNCOMPRESSED"
                    or column.total_compressed_size < 0
                    or column.total_uncompressed_size < 0
                    or "RLE_DICTIONARY" in column.encodings
                    or column.total_compressed_size > encoded_size
                ):
                    raise ArchiveError("Invalid raw archive encoding")
            rows: list[RawRecord] = []
            observed_logical = 0
            for batch in parquet.iter_batches(
                batch_size=min(_BATCH_ROWS, limits.max_records), use_threads=False
            ):
                for stored in batch.to_pylist():
                    if len(rows) >= limits.max_records:
                        raise ArchiveError("Archive limits exceeded")
                    record = _decode_row(stored, limits.max_decoded_bytes)
                    size = len(canonical_json_bytes({"raw": raw_projection(record)}))
                    if observed_logical + size > limits.max_decoded_bytes:
                        raise ArchiveError("Archive limits exceeded")
                    observed_logical += size
                    rows.append(record)
            if len(rows) != record_count or observed_logical != logical_bytes:
                raise ArchiveError("Raw archive logical size mismatch")
            return tuple(rows)
        except BaseException as error:
            if isinstance(error, ArchiveError):
                raise
            if isinstance(error, Exception):
                raise ArchiveError("Invalid raw archive object") from None
            raise
