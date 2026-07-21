import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from executor.models import LimitFillModel
from executor.shadow_intent import MarketContext, build_report, evaluate_day, record_rows
from executor.signal_reader import SignalReader


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


def _init_disciplined_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            create table disciplined_signals (
                source_signal_id integer primary key,
                source_report_id integer,
                stock_code text not null,
                stock_name text,
                market text not null,
                action text not null,
                confidence real,
                entry_high real,
                entry_low real,
                stop_loss real,
                target_price real,
                status text not null,
                created_at text,
                expires_at text,
                plan_quality text,
                schema_version text not null,
                completion_version text not null,
                completed_at text not null,
                updated_at text not null,
                model text not null,
                completion_payload_json text not null,
                gate_accepted integer not null,
                gate_action text not null,
                gate_reasons_json text not null
            );
            create table trades (
                id integer primary key autoincrement,
                stock_code text not null,
                side text not null,
                trade_date text not null,
                shares integer not null
            );
            """
        )


def _insert_disciplined(
    conn: sqlite3.Connection,
    source_signal_id: int,
    code: str,
    action: str,
    payload: dict,
    *,
    created_at: str = "2026-07-14 10:00:00",
    expires_at: str = "2026-07-17 10:00:00",
    entry_high: float = 12.0,
    stop_loss: float = 9.0,
    target_price: float = 15.0,
) -> None:
    conn.execute(
        """
        insert into disciplined_signals(
            source_signal_id, source_report_id, stock_code, stock_name, market,
            action, confidence, entry_high, entry_low, stop_loss, target_price,
            status, created_at, expires_at, plan_quality, schema_version,
            completion_version, completed_at, updated_at, model,
            completion_payload_json, gate_accepted, gate_action, gate_reasons_json
        )
        values (?, ?, ?, ?, 'cn', ?, 0.8, ?, 10.0, ?, ?,
                'active', ?, ?, 'ok', 'g5-discipline-v0.1', 'g5-minimal-v0.1',
                ?, ?, 'gemini-3.5-flash', ?, 1, 'pass', '[]')
        """,
        (
            source_signal_id,
            source_signal_id,
            code,
            code,
            action,
            entry_high,
            stop_loss,
            target_price,
            created_at,
            expires_at,
            created_at,
            created_at,
            json.dumps(payload, ensure_ascii=False),
        ),
    )


def _pcs_conditional_payload() -> dict:
    # Real production shape: the LLM labels position_context_split while the
    # reason describes a price-conditioned entry for flat accounts.
    return {
        "flat_account_action": "watch",
        "holding_action": "hold",
        "resolved_action": "watch",
        "conflict_status": "position_context_split",
        "conflict_reason": "持仓者继续持有；空仓者等待回调，在支撑区间逢低分批吸纳。",
    }


def _pcs_avoid_payload() -> dict:
    return {
        "flat_account_action": "watch",
        "holding_action": "hold",
        "resolved_action": "watch",
        "conflict_status": "position_context_split",
        "conflict_reason": "持仓者持有；空仓者暂时回避，不参与。",
    }


def _bar(conn: sqlite3.Connection, code: str, day: str, open_price: float, low: float, high: float, close: float) -> None:
    conn.execute(
        "insert into stock_daily(code, date, open, high, low, close, volume, amount, pct_chg) values (?, ?, ?, ?, ?, ?, 1000, 10000, 0)",
        (code, day, open_price, high, low, close),
    )


def _context(tmpdir: str) -> MarketContext:
    dsa_path = Path(tmpdir) / "dsa.db"
    store_path = Path(tmpdir) / "paper.db"
    if not dsa_path.exists():
        _init_dsa_db(dsa_path)
        _init_disciplined_db(store_path)
    reader = SignalReader(dsa_path, store_path, market="cn")
    return MarketContext("cn", reader, LimitFillModel(), store_path)


class ShadowIntentTests(unittest.TestCase):
    def test_reclassified_pcs_is_promoted_and_fill_simulated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            context = _context(tmpdir)
            with sqlite3.connect(context.reader.disciplined_db_path) as conn:
                _insert_disciplined(conn, 1, "600519", "buy", _pcs_conditional_payload())
                _insert_disciplined(conn, 2, "600036", "buy", _pcs_avoid_payload())
            with sqlite3.connect(context.reader.db_path) as conn:
                _bar(conn, "600519", "2026-07-15", 12.5, 11.8, 12.9, 12.6)
                _bar(conn, "600036", "2026-07-15", 12.5, 11.8, 12.9, 12.6)

            rows = evaluate_day(context, date(2026, 7, 15), now_iso="2026-07-15 12:00:00", mode="live")

            by_id = {row["signal_id"]: row for row in rows}
            # Signal 1: reason carries a price-conditioned plan -> promoted, and
            # the day's low (11.8) dips under entry_high (12.0) -> limit touch.
            self.assertEqual(by_id[1]["shadow_status"], "conditional_entry")
            self.assertEqual(by_id[1]["reclassified"], 1)
            self.assertEqual(by_id[1]["in_production"], 0)
            self.assertEqual(by_id[1]["promoted"], 1)
            self.assertEqual(by_id[1]["fill_status"], "filled")
            self.assertEqual(by_id[1]["fill_reason"], "intraday_limit_touch")
            self.assertEqual(by_id[1]["fill_price"], 12.0)
            # Signal 2: flat side gets no entry condition -> stays a split, not promoted.
            self.assertNotIn(2, by_id)

    def test_held_symbol_is_not_promoted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            context = _context(tmpdir)
            with sqlite3.connect(context.reader.disciplined_db_path) as conn:
                _insert_disciplined(conn, 1, "600519", "buy", _pcs_conditional_payload())
                conn.execute(
                    "insert into trades(stock_code, side, trade_date, shares) values ('600519', 'buy', '2026-07-14', 100)"
                )
            with sqlite3.connect(context.reader.db_path) as conn:
                _bar(conn, "600519", "2026-07-15", 12.5, 11.8, 12.9, 12.6)

            rows = evaluate_day(context, date(2026, 7, 15), now_iso="2026-07-15 12:00:00", mode="live")

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["promoted"], 0)
            self.assertEqual(rows[0]["drop_reason"], "symbol_held")

    def test_latest_by_symbol_supersedes_older_shadow_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            context = _context(tmpdir)
            with sqlite3.connect(context.reader.disciplined_db_path) as conn:
                _insert_disciplined(conn, 1, "600519", "buy", _pcs_conditional_payload(), created_at="2026-07-13 10:00:00")
                _insert_disciplined(conn, 2, "600519", "buy", _pcs_conditional_payload(), created_at="2026-07-14 10:00:00")
            with sqlite3.connect(context.reader.db_path) as conn:
                _bar(conn, "600519", "2026-07-15", 12.5, 11.8, 12.9, 12.6)

            rows = evaluate_day(context, date(2026, 7, 15), now_iso="2026-07-15 12:00:00", mode="live")

            by_id = {row["signal_id"]: row for row in rows}
            self.assertEqual(by_id[2]["promoted"], 1)
            self.assertEqual(by_id[1]["promoted"], 0)
            self.assertEqual(by_id[1]["drop_reason"], "superseded_by_newer_signal")

    def test_record_rows_is_idempotent_and_report_flags_knife(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            context = _context(tmpdir)
            with sqlite3.connect(context.reader.disciplined_db_path) as conn:
                _insert_disciplined(conn, 1, "600519", "buy", _pcs_conditional_payload(), stop_loss=11.0)
            with sqlite3.connect(context.reader.db_path) as conn:
                _bar(conn, "600519", "2026-07-15", 12.5, 11.8, 12.9, 12.6)
                # Next session gaps down through the stop -> a caught knife.
                _bar(conn, "600519", "2026-07-16", 11.2, 10.5, 11.3, 10.8)

            rows = evaluate_day(context, date(2026, 7, 15), now_iso="2026-07-15 12:00:00", mode="live")
            self.assertEqual(record_rows(context.store_db_path, rows), 1)
            self.assertEqual(record_rows(context.store_db_path, rows), 1)
            with sqlite3.connect(context.store_db_path) as conn:
                count = conn.execute("select count(*) from shadow_intent_decisions").fetchone()[0]
            self.assertEqual(count, 1)

            report = build_report(context)

            self.assertTrue(report["available"])
            self.assertEqual(report["fill_count"], 1)
            self.assertEqual(report["knife_count"], 1)
            self.assertEqual(report["fills"][0]["outcome"], "stop_loss")
            self.assertTrue(report["fills"][0]["knife"])


if __name__ == "__main__":
    unittest.main()
