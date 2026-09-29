"""Unprivileged clock evidence port; health policy is owned by the application."""

from dataclasses import dataclass
from typing import Protocol

from scryntic.domain.time import ClockSample
from scryntic.domain.validation import identifier, integer


class Clock(Protocol):
    def sample(self) -> ClockSample: ...


@dataclass(frozen=True, slots=True)
class ClockReading:
    """Host readings; boottime includes suspend, monotonic does not on Linux."""

    wall_time_ns: int
    monotonic_ns: int
    boottime_ns: int | None
    session_id: str
    capture_uncertainty_ns: int = 0

    def __post_init__(self) -> None:
        integer(self.wall_time_ns)
        integer(self.monotonic_ns, 0)
        if self.boottime_ns is not None:
            integer(self.boottime_ns, 0)
        integer(self.capture_uncertainty_ns, 0)
        identifier(self.session_id)


@dataclass(frozen=True, slots=True)
class SyncEvidence:
    """Last source measurement and current host error estimate, under host trust.

    Offset is system wall minus reference time. Uncertainty is a conservative
    radius about *uncorrected* wall time, including the absolute offset.
    A synchronized flag alone is never evidence of a numerical bound.
    """

    reference_time_ns: int
    offset_ns: int
    uncertainty_ns: int
    synchronized: bool

    def __post_init__(self) -> None:
        integer(self.reference_time_ns, 0)
        integer(self.offset_ns)
        integer(self.uncertainty_ns, 0)
        if self.uncertainty_ns < abs(self.offset_ns):
            raise ValueError("Clock uncertainty must include the offset")
        if type(self.synchronized) is not bool:
            raise TypeError("Expected explicit synchronization status")


class ClockReader(Protocol):
    def read(self) -> ClockReading: ...


class SynchronizationStatus(Protocol):
    """Unprivileged status read; unavailable/invalid reports return no evidence."""

    def read(self) -> SyncEvidence | None: ...
