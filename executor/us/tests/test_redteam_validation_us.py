import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from executor.us.config_us import UsExecutorConfig
from executor.us.redteam_validation import check_oos_gate, run_stress_scenarios


def _init_dsa_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            create table analysis_history (
                id integer primary key,
                code text not null,
                name text,
                operation_advice text,
                created_at text
            );
            create table decision_signals (
                id integer primary key,
                stock_code text not null,
                stock_name text,
                action text not null,
                confidence real,
                entry_high real,
                entry_low real,
                stop_loss real,
                target_price real,
                status text not null,
                created_at text,
                expires_at text,
                source_report_id integer,
                metadata_json text,
                market text,
                source_type text,
                source_agent text,
                plan_quality text
            );
            create table stock_daily (
                code text not null,
                date text not null,
                open real,
                high real,
                low real,
                close real,
                volume real,
                amount real,
                pct_chg real
            );
            create table news_intel (
                id integer primary key,
                code text not null,
                title text,
                snippet text,
                url text,
                source text,
                provider text,
                published_date text
            );
            """
        )


def _insert_analysis(conn: sqlite3.Connection, row_id: int, code: str, advice: str, created_at: str) -> None:
    conn.execute(
        "insert into analysis_history(id, code, name, operation_advice, created_at) values (?, ?, ?, ?, ?)",
        (row_id, code, code, advice, created_at),
    )


def _insert_signal(conn: sqlite3.Connection, row_id: int, code: str, source_report_id: int) -> None:
    conn.execute(
        """
        insert into decision_signals(
            id, stock_code, stock_name, action, confidence, entry_high, entry_low,
            stop_loss, target_price, status, created_at, expires_at, source_report_id,
            metadata_json, market, source_type, source_agent, plan_quality
        )
        values (?, ?, ?, 'buy', 0.8, 12.0, 9.0, null, null, 'active',
                '2026-07-07 17:00:00', '2026-07-15 16:00:00', ?,
                '{}', 'us', 'analysis', 'test', 'ok')
        """,
        (row_id, code, code, source_report_id),
    )


def _insert_bar(conn: sqlite3.Connection, code: str, day: str, open_price: float, close_price: float) -> None:
    conn.execute(
        """
        insert into stock_daily(code, date, open, high, low, close, volume, amount, pct_chg)
        values (?, ?, ?, ?, ?, ?, 1000, 10000, 0)
        """,
        (code, day, open_price, max(open_price, close_price), min(open_price, close_price), close_price),
    )


class UsRedTeamValidationTests(unittest.TestCase):
    def test_oos_gate_fails_closed_when_us_history_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            _init_dsa_db(dsa_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_bar(conn, "AAPL", "2025-03-14", 10.0, 10.5)
            config = UsExecutorConfig(
                dsa_db_path=dsa_path,
                ledger_db_path=Path(tmpdir) / "paper_us.db",
                disciplined_db_path=Path(tmpdir) / "paper_us.db",
                stock_pool=("AAPL",),
            )

            result = check_oos_gate(config, date(2024, 1, 1), date(2025, 12, 31))

            self.assertEqual(result.status, "failed_closed")
            self.assertIn("start_after_required", result.rows[0].reason)
            self.assertIn("end_before_required", result.rows[0].reason)
            self.assertIn("missing_news_metadata", result.rows[0].reason)

    def test_stress_runner_uses_us_per_share_commission_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            _init_dsa_db(dsa_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_analysis(conn, 1, "AAPL", "buy", "2026-07-07 17:00:00")
                _insert_analysis(conn, 2, "AAPL", "buy", "2026-07-08 17:00:00")
                _insert_signal(conn, 1, "AAPL", 1)
                _insert_bar(conn, "AAPL", "2026-07-08", 10.0, 11.0)
            config = UsExecutorConfig(
                dsa_db_path=dsa_path,
                ledger_db_path=Path(tmpdir) / "paper_us.db",
                disciplined_db_path=Path(tmpdir) / "paper_us.db",
                use_disciplined_signals=False,
                initial_cash=10_000.0,
                per_signal_cash=1_000.0,
                symbol_cap_rate=1.0,
                stock_pool=("AAPL",),
                slippage_rate=0.0,
                commission_per_share=0.005,
                commission_rate=0.0,
                min_commission=1.0,
                sec_fee_rate=0.0,
            )

            results = run_stress_scenarios(config, date(2026, 7, 8), date(2026, 7, 8))
            by_name = {item.scenario: item for item in results}

            self.assertEqual(by_name["base"].trade_count, 1)
            self.assertEqual(by_name["base"].total_commissions, 1.0)
            self.assertEqual(by_name["double_all_friction"].commission_per_share, 0.01)
            self.assertEqual(by_name["double_all_friction"].min_commission, 2.0)
            self.assertEqual(by_name["double_all_friction"].total_commissions, 2.0)


if __name__ == "__main__":
    unittest.main()
