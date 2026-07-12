import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from us_dsa_preflight import ProbeResult, select_routes  # noqa: E402


def probes(**oks):
    return {name: ProbeResult(name, ok, 10) for name, ok in oks.items()}


class CnRouteSelectionTests(unittest.TestCase):
    def test_all_ok_selects_gemini(self):
        decision = select_routes(
            probes(gemini=True, deepseek=True, tavily=True, bocha=True), region="cn"
        )
        self.assertEqual(decision.status, "ok")
        self.assertEqual(decision.llm, "gemini")
        self.assertEqual(decision.news, "bocha")
        self.assertEqual(decision.market_data, "domestic")

    def test_gemini_down_degrades_to_deepseek(self):
        decision = select_routes(
            probes(gemini=False, deepseek=True, tavily=True, bocha=True), region="cn"
        )
        self.assertEqual(decision.status, "degraded")
        self.assertEqual(decision.llm, "deepseek")
        self.assertIn("gemini_unavailable", decision.reasons)

    def test_no_llm_blocks(self):
        decision = select_routes(
            probes(gemini=False, deepseek=False, tavily=True, bocha=True), region="cn"
        )
        self.assertEqual(decision.status, "blocked")
        self.assertIsNone(decision.llm)
        self.assertIn("no_usable_llm", decision.reasons)

    def test_news_outage_never_blocks_cn(self):
        decision = select_routes(
            probes(gemini=True, deepseek=True, tavily=False, bocha=False), region="cn"
        )
        self.assertEqual(decision.status, "degraded")
        self.assertIsNone(decision.news)
        self.assertIn("no_usable_news", decision.reasons)

    def test_cn_bocha_preferred_over_tavily(self):
        decision = select_routes(
            probes(gemini=True, deepseek=True, tavily=True, bocha=False), region="cn"
        )
        self.assertEqual(decision.news, "tavily")
        decision = select_routes(
            probes(gemini=True, deepseek=True, tavily=True, bocha=True), region="cn"
        )
        self.assertEqual(decision.news, "bocha")

    def test_cn_ignores_missing_market_probes(self):
        decision = select_routes(probes(gemini=True, deepseek=True, tavily=True, bocha=True), region="cn")
        self.assertNotIn("yahoo_unavailable", decision.reasons)
        self.assertNotIn("no_usable_market_data", decision.reasons)


class UsRouteSelectionTests(unittest.TestCase):
    def test_us_default_region_behavior_unchanged(self):
        decision = select_routes(
            probes(gemini=False, deepseek=True, yahoo=True, nasdaq=True, tavily=True, bocha=True)
        )
        self.assertEqual(decision.status, "degraded")
        self.assertEqual(decision.llm, "deepseek")
        self.assertEqual(decision.market_data, "yahoo")

    def test_us_blocks_without_market_data(self):
        decision = select_routes(
            probes(gemini=True, deepseek=True, yahoo=False, nasdaq=False, tavily=True, bocha=True),
            region="us",
        )
        self.assertEqual(decision.status, "blocked")
        self.assertIn("no_usable_market_data", decision.reasons)


if __name__ == "__main__":
    unittest.main()
