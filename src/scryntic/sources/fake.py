"""Deterministic bounded candle history accepted by the real F06 parser."""

from __future__ import annotations

import json
from decimal import Decimal

from scryntic.application.sources import (
    HistoryRequest,
    RawPage,
    SourceCapability,
    SourceDescriptor,
    SourceOperation,
)
from scryntic.domain.identity import InstrumentId, SchemaRef, Version
from scryntic.domain.market import Instrument
from scryntic.domain.raw import RawEnvelope
from scryntic.domain.time import ClockSample, SourceTime, TimeQuality, TimeUnit

FAKE_CANDLE_SCHEMA = SchemaRef("fake_candle", Version(1, 0))
FAKE_INSTRUMENT_ID = InstrumentId("fake-venue", "spot", "BTC-USDT")
FIRST_START_NS = 1_700_000_000_000_000_000
INTERVAL_NS = 60_000_000_000
END_NS = FIRST_START_NS + 2 * INTERVAL_NS
MAX_PAYLOAD_BYTES = 8_192


def fake_instrument() -> Instrument:
    return Instrument(
        identity=FAKE_INSTRUMENT_ID,
        base_asset="BTC",
        quote_asset="USDT",
        price_increment=Decimal("0.01"),
        quantity_increment=Decimal("0.0001"),
        volume_unit="BTC",
        revision="fake-instrument-r1",
    )


class DeterministicCandleSource:
    """Two fixed finalized candles with bounded historical paging."""

    descriptor = SourceDescriptor(
        source_id="f09-fake-source",
        adapter_version="1.0",
        capabilities=(
            SourceCapability(
                schema=FAKE_CANDLE_SCHEMA,
                subject_kind="instrument",
                category="spot",
                operations=frozenset({SourceOperation.HISTORY}),
                sequencing="source-sequence",
                recovery="backfill",
                history_start_ns=FIRST_START_NS,
                history_end_ns=END_NS,
            ),
        ),
        max_payload_bytes=MAX_PAYLOAD_BYTES,
        max_page_records=2,
    )

    def __init__(self) -> None:
        self._closed = False

    @staticmethod
    def _envelope(index: int) -> RawEnvelope:
        start_ns = FIRST_START_NS + index * INTERVAL_NS
        bars = (
            ("100.10", "101.25", "99.90", "100.75", "12.3400"),
            ("100.75", "102.00", "100.50", "101.25", "9.0000"),
        )
        open_, high, low, close, volume = bars[index]
        payload = json.dumps(
            {
                "schema": {"name": "fake_candle", "major": 1, "minor": 0},
                "start_ns": start_ns,
                "interval_ns": INTERVAL_NS,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "finalized": True,
                "publication_time": None,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return RawEnvelope(
            source="f09-fake-source",
            stream="candles",
            channel="public",
            adapter_version="1.0",
            receipt=ClockSample(
                start_ns + INTERVAL_NS + 1_000_000_000,
                42 + index,
                "f09-fake-session",
                TimeQuality("f09-fake-clock"),
            ),
            payload=payload,
            payload_limit=MAX_PAYLOAD_BYTES,
            subject=FAKE_INSTRUMENT_ID,
            source_time=SourceTime(start_ns, TimeUnit.NANOSECOND),
            source_event_id=f"fake-candle-{index}",
            source_sequence=index,
        )

    async def fetch(self, request: HistoryRequest) -> RawPage:
        if self._closed:
            raise RuntimeError("Fake source is closed")
        self.descriptor.require(
            request.stream.schema, SourceOperation.HISTORY, request.stream.subject
        )
        if request.page_size > self.descriptor.max_page_records:
            raise ValueError("Fake source page exceeds declared limit")
        if request.start_ns < FIRST_START_NS or request.end_ns > END_NS:
            raise ValueError("Fake source range is unsupported")
        if request.cursor is None:
            start = 0
        elif request.cursor in (b"1", b"2"):
            start = int(request.cursor)
        else:
            raise ValueError("Unknown fake source cursor")
        selected = [
            index
            for index in range(2)
            if request.start_ns <= FIRST_START_NS + index * INTERVAL_NS < request.end_ns
        ]
        page = selected[start : start + request.page_size]
        next_offset = start + len(page)
        next_cursor = (
            str(next_offset).encode("ascii") if next_offset < len(selected) else None
        )
        return RawPage(
            tuple(self._envelope(index) for index in page),
            next_cursor,
            record_limit=request.page_size,
        )

    async def close(self) -> None:
        self._closed = True
