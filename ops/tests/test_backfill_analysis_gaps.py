import contextlib
import io
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backfill_dsa_gaps import (  # noqa: E402
    _analyzed_codes_on,
    _analyzed_codes_since,
    _latest_trading_date,
    _recent_observed_dates,
    scan_analysis_gaps,
)

POOL = ("600519", "300750")


def make_db(rows, daily_rows=()):
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    path = Path(handle.name)
    handle.close()
    conn = sqlite3.connect(path)
    conn.execute(
        "create table analysis_history ("
        "id integer primary key, code text, report_type text, created_at text)"
    )
    conn.executemany(
        "insert into analysis_history (code, report_type, created_at) values (?, ?, ?)",
        rows,
    )
    conn.execute("create table stock_daily (code text, date text)")
    conn.executemany("insert into stock_daily (code, date) values (?, ?)", daily_rows)
    conn.commit()
    conn.close()
    return path


class AnalysisGapScanTests(unittest.TestCase):
    def test_latest_date_gap_detected(self):
        db = make_db(
            [
                ("600519", "stock", "2026-07-09 18:05:00"),
                ("300750", "stock", "2026-07-09 18:08:00"),
                ("600519", "stock", "2026-07-10 18:05:00"),
            ]
        )
        with contextlib.redirect_stdout(io.StringIO()) as captured:
            latest_missing = scan_analysis_gaps(
                db, POOL, [date(2026, 7, 9), date(2026, 7, 10)], date(2026, 7, 10)
            )
        self.assertEqual(latest_missing, ["300750"])
        output = captured.getvalue()
        self.assertIn("analysis_gap date=2026-07-09 missing=none", output)
        self.assertIn("analysis_gap date=2026-07-10 missing=300750 latest=1", output)

    def test_late_rerun_next_morning_still_counts_for_latest(self):
        db = make_db(
            [
                ("600519", "stock", "2026-07-10 18:05:00"),
                ("300750", "stock", "2026-07-11 09:30:00"),
            ]
        )
        latest_missing = scan_analysis_gaps(db, POOL, [date(2026, 7, 10)], date(2026, 7, 10))
        self.assertEqual(latest_missing, [])

    def test_market_review_rows_do_not_mask_gaps(self):
        db = make_db([("MARKET", "market_review", "2026-07-10 18:05:00")])
        analyzed = _analyzed_codes_since(db, POOL, "2026-07-10 00:00:00")
        self.assertEqual(analyzed, set())
        analyzed_on = _analyzed_codes_on(db, POOL, date(2026, 7, 10))
        self.assertEqual(analyzed_on, set())

    def test_null_report_type_counts_as_analysis(self):
        db = make_db([("600519", None, "2026-07-10 18:05:00")])
        self.assertEqual(_analyzed_codes_since(db, POOL, "2026-07-10 00:00:00"), {"600519"})
        self.assertEqual(_analyzed_codes_on(db, POOL, date(2026, 7, 10)), {"600519"})

    def test_date_discovery_is_pool_scoped(self):
        # A US trading day (07-11 Sat CN) present only via AAPL must not become
        # the CN pool's latest date.
        db = make_db(
            [],
            daily_rows=[
                ("600519", "2026-07-10"),
                ("300750", "2026-07-10"),
                ("600519", "2026-07-09"),
                ("AAPL", "2026-07-11"),
            ],
        )
        self.assertEqual(_latest_trading_date(db, POOL), date(2026, 7, 10))
        dates = _recent_observed_dates(db, POOL, date(2026, 7, 10), 10)
        self.assertEqual(dates, [date(2026, 7, 9), date(2026, 7, 10)])


if __name__ == "__main__":
    unittest.main()
