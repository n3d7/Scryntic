"""Boundary examples: precision, identity, versioning and bounded evidence."""

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, localcontext
from typing import cast

import pytest

from scryntic.domain.identity import EntityId, InstrumentId, SchemaRef, Version
from scryntic.domain.market import CANDLE_SCHEMA, Candle, CandleKey, Instrument
from scryntic.domain.raw import IngestionId, RawEnvelope, RawRecord
from scryntic.domain.time import ClockSample, SourceTime, TimeQuality, TimeUnit


def sample() -> ClockSample:
    return ClockSample(1_000_000_000, 50, "boot-a", TimeQuality("epoch-a"))


def envelope(limit: int = 3) -> RawEnvelope:
    return RawEnvelope(
        source="fake",
        stream="candles",
        channel="public",
        adapter_version="1.0",
        receipt=sample(),
        payload=b"abc",
        payload_limit=limit,
        source_time=SourceTime(1234, TimeUnit.MILLISECOND),
    )


def candle() -> Candle:
    return Candle(
        key=CandleKey(InstrumentId("venue", "spot", "BTC-USDT"), 0, 60_000_000_000),
        open=Decimal("0.1000000000000000000001"),
        high=Decimal("0.3"),
        low=Decimal("0.1"),
        close=Decimal("0.2"),
        volume=Decimal("12.3400"),
        volume_unit="BTC",
        finalized=True,
        revision="r1",
        normalizer_version="1.0",
        raw_record=IngestionId("producer", "epoch", 1),
        receipt=sample(),
        normalized_at_ns=2_000_000_000,
        source_time=None,
        publication_time=None,
    )


def test_exact_decimals_are_not_rounded_by_current_context() -> None:
    with localcontext() as context:
        context.prec = 4
        value = candle()
        instrument = Instrument(
            InstrumentId("venue", "perpetual", "BTC-USDT"),
            "BTC",
            "USDT",
            Decimal("0.0100"),
            Decimal("0.0001"),
            "BTC",
            "meta-1",
            settlement_asset="USDT",
            contract_multiplier=Decimal("0.00100"),
        )
    assert str(value.open) == "0.1000000000000000000001"
    assert str(value.volume) == "12.3400"
    assert value == replace(value, volume=Decimal("12.34"))
    assert str(instrument.contract_multiplier) == "0.00100"
    with pytest.raises(FrozenInstanceError):
        value.volume = Decimal("7")  # type: ignore[misc]  # Exercise frozen runtime guard.


@pytest.mark.parametrize(
    "bad", [Decimal("NaN"), Decimal("Infinity"), Decimal("-1"), 0.1]
)
def test_invalid_market_values_are_rejected(bad: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        replace(candle(), volume=cast(Decimal, bad))


def test_candle_rejects_inconsistent_range_and_interval() -> None:
    with pytest.raises(ValueError):
        replace(candle(), high=Decimal("0.15"))
    with pytest.raises(ValueError):
        replace(candle().key, interval_ns=0)


def test_market_category_entity_kind_and_revision_are_distinct() -> None:
    spot = InstrumentId("venue", "spot", "BTC-USDT")
    assert spot != replace(spot, category="perpetual")
    assert spot != replace(spot, venue="another")
    document = EntityId("document", "publisher", "123")
    assert document != replace(document, kind="chain-transaction")
    original = candle()
    correction = replace(original, revision="r2", close=Decimal("0.25"))
    assert correction.key == original.key
    assert correction != original
    assert replace(original.raw_record, offset=2) != original.raw_record


def test_schema_compatibility_is_explicit_and_namespaced() -> None:
    reader = SchemaRef("candle", Version(1, 2))
    reader.require_readable(SchemaRef("candle", Version(1, 0)))
    for incoming in (
        SchemaRef("candle", Version(2, 0)),
        SchemaRef("candle", Version(1, 3)),
        SchemaRef("trade", Version(1, 0)),
    ):
        with pytest.raises(ValueError, match="Unsupported schema"):
            reader.require_readable(incoming)
    assert candle().schema == CANDLE_SCHEMA
    with pytest.raises(ValueError):
        replace(candle(), schema=SchemaRef("candle", Version(2, 0)))
    with pytest.raises(ValueError):
        Version(0, 0)


def test_raw_payload_limit_hash_and_ingestion_identity() -> None:
    raw = envelope()
    assert raw.payload == b"abc"
    assert (
        raw.content_sha256
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    assert "abc" not in repr(raw)
    assert raw.source_time == SourceTime(1234, TimeUnit.MILLISECOND)
    assert raw.subject is None  # Non-market data needs no invented instrument.
    first = RawRecord(IngestionId("producer", "epoch", 1), raw)
    second = replace(first, identity=IngestionId("producer", "epoch", 2))
    assert first != second
    assert first.envelope == second.envelope
    with pytest.raises(ValueError, match="payload"):
        envelope(limit=2)
    with pytest.raises(TypeError):
        replace(raw, payload=cast(bytes, bytearray(b"abc")), payload_limit=3)
    with pytest.raises(ValueError):
        replace(raw, channel="https://user:password@example.test", payload_limit=3)


def test_clock_quality_and_session_are_evidence_not_global_order() -> None:
    before = sample()
    after = replace(before, wall_time_ns=before.wall_time_ns - 100, monotonic_ns=100)
    assert after.wall_time_ns < before.wall_time_ns
    assert after.monotonic_ns > before.monotonic_ns
    assert before != replace(before, session_id="boot-b")
    assert before.quality.status == "unknown"
    with pytest.raises(ValueError):
        TimeQuality("epoch-a", status="healthy")  # No offset/uncertainty evidence.
    with pytest.raises(ValueError):
        replace(before, monotonic_ns=-1)
