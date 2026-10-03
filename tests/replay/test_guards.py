"""Malformed or unavailable provenance is rejected at the replay boundary."""

from dataclasses import replace
from typing import Any

import pytest

from scryntic.replay.contracts import ReplayClock, ReplayConfig
from scryntic.replay.evaluation import evaluate
from scryntic.replay.reader import CandleReplayReader
from tests.replay.helpers import START, STEP, snapshot
from tests.replay.test_reader import config


@pytest.mark.parametrize(
    "field,value",
    [
        ("mode", "live"),
        ("coverage", "infer"),
        ("time_quality", "healthy"),
        ("decision_delay_ns", -1),
        ("decision_uncertainty_ns", True),
    ],
)
def test_replay_configuration_is_closed(field: str, value: Any) -> None:
    configuration = config()
    changes = {field: value}
    with pytest.raises((ValueError, TypeError)):
        replace(configuration, **changes)


@pytest.mark.parametrize(
    "damage",
    [
        "recipe",
        "count",
        "fingerprint",
        "receipt",
        "chain",
        "label-end",
        "lag-null",
        "missing-label-value",
        "duplicate-chain",
    ],
)
def test_provenance_damage_cannot_be_evaluated(damage: str) -> None:
    manifest, rows = snapshot(lag=1)
    if damage == "recipe":
        manifest["recipe"]["minor"] = 0
    elif damage == "count":
        manifest["row_count"] += 1
    elif damage == "fingerprint":
        manifest["rows"][0]["selected"]["input_fingerprint"] = "0" * 64
    elif damage == "receipt":
        rows[0]["receipt_wall_time_ns"] += 1
    elif damage == "chain":
        manifest["derived_lineage"].pop()
    elif damage == "label-end":
        rows[0]["label_end_ns"] += 1
    elif damage == "lag-null":
        rows[1]["lag_close"] = None
    elif damage == "missing-label-value":
        rows[-1]["label_close"] = "100"
    elif damage == "duplicate-chain":
        manifest["derived_lineage"].append(manifest["derived_lineage"][0])
    configuration = config()
    with pytest.raises(ValueError):
        CandleReplayReader(manifest, rows, configuration)


def test_decision_clock_outside_quality_budget_is_rejected() -> None:
    manifest, rows = snapshot()
    reader = CandleReplayReader(manifest, rows, config())
    unbounded = ReplayClock(START + STEP, uncertainty_ns=100_000_001).sample
    with pytest.raises(ValueError, match="uncertainty"):
        reader.features(0, unbounded)
    with pytest.raises(ValueError, match="uncertainty"):
        reader.target(0, unbounded)


def test_no_training_partition_excludes_evaluation_cases_explicitly() -> None:
    result = evaluate(
        CandleReplayReader(
            *snapshot(),
            ReplayConfig(
                START - 3 * STEP,
                START - 2 * STEP,
                START + 6 * STEP,
                START + 13 * STEP,
                coverage="include",
                time_quality="include",
            ),
        )
    )
    assert result["status"] == "no-training-data"
    assert any(item["reason"] == "no-training-data" for item in result["exclusions"])


def test_empty_snapshot_produces_explicit_empty_report() -> None:
    result = evaluate(CandleReplayReader(*snapshot(()), config()))
    assert result["counts"] == {"candidates": 0, "scored": 0, "excluded": 0}
    assert result["status"] == "no-training-data"
