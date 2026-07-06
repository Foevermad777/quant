from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from executor.models import DailyBar, DecisionSignal


@dataclass(frozen=True)
class AnalysisAdvice:
    report_id: int
    stock_code: str
    operation_advice: Optional[str]
    action: str
    created_at: Optional[datetime]


def parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def parse_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _loads_json(text: Any) -> Dict[str, Any]:
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def advice_to_action(advice: Optional[str]) -> str:
    text = (advice or "").strip().lower()
    if not text:
        return "unknown"
    if any(token in text for token in ("卖出", "清仓", "strong sell", "sell")):
        return "sell"
    if any(token in text for token in ("减仓", "reduce")):
        return "reduce"
    if any(token in text for token in ("避免", "回避", "avoid")):
        return "avoid"
    if any(token in text for token in ("加仓", "add")):
        return "add"
    if any(token in text for token in ("买入", "建仓", "buy")):
        return "buy"
    if any(token in text for token in ("持有", "hold")):
        return "hold"
    if any(token in text for token in ("观望", "等待", "watch", "wait")):
        return "watch"
    return "unknown"


def _action_group(action: str) -> str:
    normalized = (action or "").strip().lower()
    if normalized in {"buy", "add"}:
        return "entry"
    if normalized in {"sell", "reduce", "avoid"}:
        return "exit"
    if normalized in {"hold", "watch", "alert"}:
        return "neutral"
    return "unknown"


class SignalReader:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        uri = f"file:{self.db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def analysis_count_on(self, day: date) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "select count(*) as count from analysis_history where date(created_at) = ?",
                (day.isoformat(),),
            ).fetchone()
        return int(row["count"] if row else 0)

    def get_signal(self, signal_id: int) -> DecisionSignal:
        with self._connect() as conn:
            row = conn.execute("select * from decision_signals where id = ?", (signal_id,)).fetchone()
        if row is None:
            raise KeyError(f"signal not found: {signal_id}")
        return self._row_to_signal(row)

    def active_signals_before(self, execution_date: date) -> List[DecisionSignal]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select *
                from decision_signals
                where status = 'active'
                  and date(created_at) < ?
                order by datetime(created_at), id
                """,
                (execution_date.isoformat(),),
            ).fetchall()
        return [self._row_to_signal(row) for row in rows]

    def open_candidates(self, execution_date: date) -> List[DecisionSignal]:
        signals = [
            signal
            for signal in self.active_signals_before(execution_date)
            if signal.action in {"buy", "add"}
        ]
        consistent = []
        for signal in signals:
            advice = self.advice_for_signal(signal)
            if self.is_s1_consistent(signal, advice):
                consistent.append(signal)
        return self._latest_by_symbol(consistent)

    def s1_conflicts(self, execution_date: date) -> List[tuple[DecisionSignal, AnalysisAdvice]]:
        conflicts: List[tuple[DecisionSignal, AnalysisAdvice]] = []
        for signal in self.active_signals_before(execution_date):
            advice = self.advice_for_signal(signal)
            if not self.is_s1_consistent(signal, advice):
                conflicts.append((signal, advice))
        return conflicts

    def advice_for_signal(self, signal: DecisionSignal) -> AnalysisAdvice:
        with self._connect() as conn:
            row = None
            if signal.source_report_id is not None:
                row = conn.execute(
                    "select id, code, operation_advice, created_at from analysis_history where id = ?",
                    (signal.source_report_id,),
                ).fetchone()
            if row is None:
                row = conn.execute(
                    """
                    select id, code, operation_advice, created_at
                    from analysis_history
                    where code = ?
                      and datetime(created_at) <= datetime(?)
                    order by datetime(created_at) desc, id desc
                    limit 1
                    """,
                    (
                        signal.stock_code,
                        signal.created_at.isoformat(sep=" ") if signal.created_at else "9999-12-31",
                    ),
                ).fetchone()
        if row is None:
            return AnalysisAdvice(0, signal.stock_code, None, "unknown", None)
        operation_advice = row["operation_advice"]
        return AnalysisAdvice(
            report_id=int(row["id"]),
            stock_code=row["code"],
            operation_advice=operation_advice,
            action=advice_to_action(operation_advice),
            created_at=parse_datetime(row["created_at"]),
        )

    def is_s1_consistent(self, signal: DecisionSignal, advice: AnalysisAdvice) -> bool:
        signal_group = _action_group(signal.action)
        advice_group = _action_group(advice.action)
        if signal_group == "unknown" or advice_group == "unknown":
            return False
        return signal_group == advice_group

    def bar(self, stock_code: str, day: date) -> Optional[DailyBar]:
        with self._connect() as conn:
            row = conn.execute(
                """
                select code, date, open, high, low, close, volume, amount, pct_chg
                from stock_daily
                where code = ? and date = ?
                """,
                (stock_code, day.isoformat()),
            ).fetchone()
        return self._row_to_bar(row) if row is not None else None

    def previous_bar(self, stock_code: str, day: date) -> Optional[DailyBar]:
        with self._connect() as conn:
            row = conn.execute(
                """
                select code, date, open, high, low, close, volume, amount, pct_chg
                from stock_daily
                where code = ? and date < ?
                order by date desc
                limit 1
                """,
                (stock_code, day.isoformat()),
            ).fetchone()
        return self._row_to_bar(row) if row is not None else None

    def bars_on(self, day: date) -> Dict[str, DailyBar]:
        with self._connect() as conn:
            rows = conn.execute(
                "select code, date, open, high, low, close, volume, amount, pct_chg from stock_daily where date = ?",
                (day.isoformat(),),
            ).fetchall()
        return {row["code"]: self._row_to_bar(row) for row in rows}

    def trading_dates(self, start: date, end: Optional[date] = None) -> List[date]:
        params: List[str] = [start.isoformat()]
        predicate = "date >= ?"
        if end is not None:
            predicate += " and date <= ?"
            params.append(end.isoformat())
        with self._connect() as conn:
            rows = conn.execute(
                f"select distinct date from stock_daily where {predicate} order by date",
                params,
            ).fetchall()
        return [parse_date(row["date"]) for row in rows if parse_date(row["date"]) is not None]

    def latest_trading_date(self) -> Optional[date]:
        with self._connect() as conn:
            row = conn.execute("select max(date) as max_date from stock_daily").fetchone()
        return parse_date(row["max_date"] if row else None)

    def outcomes(self, start: date, end: date) -> List[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select *
                from decision_signal_outcomes
                where date(coalesce(updated_at, created_at)) between ? and ?
                order by updated_at, id
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        return rows

    def signals_between(self, start: date, end: date) -> List[DecisionSignal]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select *
                from decision_signals
                where date(created_at) between ? and ?
                order by datetime(created_at), id
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        return [self._row_to_signal(row) for row in rows]

    @staticmethod
    def _latest_by_symbol(signals: Iterable[DecisionSignal]) -> List[DecisionSignal]:
        latest: Dict[str, DecisionSignal] = {}
        for signal in signals:
            current = latest.get(signal.stock_code)
            if current is None:
                latest[signal.stock_code] = signal
                continue
            current_key = (current.created_at or datetime.min, current.id)
            signal_key = (signal.created_at or datetime.min, signal.id)
            if signal_key > current_key:
                latest[signal.stock_code] = signal
        return sorted(latest.values(), key=lambda item: (item.created_at or datetime.min, item.id))

    @staticmethod
    def _row_to_signal(row: sqlite3.Row) -> DecisionSignal:
        return DecisionSignal(
            id=int(row["id"]),
            stock_code=row["stock_code"],
            stock_name=row["stock_name"],
            action=(row["action"] or "").strip().lower(),
            confidence=row["confidence"],
            entry_high=row["entry_high"],
            entry_low=row["entry_low"],
            stop_loss=row["stop_loss"],
            target_price=row["target_price"],
            status=row["status"],
            created_at=parse_datetime(row["created_at"]),
            expires_at=parse_datetime(row["expires_at"]),
            source_report_id=row["source_report_id"],
            metadata=_loads_json(row["metadata_json"]),
            market=row["market"] or "cn",
            source_type=row["source_type"] or "analysis",
            source_agent=row["source_agent"],
            plan_quality=row["plan_quality"],
        )

    @staticmethod
    def _row_to_bar(row: sqlite3.Row) -> DailyBar:
        parsed_date = parse_date(row["date"])
        if parsed_date is None:
            raise ValueError(f"invalid stock_daily date: {row['date']}")
        return DailyBar(
            code=row["code"],
            date=parsed_date,
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row["volume"],
            amount=row["amount"],
            pct_chg=row["pct_chg"],
        )
