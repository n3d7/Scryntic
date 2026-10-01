from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

from scryntic.application.sources import BYBIT_CANDLE_SCHEMA
from scryntic.domain.identity import InstrumentId
from scryntic.domain.market import Instrument
from scryntic.domain.raw import IngestionId, RawRecord
from scryntic.domain.time import ClockSample, TimeQuality
from scryntic.normalization.bybit_candle import inspect_bybit_candle
from scryntic.normalization.candle import (
    BYBIT_NORMALIZER_VERSION,
    CandleNormalization,
    ParsedFakeCandle,
    normalize_parsed_candle,
)
from scryntic.normalization.runner import Processed, process_next
from scryntic.sources.bybit import BybitHistoricalSource
from tests.sources.test_bybit import FixtureClient, fixture, request


class FixedClock:
    def sample(self) -> ClockSample:
        return ClockSample(
            1_700_000_220_000_000_000,
            1_000_000,
            "clock-session-1",
            TimeQuality("clock-epoch-1", "healthy", 0, 1_000_000, 0),
        )


def test_bybit_payload_replays_to_typed_candle_with_provider_schema_and_version() -> (
    None
):
    source = BybitHistoricalSource(
        client=FixtureClient({"/v5/market/kline": [fixture("klines-page-1.json")]}),
        clock=FixedClock(),
    )
    envelope = asyncio.run(source.fetch(request())).envelopes[0]
    record = RawRecord(IngestionId("test-producer", "test-epoch", 0), envelope)
    instrument = Instrument(
        identity=InstrumentId("bybit", "spot", "BTCUSDT"),
        base_asset="BTC",
        quote_asset="USDT",
        price_increment=Decimal("0.01"),
        quantity_increment=Decimal("0.000001"),
        volume_unit="BTC",
        revision="bybit-v5-fixture",
    )

    parsed = inspect_bybit_candle(record)

    assert isinstance(parsed, ParsedFakeCandle)
    assert parsed.schema == BYBIT_CANDLE_SCHEMA
    assert parsed.start_ns == 1_700_000_100_000_000_000
    assert str(parsed.open) == "102.00"
    assert str(parsed.volume) == "2.000"
    normalized = normalize_parsed_candle(
        record, parsed, instrument, normalized_at_ns=1_700_000_300_000_000_000
    )
    assert isinstance(normalized, CandleNormalization)
    assert normalized.input_schema == BYBIT_CANDLE_SCHEMA
    assert normalized.candle.volume_unit == "BTC"
    assert normalized.candle.normalizer_version == BYBIT_NORMALIZER_VERSION
    assert normalized.candle.finalized


def test_bybit_normalizer_rejects_row_metadata_disagreement() -> None:
    source = BybitHistoricalSource(
        client=FixtureClient({"/v5/market/kline": [fixture("klines-page-1.json")]}),
        clock=FixedClock(),
    )
    envelope = asyncio.run(source.fetch(request())).envelopes[0]
    payload = json.loads(envelope.payload)
    payload["symbol"] = "ETHUSDT"
    tampered = RawRecord(
        IngestionId("test-producer", "test-epoch", 0),
        replace(envelope, payload=json.dumps(payload).encode(), payload_limit=8192),
    )

    result = inspect_bybit_candle(tampered)

    assert not isinstance(result, ParsedFakeCandle)


def test_inverse_candle_uses_quote_volume_unit_from_recorded_metadata() -> None:
    client = FixtureClient(
        {
            "/v5/market/instruments-info": [fixture("inverse-instruments.json")],
            "/v5/market/kline": [fixture("inverse-klines.json")],
        }
    )
    source = BybitHistoricalSource(client=client, clock=FixedClock())
    instrument = asyncio.run(source.discover("inverse"))[0]
    envelope = asyncio.run(
        source.fetch(request(category="inverse", symbol="BTCUSD"))
    ).envelopes[0]
    record = RawRecord(IngestionId("test-producer", "test-epoch", 2), envelope)

    parsed = inspect_bybit_candle(record)
    assert isinstance(parsed, ParsedFakeCandle)
    normalized = normalize_parsed_candle(
        record, parsed, instrument, normalized_at_ns=1_700_000_300_000_000_000
    )

    assert isinstance(normalized, CandleNormalization)
    assert normalized.candle.volume == Decimal("200")
    assert normalized.candle.volume_unit == "USD"


def test_normalization_runner_dispatches_persisted_bybit_envelopes() -> None:
    source = BybitHistoricalSource(
        client=FixtureClient({"/v5/market/kline": [fixture("klines-page-1.json")]}),
        clock=FixedClock(),
    )
    envelope = asyncio.run(source.fetch(request())).envelopes[0]
    record = RawRecord(IngestionId("test-producer", "test-epoch", 1), envelope)
    instrument = Instrument(
        identity=InstrumentId("bybit", "spot", "BTCUSDT"),
        base_asset="BTC",
        quote_asset="USDT",
        price_increment=Decimal("0.01"),
        quantity_increment=Decimal("0.000001"),
        volume_unit="BTC",
        revision="bybit-v5-fixture",
    )

    class Reader:
        def records_after(self, offset: int, *, limit: int) -> tuple[RawRecord, ...]:
            assert offset == 0
            assert limit == 1
            return (record,)

    class Store:
        normalized: object

        def status(self) -> SimpleNamespace:
            return SimpleNamespace(
                checkpoint=None, barrier=None, producer="test-producer"
            )

        def process(
            self, _: RawRecord, normalized: object, *, expected_predecessor: object
        ) -> object:
            self.normalized = normalized
            assert expected_predecessor is None
            return normalized

    store = Store()
    result = process_next(
        Reader(),
        store,  # type: ignore[arg-type]
        {instrument.identity: instrument},
        normalized_at_ns=1_700_000_300_000_000_000,
    )

    assert isinstance(result, Processed)
    assert isinstance(store.normalized, CandleNormalization)
    assert store.normalized.candle.normalizer_version == BYBIT_NORMALIZER_VERSION
