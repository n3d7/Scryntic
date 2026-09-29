"""Pure comparisons of bounded observation/decision intervals, never bare flags."""

from scryntic.configuration.clock import ClockLimits
from scryntic.domain.time import ClockSample
from scryntic.domain.validation import integer


def time_interval(sample: ClockSample, limits: ClockLimits) -> tuple[int, int] | None:
    """Interval at capture/decision time; uncertainty already includes age growth."""
    quality = sample.quality
    offset, radius, age = (
        quality.offset_ns,
        quality.uncertainty_ns,
        quality.evidence_age_ns,
    )
    if (
        quality.status != "healthy"
        or offset is None
        or radius is None
        or age is None
        or abs(offset) > limits.max_offset_ns
        or radius > limits.max_uncertainty_ns
        or age > limits.max_evidence_age_ns
        or radius < abs(offset)
        or radius
        < abs(offset) + (age * limits.holdover_drift_ppb + 999_999_999) // 1_000_000_000
    ):
        return None
    return sample.wall_time_ns - radius, sample.wall_time_ns + radius


def is_final(
    sample: ClockSample, closing_boundary_ns: int, limits: ClockLimits
) -> bool:
    """Source-specific policy supplies the boundary; uncertainty must be past it."""
    integer(closing_boundary_ns)
    interval = time_interval(sample, limits)
    return interval is not None and interval[0] > closing_boundary_ns


def observed_by(
    observation: ClockSample, cutoff: ClockSample, limits: ClockLimits
) -> bool:
    """Latest plausible observation must be <= earliest plausible cutoff."""
    observed, decision = (
        time_interval(observation, limits),
        time_interval(cutoff, limits),
    )
    return observed is not None and decision is not None and observed[1] <= decision[0]
