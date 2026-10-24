from copy import deepcopy
from typing import Any

import pytest

from scryntic.research.baseline_jobs import EXPECTED_INPUT_SHA256, workload_identity
from scryntic.research.baseline_models import fit_model
from scryntic.research.baseline_scores import compare_results, score_rows
from scryntic.research.baselines import (
    ALGORITHMS,
    DAY,
    SPEC_SHA256,
    Row,
    load_spec,
    utc_ns,
)
from tests.research.test_baselines import SPEC


def results() -> dict[str, dict[str, Any]]:
    outputs = {}
    for algorithm in ALGORITHMS:
        scores: dict[str, Any] = {}
        for asset in ("BTCUSDT", "ETHUSDT"):
            scores[asset] = {}
            for horizon in (3600, 86400, 604800, 2419200):
                training = [
                    Row(asset, i * DAY, horizon, (0.1,) * 24, y)
                    for i, y in enumerate((0.8, 1.2))
                ]
                evaluation = [
                    Row(
                        asset,
                        utc_ns("2021-09-01T00:00:00Z") + i * DAY,
                        horizon,
                        (0.1,) * 24,
                        y,
                    )
                    for i, y in enumerate((-0.1, 0.2))
                ]
                score = score_rows(fit_model(algorithm, training), evaluation)
                score["evaluation_exclusions"] = {}
                scores[asset][str(horizon)] = score
        outputs[algorithm] = {
            "spec_sha256": SPEC_SHA256,
            "input_sha256": EXPECTED_INPUT_SHA256,
            "workload_sha256": workload_identity(),
            "prefit_counts_sha256": "a" * 64,
            "dependence_days": 1,
            "scores": scores,
        }
    return outputs


def test_reproducible_paired_bootstrap_retains_baseline_on_null_or_insufficient_evidence() -> (
    None
):
    value = results()
    first = compare_results(value, load_spec(SPEC))
    assert first == compare_results(value, load_spec(SPEC))
    assert first["bootstrap_draws"] == 2000 and first["seed"] == 42
    assert first["decision"] == "retain-baseline"
    assert not first["aggregate_claim_allowed"] and not first["promotion_allowed"]
    for horizon in first["horizons"].values():
        assert horizon["champion"] == "zero"
        assert not horizon["block_floor_met"]
        assert all(not c["claim_allowed"] for c in horizon["contrasts"].values())
        assert not horizon["probabilistic_calibration"]["BTCUSDT"][
            "calibration_qualified"
        ]


@pytest.mark.parametrize(
    "defect", ["pair", "exclusion", "asset-clock", "gap", "failed-family"]
)
def test_pair_and_chronology_drift_or_missing_failed_configuration_denies_comparison(
    defect: str,
) -> None:
    value = results()
    score = value["ridge-ar-1"]["scores"]["ETHUSDT"]["3600"]
    if defect == "pair":
        score["pairs_sha256"] = "b" * 64
    if defect == "exclusion":
        score["evaluation_exclusions"] = {"incomplete-common-pair": 1}
    if defect == "asset-clock":
        score["daily_losses"][0][0] += DAY
    if defect == "gap":
        for result in value.values():
            for asset in ("BTCUSDT", "ETHUSDT"):
                result["scores"][asset]["3600"]["daily_losses"][1][0] += DAY
    if defect == "failed-family":
        value.pop("ridge-ar-24")
    with pytest.raises(ValueError):
        compare_results(value, load_spec(SPEC))


def test_bootstrap_synchronizes_asset_windows_under_a_common_loss_rescaling() -> None:
    value = results()
    scaled = deepcopy(value)
    for result in scaled.values():
        for score in result["scores"]["ETHUSDT"].values():
            score["mae"] = repr(float(score["mae"]) * 10)
            for day in score["daily_losses"]:
                day[2] = repr(float(day[2]) * 10)
    a = compare_results(value, load_spec(SPEC))
    b = compare_results(scaled, load_spec(SPEC))
    for horizon in a["horizons"]:
        ca = a["horizons"][horizon]["contrasts"]["historical-mean"]
        cb = b["horizons"][horizon]["contrasts"]["historical-mean"]
        assert (
            abs(float(ca["simultaneous_lower_95"]) - float(cb["simultaneous_lower_95"]))
            < 1e-12
        )
