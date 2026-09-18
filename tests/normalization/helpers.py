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
from scryntic.normalization.candle import (
    CandleNormalization,
    NormalizationRejection,
    ParsedFakeCandle,
    inspect_fake_candle,
    normalize_parsed_candle,
)

INSTRUMENT_ID = InstrumentId("fake-venue", "spot", "BTC-USDT")
DEFAULT_RECEIPT = ClockSample(
    1_700_000_001_000_000_000,
    42,
    "session-a",
    TimeQuality("clock-a"),
)
DEFAULT_SOURCE_TIME = SourceTime(1_700_000_000_000, TimeUnit.MILLISECOND)
NORMALIZED_AT_NS = 1_700_000_002_000_000_000


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


def normalization(
    record: RawRecord, *, normalized_at_ns: int = NORMALIZED_AT_NS
) -> CandleNormalization | NormalizationRejection:
    inspected = inspect_fake_candle(record)
    if isinstance(inspected, NormalizationRejection):
        return inspected
    assert isinstance(inspected, ParsedFakeCandle)
    return normalize_parsed_candle(
        record, inspected, instrument(), normalized_at_ns=normalized_at_ns
    )


class FixedRawReader:
    """Retained committed inputs implementing the public F05 reader contract."""

    def __init__(self, records: tuple[RawRecord, ...]) -> None:
        self.records = records

    def records_after(self, offset: int, *, limit: int) -> tuple[RawRecord, ...]:
        return tuple(
            record for record in self.records if record.identity.offset > offset
        )[:limit]


def recovery_records() -> tuple[RawRecord, ...]:
    """Fixed accepted, rejected, duplicate and unsupported-schema inputs."""
    return (
        raw_record(offset=2),
        raw_record(offset=5, epoch="epoch-b", payload=b"not json"),
        raw_record(offset=9, epoch="epoch-c"),
        raw_record(
            offset=12, payload=fake_candle_payload(schema=("future_candle", 2, 0))
        ),
    )
