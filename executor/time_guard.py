from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Optional

A_SHARE_BAR_AVAILABLE_TIME = time(15, 0, 0)


@dataclass(frozen=True)
class NewsTimingAudit:
    signal_id: int
    stock_code: str
    horizon: str
    anchor_date: date
    decision_timestamp: Optional[datetime]
    bar_available_at: datetime
    news_title: str
    news_published_at: Optional[datetime]
    news_source: Optional[str]
    attribution_status: str
    reason: str


def bar_available_at(anchor_date: date, available_time: time = A_SHARE_BAR_AVAILABLE_TIME) -> datetime:
    return datetime.combine(anchor_date, available_time)


def classify_news_for_attribution(
    *,
    published_at: Optional[datetime],
    decision_timestamp: Optional[datetime],
    anchor_date: date,
) -> tuple[str, str]:
    if published_at is None:
        return "unknown_published_time", "missing_published_time"

    available_at = bar_available_at(anchor_date)
    if published_at > available_at:
        return "excluded_after_bar_available", "published_after_predicted_bar_available"
    if decision_timestamp is not None and published_at > decision_timestamp:
        return "not_available_at_decision", "published_after_decision_timestamp"
    return "eligible", "published_before_decision_and_bar_available"
