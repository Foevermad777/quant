import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from executor.signal_reader import SignalReader, advice_to_action


DSA_DB = Path(__file__).resolve().parents[2] / "runtime_data" / "dsa" / "stock_analysis.db"


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
            """
        )


def _insert_analysis(conn: sqlite3.Connection, row_id: int, code: str, advice: str) -> None:
    conn.execute(
        "insert into analysis_history(id, code, name, operation_advice, created_at) values (?, ?, ?, ?, ?)",
        (row_id, code, code, advice, "2026-07-08 10:00:00"),
    )


def _insert_signal(conn: sqlite3.Connection, row_id: int, code: str, action: str, source_report_id: int) -> None:
    conn.execute(
        """
        insert into decision_signals(
            id, stock_code, stock_name, action, confidence, entry_high, entry_low,
            stop_loss, target_price, status, created_at, expires_at, source_report_id,
            metadata_json, market, source_type, source_agent, plan_quality
        )
        values (?, ?, ?, ?, 0.8, 12.0, 10.0, 9.0, 15.0, 'active',
                '2026-07-08 10:00:00', '2026-07-15 15:00:00', ?,
                ?, 'cn', 'analysis', 'test', 'ok')
        """,
        (row_id, code, code, action, source_report_id, json.dumps({})),
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


def _insert_disciplined_signal(
    conn: sqlite3.Connection,
    source_signal_id: int,
    code: str,
    action: str,
    source_report_id: int,
    payload: dict,
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
        values (?, ?, ?, ?, 'cn', ?, 0.8, 12.0, 10.0, 9.0, 15.0,
                'active', '2026-07-08 10:00:00', '2026-07-15 15:00:00',
                'ok', 'g5-discipline-v0.1', 'g5-minimal-v0.1',
                '2026-07-08 10:05:00', '2026-07-08 10:05:00',
                'gemini-3.5-flash', ?, 1, 'pass', '[]')
        """,
        (source_signal_id, source_report_id, code, code, action, json.dumps(payload, ensure_ascii=False)),
    )


class SignalReaderTests(unittest.TestCase):
    def test_advice_to_action_normalizes_hold(self) -> None:
        self.assertEqual(advice_to_action("持有"), "hold")
        self.assertEqual(advice_to_action("持有观察"), "hold")
        self.assertEqual(advice_to_action("观望"), "watch")
        self.assertEqual(advice_to_action("卖出"), "sell")
        self.assertEqual(advice_to_action("减仓"), "reduce")
        self.assertEqual(advice_to_action("避免"), "avoid")

    def test_disciplined_reader_sets_sqlite_busy_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "paper.db"
            with sqlite3.connect(store_path) as conn:
                conn.execute("create table disciplined_signals(source_signal_id integer primary key)")
            reader = SignalReader(Path(tmpdir) / "dsa.db", store_path, sqlite_timeout_seconds=0.25)

            with reader._connect_disciplined() as conn:
                row = conn.execute("pragma busy_timeout").fetchone()

            self.assertEqual(row[0], 250)

    @unittest.skipUnless(DSA_DB.exists(), "local DSA database is not present")
    def test_600900_s1_conflict_is_skipped(self) -> None:
        reader = SignalReader(DSA_DB)

        signal = reader.get_signal(6)
        advice = reader.advice_for_signal(signal)

        self.assertEqual(signal.stock_code, "600900")
        self.assertEqual(signal.action, "buy")
        self.assertEqual(advice.action, "hold")
        self.assertFalse(reader.is_s1_consistent(signal, advice))

    @unittest.skipUnless(DSA_DB.exists(), "local DSA database is not present")
    def test_open_candidates_exclude_today_and_s1_conflicts(self) -> None:
        reader = SignalReader(DSA_DB)

        candidates = reader.open_candidates(date(2026, 7, 6))
        ids = {signal.id for signal in candidates}

        self.assertNotIn(6, ids)
        self.assertNotIn(7, ids)

    def test_g5_position_context_samples_do_not_open_flat_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            disciplined_path = Path(tmpdir) / "paper.db"
            _init_dsa_db(dsa_path)
            _init_disciplined_db(disciplined_path)
            with sqlite3.connect(dsa_path) as conn:
                for row_id, code in ((1, "600900"), (2, "600036")):
                    _insert_analysis(conn, row_id, code, "持有")
                    _insert_signal(conn, row_id, code, "buy", row_id)
            with sqlite3.connect(disciplined_path) as conn:
                for row_id, code in ((1, "600900"), (2, "600036")):
                    _insert_disciplined_signal(conn, row_id, code, "buy", row_id, _intent_payload())

            reader = SignalReader(dsa_path, disciplined_path)

            self.assertEqual(reader.open_candidates(date(2026, 7, 9)), [])
            conflicts = reader.s1_conflicts(date(2026, 7, 9))

            self.assertEqual([signal.stock_code for signal, _ in conflicts], ["600900", "600036"])
            self.assertTrue(all(advice.conflict_status == "position_context_split" for _, advice in conflicts))
            self.assertTrue(all(advice.flat_account_action == "watch" for _, advice in conflicts))
            self.assertTrue(all(advice.holding_action == "hold" for _, advice in conflicts))

    def test_conditional_entry_is_promoted_to_limit_plan_not_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            _init_dsa_db(dsa_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_analysis(conn, 1, "600900", "空仓者可逢低，等待回踩后分批建仓")
                _insert_signal(conn, 1, "600900", "buy", 1)

            reader = SignalReader(dsa_path)
            candidates = reader.open_candidates(date(2026, 7, 9))
            conflicts = reader.s1_conflicts(date(2026, 7, 9))

            self.assertEqual(conflicts, [])
            self.assertEqual(len(candidates), 1)
            candidate = candidates[0]
            self.assertEqual(candidate.action, "buy")
            plan = candidate.metadata["execution_plan"]
            self.assertEqual(plan["type"], "conditional_limit")
            self.assertEqual(plan["limit_price"], candidate.entry_high)
            self.assertEqual(candidate.metadata["intent_resolution"]["conflict_status"], "conditional_entry")

    def test_conditional_entry_for_held_symbol_stays_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            _init_dsa_db(dsa_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_analysis(conn, 1, "600900", "空仓者可逢低，等待回踩后分批建仓")
                _insert_signal(conn, 1, "600900", "buy", 1)

            reader = SignalReader(dsa_path)
            candidates = reader.open_candidates(date(2026, 7, 9), held_symbols={"600900"})
            conflicts = reader.s1_conflicts(date(2026, 7, 9), held_symbols={"600900"})

            self.assertEqual(candidates, [])
            self.assertEqual(len(conflicts), 1)

    def test_holding_context_can_use_holding_action_for_exit_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            disciplined_path = Path(tmpdir) / "paper.db"
            _init_dsa_db(dsa_path)
            _init_disciplined_db(disciplined_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_analysis(conn, 1, "600900", "持仓者减仓，空仓者等待回踩")
                _insert_signal(conn, 1, "600900", "buy", 1)
            with sqlite3.connect(disciplined_path) as conn:
                _insert_disciplined_signal(
                    conn,
                    1,
                    "600900",
                    "buy",
                    1,
                    _intent_payload(holding_action="reduce"),
                )

            reader = SignalReader(dsa_path, disciplined_path)
            candidates = reader.exit_candidates(date(2026, 7, 9), held_symbols={"600900"})

            self.assertEqual([signal.stock_code for signal in candidates], ["600900"])
            self.assertEqual(candidates[0].action, "reduce")

    def test_reader_isolates_market_in_disciplined_branch(self) -> None:
        # decision_signals / disciplined_signals are shared CN+US tables. The CN
        # reader must never surface US rows (and vice versa), otherwise the CN
        # executor would process US signals into the CN ledger. Regression guard
        # for the 2026-07 cross-market leak.
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            disciplined_path = Path(tmpdir) / "disciplined.db"
            _init_dsa_db(dsa_path)
            _init_disciplined_db(disciplined_path)
            with sqlite3.connect(disciplined_path) as conn:
                for sid, code, mkt in ((1, "600519", "cn"), (2, "AAPL", "us")):
                    conn.execute(
                        """
                        insert into disciplined_signals(
                            source_signal_id, source_report_id, stock_code, stock_name, market,
                            action, confidence, entry_high, entry_low, stop_loss, target_price,
                            status, created_at, expires_at, plan_quality, schema_version,
                            completion_version, completed_at, updated_at, model,
                            completion_payload_json, gate_accepted, gate_action, gate_reasons_json
                        )
                        values (?, ?, ?, ?, ?, 'buy', 0.8, 12.0, 10.0, 9.0, 15.0,
                                'active', '2026-07-08 10:00:00', '2026-07-15 15:00:00',
                                'ok', 'g5-discipline-v0.1', 'g5-minimal-v0.1',
                                '2026-07-08 10:05:00', '2026-07-08 10:05:00',
                                'gemini-3.5-flash', ?, 1, 'pass', '[]')
                        """,
                        (sid, sid, code, code, mkt, json.dumps({})),
                    )

            cn_codes = [s.stock_code for s in SignalReader(dsa_path, disciplined_path, market="cn").active_signals_before(date(2026, 7, 9))]
            us_codes = [s.stock_code for s in SignalReader(dsa_path, disciplined_path, market="us").active_signals_before(date(2026, 7, 9))]

            self.assertEqual(cn_codes, ["600519"])
            self.assertEqual(us_codes, ["AAPL"])
            self.assertNotIn("AAPL", cn_codes)

    def test_reader_isolates_market_in_decision_signals_branch(self) -> None:
        # Same guard for the raw decision_signals fallback path (no disciplined store).
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            _init_dsa_db(dsa_path)
            with sqlite3.connect(dsa_path) as conn:
                for sid, code, mkt in ((1, "600519", "cn"), (2, "AAPL", "us")):
                    conn.execute(
                        """
                        insert into decision_signals(
                            id, stock_code, stock_name, action, confidence, entry_high, entry_low,
                            stop_loss, target_price, status, created_at, expires_at, source_report_id,
                            metadata_json, market, source_type, source_agent, plan_quality
                        )
                        values (?, ?, ?, 'buy', 0.8, 12.0, 10.0, 9.0, 15.0, 'active',
                                '2026-07-08 10:00:00', '2026-07-15 15:00:00', ?,
                                ?, ?, 'analysis', 'test', 'ok')
                        """,
                        (sid, code, code, sid, json.dumps({}), mkt),
                    )

            reader = SignalReader(dsa_path, market="cn", use_disciplined_signals=False)
            codes = [s.stock_code for s in reader.active_signals_before(date(2026, 7, 9))]
            self.assertEqual(codes, ["600519"])
            self.assertNotIn("AAPL", codes)


if __name__ == "__main__":
    unittest.main()
