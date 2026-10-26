"""Closed numerical result validation before immutable PI03 acceptance."""

from __future__ import annotations

from dataclasses import dataclass
from math import isclose, isfinite, sqrt
from statistics import fmean
from typing import Any

from scryntic.domain.validation import digest
from scryntic.research.baselines import (
    DAY,
    SOURCE_USE_SHA256,
    SPEC_SHA256,
    Row,
    body_sha256,
    utc_ns,
)


@dataclass(frozen=True, slots=True)
class FrozenPredictor:
    """Recompute predictions from frozen parameters without fitting."""

    parameters: dict[str, Any]

    @property
    def probability(self) -> float | None:
        value = self.parameters["direction_probability"]
        return float(value) if value is not None else None

    def predict(self, row: Row) -> tuple[float, tuple[float, ...], float | None]:
        parameters = self.parameters
        qs = parameters["quantiles"]
        if qs:
            scale = max(1e-12, sqrt(fmean(x * x for x in row.features))) * sqrt(
                row.horizon / 3600
            )
            values = tuple(float(q) * scale for q in qs)
            return values[2], values, self.probability
        point = float(parameters["intercept"]) + sum(
            float(coefficient)
            * (row.features[i] - float(parameters["means"][i]))
            / float(parameters["scales"][i])
            for i, coefficient in enumerate(parameters["coefficients"])
        )
        return point, (), None


def _fields(value: Any, names: str) -> None:
    if type(value) is not dict or set(value) != set(names.split()):
        raise ValueError("Unknown or missing PI03 result fields")


def _integer(value: Any, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("Invalid PI03 result integer")
    return value


def _number(
    value: Any, minimum: float | None = None, maximum: float | None = None
) -> float:
    if type(value) is not str or len(value) > 64:
        raise ValueError("Invalid PI03 decimal representation")
    result = float(value)
    if (
        not isfinite(result)
        or repr(result) != value
        or (minimum is not None and result < minimum)
        or (maximum is not None and result > maximum)
    ):
        raise ValueError("Nonfinite or noncanonical PI03 result number")
    return result


def _numbers(value: Any, count: int) -> list[float]:
    if type(value) is not list or len(value) != count:
        raise ValueError("Invalid PI03 numeric vector")
    return [_number(x) for x in value]


def _exclusions(value: Any) -> None:
    allowed = {
        "immature-or-boundary-label",
        "missing-feature-history",
        "purge-or-embargo",
        "incomplete-common-pair",
    }
    if type(value) is not dict or not set(value) <= allowed:
        raise ValueError("Invalid PI03 exclusion reasons")
    for count in value.values():
        _integer(count, 0, 60000)


def _parameters(value: Any, algorithm: str) -> None:
    _fields(
        value,
        "intercept means scales coefficients quantiles direction_probability empirical_sha256",
    )
    intercept = _number(value["intercept"])
    lags = {"ridge-ar-1": 1, "ridge-ar-24": 24}.get(algorithm, 0)
    _numbers(value["means"], lags)
    scales = _numbers(value["scales"], lags)
    _numbers(value["coefficients"], lags)
    if any(x <= 0 for x in scales):
        raise ValueError("Invalid training-only standardization")
    qs = _numbers(value["quantiles"], 5 if algorithm == "volatility-empirical" else 0)
    if qs:
        if qs != sorted(qs) or intercept != 0:
            raise ValueError("Invalid empirical distribution parameters")
        if not 0 < _number(value["direction_probability"]) < 1:
            raise ValueError(
                "Direction probability must be strictly inside zero and one"
            )
        digest(value["empirical_sha256"])
    elif (
        value["direction_probability"] is not None
        or value["empirical_sha256"] is not None
    ):
        raise ValueError("Point algorithm cannot invent a distribution")
    if algorithm == "zero" and intercept != 0:
        raise ValueError("Zero-return baseline changed")


def _probabilistic(
    value: Any, count: int, ties: int, parameters: dict[str, Any]
) -> None:
    _fields(
        value,
        "pinball coverage width direction_brier direction_log_score direction_probability binary_denominator reliability_bins calibration_qualified qualification_reason",
    )
    if (
        any(x < 0 for x in _numbers(value["pinball"], 5))
        or any(not 0 <= x <= 1 for x in _numbers(value["coverage"], 2))
        or any(x < 0 for x in _numbers(value["width"], 2))
        or value["calibration_qualified"] is not False
        or value["qualification_reason"]
        != "development evidence; dependence-aware calibration gates required"
        or value["direction_probability"] != parameters["direction_probability"]
    ):
        raise ValueError("Invalid probabilistic scoring/calibration claim")
    binary = _integer(value["binary_denominator"], 0, count)
    if binary != count - ties:
        raise ValueError("Direction tie denominator mismatch")
    for key in ("direction_brier", "direction_log_score"):
        if binary:
            _number(value[key], 0)
        elif value[key] is not None:
            raise ValueError("Unsupported empty binary score")
    bins = value["reliability_bins"]
    if type(bins) is not dict or not set(bins) <= {str(i) for i in range(10)}:
        raise ValueError("Invalid direction reliability bins")
    total = 0
    for entry in bins.values():
        if type(entry) is not list or len(entry) != 2:
            raise ValueError("Invalid direction reliability counts")
        n = _integer(entry[0], 0, count)
        _integer(entry[1], 0, n)
        total += n
    if total != binary:
        raise ValueError("Reliability denominator mismatch")


def _daily_losses(
    daily: Any, horizon: int, count: int, mae: float, rmse: float
) -> None:
    n = 0
    absolute = squared = 0.0
    previous = 0
    for entry in daily:
        if type(entry) is not list or len(entry) != 9:
            raise ValueError("Invalid daily loss row")
        day = _integer(
            entry[0],
            utc_ns("2021-09-01T00:00:00Z"),
            utc_ns("2025-01-01T00:00:00Z") - DAY,
        )
        size = _integer(entry[1], 1, 24 if horizon == 3600 else 1)
        values = [_number(x, 0) for x in entry[2:]]
        if (
            day % DAY
            or day <= previous
            or any(x > size or int(x) != x for x in (values[2], values[3], values[6]))
        ):
            raise ValueError("Invalid UTC daily count/coverage")
        previous = day
        n += size
        absolute += values[0]
        squared += values[1]
    if (
        n != count
        or not isclose(absolute / n, mae, rel_tol=1e-10, abs_tol=1e-12)
        or not isclose(squared / n, rmse * rmse, rel_tol=1e-10, abs_tol=1e-12)
    ):
        raise ValueError("PI03 loss denominator or score mismatch")


def _samples(
    samples: Any,
    count: int,
    daily: list[Any],
    algorithm: str,
    parameters: dict[str, Any],
) -> None:
    if type(samples) is not list or len(samples) != min(2, count):
        raise ValueError("Missing bounded numerical verification samples")
    for sample in samples:
        _fields(sample, "origin_ns target point quantiles direction_probability")
        _integer(sample["origin_ns"], daily[0][0], daily[-1][0] + DAY - 1)
        _number(sample["target"])
        _number(sample["point"])
        _numbers(sample["quantiles"], 5 if algorithm == "volatility-empirical" else 0)
        if sample["direction_probability"] != parameters["direction_probability"]:
            raise ValueError("Direction probability sample drift")


def _score(score: Any, algorithm: str, horizon: int) -> None:
    _fields(
        score,
        "count mae rmse magnitude_mae direction_balanced_accuracy direction_prediction_ties direction_target_ties positive_targets negative_targets pairs_sha256 forecasts_sha256 daily_losses probabilistic samples training_rows training_exclusions evaluation_exclusions model_parameters model_parameters_sha256",
    )
    count = _integer(score["count"], 1, 60000)
    _integer(score["training_rows"], 2, 60000)
    mae = _number(score["mae"], 0)
    rmse = _number(score["rmse"], 0)
    _number(score["magnitude_mae"], 0)
    _integer(score["direction_prediction_ties"], 0, count)
    ties = _integer(score["direction_target_ties"], 0, count)
    pos = _integer(score["positive_targets"], 0, count)
    neg = _integer(score["negative_targets"], 0, count)
    if pos + neg + ties != count:
        raise ValueError("Direction denominator mismatch")
    if score["direction_balanced_accuracy"] is not None:
        _number(score["direction_balanced_accuracy"], 0, 1)
    for key in ("pairs_sha256", "forecasts_sha256", "model_parameters_sha256"):
        digest(score[key])
    for key in ("training_exclusions", "evaluation_exclusions"):
        _exclusions(score[key])
    parameters = score["model_parameters"]
    _parameters(parameters, algorithm)
    if body_sha256(parameters) != score["model_parameters_sha256"]:
        raise ValueError("PI03 model parameters changed")
    daily = score["daily_losses"]
    if type(daily) is not list or not 0 < len(daily) <= 1218:
        raise ValueError("Unbounded PI03 daily losses")
    _daily_losses(daily, horizon, count, mae, rmse)
    if algorithm == "volatility-empirical":
        _probabilistic(score["probabilistic"], count, ties, parameters)
    elif score["probabilistic"] is not None:
        raise ValueError("Point forecast has fabricated probability scores")
    _samples(score["samples"], count, daily, algorithm, parameters)


def validate_result(result: Any, algorithm: str, input_sha256: str) -> None:
    _fields(
        result,
        "schema spec_sha256 input_sha256 workload_sha256 source_use_sha256 algorithm evidence_class dependence_days prefit_counts_sha256 scores final_labels_accessed promotion_allowed resource_usage controls_sha256",
    )
    if (
        result["schema"] != "scryntic.pi03.baseline-result.v1"
        or result["spec_sha256"] != SPEC_SHA256
        or result["source_use_sha256"] != SOURCE_USE_SHA256
        or result["input_sha256"] != input_sha256
        or result["algorithm"] != algorithm
        or result["evidence_class"] != "historical-reconstruction"
        or result["final_labels_accessed"] is not False
        or result["promotion_allowed"] is not False
    ):
        raise ValueError("PI03 result scope/identity mismatch")
    _integer(result["dependence_days"], 1, 113)
    digest(result["workload_sha256"])
    digest(result["prefit_counts_sha256"])
    digest(result["controls_sha256"])
    usage = result["resource_usage"]
    _fields(usage, "elapsed_ns cpu_ns max_rss_kib")
    for key, maximum in {
        "elapsed_ns": 180 * 10**9,
        "cpu_ns": 90 * 10**9,
        "max_rss_kib": 4 * 1024**2,
    }.items():
        _integer(usage[key], 0, maximum)
    _fields(result["scores"], "BTCUSDT ETHUSDT")
    for scores in result["scores"].values():
        _fields(scores, "3600 86400 604800 2419200")
        for horizon, score in scores.items():
            _score(score, algorithm, int(horizon))
