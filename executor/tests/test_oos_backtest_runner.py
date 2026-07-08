import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from executor.oos_backtest_runner import (
    RuleSignalClient,
    ensure_oos_backtest_schema,
    generate_oos_signals,
    run_oos_backtest,
)
from executor.oos_builder import initialize_oos_schema, upsert_news, upsert_stock_bar


def _seed_oos_db(path: Path, start: date, days: int = 45) -> None:
    initialize_oos_schema(path)
    ensure_oos_backtest_schema(path)
    with sqlite3.connect(path) as conn:
        price = 10.0
        for offset in range(days):
            day = start + timedelta(days=offset)
            price += 0.15
            upsert_stock_bar(
                conn,
                code="600519",
                day=day.isoformat(),
                open_price=price,
                high=price + 0.3,
                low=price - 0.2,
                close=price + 0.1,
                volume=1000 + offset,
                amount=(1000 + offset) * price,
                pct_chg=1.0,
                data_source="test",
            )
        upsert_news(
            conn,
            code="600519",
            name="600519",
            dimension="company_news",
            query="600519",
            provider="test",
            title="dated OOS news",
            snippet="news before decision date",
            url="test://news/1",
            source="test",
            published_date=(start + timedelta(days=20)).isoformat(),
            fetched_at=(start + timedelta(days=20)).isoformat(),
            query_source="test",
        )


class OosBacktestRunnerTests(unittest.TestCase):
    def test_generate_oos_signals_persists_runner_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "oos.db"
            _seed_oos_db(db_path, date(2024, 1, 1))

            summary = generate_oos_signals(
                db_path=db_path,
                start=date(2024, 1, 25),
                end=date(2024, 1, 26),
                stock_pool=("600519",),
                client=RuleSignalClient(),
                max_calls=1,
                lookback_bars=20,
            )

            self.assertEqual(summary.generated, 1)
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "select stock_code, source_agent, date(created_at) as day from decision_signals"
                ).fetchone()
            self.assertEqual(row["stock_code"], "600519")
            self.assertEqual(row["source_agent"], "oos_backtest_runner")
            self.assertEqual(row["day"], "2024-01-25")

    def test_run_oos_backtest_with_mock_signals_produces_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "oos.db"
            ledger_path = Path(tmpdir) / "ledger.db"
            _seed_oos_db(db_path, date(2024, 1, 1))

            result = run_oos_backtest(
                db_path=db_path,
                ledger_db_path=ledger_path,
                start=date(2024, 1, 25),
                end=date(2024, 2, 5),
                stock_pool=("600519",),
                signal_client=RuleSignalClient(),
                generate_signals=True,
                lookback_bars=20,
            )

            self.assertGreater(result.signal_generation.generated, 0)
            self.assertGreater(result.metrics.snapshot_count, 0)
            self.assertIsNotNone(result.metrics.final_value)


if __name__ == "__main__":
    unittest.main()
