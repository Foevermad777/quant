import unittest

from ops.weekly_review import bootstrap_mean_ci, expectancy, max_drawdown, profit_loss_ratio


class WeeklyReviewMetricTests(unittest.TestCase):
    def test_profit_loss_ratio_uses_total_wins_over_total_losses(self) -> None:
        self.assertEqual(profit_loss_ratio([100.0, -50.0, 25.0]), 2.5)

    def test_profit_loss_ratio_handles_no_losses(self) -> None:
        self.assertIsNone(profit_loss_ratio([100.0, 25.0]))

    def test_expectancy_is_mean_pnl_per_trade(self) -> None:
        self.assertEqual(expectancy([100.0, -50.0, 25.0]), 25.0)
        self.assertIsNone(expectancy([]))

    def test_max_drawdown_returns_peak_to_trough_rate(self) -> None:
        self.assertEqual(max_drawdown([100.0, 120.0, 90.0, 130.0]), 0.25)

    def test_bootstrap_mean_ci_is_deterministic_and_contains_mean(self) -> None:
        lower, upper = bootstrap_mean_ci([0.01, -0.02, 0.03, 0.04], samples=200, seed=7)

        self.assertLessEqual(lower, 0.015)
        self.assertGreaterEqual(upper, 0.015)
        self.assertEqual((lower, upper), bootstrap_mean_ci([0.01, -0.02, 0.03, 0.04], samples=200, seed=7))


if __name__ == "__main__":
    unittest.main()
