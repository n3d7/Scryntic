"""UTC POSIX wall time and session-scoped monotonic evidence, without reading clocks."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from scryntic.domain.validation import identifier, integer


class TimeUnit(StrEnum):
    SECOND = "s"
    MILLISECOND = "ms"
    MICROSECOND = "us"
    NANOSECOND = "ns"


@dataclass(frozen=True, slots=True)
class SourceTime:
    value: int
    unit: TimeUnit

    def __post_init__(self) -> None:
        integer(self.value)
        if not isinstance(self.unit, TimeUnit):
            raise TypeError("Expected an explicit source timestamp unit")


@dataclass(frozen=True, slots=True)
class TimeQuality:
    epoch: str
    status: Literal["unknown", "degraded", "healthy"] = "unknown"
    offset_ns: int | None = None
    uncertainty_ns: int | None = None
    evidence_age_ns: int | None = None

    def __post_init__(self) -> None:
        identifier(self.epoch)
        if self.status not in ("unknown", "degraded", "healthy"):
            raise ValueError("Unknown clock quality status")
        if self.offset_ns is not None:
            integer(self.offset_ns)
        for value in (self.uncertainty_ns, self.evidence_age_ns):
            if value is not None:
                integer(value, 0)
        if self.status == "healthy" and None in (
            self.offset_ns,
            self.uncertainty_ns,
            self.evidence_age_ns,
        ):
            raise ValueError("Healthy clock requires bounded evidence")


@dataclass(frozen=True, slots=True)
class ClockSample:
    wall_time_ns: int
    monotonic_ns: int
    session_id: str
    quality: TimeQuality

    def __post_init__(self) -> None:
        integer(self.wall_time_ns)
        integer(self.monotonic_ns, 0)
        identifier(self.session_id)
