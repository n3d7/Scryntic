"""Repeatability, training-only state, purging and honest evaluation accounting."""

from copy import deepcopy
from dataclasses import replace
from decimal import ROUND_UP, Decimal, getcontext, localcontext

import pytest

from scryntic.replay.evaluation import evaluate, fit_standardizer
from scryntic.replay.reader import CandleReplayReader
from tests.dataset.test_recipes import _received
from tests.dataset.test_selection import _source
from tests.replay.helpers import START, STEP, snapshot, sources
from tests.replay.test_reader import config


def test_repeatability_metrics_and_training_only_fit() -> None:
    manifest, rows = snapshot()
    reader = CandleReplayReader(manifest, rows, config())
    first = evaluate(reader)
    assert evaluate(reader) == first
    with localcontext() as ctx:
        ctx.prec = 3
        ctx.rounding = ROUND_UP
        assert evaluate(reader) == first
    assert first["transforms"][0]["fit_row_indices"] == [0, 1, 2]
    assert first["transforms"][0]["mean"] == ["100.01"]
    assert all(
        item["mae"] == "0.01" and item["rmse"] == "0.01" and item["bias"] == "-0.01"
        for item in first["metrics"]
    )
    assert any(item["reason"] == "label-purged" for item in first["exclusions"])


def test_future_value_changes_cannot_change_training_fit_or_past_predictions() -> None:
    inputs = sources()
    changed = list(inputs)
    changed[8] = _received(
        _source(9, finalized=True, close="100.99", start_ns=START + 8 * STEP),
        START + 9 * STEP,
    )
    first = evaluate(CandleReplayReader(*snapshot(inputs), config()))
    future = evaluate(CandleReplayReader(*snapshot(tuple(changed)), config()))
    assert first["transforms"] == future["transforms"]
    assert [item for item in first["predictions"] if item["partition"] == "train"] == [
        item for item in future["predictions"] if item["partition"] == "train"
    ]


def test_multistep_overlapping_labels_are_purged_from_both_boundaries() -> None:
    result = evaluate(CandleReplayReader(*snapshot(horizon=2), config()))
    purged = {
        item["row_index"]
        for item in result["exclusions"]
        if item["reason"] == "label-purged"
    }
    assert purged >= {2, 3, 6, 7}
    for item in result["predictions"]:
        end = (
            config().validation_start_ns
            if item["partition"] == "train"
            else config().test_start_ns
            if item["partition"] == "validation"
            else config().end_ns
        )
        assert item["label_end_ns"] < end


def test_late_training_labels_cannot_enter_fit() -> None:
    inputs = sources()
    inputs = (
        _received(inputs[0], START + STEP),
        _received(inputs[1], START + 10 * STEP),
        *inputs[2:],
    )
    result = evaluate(CandleReplayReader(*snapshot(inputs), config(mode="as-observed")))
    assert 0 not in result["transforms"][0]["fit_row_indices"]
    assert any(
        item["reason"] == "label-availability-purged" and item["row_index"] == 0
        for item in result["exclusions"]
    )


def test_no_training_data_and_missing_coverage_are_reported() -> None:
    result = evaluate(
        CandleReplayReader(*snapshot(), replace(config(), coverage="exclude"))
    )
    assert result["status"] == "no-training-data"
    assert not result["metrics"]
    assert not result["transforms"]
    assert result["counts"]["excluded"] == 12
    assert any(item["reason"] == "coverage" for item in result["exclusions"])


def test_transform_rejects_nontraining_cases() -> None:
    from scryntic.replay.contracts import ReplayFeatures
    from scryntic.replay.evaluation import ForecastCase
    from scryntic.replay.reader import ReplayTarget

    frame = ReplayFeatures(0, ("v", "c", "s", 1), 0, (Decimal(1),), ())
    case = ForecastCase(
        frame, "test", ReplayTarget(Decimal(2), 1, 1, ()), Decimal(1), 2
    )
    with pytest.raises(ValueError, match="training"):
        fit_standardizer((case,))
    assert getcontext().prec > 0


def test_input_primitives_are_not_mutated() -> None:
    manifest, rows = snapshot(lag=1)
    saved = deepcopy((manifest, rows))
    evaluate(CandleReplayReader(manifest, rows, config()))
    assert manifest == saved[0]
    assert rows == saved[1]
