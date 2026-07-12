import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from verify_dsa_analysis import parse_stocks, verify  # noqa: E402


def make_db(rows):
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
    conn.commit()
    conn.close()
    return path


class VerifyDsaAnalysisTests(unittest.TestCase):
    def test_rows_before_since_do_not_count(self):
        db = make_db(
            [
                ("600519", "stock", "2026-07-12 10:00:00.123"),
                ("300750", "stock", "2026-07-12 18:05:00.456"),
            ]
        )
        analyzed, missing = verify(db, ["600519", "300750"], "2026-07-12 17:58:00")
        self.assertEqual(analyzed, ["300750"])
        self.assertEqual(missing, ["600519"])

    def test_market_review_rows_are_ignored(self):
        db = make_db([("MARKET", "market_review", "2026-07-12 18:05:00")])
        analyzed, missing = verify(db, ["600519"], "2026-07-12 17:58:00")
        self.assertEqual(analyzed, [])
        self.assertEqual(missing, ["600519"])

    def test_all_covered(self):
        db = make_db(
            [
                ("AAPL", "stock", "2026-07-12 05:12:00"),
                ("nvda", "stock", "2026-07-12 05:14:00"),
            ]
        )
        analyzed, missing = verify(db, parse_stocks("aapl, NVDA"), "2026-07-12 05:10:00")
        self.assertEqual(analyzed, ["AAPL", "NVDA"])
        self.assertEqual(missing, [])

    def test_parse_stocks_normalizes(self):
        self.assertEqual(parse_stocks(" aapl ,,NVDA "), ["AAPL", "NVDA"])

    def test_null_report_type_counts_as_analysis(self):
        db = make_db([("600519", None, "2026-07-12 18:05:00")])
        analyzed, missing = verify(db, ["600519"], "2026-07-12 17:58:00")
        self.assertEqual(analyzed, ["600519"])
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
