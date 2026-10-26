"""Fixed standard-library PI03 estimators; no search or evaluation refitting."""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import fmean
from typing import Protocol

from scryntic.research.baselines import ALGORITHMS, Row

QUANTILES = (0.025, 0.10, 0.50, 0.90, 0.975)


class Predictor(Protocol):
    @property
    def probability(self) -> float | None: ...

    def predict(self, row: Row) -> tuple[float, tuple[float, ...], float | None]: ...


def quantile(sorted_values: list[float] | tuple[float, ...], q: float) -> float:
    if not sorted_values or not 0 <= q <= 1:
        raise ValueError("Invalid empirical quantile")
    index = (len(sorted_values) - 1) * q
    left = int(index)
    right = min(left + 1, len(sorted_values) - 1)
    return sorted_values[left] + (index - left) * (
        sorted_values[right] - sorted_values[left]
    )


def _scale(row: Row) -> float:
    return max(1e-12, sqrt(fmean(x * x for x in row.features))) * sqrt(
        row.horizon / 3600
    )


@dataclass(frozen=True, slots=True)
class FittedModel:
    algorithm: str
    intercept: float
    means: tuple[float, ...] = ()
    scales: tuple[float, ...] = ()
    coefficients: tuple[float, ...] = ()
    empirical: tuple[float, ...] = ()
    probability: float | None = None

    def predict(self, row: Row) -> tuple[float, tuple[float, ...], float | None]:
        if self.empirical:
            scale = _scale(row)
            values = tuple(quantile(self.empirical, q) * scale for q in QUANTILES)
            return values[2], values, self.probability
        point = self.intercept + sum(
            coefficient * (row.features[i] - self.means[i]) / self.scales[i]
            for i, coefficient in enumerate(self.coefficients)
        )
        return point, (), None


def _solve(matrix: list[list[float]], rhs: list[float]) -> tuple[float, ...]:
    """Cholesky solve of the fixed positive-definite ridge normal equations."""
    n = len(rhs)
    lower = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            value = matrix[i][j] - sum(lower[i][k] * lower[j][k] for k in range(j))
            lower[i][j] = sqrt(value) if i == j else value / lower[j][j]
    intermediate = [0.0] * n
    for i in range(n):
        intermediate[i] = (
            rhs[i] - sum(lower[i][j] * intermediate[j] for j in range(i))
        ) / lower[i][i]
    solution = [0.0] * n
    for i in reversed(range(n)):
        solution[i] = (
            intermediate[i] - sum(lower[j][i] * solution[j] for j in range(i + 1, n))
        ) / lower[i][i]
    return tuple(solution)


def fit_model(algorithm: str, training: list[Row]) -> FittedModel:
    if algorithm not in ALGORITHMS or len(training) < 2:
        raise ValueError("Unregistered algorithm or insufficient training rows")
    if algorithm == "zero":
        return FittedModel(algorithm, 0.0)
    mean = fmean(row.target for row in training)
    if algorithm == "historical-mean":
        return FittedModel(algorithm, mean)
    if algorithm == "volatility-empirical":
        empirical = tuple(sorted(row.target / _scale(row) for row in training))
        nonzero = [x for x in empirical if x != 0]
        probability = (sum(x > 0 for x in nonzero) + 0.5) / (len(nonzero) + 1)
        return FittedModel(algorithm, 0.0, empirical=empirical, probability=probability)
    return _fit_ridge(algorithm, training, mean)


def _fit_ridge(algorithm: str, training: list[Row], mean: float) -> FittedModel:
    lags = 1 if algorithm == "ridge-ar-1" else 24
    means = tuple(fmean(row.features[j] for row in training) for j in range(lags))
    scales = tuple(
        sqrt(fmean((row.features[j] - means[j]) ** 2 for row in training)) or 1.0
        for j in range(lags)
    )
    gram = [[float(i == j) for j in range(lags)] for i in range(lags)]
    rhs = [0.0] * lags
    for row in training:
        x = [(row.features[j] - means[j]) / scales[j] for j in range(lags)]
        y = row.target - mean
        for i in range(lags):
            rhs[i] += x[i] * y
            for j in range(i + 1):
                gram[i][j] += x[i] * x[j]
    for i in range(lags):
        for j in range(i):
            gram[j][i] = gram[i][j]
    return FittedModel(algorithm, mean, means, scales, _solve(gram, rhs))
