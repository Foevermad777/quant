import unittest
from datetime import date
from pathlib import Path

from executor.oos_builder import OosBuildSummary, OosCodeSummary


class OosBuilderTests(unittest.TestCase):
    def test_bars_ready_requires_coverage_through_r1_oos_end(self) -> None:
        common = {
            "db_path": Path("/tmp/oos.db"),
            "source_db_path": Path("/tmp/source.db"),
            "start": date(2024, 1, 1),
            "end": date(2025, 3, 13),
            "generated_at": "2026-07-08 00:00:00",
        }
        short_summary = OosBuildSummary(
            **common,
            codes=(
                OosCodeSummary(
                    code="600519",
                    name="贵州茅台",
                    min_bar_date="2024-01-02",
                    max_bar_date="2025-03-13",
                    news_count=1,
                ),
            ),
        )
        complete_summary = OosBuildSummary(
            **common,
            codes=(
                OosCodeSummary(
                    code="600519",
                    name="贵州茅台",
                    min_bar_date="2024-01-02",
                    max_bar_date="2025-12-31",
                    news_count=1,
                ),
            ),
        )

        self.assertFalse(short_summary.bars_ready)
        self.assertTrue(complete_summary.bars_ready)


if __name__ == "__main__":
    unittest.main()
