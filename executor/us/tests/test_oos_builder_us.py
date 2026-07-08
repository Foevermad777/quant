import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from executor.us.oos_builder_us import (
    UsOosBuildSummary,
    UsOosCodeSummary,
    import_alphavantage_daily,
    import_stooq_daily,
    import_tavily_news,
    import_yahoo_chart_daily,
    render_us_oos_import_report,
)
from executor.oos_builder import initialize_oos_schema


class UsOosBuilderTests(unittest.TestCase):
    def test_import_alphavantage_daily_upserts_stock_bars(self) -> None:
        payload = {
            "Time Series (Daily)": {
                "2024-01-03": {
                    "1. open": "100.0",
                    "2. high": "102.0",
                    "3. low": "99.0",
                    "4. close": "101.0",
                    "5. adjusted close": "101.5",
                    "6. volume": "1000",
                },
                "2023-12-29": {
                    "1. open": "90.0",
                    "2. high": "91.0",
                    "3. low": "89.0",
                    "4. close": "90.0",
                    "5. adjusted close": "90.0",
                    "6. volume": "1000",
                },
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "us_oos.db"
            initialize_oos_schema(db_path)
            with patch("executor.us.oos_builder_us._http_json", return_value=payload):
                count = import_alphavantage_daily(
                    db_path,
                    "AAPL",
                    date(2024, 1, 1),
                    date(2024, 1, 31),
                    api_key="test",
                )

            self.assertEqual(count, 1)
            with sqlite3.connect(db_path) as conn:
                row = conn.execute("select code, date, close, data_source from stock_daily").fetchone()
            self.assertEqual(row, ("AAPL", "2024-01-03", 101.5, "alphavantage_us_oos_daily"))

    def test_import_yahoo_chart_daily_upserts_adjusted_stock_bars(self) -> None:
        payload = {
            "chart": {
                "result": [
                    {
                        "timestamp": [1704240000],
                        "indicators": {
                            "quote": [
                                {
                                    "open": [200.0],
                                    "high": [220.0],
                                    "low": [180.0],
                                    "close": [210.0],
                                    "volume": [1000],
                                }
                            ],
                            "adjclose": [{"adjclose": [105.0]}],
                        },
                    }
                ],
                "error": None,
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "us_oos.db"
            initialize_oos_schema(db_path)
            with patch("executor.us.oos_builder_us._http_json", return_value=payload):
                count = import_yahoo_chart_daily(
                    db_path,
                    "AAPL",
                    date(2024, 1, 1),
                    date(2024, 1, 31),
                )

            self.assertEqual(count, 1)
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "select code, date, open, high, low, close, data_source from stock_daily"
                ).fetchone()
            self.assertEqual(row, ("AAPL", "2024-01-03", 100.0, 110.0, 90.0, 105.0, "yahoo_chart_us_oos_daily"))

    def test_import_stooq_daily_upserts_stock_bars(self) -> None:
        csv_payload = "\n".join(
            [
                "Date,Open,High,Low,Close,Volume",
                "2024-01-02,100.0,102.0,99.0,101.0,1234",
                "2025-12-31,110.0,112.0,109.0,111.0,2345",
            ]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "us_oos.db"
            initialize_oos_schema(db_path)
            with patch("executor.us.oos_builder_us._http_text", return_value=csv_payload):
                count = import_stooq_daily(db_path, "AAPL", date(2024, 1, 1), date(2025, 12, 31))

            self.assertEqual(count, 2)
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "select code, min(date), max(date), count(*), max(data_source) from stock_daily"
                ).fetchone()
            self.assertEqual(row, ("AAPL", "2024-01-02", "2025-12-31", 2, "stooq_us_oos_daily"))

    def test_import_tavily_news_upserts_only_dated_oos_items(self) -> None:
        payload = {
            "results": [
                {
                    "title": "Apple reports results",
                    "content": "dated item",
                    "url": "https://example.com/apple-2024",
                    "published_date": "Thu, 02 May 2024 00:00:00 GMT",
                },
                {
                    "title": "Apple outside window",
                    "content": "new item",
                    "url": "https://example.com/apple-2026",
                    "published_date": "2026-01-02",
                },
                {
                    "title": "Apple missing date",
                    "content": "undated item",
                    "url": "https://example.com/apple-undated",
                },
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "us_oos.db"
            initialize_oos_schema(db_path)
            with patch("executor.us.oos_builder_us._tavily_search", return_value=payload):
                count = import_tavily_news(
                    db_path,
                    "AAPL",
                    "Apple Inc.",
                    date(2024, 1, 1),
                    date(2025, 12, 31),
                    api_keys=("test",),
                )

            self.assertEqual(count, 1)
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "select code, provider, title, date(published_date), query_source from news_intel"
                ).fetchone()
            self.assertEqual(row, ("AAPL", "Tavily", "Apple reports results", "2024-05-02", "us_oos_builder"))

    def test_summary_requires_historical_news_for_news_ready(self) -> None:
        common = {
            "db_path": Path("/tmp/us_oos.db"),
            "source_db_path": Path("/tmp/source.db"),
            "start": date(2024, 1, 1),
            "end": date(2025, 12, 31),
            "generated_at": "2026-07-08 00:00:00",
            "price_provider": "yahoochart",
            "news_provider": "tavily",
        }
        without_news = UsOosBuildSummary(
            **common,
            codes=(
                UsOosCodeSummary(
                    code="AAPL",
                    name="Apple Inc.",
                    min_bar_date="2024-01-02",
                    max_bar_date="2025-12-31",
                    news_count=0,
                ),
            ),
        )
        with_news = UsOosBuildSummary(
            **common,
            codes=(
                UsOosCodeSummary(
                    code="AAPL",
                    name="Apple Inc.",
                    min_bar_date="2024-01-02",
                    max_bar_date="2025-12-31",
                    news_count=3,
                ),
            ),
        )

        self.assertTrue(without_news.bars_ready)
        self.assertFalse(without_news.news_ready)
        self.assertTrue(with_news.news_ready)
        self.assertIn("News metadata ready", render_us_oos_import_report(without_news))


if __name__ == "__main__":
    unittest.main()
