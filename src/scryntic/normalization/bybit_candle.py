"""Replay parser for the bounded per-candle Bybit V5 capture record."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import cast

from scryntic.application.sources import BYBIT_CANDLE_SCHEMA
from scryntic.domain.identity import InstrumentId, SchemaRef, Version
from scryntic.domain.raw import RawEnvelope, RawRecord
from scryntic.domain.time import SourceTime, TimeUnit
from scryntic.normalization.candle import (
    BYBIT_NORMALIZER_VERSION,
    NormalizationRejection,
    ParsedFakeCandle,
    RejectionCode,
    RejectionField,
    UnsupportedSchema,
)

_PAYLOAD_LIMIT = 8_192
_MS_NS = 1_000_000
_DECIMAL = re.compile(r"(?:0|[1-9]\d*)(?:\.\d+)?", re.ASCII)
_INTEGER = re.compile(r"\d+", re.ASCII)
_INTERVAL_NS = {
    "1": 60_000_000_000,
    "3": 180_000_000_000,
    "5": 300_000_000_000,
    "15": 900_000_000_000,
    "30": 1_800_000_000_000,
    "60": 3_600_000_000_000,
    "120": 7_200_000_000_000,
    "240": 14_400_000_000_000,
    "360": 21_600_000_000_000,
    "720": 43_200_000_000_000,
    "D": 86_400_000_000_000,
    "W": 604_800_000_000_000,
}
_FIELDS = frozenset(
    {
        "category",
        "close",
        "finalized",
        "high",
        "interval",
        "interval_ns",
        "low",
        "open",
        "publication_time",
        "row",
        "schema",
        "start_ns",
        "symbol",
        "volume",
    }
)


class _DuplicateKey(ValueError):
    pass


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey
        result[key] = value
    return result


def _reject(
    record: RawRecord,
    code: RejectionCode,
    field: RejectionField | None = None,
) -> NormalizationRejection:
    return NormalizationRejection(
        raw_record=record.identity,
        raw_sha256=record.envelope.content_sha256,
        normalizer_version=BYBIT_NORMALIZER_VERSION,
        code=code,
        field=field,
        input_schema=BYBIT_CANDLE_SCHEMA,
    )


def _decimal(value: object) -> Decimal:
    if type(value) is not str or not 1 <= len(value) <= 128:
        raise ValueError
    if _DECIMAL.fullmatch(value) is None:
        raise ValueError
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError from None
    if not number.is_finite():
        raise ValueError
    return number


def inspect_bybit_candle(
    record: RawRecord,
) -> ParsedFakeCandle | NormalizationRejection | UnsupportedSchema:
    return _inspect(
        record.envelope, lambda code, field=None: _reject(record, code, field)
    )


def inspect_bybit_envelope(envelope: RawEnvelope) -> ParsedFakeCandle | None:
    result = _inspect(envelope, lambda code, field=None: None)
    return result if isinstance(result, ParsedFakeCandle) else None


def _schema_declaration(value: object) -> SchemaRef | None:
    if not isinstance(value, dict) or set(value) != {"name", "major", "minor"}:
        return None
    if (
        value.get("name") != BYBIT_CANDLE_SCHEMA.name
        or type(value.get("major")) is not int
        or type(value.get("minor")) is not int
    ):
        return None
    return SchemaRef(
        cast(str, value["name"]),
        Version(cast(int, value["major"]), cast(int, value["minor"])),
    )


def _publication_time(value: object) -> SourceTime | None:
    if value is None:
        return None
    if type(value) is not dict:
        raise ValueError("Invalid publication time")
    if (
        set(value) != {"value", "unit"}
        or type(value["value"]) is not int
        or not 0 <= value["value"] < (2**63 - 1) // _MS_NS
        or value["unit"] != TimeUnit.MILLISECOND.value
    ):
        raise ValueError("Invalid publication time")
    return SourceTime(value["value"], TimeUnit.MILLISECOND)


def _invalid_constant(value: str) -> object:
    raise ValueError("Invalid JSON constant")


def _inspect[Result](
    envelope: RawEnvelope, reject: Callable[..., Result]
) -> ParsedFakeCandle | UnsupportedSchema | Result:
    """Validate source bytes without metadata lookup, host clock, or network access."""
    payload = envelope.payload
    if len(payload) > _PAYLOAD_LIMIT:
        return reject(RejectionCode.PAYLOAD_TOO_LARGE)
    try:
        document = json.loads(
            payload.decode("utf-8", "strict"),
            object_pairs_hook=_object,
            parse_constant=_invalid_constant,
        )
    except _DuplicateKey:
        return reject(RejectionCode.DUPLICATE_JSON_KEY)
    except (ValueError, RecursionError):
        return reject(RejectionCode.INVALID_JSON)
    if not isinstance(document, dict):
        return reject(RejectionCode.INVALID_SCHEMA, RejectionField.SCHEMA)
    schema = _schema_declaration(document.get("schema"))
    if schema is None:
        return reject(RejectionCode.INVALID_SCHEMA, RejectionField.SCHEMA)
    try:
        BYBIT_CANDLE_SCHEMA.require_readable(schema)
    except ValueError:
        return UnsupportedSchema(schema)
    if set(document) != _FIELDS:
        return reject(RejectionCode.FIELD_SET_MISMATCH)
    subject = envelope.subject
    if not isinstance(subject, InstrumentId):
        return reject(RejectionCode.INVALID_SUBJECT, RejectionField.SUBJECT)
    category = document["category"]
    symbol = document["symbol"]
    interval = document["interval"]
    if (
        type(category) is not str
        or category not in ("spot", "linear", "inverse")
        or category != subject.category
        or type(symbol) is not str
        or symbol != subject.symbol
        or type(interval) is not str
        or interval not in _INTERVAL_NS
        or envelope.channel != f"kline-{interval}"
        or document["interval_ns"] != _INTERVAL_NS.get(interval)
        or type(document["interval_ns"]) is not int
    ):
        return reject(RejectionCode.INVALID_SUBJECT, RejectionField.SUBJECT)
    row = document["row"]
    source_time = envelope.source_time
    if (
        type(row) is not list
        or len(row) != 7
        or any(type(item) is not str for item in row)
        or source_time is None
        or source_time.unit is not TimeUnit.MILLISECOND
        or _INTEGER.fullmatch(cast(str, row[0])) is None
        or int(cast(str, row[0])) != source_time.value
        or type(document["start_ns"]) is not int
        or document["start_ns"] != source_time.value * _MS_NS
    ):
        return reject(RejectionCode.INVALID_TIME, RejectionField.START_NS)
    return _inspect_values(document, row, reject)


def _inspect_values[Result](
    document: dict[str, object], row: list[object], reject: Callable[..., Result]
) -> ParsedFakeCandle | Result:
    names = ("open", "high", "low", "close", "volume")
    decimals: dict[str, Decimal] = {}
    for index, name in enumerate(names, start=1):
        try:
            value = _decimal(document[name])
            raw_value = _decimal(row[index])
        except ValueError:
            return reject(RejectionCode.INVALID_DECIMAL, RejectionField(name))
        if value != raw_value or (name == "volume" and value < 0):
            return reject(RejectionCode.INVALID_DECIMAL, RejectionField(name))
        decimals[name] = value
    try:
        _decimal(row[6])
    except ValueError:
        return reject(RejectionCode.INVALID_DECIMAL, RejectionField.VOLUME)
    open_value, high, low, close, volume = (decimals[name] for name in names)
    if not low <= min(open_value, close) <= max(open_value, close) <= high:
        return reject(RejectionCode.INCONSISTENT_OHLC)
    if type(document["finalized"]) is not bool:
        return reject(RejectionCode.INVALID_FIELD_TYPE, RejectionField.FINALIZED)
    try:
        publication_time = _publication_time(document["publication_time"])
    except ValueError:
        return reject(RejectionCode.INVALID_TIME, RejectionField.PUBLICATION_TIME)
    return ParsedFakeCandle(
        schema=BYBIT_CANDLE_SCHEMA,
        start_ns=cast(int, document["start_ns"]),
        interval_ns=cast(int, document["interval_ns"]),
        open=open_value,
        high=high,
        low=low,
        close=close,
        volume=volume,
        finalized=document["finalized"],
        publication_time=publication_time,
    )
