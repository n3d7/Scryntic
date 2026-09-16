"""Bounded inspection of the explicit fake-candle wire format."""

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from scryntic.domain.identity import InstrumentId, SchemaRef, Version
from scryntic.domain.raw import IngestionId, RawRecord
from scryntic.domain.time import SourceTime, TimeUnit

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
