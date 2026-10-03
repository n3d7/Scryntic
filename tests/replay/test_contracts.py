"""Absolute boundaries and synthetic clocks cannot depend on runtime time."""

import pytest

from scryntic.replay.contracts import ReplayClock, ReplayConfig


def test_clock_repeats_and_refuses_backward_motion() -> None:
    clock = ReplayClock(10, uncertainty_ns=2)
    assert clock.sample.wall_time_ns == 10
    assert clock.sample.quality.uncertainty_ns == 2
    assert clock.advance(20) == ReplayClock(20, uncertainty_ns=2).sample
    with pytest.raises(ValueError, match="backward"):
        clock.advance(19)


def test_temporal_ranges_purge_touching_and_overlapping_labels() -> None:
    config = ReplayConfig(0, 100, 200, 300)
    assert config.partition(99, 99) == ("train", 100)
    assert config.partition(100, 100) == ("validation", 200)
    assert config.partition(200, 200) == ("test", 300)
    assert config.partition(99, 100) is None
    assert config.partition(-1, -1) is None
    assert config.partition(300, 300) is None
    assert config.label_fits(99, 99, 100)
    assert not config.label_fits(100, 99, 100)
    assert not config.label_fits(99, 100, 100)


@pytest.mark.parametrize("bounds", [(0, 0, 2, 3), (3, 2, 1, 0), (0, True, 2, 3)])
def test_invalid_boundaries_fail(bounds: tuple[int, int, int, int]) -> None:
    with pytest.raises((TypeError, ValueError)):
        ReplayConfig(*bounds)
