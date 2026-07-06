import unittest
from datetime import date
from pathlib import Path

from executor.signal_reader import SignalReader, advice_to_action


DSA_DB = Path(__file__).resolve().parents[2] / "runtime_data" / "dsa" / "stock_analysis.db"


class SignalReaderTests(unittest.TestCase):
    def test_advice_to_action_normalizes_hold(self) -> None:
        self.assertEqual(advice_to_action("持有"), "hold")
        self.assertEqual(advice_to_action("持有观察"), "hold")
        self.assertEqual(advice_to_action("观望"), "watch")
        self.assertEqual(advice_to_action("卖出"), "sell")
        self.assertEqual(advice_to_action("减仓"), "reduce")
        self.assertEqual(advice_to_action("避免"), "avoid")

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


if __name__ == "__main__":
    unittest.main()
