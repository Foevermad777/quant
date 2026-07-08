import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from executor.config import ExecutorConfig
from executor.discipline_completion import DsaSignalContextLoader
from executor.redteam_validation import check_oos_gate, run_stress_scenarios
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


def _insert_signal(
    conn: sqlite3.Connection,
    row_id: int,
    code: str,
    *,
    created_at: str = "2026-07-05 18:00:00",
    source_report_id: int = 1,
) -> None:
    conn.execute(
        """
        insert into decision_signals(
            id, stock_code, stock_name, action, confidence, entry_high, entry_low,
            stop_loss, target_price, status, created_at, expires_at, source_report_id,
            metadata_json, market, source_type, source_agent, plan_quality
        )
        values (?, ?, ?, 'buy', 0.8, 10.0, 9.5, 9.0, 12.0, 'active',
                ?, '2026-07-10 15:00:00', ?, '{}', 'cn', 'analysis', 'test', 'ok')
        """,
        (row_id, code, code, created_at, source_report_id),
    )


def _insert_bar(conn: sqlite3.Connection, code: str, day: str, open_price: float, close_price: float) -> None:
    conn.execute(
        """
        insert into stock_daily(code, date, open, high, low, close, volume, amount, pct_chg)
        values (?, ?, ?, ?, ?, ?, 1000, 10000, 0)
        """,
        (code, day, open_price, max(open_price, close_price), min(open_price, close_price), close_price),
    )


def _init_disciplined_store(path: Path) -> None:
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
                score integer,
                entry_low real,
                entry_high real,
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
                prompt_tokens integer,
                completion_tokens integer,
                total_tokens integer,
                latency_ms integer,
                estimated_cost_usd real,
                scenarios_json text not null,
                invalid_conditions_json text not null,
                source_attribution_json text not null,
                confidence_rationale text,
                single_side_flag integer not null,
                normalized_terms_json text,
                completion_payload_json text not null,
                raw_dsa_signal_json text not null,
                dsa_analysis_json text not null,
                dated_news_json text not null,
                undated_news_json text not null,
                guardrail_json text not null,
                gate_accepted integer not null,
                gate_action text not null,
                gate_reasons_json text not null
            );
            """
        )


def _insert_disciplined(path: Path, row_id: int, *, created_at: str, completed_at: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            insert into disciplined_signals(
                source_signal_id, source_report_id, stock_code, stock_name, market,
                action, confidence, score, entry_low, entry_high, stop_loss, target_price,
                status, created_at, expires_at, decision_timestamp, market_phase, data_asof,
                bar_cutoff, news_cutoff, plan_quality, schema_version, completion_version,
                completed_at, updated_at, model, prompt_tokens, completion_tokens, total_tokens,
                latency_ms, estimated_cost_usd, scenarios_json, invalid_conditions_json,
                source_attribution_json, confidence_rationale, single_side_flag,
                normalized_terms_json, completion_payload_json, raw_dsa_signal_json,
                dsa_analysis_json, dated_news_json, undated_news_json, guardrail_json,
                gate_accepted, gate_action, gate_reasons_json
            )
            values (
                ?, 1, '600519', '贵州茅台', 'cn', 'buy', 0.8, 80, 9.5, 10.0, 9.0, 12.0,
                'active', ?, '2026-07-10 15:00:00', ?, 'postclose', '2026-07-05',
                '2026-07-05 15:00:00', ?, 'ok', 'g5-discipline-v0.1', 'g5-minimal-v0.1',
                ?, ?, 'test', null, null, null, null, 0.0, '{}', '[]', '[]', 'ok',
                0, '[]', '{}', '{}', '{}', '[]', '[]', '{}', 1, 'pass', '[]'
            )
            """,
            (row_id, created_at, created_at, created_at, completed_at, completed_at),
        )


class RedTeamValidationTests(unittest.TestCase):
    def test_oos_gate_fails_closed_when_required_history_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            _init_dsa_db(dsa_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_bar(conn, "600519", "2025-03-14", 10.0, 10.5)
            config = ExecutorConfig(dsa_db_path=dsa_path, ledger_db_path=Path(tmpdir) / "paper.db", stock_pool=("600519",))

            result = check_oos_gate(config, date(2024, 1, 1), date(2025, 12, 31))

            self.assertEqual(result.status, "failed_closed")
            self.assertEqual(result.rows[0].status, "failed_closed")
            self.assertIn("start_after_required", result.rows[0].reason)
            self.assertIn("end_before_required", result.rows[0].reason)

    def test_disciplined_signal_completed_on_execution_day_is_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            store_path = Path(tmpdir) / "paper.db"
            _init_dsa_db(dsa_path)
            _init_disciplined_store(store_path)
            _insert_disciplined(store_path, 1, created_at="2026-07-05 18:00:00", completed_at="2026-07-06 18:10:00")
            _insert_disciplined(store_path, 2, created_at="2026-07-05 18:00:00", completed_at="2026-07-05 18:10:00")
            reader = SignalReader(dsa_path, store_path)

            signals = reader.active_signals_before(date(2026, 7, 6))

            self.assertEqual([signal.id for signal in signals], [2])

    def test_g5_loader_cuts_news_after_signal_decision_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            _init_dsa_db(dsa_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_analysis(conn, 1, "600519", "买入", "2026-07-07 10:00:00")
                _insert_signal(conn, 1, "600519", created_at="2026-07-07 10:00:00")
                conn.execute(
                    """
                    insert into news_intel(id, code, title, source, provider, published_date)
                    values (1, '600519', '决策前新闻', 'fixture', 'test', '2026-07-07 09:30:00')
                    """
                )
                conn.execute(
                    """
                    insert into news_intel(id, code, title, source, provider, published_date)
                    values (2, '600519', '决策后新闻', 'fixture', 'test', '2026-07-07 15:30:00')
                    """
                )

            context = DsaSignalContextLoader(dsa_path).load(1)

            self.assertEqual([item["title"] for item in context.dated_news], ["决策前新闻"])

    def test_stress_runner_replays_into_temporary_ledger_and_flags_entry_band(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            ledger_path = Path(tmpdir) / "paper.db"
            _init_dsa_db(dsa_path)
            with sqlite3.connect(dsa_path) as conn:
                _insert_analysis(conn, 1, "600519", "买入", "2026-07-05 18:00:00")
                _insert_analysis(conn, 2, "600519", "买入", "2026-07-06 18:00:00")
                _insert_signal(conn, 1, "600519", source_report_id=1)
                _insert_bar(conn, "600519", "2026-07-06", 10.5, 10.8)
            config = ExecutorConfig(
                dsa_db_path=dsa_path,
                ledger_db_path=ledger_path,
                use_disciplined_signals=False,
                stock_pool=("600519",),
                commission_rate=0.0,
                min_commission=0.0,
                stamp_tax_rate=0.0,
                slippage_rate=0.001,
                per_signal_cash=10_000.0,
            )

            results = run_stress_scenarios(config, date(2026, 7, 6), date(2026, 7, 6), liquidity_impact_bps=10.0)

            base = results[0]
            self.assertEqual(base.scenario, "base")
            self.assertEqual(base.trade_count, 1)
            self.assertGreater(base.estimated_liquidity_impact, 0)
            self.assertEqual(len(base.buy_diagnostics), 1)
            self.assertTrue(base.buy_diagnostics[0].exec_outside_band)
            self.assertGreater(results[1].buy_diagnostics[0].exec_price, base.buy_diagnostics[0].exec_price)
            self.assertFalse(ledger_path.exists(), "stress runner must not write into the live ledger")


if __name__ == "__main__":
    unittest.main()
