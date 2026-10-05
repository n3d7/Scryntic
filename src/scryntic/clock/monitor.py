"""Clock.sample implementation: capture continues; timing authority is conditional."""

import logging
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from scryntic.application.clock import (
    ClockReader,
    ClockReading,
    SyncEvidence,
    SynchronizationStatus,
)
from scryntic.clock.policy import is_final
from scryntic.configuration.clock import ClockLimits
from scryntic.domain.time import ClockSample, TimeQuality

_LOG = logging.getLogger(__name__)
type QualityStatus = Literal["unknown", "degraded", "healthy"]


@dataclass(frozen=True, slots=True)
class ClockHealth:
    sample: ClockSample
    reason: str


class ClockMonitor:
    """Single-owner monitor. Query synchronization on every decision/capture.

    A disruption invalidates prior evidence until a later reference measurement.
    State is process-local; immutable samples carry unique epochs into storage.
    """

    def __init__(
        self, reader: ClockReader, status: SynchronizationStatus, limits: ClockLimits
    ) -> None:
        self._reader, self._status, self._limits = reader, status, limits
        self._previous: ClockReading | None = None
        self._last_reference: int | None = None
        self._refresh_after: int | None = None
        self._instance = uuid4().hex
        self._epoch = 0
        self._unmeasured_change_ns = 0
        self.health: ClockHealth | None = None

    def _discontinuity(self, before: ClockReading, after: ClockReading) -> str | None:
        if before.session_id != after.session_id:
            return "session_changed"
        elapsed = after.monotonic_ns - before.monotonic_ns
        if elapsed < 0:
            return "monotonic_regression"
        allowance = before.capture_uncertainty_ns + after.capture_uncertainty_ns
        if before.boottime_ns is not None and after.boottime_ns is not None:
            boot_elapsed = after.boottime_ns - before.boottime_ns
            if boot_elapsed < 0:
                return "monotonic_regression"
            if abs(boot_elapsed - elapsed) > self._limits.max_step_ns + allowance:
                return "suspend"
        if (
            abs(after.wall_time_ns - before.wall_time_ns - elapsed)
            > self._limits.max_step_ns + allowance
        ):
            return "clock_step"
        if elapsed > self._limits.max_sample_gap_ns:
            return "sampling_gap"
        return None

    def _quality(
        self, reading: ClockReading, evidence: SyncEvidence | None, probe_ns: int
    ) -> tuple[QualityStatus, str, int | None, int | None, int | None]:
        if evidence is None:
            return "unknown", "missing_evidence", None, None, None
        offset = evidence.offset_ns
        radius = (
            evidence.uncertainty_ns
            + reading.capture_uncertainty_ns
            + probe_ns
            + self._unmeasured_change_ns
        )
        # Solve age >= wall + radius(age) - reference, rather than understating
        # age by ignoring its own holdover contribution to the error interval.
        denominator = 1_000_000_000 - self._limits.holdover_drift_ppb
        age = (
            max(0, reading.wall_time_ns + radius - evidence.reference_time_ns)
            * 1_000_000_000
            + denominator
            - 1
        ) // denominator
        # Holdover is an operator-declared upper drift assumption, not a claim
        # inferred from the synchronized flag. Outward integer rounding only.
        radius += (age * self._limits.holdover_drift_ppb + 999_999_999) // 1_000_000_000
        if not evidence.synchronized:
            return "degraded", "unsynchronized", offset, radius, age
        if (
            self._refresh_after is not None
            and evidence.reference_time_ns <= self._refresh_after
        ):
            return "unknown", "awaiting_refresh", offset, radius, age
        if evidence.reference_time_ns > reading.wall_time_ns + radius:
            return "unknown", "future_evidence", offset, radius, None
        if age > self._limits.max_evidence_age_ns:
            return "unknown", "stale_evidence", offset, radius, age
        if abs(offset) > self._limits.max_offset_ns:
            return "degraded", "excess_offset", offset, radius, age
        if radius > self._limits.max_uncertainty_ns:
            return "degraded", "excess_uncertainty", offset, radius, age
        if reading.boottime_ns is None:
            return "unknown", "unsupported_suspend_clock", offset, radius, age
        self._refresh_after = None
        return "healthy", "bounded_evidence", offset, radius, age

    def _update_continuity(
        self, before: ClockReading, after: ClockReading, evidence: SyncEvidence | None
    ) -> str | None:
        disruption = self._discontinuity(before, after)
        if self._previous is not None:
            disruption = self._discontinuity(self._previous, before) or disruption
            disruption = disruption or self._discontinuity(self._previous, after)
            self._unmeasured_change_ns += abs(
                after.wall_time_ns
                - self._previous.wall_time_ns
                - (after.monotonic_ns - self._previous.monotonic_ns)
            )
        if (
            disruption is None
            and evidence is not None
            and evidence.synchronized
            and self._last_reference is not None
            and evidence.reference_time_ns < self._last_reference
        ):
            disruption = "reference_regression"
        if (
            disruption is None
            and evidence is not None
            and evidence.synchronized
            and self._last_reference is not None
            and evidence.reference_time_ns > self._last_reference
            and evidence.reference_time_ns
            <= after.wall_time_ns + evidence.uncertainty_ns
        ):
            self._unmeasured_change_ns = 0
        if disruption is not None:
            self._refresh_after = max(
                before.wall_time_ns,
                after.wall_time_ns,
                self._previous.wall_time_ns if self._previous else before.wall_time_ns,
                self._last_reference or 0,
            )
        return disruption

    def _record_transition(
        self, status: QualityStatus, reason: str, disruption: str | None
    ) -> None:
        previous_health = self.health
        if (
            previous_health is None
            or disruption is not None
            or (previous_health.sample.quality.status, previous_health.reason)
            != (status, reason)
        ):
            self._epoch += 1
            _LOG.log(
                logging.INFO if status == "healthy" else logging.WARNING,
                "clock_quality status=%s previous=%s reason=%s epoch=%s:%d",
                status,
                previous_health.sample.quality.status if previous_health else "none",
                reason,
                self._instance,
                self._epoch,
            )

    def sample(self) -> ClockSample:
        before = self._reader.read()
        evidence = self._status.read()
        after = self._reader.read()
        disruption = self._update_continuity(before, after, evidence)
        status, reason, offset, radius, age = self._quality(
            after, evidence, max(0, after.monotonic_ns - before.monotonic_ns)
        )
        if any(
            value is not None and abs(value) > (1 << 63) - 1
            for value in (offset, radius, age)
        ):
            # Persisting this sample must remain possible even with invalid or
            # extreme daemon/configuration evidence; SQLite/Parquet use int64.
            status, reason, offset, radius, age = (
                "unknown",
                "unrepresentable_evidence",
                None,
                None,
                None,
            )
        if disruption is not None:
            status = (
                "unknown"
                if disruption in ("session_changed", "reference_regression")
                else "degraded"
            )
            reason = disruption
        elif self._previous is None and status == "healthy":
            status, reason = "unknown", "startup"
        self._record_transition(status, reason, disruption)
        quality = TimeQuality(
            f"{self._instance}:{self._epoch}", status, offset, radius, age
        )
        sample = ClockSample(
            after.wall_time_ns, after.monotonic_ns, after.session_id, quality
        )
        self.health = ClockHealth(sample, reason)
        self._previous = after
        if evidence is not None and reason in ("startup", "bounded_evidence"):
            self._last_reference = evidence.reference_time_ns
        return sample

    def finality_ready(self, closing_boundary_ns: int) -> bool:
        """Always refresh the decision sample; a stored healthy sample is not live."""
        return is_final(self.sample(), closing_boundary_ns, self._limits)
