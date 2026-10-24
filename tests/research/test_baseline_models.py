import unittest
from random import Random
from typing import Any

import pytest

from scryntic.research.baseline_models import QUANTILES, fit_model, quantile
from scryntic.research.baseline_results import FrozenPredictor
from scryntic.research.baseline_scores import score_rows
from scryntic.research.baselines import ALGORITHMS, DAY, Row


def row(x: float, y: float) -> Row:
    return Row("BTCUSDT", 0, 3600, (x,) * 24, y)


class ModelTests(unittest.TestCase):
    def test_train_only_mean_and_no_evaluation_refit(self) -> None:
        model = fit_model("historical-mean", [row(0.1, 1.0), row(0.2, 3.0)])
        self.assertEqual(model.predict(row(1000.0, -999.0))[0], 2.0)

    def test_fixed_ridge_standardization_and_intercept(self) -> None:
        model = fit_model("ridge-ar-1", [row(-1.0, 1.0), row(1.0, 5.0)])
        self.assertAlmostEqual(model.predict(row(0.0, 999.0))[0], 3.0)
        self.assertAlmostEqual(model.predict(row(1.0, 999.0))[0], 3.0 + 4.0 / 3.0)
        self.assertEqual(model.means, (-0.0,))
        self.assertEqual(model.scales, (1.0,))

    def test_empirical_distribution_rescales_without_using_target(self) -> None:
        model = fit_model("volatility-empirical", [row(0.1, -0.2), row(0.1, 0.4)])
        point, qs, probability = model.predict(row(0.2, 999.0))
        self.assertAlmostEqual(point, 0.2)
        self.assertAlmostEqual(qs[0], -0.37)
        self.assertEqual(probability, 0.5)
        self.assertEqual(model.predict(row(0.2, -999.0)), (point, qs, probability))

    def test_quantile_and_constant_features(self) -> None:
        self.assertEqual(quantile([1.0, 3.0], 0.5), 2.0)
        model = fit_model("ridge-ar-24", [row(0.0, 2.0), row(0.0, 4.0)])
        self.assertEqual(model.predict(row(100.0, -1.0))[0], 3.0)


if __name__ == "__main__":
    unittest.main()


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_frozen_parameter_round_trip_reproduces_all_scores_without_refitting(
    algorithm: str,
) -> None:
    rng = Random(42)
    training = [
        Row(
            "BTCUSDT",
            i * DAY,
            604800,
            tuple(rng.uniform(-0.01, 0.01) for _ in range(24)),
            rng.uniform(-0.1, 0.1),
        )
        for i in range(80)
    ]
    evaluation = [
        Row(
            "BTCUSDT",
            i * DAY,
            604800,
            tuple(rng.uniform(-0.01, 0.01) for _ in range(24)),
            rng.uniform(-0.1, 0.1),
        )
        for i in range(20)
    ]
    model = fit_model(algorithm, training)
    parameters: dict[str, Any] = {
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
    }
    assert score_rows(model, evaluation) == score_rows(
        FrozenPredictor(parameters), evaluation
    )
