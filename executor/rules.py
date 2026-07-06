from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from executor.models import DailyBar


def _to_cents(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def limit_rate(stock_code: str, is_st: bool = False) -> float:
    if is_st:
        return 0.05
    if str(stock_code).startswith("300"):
        return 0.20
    return 0.10


def limit_price(previous_close: float, direction: str, stock_code: str, is_st: bool = False) -> float:
    rate = limit_rate(stock_code, is_st=is_st)
    if direction == "up":
        return _to_cents(previous_close * (1 + rate))
    if direction == "down":
        return _to_cents(previous_close * (1 - rate))
    raise ValueError("direction must be up or down")


def is_limit_up_open(open_price: Optional[float], previous_close: Optional[float], stock_code: str, is_st: bool = False) -> bool:
    if open_price is None or previous_close is None:
        return False
    return _to_cents(open_price) >= limit_price(previous_close, "up", stock_code, is_st=is_st)


def is_limit_down_open(open_price: Optional[float], previous_close: Optional[float], stock_code: str, is_st: bool = False) -> bool:
    if open_price is None or previous_close is None:
        return False
    return _to_cents(open_price) <= limit_price(previous_close, "down", stock_code, is_st=is_st)


def round_lot_shares(cash: float, price: float, lot_size: int = 100) -> int:
    if cash <= 0 or price <= 0:
        return 0
    raw_shares = int(cash // price)
    return raw_shares - (raw_shares % lot_size)


def cap_order_shares(
    *,
    target_cash: float,
    price: float,
    current_symbol_market_value: float,
    portfolio_value: float,
    cap_rate: float,
    lot_size: int = 100,
) -> int:
    remaining_cap = max(0.0, portfolio_value * cap_rate - current_symbol_market_value)
    capped_cash = min(target_cash, remaining_cap)
    return round_lot_shares(capped_cash, price, lot_size=lot_size)


@dataclass(frozen=True)
class T1Position:
    quantity: int = 0
    old_quantity: int = 0

    @property
    def closable(self) -> int:
        return min(self.quantity, self.old_quantity)

    def buy(self, shares: int) -> "T1Position":
        if shares <= 0:
            raise ValueError("shares must be positive")
        object.__setattr__(self, "quantity", self.quantity + shares)
        return self

    def sell(self, shares: int) -> "T1Position":
        if shares <= 0:
            raise ValueError("shares must be positive")
        if shares > self.closable:
            raise ValueError("T+1 violation: shares exceed old/closable quantity")
        object.__setattr__(self, "quantity", self.quantity - shares)
        object.__setattr__(self, "old_quantity", self.old_quantity - shares)
        return self

    def settle(self) -> "T1Position":
        object.__setattr__(self, "old_quantity", self.quantity)
        return self


@dataclass(frozen=True)
class ExitTrigger:
    reason: str
    price: Optional[float]


def first_exit_trigger(bar: DailyBar, stop_loss: Optional[float], take_profit: Optional[float]) -> ExitTrigger:
    stop_hit = stop_loss is not None and bar.low is not None and bar.low <= stop_loss
    take_hit = take_profit is not None and bar.high is not None and bar.high >= take_profit
    if stop_hit:
        return ExitTrigger("stop_loss", stop_loss)
    if take_hit:
        return ExitTrigger("take_profit", take_profit)
    return ExitTrigger("none", None)


@dataclass(frozen=True)
class PendingExit:
    reason: str
    triggered_date: date
    stop_price: float
    sellable_today: bool = False


def same_day_stop_pending(fill_date: date, bar: DailyBar, stop_loss: Optional[float]) -> Optional[PendingExit]:
    if stop_loss is None or bar.low is None or bar.date != fill_date:
        return None
    if bar.low <= stop_loss:
        return PendingExit("t1_stop_loss_pending", fill_date, stop_loss, sellable_today=False)
    return None
