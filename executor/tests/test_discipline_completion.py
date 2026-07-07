import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from executor.discipline_completion import (
    DisciplineCompleter,
    GeminiUsage,
    _guardrail_payload,
    normalize_completion_payload,
)
from executor.guardrails import MISSING_INVALID_CONDITIONS, MISSING_SCENARIOS, gate_dsa_output
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
                sentiment_score integer,
                analysis_summary text,
                news_content text,
                trend_prediction text,
                created_at text
            );
            create table decision_signals (
                id integer primary key,
                stock_code text not null,
                stock_name text,
                market text,
                source_type text,
                source_agent text,
                source_report_id integer,
                action text not null,
                confidence real,
                score integer,
                entry_high real,
                entry_low real,
                stop_loss real,
                target_price real,
                invalidation text,
                reason text,
                risk_summary text,
                catalyst_summary text,
                plan_quality text,
                status text not null,
                created_at text,
                expires_at text,
                metadata_json text
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
        conn.execute(
            """
            insert into analysis_history(
                id, code, name, operation_advice, sentiment_score, analysis_summary,
                news_content, trend_prediction, created_at
            )
            values (21, '600519', '贵州茅台', '持有', 58, '估值底部与技术弱势博弈', '', '震荡', '2026-07-07 15:50:48')
            """
        )
        conn.execute(
            """
            insert into decision_signals(
                id, stock_code, stock_name, market, source_type, source_agent, source_report_id,
                action, confidence, score, entry_high, entry_low, stop_loss, target_price,
                invalidation, reason, risk_summary, catalyst_summary, plan_quality, status,
                created_at, expires_at, metadata_json
            )
            values (
                18, '600519', '贵州茅台', 'cn', 'analysis', 'dsa', 21,
                'hold', 0.6, 58, 1170.0, 1150.0, 1130.0, 1300.0,
                '', '长线配置价值但短期等待止跌', '技术面MACD空头排列', '估值处于底部',
                'ok', 'active', '2026-07-07 07:50:48', '2026-07-10 07:50:48', '{}'
            )
            """
        )
        conn.execute(
            """
            insert into news_intel(id, code, title, snippet, url, source, provider, published_date)
            values (
                120, '600519', '贵州茅台:1188.80 -1.50% -18.11 600519 搜狐证券',
                '2026-07-07 15:00 市盈TTM 17.97', 'https://q.stock.sohu.com/cn/600519',
                '搜狐股票', 'Bocha', '2026-07-07 00:00:00'
            )
            """
        )


class FakeGeminiClient:
    model = "fake-gemini"

    def __init__(self) -> None:
        self.calls = 0

    def generate_json(self, prompt, schema):
        self.calls += 1
        return (
            {
                "scenarios": {
                    "base": {
                        "assumptions": ["估值低位但技术仍弱"],
                        "triggers": ["收复1200元关口"],
                        "key_risks": ["量能不足"],
                        "probability": 0.55,
                    },
                    "bull": {
                        "assumptions": ["政策扰动缓和"],
                        "triggers": ["放量突破1170-1200区间"],
                        "key_risks": ["白酒需求恢复不及预期"],
                        "probability": 0.25,
                    },
                    "bear": {
                        "assumptions": ["技术弱势延续"],
                        "triggers": ["跌破1130止损位"],
                        "key_risks": ["行业估值继续下修"],
                        "probability": 0.20,
                    },
                },
                "invalid_conditions": [
                    {"condition": "跌破止损位", "trigger_price_or_data": "1130", "type": "price"}
                ],
                "source_attribution": [
                    {
                        "claim": "7月7日收盘价为1188.80且当日下跌1.50%",
                        "source": "news_intel#120 搜狐股票",
                        "published_date": "2026-07-07",
                    }
                ],
                "confidence": 0.52,
                "confidence_rationale": "证据有价格日期但缺少资金流，按技术反证降权。",
                "single_side_flag": False,
                "normalized_terms": [],
            },
            GeminiUsage(prompt_tokens=100, completion_tokens=80, total_tokens=180, latency_ms=1234),
        )


class ScriptedGeminiClient(FakeGeminiClient):
    def __init__(self, outcomes) -> None:
        super().__init__()
        self.outcomes = list(outcomes)

    def generate_json(self, prompt, schema):
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                self.calls += 1
                raise outcome
        return super().generate_json(prompt, schema)


class DisciplineCompletionTests(unittest.TestCase):
    def test_raw_dsa_payload_rejects_but_completed_signal_persists_and_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            store_path = Path(tmpdir) / "paper.db"
            _init_dsa_db(dsa_path)
            fake_client = FakeGeminiClient()
            completer = DisciplineCompleter(dsa_db_path=dsa_path, store_db_path=store_path, client=fake_client)

            context = completer.loader.load(18)
            raw_gate = gate_dsa_output(context.signal)
            self.assertFalse(raw_gate.accepted)
            self.assertIn(MISSING_INVALID_CONDITIONS, raw_gate.gate_reasons)
            self.assertIn(MISSING_SCENARIOS, raw_gate.gate_reasons)

            summary = completer.complete_signal(18)
            self.assertFalse(summary.skipped)
            self.assertTrue(summary.gate_accepted)
            self.assertEqual(fake_client.calls, 1)

            skipped = completer.complete_signal(18)
            self.assertTrue(skipped.skipped)
            self.assertEqual(fake_client.calls, 1)

            reader = SignalReader(dsa_path, store_path)
            signals = reader.active_signals_before(date(2026, 7, 8))
            self.assertEqual([signal.id for signal in signals], [18])
            self.assertEqual(signals[0].source_type, "disciplined_signal")
            self.assertEqual(signals[0].confidence, 0.52)
            self.assertEqual(signals[0].metadata["discipline"]["schema_version"], "g5-discipline-v0.1")

    def test_source_attribution_must_match_dated_news(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            _init_dsa_db(dsa_path)
            completer = DisciplineCompleter(dsa_db_path=dsa_path, store_db_path=Path(tmpdir) / "paper.db", client=FakeGeminiClient())
            context = completer.loader.load(18)
            payload = normalize_completion_payload(
                {
                    "scenarios": {
                        "base": {"assumptions": ["a"], "triggers": ["b"], "key_risks": ["c"], "probability": 1.0},
                        "bull": {"assumptions": ["a"], "triggers": ["b"], "key_risks": ["c"], "probability": 0.0},
                        "bear": {"assumptions": ["a"], "triggers": ["b"], "key_risks": ["c"], "probability": 0.0},
                    },
                    "invalid_conditions": [
                        {"condition": "bad data", "trigger_price_or_data": "missing", "type": "data"}
                    ],
                    "source_attribution": [
                        {"claim": "invented", "source": "agent:gemini", "published_date": "2026-01-01"}
                    ],
                    "confidence": 0.9,
                    "confidence_rationale": "test",
                    "single_side_flag": False,
                },
                context,
            )

            result = gate_dsa_output(_guardrail_payload(context.signal, payload))

            self.assertFalse(result.accepted)

    def test_complete_many_records_failure_and_continues(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            store_path = Path(tmpdir) / "paper.db"
            _init_dsa_db(dsa_path)
            fake_client = FakeGeminiClient()
            completer = DisciplineCompleter(dsa_db_path=dsa_path, store_db_path=store_path, client=fake_client)

            summaries = completer.complete_many([999, 18], retries=0, retry_delay_seconds=0)

            self.assertEqual(len(summaries), 2)
            self.assertEqual(summaries[0].source_signal_id, 999)
            self.assertEqual(summaries[0].gate_action, "error")
            self.assertIsNotNone(summaries[0].error)
            self.assertEqual(summaries[0].attempts, 1)
            self.assertEqual(summaries[1].source_signal_id, 18)
            self.assertTrue(summaries[1].gate_accepted)
            self.assertIsNone(summaries[1].error)
            self.assertEqual(fake_client.calls, 1)

    def test_complete_many_retries_one_timeout_per_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dsa_path = Path(tmpdir) / "dsa.db"
            store_path = Path(tmpdir) / "paper.db"
            _init_dsa_db(dsa_path)
            fake_client = ScriptedGeminiClient([TimeoutError("Gemini timed out")])
            completer = DisciplineCompleter(dsa_db_path=dsa_path, store_db_path=store_path, client=fake_client)

            summaries = completer.complete_many([18], retries=1, retry_delay_seconds=0)

            self.assertEqual(len(summaries), 1)
            self.assertTrue(summaries[0].gate_accepted)
            self.assertEqual(summaries[0].attempts, 2)
            self.assertIsNone(summaries[0].error)
            self.assertEqual(fake_client.calls, 2)


if __name__ == "__main__":
    unittest.main()
