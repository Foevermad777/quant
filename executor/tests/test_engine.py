import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from executor.config import FILL_MODEL_LIMIT_ENTRY_HIGH, FILL_MODEL_NEXT_OPEN, ExecutorConfig
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
    expires_at: str | None = "2026-07-10 15:00:00",
) -> None:
    conn.execute(
        """
        insert into decision_signals(
            id, stock_code, stock_name, action, confidence, entry_high, entry_low,
            stop_loss, target_price, status, created_at, expires_at, source_report_id,
            metadata_json, market, source_type, source_agent, plan_quality
        )
        values (?, ?, ?, ?, 0.8, ?, 10.0, ?, null, 'active',
                '2026-07-05 12:00:00', ?, ?,
                ?, 'cn', 'analysis', 'test', 'ok')
        """,
        (row_id, code, code, action, entry_high, stop_loss, expires_at, source_report_id, json.dumps({})),
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
            self.assertEqual(events[0]["reason"], "hard_conflict")
            self.assertEqual(events[1]["reason"], "missing_stock_daily_bars_for_pool")
            self.assertIn("600519", events[1]["details_json"])

            with sqlite3.connect(dsa_path) as conn:
                _insert_bar(conn, "600519", "2026-07-06", 10.0, 10.5)

            stats = engine.run_day(date(2026, 7, 6))

            self.assertEqual(stats["data_gaps"], 0)
            with engine.ledger._connect() as conn:
                events = conn.execute("select event_type from signal_events order by id").fetchall()
            self.assertEqual([row["event_type"] for row in events], ["s1_conflict_skip"])

    def test_conditional_entry_becomes_limit_plan_and_fills_in_zone(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            ledger_path = Path(tmpdir) / "paper.db"
            _init_dsa_db(dsa_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_analysis(conn, 1, "600900", "空仓者可逢低，等待回踩后分批建仓", "2026-07-05 12:00:00")
                _insert_analysis(conn, 2, "600900", "空仓者可逢低，等待回踩后分批建仓", "2026-07-06 12:00:00")
                _insert_signal(conn, 1, "600900", "buy", 1)
                _insert_bar(conn, "600900", "2026-07-06", 10.0, 10.5)

            config = ExecutorConfig(
                dsa_db_path=dsa_path,
                ledger_db_path=ledger_path,
                stock_pool=("600900",),
            )
            engine = PaperEngine(config)

            stats = engine.run_day(date(2026, 7, 6))

            # Promoted to a conditional limit plan instead of an s1 conflict skip;
            # open 10.0 <= entry_high 12.0 so the resting order fills at the open.
            self.assertEqual(stats["s1_conflicts"], 0)
            self.assertEqual(stats["open_candidates"], 1)
            self.assertEqual(stats["filled"], 1)
            with engine.ledger._connect() as conn:
                events = conn.execute(
                    "select count(*) as count from signal_events where event_type = 's1_conflict_skip'"
                ).fetchone()
                trade = conn.execute("select fill_price, reason from trades").fetchone()
            self.assertEqual(events["count"], 0)
            self.assertEqual(trade["fill_price"], 10.0)
            self.assertEqual(trade["reason"], "open_within_limit")

    def test_conditional_entry_limit_plan_does_not_chase_above_zone(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            ledger_path = Path(tmpdir) / "paper.db"
            _init_dsa_db(dsa_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_analysis(conn, 1, "600900", "空仓者可逢低，等待回踩后分批建仓", "2026-07-05 12:00:00")
                _insert_analysis(conn, 2, "600900", "空仓者可逢低，等待回踩后分批建仓", "2026-07-06 12:00:00")
                _insert_signal(conn, 1, "600900", "buy", 1)
                _insert_bar(conn, "600900", "2026-07-06", 13.0, 13.5)

            config = ExecutorConfig(
                dsa_db_path=dsa_path,
                ledger_db_path=ledger_path,
                stock_pool=("600900",),
            )
            engine = PaperEngine(config)

            stats = engine.run_day(date(2026, 7, 6))

            # Whole bar above entry_high 12.0: the plan rests unfilled, no chase.
            self.assertEqual(stats["s1_conflicts"], 0)
            self.assertEqual(stats["open_candidates"], 1)
            self.assertEqual(stats["filled"], 0)
            self.assertEqual(stats["unfilled"], 1)
            with engine.ledger._connect() as conn:
                attempt = conn.execute("select status, reason from order_attempts").fetchone()
                trades = conn.execute("select count(*) as count from trades").fetchone()
            self.assertEqual(attempt["status"], "unfilled")
            self.assertEqual(attempt["reason"], "limit_not_touched")
            self.assertEqual(trades["count"], 0)

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

    def test_open_candidates_survive_a_day_without_analysis_rows(self) -> None:
        # A resting plan must still execute on a day DSA produced no analysis
        # rows (e.g. a failed batch): candidates come from prior-day signals,
        # not from same-day analysis_history. The old gate skipped all new
        # openings whenever analysis_count == 0. Mirrors the US engine.
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            ledger_path = Path(tmpdir) / "paper.db"
            _init_dsa_db(dsa_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_analysis(conn, 1, "600900", "空仓者可逢低，等待回踩后分批建仓", "2026-07-05 12:00:00")
                _insert_signal(conn, 1, "600900", "buy", 1)
                _insert_bar(conn, "600900", "2026-07-06", 10.0, 10.5)

            config = ExecutorConfig(
                dsa_db_path=dsa_path,
                ledger_db_path=ledger_path,
                stock_pool=("600900",),
            )
            engine = PaperEngine(config)

            stats = engine.run_day(date(2026, 7, 6))

            self.assertEqual(stats["analysis_count"], 0)
            self.assertEqual(stats["open_candidates"], 1)
            self.assertEqual(stats["filled"], 1)

    def test_expired_sell_signal_never_reaches_exit_candidates(self) -> None:
        # Layer 1 of the stale-signal guard: the reader must not surface an
        # expired sell signal, so a held position is never sold off a stale plan.
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            ledger_path = Path(tmpdir) / "paper.db"
            _init_dsa_db(dsa_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_analysis(conn, 1, "600519", "卖出", "2026-07-05 12:00:00")
                _insert_signal(conn, 1, "600519", "sell", 1, expires_at="2026-07-05 15:00:00")
                _insert_bar(conn, "600519", "2026-07-05", 10.0, 10.0)
                _insert_bar(conn, "600519", "2026-07-06", 11.0, 11.5)

            config = ExecutorConfig(
                dsa_db_path=dsa_path,
                ledger_db_path=ledger_path,
                stock_pool=("600519",),
            )
            ledger = PaperLedger(ledger_path, config=config)
            _seed_position(ledger)
            engine = PaperEngine(config)

            stats = engine.run_day(date(2026, 7, 6))

            self.assertEqual(stats["exit_candidates"], 0)
            self.assertEqual(stats["sells"], 0)
            self.assertEqual(engine.ledger.position("600519")["quantity"], 1000)

    def test_engine_backstop_blocks_stale_exit_signal(self) -> None:
        # Layer 2 (defense in depth): even if a stale exit signal slips past the
        # reader (future regression), the engine must block it, never sell.
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            ledger_path = Path(tmpdir) / "paper.db"
            _init_dsa_db(dsa_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_analysis(conn, 1, "600519", "卖出", "2026-07-05 12:00:00")
                _insert_signal(conn, 1, "600519", "sell", 1, expires_at="2026-07-05 15:00:00")
                _insert_bar(conn, "600519", "2026-07-05", 10.0, 10.0)
                _insert_bar(conn, "600519", "2026-07-06", 11.0, 11.5)

            config = ExecutorConfig(
                dsa_db_path=dsa_path,
                ledger_db_path=ledger_path,
                stock_pool=("600519",),
            )
            ledger = PaperLedger(ledger_path, config=config)
            _seed_position(ledger)
            engine = PaperEngine(config)
            stale = engine.reader.get_signal(1)
            engine.reader.exit_candidates = lambda execution_date, held_symbols=None: [stale]

            stats = engine.run_day(date(2026, 7, 6))

            self.assertEqual(stats["sells"], 0)
            self.assertEqual(stats["blocked"], 1)
            self.assertEqual(engine.ledger.position("600519")["quantity"], 1000)
            with engine.ledger._connect() as conn:
                attempt = conn.execute(
                    "select status, reason from order_attempts where stock_code = '600519'"
                ).fetchone()
            self.assertEqual(attempt["status"], "blocked")
            self.assertEqual(attempt["reason"], "exit_signal_expired")

    def test_run_day_limit_up_block_pending_exit_and_settlement_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            ledger_path = Path(tmpdir) / "paper.db"
            _init_dsa_db(dsa_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_analysis(conn, 1, "600519", "买入", "2026-07-05 12:00:00")
                _insert_analysis(conn, 2, "600036", "买入", "2026-07-05 12:00:00")
                _insert_analysis(conn, 3, "600519", "观望", "2026-07-06 12:00:00")
                # Signal 1 expires after its fill day: under the corrected
                # opening gate (candidates independent of same-day analysis
                # rows) a still-live buy signal would legitimately re-buy on
                # 07-07 right after the stop-out; this test is about the
                # limit-up block -> pending exit -> settlement chain only.
                _insert_signal(conn, 1, "600519", "buy", 1, entry_high=10.5, stop_loss=9.0, expires_at="2026-07-06 15:00:00")
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

    def test_default_fill_model_stays_next_open_hell_mode(self) -> None:
        # Red-team decision: consistent buys fill at next open with double
        # slippage; the entry_high limit model is A/B only. Conditional-entry
        # promotions use the limit model per signal, not via this default.
        self.assertEqual(ExecutorConfig().fill_model, FILL_MODEL_NEXT_OPEN)

    def test_default_open_fill_uses_next_open_and_double_buy_slippage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            ledger_path = Path(tmpdir) / "paper.db"
            _init_dsa_db(dsa_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_analysis(conn, 1, "600519", "买入", "2026-07-05 12:00:00")
                _insert_analysis(conn, 2, "600519", "买入", "2026-07-06 12:00:00")
                _insert_signal(conn, 1, "600519", "buy", 1, entry_high=10.0)
                _insert_bar(conn, "600519", "2026-07-06", 10.5, 10.8)

            config = ExecutorConfig(
                dsa_db_path=dsa_path,
                ledger_db_path=ledger_path,
                stock_pool=("600519",),
                commission_rate=0.0,
                min_commission=0.0,
                stamp_tax_rate=0.0,
                slippage_rate=0.001,
                per_signal_cash=10_000.0,
            )
            engine = PaperEngine(config)

            stats = engine.run_day(date(2026, 7, 6))

            self.assertEqual(stats["filled"], 1)
            with engine.ledger._connect() as conn:
                trade = conn.execute("select fill_price, exec_price, reason from trades").fetchone()
            self.assertEqual(trade["fill_price"], 10.5)
            self.assertEqual(trade["exec_price"], 10.521)
            self.assertEqual(trade["reason"], "next_day_open")

    def test_limit_entry_high_fill_model_remains_available_for_ab_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            ledger_path = Path(tmpdir) / "paper.db"
            _init_dsa_db(dsa_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_analysis(conn, 1, "600519", "买入", "2026-07-05 12:00:00")
                _insert_analysis(conn, 2, "600519", "买入", "2026-07-06 12:00:00")
                _insert_signal(conn, 1, "600519", "buy", 1, entry_high=10.0)
                _insert_bar(conn, "600519", "2026-07-06", 10.5, 10.2)

            config = ExecutorConfig(
                dsa_db_path=dsa_path,
                ledger_db_path=ledger_path,
                stock_pool=("600519",),
                fill_model=FILL_MODEL_LIMIT_ENTRY_HIGH,
                commission_rate=0.0,
                min_commission=0.0,
                stamp_tax_rate=0.0,
                slippage_rate=0.0,
            )
            engine = PaperEngine(config)

            stats = engine.run_day(date(2026, 7, 6))

            self.assertEqual(stats["filled"], 0)
            self.assertEqual(stats["unfilled"], 1)
            with engine.ledger._connect() as conn:
                attempt = conn.execute(
                    "select status, reason, price from order_attempts where stock_code = '600519'"
                ).fetchone()
                trades = conn.execute("select count(*) as count from trades").fetchone()
            self.assertEqual(attempt["status"], "unfilled")
            self.assertEqual(attempt["reason"], "limit_not_touched")
            self.assertIsNone(attempt["price"])
            self.assertEqual(trades["count"], 0)


if __name__ == "__main__":
    unittest.main()
