from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence
from zoneinfo import ZoneInfo

from executor.intent_resolution import (
    action_group as _action_group,
    advice_to_action,
    resolve_intent,
)
from executor.us.config_us import US_MARKET, US_STOCK_POOL
from executor.us.models_us import DailyBar, DecisionSignal


@dataclass(frozen=True)
class AnalysisAdvice:
    report_id: int
    stock_code: str
    operation_advice: Optional[str]
    action: str
    created_at: Optional[datetime]
    flat_account_action: str = "unknown"
    holding_action: str = "unknown"
    resolved_action: str = "unknown"
    conflict_status: str = "hard_conflict"
    conflict_reason: str = ""
    intent_source: str = "legacy_operation_advice"


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


US_MARKET_TZ = ZoneInfo("America/New_York")
LOCAL_RUNTIME_TZ = ZoneInfo("Asia/Shanghai")
US_REGULAR_OPEN_TIME = time(9, 30, 0)
TEMPORAL_COLUMNS = ("decision_timestamp", "market_phase", "data_asof", "bar_cutoff", "news_cutoff")


class UsSignalReader:
    def __init__(
        self,
        db_path: Path,
        disciplined_db_path: Optional[Path] = None,
        *,
        stock_pool: Sequence[str] = US_STOCK_POOL,
        market: str = US_MARKET,
        use_disciplined_signals: bool = True,
    ) -> None:
        self.db_path = Path(db_path)
        self.disciplined_db_path = Path(disciplined_db_path) if disciplined_db_path is not None else None
        self.stock_pool = tuple(dict.fromkeys(stock_pool))
        if not self.stock_pool:
            raise ValueError("US stock_pool must not be empty")
        self.market = market
        self.use_disciplined_signals = use_disciplined_signals

    def _connect(self) -> sqlite3.Connection:
        uri = f"file:{self.db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def _connect_disciplined(self) -> sqlite3.Connection:
        if self.disciplined_db_path is None:
            raise FileNotFoundError("disciplined signal store is not configured")
        uri = f"file:{self.disciplined_db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def _pool_placeholders(self) -> str:
        return ",".join("?" for _ in self.stock_pool)

    def _market_pool_params(self, *prefix: str) -> tuple[str, ...]:
        return (*prefix, self.market, *self.stock_pool)

    def has_disciplined_signal_store(self) -> bool:
        if not self.use_disciplined_signals or self.disciplined_db_path is None or not self.disciplined_db_path.exists():
            return False
        try:
            with self._connect_disciplined() as conn:
                row = conn.execute(
                    "select 1 from sqlite_master where type = 'table' and name = 'disciplined_signals'"
                ).fetchone()
        except sqlite3.Error:
            return False
        return row is not None

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
        placeholders = self._pool_placeholders()
        if self.has_disciplined_signal_store():
            with self._connect_disciplined() as conn:
                columns = {row["name"] for row in conn.execute("pragma table_info(disciplined_signals)").fetchall()}
                rows = conn.execute(
                    f"""
                    select *
                    from disciplined_signals
                    where status = 'active'
                      and gate_accepted = 1
                      and market = ?
                      and stock_code in ({placeholders})
                    order by datetime(created_at), source_signal_id
                    """,
                    self._market_pool_params(),
                ).fetchall()
            return [
                self._row_to_disciplined_signal(row)
                for row in rows
                if _disciplined_row_available_before(row, execution_date, columns)
            ]

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                select *
                from decision_signals
                where status = 'active'
                  and date(created_at) < ?
                  and market = ?
                  and stock_code in ({placeholders})
                order by datetime(created_at), id
                """,
                self._market_pool_params(execution_date.isoformat()),
            ).fetchall()
        return [self._row_to_signal(row) for row in rows]

    def open_candidates(self, execution_date: date, held_symbols: Optional[Iterable[str]] = None) -> List[DecisionSignal]:
        held = set(held_symbols or ())
        candidates = []
        for signal in self.active_signals_before(execution_date):
            advice = self.advice_for_signal(signal, has_position=signal.stock_code in held)
            if _action_group(advice.action) == "entry" and self.is_s1_consistent(signal, advice):
                candidates.append(self._with_execution_action(signal, advice))
            elif signal.stock_code not in held and self.is_conditional_entry_plan(signal, advice):
                candidates.append(self._as_conditional_limit_plan(signal, advice))
        return self._latest_by_symbol(candidates)

    def exit_candidates(self, execution_date: date, held_symbols: Optional[Iterable[str]] = None) -> List[DecisionSignal]:
        held = set(held_symbols or ())
        consistent = []
        for signal in self.active_signals_before(execution_date):
            advice = self.advice_for_signal(signal, has_position=signal.stock_code in held)
            if _action_group(advice.action) == "exit" and self.is_s1_consistent(signal, advice):
                consistent.append(self._with_execution_action(signal, advice))
        return self._latest_by_symbol(consistent)

    def s1_conflicts(
        self,
        execution_date: date,
        held_symbols: Optional[Iterable[str]] = None,
    ) -> List[tuple[DecisionSignal, AnalysisAdvice]]:
        held = set(held_symbols or ())
        conflicts: List[tuple[DecisionSignal, AnalysisAdvice]] = []
        for signal in self.active_signals_before(execution_date):
            advice = self.advice_for_signal(signal, has_position=signal.stock_code in held)
            if self.is_s1_consistent(signal, advice):
                continue
            if signal.stock_code not in held and self.is_conditional_entry_plan(signal, advice):
                continue
            conflicts.append((signal, advice))
        return conflicts

    def advice_for_signal(self, signal: DecisionSignal, *, has_position: bool = False) -> AnalysisAdvice:
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
            embedded = signal.metadata.get("dsa_analysis")
            if isinstance(embedded, dict):
                operation_advice = embedded.get("operation_advice")
                return self._resolved_advice(
                    signal=signal,
                    report_id=int(embedded.get("id") or signal.source_report_id or 0),
                    stock_code=str(embedded.get("code") or signal.stock_code),
                    operation_advice=str(operation_advice or ""),
                    created_at=parse_datetime(embedded.get("created_at")),
                    has_position=has_position,
                )
            return self._resolved_advice(
                signal=signal,
                report_id=0,
                stock_code=signal.stock_code,
                operation_advice=None,
                created_at=None,
                has_position=has_position,
            )
        operation_advice = row["operation_advice"]
        return self._resolved_advice(
            signal=signal,
            report_id=int(row["id"]),
            stock_code=row["code"],
            operation_advice=operation_advice,
            created_at=parse_datetime(row["created_at"]),
            has_position=has_position,
        )

    def is_s1_consistent(self, signal: DecisionSignal, advice: AnalysisAdvice) -> bool:
        advice_group = _action_group(advice.action)
        if advice_group == "unknown":
            return False
        if advice.conflict_status in {"hard_conflict", "conditional_entry"}:
            return False
        if advice.conflict_status == "position_context_split":
            return advice_group in {"entry", "exit"}
        if advice.intent_source == "g5":
            return advice.conflict_status == "consistent"
        signal_group = _action_group(signal.action)
        if signal_group == "unknown":
            return False
        return signal_group == advice_group

    def _resolved_advice(
        self,
        *,
        signal: DecisionSignal,
        report_id: int,
        stock_code: str,
        operation_advice: Optional[str],
        created_at: Optional[datetime],
        has_position: bool,
    ) -> AnalysisAdvice:
        resolution = resolve_intent(
            signal_action=signal.action,
            operation_advice=operation_advice,
            metadata=signal.metadata,
            has_position=has_position,
        )
        return AnalysisAdvice(
            report_id=report_id,
            stock_code=stock_code,
            operation_advice=operation_advice,
            action=resolution.effective_action,
            created_at=created_at,
            flat_account_action=resolution.flat_account_action,
            holding_action=resolution.holding_action,
            resolved_action=resolution.resolved_action,
            conflict_status=resolution.conflict_status,
            conflict_reason=resolution.conflict_reason,
            intent_source=resolution.source,
        )

    @staticmethod
    def _with_execution_action(signal: DecisionSignal, advice: AnalysisAdvice) -> DecisionSignal:
        metadata = dict(signal.metadata)
        metadata["intent_resolution"] = {
            "flat_account_action": advice.flat_account_action,
            "holding_action": advice.holding_action,
            "resolved_action": advice.resolved_action,
            "effective_action": advice.action,
            "conflict_status": advice.conflict_status,
            "conflict_reason": advice.conflict_reason,
            "intent_source": advice.intent_source,
        }
        return replace(signal, action=advice.action, metadata=metadata)

    @staticmethod
    def is_conditional_entry_plan(signal: DecisionSignal, advice: AnalysisAdvice) -> bool:
        """A conditional-entry buy carries an executable plan: entry zone + expiry.

        Instead of discarding it as a conflict, it becomes a resting limit order
        at entry_high that only fills if price returns to the zone (never chases).
        """
        return (
            advice.conflict_status == "conditional_entry"
            and _action_group(signal.action) == "entry"
            and signal.entry_high is not None
            and signal.entry_high > 0
            and signal.expires_at is not None
        )

    @staticmethod
    def _as_conditional_limit_plan(signal: DecisionSignal, advice: AnalysisAdvice) -> DecisionSignal:
        metadata = dict(signal.metadata)
        metadata["intent_resolution"] = {
            "flat_account_action": advice.flat_account_action,
            "holding_action": advice.holding_action,
            "resolved_action": advice.resolved_action,
            "effective_action": signal.action,
            "conflict_status": advice.conflict_status,
            "conflict_reason": advice.conflict_reason,
            "intent_source": advice.intent_source,
        }
        metadata["execution_plan"] = {
            "type": "conditional_limit",
            "limit_price": signal.entry_high,
            "source": "s1_conditional_entry_promotion",
        }
        return replace(signal, metadata=metadata)

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
        placeholders = self._pool_placeholders()
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                select code, date, open, high, low, close, volume, amount, pct_chg
                from stock_daily
                where date = ? and code in ({placeholders})
                """,
                (day.isoformat(), *self.stock_pool),
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

    def latest_stock_name(self, stock_code: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                """
                select stock_name
                from decision_signals
                where stock_code = ? and stock_name is not null and stock_name != ''
                order by datetime(created_at) desc, id desc
                limit 1
                """,
                (stock_code,),
            ).fetchone()
            if row is not None:
                return str(row["stock_name"])
            row = conn.execute(
                """
                select name
                from analysis_history
                where code = ? and name is not null and name != ''
                order by datetime(created_at) desc, id desc
                limit 1
                """,
                (stock_code,),
            ).fetchone()
        return str(row["name"]) if row is not None else None

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
        placeholders = self._pool_placeholders()
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                select *
                from decision_signals
                where date(created_at) between ? and ?
                  and market = ?
                  and stock_code in ({placeholders})
                order by datetime(created_at), id
                """,
                (start.isoformat(), end.isoformat(), self.market, *self.stock_pool),
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
            market=row["market"] or US_MARKET,
            source_type=row["source_type"] or "analysis",
            source_agent=row["source_agent"],
            plan_quality=row["plan_quality"],
        )

    @staticmethod
    def _row_to_disciplined_signal(row: sqlite3.Row) -> DecisionSignal:
        metadata = _loads_json(row["completion_payload_json"])
        intent_resolution = _row_intent_resolution(row, metadata)
        if intent_resolution:
            metadata["intent_resolution"] = intent_resolution
            for key, value in intent_resolution.items():
                metadata.setdefault(key, value)
        dsa_analysis = _loads_json(_row_value(row, "dsa_analysis_json"))
        if dsa_analysis:
            metadata["dsa_analysis"] = dsa_analysis
        metadata["discipline"] = {
            "schema_version": row["schema_version"],
            "completion_version": row["completion_version"],
            "model": row["model"],
            "completed_at": row["completed_at"],
            "source_signal_id": row["source_signal_id"],
            "gate_action": row["gate_action"],
            "gate_reasons": json.loads(row["gate_reasons_json"] or "[]"),
        }
        metadata["temporal"] = {
            "decision_timestamp": _row_value(row, "decision_timestamp"),
            "market_phase": _row_value(row, "market_phase"),
            "data_asof": _row_value(row, "data_asof"),
            "bar_cutoff": _row_value(row, "bar_cutoff"),
            "news_cutoff": _row_value(row, "news_cutoff"),
        }
        return DecisionSignal(
            id=int(row["source_signal_id"]),
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
            metadata=metadata,
            market=row["market"] or US_MARKET,
            source_type="disciplined_signal",
            source_agent="g5_completion",
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


def _row_value(row: sqlite3.Row, key: str) -> Any:
    try:
        return row[key]
    except (IndexError, KeyError):
        return None


def _row_intent_resolution(row: sqlite3.Row, metadata: Dict[str, Any]) -> Dict[str, Any]:
    resolution = {
        "flat_account_action": _row_value(row, "flat_account_action") or metadata.get("flat_account_action"),
        "holding_action": _row_value(row, "holding_action") or metadata.get("holding_action"),
        "resolved_action": _row_value(row, "resolved_action") or metadata.get("resolved_action"),
        "conflict_status": _row_value(row, "conflict_status") or metadata.get("conflict_status"),
        "conflict_reason": _row_value(row, "conflict_reason") or metadata.get("conflict_reason"),
    }
    return {key: value for key, value in resolution.items() if value not in (None, "")}


def _disciplined_row_available_before(row: sqlite3.Row, execution_date: date, columns: set[str]) -> bool:
    if all(column in columns for column in TEMPORAL_COLUMNS):
        data_asof = parse_date(_row_value(row, "data_asof"))
        if data_asof is not None:
            if data_asof >= execution_date:
                return False
            completed_utc = _as_utc(parse_datetime(_row_value(row, "completed_at")))
            open_utc = _us_regular_open_utc(execution_date)
            return completed_utc is None or completed_utc < open_utc
    return _legacy_row_date_before(row, "created_at", execution_date) and (
        _row_value(row, "completed_at") in (None, "") or _legacy_row_date_before(row, "completed_at", execution_date)
    )


def _legacy_row_date_before(row: sqlite3.Row, key: str, execution_date: date) -> bool:
    parsed = parse_date(_row_value(row, key))
    return parsed is not None and parsed < execution_date


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=LOCAL_RUNTIME_TZ)
    return value.astimezone(timezone.utc)


def _us_regular_open_utc(execution_date: date) -> datetime:
    return datetime.combine(execution_date, US_REGULAR_OPEN_TIME, tzinfo=US_MARKET_TZ).astimezone(timezone.utc)
