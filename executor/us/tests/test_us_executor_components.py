import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from executor.us.config_us import UsExecutorConfig
from executor.us.engine_us import UsPaperEngine
from executor.us.ledger_us import TradeFill, UsPaperLedger
from executor.us.models_us import UsFeeModel
from executor.us.rules_us import T0Position, cap_order_shares, round_lot_shares
from executor.us.time_guard_us import bar_available_at


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


def _insert_signal(conn: sqlite3.Connection, row_id: int, code: str, action: str, source_report_id: int) -> None:
    conn.execute(
        """
        insert into decision_signals(
            id, stock_code, stock_name, action, confidence, entry_high, entry_low,
            stop_loss, target_price, status, created_at, expires_at, source_report_id,
            metadata_json, market, source_type, source_agent, plan_quality
        )
        values (?, ?, ?, ?, 0.8, 32.0, 28.0, 9.0, null, 'active',
                '2026-07-07 12:00:00', '2026-07-15 16:00:00', ?,
                ?, 'us', 'analysis', 'test', 'ok')
        """,
        (row_id, code, code, action, source_report_id, json.dumps({})),
    )


def _insert_bar(conn: sqlite3.Connection, code: str, day: str, open_price: float, low: float, close: float) -> None:
    conn.execute(
        """
        insert into stock_daily(code, date, open, high, low, close, volume, amount, pct_chg)
        values (?, ?, ?, ?, ?, ?, 1000, 10000, 0)
        """,
        (code, day, open_price, max(open_price, close), low, close),
    )


class UsRulesModelsLedgerTests(unittest.TestCase):
    def test_lot_one_and_t0_position_are_default_us_rules(self) -> None:
        self.assertEqual(round_lot_shares(123.45, 10.0), 12)
        self.assertEqual(
            cap_order_shares(
                target_cash=123.45,
                price=10.0,
                current_symbol_market_value=0.0,
                portfolio_value=1_000_000.0,
                cap_rate=0.2,
            ),
            12,
        )

        position = T0Position().buy(3)

        self.assertEqual(position.closable, 3)
        self.assertEqual(position.sell(2).quantity, 1)

    def test_us_fee_model_charges_sec_fee_only_on_sells(self) -> None:
        fees = UsFeeModel(
            commission_per_share=0.0,
            commission_rate=0.0,
            min_commission=0.0,
            sec_fee_rate=27.80 / 1_000_000,
        )

        self.assertEqual(fees.total_costs(10_000.0, "buy"), (0.0, 0.0))
        self.assertEqual(fees.total_costs(10_000.0, "sell"), (0.0, 0.28))

    def test_us_fee_model_uses_per_share_commission_with_minimum(self) -> None:
        fees = UsFeeModel(commission_per_share=0.005, commission_rate=0.0, min_commission=1.0, sec_fee_rate=0.0)

        self.assertEqual(fees.total_costs(10_000.0, "buy", shares=100), (1.0, 0.0))
        self.assertEqual(fees.total_costs(10_000.0, "buy", shares=500), (2.5, 0.0))

    def test_us_bar_available_time_uses_eastern_timezone_with_dst(self) -> None:
        available = bar_available_at(date(2026, 7, 8))

        self.assertEqual(available.hour, 16)
        self.assertEqual(available.tzname(), "EDT")

    def test_ledger_buy_is_immediately_sellable_t0(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "paper_us.db"
            config = UsExecutorConfig(ledger_db_path=ledger_path, disciplined_db_path=ledger_path)
            ledger = UsPaperLedger(ledger_path, config=config)
            ledger.initialize()
            buy = TradeFill(
                signal_id=1,
                stock_code="AAPL",
                side="buy",
                trade_date=date(2026, 7, 8),
                shares=3,
                fill_price=10.0,
                exec_price=10.0,
                gross_amount=30.0,
                fees=0.0,
                taxes=0.0,
                cash_delta=-30.0,
                reason="seed",
                created_at=datetime(2026, 7, 8, 9, 30),
            )
            sell = TradeFill(
                signal_id=1,
                stock_code="AAPL",
                side="sell",
                trade_date=date(2026, 7, 8),
                shares=2,
                fill_price=11.0,
                exec_price=11.0,
                gross_amount=22.0,
                fees=0.0,
                taxes=0.0,
                cash_delta=22.0,
                reason="same_day_exit",
                realized_pnl=2.0,
                created_at=datetime(2026, 7, 8, 10, 30),
            )

            self.assertTrue(ledger.apply_trade(buy))
            after_buy = ledger.position("AAPL")
            self.assertEqual(after_buy["quantity"], 3)
            self.assertEqual(after_buy["old_quantity"], 3)
            self.assertTrue(ledger.apply_trade(sell))
            after_sell = ledger.position("AAPL")
            self.assertEqual(after_sell["quantity"], 1)
            self.assertEqual(after_sell["old_quantity"], 1)


class UsPaperEngineTests(unittest.TestCase):
    def test_big_gap_open_still_fills_and_same_day_stop_sells_t0(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            ledger_path = Path(tmpdir) / "paper_us.db"
            _init_dsa_db(dsa_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_analysis(conn, 1, "AAPL", "buy", "2026-07-07 12:00:00")
                _insert_analysis(conn, 2, "AAPL", "buy", "2026-07-08 12:00:00")
                _insert_signal(conn, 1, "AAPL", "buy", 1)
                _insert_bar(conn, "AAPL", "2026-07-07", 10.0, 9.5, 10.0)
                _insert_bar(conn, "AAPL", "2026-07-08", 30.0, 8.5, 20.0)

            config = UsExecutorConfig(
                dsa_db_path=dsa_path,
                ledger_db_path=ledger_path,
                disciplined_db_path=ledger_path,
                stock_pool=("AAPL",),
                commission_per_share=0.0,
                commission_rate=0.0,
                min_commission=0.0,
                sec_fee_rate=0.0,
                slippage_rate=0.0,
            )
            engine = UsPaperEngine(config)

            stats = engine.run_day(date(2026, 7, 8))

            self.assertEqual(stats["filled"], 1)
            self.assertEqual(stats["sells"], 1)
            self.assertEqual(stats["pending_exits"], 0)
            position = engine.ledger.position("AAPL")
            self.assertEqual(position["quantity"], 0)
            with engine.ledger._connect() as conn:
                trades = conn.execute("select side, fill_price, reason from trades order by id").fetchall()
            self.assertEqual([(row["side"], row["fill_price"], row["reason"]) for row in trades], [
                ("buy", 30.0, "next_day_open"),
                ("sell", 9.0, "stop_loss"),
            ])


if __name__ == "__main__":
    unittest.main()
