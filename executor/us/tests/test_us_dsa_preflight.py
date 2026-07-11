import unittest

from ops.us_dsa_preflight import ProbeResult, classify_http_failure, select_routes


def _probe(name: str, ok: bool, error_type: str | None = None) -> ProbeResult:
    return ProbeResult(
        name=name,
        ok=ok,
        latency_ms=10,
        error_type=error_type,
    )


class UsDsaPreflightTests(unittest.TestCase):
    def test_classifies_gemini_region_rejection_separately_from_quota(self) -> None:
        self.assertEqual(
            classify_http_failure(
                "gemini",
                400,
                '{"error":{"message":"User location is not supported for the API use."}}',
            ),
            "region_unsupported",
        )
        self.assertEqual(classify_http_failure("gemini", 429, "quota exceeded"), "quota")

    def test_selects_independent_fallback_routes_when_primaries_fail(self) -> None:
        decision = select_routes(
            {
                "gemini": _probe("gemini", False, "region_unsupported"),
                "deepseek": _probe("deepseek", True),
                "yahoo": _probe("yahoo", False, "transport"),
                "nasdaq": _probe("nasdaq", True),
                "tavily": _probe("tavily", False, "timeout"),
                "bocha": _probe("bocha", True),
            }
        )

        self.assertEqual(decision.status, "degraded")
        self.assertEqual(decision.llm, "deepseek")
        self.assertEqual(decision.market_data, "nasdaq")
        self.assertEqual(decision.news, "bocha")
        self.assertEqual(
            set(decision.reasons),
            {"gemini_unavailable", "yahoo_unavailable", "tavily_unavailable"},
        )

    def test_blocks_when_no_llm_route_is_usable(self) -> None:
        decision = select_routes(
            {
                "gemini": _probe("gemini", False, "transport"),
                "deepseek": _probe("deepseek", False, "transport"),
                "yahoo": _probe("yahoo", True),
                "nasdaq": _probe("nasdaq", True),
                "tavily": _probe("tavily", True),
                "bocha": _probe("bocha", True),
            }
        )

        self.assertEqual(decision.status, "blocked")
        self.assertIsNone(decision.llm)
        self.assertIn("no_usable_llm", decision.reasons)

    def test_blocks_when_no_market_data_route_is_usable(self) -> None:
        decision = select_routes(
            {
                "gemini": _probe("gemini", True),
                "deepseek": _probe("deepseek", True),
                "yahoo": _probe("yahoo", False, "timeout"),
                "nasdaq": _probe("nasdaq", False, "timeout"),
                "tavily": _probe("tavily", True),
                "bocha": _probe("bocha", True),
            }
        )

        self.assertEqual(decision.status, "blocked")
        self.assertIsNone(decision.market_data)
        self.assertIn("no_usable_market_data", decision.reasons)

    def test_marks_missing_news_routes_degraded_without_blocking_analysis(self) -> None:
        decision = select_routes(
            {
                "gemini": _probe("gemini", True),
                "deepseek": _probe("deepseek", True),
                "yahoo": _probe("yahoo", True),
                "nasdaq": _probe("nasdaq", True),
                "tavily": _probe("tavily", False, "timeout"),
                "bocha": _probe("bocha", False, "timeout"),
            }
        )

        self.assertEqual(decision.status, "degraded")
        self.assertIsNone(decision.news)
        self.assertIn("no_usable_news", decision.reasons)


if __name__ == "__main__":
    unittest.main()
