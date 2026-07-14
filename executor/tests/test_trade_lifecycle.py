"""End-to-end trade-lifecycle proof for the LIVE (G5 disciplined_signals) path.

Motivation (2026-07-14 deep-dive): the paper executors have 0 trades ever,
because in the live pipeline the executor reads `disciplined_signals` and only
opens when the G5 layer emits `flat_account_action=buy` + `conflict_status=
consistent` with price in the entry zone — a combination that has never once
occurred in real data (every real buy resolved to watch/conditional_entry).
The legacy `decision_signals` buy->fill->stop path is covered by test_engine,
but the disciplined path that the live system actually uses had NO test proving
it can open a position at all.

These tests build an isolated synthetic fixture (throwaway temp DBs, no DSA
re-run, no live paper.db, no historical data) and drive the real engine through
the disciplined-signals path to prove the trigger can be pulled:

  buy (flat_account_action=buy, consistent) -> fill at next open -> position +
  cash decrement + snapshot -> next-day price hits target/stop -> exit + realized
  PnL of the correct sign.

A negative case confirms a disciplined `watch` still blocks (mirrors live).
"""
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from executor.config import ExecutorConfig
from executor.discipline_completion import DisciplinedSignalStore
from executor.engine import PaperEngine


def _init_dsa_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            create table analysis_history (
                id integer primary key, code text not null, name text,
                operation_advice text, created_at text
            );
            create table decision_signals (
                id integer primary key, stock_code text not null, stock_name text,
                action text not null, confidence real, entry_high real, entry_low real,
                stop_loss real, target_price real, status text not null, created_at text,
                expires_at text, source_report_id integer, metadata_json text, market text,
                source_type text, source_agent text, plan_quality text
            );
            create table stock_daily (
                code text not null, date text not null, open real, high real, low real,
                close real, volume real, amount real, pct_chg real
            );
            """
        )


def _insert_bar(path: Path, code: str, day: str, o: float, h: float, low: float, c: float) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "insert into stock_daily(code,date,open,high,low,close,volume,amount,pct_chg)"
            " values (?,?,?,?,?,?,1000,10000,0)",
            (code, day, o, h, low, c),
        )


def _insert_analysis(path: Path, row_id: int, code: str, created_at: str) -> None:
    # The engine only opens new positions on a date that has fresh analysis
    # activity (reader.analysis_count_on). Exits are not gated by this.
    with sqlite3.connect(path) as conn:
        conn.execute(
            "insert into analysis_history(id,code,name,operation_advice,created_at) values (?,?,?,?,?)",
            (row_id, code, code, "synthetic", created_at),
        )


# NOT NULL columns in disciplined_signals that must be supplied for a bare insert.
_REQUIRED_DEFAULTS = {
    "market": "cn",
    "status": "active",
    "schema_version": "test",
    "completion_version": "test",
    "completed_at": "2026-07-05 12:30:00",
    "updated_at": "2026-07-05 12:30:00",
    "model": "test",
    "scenarios_json": "{}",
    "invalid_conditions_json": "[]",
    "source_attribution_json": "[]",
    "single_side_flag": 0,
    "completion_payload_json": "{}",
    "raw_dsa_signal_json": "{}",
    "dsa_analysis_json": "{}",
    "dated_news_json": "[]",
    "undated_news_json": "[]",
    "guardrail_json": "{}",
    "gate_accepted": 1,
    "gate_action": "accept",
    "gate_reasons_json": "[]",
}


def _insert_disciplined_buy(
    path: Path,
    *,
    code: str = "600519",
    flat_account_action: str = "buy",
    resolved_action: str = "buy",
    conflict_status: str = "consistent",
    entry_low: float = 9.5,
    entry_high: float = 10.5,
    stop_loss: float = 9.0,
    target_price: float = 12.0,
) -> None:
    store = DisciplinedSignalStore(path)
    store.initialize()
    columns = {
        "source_signal_id": 1,
        "source_report_id": 1,
        "stock_code": code,
        "stock_name": code,
        "action": "buy",
        "confidence": 0.7,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop_loss": stop_loss,
        "target_price": target_price,
        "created_at": "2026-07-05 12:00:00",
        "expires_at": "2026-07-31 15:00:00",
        "plan_quality": "ok",
        "flat_account_action": flat_account_action,
        "holding_action": "hold",
        "resolved_action": resolved_action,
        "conflict_status": conflict_status,
        "conflict_reason": "synthetic fixture",
        **_REQUIRED_DEFAULTS,
    }
    cols = ",".join(columns)
    marks = ",".join("?" for _ in columns)
    with sqlite3.connect(path) as conn:
        conn.execute(f"insert into disciplined_signals({cols}) values ({marks})", tuple(columns.values()))


class DisciplinedTradeLifecycleTests(unittest.TestCase):
    def _make(self, tmp: str):
        dsa = Path(tmp) / "dsa.db"
        ledger = Path(tmp) / "paper.db"
        disc = Path(tmp) / "disciplined.db"
        _init_dsa_db(dsa)
        config = ExecutorConfig(
            dsa_db_path=dsa,
            ledger_db_path=ledger,
            disciplined_db_path=disc,
            stock_pool=("600519",),
            per_signal_cash=10_000.0,
            commission_rate=0.0,
            min_commission=0.0,
            stamp_tax_rate=0.0,
            slippage_rate=0.0,
        )
        return dsa, ledger, disc, config

    def test_g5_buy_opens_position_at_next_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dsa, ledger, disc, config = self._make(tmp)
            _insert_disciplined_buy(disc)
            _insert_bar(dsa, "600519", "2026-07-06", 10.0, 10.3, 9.9, 10.2)
            _insert_analysis(dsa, 10, "600519", "2026-07-06 12:00:00")
            engine = PaperEngine(config)

            self.assertTrue(engine.reader.has_disciplined_signal_store())
            stats = engine.run_day(date(2026, 7, 6))

            # The exact live seam: a disciplined buy became an open candidate and filled.
            self.assertEqual(stats["open_candidates"], 1)
            self.assertEqual(stats["s1_conflicts"], 0)
            self.assertEqual(stats["filled"], 1)
            position = engine.ledger.position("600519")
            self.assertGreater(position["quantity"], 0)
            self.assertLess(engine.ledger.get_cash(), config.initial_cash)
            with engine.ledger._connect() as conn:
                trade = conn.execute("select side, fill_price, shares from trades").fetchone()
            self.assertEqual(trade["side"], "buy")
            self.assertEqual(trade["fill_price"], 10.0)  # next-day open

    def test_g5_position_exits_with_profit_on_target_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dsa, ledger, disc, config = self._make(tmp)
            _insert_disciplined_buy(disc)
            _insert_bar(dsa, "600519", "2026-07-06", 10.0, 10.3, 9.9, 10.2)   # buy day
            _insert_analysis(dsa, 10, "600519", "2026-07-06 12:00:00")
            _insert_bar(dsa, "600519", "2026-07-07", 11.8, 12.5, 11.7, 12.3)  # target 12.0 touched
            engine = PaperEngine(config)

            engine.run_day(date(2026, 7, 6))
            self.assertGreater(engine.ledger.position("600519")["quantity"], 0)

            stats = engine.run_day(date(2026, 7, 7))

            self.assertEqual(stats["sells"], 1)
            position = engine.ledger.position("600519")
            self.assertEqual(position["quantity"], 0)
            with engine.ledger._connect() as conn:
                pnl = conn.execute(
                    "select coalesce(sum(realized_pnl),0) p from trades where side='sell'"
                ).fetchone()["p"]
            self.assertGreater(pnl, 0)  # exited above the 10.0 entry

    def test_g5_position_exits_with_loss_on_stop_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dsa, ledger, disc, config = self._make(tmp)
            _insert_disciplined_buy(disc)
            _insert_bar(dsa, "600519", "2026-07-06", 10.0, 10.3, 9.9, 10.2)  # buy day
            _insert_analysis(dsa, 10, "600519", "2026-07-06 12:00:00")
            _insert_bar(dsa, "600519", "2026-07-07", 9.5, 9.6, 8.8, 8.9)     # stop 9.0 breached
            engine = PaperEngine(config)

            engine.run_day(date(2026, 7, 6))
            stats = engine.run_day(date(2026, 7, 7))

            self.assertEqual(stats["sells"], 1)
            self.assertEqual(engine.ledger.position("600519")["quantity"], 0)
            with engine.ledger._connect() as conn:
                pnl = conn.execute(
                    "select coalesce(sum(realized_pnl),0) p from trades where side='sell'"
                ).fetchone()["p"]
            self.assertLess(pnl, 0)  # exited below the 10.0 entry

    def test_g5_watch_is_blocked_like_live(self) -> None:
        # Mirrors the live reality: a disciplined signal whose flat-account action
        # is watch (not buy) must NOT open, and is logged as an s1 conflict skip.
        with tempfile.TemporaryDirectory() as tmp:
            dsa, ledger, disc, config = self._make(tmp)
            _insert_disciplined_buy(
                disc, flat_account_action="watch", resolved_action="watch",
                conflict_status="conditional_entry",
            )
            _insert_bar(dsa, "600519", "2026-07-06", 10.0, 10.3, 9.9, 10.2)
            _insert_analysis(dsa, 10, "600519", "2026-07-06 12:00:00")
            engine = PaperEngine(config)

            stats = engine.run_day(date(2026, 7, 6))

            self.assertEqual(stats["open_candidates"], 0)
            self.assertEqual(stats["filled"], 0)
            self.assertEqual(stats["s1_conflicts"], 1)
            position = engine.ledger.position("600519")  # never traded -> no position row
            self.assertTrue(position is None or position["quantity"] == 0)


if __name__ == "__main__":
    unittest.main()
