"""Instrument metadata and candle observations; prices/quantities stay exact."""

from dataclasses import dataclass
from decimal import Decimal

from scryntic.domain.identity import InstrumentId, SchemaRef, Version
from scryntic.domain.raw import IngestionId
from scryntic.domain.time import ClockSample, SourceTime
from scryntic.domain.validation import decimal, identifier, immutable_tuple, integer

INSTRUMENT_SCHEMA = SchemaRef("instrument", Version(1, 0))
CANDLE_SCHEMA = SchemaRef("candle", Version(1, 0))


@dataclass(frozen=True, slots=True)
class Instrument:
    identity: InstrumentId
    base_asset: str
    quote_asset: str
    price_increment: Decimal
    quantity_increment: Decimal
    volume_unit: str
    revision: str
    settlement_asset: str | None = None
    contract_multiplier: Decimal | None = None
    expiry_ns: int | None = None
    schema: SchemaRef = INSTRUMENT_SCHEMA

    def __post_init__(self) -> None:
        INSTRUMENT_SCHEMA.require_readable(self.schema)
        for value in (
            self.base_asset,
            self.quote_asset,
            self.volume_unit,
            self.revision,
        ):
            identifier(value)
        decimal(self.price_increment, positive=True)
        decimal(self.quantity_increment, positive=True)
        if self.settlement_asset is not None:
            identifier(self.settlement_asset)
        if self.contract_multiplier is not None:
            decimal(self.contract_multiplier, positive=True)
        if self.expiry_ns is not None:
            integer(self.expiry_ns)


@dataclass(frozen=True, slots=True)
class CandleKey:
    instrument: InstrumentId
    start_ns: int
    interval_ns: int

    def __post_init__(self) -> None:
        integer(self.start_ns)
        integer(self.interval_ns, 1)


@dataclass(frozen=True, slots=True, kw_only=True)
class Candle:
    key: CandleKey
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    volume_unit: str
    finalized: bool
    revision: str
    normalizer_version: str
    raw_record: IngestionId
    receipt: ClockSample
    normalized_at_ns: int
    source_time: SourceTime | None
    publication_time: SourceTime | None
    quality_flags: tuple[str, ...] = ()
    schema: SchemaRef = CANDLE_SCHEMA

    def __post_init__(self) -> None:
        CANDLE_SCHEMA.require_readable(self.schema)
        for value in (self.open, self.high, self.low, self.close, self.volume):
            decimal(value)
        if (
            not self.low
            <= min(self.open, self.close)
            <= max(self.open, self.close)
            <= self.high
        ):
            raise ValueError("Inconsistent candle price range")
        if type(self.finalized) is not bool:
            raise TypeError("Expected explicit candle finality")
        integer(self.normalized_at_ns)
        immutable_tuple(self.quality_flags, 32)
        for name in (
            self.volume_unit,
            self.revision,
            self.normalizer_version,
            *self.quality_flags,
        ):
            identifier(name)
