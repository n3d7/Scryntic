import unittest

from scryntic.research.baseline_models import fit_model
from scryntic.research.baseline_scores import score_rows
from scryntic.research.baselines import DAY, Row


class ScoreTests(unittest.TestCase):
    def test_returns_scores_ties_and_pair_identity(self) -> None:
        training = [
            Row("BTCUSDT", i * DAY, 3600, (0.1,) * 24, y)
            for i, y in enumerate([-0.2, 0.2])
        ]
        zero = score_rows(fit_model("zero", training), training)
        mean = score_rows(fit_model("historical-mean", training), training)
        self.assertEqual(zero["mae"], "0.2")
        self.assertEqual(zero["direction_prediction_ties"], 2)
        self.assertEqual(zero["pairs_sha256"], mean["pairs_sha256"])
        self.assertIsNone(zero["probabilistic"])
        self.assertEqual(zero["daily_losses"], mean["daily_losses"])

    def test_probabilistic_scores_are_real_and_explicitly_unqualified(self) -> None:
        training = [
            Row("BTCUSDT", i * DAY, 3600, (0.1,) * 24, y)
            for i, y in enumerate([-0.2, 0.4])
        ]
        result = score_rows(fit_model("volatility-empirical", training), training)
        probabilistic = result["probabilistic"]
        self.assertEqual(probabilistic["direction_brier"], "0.25")
        self.assertFalse(probabilistic["calibration_qualified"])
        self.assertEqual(len(probabilistic["pinball"]), 5)


if __name__ == "__main__":
    unittest.main()
