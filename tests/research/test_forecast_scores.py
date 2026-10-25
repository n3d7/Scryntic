from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path

import pytest

from scryntic.forecast.ledger import ForecastLedger
from scryntic.forecast.scoring import ForecastScores, prepare_content
from scryntic.research.targets import (
    HOUR_NS,
    ISSUE_DELAY_NS,
    HourlyBar,
    HourlySeries,
    SeriesKey,
)
from tests.normalization.helpers import installation
from tests.research.test_forecast_ledger import Clock, T, content


def series(*, revised: bool = False, missing: bool = False) -> HourlySeries:
    bars = [HourlyBar(T - HOUR_NS, Decimal("100"), "a" * 64, T)]
    if not missing:
        bars.append(
            HourlyBar(T, Decimal("110"), "b" * 64, T + HOUR_NS + ISSUE_DELAY_NS)
        )
        if revised:
            bars.extend(
                [
                    HourlyBar(
                        T - HOUR_NS,
                        Decimal("200"),
                        "d" * 64,
                        T + 2 * HOUR_NS,
                        revision=1,
                    ),
                    HourlyBar(T, Decimal("120"), "c" * 64, T + 2 * HOUR_NS, revision=1),
                ]
            )
    bars.sort(key=lambda b: (b.start_ns, b.revision))
    return HourlySeries(
        SeriesKey(
            "synthetic-hourly", "Fixture", "linear", "BTCUSDT", "e" * 64, "as-observed"
        ),
        bars,
    )


def test_maturity_and_revision_keep_original_base_and_prior_scores(
    tmp_path: Path,
) -> None:
    clock = Clock(T + 1)
    forecast = prepare_content(content().slot, series())
    with ForecastLedger(installation(tmp_path), clock) as ledger:
        ledger.publish(forecast, attempt_id="1" * 32)
        scoring = ForecastScores(ledger)
        clock.now = T + HOUR_NS
        assert (
            scoring.mature(forecast.slot.sha256, series(), clock.now)["state"]
            == "pending"
        )
        assert not ledger.events("score")
        clock.now += ISSUE_DELAY_NS
        first = scoring.mature(forecast.slot.sha256, series(), clock.now)
        assert first["state"] == "scored"
        assert Decimal(first["absolute_error"]) == (Decimal("110") / 100).ln()
        clock.now = T + 2 * HOUR_NS
        revised = scoring.mature(forecast.slot.sha256, series(revised=True), clock.now)
        assert Decimal(revised["absolute_error"]) == (Decimal("120") / 100).ln()
        assert revised["outcome_sha256"] != first["outcome_sha256"]
        assert len(ledger.events("outcome")) == 2
        assert (
            ledger.events("outcome")[-1]["body"]["supersedes"]
            == first["outcome_sha256"]
        )
        assert len(ledger.events("score")) == 2
        assert scoring.view(forecast.slot.sha256, T + HOUR_NS + ISSUE_DELAY_NS) == first
        assert (
            scoring.view(forecast.slot.sha256, clock.now, first_available=True) == first
        )
        assert scoring.view(forecast.slot.sha256, clock.now) == revised
        assert (
            scoring.mature(forecast.slot.sha256, series(revised=True), clock.now)
            == revised
        )


def test_missing_is_not_zero_and_comparison_reports_common_pairs(
    tmp_path: Path,
) -> None:
    clock = Clock(T + 1)
    first = prepare_content(content().slot, series())
    second = replace(
        first,
        slot=replace(
            first.slot, stream=replace(first.slot.stream, stream_id="second-zero")
        ),
    )
    with ForecastLedger(installation(tmp_path), clock) as ledger:
        ledger.publish(first, attempt_id="1" * 32)
        ledger.publish(second, attempt_id="2" * 32)
        scoring = ForecastScores(ledger)
        clock.now = T + HOUR_NS + ISSUE_DELAY_NS
        assert (
            scoring.mature(first.slot.sha256, series(missing=True), clock.now)["state"]
            == "missing"
        )
        assert not ledger.events("score")
        scoring.mature(first.slot.sha256, series(), clock.now)
        assert scoring.compare("fixture-zero", "second-zero", clock.now)["paired"] == 0
        scoring.mature(second.slot.sha256, series(), clock.now)
        comparison = scoring.compare("fixture-zero", "second-zero", clock.now)
        assert comparison["paired"] == 1
        assert comparison["mae_difference"] == "0"
        assert comparison["claims"] == "descriptive-only-no-promotion"


def test_failed_refresh_preserves_already_available_score(tmp_path: Path) -> None:
    clock = Clock(T + 1)
    forecast = prepare_content(content().slot, series())
    with ForecastLedger(installation(tmp_path), clock) as ledger:
        ledger.publish(forecast, attempt_id="1" * 32)
        scoring = ForecastScores(ledger)
        clock.now = T + HOUR_NS + ISSUE_DELAY_NS
        first = scoring.mature(forecast.slot.sha256, series(), clock.now)
        clock.now += HOUR_NS
        scoring.mature(forecast.slot.sha256, series(missing=True), clock.now)
        assert scoring.view(forecast.slot.sha256, clock.now) == first
        assert scoring.cohort(clock.now)[0]["state"] == "scored"


def test_forecast_source_identity_and_score_as_of_fail_closed(tmp_path: Path) -> None:
    wrong = series()
    wrong.key = replace(wrong.key, instrument="ETHUSDT")
    with pytest.raises(ValueError):
        prepare_content(content().slot, wrong)
    clock = Clock(T + 1)
    with ForecastLedger(installation(tmp_path), clock) as ledger:
        forecast = prepare_content(content().slot, series())
        ledger.publish(forecast, attempt_id="1" * 32)
        with pytest.raises(ValueError):
            ForecastScores(ledger).mature(
                forecast.slot.sha256, series(), T + 3 * HOUR_NS
            )


def test_comparison_denominators_keep_unpublished_states_and_honor_cutoff(
    tmp_path: Path,
) -> None:
    clock = Clock(T + ISSUE_DELAY_NS)
    with ForecastLedger(installation(tmp_path), clock) as ledger:
        ledger.publish(content(), attempt_id="1" * 32)
        missing = replace(content().slot, origin_ns=T + HOUR_NS)
        missed = replace(content().slot, origin_ns=T + 2 * HOUR_NS)
        for slot, state in ((missing, "missing"), (missed, "missed")):
            ledger._append(
                "slot",
                slot.sha256,
                {
                    "slot": asdict(slot),
                    "state": state,
                    "reason": "fixture",
                    "as_of_ns": T + 3 * HOUR_NS,
                },
            )
        scoring = ForecastScores(ledger)
        assert (
            scoring.compare("fixture-zero", "other-zero", T - 1)["counts"][
                "fixture-zero"
            ]
            == {}
        )
        counts = scoring.compare("fixture-zero", "other-zero", T + 4 * HOUR_NS)[
            "counts"
        ]["fixture-zero"]
        assert counts["missing"] == 1
        assert counts["missed"] == 1
        assert counts["pending"] == 1
