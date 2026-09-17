"""One-record normalization using explicit raw and instrument dependencies."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from scryntic.domain.identity import InstrumentId
from scryntic.domain.market import Instrument
from scryntic.domain.raw import IngestionId, RawRecord
from scryntic.domain.validation import integer
from scryntic.normalization.candle import (
    NormalizationRejection,
    UnsupportedSchema,
    inspect_fake_candle,
    normalize_parsed_candle,
)
from scryntic.normalization.sqlite_store import (
    BarrierReason,
    NormalizationError,
    NormalizationStore,
    ProcessingBarrier,
    ProcessingOutcome,
)


class RawRecordReader(Protocol):
    """The public F05 read contract; no storage implementation is required."""

    def records_after(self, offset: int, *, limit: int) -> tuple[RawRecord, ...]: ...


@dataclass(frozen=True, slots=True)
class NoWork:
    checkpoint: IngestionId | None


@dataclass(frozen=True, slots=True)
class Processed:
    outcome: ProcessingOutcome


@dataclass(frozen=True, slots=True)
class Blocked:
    barrier: ProcessingBarrier


def process_next(
    reader: RawRecordReader,
    store: NormalizationStore,
    instruments: Mapping[InstrumentId, Instrument],
    *,
    normalized_at_ns: int,
) -> NoWork | Processed | Blocked:
    """Inspect, resolve and commit at most the next existing raw record."""
    integer(normalized_at_ns)
    if not -(2**63) <= normalized_at_ns <= 2**63 - 1:
        raise ValueError("Normalization time exceeds storage range")
    status = store.status()
    checkpoint = status.checkpoint
    offset = 0 if checkpoint is None else checkpoint.offset
    try:
        records = reader.records_after(offset, limit=1)
    except Exception:
        raise NormalizationError("Unable to read next normalization record") from None
    if not isinstance(records, tuple) or len(records) > 1:
        raise NormalizationError("Invalid normalization reader result")
    if records and (
        not isinstance(records[0], RawRecord)
        or records[0].identity.producer != status.producer
        or records[0].identity.offset <= offset
    ):
        raise NormalizationError("Invalid normalization record order")
    barrier = status.barrier
    if barrier is not None and (
        not records
        or barrier.blocker != records[0].identity
        or barrier.raw_sha256 != records[0].envelope.content_sha256
        or barrier.predecessor != checkpoint
    ):
        raise NormalizationError("Normalization record does not match barrier")
    if not records:
        return NoWork(checkpoint)
    record = records[0]
    inspected = inspect_fake_candle(record)
    if isinstance(inspected, NormalizationRejection):
        return Processed(
            store.process(record, inspected, expected_predecessor=checkpoint)
        )
    if isinstance(inspected, UnsupportedSchema):
        return Blocked(
            store.block(
                record,
                expected_predecessor=checkpoint,
                reason=BarrierReason.UNSUPPORTED_SCHEMA,
                schema=inspected.schema,
            )
        )
    subject = record.envelope.subject
    if not isinstance(subject, InstrumentId):
        raise NormalizationError("Inspector returned candle for invalid subject")
    try:
        instrument = instruments.get(subject)
    except Exception:
        raise NormalizationError("Unable to resolve normalization metadata") from None
    if instrument is None:
        return Blocked(
            store.block(
                record,
                expected_predecessor=checkpoint,
                reason=BarrierReason.METADATA_UNAVAILABLE,
                schema=inspected.schema,
                instrument=subject,
            )
        )
    if not isinstance(instrument, Instrument) or instrument.identity != subject:
        raise NormalizationError("Normalization metadata identity mismatch")
    normalized = normalize_parsed_candle(
        record, inspected, instrument, normalized_at_ns=normalized_at_ns
    )
    return Processed(store.process(record, normalized, expected_predecessor=checkpoint))
