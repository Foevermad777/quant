from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class DailyBar:
    code: str
    date: date
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]
    volume: Optional[float] = None
    amount: Optional[float] = None
    pct_chg: Optional[float] = None


@dataclass(frozen=True)
class DecisionSignal:
    id: int
    stock_code: str
    stock_name: Optional[str]
    action: str
    confidence: Optional[float]
    entry_high: Optional[float]
    entry_low: Optional[float]
    stop_loss: Optional[float]
    target_price: Optional[float]
    status: str
    created_at: Optional[datetime]
    expires_at: Optional[datetime]
    source_report_id: Optional[int]
    metadata: Dict[str, Any] = field(default_factory=dict)
    market: str = "cn"
    source_type: str = "analysis"
    source_agent: Optional[str] = None
    plan_quality: Optional[str] = None


@dataclass(frozen=True)
class FillResult:
    filled: bool
    status: str
    reason: str
    price: Optional[float] = None
    trade_date: Optional[date] = None


class LimitFillModel:
    """Daily-bar approximation of an A-share limit buy order.

    Limit price is the signal's entry_high. If the open is already at or below
    the limit, the order fills at the open. If the open is above the limit but
    the day's low touches the limit, it fills at the limit. Otherwise it is
    carried to the next execution date until expiry.
    """

    def buy_fill(self, signal: DecisionSignal, bar: Optional[DailyBar]) -> FillResult:
        if bar is None:
            return FillResult(False, "unfilled", "suspended")
        if signal.entry_high is None or signal.entry_high <= 0:
            return FillResult(False, "unfilled", "missing_entry_high", trade_date=bar.date)
        if bar.open is None or bar.low is None:
            return FillResult(False, "unfilled", "incomplete_bar", trade_date=bar.date)

        limit_price = float(signal.entry_high)
        if bar.open <= limit_price:
            return FillResult(True, "filled", "open_within_limit", price=float(bar.open), trade_date=bar.date)
        if bar.low <= limit_price:
            return FillResult(True, "filled", "intraday_limit_touch", price=limit_price, trade_date=bar.date)
        return FillResult(False, "unfilled", "limit_not_touched", trade_date=bar.date)

    def expired_unfilled(self, signal: DecisionSignal, current_date: date) -> FillResult:
        if signal.expires_at is not None and current_date > signal.expires_at.date():
            return FillResult(False, "blocked", "discipline_blocked_chase", trade_date=current_date)
        return FillResult(False, "unfilled", "not_expired", trade_date=current_date)


@dataclass(frozen=True)
class SlippageModel:
    rate: float = 0.001

    def execution_price(self, fill_price: float, side: str) -> float:
        if side == "buy":
            return round(fill_price * (1 + self.rate), 6)
        if side == "sell":
            return round(fill_price * (1 - self.rate), 6)
        raise ValueError(f"unsupported side: {side}")


@dataclass(frozen=True)
class FeeModel:
    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.0005

    def commission(self, amount: float) -> float:
        return round(max(self.min_commission, amount * self.commission_rate), 2)

    def stamp_tax(self, amount: float, side: str) -> float:
        if side == "sell":
            return round(amount * self.stamp_tax_rate, 2)
        return 0.0

    def total_costs(self, amount: float, side: str) -> tuple[float, float]:
        commission = self.commission(amount)
        tax = self.stamp_tax(amount, side)
        return commission, tax
