from dataclasses import replace

from scryntic.application.clock import ClockReading, SyncEvidence
from scryntic.clock.monitor import ClockMonitor
from scryntic.configuration.clock import ClockLimits

LIMITS = ClockLimits(
    max_offset_ns=10,
    max_uncertainty_ns=50,
    max_evidence_age_ns=100,
    max_step_ns=20,
    max_sample_gap_ns=200,
    holdover_drift_ppb=100_000_000,
)


class FakeHost:
    def __init__(self) -> None:
        self.value = ClockReading(1000, 100, 100, "session-a")

    def read(self) -> ClockReading:
        return self.value

    def advance(self, elapsed: int, *, step: int = 0, suspend: int = 0) -> None:
        self.value = replace(
            self.value,
            wall_time_ns=self.value.wall_time_ns + elapsed + step + suspend,
            monotonic_ns=self.value.monotonic_ns + elapsed,
            boottime_ns=(self.value.boottime_ns or 0) + elapsed + suspend,
        )


class FakeStatus:
    def __init__(self) -> None:
        self.value: SyncEvidence | None = SyncEvidence(990, 2, 5, True)

    def read(self) -> SyncEvidence | None:
        return self.value


def healthy_monitor() -> tuple[FakeHost, FakeStatus, ClockMonitor]:
    host, status = FakeHost(), FakeStatus()
    monitor = ClockMonitor(host, status, LIMITS)
    assert monitor.sample().quality.status == "unknown"
    assert monitor.sample().quality.status == "healthy"
    return host, status, monitor
