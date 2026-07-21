import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from executor.us.signal_reader_us import UsSignalReader


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
                decision_timestamp text,
                market_phase text,
                data_asof text,
                bar_cutoff text,
                news_cutoff text,
                plan_quality text,
                schema_version text not null,
                completion_version text not null,
                completed_at text not null,
                updated_at text not null,
                model text not null,
                dsa_analysis_json text,
                completion_payload_json text not null,
                gate_accepted integer not null,
                gate_action text not null,
                gate_reasons_json text not null
            );
            """
        )


def _insert_analysis(conn: sqlite3.Connection, row_id: int, code: str, advice: str) -> None:
    conn.execute(
        "insert into analysis_history(id, code, name, operation_advice, created_at) values (?, ?, ?, ?, ?)",
        (row_id, code, code, advice, "2026-07-07 12:00:00"),
    )


def _insert_signal(
    conn: sqlite3.Connection,
    row_id: int,
    code: str,
    action: str,
    market: str,
    source_report_id: int,
    *,
    expires_at: str | None = "2026-07-15 16:00:00",
) -> None:
    conn.execute(
        """
        insert into decision_signals(
            id, stock_code, stock_name, action, confidence, entry_high, entry_low,
            stop_loss, target_price, status, created_at, expires_at, source_report_id,
            metadata_json, market, source_type, source_agent, plan_quality
        )
        values (?, ?, ?, ?, 0.8, 12.0, 10.0, 9.0, 15.0, 'active',
                '2026-07-07 12:00:00', ?, ?,
                ?, ?, 'analysis', 'test', 'ok')
        """,
        (row_id, code, code, action, expires_at, source_report_id, json.dumps({}), market),
    )


def _insert_disciplined_signal(
    conn: sqlite3.Connection,
    source_signal_id: int,
    code: str,
    action: str,
    market: str,
    source_report_id: int,
    *,
    completed_at: str = "2026-07-07 12:05:00",
    completion_payload: dict | None = None,
    expires_at: str | None = "2026-07-15 16:00:00",
) -> None:
    payload = completion_payload if completion_payload is not None else {"ok": True}
    conn.execute(
        """
        insert into disciplined_signals(
            source_signal_id, source_report_id, stock_code, stock_name, market,
            action, confidence, entry_high, entry_low, stop_loss, target_price,
            status, created_at, expires_at, plan_quality, schema_version,
            completion_version, completed_at, updated_at, model,
            completion_payload_json, gate_accepted, gate_action, gate_reasons_json
        )
        values (?, ?, ?, ?, ?, ?, 0.8, 12.0, 10.0, 9.0, 15.0,
                'active', '2026-07-07 12:00:00', ?,
                'ok', 'g5-discipline-v0.1', 'g5-minimal-v0.1',
                ?, ?,
                'gemini-3.5-flash', ?, 1, 'pass', '[]')
        """,
        (
            source_signal_id,
            source_report_id,
            code,
            code,
            market,
            action,
            expires_at,
            completed_at,
            completed_at,
            json.dumps(payload, ensure_ascii=False),
        ),
    )


def _intent_payload(
    *,
    flat_account_action: str = "watch",
    holding_action: str = "hold",
    resolved_action: str = "watch",
    conflict_status: str = "position_context_split",
) -> dict:
    return {
        "flat_account_action": flat_account_action,
        "holding_action": holding_action,
        "resolved_action": resolved_action,
        "conflict_status": conflict_status,
        "conflict_reason": "正文建议持仓者持有，空仓者等待回踩，不应直接追买。",
    }


class UsSignalReaderTests(unittest.TestCase):
    def test_decision_signal_fallback_filters_to_us_market_and_pool(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            _init_dsa_db(dsa_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_analysis(conn, 1, "AAPL", "buy")
                _insert_analysis(conn, 2, "600519", "buy")
                _insert_analysis(conn, 3, "TSLA", "buy")
                _insert_signal(conn, 1, "AAPL", "buy", "us", 1)
                _insert_signal(conn, 2, "600519", "buy", "cn", 2)
                _insert_signal(conn, 3, "TSLA", "buy", "us", 3)

            reader = UsSignalReader(dsa_path, stock_pool=("AAPL", "MSFT"))

            signals = reader.active_signals_before(date(2026, 7, 8))

            self.assertEqual([signal.stock_code for signal in signals], ["AAPL"])
            self.assertEqual([signal.market for signal in signals], ["us"])

    def test_disciplined_store_filters_to_us_market_and_pool(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            disciplined_path = Path(tmpdir) / "paper_us.db"
            _init_dsa_db(dsa_path)
            _init_disciplined_db(disciplined_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_analysis(conn, 1, "AAPL", "buy")
                _insert_analysis(conn, 2, "600519", "buy")
                _insert_analysis(conn, 3, "TSLA", "buy")
            with sqlite3.connect(disciplined_path) as conn:
                _insert_disciplined_signal(conn, 1, "AAPL", "buy", "us", 1)
                _insert_disciplined_signal(conn, 2, "600519", "buy", "cn", 2)
                _insert_disciplined_signal(conn, 3, "TSLA", "buy", "us", 3)

            reader = UsSignalReader(dsa_path, disciplined_path, stock_pool=("AAPL", "MSFT"))

            signals = reader.active_signals_before(date(2026, 7, 8))

            self.assertEqual([signal.stock_code for signal in signals], ["AAPL"])
            self.assertEqual([signal.market for signal in signals], ["us"])
            self.assertEqual(signals[0].source_type, "disciplined_signal")

    def test_reader_excludes_expired_signals_in_disciplined_branch(self) -> None:
        # Regression guard for the 2026-07 stale-signal leak: disciplined rows
        # are never status-flipped after insert, so expiry must be enforced at
        # selection time. Boundary: a plan expiring ON the execution date is
        # still executable (mirrors LimitFillModel.expired_unfilled's strict >).
        # Mirrors executor/tests/test_signal_reader.py.
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            disciplined_path = Path(tmpdir) / "paper_us.db"
            _init_dsa_db(dsa_path)
            _init_disciplined_db(disciplined_path)
            with sqlite3.connect(disciplined_path) as conn:
                _insert_disciplined_signal(conn, 1, "AAPL", "buy", "us", 1, expires_at="2026-07-15 16:00:00")
                _insert_disciplined_signal(conn, 2, "MSFT", "buy", "us", 2, expires_at="2026-07-16 01:00:00")
                _insert_disciplined_signal(conn, 3, "NVDA", "buy", "us", 3, expires_at=None)

            reader = UsSignalReader(dsa_path, disciplined_path, stock_pool=("AAPL", "MSFT", "NVDA"))
            codes = [s.stock_code for s in reader.active_signals_before(date(2026, 7, 16))]

            self.assertEqual(codes, ["MSFT", "NVDA"])
            self.assertNotIn("AAPL", codes)

    def test_reader_excludes_expired_signals_in_decision_signals_branch(self) -> None:
        # Same guard for the raw decision_signals fallback path.
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            _init_dsa_db(dsa_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_signal(conn, 1, "AAPL", "buy", "us", 1, expires_at="2026-07-15 16:00:00")
                _insert_signal(conn, 2, "MSFT", "buy", "us", 2, expires_at="2026-07-16 01:00:00")
                _insert_signal(conn, 3, "NVDA", "buy", "us", 3, expires_at=None)

            reader = UsSignalReader(dsa_path, stock_pool=("AAPL", "MSFT", "NVDA"))
            codes = [s.stock_code for s in reader.active_signals_before(date(2026, 7, 16))]

            self.assertEqual(codes, ["MSFT", "NVDA"])
            self.assertNotIn("AAPL", codes)

    def test_disciplined_store_excludes_g5_completed_on_execution_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            disciplined_path = Path(tmpdir) / "paper_us.db"
            _init_dsa_db(dsa_path)
            _init_disciplined_db(disciplined_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_analysis(conn, 1, "AAPL", "buy")
                _insert_analysis(conn, 2, "MSFT", "buy")
            with sqlite3.connect(disciplined_path) as conn:
                _insert_disciplined_signal(
                    conn,
                    1,
                    "AAPL",
                    "buy",
                    "us",
                    1,
                    completed_at="2026-07-08 05:00:00",
                )
                _insert_disciplined_signal(
                    conn,
                    2,
                    "MSFT",
                    "buy",
                    "us",
                    2,
                    completed_at="2026-07-07 05:00:00",
                )

            reader = UsSignalReader(dsa_path, disciplined_path, stock_pool=("AAPL", "MSFT"))

            signals = reader.active_signals_before(date(2026, 7, 8))

            self.assertEqual([signal.stock_code for signal in signals], ["MSFT"])

    def test_disciplined_store_allows_us_postclose_signal_completed_in_beijing_morning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            disciplined_path = Path(tmpdir) / "paper_us.db"
            _init_dsa_db(dsa_path)
            _init_disciplined_db(disciplined_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_analysis(conn, 1, "AAPL", "buy")
            with sqlite3.connect(disciplined_path) as conn:
                _insert_disciplined_signal(
                    conn,
                    1,
                    "AAPL",
                    "buy",
                    "us",
                    1,
                    completed_at="2026-07-08 04:19:10",
                )
                conn.execute(
                    """
                    update disciplined_signals
                    set created_at = '2026-07-08 04:12:45',
                        decision_timestamp = '2026-07-07 20:12:45.000+00:00',
                        market_phase = 'postclose',
                        data_asof = '2026-07-07',
                        bar_cutoff = '2026-07-07 20:00:00.000+00:00',
                        news_cutoff = '2026-07-07 20:12:45.000+00:00'
                    where source_signal_id = 1
                    """
                )

            reader = UsSignalReader(dsa_path, disciplined_path, stock_pool=("AAPL",))

            signals = reader.open_candidates(date(2026, 7, 8))

            self.assertEqual([signal.stock_code for signal in signals], ["AAPL"])

    def test_disciplined_store_uses_embedded_analysis_when_oos_db_has_no_analysis_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            disciplined_path = Path(tmpdir) / "paper_us.db"
            _init_dsa_db(dsa_path)
            _init_disciplined_db(disciplined_path)
            with sqlite3.connect(disciplined_path) as conn:
                _insert_disciplined_signal(conn, 1, "AAPL", "buy", "us", 101)
                conn.execute(
                    """
                    update disciplined_signals
                    set dsa_analysis_json = ?
                    where source_signal_id = 1
                    """,
                    (
                        json.dumps(
                            {
                                "id": 101,
                                "code": "AAPL",
                                "operation_advice": "buy",
                                "created_at": "2026-07-07 12:00:00",
                            }
                        ),
                    ),
                )

            reader = UsSignalReader(dsa_path, disciplined_path, stock_pool=("AAPL",))

            signals = reader.open_candidates(date(2026, 7, 8))

            self.assertEqual([signal.stock_code for signal in signals], ["AAPL"])

    def test_g5_position_context_samples_do_not_open_flat_us_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            disciplined_path = Path(tmpdir) / "paper_us.db"
            _init_dsa_db(dsa_path)
            _init_disciplined_db(disciplined_path)
            samples = ((1, "AAPL"), (2, "JPM"), (3, "MSFT"))
            with sqlite3.connect(dsa_path) as conn:
                for row_id, code in samples:
                    _insert_analysis(conn, row_id, code, "持有")
            with sqlite3.connect(disciplined_path) as conn:
                for row_id, code in samples:
                    _insert_disciplined_signal(
                        conn,
                        row_id,
                        code,
                        "buy",
                        "us",
                        row_id,
                        completion_payload=_intent_payload(),
                    )

            reader = UsSignalReader(dsa_path, disciplined_path, stock_pool=("AAPL", "JPM", "MSFT"))

            self.assertEqual(reader.open_candidates(date(2026, 7, 8)), [])
            conflicts = reader.s1_conflicts(date(2026, 7, 8))

            self.assertEqual([signal.stock_code for signal, _ in conflicts], ["AAPL", "JPM", "MSFT"])
            self.assertTrue(all(advice.conflict_status == "position_context_split" for _, advice in conflicts))
            self.assertTrue(all(advice.flat_account_action == "watch" for _, advice in conflicts))
            self.assertTrue(all(advice.holding_action == "hold" for _, advice in conflicts))

    def test_us_holding_context_can_use_holding_action_for_exit_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            disciplined_path = Path(tmpdir) / "paper_us.db"
            _init_dsa_db(dsa_path)
            _init_disciplined_db(disciplined_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_analysis(conn, 1, "JPM", "持仓者减仓，空仓者等待回踩")
            with sqlite3.connect(disciplined_path) as conn:
                _insert_disciplined_signal(
                    conn,
                    1,
                    "JPM",
                    "buy",
                    "us",
                    1,
                    completion_payload=_intent_payload(holding_action="reduce"),
                )

            reader = UsSignalReader(dsa_path, disciplined_path, stock_pool=("JPM",))
            candidates = reader.exit_candidates(date(2026, 7, 8), held_symbols={"JPM"})

            self.assertEqual([signal.stock_code for signal in candidates], ["JPM"])
            self.assertEqual(candidates[0].action, "reduce")

    def test_disciplined_store_excludes_us_signal_completed_after_regular_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            disciplined_path = Path(tmpdir) / "paper_us.db"
            _init_dsa_db(dsa_path)
            _init_disciplined_db(disciplined_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_analysis(conn, 1, "AAPL", "buy")
            with sqlite3.connect(disciplined_path) as conn:
                _insert_disciplined_signal(
                    conn,
                    1,
                    "AAPL",
                    "buy",
                    "us",
                    1,
                    completed_at="2026-07-08 22:00:00",
                )
                conn.execute(
                    """
                    update disciplined_signals
                    set created_at = '2026-07-08 04:12:45',
                        decision_timestamp = '2026-07-07 20:12:45.000+00:00',
                        market_phase = 'postclose',
                        data_asof = '2026-07-07',
                        bar_cutoff = '2026-07-07 20:00:00.000+00:00',
                        news_cutoff = '2026-07-07 20:12:45.000+00:00'
                    where source_signal_id = 1
                    """
                )

            reader = UsSignalReader(dsa_path, disciplined_path, stock_pool=("AAPL",))

            signals = reader.active_signals_before(date(2026, 7, 8))

            self.assertEqual(signals, [])

    def test_entry_points_reuse_market_filtered_active_signals(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            disciplined_path = Path(tmpdir) / "paper_us.db"
            _init_dsa_db(dsa_path)
            _init_disciplined_db(disciplined_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_analysis(conn, 1, "AAPL", "buy")
                _insert_analysis(conn, 2, "MSFT", "hold")
                _insert_analysis(conn, 3, "600519", "buy")
            with sqlite3.connect(disciplined_path) as conn:
                _insert_disciplined_signal(conn, 1, "AAPL", "buy", "us", 1)
                _insert_disciplined_signal(conn, 2, "MSFT", "sell", "us", 2)
                _insert_disciplined_signal(conn, 3, "600519", "buy", "cn", 3)

            reader = UsSignalReader(dsa_path, disciplined_path, stock_pool=("AAPL", "MSFT"))

            open_candidates = reader.open_candidates(date(2026, 7, 8))
            conflicts = reader.s1_conflicts(date(2026, 7, 8))

            self.assertEqual([signal.stock_code for signal in open_candidates], ["AAPL"])
            self.assertEqual([(signal.stock_code, advice.action) for signal, advice in conflicts], [("MSFT", "hold")])


if __name__ == "__main__":
    unittest.main()
