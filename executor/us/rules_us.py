from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from executor.us.models_us import DailyBar


def round_lot_shares(cash: float, price: float, lot_size: int = 1) -> int:
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
    lot_size: int = 1,
) -> int:
    remaining_cap = max(0.0, portfolio_value * cap_rate - current_symbol_market_value)
    capped_cash = min(target_cash, remaining_cap)
    return round_lot_shares(capped_cash, price, lot_size=lot_size)


@dataclass(frozen=True)
class T0Position:
    quantity: int = 0

    @property
    def closable(self) -> int:
        return self.quantity

    def buy(self, shares: int) -> "T0Position":
        if shares <= 0:
            raise ValueError("shares must be positive")
        object.__setattr__(self, "quantity", self.quantity + shares)
        return self

    def sell(self, shares: int) -> "T0Position":
        if shares <= 0:
            raise ValueError("shares must be positive")
        if shares > self.quantity:
            raise ValueError("shares exceed position quantity")
        object.__setattr__(self, "quantity", self.quantity - shares)
        return self

    def settle(self) -> "T0Position":
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
