"""Closed forecast-ledger requests shared by CLI and TUI."""

from dataclasses import dataclass

from scryntic.application.operations import Field, Operation, Workflow
from scryntic.domain.validation import digest, identifier, integer


@dataclass(frozen=True, slots=True)
class ForecastStatusRequest:
    limit: int = 50
    offset: int = 0

    def __post_init__(self) -> None:
        integer(self.limit, 1)
        integer(self.offset, 0)
        if self.limit > 100 or self.offset > 10000:
            raise ValueError("PI04 page bound")

    @property
    def operation(self) -> Operation:
        return Operation.FORECASTS_STATUS


@dataclass(frozen=True, slots=True)
class ForecastInspectRequest:
    slot_sha256: str
    as_of_ns: int = 0

    def __post_init__(self) -> None:
        digest(self.slot_sha256)
        integer(self.as_of_ns, 0)

    @property
    def operation(self) -> Operation:
        return Operation.FORECASTS_INSPECT


@dataclass(frozen=True, slots=True)
class ForecastCompareRequest:
    left: str
    right: str
    as_of_ns: int = 0
    view: str = "revised"

    def __post_init__(self) -> None:
        identifier(self.left)
        identifier(self.right)
        integer(self.as_of_ns, 0)
        if self.left == self.right or self.view not in ("first-available", "revised"):
            raise ValueError("Invalid PI04 comparison")

    @property
    def operation(self) -> Operation:
        return Operation.FORECASTS_COMPARE


WORKFLOWS: tuple[Workflow, ...] = (
    Workflow(
        Operation.FORECASTS_STATUS,
        "Forecast evidence and maturity",
        "Forecasts",
        (Field("limit", "Page size", "50"), Field("offset", "Page offset", "0")),
        lambda v: ForecastStatusRequest(int(v["limit"]), int(v["offset"])),
    ),
    Workflow(
        Operation.FORECASTS_INSPECT,
        "Inspect a forecast and its score vintages",
        "Forecasts",
        (
            Field("slot_sha256", "Forecast slot SHA256", required=True),
            Field("as_of_ns", "Score as-of (UTC ns; 0 = now)", "0"),
        ),
        lambda v: ForecastInspectRequest(v["slot_sha256"], int(v["as_of_ns"])),
    ),
    Workflow(
        Operation.FORECASTS_COMPARE,
        "Compare common matured forecast pairs",
        "Forecasts",
        (
            Field("left", "First stream", required=True),
            Field("right", "Second stream", required=True),
            Field("as_of_ns", "Score as-of (UTC ns; 0 = now)", "0"),
            Field(
                "view",
                "Outcome vintage",
                "revised",
                choices=("first-available", "revised"),
            ),
        ),
        lambda v: ForecastCompareRequest(
            v["left"], v["right"], int(v["as_of_ns"]), v["view"]
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class ForecastRunRequest:
    wait_seconds: int = 0

    def __post_init__(self) -> None:
        integer(self.wait_seconds, 0)
        if self.wait_seconds > 3600:
            raise ValueError("PI04 finite schedule bound")

    @property
    def operation(self) -> Operation:
        return Operation.FORECASTS_RUN


@dataclass(frozen=True, slots=True)
class ForecastMatureRequest:
    limit: int = 4

    def __post_init__(self) -> None:
        integer(self.limit, 1)
        if self.limit > 16:
            raise ValueError("PI04 maturation bound")

    @property
    def operation(self) -> Operation:
        return Operation.FORECASTS_MATURE


WORKFLOWS += (
    Workflow(
        Operation.FORECASTS_RUN,
        "Run one scheduled shadow origin",
        "Forecasts",
        (Field("wait_seconds", "Maximum wait for next origin (seconds)", "0"),),
        lambda v: ForecastRunRequest(int(v["wait_seconds"])),
    ),
    Workflow(
        Operation.FORECASTS_MATURE,
        "Append available outcome and score vintages",
        "Forecasts",
        (Field("limit", "Maximum forecasts to inspect", "4"),),
        lambda v: ForecastMatureRequest(int(v["limit"])),
    ),
)
