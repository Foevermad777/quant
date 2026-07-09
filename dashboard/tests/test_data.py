from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dashboard.data import DashboardPaths, build_overview


def _paths(root: Path) -> DashboardPaths:
    return DashboardPaths(
        project_root=root,
        dsa_db=root / "runtime_data" / "dsa" / "stock_analysis.db",
        cn_ledger_db=root / "runtime_data" / "quant" / "paper.db",
        us_ledger_db=root / "runtime_data" / "quant" / "paper_us.db",
        quant_dir=root / "runtime_data" / "quant",
        logs_dir=root / "runtime_data" / "logs",
    )


def _init_dsa(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            create table analysis_history (
                id integer primary key,
                code text not null,
                name text,
                report_type text,
                sentiment_score integer,
                operation_advice text,
                trend_prediction text,
                analysis_summary text,
                ideal_buy real,
                secondary_buy real,
                stop_loss real,
                take_profit real,
                created_at text
            );
            create table decision_signals (
                id integer primary key,
                stock_code text not null,
                stock_name text,
                market text,
                action text,
                action_label text,
                confidence real,
                score integer,
                horizon text,
                entry_low real,
                entry_high real,
                stop_loss real,
                target_price real,
                plan_quality text,
                status text,
                reason text,
                created_at text,
                expires_at text
            );
            insert into analysis_history(
                id, code, name, report_type, sentiment_score, operation_advice,
                trend_prediction, analysis_summary, created_at
            ) values
                (1, '600519', '贵州茅台', 'simple', 55, '观望', '震荡', '测试摘要', '2026-07-08 10:00:00'),
                (2, 'MARKET', '大盘复盘', 'market_review', 50, '查看复盘', '震荡', '市场摘要', '2026-07-08 10:05:00');
            insert into decision_signals(
                id, stock_code, stock_name, market, action, confidence, score,
                plan_quality, status, reason, created_at, expires_at
            ) values
                (1, '600519', '贵州茅台', 'cn', 'watch', 0.6, 55, 'complete', 'active', '等待', '2026-07-08 10:01:00', '2026-07-09 15:00:00');
            """
        )


def _init_ledger(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            create table account (
                id integer primary key,
                cash real not null,
                initial_cash real not null,
                updated_at text not null
            );
            create table positions (
                stock_code text primary key,
                quantity integer not null,
                old_quantity integer not null,
                avg_cost real not null,
                stop_loss real,
                target_price real,
                source_signal_id integer,
                updated_at text not null
            );
            create table trades (
                id integer primary key autoincrement,
                signal_id integer,
                stock_code text not null,
                side text not null,
                trade_date text not null,
                shares integer not null,
                fill_price real not null,
                exec_price real not null,
                gross_amount real not null,
                fees real not null,
                taxes real not null,
                cash_delta real not null,
                realized_pnl real,
                reason text not null,
                created_at text not null
            );
            create table order_attempts (
                id integer primary key autoincrement,
                signal_id integer,
                stock_code text not null,
                trade_date text not null,
                status text not null,
                reason text not null,
                price real,
                created_at text not null
            );
            create table signal_events (
                id integer primary key autoincrement,
                signal_id integer,
                stock_code text not null,
                event_date text not null,
                event_type text not null,
                reason text not null,
                created_at text not null
            );
            create table pending_exits (
                id integer primary key autoincrement,
                signal_id integer,
                stock_code text not null,
                shares integer not null,
                stop_price real not null,
                reason text not null,
                triggered_date text not null,
                earliest_trade_date text,
                status text not null,
                updated_at text not null
            );
            create table portfolio_snapshots (
                snapshot_date text primary key,
                cash real not null,
                market_value real not null,
                total_value real not null,
                realized_pnl real not null,
                unrealized_pnl real not null,
                created_at text not null
            );
            create table disciplined_signals (
                source_signal_id integer primary key,
                stock_code text not null,
                stock_name text,
                market text,
                action text,
                confidence real,
                score integer,
                status text,
                plan_quality text,
                gate_action text,
                gate_accepted integer,
                model text,
                total_tokens integer,
                estimated_cost_usd real,
                completed_at text
            );
            insert into account(id, cash, initial_cash, updated_at)
            values (1, 1000000, 1000000, '2026-07-08 10:00:00');
            insert into portfolio_snapshots(
                snapshot_date, cash, market_value, total_value, realized_pnl, unrealized_pnl, created_at
            ) values ('2026-07-08', 990000, 15000, 1005000, 1000, 4000, '2026-07-08 10:10:00');
            insert into signal_events(signal_id, stock_code, event_date, event_type, reason, created_at)
            values (1, '600519', '2026-07-08', 's1_conflict_skip', 'advice_signal_action_mismatch', '2026-07-08 10:11:00');
            insert into disciplined_signals(
                source_signal_id, stock_code, stock_name, market, action, confidence, score,
                status, plan_quality, gate_action, gate_accepted, model, total_tokens,
                estimated_cost_usd, completed_at
            ) values (1, '600519', '贵州茅台', 'cn', 'watch', 0.6, 55, 'active',
                      'complete', 'pass', 1, 'gemini-3.5-flash', 1234, 0.01,
                      '2026-07-08 10:09:00');
            """
        )


class DashboardDataTests(unittest.TestCase):
    def test_build_overview_reads_scan_ledger_and_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = _paths(root)
            _init_dsa(paths.dsa_db)
            _init_ledger(paths.cn_ledger_db)
            paths.logs_dir.mkdir(parents=True, exist_ok=True)
            (paths.logs_dir / "stock_analysis_20260708.log").write_text("line1\nline2\n", encoding="utf-8")

            overview = build_overview(paths)

            self.assertEqual(overview["scan"]["counts"]["analysis_history"], 2)
            self.assertEqual(overview["scan"]["counts"]["active_signals"], 1)
            self.assertEqual(overview["scan"]["pool_analysis"]["cn"][0]["code"], "600519")
            self.assertEqual(overview["executors"]["cn"]["latest_snapshot"]["total_value"], 1005000)
            self.assertAlmostEqual(overview["executors"]["cn"]["return_rate"], 0.005)
            self.assertTrue(overview["executors"]["cn"]["discipline"]["available"])
            self.assertEqual(overview["logs"]["dsa_daily"]["tail"], ["line1", "line2"])

    def test_missing_databases_are_reported_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            overview = build_overview(_paths(Path(tmpdir)))

            self.assertFalse(overview["scan"]["available"])
            self.assertFalse(overview["executors"]["cn"]["available"])
            self.assertFalse(overview["executors"]["us"]["available"])
            self.assertEqual(overview["scan"]["recent_signals"], [])


if __name__ == "__main__":
    unittest.main()

