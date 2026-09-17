"""Bounded inspection of the explicit fake-candle wire format."""

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Any

from scryntic.domain.identity import InstrumentId, SchemaRef, Version
from scryntic.domain.market import CANDLE_SCHEMA, Candle, CandleKey, Instrument
from scryntic.domain.raw import IngestionId, RawRecord
from scryntic.domain.time import SourceTime, TimeUnit
from scryntic.domain.validation import digest, identifier, immutable_tuple, integer

FAKE_CANDLE_SCHEMA = SchemaRef("fake_candle", Version(1, 0))
NORMALIZER_VERSION = "f06.fake_candle.v1"
PAYLOAD_LIMIT = 4096

_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1
_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema",
        "start_ns",
        "interval_ns",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "finalized",
        "publication_time",
    }
)
_SCHEMA_FIELDS = frozenset({"name", "major", "minor"})
_PUBLICATION_TIME_FIELDS = frozenset({"value", "unit"})
_DECIMAL_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+)?", flags=re.ASCII)
_CANONICAL_DECIMAL_PATTERN = re.compile(
    r"(?:0|[1-9][0-9]*|(?:0|[1-9][0-9]*)\.[0-9]*[1-9])", flags=re.ASCII
)


class RejectionCode(StrEnum):
    PAYLOAD_TOO_LARGE = "payload_too_large"
    INVALID_UTF8 = "invalid_utf8"
    INVALID_JSON = "invalid_json"
    DUPLICATE_JSON_KEY = "duplicate_json_key"
    JSON_TOKEN_TOO_LONG = "json_token_too_long"
    INVALID_SCHEMA = "invalid_schema"
    INVALID_SUBJECT = "invalid_subject"
    FIELD_SET_MISMATCH = "field_set_mismatch"
    INVALID_FIELD_TYPE = "invalid_field_type"
    INTEGER_OUT_OF_RANGE = "integer_out_of_range"
    INVALID_DECIMAL = "invalid_decimal"
    INCONSISTENT_OHLC = "inconsistent_ohlc"
    INVALID_TIME = "invalid_time"
    INVALID_DOMAIN_VALUE = "invalid_domain_value"


class RejectionField(StrEnum):
    SCHEMA = "schema"
    START_NS = "start_ns"
    INTERVAL_NS = "interval_ns"
    OPEN = "open"
    HIGH = "high"
    LOW = "low"
    CLOSE = "close"
    VOLUME = "volume"
    FINALIZED = "finalized"
    PUBLICATION_TIME = "publication_time"
    SUBJECT = "subject"


@dataclass(frozen=True, slots=True)
class ParsedFakeCandle:
    schema: SchemaRef
    start_ns: int
    interval_ns: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    finalized: bool
    publication_time: SourceTime | None


@dataclass(frozen=True, slots=True)
class UnsupportedSchema:
    schema: SchemaRef


@dataclass(frozen=True, slots=True)
class NormalizationRejection:
    raw_record: IngestionId
    raw_sha256: str
    normalizer_version: str
    code: RejectionCode
    field: RejectionField | None
    input_schema: SchemaRef | None = None
    instrument_schema: SchemaRef | None = None
    instrument_revision: str | None = None
    output_schema: SchemaRef | None = None
    normalized_at_ns: int | None = None

    def __post_init__(self) -> None:
        digest(self.raw_sha256)
        if self.normalizer_version != NORMALIZER_VERSION:
            raise ValueError("Unexpected normalizer version")
        instrument_values = (self.instrument_schema, self.instrument_revision)
        if (instrument_values[0] is None) != (instrument_values[1] is None):
            raise ValueError("Instrument provenance must be complete")
        if self.instrument_revision is not None:
            identifier(self.instrument_revision)
        if self.normalized_at_ns is not None:
            integer(self.normalized_at_ns)


def canonical_decimal(value: Decimal) -> str:
    """Render a nonnegative finite decimal without using ambient context."""
    if not isinstance(value, Decimal):
        raise TypeError("Expected an exact Decimal")
    sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        raise ValueError("Expected a finite decimal")
    if not any(digits):
        return "0"
    if sign:
        raise ValueError("Expected a nonnegative decimal")

    coefficient = list(digits)
    while coefficient[-1] == 0:
        coefficient.pop()
        exponent += 1
    text = "".join(str(digit) for digit in coefficient)
    if exponent >= 0:
        return text + ("0" * exponent)
    decimal_index = len(text) + exponent
    if decimal_index > 0:
        return f"{text[:decimal_index]}.{text[decimal_index:]}"
    return f"0.{('0' * -decimal_index)}{text}"


def _canonical_decimal_text(value: str) -> None:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 128
        or _CANONICAL_DECIMAL_PATTERN.fullmatch(value) is None
        or canonical_decimal(Decimal(value)) != value
    ):
        raise ValueError("Expected canonical decimal text")


@dataclass(frozen=True, slots=True)
class CandleSemantics:
    key: CandleKey
    open: str
    high: str
    low: str
    close: str
    volume: str
    volume_unit: str
    finalized: bool
    source_time: SourceTime | None
    publication_time: SourceTime | None
    quality_flags: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.key, CandleKey):
            raise TypeError("Expected a candle key")
        for value in (self.open, self.high, self.low, self.close, self.volume):
            _canonical_decimal_text(value)
        identifier(self.volume_unit)
        if type(self.finalized) is not bool:
            raise TypeError("Expected explicit candle finality")
        for timestamp in (self.source_time, self.publication_time):
            if timestamp is not None and not isinstance(timestamp, SourceTime):
                raise TypeError("Expected an explicit source timestamp")
        immutable_tuple(self.quality_flags, 32)
        for flag in self.quality_flags:
            identifier(flag)
        if self.quality_flags != tuple(sorted(set(self.quality_flags))):
            raise ValueError("Quality flags must be sorted and unique")

    def canonical_bytes(self) -> bytes:
        semantic_value = [
            "scryntic-candle-semantic-v1",
            [
                self.key.instrument.venue,
                self.key.instrument.category,
                self.key.instrument.symbol,
            ],
            self.key.start_ns,
            self.key.interval_ns,
            self.open,
            self.high,
            self.low,
            self.close,
            self.volume,
            self.volume_unit,
            self.finalized,
            None
            if self.source_time is None
            else [self.source_time.value, self.source_time.unit.value],
            None
            if self.publication_time is None
            else [self.publication_time.value, self.publication_time.unit.value],
            list(self.quality_flags),
        ]
        return json.dumps(
            semantic_value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")

    def revision(self) -> str:
        return f"sha256:{sha256(self.canonical_bytes()).hexdigest()}"


@dataclass(frozen=True, slots=True)
class CandleNormalization:
    candle: Candle
    input_schema: SchemaRef
    instrument_schema: SchemaRef
    instrument_revision: str
    semantics: CandleSemantics

    def __post_init__(self) -> None:
        if self.candle.revision != self.semantics.revision():
            raise ValueError("Candle revision does not match semantic revision")
        semantic_fields_match = (
            self.candle.key == self.semantics.key
            and canonical_decimal(self.candle.open) == self.semantics.open
            and canonical_decimal(self.candle.high) == self.semantics.high
            and canonical_decimal(self.candle.low) == self.semantics.low
            and canonical_decimal(self.candle.close) == self.semantics.close
            and canonical_decimal(self.candle.volume) == self.semantics.volume
            and self.candle.volume_unit == self.semantics.volume_unit
            and self.candle.finalized is self.semantics.finalized
            and self.candle.source_time == self.semantics.source_time
            and self.candle.publication_time == self.semantics.publication_time
            and self.candle.quality_flags == self.semantics.quality_flags
        )
        if not semantic_fields_match:
            raise ValueError("Candle fields do not match semantic value")
        identifier(self.instrument_revision)


@dataclass(frozen=True, slots=True)
class _IntegerToken:
    text: str


@dataclass(frozen=True, slots=True)
class _FloatToken:
    text: str


class _DuplicateKey(ValueError):
    pass


class _InvalidConstant(ValueError):
    pass


class _TokenTooLong(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _ValidationFailure(Exception):
    code: RejectionCode
    field: RejectionField | None


def _parse_int(text: str) -> _IntegerToken:
    if len(text) > 20:
        raise _TokenTooLong
    return _IntegerToken(text)


def _parse_float(text: str) -> _FloatToken:
    if len(text) > 128:
        raise _TokenTooLong
    return _FloatToken(text)


def _reject_constant(_text: str) -> None:
    raise _InvalidConstant


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKey
        value[key] = item
    return value


def _rejection(
    record: RawRecord,
    code: RejectionCode,
    field: RejectionField | None = None,
    *,
    input_schema: SchemaRef | None = None,
) -> NormalizationRejection:
    return NormalizationRejection(
        raw_record=record.identity,
        raw_sha256=record.envelope.content_sha256,
        normalizer_version=NORMALIZER_VERSION,
        code=code,
        field=field,
        input_schema=input_schema,
    )


def _bounded_integer(value: object, field: RejectionField) -> int:
    if not isinstance(value, _IntegerToken):
        raise _ValidationFailure(RejectionCode.INVALID_FIELD_TYPE, field)
    converted = int(value.text)
    if converted < _INT64_MIN or converted > _INT64_MAX:
        raise _ValidationFailure(RejectionCode.INTEGER_OUT_OF_RANGE, field)
    return converted


def _schema(document: object) -> SchemaRef:
    if not isinstance(document, dict) or set(document) != _SCHEMA_FIELDS:
        raise _ValidationFailure(RejectionCode.INVALID_SCHEMA, RejectionField.SCHEMA)
    name = document["name"]
    major = document["major"]
    minor = document["minor"]
    if type(name) is not str or not isinstance(major, _IntegerToken) or not isinstance(
        minor, _IntegerToken
    ):
        raise _ValidationFailure(RejectionCode.INVALID_SCHEMA, RejectionField.SCHEMA)
    major_value = int(major.text)
    minor_value = int(minor.text)
    if not (1 <= major_value <= _INT64_MAX) or not (
        0 <= minor_value <= _INT64_MAX
    ):
        raise _ValidationFailure(RejectionCode.INVALID_SCHEMA, RejectionField.SCHEMA)
    try:
        return SchemaRef(name, Version(major_value, minor_value))
    except (TypeError, ValueError):
        raise _ValidationFailure(
            RejectionCode.INVALID_SCHEMA, RejectionField.SCHEMA
        ) from None


def _decimal(value: object, field: RejectionField) -> Decimal:
    if type(value) is not str:
        raise _ValidationFailure(RejectionCode.INVALID_FIELD_TYPE, field)
    if (
        not 1 <= len(value) <= 128
        or len(value.replace(".", "")) > 96
        or _DECIMAL_PATTERN.fullmatch(value) is None
    ):
        raise _ValidationFailure(RejectionCode.INVALID_DECIMAL, field)
    return Decimal(value)


def _publication_time(value: object) -> SourceTime | None:
    field = RejectionField.PUBLICATION_TIME
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _ValidationFailure(RejectionCode.INVALID_FIELD_TYPE, field)
    if set(value) != _PUBLICATION_TIME_FIELDS:
        raise _ValidationFailure(RejectionCode.FIELD_SET_MISMATCH, field)
    timestamp = _bounded_integer(value["value"], field)
    unit = value["unit"]
    if type(unit) is not str:
        raise _ValidationFailure(RejectionCode.INVALID_FIELD_TYPE, field)
    try:
        return SourceTime(timestamp, TimeUnit(unit))
    except ValueError:
        raise _ValidationFailure(RejectionCode.INVALID_TIME, field) from None


def _supported(document: dict[str, Any], record: RawRecord) -> ParsedFakeCandle:
    if set(document) != _TOP_LEVEL_FIELDS:
        raise _ValidationFailure(RejectionCode.FIELD_SET_MISMATCH, None)

    start_ns = _bounded_integer(document["start_ns"], RejectionField.START_NS)
    interval_ns = _bounded_integer(
        document["interval_ns"], RejectionField.INTERVAL_NS
    )
    if start_ns < 0:
        raise _ValidationFailure(RejectionCode.INVALID_TIME, RejectionField.START_NS)
    if interval_ns <= 0:
        raise _ValidationFailure(RejectionCode.INVALID_TIME, RejectionField.INTERVAL_NS)

    open_value = _decimal(document["open"], RejectionField.OPEN)
    high = _decimal(document["high"], RejectionField.HIGH)
    low = _decimal(document["low"], RejectionField.LOW)
    close = _decimal(document["close"], RejectionField.CLOSE)
    volume = _decimal(document["volume"], RejectionField.VOLUME)
    if not low <= min(open_value, close) <= max(open_value, close) <= high:
        raise _ValidationFailure(RejectionCode.INCONSISTENT_OHLC, None)

    finalized = document["finalized"]
    if type(finalized) is not bool:
        raise _ValidationFailure(
            RejectionCode.INVALID_FIELD_TYPE, RejectionField.FINALIZED
        )
    publication_time = _publication_time(document["publication_time"])
    if not isinstance(record.envelope.subject, InstrumentId):
        raise _ValidationFailure(
            RejectionCode.INVALID_SUBJECT, RejectionField.SUBJECT
        )
    return ParsedFakeCandle(
        schema=FAKE_CANDLE_SCHEMA,
        start_ns=start_ns,
        interval_ns=interval_ns,
        open=open_value,
        high=high,
        low=low,
        close=close,
        volume=volume,
        finalized=finalized,
        publication_time=publication_time,
    )


def inspect_fake_candle(
    record: RawRecord,
) -> ParsedFakeCandle | NormalizationRejection | UnsupportedSchema:
    """Inspect one raw record without metadata lookup or clock access."""
    payload = record.envelope.payload
    if len(payload) > PAYLOAD_LIMIT:
        return _rejection(record, RejectionCode.PAYLOAD_TOO_LARGE)
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return _rejection(record, RejectionCode.INVALID_UTF8)
    try:
        document = json.loads(
            text,
            parse_int=_parse_int,
            parse_float=_parse_float,
            parse_constant=_reject_constant,
            object_pairs_hook=_object,
        )
    except _DuplicateKey:
        return _rejection(record, RejectionCode.DUPLICATE_JSON_KEY)
    except _TokenTooLong:
        return _rejection(record, RejectionCode.JSON_TOKEN_TOO_LONG)
    except (_InvalidConstant, json.JSONDecodeError, RecursionError):
        return _rejection(record, RejectionCode.INVALID_JSON)

    if not isinstance(document, dict) or "schema" not in document:
        return _rejection(
            record, RejectionCode.INVALID_SCHEMA, RejectionField.SCHEMA
        )
    try:
        schema = _schema(document["schema"])
    except _ValidationFailure as failure:
        return _rejection(record, failure.code, failure.field)
    if schema != FAKE_CANDLE_SCHEMA:
        return UnsupportedSchema(schema)
    try:
        return _supported(document, record)
    except _ValidationFailure as failure:
        return _rejection(
            record,
            failure.code,
            failure.field,
            input_schema=FAKE_CANDLE_SCHEMA,
        )


def _domain_rejection(
    record: RawRecord,
    parsed: ParsedFakeCandle,
    instrument: Instrument,
    normalized_at_ns: int,
) -> NormalizationRejection:
    return NormalizationRejection(
        raw_record=record.identity,
        raw_sha256=record.envelope.content_sha256,
        normalizer_version=NORMALIZER_VERSION,
        code=RejectionCode.INVALID_DOMAIN_VALUE,
        field=None,
        input_schema=parsed.schema,
        instrument_schema=instrument.schema,
        instrument_revision=instrument.revision,
        output_schema=CANDLE_SCHEMA,
        normalized_at_ns=normalized_at_ns,
    )


def normalize_parsed_candle(
    record: RawRecord,
    parsed: ParsedFakeCandle,
    instrument: Instrument,
    *,
    normalized_at_ns: int,
) -> CandleNormalization | NormalizationRejection:
    """Construct a candle using only explicit values supplied by the caller."""
    if not isinstance(record, RawRecord):
        raise TypeError("Expected a raw record")
    if not isinstance(parsed, ParsedFakeCandle):
        raise TypeError("Expected a parsed fake candle")
    if not isinstance(instrument, Instrument):
        raise TypeError("Expected instrument metadata")
    integer(normalized_at_ns)
    if not isinstance(parsed.schema, SchemaRef):
        raise TypeError("Expected a parsed schema reference")
    integer(parsed.start_ns)
    integer(parsed.interval_ns)
    for value in (parsed.open, parsed.high, parsed.low, parsed.close, parsed.volume):
        if not isinstance(value, Decimal):
            raise TypeError("Expected parsed candle Decimal values")
    if type(parsed.finalized) is not bool:
        raise TypeError("Expected parsed candle finality")
    if parsed.publication_time is not None and not isinstance(
        parsed.publication_time, SourceTime
    ):
        raise TypeError("Expected parsed publication time")
    subject = record.envelope.subject
    if not isinstance(subject, InstrumentId) or subject != instrument.identity:
        raise ValueError("Raw and instrument identity mismatch")
    if parsed.schema != FAKE_CANDLE_SCHEMA:
        raise ValueError("Parsed candle schema mismatch")

    try:
        key = CandleKey(subject, parsed.start_ns, parsed.interval_ns)
        semantics = CandleSemantics(
            key,
            canonical_decimal(parsed.open),
            canonical_decimal(parsed.high),
            canonical_decimal(parsed.low),
            canonical_decimal(parsed.close),
            canonical_decimal(parsed.volume),
            instrument.volume_unit,
            parsed.finalized,
            record.envelope.source_time,
            parsed.publication_time,
            (),
        )
    except ValueError:
        return _domain_rejection(record, parsed, instrument, normalized_at_ns)

    revision = semantics.revision()
    try:
        candle = Candle(
            key=key,
            open=parsed.open,
            high=parsed.high,
            low=parsed.low,
            close=parsed.close,
            volume=parsed.volume,
            volume_unit=instrument.volume_unit,
            finalized=parsed.finalized,
            revision=revision,
            normalizer_version=NORMALIZER_VERSION,
            raw_record=record.identity,
            receipt=record.envelope.receipt,
            normalized_at_ns=normalized_at_ns,
            source_time=record.envelope.source_time,
            publication_time=parsed.publication_time,
            quality_flags=(),
            schema=CANDLE_SCHEMA,
        )
    except ValueError:
        return _domain_rejection(record, parsed, instrument, normalized_at_ns)
    return CandleNormalization(
        candle,
        parsed.schema,
        instrument.schema,
        instrument.revision,
        semantics,
    )
