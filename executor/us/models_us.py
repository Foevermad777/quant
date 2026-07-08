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
    market: str = "us"
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


class NextOpenFillModel:
    """Fill entry orders at the next available open without entry_high gating."""

    def buy_fill(self, signal: DecisionSignal, bar: Optional[DailyBar]) -> FillResult:
        if bar is None:
            return FillResult(False, "unfilled", "suspended")
        if bar.open is None or bar.open <= 0:
            return FillResult(False, "unfilled", "missing_open", trade_date=bar.date)
        return FillResult(True, "filled", "next_day_open", price=float(bar.open), trade_date=bar.date)

    def expired_unfilled(self, signal: DecisionSignal, current_date: date) -> FillResult:
        if signal.expires_at is not None and current_date > signal.expires_at.date():
            return FillResult(False, "blocked", "open_unavailable_expired", trade_date=current_date)
        return FillResult(False, "unfilled", "not_expired", trade_date=current_date)


@dataclass(frozen=True)
class SlippageModel:
    rate: float = 0.001

    def execution_price(self, fill_price: float, side: str, *, multiplier: float = 1.0) -> float:
        effective_rate = self.rate * multiplier
        if side == "buy":
            return round(fill_price * (1 + effective_rate), 6)
        if side == "sell":
            return round(fill_price * (1 - effective_rate), 6)
        raise ValueError(f"unsupported side: {side}")


@dataclass(frozen=True)
class UsFeeModel:
    commission_per_share: float = 0.005
    commission_rate: float = 0.0
    min_commission: float = 1.0
    sec_fee_rate: float = 27.80 / 1_000_000

    def commission(self, amount: float, *, shares: int = 0) -> float:
        per_share_fee = max(0, abs(int(shares))) * max(0.0, self.commission_per_share)
        notional_fee = max(0.0, amount) * max(0.0, self.commission_rate)
        raw_fee = per_share_fee + notional_fee
        if raw_fee <= 0:
            return 0.0
        return round(max(self.min_commission, raw_fee), 2)

    def sec_fee(self, amount: float, side: str) -> float:
        if side == "sell":
            return round(amount * self.sec_fee_rate, 2)
        return 0.0

    def total_costs(self, amount: float, side: str, *, shares: int = 0) -> tuple[float, float]:
        commission = self.commission(amount, shares=shares)
        regulatory_fee = self.sec_fee(amount, side)
        return commission, regulatory_fee
