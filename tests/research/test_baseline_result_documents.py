from copy import deepcopy
from typing import Any

import pytest

from scryntic.research.baseline_jobs import EXPECTED_INPUT_SHA256, workload_identity
from scryntic.research.baseline_models import fit_model
from scryntic.research.baseline_results import validate_result
from scryntic.research.baseline_scores import score_rows
from scryntic.research.baselines import (
    DAY,
    SOURCE_USE_SHA256,
    SPEC_SHA256,
    Row,
    body_sha256,
    utc_ns,
)


def document() -> dict[str, Any]:
    scores: dict[str, Any] = {}
    parameters: dict[str, Any] = {
        "intercept": "0.0",
        "means": [],
        "scales": [],
        "coefficients": [],
        "quantiles": [],
        "direction_probability": None,
        "empirical_sha256": None,
    }
    for asset in ("BTCUSDT", "ETHUSDT"):
        scores[asset] = {}
        for horizon in (3600, 86400, 604800, 2419200):
            rows = [
                Row(
                    asset,
                    utc_ns("2021-09-01T00:00:00Z") + i * DAY,
                    horizon,
                    (0.1,) * 24,
                    y,
                )
                for i, y in enumerate((-0.1, 0.2))
            ]
            score = score_rows(fit_model("zero", rows), rows)
            score.update(
                training_rows=2,
                training_exclusions={},
                evaluation_exclusions={},
                model_parameters=deepcopy(parameters),
                model_parameters_sha256=body_sha256(parameters),
            )
            scores[asset][str(horizon)] = score
    return {
        "schema": "scryntic.pi03.baseline-result.v1",
        "spec_sha256": SPEC_SHA256,
        "input_sha256": EXPECTED_INPUT_SHA256,
        "workload_sha256": workload_identity(),
        "source_use_sha256": SOURCE_USE_SHA256,
        "algorithm": "zero",
        "evidence_class": "historical-reconstruction",
        "dependence_days": 73,
        "prefit_counts_sha256": "a" * 64,
        "scores": scores,
        "final_labels_accessed": False,
        "promotion_allowed": False,
        "resource_usage": {"elapsed_ns": 1, "cpu_ns": 1, "max_rss_kib": 1},
        "controls_sha256": "b" * 64,
    }


def test_complete_finite_document_is_valid() -> None:
    validate_result(document(), "zero", EXPECTED_INPUT_SHA256)


@pytest.mark.parametrize(
    "defect",
    [
        "nan",
        "infinity",
        "count",
        "probability",
        "parameters",
        "usage",
        "future",
        "extra",
    ],
)
def test_tampered_worker_results_are_denied_before_acceptance(defect: str) -> None:
    value = document()
    score = value["scores"]["BTCUSDT"]["3600"]
    if defect == "nan":
        score["mae"] = "nan"
    if defect == "infinity":
        score["daily_losses"][0][2] = "inf"
    if defect == "count":
        score["count"] += 1
    if defect == "probability":
        score["probabilistic"] = {}
    if defect == "parameters":
        score["model_parameters"]["intercept"] = "1.0"
    if defect == "usage":
        value["resource_usage"]["cpu_ns"] = 91 * 10**9
    if defect == "future":
        value["final_labels_accessed"] = True
    if defect == "extra":
        value["extra"] = True
    with pytest.raises(ValueError):
        validate_result(value, "zero", EXPECTED_INPUT_SHA256)
