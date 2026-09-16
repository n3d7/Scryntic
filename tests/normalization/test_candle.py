"""Strict fake-candle inspection and bounded rejection behavior."""

import json
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal

import pytest

from scryntic.domain.identity import EntityId, SchemaRef, Version
from scryntic.domain.time import SourceTime, TimeUnit
from scryntic.normalization.candle import (
    FAKE_CANDLE_SCHEMA,
    NORMALIZER_VERSION,
    PAYLOAD_LIMIT,
    NormalizationRejection,
    ParsedFakeCandle,
    RejectionCode,
    RejectionField,
    UnsupportedSchema,
    inspect_fake_candle,
)
from tests.normalization.helpers import (
    INSTRUMENT_ID,
    fake_candle_payload,
    raw_record,
)

INT64_MAX = 9_223_372_036_854_775_807
INT64_MIN = -9_223_372_036_854_775_808


def rejection(
    payload: bytes,
    code: RejectionCode,
    field: RejectionField | None = None,
    *,
    subject: object = INSTRUMENT_ID,
    input_schema: SchemaRef | None = None,
) -> NormalizationRejection:
    record = raw_record(payload=payload, subject=subject)  # type: ignore[arg-type]
    result = inspect_fake_candle(record)
    assert isinstance(result, NormalizationRejection)
    assert result.raw_record == record.identity
    assert result.raw_sha256 == record.envelope.content_sha256
    assert result.normalizer_version == NORMALIZER_VERSION
    assert result.code is code
    assert result.field is field
    assert result.input_schema == input_schema
    assert result.instrument_schema is None
    assert result.instrument_revision is None
    assert result.output_schema is None
    assert result.normalized_at_ns is None
    return result


def test_supported_record_is_parsed_without_instrument_metadata() -> None:
    result = inspect_fake_candle(raw_record())

    assert result == ParsedFakeCandle(
        schema=FAKE_CANDLE_SCHEMA,
        start_ns=1_700_000_000_000_000_000,
        interval_ns=60_000_000_000,
        open=Decimal("100.10"),
        high=Decimal("101.25"),
        low=Decimal("99.90"),
        close=Decimal("100.75"),
        volume=Decimal("12.3400"),
        finalized=False,
        publication_time=None,
    )
    assert str(result.volume) == "12.3400"


def test_contract_constants_are_fixed() -> None:
    assert FAKE_CANDLE_SCHEMA == SchemaRef("fake_candle", Version(1, 0))
    assert NORMALIZER_VERSION == "f06.fake_candle.v1"
    assert PAYLOAD_LIMIT == 4096


def test_payload_limit_precedes_decoding() -> None:
    rejection(b"\xff" * (PAYLOAD_LIMIT + 1), RejectionCode.PAYLOAD_TOO_LARGE)


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff",
        fake_candle_payload().decode().encode("utf-16"),
        fake_candle_payload().decode().encode("utf-32"),
    ],
)
def test_payload_requires_strict_utf8(payload: bytes) -> None:
    rejection(payload, RejectionCode.INVALID_UTF8)


def test_decoder_errors_are_bounded_rejections() -> None:
    rejection(b"{", RejectionCode.INVALID_JSON)


def test_decoder_recursion_errors_are_bounded_rejections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def recursive_failure(*_args: object, **_kwargs: object) -> object:
        raise RecursionError

    monkeypatch.setattr(json, "loads", recursive_failure)
    rejection(fake_candle_payload(), RejectionCode.INVALID_JSON)


def test_duplicate_keys_are_rejected_at_nested_levels() -> None:
    payload = (
        b'{"schema":{"name":"fake_candle","major":1,"major":1,"minor":0}}'
    )
    result = rejection(payload, RejectionCode.DUPLICATE_JSON_KEY)
    assert "major" not in repr(result)


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
def test_nonfinite_json_constants_are_rejected(constant: bytes) -> None:
    payload = fake_candle_payload().replace(b'"open":"100.10"', b'"open":' + constant)
    rejection(payload, RejectionCode.INVALID_JSON)


def test_integer_token_limit_applies_before_schema_routing() -> None:
    payload = (
        b'{"schema":{"name":"future_candle","major":1,"minor":0},'
        b'"future":100000000000000000000}'
    )
    rejection(payload, RejectionCode.JSON_TOKEN_TOO_LONG)


def test_float_token_limit_applies_before_schema_routing() -> None:
    token = b"1." + b"0" * 127
    assert len(token) == 129
    payload = (
        b'{"schema":{"name":"future_candle","major":1,"minor":0},'
        b'"future":' + token + b"}"
    )
    rejection(payload, RejectionCode.JSON_TOKEN_TOO_LONG)


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"schema": None},
        {"schema": []},
        {"schema": {"name": "fake_candle", "major": 1, "minor": 0, "extra": 1}},
        {"schema": {"name": "bad/name", "major": 1, "minor": 0}},
        {"schema": {"name": "x" * 257, "major": 1, "minor": 0}},
        {"schema": {"name": "fake_candle", "major": True, "minor": 0}},
        {"schema": {"name": "fake_candle", "major": 1, "minor": False}},
        {"schema": {"name": "fake_candle", "major": 0, "minor": 0}},
        {"schema": {"name": "fake_candle", "major": 1, "minor": -1}},
        {"schema": {"name": "fake_candle", "major": INT64_MAX + 1, "minor": 0}},
        {"schema": {"name": "fake_candle", "major": 1, "minor": INT64_MAX + 1}},
    ],
)
def test_schema_declaration_must_be_exact_and_bounded(value: object) -> None:
    payload = json.dumps(value, separators=(",", ":")).encode()
    rejection(payload, RejectionCode.INVALID_SCHEMA, RejectionField.SCHEMA)


@pytest.mark.parametrize(
    "schema",
    [
        SchemaRef("future_candle", Version(1, 0)),
        SchemaRef("fake_candle", Version(2, 0)),
        SchemaRef("fake_candle", Version(1, 1)),
    ],
)
def test_well_formed_unsupported_schema_quarantines_before_body_or_subject(
    schema: SchemaRef,
) -> None:
    payload = json.dumps(
        {
            "schema": {
                "name": schema.name,
                "major": schema.version.major,
                "minor": schema.version.minor,
            },
            "unknown": {"float": 1.5, "shape": [True, None]},
        },
        separators=(",", ":"),
    ).encode()

    assert inspect_fake_candle(raw_record(payload=payload, subject=None)) == UnsupportedSchema(
        schema
    )


def test_rejection_rendering_never_contains_payload_or_decoder_text() -> None:
    result = rejection(
        b'{"credential":"user:secret@example.test"', RejectionCode.INVALID_JSON
    )
    rendered = repr(result)
    assert "secret" not in rendered
    assert "credential" not in rendered
    assert "Expecting" not in rendered


def test_supported_body_requires_exact_top_level_fields() -> None:
    missing = json.loads(fake_candle_payload())
    del missing["volume"]
    rejection(
        json.dumps(missing, separators=(",", ":")).encode(),
        RejectionCode.FIELD_SET_MISMATCH,
        input_schema=FAKE_CANDLE_SCHEMA,
    )
    extra = json.loads(fake_candle_payload())
    extra["credential"] = "do-not-copy"
    result = rejection(
        json.dumps(extra, separators=(",", ":")).encode(),
        RejectionCode.FIELD_SET_MISMATCH,
        input_schema=FAKE_CANDLE_SCHEMA,
    )
    assert "credential" not in repr(result)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("start_ns", 1.5),
        ("start_ns", True),
        ("interval_ns", "60000000000"),
        ("interval_ns", False),
        ("open", 100.1),
        ("high", None),
        ("low", 99),
        ("close", False),
        ("volume", 12.3),
        ("finalized", 1),
    ],
)
def test_supported_fields_reject_wrong_scalar_types(field: str, value: object) -> None:
    body = json.loads(fake_candle_payload())
    body[field] = value
    rejection(
        json.dumps(body, separators=(",", ":")).encode(),
        RejectionCode.INVALID_FIELD_TYPE,
        RejectionField(field),
        input_schema=FAKE_CANDLE_SCHEMA,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("start_ns", INT64_MAX + 1),
        ("start_ns", INT64_MIN - 1),
        ("interval_ns", INT64_MAX + 1),
    ],
)
def test_supported_integers_require_signed_64_bit_range(
    field: str, value: int
) -> None:
    body = json.loads(fake_candle_payload())
    body[field] = value
    rejection(
        json.dumps(body, separators=(",", ":")).encode(),
        RejectionCode.INTEGER_OUT_OF_RANGE,
        RejectionField(field),
        input_schema=FAKE_CANDLE_SCHEMA,
    )


@pytest.mark.parametrize(
    ("field", "value"), [("start_ns", -1), ("interval_ns", 0), ("interval_ns", -1)]
)
def test_candle_time_domain_bounds_are_explicit(field: str, value: int) -> None:
    body = json.loads(fake_candle_payload())
    body[field] = value
    rejection(
        json.dumps(body, separators=(",", ":")).encode(),
        RejectionCode.INVALID_TIME,
        RejectionField(field),
        input_schema=FAKE_CANDLE_SCHEMA,
    )


@pytest.mark.parametrize(
    "value",
    ["", "+1", "-1", "1e3", "1.", ".1", " 1", "1 ", "NaN", "Infinity"],
)
def test_decimal_strings_use_unsigned_plain_ascii_notation(value: str) -> None:
    rejection(
        fake_candle_payload(volume=value),
        RejectionCode.INVALID_DECIMAL,
        RejectionField.VOLUME,
        input_schema=FAKE_CANDLE_SCHEMA,
    )


@pytest.mark.parametrize("value", ["1" * 97, "0." + "1" * 126, "1" * 129])
def test_decimal_strings_are_bounded_by_digits_and_characters(value: str) -> None:
    rejection(
        fake_candle_payload(volume=value),
        RejectionCode.INVALID_DECIMAL,
        RejectionField.VOLUME,
        input_schema=FAKE_CANDLE_SCHEMA,
    )


def test_decimal_boundary_accepts_96_digits_without_context_rounding() -> None:
    value = "1" * 96
    result = inspect_fake_candle(
        raw_record(
            payload=fake_candle_payload(
                open=value, high=value, low=value, close=value, volume=value
            )
        )
    )
    assert isinstance(result, ParsedFakeCandle)
    assert str(result.volume) == value


@pytest.mark.parametrize(
    "payload",
    [
        fake_candle_payload(open="102"),
        fake_candle_payload(low="100.80"),
        fake_candle_payload(close="102"),
    ],
)
def test_ohlc_range_must_be_consistent(payload: bytes) -> None:
    rejection(
        payload,
        RejectionCode.INCONSISTENT_OHLC,
        input_schema=FAKE_CANDLE_SCHEMA,
    )


@pytest.mark.parametrize(
    "subject",
    [None, EntityId("document", "publisher", "event-1")],
)
def test_supported_candle_requires_instrument_subject(subject: object) -> None:
    rejection(
        fake_candle_payload(),
        RejectionCode.INVALID_SUBJECT,
        RejectionField.SUBJECT,
        subject=subject,
        input_schema=FAKE_CANDLE_SCHEMA,
    )


@pytest.mark.parametrize("unit", list(TimeUnit))
def test_publication_time_supports_every_explicit_time_unit(unit: TimeUnit) -> None:
    result = inspect_fake_candle(
        raw_record(
            payload=fake_candle_payload(
                publication_time={"value": INT64_MIN, "unit": unit.value}
            )
        )
    )
    assert isinstance(result, ParsedFakeCandle)
    assert result.publication_time == SourceTime(INT64_MIN, unit)


def test_null_publication_time_is_preserved() -> None:
    result = inspect_fake_candle(raw_record(payload=fake_candle_payload()))
    assert isinstance(result, ParsedFakeCandle)
    assert result.publication_time is None


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ([], RejectionCode.INVALID_FIELD_TYPE),
        ({"value": 1}, RejectionCode.FIELD_SET_MISMATCH),
        ({"value": 1, "unit": "ms", "extra": 2}, RejectionCode.FIELD_SET_MISMATCH),
        ({"value": 1.5, "unit": "ms"}, RejectionCode.INVALID_FIELD_TYPE),
        ({"value": True, "unit": "ms"}, RejectionCode.INVALID_FIELD_TYPE),
        ({"value": INT64_MAX + 1, "unit": "ms"}, RejectionCode.INTEGER_OUT_OF_RANGE),
        ({"value": 1, "unit": 1}, RejectionCode.INVALID_FIELD_TYPE),
        ({"value": 1, "unit": "minute"}, RejectionCode.INVALID_TIME),
    ],
)
def test_publication_time_shape_type_range_and_unit_are_validated(
    value: object, code: RejectionCode
) -> None:
    body = json.loads(fake_candle_payload())
    body["publication_time"] = value
    rejection(
        json.dumps(body, separators=(",", ":")).encode(),
        code,
        RejectionField.PUBLICATION_TIME,
        input_schema=FAKE_CANDLE_SCHEMA,
    )


def test_signed_64_bit_boundaries_are_accepted_for_supported_body() -> None:
    result = inspect_fake_candle(
        raw_record(
            payload=fake_candle_payload(
                start_ns=INT64_MAX,
                interval_ns=INT64_MAX,
                publication_time={"value": INT64_MIN, "unit": "ns"},
            )
        )
    )
    assert isinstance(result, ParsedFakeCandle)
    assert result.start_ns == INT64_MAX
    assert result.interval_ns == INT64_MAX
    assert result.publication_time == SourceTime(INT64_MIN, TimeUnit.NANOSECOND)


def test_predeclaration_failures_do_not_claim_an_input_schema() -> None:
    result = rejection(b"{", RejectionCode.INVALID_JSON)
    assert result.input_schema is None


def test_supported_body_failures_retain_the_input_schema() -> None:
    result = rejection(
        fake_candle_payload(volume="-1"),
        RejectionCode.INVALID_DECIMAL,
        RejectionField.VOLUME,
        input_schema=FAKE_CANDLE_SCHEMA,
    )
    assert result.input_schema is FAKE_CANDLE_SCHEMA


def test_rejections_are_frozen_values() -> None:
    value = rejection(b"{", RejectionCode.INVALID_JSON)
    with pytest.raises(FrozenInstanceError):
        replace(value, code=RejectionCode.INVALID_UTF8).code = (  # type: ignore[misc]
            RejectionCode.INVALID_JSON
        )
