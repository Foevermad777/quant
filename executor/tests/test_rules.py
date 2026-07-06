import unittest
from datetime import date

from executor.models import DailyBar
from executor.rules import (
    T1Position,
    cap_order_shares,
    first_exit_trigger,
    is_limit_down_open,
    is_limit_up_open,
    limit_price,
    round_lot_shares,
    same_day_stop_pending,
)


class RulesTests(unittest.TestCase):
    def test_t1_today_buy_is_not_closable_until_settlement(self) -> None:
        position = T1Position()

        position.buy(1000)

        self.assertEqual(position.quantity, 1000)
        self.assertEqual(position.old_quantity, 0)
        self.assertEqual(position.closable, 0)
        with self.assertRaises(ValueError):
            position.sell(100)

        position.settle()
        self.assertEqual(position.closable, 1000)

        position.sell(400)
        self.assertEqual(position.quantity, 600)
        self.assertEqual(position.old_quantity, 600)
        self.assertEqual(position.closable, 600)

    def test_limit_prices_and_open_blocks(self) -> None:
        self.assertEqual(limit_price(10.0, "up", "600519"), 11.0)
        self.assertEqual(limit_price(10.0, "down", "600519"), 9.0)
        self.assertEqual(limit_price(10.0, "up", "300750"), 12.0)
        self.assertEqual(limit_price(10.0, "down", "300750"), 8.0)
        self.assertEqual(limit_price(10.0, "up", "600000", is_st=True), 10.5)
        self.assertEqual(limit_price(10.0, "down", "600000", is_st=True), 9.5)

        self.assertTrue(is_limit_up_open(11.0, 10.0, "600519"))
        self.assertFalse(is_limit_up_open(10.99, 10.0, "600519"))
        self.assertTrue(is_limit_down_open(9.0, 10.0, "600519"))
        self.assertFalse(is_limit_down_open(9.01, 10.0, "600519"))

    def test_stop_loss_wins_same_bar_ambiguity(self) -> None:
        bar = DailyBar(
            code="600519",
            date=date(2026, 7, 7),
            open=10.0,
            high=12.0,
            low=8.8,
            close=11.0,
        )

        trigger = first_exit_trigger(bar, stop_loss=9.5, take_profit=11.5)

        self.assertEqual(trigger.reason, "stop_loss")
        self.assertEqual(trigger.price, 9.5)

    def test_round_lot_and_single_symbol_cap(self) -> None:
        self.assertEqual(round_lot_shares(99999.0, 10.0), 9900)
        self.assertEqual(round_lot_shares(100099.0, 10.0), 10000)

        capped = cap_order_shares(
            target_cash=100000.0,
            price=10.0,
            current_symbol_market_value=150000.0,
            portfolio_value=1000000.0,
            cap_rate=0.20,
        )

        self.assertEqual(capped, 5000)

    def test_same_day_stop_creates_t1_pending_exit(self) -> None:
        bar = DailyBar(
            code="600519",
            date=date(2026, 7, 7),
            open=10.0,
            high=10.4,
            low=8.9,
            close=9.2,
        )

        pending = same_day_stop_pending(fill_date=bar.date, bar=bar, stop_loss=9.0)

        self.assertIsNotNone(pending)
        self.assertEqual(pending.reason, "t1_stop_loss_pending")
        self.assertFalse(pending.sellable_today)


if __name__ == "__main__":
    unittest.main()
