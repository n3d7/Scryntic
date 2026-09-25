"""Self-contained F06 processing outcomes in bounded immutable Parquet."""

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from scryntic.application.archive import ArchiveLimits
from scryntic.archive.canonical import (
    PublicationInput,
    canonical_json_bytes,
    normalized_projection,
    semantics_projection,
)
from scryntic.archive.model import ArchiveObject, ArchiveRole
from scryntic.archive.raw_parquet import PARQUET_CODEC
from scryntic.archive.storage import ImmutableArchiveStorage
from scryntic.configuration.paths import Installation
from scryntic.domain.identity import InstrumentId, SchemaRef, Version
from scryntic.domain.market import CandleKey
from scryntic.domain.raw import IngestionId
from scryntic.domain.time import ClockSample, SourceTime, TimeQuality, TimeUnit
from scryntic.normalization.candle import (
    CandleSemantics,
    RejectionCode,
    RejectionField,
)
from scryntic.normalization.sqlite_store import OutcomeKind, ProcessingOutcome

NORMALIZED_PARQUET_SCHEMA = SchemaRef(
    "scryntic.normalization-outcome.parquet", Version(1, 0)
)
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


class NormalizedArchiveError(RuntimeError):
    """Fixed-message normalized archive failure."""


@dataclass(frozen=True, slots=True)
class ArchivedNormalization:
    identity: IngestionId
    outcome: ProcessingOutcome
    semantics: CandleSemantics | None

    def __post_init__(self) -> None:
        if self.outcome.identity != self.identity:
            raise ValueError("Archived normalization identity mismatch")
        if self.outcome.kind is OutcomeKind.REJECTED:
            if self.semantics is not None or self.outcome.semantic_revision is not None:
                raise ValueError("Rejected archive row has semantics")
        elif (
            self.semantics is None
            or self.outcome.semantic_revision != self.semantics.revision()
        ):
            raise ValueError("Archived normalization semantics mismatch")


def archived_normalization(value: PublicationInput) -> ArchivedNormalization:
    return ArchivedNormalization(value.raw.identity, value.outcome, value.semantics)


def _schema(record_count: int, logical_bytes: int) -> pa.Schema:
    fields = [
        pa.field("producer", pa.string(), nullable=False),
        pa.field("epoch", pa.string(), nullable=False),
        pa.field("offset", pa.int64(), nullable=False),
        pa.field("predecessor_producer", pa.string()),
        pa.field("predecessor_epoch", pa.string()),
        pa.field("predecessor_offset", pa.int64()),
        pa.field("raw_sha256", pa.string(), nullable=False),
        pa.field("kind", pa.string(), nullable=False),
        pa.field("semantic_revision", pa.string()),
        pa.field("rejection_code", pa.string()),
        pa.field("rejection_field", pa.string()),
    ]
    for prefix in ("input", "instrument", "output"):
        fields.extend(
            (
                pa.field(f"{prefix}_schema_name", pa.string()),
                pa.field(f"{prefix}_schema_major", pa.int64()),
                pa.field(f"{prefix}_schema_minor", pa.int64()),
            )
        )
    fields.extend(
        (
            pa.field("instrument_revision", pa.string()),
            pa.field("normalizer_version", pa.string(), nullable=False),
            pa.field("receipt_wall_time_ns", pa.int64(), nullable=False),
            pa.field("receipt_monotonic_ns", pa.int64(), nullable=False),
            pa.field("receipt_session_id", pa.string(), nullable=False),
            pa.field("quality_epoch", pa.string(), nullable=False),
            pa.field("quality_status", pa.string(), nullable=False),
            pa.field("quality_offset_ns", pa.int64()),
            pa.field("quality_uncertainty_ns", pa.int64()),
            pa.field("quality_evidence_age_ns", pa.int64()),
            pa.field("normalized_at_ns", pa.int64()),
            pa.field("semantic_venue", pa.string()),
            pa.field("semantic_category", pa.string()),
            pa.field("semantic_symbol", pa.string()),
            pa.field("semantic_start_ns", pa.int64()),
            pa.field("semantic_interval_ns", pa.int64()),
            pa.field("open", pa.string()),
            pa.field("high", pa.string()),
            pa.field("low", pa.string()),
            pa.field("close", pa.string()),
            pa.field("volume", pa.string()),
            pa.field("volume_unit", pa.string()),
            pa.field("finalized", pa.bool_()),
            pa.field("source_time_value", pa.int64()),
            pa.field("source_time_unit", pa.string()),
            pa.field("publication_time_value", pa.int64()),
            pa.field("publication_time_unit", pa.string()),
            pa.field(
                "quality_flags",
                pa.list_(pa.field("element", pa.string())),
            ),
        )
    )
    return pa.schema(
        fields,
        metadata={
            b"scryntic.format": NORMALIZED_PARQUET_SCHEMA.name.encode("ascii"),
            b"scryntic.version": b"1.0",
            b"scryntic.codec": PARQUET_CODEC.encode("ascii"),
            b"scryntic.record_count": str(record_count).encode("ascii"),
            b"scryntic.logical_decoded_bytes": str(logical_bytes).encode("ascii"),
        },
    )


def _schema_values(value: SchemaRef | None) -> tuple[str | None, int | None, int | None]:
    if value is None:
        return None, None, None
    return value.name, value.version.major, value.version.minor


def _time_values(value: SourceTime | None) -> tuple[int | None, str | None]:
    if value is None:
        return None, None
    return value.value, value.unit.value


def _row(value: PublicationInput) -> dict[str, object]:
    outcome = value.outcome
    predecessor = outcome.predecessor
    input_schema = _schema_values(outcome.input_schema)
    instrument_schema = _schema_values(outcome.instrument_schema)
    output_schema = _schema_values(outcome.output_schema)
    quality = outcome.receipt.quality
    semantics = value.semantics
    source_time = _time_values(None if semantics is None else semantics.source_time)
    publication_time = _time_values(
        None if semantics is None else semantics.publication_time
    )
    return {
        "producer": outcome.identity.producer,
        "epoch": outcome.identity.epoch,
        "offset": outcome.identity.offset,
        "predecessor_producer": None if predecessor is None else predecessor.producer,
        "predecessor_epoch": None if predecessor is None else predecessor.epoch,
        "predecessor_offset": None if predecessor is None else predecessor.offset,
        "raw_sha256": outcome.raw_sha256,
        "kind": outcome.kind.value,
        "semantic_revision": outcome.semantic_revision,
        "rejection_code": (
            None if outcome.rejection_code is None else outcome.rejection_code.value
        ),
        "rejection_field": (
            None if outcome.rejection_field is None else outcome.rejection_field.value
        ),
        "input_schema_name": input_schema[0],
        "input_schema_major": input_schema[1],
        "input_schema_minor": input_schema[2],
        "instrument_schema_name": instrument_schema[0],
        "instrument_schema_major": instrument_schema[1],
        "instrument_schema_minor": instrument_schema[2],
        "output_schema_name": output_schema[0],
        "output_schema_major": output_schema[1],
        "output_schema_minor": output_schema[2],
        "instrument_revision": outcome.instrument_revision,
        "normalizer_version": outcome.normalizer_version,
        "receipt_wall_time_ns": outcome.receipt.wall_time_ns,
        "receipt_monotonic_ns": outcome.receipt.monotonic_ns,
        "receipt_session_id": outcome.receipt.session_id,
        "quality_epoch": quality.epoch,
        "quality_status": quality.status,
        "quality_offset_ns": quality.offset_ns,
        "quality_uncertainty_ns": quality.uncertainty_ns,
        "quality_evidence_age_ns": quality.evidence_age_ns,
        "normalized_at_ns": outcome.normalized_at_ns,
        "semantic_venue": None if semantics is None else semantics.key.instrument.venue,
        "semantic_category": (
            None if semantics is None else semantics.key.instrument.category
        ),
        "semantic_symbol": None if semantics is None else semantics.key.instrument.symbol,
        "semantic_start_ns": None if semantics is None else semantics.key.start_ns,
        "semantic_interval_ns": None if semantics is None else semantics.key.interval_ns,
        "open": None if semantics is None else semantics.open,
        "high": None if semantics is None else semantics.high,
        "low": None if semantics is None else semantics.low,
        "close": None if semantics is None else semantics.close,
        "volume": None if semantics is None else semantics.volume,
        "volume_unit": None if semantics is None else semantics.volume_unit,
        "finalized": None if semantics is None else semantics.finalized,
        "source_time_value": source_time[0],
        "source_time_unit": source_time[1],
        "publication_time_value": publication_time[0],
        "publication_time_unit": publication_time[1],
        "quality_flags": None if semantics is None else list(semantics.quality_flags),
    }


def _text(row: dict[str, object], name: str, *, nullable: bool = False) -> str | None:
    value = row[name]
    if value is None and nullable:
        return None
    if type(value) is not str:
        raise ValueError("Expected stored text")
    return value


def _number(row: dict[str, object], name: str, *, nullable: bool = False) -> int | None:
    value = row[name]
    if value is None and nullable:
        return None
    if type(value) is not int:
        raise ValueError("Expected stored integer")
    return value


def _identity(row: dict[str, object], prefix: str = "") -> IngestionId:
    return IngestionId(
        cast(str, _text(row, f"{prefix}producer")),
        cast(str, _text(row, f"{prefix}epoch")),
        cast(int, _number(row, f"{prefix}offset")),
    )


def _optional_identity(row: dict[str, object]) -> IngestionId | None:
    values = (
        _text(row, "predecessor_producer", nullable=True),
        _text(row, "predecessor_epoch", nullable=True),
        _number(row, "predecessor_offset", nullable=True),
    )
    if values == (None, None, None):
        return None
    if any(value is None for value in values):
        raise ValueError("Incomplete predecessor")
    return IngestionId(cast(str, values[0]), cast(str, values[1]), cast(int, values[2]))


def _schema_ref(row: dict[str, object], prefix: str) -> SchemaRef | None:
    values = (
        _text(row, f"{prefix}_schema_name", nullable=True),
        _number(row, f"{prefix}_schema_major", nullable=True),
        _number(row, f"{prefix}_schema_minor", nullable=True),
    )
    if values == (None, None, None):
        return None
    if any(value is None for value in values):
        raise ValueError("Incomplete schema reference")
    return SchemaRef(
        cast(str, values[0]), Version(cast(int, values[1]), cast(int, values[2]))
    )


def _source_time(row: dict[str, object], prefix: str) -> SourceTime | None:
    value = _number(row, f"{prefix}_time_value", nullable=True)
    unit = _text(row, f"{prefix}_time_unit", nullable=True)
    if value is None and unit is None:
        return None
    if value is None or unit is None:
        raise ValueError("Incomplete source time")
    return SourceTime(value, TimeUnit(unit))


def _decode(row: dict[str, object]) -> ArchivedNormalization:
    identity = _identity(row)
    status = cast(str, _text(row, "quality_status"))
    if status not in ("unknown", "degraded", "healthy"):
        raise ValueError("Invalid quality status")
    receipt = ClockSample(
        cast(int, _number(row, "receipt_wall_time_ns")),
        cast(int, _number(row, "receipt_monotonic_ns")),
        cast(str, _text(row, "receipt_session_id")),
        TimeQuality(
            cast(str, _text(row, "quality_epoch")),
            cast(Literal["unknown", "degraded", "healthy"], status),
            _number(row, "quality_offset_ns", nullable=True),
            _number(row, "quality_uncertainty_ns", nullable=True),
            _number(row, "quality_evidence_age_ns", nullable=True),
        ),
    )
    kind = OutcomeKind(cast(str, _text(row, "kind")))
    rejection_code_text = _text(row, "rejection_code", nullable=True)
    rejection_field_text = _text(row, "rejection_field", nullable=True)
    outcome = ProcessingOutcome(
        identity=identity,
        predecessor=_optional_identity(row),
        raw_sha256=cast(str, _text(row, "raw_sha256")),
        kind=kind,
        semantic_revision=_text(row, "semantic_revision", nullable=True),
        rejection_code=(
            None if rejection_code_text is None else RejectionCode(rejection_code_text)
        ),
        rejection_field=(
            None
            if rejection_field_text is None
            else RejectionField(rejection_field_text)
        ),
        input_schema=_schema_ref(row, "input"),
        instrument_schema=_schema_ref(row, "instrument"),
        output_schema=_schema_ref(row, "output"),
        instrument_revision=_text(row, "instrument_revision", nullable=True),
        normalizer_version=cast(str, _text(row, "normalizer_version")),
        receipt=receipt,
        normalized_at_ns=_number(row, "normalized_at_ns", nullable=True),
    )
    venue = _text(row, "semantic_venue", nullable=True)
    if venue is None:
        semantics = None
    else:
        flags = row["quality_flags"]
        if not isinstance(flags, list) or any(type(flag) is not str for flag in flags):
            raise ValueError("Invalid quality flags")
        finalized = row["finalized"]
        if type(finalized) is not bool:
            raise ValueError("Invalid finality")
        semantics = CandleSemantics(
            CandleKey(
                InstrumentId(
                    venue,
                    cast(str, _text(row, "semantic_category")),
                    cast(str, _text(row, "semantic_symbol")),
                ),
                cast(int, _number(row, "semantic_start_ns")),
                cast(int, _number(row, "semantic_interval_ns")),
            ),
            cast(str, _text(row, "open")),
            cast(str, _text(row, "high")),
            cast(str, _text(row, "low")),
            cast(str, _text(row, "close")),
            cast(str, _text(row, "volume")),
            cast(str, _text(row, "volume_unit")),
            finalized,
            _source_time(row, "source"),
            _source_time(row, "publication"),
            tuple(cast(list[str], flags)),
        )
    return ArchivedNormalization(identity, outcome, semantics)


def _logical(value: PublicationInput | ArchivedNormalization) -> int:
    outcome = value.outcome
    semantics = value.semantics
    return len(
        canonical_json_bytes(
            {
                "outcome": normalized_projection(outcome, semantics),
                "semantics": semantics_projection(semantics),
            }
        )
    )


def _metadata_integer(value: bytes | None) -> int:
    if value is None:
        raise ValueError("Missing normalized metadata")
    text = value.decode("ascii")
    converted = int(text)
    if converted < 1 or text != str(converted):
        raise ValueError("Invalid normalized metadata")
    return converted


class NormalizedParquetArchive:
    def __init__(self, installation: Installation) -> None:
        self._owner_uid = installation.owner_uid
        self._storage = ImmutableArchiveStorage(installation)

    async def seal(
        self, values: tuple[PublicationInput, ...], limits: ArchiveLimits
    ) -> ArchiveObject:
        try:
            if type(values) is not tuple or not values:
                raise NormalizedArchiveError("Normalized archive inputs are empty")
            if len(values) > limits.max_records:
                raise NormalizedArchiveError("Normalized archive limits exceeded")
            producer = values[0].raw.identity.producer
            previous = 0
            logical_bytes = 0
            for value in values:
                if (
                    not isinstance(value, PublicationInput)
                    or value.raw.identity.producer != producer
                    or value.raw.identity.offset <= previous
                ):
                    raise NormalizedArchiveError("Invalid normalized archive order")
                previous = value.raw.identity.offset
                logical_bytes += _logical(value)
                if logical_bytes > limits.max_decoded_bytes:
                    raise NormalizedArchiveError("Normalized archive limits exceeded")
            table = pa.Table.from_pylist(
                [_row(value) for value in values],
                schema=_schema(len(values), logical_bytes),
            )
            staging = self._storage.create_staging("normalized")
            try:
                pq.write_table(
                    table,
                    staging,
                    version="2.6",
                    compression="NONE",
                    use_dictionary=False,
                    row_group_size=len(values),
                    write_statistics=False,
                    use_deprecated_int96_timestamps=False,
                )
                encoded_bytes, object_sha256 = self._storage.prepare_staging(staging)
                if encoded_bytes > limits.max_encoded_bytes:
                    raise NormalizedArchiveError("Normalized archive limits exceeded")
                descriptor = ArchiveObject(
                    ArchiveRole.NORMALIZED,
                    object_sha256,
                    NORMALIZED_PARQUET_SCHEMA,
                    PARQUET_CODEC,
                    encoded_bytes,
                    logical_bytes,
                    len(values),
                )
                if self._read_path(staging, descriptor, limits) != tuple(
                    archived_normalization(value) for value in values
                ):
                    raise NormalizedArchiveError("Normalized validation mismatch")
                target = self._storage.install_staging(staging, object_sha256)
                if self._read_path(target, descriptor, limits) != tuple(
                    archived_normalization(value) for value in values
                ):
                    raise NormalizedArchiveError("Normalized validation mismatch")
            finally:
                if staging.exists():
                    staging.unlink()
            return descriptor
        except BaseException as error:
            if isinstance(error, NormalizedArchiveError):
                raise
            if isinstance(error, Exception):
                raise NormalizedArchiveError("Unable to seal normalized archive") from None
            raise

    async def read(
        self, descriptor: ArchiveObject, limits: ArchiveLimits
    ) -> tuple[ArchivedNormalization, ...]:
        try:
            if descriptor.role is not ArchiveRole.NORMALIZED:
                raise NormalizedArchiveError("Normalized descriptor mismatch")
            path = self._storage.object_path(descriptor.sha256)
            if not path.exists():
                raise NormalizedArchiveError("Normalized object hash mismatch")
            return self._read_path(path, descriptor, limits)
        except BaseException as error:
            if isinstance(error, NormalizedArchiveError):
                raise
            if isinstance(error, Exception):
                raise NormalizedArchiveError("Unable to read normalized archive") from None
            raise

    def _read_path(
        self, path: Path, descriptor: ArchiveObject, limits: ArchiveLimits
    ) -> tuple[ArchivedNormalization, ...]:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != self._owner_uid
                or stat.S_IMODE(info.st_mode) != 0o400
            ):
                raise NormalizedArchiveError("Unsafe normalized archive object")
            if info.st_size > limits.max_encoded_bytes:
                raise NormalizedArchiveError("Normalized archive limits exceeded")
            if info.st_size != descriptor.encoded_bytes:
                raise NormalizedArchiveError("Normalized descriptor mismatch")
            if info.st_size < 12:
                raise NormalizedArchiveError("Invalid normalized archive footer")
            trailer = os.pread(fd, 8, info.st_size - 8)
            footer_length = int.from_bytes(trailer[:4], "little")
            if trailer[4:] != b"PAR1" or footer_length > info.st_size - 12:
                raise NormalizedArchiveError("Invalid normalized archive footer")
        finally:
            os.close(fd)
        if self._storage.file_sha256(path) != descriptor.sha256:
            raise NormalizedArchiveError("Normalized object hash mismatch")
        if (
            descriptor.format != NORMALIZED_PARQUET_SCHEMA
            or descriptor.codec != PARQUET_CODEC
        ):
            raise NormalizedArchiveError("Normalized descriptor mismatch")
        if (
            descriptor.record_count > limits.max_records
            or descriptor.decoded_bytes > limits.max_decoded_bytes
        ):
            raise NormalizedArchiveError("Normalized archive limits exceeded")
        parquet = pq.ParquetFile(path, memory_map=False, pre_buffer=False)
        metadata = parquet.metadata
        schema_metadata = parquet.schema_arrow.metadata or {}
        if frozenset(schema_metadata) != _METADATA_KEYS:
            raise NormalizedArchiveError("Invalid normalized archive schema")
        record_count = _metadata_integer(schema_metadata.get(b"scryntic.record_count"))
        logical_bytes = _metadata_integer(
            schema_metadata.get(b"scryntic.logical_decoded_bytes")
        )
        if (
            record_count > limits.max_records
            or logical_bytes > limits.max_decoded_bytes
        ):
            raise NormalizedArchiveError("Normalized archive limits exceeded")
        if (
            record_count != descriptor.record_count
            or logical_bytes != descriptor.decoded_bytes
            or schema_metadata.get(b"scryntic.format")
            != NORMALIZED_PARQUET_SCHEMA.name.encode("ascii")
            or schema_metadata.get(b"scryntic.version") != b"1.0"
            or schema_metadata.get(b"scryntic.codec") != PARQUET_CODEC.encode("ascii")
            or not parquet.schema_arrow.equals(
                _schema(record_count, logical_bytes), check_metadata=True
            )
            or metadata.num_row_groups != 1
            or metadata.num_rows != record_count
        ):
            raise NormalizedArchiveError("Normalized descriptor mismatch")
        row_group = metadata.row_group(0)
        for index in range(row_group.num_columns):
            column = row_group.column(index)
            if (
                column.compression != "UNCOMPRESSED"
                or column.total_compressed_size < 0
                or column.total_uncompressed_size < 0
                or "RLE_DICTIONARY" in column.encodings
                or column.total_compressed_size > descriptor.encoded_bytes
            ):
                raise NormalizedArchiveError("Invalid normalized archive encoding")
        values: list[ArchivedNormalization] = []
        observed_logical = 0
        for batch in parquet.iter_batches(
            batch_size=min(_BATCH_ROWS, limits.max_records), use_threads=False
        ):
            for row in batch.to_pylist():
                if len(values) >= limits.max_records:
                    raise NormalizedArchiveError("Normalized archive limits exceeded")
                value = _decode(row)
                size = _logical(value)
                if observed_logical + size > limits.max_decoded_bytes:
                    raise NormalizedArchiveError("Normalized archive limits exceeded")
                observed_logical += size
                values.append(value)
        if len(values) != record_count or observed_logical != logical_bytes:
            raise NormalizedArchiveError("Normalized logical size mismatch")
        return tuple(values)
