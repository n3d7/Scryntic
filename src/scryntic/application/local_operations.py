"""Pure bounded local workflow requests and shared interface metadata."""

import re
from dataclasses import dataclass
from typing import Literal, cast

from scryntic.application.operations import Field, Operation, Workflow
from scryntic.domain.validation import identifier, integer

SourceProfile = Literal["fixture", "bybit"]
Category = Literal["spot", "linear", "inverse"]
_STEP = 60_000_000_000


def _selection(profile: str, category: str, symbol: str) -> None:
    if profile not in ("fixture", "bybit") or category not in (
        "spot",
        "linear",
        "inverse",
    ):
        raise ValueError("Unsupported source profile")
    if not isinstance(symbol, str) or re.fullmatch(r"[A-Z0-9]{1,32}", symbol) is None:
        raise ValueError("Unsupported instrument symbol")
    if profile == "fixture" and (category != "spot" or symbol != "BTCUSDT"):
        raise ValueError("Fixture collection has one explicit instrument")


def _seconds(value: int) -> None:
    integer(value, 1)
    if value > 300:
        raise ValueError("Finite operation exceeds its bounded duration")


@dataclass(frozen=True, slots=True)
class ConfigValidateRequest:
    @property
    def operation(self) -> Operation:
        return Operation.CONFIG_VALIDATE


@dataclass(frozen=True, slots=True)
class SourcesListRequest:
    @property
    def operation(self) -> Operation:
        return Operation.SOURCES_LIST


@dataclass(frozen=True, slots=True)
class SourceInspectRequest:
    @property
    def operation(self) -> Operation:
        return Operation.SOURCES_INSPECT

    profile: SourceProfile

    def __post_init__(self) -> None:
        if self.profile not in ("fixture", "bybit"):
            raise ValueError("Unknown built-in source profile")


@dataclass(frozen=True, slots=True)
class StatusRequest:
    @property
    def operation(self) -> Operation:
        return Operation.STATUS


@dataclass(frozen=True, slots=True)
class CollectRequest:
    @property
    def operation(self) -> Operation:
        return Operation.COLLECT

    profile: SourceProfile = "fixture"
    category: Category = "spot"
    symbol: str = "BTCUSDT"
    producer: str = "cli"
    epoch: str = "cli-v1"
    seconds: int = 1

    def __post_init__(self) -> None:
        _selection(self.profile, self.category, self.symbol)
        identifier(self.producer)
        identifier(self.epoch)
        _seconds(self.seconds)


@dataclass(frozen=True, slots=True)
class BackfillRequest:
    @property
    def operation(self) -> Operation:
        return Operation.BACKFILL

    start_ns: int
    end_ns: int
    profile: Literal["bybit"] = "bybit"
    category: Category = "spot"
    symbol: str = "BTCUSDT"
    producer: str = "cli"
    epoch: str = "cli-v1"
    seconds: int = 30

    def __post_init__(self) -> None:
        _selection(self.profile, self.category, self.symbol)
        if self.profile != "bybit":
            raise ValueError("Finite recovery supports the Bybit candle contract")
        identifier(self.producer)
        identifier(self.epoch)
        _seconds(self.seconds)
        integer(self.start_ns, 0)
        integer(self.end_ns, 1)
        if (
            self.end_ns > 2**63 - 1
            or self.start_ns % _STEP
            or self.end_ns % _STEP
            or not 0 < self.end_ns - self.start_ns <= 256 * _STEP
        ):
            raise ValueError("Backfill needs 1–256 complete one-minute candle slots")


_PROFILE = Field("profile", "Source profile", "fixture", choices=("fixture", "bybit"))
_SOURCE_FIELDS = (
    Field("category", "Market category", "spot", choices=("spot", "linear", "inverse")),
    Field("symbol", "Instrument symbol", "BTCUSDT"),
    Field("producer", "Collector producer", "cli"),
    Field("epoch", "Collector epoch", "cli-v1"),
)


def _collect(values: dict[str, str]) -> CollectRequest:
    return CollectRequest(
        cast(SourceProfile, values["profile"]),
        cast(Category, values["category"]),
        values["symbol"],
        values["producer"],
        values["epoch"],
        int(values["seconds"]),
    )


def _backfill(values: dict[str, str]) -> BackfillRequest:
    return BackfillRequest(
        int(values["start_ns"]),
        int(values["end_ns"]),
        cast(Literal["bybit"], values["profile"]),
        cast(Category, values["category"]),
        values["symbol"],
        values["producer"],
        values["epoch"],
        int(values["seconds"]),
    )


WORKFLOWS = (
    Workflow(
        Operation.CONFIG_VALIDATE,
        "Validate protected configuration",
        "Configuration",
        (),
        lambda _: ConfigValidateRequest(),
    ),
    Workflow(
        Operation.SOURCES_LIST,
        "List built-in sources",
        "Collection",
        (),
        lambda _: SourcesListRequest(),
    ),
    Workflow(
        Operation.SOURCES_INSPECT,
        "Inspect source capabilities",
        "Collection",
        (_PROFILE,),
        lambda v: SourceInspectRequest(cast(SourceProfile, v["profile"])),
    ),
    Workflow(
        Operation.COLLECT,
        "Collect for a bounded duration",
        "Collection",
        (_PROFILE, *_SOURCE_FIELDS, Field("seconds", "Duration (1–300 seconds)", "1")),
        _collect,
    ),
    Workflow(
        Operation.BACKFILL,
        "Backfill an explicit candle range",
        "Collection",
        (
            Field("profile", "Source profile", "bybit", choices=("bybit",)),
            *_SOURCE_FIELDS,
            Field(
                "start_ns", "Inclusive start (UTC ns, minute aligned)", required=True
            ),
            Field("end_ns", "Exclusive end (UTC ns, minute aligned)", required=True),
            Field("seconds", "Deadline (1–300 seconds)", "30"),
        ),
        _backfill,
    ),
    Workflow(
        Operation.STATUS,
        "Inspect offline installation status",
        "Overview",
        (),
        lambda _: StatusRequest(),
    ),
)
