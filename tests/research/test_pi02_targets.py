"""PI02 timing, lineage, and label boundaries."""

from decimal import Decimal

import pytest

from scryntic.research.targets import HOUR_NS, HourlyBar, HourlySeries, SeriesKey


def _bar(
    hour: int,
    *,
    close: str = "100",
    known_at_ns: int | None = None,
    revision: int = 0,
    quality_ok: bool = True,
) -> HourlyBar:
    return HourlyBar(
        start_ns=hour * HOUR_NS,
        close=Decimal(close),
        raw_sha256=f"{hour + revision + 1:064x}",
        known_at_ns=known_at_ns,
        revision=revision,
        quality_ok=quality_ok,
    )


def _series(
    bars: list[HourlyBar], plane: str = "historical-reconstruction"
) -> HourlySeries:
    return HourlySeries(
        SeriesKey(
            source_id="binance-vision-usdm-1h",
            venue="Binance",
            category="USD-M",
            instrument="BTCUSDT",
            snapshot_sha256="a" * 64,
            availability_plane=plane,
        ),
        bars,
    )


def test_daily_resample_requires_every_final_hour_and_preserves_unknown_availability() -> (
    None
):
    bars = [_bar(hour, close=str(hour + 1)) for hour in range(24)]
    complete = _series(bars).resample(0, 24 * HOUR_NS, 24 * HOUR_NS)
    assert len(complete.bars) == 1
    assert complete.bars[0].close == Decimal("24")
    assert complete.bars[0].known_at_ns is None
    assert complete.bars[0].raw_lineage_sha256 != bars[-1].raw_sha256

    gap = _series([bar for bar in bars if bar.start_ns != 7 * HOUR_NS]).resample(
        0, 24 * HOUR_NS, 24 * HOUR_NS
    )
    assert gap.bars == ()
    assert gap.exclusions == ((0, "missing-constituent"),)


def test_observed_resample_uses_latest_constituent_availability() -> None:
    bars = [_bar(hour, known_at_ns=(hour + 1) * HOUR_NS + 10) for hour in range(24)]
    series = _series(bars, "as-observed")
    assert series.resample(0, 24 * HOUR_NS, 24 * HOUR_NS + 9).bars == ()
    result = series.resample(0, 24 * HOUR_NS, 24 * HOUR_NS + 10)
    assert result.bars[0].known_at_ns == 24 * HOUR_NS + 10


def test_issue_record_never_contains_future_label_and_late_revision_is_versioned() -> (
    None
):
    bars = [_bar(hour, known_at_ns=(hour + 1) * HOUR_NS + 5) for hour in range(25)]
    bars.append(_bar(24, close="121", known_at_ns=26 * HOUR_NS, revision=1))
    series = _series(bars, "as-observed")
    origin = series.origin(24 * HOUR_NS, 3600, "c" * 64, lookback_hours=24)
    assert origin.eligible
    assert not hasattr(origin, "target_value")
    first = series.outcome(origin, 25 * HOUR_NS + 60_000_000_000)
    assert first.value == Decimal("0")
    corrected = series.outcome(origin, 26 * HOUR_NS)
    assert corrected.value is not None
    assert corrected.value > 0
    assert corrected.version_key != first.version_key
    assert corrected.origin_key == first.origin_key


def test_historical_28d_endpoint_is_duration_based_and_not_as_observed() -> None:
    series = _series([_bar(hour) for hour in range(24 + 28 * 24)])
    origin = series.origin(24 * HOUR_NS, 28 * 86400, "c" * 64)
    assert origin.eligible
    assert origin.known_at_ns is None
    outcome = series.outcome(origin)
    assert outcome.value == 0
    assert outcome.matured_at_ns is None
    assert outcome.evidence_class == "historical-reconstruction"


def test_quality_failure_and_missing_history_fail_closed() -> None:
    series = _series([_bar(hour, quality_ok=hour != 4) for hour in range(25)])
    origin = series.origin(24 * HOUR_NS, 3600, "c" * 64)
    assert not origin.eligible
    assert origin.reason == "time-quality"
    with pytest.raises(ValueError, match="ineligible"):
        series.outcome(origin)


def test_duplicate_unknown_revision_and_invalid_alignment_are_rejected() -> None:
    with pytest.raises(ValueError, match="revision"):
        _series([_bar(0), _bar(0, close="101", revision=1)])
    with pytest.raises(ValueError, match="aligned"):
        _series([HourlyBar(1, Decimal("100"), "b" * 64)])


def test_outcome_cannot_join_another_instrument_under_same_origin_time() -> None:
    btc = _series([_bar(hour) for hour in range(25)])
    eth = HourlySeries(
        SeriesKey(
            "binance-vision-usdm-1h",
            "Binance",
            "USD-M",
            "ETHUSDT",
            "a" * 64,
            "historical-reconstruction",
        ),
        [_bar(hour) for hour in range(25)],
    )
    origin = btc.origin(24 * HOUR_NS, 3600, "c" * 64)
    with pytest.raises(ValueError, match="series"):
        eth.outcome(origin)


def test_common_origin_key_is_separate_from_pre_outcome_input_version() -> None:
    bars = [_bar(hour) for hour in range(25)]
    first = _series(bars).origin(24 * HOUR_NS, 3600, "c" * 64)
    updated = HourlySeries(
        SeriesKey(
            "binance-vision-usdm-1h",
            "Binance",
            "USD-M",
            "BTCUSDT",
            "f" * 64,
            "historical-reconstruction",
        ),
        bars,
    ).origin(24 * HOUR_NS, 3600, "c" * 64)
    assert first.origin_key == updated.origin_key
    assert first.input_version_key != updated.input_version_key
    assert first.recipe_sha256 is not None
    assert first.feature_snapshot_sha256 != updated.feature_snapshot_sha256
