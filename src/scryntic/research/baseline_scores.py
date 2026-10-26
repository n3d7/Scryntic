"""Return-space scores and synchronized chronological development comparisons."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from hashlib import sha256
from math import log, sqrt
from random import Random
from statistics import fmean
from typing import Any

from scryntic.research.baseline_models import (
    QUANTILES,
    Predictor,
    fit_model,
    quantile,
)
from scryntic.research.baselines import (
    ALGORITHMS,
    DAY,
    SPEC_SHA256,
    Row,
    body_sha256,
    eligible_indices,
    prefit_counts,
    rows,
    validate_input,
)


@dataclass(slots=True)
class _ProbabilityScores:
    pinball: list[float] = field(default_factory=lambda: [0.0] * 5)
    coverage: list[int] = field(default_factory=lambda: [0, 0])
    width: list[float] = field(default_factory=lambda: [0.0, 0.0])
    brier: float = 0.0
    logarithmic: float = 0.0
    binary: int = 0
    reliability: dict[int, list[int]] = field(
        default_factory=lambda: defaultdict(lambda: [0, 0])
    )

    def add(
        self, y: float, qs: tuple[float, ...], p: float, block: list[float]
    ) -> None:
        for i, q in enumerate(QUANTILES):
            residual = y - qs[i]
            self.pinball[i] += max(q * residual, (q - 1) * residual)
        for i, (lo, hi) in enumerate(((1, 3), (0, 4))):
            covered = qs[lo] <= y <= qs[hi]
            self.coverage[i] += covered
            self.width[i] += qs[hi] - qs[lo]
            block[3 + i] += covered
            block[5 + i] += qs[hi] - qs[lo]
        if y != 0:
            actual = int(y > 0)
            self.brier += (p - actual) ** 2
            self.logarithmic -= log(p if actual else 1 - p)
            self.binary += 1
            self.reliability[min(9, int(p * 10))][0] += 1
            self.reliability[min(9, int(p * 10))][1] += actual
            block[7] += 1

    def report(self, probability: float, n: int) -> dict[str, Any]:
        return {
            "pinball": [repr(x / n) for x in self.pinball],
            "coverage": [repr(x / n) for x in self.coverage],
            "width": [repr(x / n) for x in self.width],
            "direction_brier": repr(self.brier / self.binary) if self.binary else None,
            "direction_log_score": repr(self.logarithmic / self.binary)
            if self.binary
            else None,
            "direction_probability": repr(probability),
            "binary_denominator": self.binary,
            "reliability_bins": {
                str(k): v for k, v in sorted(self.reliability.items())
            },
            "calibration_qualified": False,
            "qualification_reason": "development evidence; dependence-aware calibration gates required",
        }


def score_rows(model: Predictor, evaluation: list[Row]) -> dict[str, Any]:
    if not evaluation:
        raise ValueError("No paired evaluation rows")
    pairs = sha256()
    forecasts = sha256()
    daily: dict[int, list[float]] = defaultdict(lambda: [0.0] * 8)
    absolute = squared = magnitude = 0.0
    ties = target_ties = positive = negative = true_positive = true_negative = 0
    probability_scores = _ProbabilityScores()
    samples: list[dict[str, Any]] = []
    for row in evaluation:
        point, qs, p = model.predict(row)
        y = row.target
        sample = {
            "origin_ns": row.origin_ns,
            "target": repr(y),
            "point": repr(point),
            "quantiles": [repr(x) for x in qs],
            "direction_probability": repr(p) if p is not None else None,
        }
        if len(samples) < 2:
            samples.append(sample)
        else:
            samples[1] = sample
        error = abs(y - point)
        absolute += error
        squared += (y - point) ** 2
        magnitude += abs(abs(y) - abs(point))
        pairs.update(f"{row.asset}:{row.origin_ns}:{row.horizon}:{y!r}\n".encode())
        forecasts.update(f"{point!r}:{qs!r}:{p!r}\n".encode())
        block = daily[row.origin_ns // DAY * DAY]
        block[0] += 1
        block[1] += error
        block[2] += (y - point) ** 2
        ties += point == 0
        target_ties += y == 0
        positive += y > 0
        negative += y < 0
        true_positive += y > 0 and point > 0
        true_negative += y < 0 and point < 0
        if qs and p is not None:
            probability_scores.add(y, qs, p, block)
    n = len(evaluation)
    probabilistic = (
        probability_scores.report(model.probability, n)
        if model.probability is not None
        else None
    )
    return {
        "count": n,
        "mae": repr(absolute / n),
        "rmse": repr(sqrt(squared / n)),
        "magnitude_mae": repr(magnitude / n),
        "direction_balanced_accuracy": repr(
            (true_positive / positive + true_negative / negative) / 2
        )
        if positive and negative
        else None,
        "direction_prediction_ties": ties,
        "direction_target_ties": target_ties,
        "positive_targets": positive,
        "negative_targets": negative,
        "pairs_sha256": pairs.hexdigest(),
        "forecasts_sha256": forecasts.hexdigest(),
        "daily_losses": [
            [day, int(v[0]), *[repr(x) for x in v[1:]]]
            for day, v in sorted(daily.items())
        ],
        "probabilistic": probabilistic,
        "samples": samples,
    }


def evaluate_algorithm(data: bytes, algorithm: str) -> dict[str, Any]:
    """Worker-only entry: caller must verify effective controls before invoking."""
    value = validate_input(data)
    if algorithm not in ALGORITHMS:
        raise ValueError("Unregistered PI03 algorithm")
    counts = prefit_counts(data)
    dependence = counts["training_dependence_days"]
    results: dict[str, Any] = {}
    for asset in value["spec"]["instruments"]:
        results[asset] = {}
        for horizon in value["spec"]["horizons_seconds"]:
            training = rows(value, asset, horizon, True)
            model = fit_model(algorithm, training)
            # Only after the fixed model is frozen are evaluation labels formed.
            evaluation = rows(value, asset, horizon, False)
            score = score_rows(model, evaluation)
            _, training_exclusions = eligible_indices(value, horizon, True)
            parameters = {
                "intercept": repr(model.intercept),
                "means": [repr(x) for x in model.means],
                "scales": [repr(x) for x in model.scales],
                "coefficients": [repr(x) for x in model.coefficients],
                "quantiles": [repr(quantile(model.empirical, q)) for q in QUANTILES]
                if model.empirical
                else [],
                "direction_probability": repr(model.probability)
                if model.probability is not None
                else None,
                "empirical_sha256": body_sha256([repr(x) for x in model.empirical])
                if model.empirical
                else None,
            }
            score.update(
                training_rows=len(training),
                training_exclusions=training_exclusions,
                evaluation_exclusions=counts["horizons"][str(horizon)]["exclusions"],
                model_parameters=parameters,
                model_parameters_sha256=body_sha256(parameters),
            )
            results[asset][str(horizon)] = score
    return {
        "schema": "scryntic.pi03.baseline-result.v1",
        "spec_sha256": SPEC_SHA256,
        "input_sha256": sha256(data).hexdigest(),
        "source_use_sha256": value["source_use_sha256"],
        "algorithm": algorithm,
        "evidence_class": "historical-reconstruction",
        "dependence_days": dependence,
        "prefit_counts_sha256": body_sha256(counts),
        "scores": results,
        "final_labels_accessed": False,
        "promotion_allowed": False,
    }


def _window_sums(values: list[float], length: int) -> list[float]:
    prefix = [0.0]
    for value in values:
        prefix.append(prefix[-1] + value)
    return [prefix[i + length] - prefix[i] for i in range(len(values) - length + 1)]


def _append_coverage_draw(
    draws: dict[tuple[str, int], list[float]],
    assets: list[str],
    sums: dict[tuple[str, int], list[float]],
    tails: dict[tuple[str, int], list[float]],
    starts: list[int],
    tail: int,
    last: int,
) -> None:
    for asset in assets:
        count = sum(sums[asset, 1][i] for i in starts) + (
            tails[asset, 1][last] if tail else 0
        )
        for column in (4, 5):
            covered = sum(sums[asset, column][i] for i in starts) + (
                tails[asset, column][last] if tail else 0
            )
            draws[asset, column].append(covered / count)


def _coverage_draws(
    result: dict[str, Any], assets: list[str], horizon: str, length: int
) -> tuple[dict[tuple[str, int], list[float]], int]:
    daily = {
        asset: result["scores"][asset][horizon]["daily_losses"] for asset in assets
    }
    n = len(daily[assets[0]])
    window = min(length, n)
    tail = n % window
    vectors = {
        (asset, column): [float(entry[column]) for entry in entries]
        for asset, entries in daily.items()
        for column in (1, 4, 5)
    }
    sums = {key: _window_sums(values, window) for key, values in vectors.items()}
    tails = (
        {key: _window_sums(values, tail) for key, values in vectors.items()}
        if tail
        else {}
    )
    draws: dict[tuple[str, int], list[float]] = {
        (asset, column): [] for asset in assets for column in (4, 5)
    }
    rng = Random(42)
    for _ in range(2000):
        starts = [rng.randrange(n - window + 1) for _ in range(n // window)]
        last = rng.randrange(n - tail + 1) if tail else 0
        _append_coverage_draw(draws, assets, sums, tails, starts, tail, last)
    return draws, n


def _coverage_intervals(
    result: dict[str, Any], assets: list[str], horizon: str, length: int
) -> dict[str, Any]:
    draws, n = _coverage_draws(result, assets, horizon, length)
    report = {}
    for asset in assets:
        probabilistic = result["scores"][asset][horizon]["probabilistic"]
        if probabilistic is None:
            raise ValueError(
                "Declared probabilistic configuration has no distribution scores"
            )
        intervals = []
        for i, column in enumerate((4, 5)):
            distribution = sorted(draws[asset, column])
            intervals.append(
                {
                    "nominal": "0.8" if i == 0 else "0.95",
                    "coverage": probabilistic["coverage"][i],
                    "width": probabilistic["width"][i],
                    "coverage_lower_95": repr(quantile(distribution, 0.025)),
                    "coverage_upper_95": repr(quantile(distribution, 0.975)),
                    "absolute_error_tolerance_met": abs(
                        float(probabilistic["coverage"][i]) - (0.8 if i == 0 else 0.95)
                    )
                    <= (0.05 if i == 0 else 0.03),
                }
            )
        report[asset] = {
            "intervals": intervals,
            "reliability_time_blocks_upper_bound": (n + length - 1) // length,
            "calibration_qualified": False,
            "evidence": "descriptive development bootstrap; no calibrated-probability claim",
        }
    return report


def _comparison_arrays(
    results: dict[str, dict[str, Any]], assets: list[str], h: str, days: list[int]
) -> dict[tuple[str, str], tuple[list[float], list[float]]]:
    arrays: dict[tuple[str, str], tuple[list[float], list[float]]] = {}
    for a in ALGORITHMS:
        for asset in assets:
            entries = results[a]["scores"][asset][h]["daily_losses"]
            if [e[0] for e in entries] != days:
                raise ValueError("Cross-asset UTC window drift")
            arrays[a, asset] = (
                [float(e[2]) for e in entries],
                [float(e[1]) for e in entries],
            )
    return arrays


def _sampled_mae(
    assets: list[str],
    sums: dict[tuple[str, str], tuple[list[float], list[float]]],
    tail_sums: dict[tuple[str, str], tuple[list[float], list[float]]],
    starts: list[int],
    tail: int,
    last: int,
) -> dict[str, float]:
    sampled: dict[str, float] = {}
    for a in ALGORITHMS:
        losses = []
        for asset in assets:
            numerator, denominator = sums[a, asset]
            total = sum(numerator[i] for i in starts)
            count = sum(denominator[i] for i in starts)
            if tail:
                ta, tn = tail_sums[a, asset]
                total += ta[last]
                count += tn[last]
            losses.append(total / count)
        sampled[a] = fmean(losses)
    return sampled


def _skill_draws(
    arrays: dict[tuple[str, str], tuple[list[float], list[float]]],
    assets: list[str],
    n: int,
    length: int,
    champion: str,
) -> dict[str, list[float]]:
    window = min(length, n)
    sums = {
        key: (_window_sums(a, window), _window_sums(n, window))
        for key, (a, n) in arrays.items()
    }
    tail = n % window
    tail_sums = (
        {
            key: (_window_sums(a, tail), _window_sums(n, tail))
            for key, (a, n) in arrays.items()
        }
        if tail
        else {}
    )
    draws: dict[str, list[float]] = {a: [] for a in ALGORITHMS}
    rng = Random(42)
    for _ in range(2000):
        starts = [rng.randrange(n - window + 1) for _ in range(n // window)]
        last = rng.randrange(n - tail + 1) if tail else 0
        sampled = _sampled_mae(assets, sums, tail_sums, starts, tail, last)
        for a in ALGORITHMS:
            draws[a].append(
                1 - sampled[a] / sampled[champion] if sampled[champion] else 0.0
            )
    return draws


def _compare_horizon(
    results: dict[str, dict[str, Any]],
    spec: dict[str, Any],
    horizon: int,
    family: int,
    all_p: list[tuple[float, str, str]],
) -> dict[str, Any]:
    h = str(horizon)
    assets = spec["instruments"]
    for asset in assets:
        pairs = {
            (
                r["scores"][asset][h]["pairs_sha256"],
                r["scores"][asset][h]["count"],
                body_sha256(r["scores"][asset][h]["evaluation_exclusions"]),
            )
            for r in results.values()
        }
        if len(pairs) != 1:
            raise ValueError("Comparison pairs/exclusions differ")
    mae = {
        a: fmean(float(results[a]["scores"][asset][h]["mae"]) for asset in assets)
        for a in ALGORITHMS
    }
    champion = min(ALGORITHMS[:4], key=lambda a: (mae[a], a))
    days = [
        entry[0] for entry in results[champion]["scores"][assets[0]][h]["daily_losses"]
    ]
    if any(b - a != DAY for a, b in zip(days, days[1:], strict=False)):
        raise ValueError(
            "Noncontiguous UTC comparison windows require a new registration"
        )
    length = max(1, horizon // 86400, *(r["dependence_days"] for r in results.values()))
    effective = (len(days) + length - 1) // length
    floor = spec["comparison"]["minimum_blocks"][h]
    arrays = _comparison_arrays(results, assets, h, days)
    draws = _skill_draws(arrays, assets, len(days), length, champion)
    contrasts: dict[str, Any] = {}
    for a in ALGORITHMS:
        distribution = sorted(draws[a])
        lower = quantile(distribution, 0.025 / family)
        skill = 1 - mae[a] / mae[champion] if mae[champion] else 0.0
        # Paired bootstrap centered under the zero-skill null.
        p = (1 + sum(x - skill >= skill for x in distribution)) / 2001
        all_p.append((p, h, a))
        contrasts[a] = {
            "relative_mae_skill": repr(skill),
            "simultaneous_lower_95": repr(lower),
            "bootstrap_p": repr(p),
            "margin_bound_pass": lower > 0.02,
            "holm_reject": False,
            "claim_allowed": False,
        }
    comparison = {
        "champion": champion,
        "equal_asset_mae": {a: repr(v) for a, v in mae.items()},
        "block_days": length,
        "effective_time_blocks_upper_bound": effective,
        "required_blocks": floor,
        "block_floor_met": effective >= floor,
        "contrasts": contrasts,
        "probabilistic_calibration": _coverage_intervals(
            results["volatility-empirical"], assets, h, length
        ),
    }
    return comparison


def compare_results(
    results: dict[str, dict[str, Any]], spec: dict[str, Any]
) -> dict[str, Any]:
    """Compute paired moving-window contrasts without selecting new algorithms."""
    from scryntic.research.baselines import check_spec

    check_spec(spec)
    if set(results) != set(ALGORITHMS):
        raise ValueError("Failed/missing registered configurations prevent comparison")
    identities = {
        (
            r["spec_sha256"],
            r["input_sha256"],
            r["prefit_counts_sha256"],
            r["workload_sha256"],
        )
        for r in results.values()
    }
    if len(identities) != 1:
        raise ValueError("Comparison input identity drift")
    family = len(ALGORITHMS) * len(spec["horizons_seconds"])
    comparisons: dict[str, Any] = {}
    all_p: list[tuple[float, str, str]] = []
    for horizon in spec["horizons_seconds"]:
        comparisons[str(horizon)] = _compare_horizon(
            results, spec, horizon, family, all_p
        )
    for rank, (p, h, a) in enumerate(sorted(all_p)):
        if p > 0.05 / (family - rank):
            break
        comparisons[h]["contrasts"][a]["holm_reject"] = True
    return {
        "schema": "scryntic.pi03.comparison.v1",
        "spec_sha256": SPEC_SHA256,
        "input_sha256": next(iter(results.values()))["input_sha256"],
        "horizons": comparisons,
        "bootstrap_draws": 2000,
        "seed": 42,
        "family_size_including_all_declared_configurations": family,
        "equal_horizon_mae_skill": {
            a: repr(
                fmean(
                    float(c["contrasts"][a]["relative_mae_skill"])
                    for c in comparisons.values()
                )
            )
            for a in ALGORITHMS
        },
        "aggregate_claim_allowed": False,
        "decision": "retain-baseline",
        "promotion_allowed": False,
        "final_labels_accessed": False,
        "evidence_class": "historical-reconstruction; development only; first-seen times unknown",
    }
