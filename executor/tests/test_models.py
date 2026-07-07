import unittest
from datetime import date, datetime

from executor.models import DailyBar, DecisionSignal, LimitFillModel, NextOpenFillModel, SlippageModel


def _signal() -> DecisionSignal:
    return DecisionSignal(
        id=1,
        stock_code="600519",
        stock_name="贵州茅台",
        action="buy",
        confidence=0.6,
        entry_high=10.0,
        entry_low=9.5,
        stop_loss=9.0,
        target_price=12.0,
        status="active",
        created_at=datetime(2026, 7, 5, 23, 0, 0),
        expires_at=datetime(2026, 7, 8, 15, 0, 0),
        source_report_id=1,
        metadata={},
    )


class NextOpenFillModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signal = _signal()
        self.model = NextOpenFillModel()

    def test_next_open_ignores_entry_high_and_fills_at_open(self) -> None:
        bar = DailyBar("600519", date(2026, 7, 6), open=10.8, high=11.0, low=10.6, close=10.9)

        fill = self.model.buy_fill(self.signal, bar)

        self.assertTrue(fill.filled)
        self.assertEqual(fill.price, 10.8)
        self.assertEqual(fill.reason, "next_day_open")

    def test_next_open_requires_available_open(self) -> None:
        bar = DailyBar("600519", date(2026, 7, 6), open=None, high=11.0, low=10.6, close=10.9)

        fill = self.model.buy_fill(self.signal, bar)

        self.assertFalse(fill.filled)
        self.assertEqual(fill.reason, "missing_open")

    def test_suspension_stays_unfilled(self) -> None:
        fill = self.model.buy_fill(self.signal, None)

        self.assertFalse(fill.filled)
        self.assertEqual(fill.reason, "suspended")

    def test_expired_unfilled_is_open_unavailable_block(self) -> None:
        blocked = self.model.expired_unfilled(self.signal, date(2026, 7, 9))

        self.assertFalse(blocked.filled)
        self.assertEqual(blocked.status, "blocked")
        self.assertEqual(blocked.reason, "open_unavailable_expired")


class SlippageModelTests(unittest.TestCase):
    def test_buy_slippage_can_use_open_multiplier(self) -> None:
        model = SlippageModel(0.001)

        self.assertEqual(model.execution_price(10.0, "buy", multiplier=2.0), 10.02)
        self.assertEqual(model.execution_price(10.0, "sell"), 9.99)


class LimitFillModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signal = _signal()
        self.model = LimitFillModel()

    def test_open_inside_limit_fills_at_open(self) -> None:
        bar = DailyBar("600519", date(2026, 7, 6), open=9.8, high=10.2, low=9.7, close=10.1)

        fill = self.model.buy_fill(self.signal, bar)

        self.assertTrue(fill.filled)
        self.assertEqual(fill.price, 9.8)
        self.assertEqual(fill.reason, "open_within_limit")

    def test_open_above_limit_but_intraday_touch_fills_at_entry_high(self) -> None:
        bar = DailyBar("600519", date(2026, 7, 6), open=10.5, high=10.8, low=9.95, close=10.2)

        fill = self.model.buy_fill(self.signal, bar)

        self.assertTrue(fill.filled)
        self.assertEqual(fill.price, 10.0)
        self.assertEqual(fill.reason, "intraday_limit_touch")

    def test_not_touched_stays_unfilled(self) -> None:
        bar = DailyBar("600519", date(2026, 7, 6), open=10.5, high=10.8, low=10.1, close=10.2)

        fill = self.model.buy_fill(self.signal, bar)

        self.assertFalse(fill.filled)
        self.assertEqual(fill.reason, "limit_not_touched")

    def test_suspension_stays_unfilled(self) -> None:
        fill = self.model.buy_fill(self.signal, None)

        self.assertFalse(fill.filled)
        self.assertEqual(fill.reason, "suspended")

    def test_expired_unfilled_is_discipline_block(self) -> None:
        blocked = self.model.expired_unfilled(self.signal, date(2026, 7, 9))

        self.assertFalse(blocked.filled)
        self.assertEqual(blocked.status, "blocked")
        self.assertEqual(blocked.reason, "discipline_blocked_chase")


if __name__ == "__main__":
    unittest.main()
