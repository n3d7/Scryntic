"""Exact versioned inputs for F07 reservation and publication hashing."""

import json
from dataclasses import dataclass
from hashlib import sha256

from scryntic.domain.identity import EntityId, InstrumentId, SchemaRef, SubjectId
from scryntic.domain.raw import IngestionId, RawRecord
from scryntic.domain.time import ClockSample, SourceTime, TimeQuality
from scryntic.domain.validation import digest
from scryntic.normalization.candle import CandleSemantics
from scryntic.normalization.sqlite_store import OutcomeKind, ProcessingOutcome

INPUT_FINGERPRINT_ALGORITHM = "scryntic-publication-input-v1"
ORDERED_INPUT_ALGORITHM = "scryntic-publication-ordered-input-v1"
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1

type JsonScalar = None | bool | int | str
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
type JsonObject = dict[str, JsonValue]


def _validate_json(value: JsonValue) -> None:
    if value is None or type(value) in (bool, str):
        return
    if type(value) is int:
        if not _INT64_MIN <= value <= _INT64_MAX:
            raise ValueError("Canonical integer exceeds signed 64-bit range")
        return
    if type(value) is list:
        for item in value:
            _validate_json(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError("Canonical object keys must be strings")
            _validate_json(item)
        return
    raise TypeError("Unsupported canonical JSON value")


def canonical_json_bytes(value: JsonValue) -> bytes:
    """Encode canonical-json-v1 after checking its closed value domain."""
    _validate_json(value)
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _identity(value: IngestionId | None) -> JsonObject | None:
    if value is None:
        return None
    return {
        "producer": value.producer,
        "epoch": value.epoch,
        "offset": value.offset,
    }


def _schema(value: SchemaRef | None) -> JsonObject | None:
    if value is None:
        return None
    return {
        "name": value.name,
        "major": value.version.major,
        "minor": value.version.minor,
    }


def _source_time(value: SourceTime | None) -> JsonObject | None:
    if value is None:
        return None
    return {"value": value.value, "unit": value.unit.value}


def _quality(value: TimeQuality) -> JsonObject:
    return {
        "epoch": value.epoch,
        "status": value.status,
        "offset_ns": value.offset_ns,
        "uncertainty_ns": value.uncertainty_ns,
        "evidence_age_ns": value.evidence_age_ns,
    }


def _receipt(value: ClockSample) -> JsonObject:
    return {
        "wall_time_ns": value.wall_time_ns,
        "monotonic_ns": value.monotonic_ns,
        "session_id": value.session_id,
        "quality": _quality(value.quality),
    }


def _subject(value: SubjectId | None) -> JsonObject | None:
    if value is None:
        return None
    if isinstance(value, InstrumentId):
        return {
            "type": "instrument",
            "venue": value.venue,
            "category": value.category,
            "symbol": value.symbol,
        }
    if isinstance(value, EntityId):
        return {
            "type": "entity",
            "kind": value.kind,
            "namespace": value.namespace,
            "value": value.value,
        }
    raise TypeError("Unsupported raw subject")


def raw_projection(record: RawRecord) -> JsonObject:
    if not isinstance(record, RawRecord):
        raise TypeError("Expected a raw record")
    envelope = record.envelope
    content_sha256 = sha256(envelope.payload).hexdigest()
    if content_sha256 != envelope.content_sha256:
        raise ValueError("Raw content hash mismatch")
    return {
        "identity": _identity(record.identity),
        "source": envelope.source,
        "stream": envelope.stream,
        "channel": envelope.channel,
        "adapter_version": envelope.adapter_version,
        "receipt": _receipt(envelope.receipt),
        "subject": _subject(envelope.subject),
        "source_time": _source_time(envelope.source_time),
        "source_event_id": envelope.source_event_id,
        "source_sequence": envelope.source_sequence,
        "payload_hex": envelope.payload.hex(),
        "content_sha256": content_sha256,
    }


def _semantics(value: CandleSemantics | None) -> JsonObject | None:
    if value is None:
        return None
    return {
        "key": {
            "instrument": {
                "venue": value.key.instrument.venue,
                "category": value.key.instrument.category,
                "symbol": value.key.instrument.symbol,
            },
            "start_ns": value.key.start_ns,
            "interval_ns": value.key.interval_ns,
        },
        "open": value.open,
        "high": value.high,
        "low": value.low,
        "close": value.close,
        "volume": value.volume,
        "volume_unit": value.volume_unit,
        "finalized": value.finalized,
        "source_time": _source_time(value.source_time),
        "publication_time": _source_time(value.publication_time),
        "quality_flags": list(value.quality_flags),
    }


def normalized_projection(
    outcome: ProcessingOutcome, semantics: CandleSemantics | None
) -> JsonObject:
    if not isinstance(outcome, ProcessingOutcome):
        raise TypeError("Expected a processing outcome")
    return {
        "identity": _identity(outcome.identity),
        "predecessor": _identity(outcome.predecessor),
        "raw_sha256": outcome.raw_sha256,
        "kind": outcome.kind.value,
        "semantic_revision": outcome.semantic_revision,
        "rejection_code": (
            None if outcome.rejection_code is None else outcome.rejection_code.value
        ),
        "rejection_field": (
            None if outcome.rejection_field is None else outcome.rejection_field.value
        ),
        "input_schema": _schema(outcome.input_schema),
        "instrument_schema": _schema(outcome.instrument_schema),
        "output_schema": _schema(outcome.output_schema),
        "instrument_revision": outcome.instrument_revision,
        "normalizer_version": outcome.normalizer_version,
        "receipt": _receipt(outcome.receipt),
        "normalized_at_ns": outcome.normalized_at_ns,
    }


@dataclass(frozen=True, slots=True)
class PublicationInput:
    raw: RawRecord
    outcome: ProcessingOutcome
    semantics: CandleSemantics | None

    def __post_init__(self) -> None:
        if not isinstance(self.raw, RawRecord) or not isinstance(
            self.outcome, ProcessingOutcome
        ):
            raise TypeError("Expected public raw and normalization values")
        if self.raw.identity != self.outcome.identity:
            raise ValueError("Raw and outcome identity mismatch")
        if self.raw.envelope.content_sha256 != self.outcome.raw_sha256:
            raise ValueError("Raw and outcome hash mismatch")
        if self.raw.envelope.receipt != self.outcome.receipt:
            raise ValueError("Raw and outcome receipt mismatch")
        if self.outcome.kind is OutcomeKind.REJECTED:
            if self.semantics is not None or self.outcome.semantic_revision is not None:
                raise ValueError("Rejected outcome must not have semantics")
            if self.outcome.rejection_code is None:
                raise ValueError("Rejected outcome requires rejection evidence")
        else:
            if not isinstance(self.semantics, CandleSemantics):
                raise ValueError("Accepted outcome requires semantics")
            if self.outcome.semantic_revision != self.semantics.revision():
                raise ValueError("Outcome semantics revision mismatch")
            if self.outcome.rejection_code is not None:
                raise ValueError("Accepted outcome must not have rejection evidence")

    def projection(self) -> JsonObject:
        return {
            "algorithm": INPUT_FINGERPRINT_ALGORITHM,
            "outcome": normalized_projection(self.outcome, self.semantics),
            "raw": raw_projection(self.raw),
            "semantics": _semantics(self.semantics),
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.projection())


def input_fingerprint(value: PublicationInput) -> str:
    if not isinstance(value, PublicationInput):
        raise TypeError("Expected a publication input")
    return sha256(value.canonical_bytes()).hexdigest()


def ordered_digest_from_pairs(
    pairs: tuple[tuple[IngestionId, str], ...],
) -> str:
    if type(pairs) is not tuple or not pairs:
        raise ValueError("Ordered publication inputs must be non-empty")
    producer: str | None = None
    previous_offset = 0
    entries: list[JsonValue] = []
    for identity, fingerprint in pairs:
        if not isinstance(identity, IngestionId):
            raise TypeError("Expected an ingestion identity")
        digest(fingerprint)
        if producer is None:
            producer = identity.producer
        if identity.producer != producer or identity.offset <= previous_offset:
            raise ValueError("Invalid ordered publication input order")
        previous_offset = identity.offset
        entries.append(
            {
                "identity": _identity(identity),
                "input_fingerprint": fingerprint,
            }
        )
    projection: JsonObject = {
        "algorithm": ORDERED_INPUT_ALGORITHM,
        "inputs": entries,
        "record_count": len(entries),
    }
    return sha256(canonical_json_bytes(projection)).hexdigest()


def ordered_input_digest(values: tuple[PublicationInput, ...]) -> str:
    if type(values) is not tuple:
        raise TypeError("Expected immutable publication inputs")
    return ordered_digest_from_pairs(
        tuple((value.raw.identity, input_fingerprint(value)) for value in values)
    )


def semantics_projection(value: CandleSemantics | None) -> JsonObject | None:
    """Return the exact public projection used by Parquet codecs."""
    return _semantics(value)
