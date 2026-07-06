import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from executor.ledger import PaperLedger, TradeFill


class LedgerTests(unittest.TestCase):
    def test_trade_idempotency_by_signal_date_side(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = PaperLedger(Path(tmpdir) / "paper.db")
            ledger.initialize()
            fill = TradeFill(
                signal_id=42,
                stock_code="600519",
                side="buy",
                trade_date=date(2026, 7, 6),
                shares=1000,
                fill_price=10.0,
                exec_price=10.01,
                gross_amount=10010.0,
                fees=5.0,
                taxes=0.0,
                cash_delta=-10015.0,
                reason="open_within_limit",
                created_at=datetime(2026, 7, 6, 18, 40, 0),
            )

            first = ledger.record_trade(fill)
            second = ledger.record_trade(fill)

            self.assertTrue(first)
            self.assertFalse(second)
            self.assertEqual(ledger.trade_count(), 1)

    def test_none_signal_trade_uses_sentinel_for_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = PaperLedger(Path(tmpdir) / "paper.db")
            ledger.initialize()
            fill = TradeFill(
                signal_id=None,
                stock_code="600519",
                side="buy",
                trade_date=date(2026, 7, 6),
                shares=1000,
                fill_price=10.0,
                exec_price=10.01,
                gross_amount=10010.0,
                fees=5.0,
                taxes=0.0,
                cash_delta=-10015.0,
                reason="manual_seed",
                created_at=datetime(2026, 7, 6, 18, 40, 0),
            )

            first = ledger.apply_trade(fill)
            second = ledger.apply_trade(fill)

            self.assertTrue(first)
            self.assertFalse(second)
            self.assertEqual(ledger.trade_count(), 1)


if __name__ == "__main__":
    unittest.main()
