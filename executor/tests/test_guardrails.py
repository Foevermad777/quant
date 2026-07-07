import unittest

from executor.guardrails import (
    MISSING_INVALID_CONDITIONS,
    MISSING_SCENARIOS,
    MISSING_SOURCE_ATTRIBUTION,
    gate_dsa_output,
)


def _valid_payload() -> dict:
    return {
        "stock_code": "600519",
        "action": "watch",
        "confidence": 0.72,
        "sources": [{"source": "fixture", "published_date": "2026-07-06"}],
        "invalid_conditions": [
            {"condition": "跌破 1180 且无法收回", "trigger_price_or_data": "1180", "type": "price"}
        ],
        "scenarios": {
            "base": {"probability": 0.55, "summary": "震荡整理"},
            "bull": {"probability": 0.25, "summary": "放量收复均线"},
            "bear": {"probability": 0.20, "summary": "跌破支撑"},
        },
    }


class GuardrailTests(unittest.TestCase):
    def test_valid_payload_passes(self) -> None:
        result = gate_dsa_output(_valid_payload())

        self.assertTrue(result.accepted)
        self.assertEqual(result.gate_reasons, ())
        self.assertEqual(result.signal["guardrail"]["action"], "pass")

    def test_missing_source_attribution_rejects(self) -> None:
        payload = _valid_payload()
        payload.pop("sources")

        result = gate_dsa_output(payload)

        self.assertFalse(result.accepted)
        self.assertIn(MISSING_SOURCE_ATTRIBUTION, result.gate_reasons)
        self.assertEqual(result.signal["guardrail"]["action"], "reject")

    def test_undated_source_attribution_rejects(self) -> None:
        payload = _valid_payload()
        payload["sources"] = [{"source": "agent:gemini"}]

        result = gate_dsa_output(payload)

        self.assertFalse(result.accepted)
        self.assertIn(MISSING_SOURCE_ATTRIBUTION, result.gate_reasons)

    def test_missing_invalid_conditions_rejects(self) -> None:
        payload = _valid_payload()
        payload.pop("invalid_conditions")

        result = gate_dsa_output(payload)

        self.assertFalse(result.accepted)
        self.assertIn(MISSING_INVALID_CONDITIONS, result.gate_reasons)

    def test_free_text_invalidation_rejects(self) -> None:
        payload = _valid_payload()
        payload["invalid_conditions"] = ["跌破 1180 且无法收回"]

        result = gate_dsa_output(payload)

        self.assertFalse(result.accepted)
        self.assertIn(MISSING_INVALID_CONDITIONS, result.gate_reasons)

    def test_missing_base_bull_bear_rejects(self) -> None:
        payload = _valid_payload()
        payload["scenarios"] = {"base": {"summary": "only one scenario"}}

        result = gate_dsa_output(payload)

        self.assertFalse(result.accepted)
        self.assertIn(MISSING_SCENARIOS, result.gate_reasons)

    def test_degrade_mode_keeps_signal_but_lowers_confidence(self) -> None:
        payload = _valid_payload()
        payload.pop("invalid_conditions")

        result = gate_dsa_output(payload, mode="degrade", confidence_penalty=0.2)

        self.assertTrue(result.accepted)
        self.assertEqual(result.action, "degrade")
        self.assertEqual(result.confidence_before, 0.72)
        self.assertEqual(result.confidence_after, 0.52)
        self.assertEqual(result.signal["confidence"], 0.52)
        self.assertIn(MISSING_INVALID_CONDITIONS, result.signal["guardrail"]["reasons"])


if __name__ == "__main__":
    unittest.main()
