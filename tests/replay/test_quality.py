"""Explicit gap/quality policies apply to features and label dependencies."""

from dataclasses import replace
from decimal import Decimal

import pytest

from scryntic.dataset.recipes import CoverageClaim
from scryntic.domain.identity import InstrumentId
from scryntic.domain.time import TimeQuality
from scryntic.replay.contracts import ReplayClock
from scryntic.replay.evaluation import evaluate
from scryntic.replay.reader import CandleReplayReader
from tests.dataset.test_recipes import _clock, _received
from tests.dataset.test_selection import _source
from tests.replay.helpers import START, STEP, snapshot, sources
from tests.replay.test_reader import config


def test_later_coverage_repair_cannot_repair_past_as_observed_decision() -> None:
    inputs = sources()
    semantics = inputs[0].value.semantics
    assert semantics is not None
    claim = CoverageClaim(
        semantics.key.instrument,
        STEP,
        START,
        START + 12 * STEP,
        "complete",
        _clock(START + 3 * STEP),
    )
    manifest, rows = snapshot(inputs)
    manifest["coverage_claims"] = [claim.projection()]
    reader = CandleReplayReader(
        manifest, rows, replace(config(mode="as-observed"), coverage="exclude")
    )
    assert reader.features(0, ReplayClock(START + STEP).sample) is None
    assert reader.features(0, ReplayClock(START + 3 * STEP).sample) is not None
    result = evaluate(reader)
    assert any(
        item["row_index"] == 0 and item["reason"] == "coverage"
        for item in result["exclusions"]
    )


def test_unknown_clock_evidence_is_flagged_or_excluded_never_qualified_by_mode() -> (
    None
):
    inputs = sources()
    source = inputs[0]
    raw = replace(
        source.value.raw,
        envelope=replace(
            source.value.raw.envelope,
            receipt=replace(
                source.value.raw.envelope.receipt, quality=TimeQuality("bad")
            ),
            payload_limit=len(source.value.raw.envelope.payload),
        ),
    )
    bad = replace(
        source,
        value=replace(
            source.value,
            raw=raw,
            outcome=replace(source.value.outcome, receipt=raw.envelope.receipt),
        ),
    )
    manifest, rows = snapshot((bad, *inputs[1:]))
    included = evaluate(CandleReplayReader(manifest, rows, config()))
    assert "time-quality" in included["predictions"][0]["quality_flags"]
    historical = evaluate(
        CandleReplayReader(manifest, rows, replace(config(), time_quality="exclude"))
    )
    strict = evaluate(CandleReplayReader(manifest, rows, config(mode="as-observed")))
    for result in (historical, strict):
        assert any(
            item["reason"] == "time-quality" and item["row_index"] == 0
            for item in result["exclusions"]
        )


def test_gaps_and_original_exclusions_remain_in_report() -> None:
    inputs = sources()
    manifest, rows = snapshot((*inputs[:2], *inputs[3:]), horizon=2)
    manifest["exclusions"] = [
        {"reason": "time-quality", "excluded": True, "offset": 99}
    ]
    result = evaluate(CandleReplayReader(manifest, rows, config()))
    assert result["dataset_exclusions"] == manifest["exclusions"]
    assert any(
        item["reason"] == "label-gap-or-missing" for item in result["exclusions"]
    )
    assert any(
        item["label_missing_reason"] == "gap-or-missing"
        for item in result["derived_lineage"]
    )


def test_constant_training_features_do_not_divide_by_zero() -> None:
    inputs = tuple(
        _received(
            _source(i + 1, finalized=True, close="100", start_ns=START + i * STEP),
            START + (i + 1) * STEP,
        )
        for i in range(12)
    )
    result = evaluate(CandleReplayReader(*snapshot(inputs), config()))
    assert result["transforms"][0]["scale"] == ["1"]
    assert all(item["transformed_features"] == ["0"] for item in result["predictions"])
    assert all(item["mae"] == "0" for item in result["metrics"])


def test_delay_cannot_turn_already_known_target_into_future_forecast() -> None:
    result = evaluate(CandleReplayReader(*snapshot(), config(decision_delay_ns=STEP)))
    assert result["status"] == "no-training-data"
    assert any(item["reason"] == "label-not-future" for item in result["exclusions"])


def test_decimal128_boundaries_reject_worker_exponent_amplification() -> None:
    manifest, rows = snapshot()
    rows[0]["close"] = "1e1000000"
    configuration = config()
    with pytest.raises(ValueError, match="decimal128"):
        CandleReplayReader(manifest, rows, configuration)


def test_lag_standardizer_fits_both_columns_only_on_training() -> None:
    result = evaluate(CandleReplayReader(*snapshot(lag=1), config()))
    assert result["feature_names"] == ["close", "lag_close"]
    assert result["transforms"][0]["fit_row_indices"] == [1, 2]
    assert list(map(Decimal, result["transforms"][0]["mean"])) == [
        Decimal("100.015"),
        Decimal("100.005"),
    ]


def test_multiple_series_fit_and_score_independently() -> None:
    second = []
    for i in range(12):
        source = _received(
            _source(i + 13, finalized=True, close="100.20", start_ns=START + i * STEP),
            START + (i + 1) * STEP,
        )
        value = source.value
        assert value.semantics is not None
        instrument = InstrumentId("fake", "spot", "ETHUSDT")
        raw = replace(
            value.raw,
            envelope=replace(
                value.raw.envelope,
                subject=instrument,
                payload_limit=len(value.raw.envelope.payload),
            ),
        )
        semantics = replace(
            value.semantics, key=replace(value.semantics.key, instrument=instrument)
        )
        outcome = replace(value.outcome, semantic_revision=semantics.revision())
        second.append(
            replace(
                source,
                value=replace(value, raw=raw, semantics=semantics, outcome=outcome),
            )
        )
    result = evaluate(CandleReplayReader(*snapshot((*sources(), *second)), config()))
    assert len(result["transforms"]) == 2
    by_symbol = {item["series"]["symbol"]: item for item in result["transforms"]}
    assert by_symbol["ETHUSDT"]["mean"] == ["100.2"]
    assert all(
        item["mae"] == "0"
        for item in result["metrics"]
        if item["series"]["symbol"] == "ETHUSDT"
    )
