import unittest
import sqlite3
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

from executor.config import ExecutorConfig
from executor.signal_reader import SignalReader
from ops.weekly_review import (
    _equal_weight_return,
    _hs300_return,
    bootstrap_mean_ci,
    expectancy,
    max_drawdown,
    profit_loss_ratio,
)


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

    def test_equal_weight_benchmark_is_gross_price_return(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "dsa.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    create table stock_daily (
                        code text, date text, open real, high real, low real, close real,
                        volume real, amount real, pct_chg real
                    )
                    """
                )
                conn.executemany(
                    """
                    insert into stock_daily(code, date, open, high, low, close, volume, amount, pct_chg)
                    values (?, ?, ?, ?, ?, ?, null, null, null)
                    """,
                    [
                        ("600519", "2026-07-06", 10.0, 11.0, 9.0, 10.5),
                        ("300750", "2026-07-06", 20.0, 21.0, 19.0, 20.5),
                        ("600519", "2026-07-07", 11.0, 12.0, 10.0, 11.0),
                        ("300750", "2026-07-07", 20.0, 21.0, 19.0, 18.0),
                    ],
                )
            config = ExecutorConfig(dsa_db_path=db_path, stock_pool=("600519", "300750"))
            reader = SignalReader(db_path)

            benchmark = _equal_weight_return(reader, config, date(2026, 7, 6), date(2026, 7, 7))

            self.assertTrue(benchmark["available"])
            self.assertAlmostEqual(benchmark["return"], 0.0)
            self.assertIn("without fees/slippage", benchmark["source"])

    def test_hs300_benchmark_uses_external_index_bars(self) -> None:
        config = ExecutorConfig(benchmark_codes=("000300",))
        bars = [
            {"date": "2026-07-06", "open": 100.0, "close": 101.0},
            {"date": "2026-07-07", "open": 101.0, "close": 103.0},
        ]

        with patch("ops.weekly_review._fetch_eastmoney_index_bars", return_value=bars):
            benchmark = _hs300_return(config, date(2026, 7, 6), date(2026, 7, 7))

        self.assertTrue(benchmark["available"])
        self.assertEqual(benchmark["source"], "Eastmoney index kline, gross price return without fees/slippage")
        self.assertAlmostEqual(benchmark["return"], 0.03)

    def test_hs300_benchmark_falls_back_to_tencent(self) -> None:
        config = ExecutorConfig(benchmark_codes=("000300",))
        bars = [
            {"date": "2026-07-06", "open": 100.0, "close": 99.0},
        ]

        with patch("ops.weekly_review._fetch_eastmoney_index_bars", side_effect=RuntimeError("disconnect")):
            with patch("ops.weekly_review._fetch_tencent_index_bars", return_value=bars):
                benchmark = _hs300_return(config, date(2026, 7, 6), date(2026, 7, 6))

        self.assertTrue(benchmark["available"])
        self.assertEqual(benchmark["source"], "Tencent index kline, gross price return without fees/slippage")
        self.assertAlmostEqual(benchmark["return"], -0.01)


if __name__ == "__main__":
    unittest.main()
