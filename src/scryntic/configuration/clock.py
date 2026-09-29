"""Operator-selected workload budgets, in nanoseconds and parts per billion."""

from dataclasses import dataclass

from scryntic.domain.validation import integer


@dataclass(frozen=True, slots=True)
class ClockLimits:
    max_offset_ns: int = 50_000_000
    max_uncertainty_ns: int = 100_000_000
    max_evidence_age_ns: int = 120_000_000_000
    max_step_ns: int = 50_000_000
    max_sample_gap_ns: int = 30_000_000_000
    holdover_drift_ppb: int = 500_000

    def __post_init__(self) -> None:
        for value in (
            self.max_offset_ns,
            self.max_uncertainty_ns,
            self.holdover_drift_ppb,
        ):
            integer(value, 0)
        for value in (
            self.max_evidence_age_ns,
            self.max_step_ns,
            self.max_sample_gap_ns,
        ):
            integer(value, 1)
        if self.holdover_drift_ppb >= 1_000_000_000:
            raise ValueError("Holdover drift must be less than one second per second")
        if any(
            value > (1 << 63) - 1
            for value in (
                self.max_offset_ns,
                self.max_uncertainty_ns,
                self.max_evidence_age_ns,
                self.max_step_ns,
                self.max_sample_gap_ns,
            )
        ):
            raise ValueError("Clock budgets must fit signed 64-bit storage")
