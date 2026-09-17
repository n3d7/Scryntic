"""Explicit fixtures for normalization contracts."""

import json
import os
from decimal import Decimal
from pathlib import Path

from scryntic.configuration.paths import Installation
from scryntic.domain.identity import InstrumentId, SubjectId
from scryntic.domain.market import Instrument
from scryntic.domain.raw import IngestionId, RawEnvelope, RawRecord
from scryntic.domain.time import ClockSample, SourceTime, TimeQuality, TimeUnit

INSTRUMENT_ID = InstrumentId("fake-venue", "spot", "BTC-USDT")
DEFAULT_RECEIPT = ClockSample(
    1_700_000_001_000_000_000,
    42,
    "session-a",
    TimeQuality("clock-a"),
)
DEFAULT_SOURCE_TIME = SourceTime(1_700_000_000_000, TimeUnit.MILLISECOND)


def fake_candle_payload(
    *,
    schema: tuple[str, int, int] = ("fake_candle", 1, 0),
    start_ns: int = 1_700_000_000_000_000_000,
    interval_ns: int = 60_000_000_000,
    open: str = "100.10",
    high: str = "101.25",
    low: str = "99.90",
    close: str = "100.75",
    volume: str = "12.3400",
    finalized: bool = False,
    publication_time: dict[str, object] | None = None,
) -> bytes:
    name, major, minor = schema
    value = {
        "schema": {"name": name, "major": major, "minor": minor},
        "start_ns": start_ns,
        "interval_ns": interval_ns,
        "open": open,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "finalized": finalized,
        "publication_time": publication_time,
    }
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def installation(root: Path) -> Installation:
    config = root / "config"
    state = root / "state"
    runtime = root / "runtime"
    credentials = config / "credentials"
    for path in (config, state, runtime, credentials):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
    uid = os.geteuid()
    return Installation(config, state, runtime, credentials, uid, uid, False)


def instrument(*, identity: InstrumentId = INSTRUMENT_ID) -> Instrument:
    return Instrument(
        identity=identity,
        base_asset="BTC",
        quote_asset="USDT",
        price_increment=Decimal("0.01"),
        quantity_increment=Decimal("0.0001"),
        volume_unit="BTC",
        revision="instrument-r1",
    )


def envelope(
    *,
    payload: bytes = fake_candle_payload(),
    subject: SubjectId | None = INSTRUMENT_ID,
    receipt: ClockSample = DEFAULT_RECEIPT,
    source_time: SourceTime | None = DEFAULT_SOURCE_TIME,
) -> RawEnvelope:
    return RawEnvelope(
        source="fake-source",
        stream="candles",
        channel="public",
        adapter_version="1.0",
        receipt=receipt,
        payload=payload,
        payload_limit=max(8_192, len(payload)),
        subject=subject,
        source_time=source_time,
    )


def raw_record(
    *,
    offset: int = 1,
    epoch: str = "epoch-a",
    payload: bytes = fake_candle_payload(),
    subject: SubjectId | None = INSTRUMENT_ID,
    receipt: ClockSample = DEFAULT_RECEIPT,
    source_time: SourceTime | None = DEFAULT_SOURCE_TIME,
) -> RawRecord:
    return RawRecord(
        IngestionId("collector-a", epoch, offset),
        envelope(
            payload=payload,
            subject=subject,
            receipt=receipt,
            source_time=source_time,
        ),
    )
