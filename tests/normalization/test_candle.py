"""Strict fake-candle inspection and bounded rejection behavior."""

import json
from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, localcontext

import pytest

from scryntic.domain.identity import EntityId, InstrumentId, SchemaRef, Version
from scryntic.domain.market import CANDLE_SCHEMA, CandleKey
from scryntic.domain.time import ClockSample, SourceTime, TimeQuality, TimeUnit
from scryntic.normalization.candle import (
    FAKE_CANDLE_SCHEMA,
    NORMALIZER_VERSION,
    PAYLOAD_LIMIT,
    CandleNormalization,
    CandleSemantics,
    NormalizationRejection,
    ParsedFakeCandle,
    RejectionCode,
    RejectionField,
    UnsupportedSchema,
    canonical_decimal,
    inspect_fake_candle,
    normalize_parsed_candle,
)
from tests.normalization.helpers import (
    INSTRUMENT_ID,
    fake_candle_payload,
    instrument,
    raw_record,
)

INT64_MAX = 9_223_372_036_854_775_807
INT64_MIN = -9_223_372_036_854_775_808
SEMANTIC_KEY = CandleKey(
    InstrumentId("fake", "spot", "BTC-USDT"),
    1_700_000_000_000_000_000,
    60_000_000_000,
)
SEMANTIC_SOURCE_TIME = SourceTime(1_700_000_000_123, TimeUnit.MILLISECOND)


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
    payload = b'{"schema":{"name":"fake_candle","major":1,"major":1,"minor":0}}'
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

    assert inspect_fake_candle(
        raw_record(payload=payload, subject=None)
    ) == UnsupportedSchema(schema)


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
def test_supported_integers_require_signed_64_bit_range(field: str, value: int) -> None:
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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("0"), "0"),
        (Decimal("-0.000"), "0"),
        (Decimal("12.3400"), "12.34"),
        (Decimal("0012.3400"), "12.34"),
        (Decimal("0.0001000"), "0.0001"),
        (Decimal("1000"), "1000"),
        (Decimal("1E+6"), "1000000"),
    ],
)
def test_decimal_canonicalization_ignores_context(
    value: Decimal, expected: str
) -> None:
    with localcontext() as context:
        context.prec = 2
        assert canonical_decimal(value) == expected


@pytest.mark.parametrize(
    "value",
    [Decimal("-0.1"), Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")],
)
def test_decimal_canonicalization_rejects_negative_or_nonfinite_values(
    value: Decimal,
) -> None:
    with pytest.raises(ValueError):
        canonical_decimal(value)


def semantic_value(
    *,
    key: CandleKey = SEMANTIC_KEY,
    open: str = "100.1",
    high: str = "101.25",
    low: str = "99.9",
    close: str = "100.75",
    volume: str = "12.34",
    volume_unit: str = "BTC",
    finalized: bool = False,
    source_time: SourceTime | None = SEMANTIC_SOURCE_TIME,
    publication_time: SourceTime | None = None,
    quality_flags: tuple[str, ...] = (),
) -> CandleSemantics:
    return CandleSemantics(
        key,
        open,
        high,
        low,
        close,
        volume,
        volume_unit,
        finalized,
        source_time,
        publication_time,
        quality_flags,
    )


def test_semantic_fingerprint_has_fixed_bytes_and_revision() -> None:
    expected_bytes = (
        b'["scryntic-candle-semantic-v1",["fake","spot","BTC-USDT"],'
        b'1700000000000000000,60000000000,"100.1","101.25","99.9",'
        b'"100.75","12.34","BTC",false,[1700000000123,"ms"],null,[]]'
    )
    expected_revision = (
        "sha256:57cb824df731e0c154a3dabd0faca58453a48606b845b7fe7b05c69714de6929"
    )

    semantics = semantic_value()

    assert semantics.canonical_bytes() == expected_bytes
    assert semantics.revision() == expected_revision


def test_every_semantic_field_changes_the_revision() -> None:
    original = semantic_value(
        publication_time=SourceTime(1_700_000_000_456, TimeUnit.MICROSECOND),
        quality_flags=("flag-a",),
    )
    source_time = original.source_time
    publication_time = original.publication_time
    assert source_time is not None
    assert publication_time is not None
    changed_keys = [
        replace(original.key, instrument=InstrumentId("other", "spot", "BTC-USDT")),
        replace(original.key, instrument=InstrumentId("fake", "linear", "BTC-USDT")),
        replace(original.key, instrument=InstrumentId("fake", "spot", "ETH-USDT")),
        replace(original.key, start_ns=original.key.start_ns + 1),
        replace(original.key, interval_ns=original.key.interval_ns + 1),
    ]
    changed = [
        *(replace(original, key=key) for key in changed_keys),
        replace(original, open="100.2"),
        replace(original, high="101.3"),
        replace(original, low="99.8"),
        replace(original, close="100.8"),
        replace(original, volume="12.35"),
        replace(original, volume_unit="USDT"),
        replace(original, finalized=True),
        replace(
            original,
            source_time=SourceTime(source_time.value + 1, TimeUnit.MILLISECOND),
        ),
        replace(
            original,
            source_time=SourceTime(source_time.value, TimeUnit.MICROSECOND),
        ),
        replace(original, source_time=None),
        replace(
            original,
            publication_time=SourceTime(
                publication_time.value + 1, TimeUnit.MICROSECOND
            ),
        ),
        replace(
            original,
            publication_time=SourceTime(publication_time.value, TimeUnit.NANOSECOND),
        ),
        replace(original, publication_time=None),
        replace(original, quality_flags=("flag-b",)),
    ]

    assert len({value.revision() for value in changed}) == len(changed)
    assert all(value.revision() != original.revision() for value in changed)


@pytest.mark.parametrize(
    "change",
    [
        lambda semantics, value: replace(semantics, open=value),
        lambda semantics, value: replace(semantics, high=value),
        lambda semantics, value: replace(semantics, low=value),
        lambda semantics, value: replace(semantics, close=value),
        lambda semantics, value: replace(semantics, volume=value),
    ],
)
@pytest.mark.parametrize("value", ["12.3400", "01", "-1", "1E+2", "NaN"])
def test_semantics_reject_noncanonical_decimal_text(
    change: Callable[[CandleSemantics, str], CandleSemantics], value: str
) -> None:
    with pytest.raises(ValueError):
        change(semantic_value(), value)


@pytest.mark.parametrize(
    "flags",
    [("flag-b", "flag-a"), ("flag-a", "flag-a"), ("bad flag",)],
)
def test_semantics_require_sorted_unique_identifier_flags(
    flags: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError):
        semantic_value(quality_flags=flags)


def parsed_candle(record_payload: bytes | None = None) -> ParsedFakeCandle:
    record = raw_record(
        payload=fake_candle_payload() if record_payload is None else record_payload
    )
    result = inspect_fake_candle(record)
    assert isinstance(result, ParsedFakeCandle)
    return result


def test_pure_normalizer_is_context_independent_and_preserves_evidence() -> None:
    record = raw_record(
        payload=fake_candle_payload(
            publication_time={"value": 1_700_000_000_456, "unit": "us"}
        )
    )
    parsed = parsed_candle(record.envelope.payload)
    metadata = instrument()

    with localcontext() as context:
        context.prec = 2
        first = normalize_parsed_candle(
            record, parsed, metadata, normalized_at_ns=1_700_000_002_000_000_000
        )
    with localcontext() as context:
        context.prec = 50
        second = normalize_parsed_candle(
            record, parsed, metadata, normalized_at_ns=1_700_000_002_000_000_000
        )

    assert first == second
    assert isinstance(first, CandleNormalization)
    assert first.input_schema == FAKE_CANDLE_SCHEMA
    assert first.instrument_schema == metadata.schema
    assert first.instrument_revision == metadata.revision
    assert first.candle.raw_record == record.identity
    assert first.candle.receipt == record.envelope.receipt
    assert first.candle.source_time == record.envelope.source_time
    assert first.candle.publication_time == parsed.publication_time
    assert first.candle.normalized_at_ns == 1_700_000_002_000_000_000
    assert first.candle.schema == CANDLE_SCHEMA
    assert first.candle.quality_flags == ()
    assert first.candle.normalizer_version == NORMALIZER_VERSION
    assert first.semantics == CandleSemantics(
        CandleKey(INSTRUMENT_ID, parsed.start_ns, parsed.interval_ns),
        "100.1",
        "101.25",
        "99.9",
        "100.75",
        "12.34",
        "BTC",
        False,
        record.envelope.source_time,
        parsed.publication_time,
        (),
    )


def test_equivalent_decimal_encodings_share_a_revision() -> None:
    record = raw_record()
    parsed = parsed_candle()
    first = normalize_parsed_candle(record, parsed, instrument(), normalized_at_ns=1)
    second = normalize_parsed_candle(
        record,
        replace(
            parsed,
            open=Decimal("100.1000"),
            low=Decimal("99.9000"),
            volume=Decimal("00012.34000"),
        ),
        instrument(),
        normalized_at_ns=1,
    )
    zero = replace(parsed, volume=Decimal("0"))
    signed_scaled_zero = replace(parsed, volume=Decimal("-0.000"))
    third = normalize_parsed_candle(record, zero, instrument(), normalized_at_ns=1)
    fourth = normalize_parsed_candle(
        record, signed_scaled_zero, instrument(), normalized_at_ns=1
    )

    assert isinstance(first, CandleNormalization)
    assert isinstance(second, CandleNormalization)
    assert isinstance(third, CandleNormalization)
    assert isinstance(fourth, CandleNormalization)
    assert first.candle.revision == second.candle.revision
    assert third.candle.revision == fourth.candle.revision


def test_only_semantic_provenance_changes_leave_revision_unchanged() -> None:
    parsed = parsed_candle()
    base_record = raw_record()
    changed_receipt = ClockSample(
        base_record.envelope.receipt.wall_time_ns + 1,
        99,
        "session-b",
        TimeQuality("clock-b", "degraded"),
    )
    records = [
        base_record,
        raw_record(offset=9, epoch="epoch-b"),
        raw_record(receipt=changed_receipt),
    ]
    values = [
        normalize_parsed_candle(
            record,
            parsed,
            replace(instrument(), revision=revision),
            normalized_at_ns=normalized_at_ns,
        )
        for record, revision, normalized_at_ns in zip(
            records,
            ("instrument-r1", "instrument-r2", "instrument-r3"),
            (1, 2, 3),
            strict=True,
        )
    ]

    revisions: set[str] = set()
    for value in values:
        assert isinstance(value, CandleNormalization)
        revisions.add(value.candle.revision)
    assert len(revisions) == 1

    original = values[0]
    assert isinstance(original, CandleNormalization)
    changed_versions = replace(
        original,
        candle=replace(original.candle, normalizer_version="f06.fake_candle.v2"),
        input_schema=SchemaRef("fake_candle", Version(2, 0)),
        instrument_schema=SchemaRef("instrument", Version(2, 0)),
    )
    assert changed_versions.candle.revision == original.candle.revision


def test_volume_unit_changes_revision() -> None:
    record = raw_record()
    parsed = parsed_candle()
    base = normalize_parsed_candle(record, parsed, instrument(), normalized_at_ns=1)
    changed = normalize_parsed_candle(
        record,
        parsed,
        replace(instrument(), volume_unit="USDT"),
        normalized_at_ns=1,
    )

    assert isinstance(base, CandleNormalization)
    assert isinstance(changed, CandleNormalization)
    assert base.candle.revision != changed.candle.revision


def test_invalid_caller_time_and_identity_mismatch_propagate() -> None:
    parsed = parsed_candle()
    with pytest.raises(TypeError, match="integer"):
        normalize_parsed_candle(
            raw_record(), parsed, instrument(), normalized_at_ns=True
        )
    with pytest.raises(ValueError, match="identity"):
        normalize_parsed_candle(
            raw_record(),
            parsed,
            instrument(identity=InstrumentId("fake-venue", "spot", "ETH-USDT")),
            normalized_at_ns=1,
        )


def test_invalid_parsed_field_type_propagates() -> None:
    parsed = replace(parsed_candle(), open="100.1")  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="Decimal"):
        normalize_parsed_candle(raw_record(), parsed, instrument(), normalized_at_ns=1)


def test_supported_domain_failure_becomes_provenanced_rejection() -> None:
    record = raw_record()
    parsed = replace(parsed_candle(), high=Decimal("1"))
    metadata = instrument()

    result = normalize_parsed_candle(record, parsed, metadata, normalized_at_ns=7)

    assert result == NormalizationRejection(
        raw_record=record.identity,
        raw_sha256=record.envelope.content_sha256,
        normalizer_version=NORMALIZER_VERSION,
        code=RejectionCode.INVALID_DOMAIN_VALUE,
        field=None,
        input_schema=FAKE_CANDLE_SCHEMA,
        instrument_schema=metadata.schema,
        instrument_revision=metadata.revision,
        output_schema=CANDLE_SCHEMA,
        normalized_at_ns=7,
    )


def test_normalization_wrapper_rejects_semantic_mismatch() -> None:
    result = normalize_parsed_candle(
        raw_record(), parsed_candle(), instrument(), normalized_at_ns=1
    )
    assert isinstance(result, CandleNormalization)

    with pytest.raises(ValueError, match="semantic"):
        replace(result, candle=replace(result.candle, revision="sha256:" + "0" * 64))
    with pytest.raises(ValueError, match="semantic"):
        replace(result, candle=replace(result.candle, volume_unit="USDT"))


def test_rejection_validates_digest_version_and_instrument_provenance() -> None:
    result = rejection(b"{", RejectionCode.INVALID_JSON)

    with pytest.raises(ValueError):
        replace(result, raw_sha256="not-a-digest")
    with pytest.raises(ValueError):
        replace(result, normalizer_version="f06.fake_candle.v2")
    with pytest.raises(ValueError):
        replace(result, instrument_schema=instrument().schema)
    with pytest.raises(ValueError):
        replace(result, instrument_revision="instrument-r1")
