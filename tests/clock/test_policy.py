from dataclasses import replace

import pytest

from scryntic.clock.policy import is_final, observed_by, time_interval
from scryntic.domain.time import ClockSample, TimeQuality
from tests.clock.helpers import LIMITS, healthy_monitor


def sample(wall: int, uncertainty: int = 5) -> ClockSample:
    return ClockSample(
        wall, 100, "session", TimeQuality("epoch", "healthy", 0, uncertainty, 0)
    )


@pytest.mark.parametrize("wall,want", [(104, False), (105, False), (106, True)])
def test_within_budget_uncertainty_straddling_finality_defers(
    wall: int, want: bool
) -> None:
    assert is_final(sample(wall), 100, LIMITS) is want


def test_live_finality_samples_again_instead_of_reusing_a_healthy_flag() -> None:
    host, _, monitor = healthy_monitor()
    assert monitor.finality_ready(990)
    host.advance(86)
    assert not monitor.finality_ready(990)


@pytest.mark.parametrize(
    "observation,cutoff,want", [(90, 100, True), (91, 100, False), (100, 100, False)]
)
def test_strict_observed_selection_compares_latest_to_earliest(
    observation: int, cutoff: int, want: bool
) -> None:
    assert observed_by(sample(observation), sample(cutoff), LIMITS) is want


@pytest.mark.parametrize(
    "quality",
    [
        TimeQuality("epoch"),
        TimeQuality("epoch", "degraded", 0, 5, 0),
        TimeQuality("epoch", "healthy", 11, 15, 0),
        TimeQuality("epoch", "healthy", 0, 51, 0),
        TimeQuality("epoch", "healthy", 0, 5, 101),
        TimeQuality("epoch", "healthy", 10, 5, 0),
        TimeQuality("epoch", "healthy", 0, 5, 100),
    ],
)
def test_bare_healthy_or_unbounded_claim_never_substitutes_for_interval(
    quality: TimeQuality,
) -> None:
    value = replace(sample(1000), quality=quality)
    assert time_interval(value, LIMITS) is None
    assert not is_final(value, 0, LIMITS)
    assert not observed_by(value, sample(2000), LIMITS)
    assert not observed_by(sample(0), value, LIMITS)
