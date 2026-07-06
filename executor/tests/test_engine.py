import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from executor.config import ExecutorConfig
from executor.engine import PaperEngine
from executor.ledger import PaperLedger, TradeFill


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
            """
        )


def _insert_analysis(conn: sqlite3.Connection, row_id: int, code: str, advice: str, created_at: str) -> None:
    conn.execute(
        "insert into analysis_history(id, code, name, operation_advice, created_at) values (?, ?, ?, ?, ?)",
        (row_id, code, code, advice, created_at),
    )


def _insert_signal(
    conn: sqlite3.Connection,
    row_id: int,
    code: str,
    action: str,
    source_report_id: int,
    *,
    entry_high: float = 12.0,
    stop_loss: float | None = None,
) -> None:
    conn.execute(
        """
        insert into decision_signals(
            id, stock_code, stock_name, action, confidence, entry_high, entry_low,
            stop_loss, target_price, status, created_at, expires_at, source_report_id,
            metadata_json, market, source_type, source_agent, plan_quality
        )
        values (?, ?, ?, ?, 0.8, ?, 10.0, ?, null, 'active',
                '2026-07-05 12:00:00', '2026-07-10 15:00:00', ?,
                ?, 'cn', 'analysis', 'test', 'ok')
        """,
        (row_id, code, code, action, entry_high, stop_loss, source_report_id, json.dumps({})),
    )


def _insert_bar(conn: sqlite3.Connection, code: str, day: str, open_price: float, close_price: float) -> None:
    conn.execute(
        """
        insert into stock_daily(code, date, open, high, low, close, volume, amount, pct_chg)
        values (?, ?, ?, ?, ?, ?, 1000, 10000, 0)
        """,
        (code, day, open_price, max(open_price, close_price), min(open_price, close_price), close_price),
    )


def _seed_position(ledger: PaperLedger, code: str = "600519", shares: int = 1000) -> None:
    ledger.initialize()
    ledger.apply_trade(
        TradeFill(
            signal_id=999,
            stock_code=code,
            side="buy",
            trade_date=date(2026, 7, 5),
            shares=shares,
            fill_price=10.0,
            exec_price=10.0,
            gross_amount=10.0 * shares,
            fees=0.0,
            taxes=0.0,
            cash_delta=-(10.0 * shares),
            reason="seed_position",
            created_at=datetime(2026, 7, 5, 12, 0, 0),
        )
    )
    ledger.settle_positions()


class EngineTests(unittest.TestCase):
    def test_s1_conflict_and_partial_data_gap_are_both_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            ledger_path = Path(tmpdir) / "paper.db"
            _init_dsa_db(dsa_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_analysis(conn, 1, "600519", "持有", "2026-07-05 12:00:00")
                _insert_analysis(conn, 2, "300750", "观望", "2026-07-06 12:00:00")
                _insert_signal(conn, 1, "600519", "buy", 1)
                _insert_bar(conn, "300750", "2026-07-06", 100.0, 101.0)

            config = ExecutorConfig(
                dsa_db_path=dsa_path,
                ledger_db_path=ledger_path,
                stock_pool=("600519", "300750"),
            )
            engine = PaperEngine(config)

            stats = engine.run_day(date(2026, 7, 6))

            self.assertEqual(stats["s1_conflicts"], 1)
            self.assertEqual(stats["data_gaps"], 1)
            with engine.ledger._connect() as conn:
                events = conn.execute("select event_type, reason, details_json from signal_events order by id").fetchall()
            self.assertEqual([row["event_type"] for row in events], ["s1_conflict_skip", "data_gap"])
            self.assertEqual(events[1]["reason"], "missing_stock_daily_bars_for_pool")
            self.assertIn("600519", events[1]["details_json"])

    def _run_exit_signal(self, action: str, advice: str) -> tuple[dict, sqlite3.Row]:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        dsa_path = Path(tmpdir.name) / "dsa.db"
        ledger_path = Path(tmpdir.name) / "paper.db"
        _init_dsa_db(dsa_path)
        with sqlite3.connect(dsa_path) as conn:
            _insert_analysis(conn, 1, "600519", advice, "2026-07-05 12:00:00")
            _insert_signal(conn, 1, "600519", action, 1)
            _insert_bar(conn, "600519", "2026-07-05", 10.0, 10.0)
            _insert_bar(conn, "600519", "2026-07-06", 11.0, 11.5)

        config = ExecutorConfig(
            dsa_db_path=dsa_path,
            ledger_db_path=ledger_path,
            stock_pool=("600519",),
            commission_rate=0.0,
            min_commission=0.0,
            stamp_tax_rate=0.0,
            slippage_rate=0.0,
        )
        ledger = PaperLedger(ledger_path, config=config)
        _seed_position(ledger)
        engine = PaperEngine(config)

        stats = engine.run_day(date(2026, 7, 6))
        position = engine.ledger.position("600519")
        self.assertIsNotNone(position)
        return stats, position

    def test_sell_signal_closes_existing_position_at_next_open(self) -> None:
        stats, position = self._run_exit_signal("sell", "卖出")

        self.assertEqual(stats["exit_candidates"], 1)
        self.assertEqual(stats["sells"], 1)
        self.assertEqual(position["quantity"], 0)

    def test_avoid_signal_is_treated_as_full_exit(self) -> None:
        stats, position = self._run_exit_signal("avoid", "避免")

        self.assertEqual(stats["sells"], 1)
        self.assertEqual(position["quantity"], 0)

    def test_reduce_signal_sells_half_position_rounded_to_lot(self) -> None:
        stats, position = self._run_exit_signal("reduce", "减仓")

        self.assertEqual(stats["sells"], 1)
        self.assertEqual(position["quantity"], 500)

    def test_run_day_limit_up_block_pending_exit_and_settlement_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            ledger_path = Path(tmpdir) / "paper.db"
            _init_dsa_db(dsa_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_analysis(conn, 1, "600519", "买入", "2026-07-05 12:00:00")
                _insert_analysis(conn, 2, "600036", "买入", "2026-07-05 12:00:00")
                _insert_analysis(conn, 3, "600519", "观望", "2026-07-06 12:00:00")
                _insert_signal(conn, 1, "600519", "buy", 1, entry_high=10.5, stop_loss=9.0)
                _insert_signal(conn, 2, "600036", "buy", 2, entry_high=12.0)
                _insert_bar(conn, "600519", "2026-07-05", 10.0, 10.0)
                _insert_bar(conn, "600036", "2026-07-05", 10.0, 10.0)
                _insert_bar(conn, "600519", "2026-07-06", 10.0, 9.0)
                _insert_bar(conn, "600036", "2026-07-06", 11.0, 10.5)
                _insert_bar(conn, "600519", "2026-07-07", 8.8, 8.9)
                _insert_bar(conn, "600036", "2026-07-07", 10.4, 10.5)

            config = ExecutorConfig(
                dsa_db_path=dsa_path,
                ledger_db_path=ledger_path,
                stock_pool=("600519", "600036"),
                commission_rate=0.0,
                min_commission=0.0,
                stamp_tax_rate=0.0,
                slippage_rate=0.0,
            )
            engine = PaperEngine(config)

            first_day = engine.run_day(date(2026, 7, 6))
            position_after_buy = engine.ledger.position("600519")
            self.assertEqual(first_day["filled"], 1)
            self.assertEqual(first_day["unfilled"], 1)
            self.assertEqual(first_day["pending_exits"], 1)
            self.assertGreater(position_after_buy["old_quantity"], 0)
            with engine.ledger._connect() as conn:
                attempt = conn.execute("select reason from order_attempts where stock_code = '600036'").fetchone()
            self.assertEqual(attempt["reason"], "unfilled_limit_up")

            second_day = engine.run_day(date(2026, 7, 7))
            position_after_exit = engine.ledger.position("600519")
            self.assertEqual(second_day["sells"], 1)
            self.assertEqual(position_after_exit["quantity"], 0)


if __name__ == "__main__":
    unittest.main()
