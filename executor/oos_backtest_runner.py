from __future__ import annotations

import json
import math
import sqlite3
import statistics
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol, Sequence

from executor.config import DEEPSEEK_API_KEY_PATH, RUNTIME_DIR, ExecutorConfig
from executor.discipline_completion import DEEPSEEK_CHAT_COMPLETIONS_URL
from executor.engine import PaperEngine
from executor.ledger import PaperLedger
from executor.oos_builder import OOS_DSA_DB_PATH
from executor.signal_reader import parse_date


OOS_BACKTEST_DIR = RUNTIME_DIR / "oos"
OOS_BACKTEST_REPORT_DIR = OOS_BACKTEST_DIR
OOS_SIGNAL_AGENT = "oos_backtest_runner"
DEFAULT_OOS_START = date(2024, 1, 1)
DEFAULT_OOS_END = date(2025, 12, 31)
DEFAULT_LOOKBACK_BARS = 60
DEFAULT_NEWS_LOOKBACK_DAYS = 30
DEFAULT_EXPIRY_TRADING_DAYS = 5


class SignalClient(Protocol):
    def generate_signal(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        ...


@dataclass(frozen=True)
class GeneratedSignal:
    code: str
    decision_date: str
    action: str
    confidence: Optional[float]
    source_report_id: int
    signal_id: int


@dataclass(frozen=True)
class SignalGenerationSummary:
    requested: int = 0
    generated: int = 0
    skipped_existing: int = 0
    errors: tuple[str, ...] = ()
    generated_signals: tuple[GeneratedSignal, ...] = ()


@dataclass(frozen=True)
class BacktestMetrics:
    start: str
    end: str
    trading_days: int
    snapshot_count: int
    trade_count: int
    buy_count: int
    sell_count: int
    initial_value: float
    final_value: Optional[float]
    total_return: Optional[float]
    annualized_return: Optional[float]
    max_drawdown: Optional[float]
    sharpe: Optional[float]
    expectancy_per_trade: Optional[float]
    win_rate: Optional[float]
    profit_factor: Optional[float]


@dataclass(frozen=True)
class OosBacktestResult:
    db_path: Path
    ledger_db_path: Path
    report_generated_at: str
    stock_pool: tuple[str, ...]
    signal_generation: SignalGenerationSummary
    stats: dict[str, int]
    metrics: BacktestMetrics
    warnings: tuple[str, ...] = ()


class DeepSeekSignalClient:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "deepseek-chat",
        api_url: str = DEEPSEEK_CHAT_COMPLETIONS_URL,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key.strip()
        self.model = model
        self.api_url = api_url
        self.timeout = timeout

    @classmethod
    def from_key_file(cls, key_path: Path, **kwargs: Any) -> "DeepSeekSignalClient":
        api_key = Path(key_path).read_text(encoding="utf-8").strip()
        if not api_key:
            raise RuntimeError(f"DeepSeek API key file is empty: {key_path}")
        return cls(api_key, **kwargs)

    def generate_signal(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        prompt = build_signal_prompt(context)
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an out-of-sample trading signal generator. "
                        "Use only the supplied historical bars and dated news. "
                        "Return only one valid JSON object."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.10,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            self.api_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"DeepSeek signal call failed http_status={exc.code} body={body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"DeepSeek signal call failed url_error={exc.reason}") from exc
        try:
            text = response_payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"DeepSeek signal response has no message content: {response_payload}") from exc
        return _loads_json_object(text)


class RuleSignalClient:
    """Deterministic local signal client for smoke tests and offline dry runs."""

    def generate_signal(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        bars = list(context.get("bars") or [])
        code = str(context.get("code") or "")
        if len(bars) < 20:
            return _rule_payload("watch", None, "Insufficient OOS lookback bars for a directional signal.")
        close = _float_or_none(bars[-1].get("close"))
        prev_close = _float_or_none(bars[-2].get("close")) if len(bars) >= 2 else None
        closes = [_float_or_none(row.get("close")) for row in bars[-20:]]
        closes = [value for value in closes if value is not None and value > 0]
        if close is None or close <= 0 or not closes:
            return _rule_payload("watch", None, "Latest close is missing.")
        ma20 = sum(closes) / len(closes)
        one_day_return = (close / prev_close - 1.0) if prev_close and prev_close > 0 else 0.0
        if close > ma20 * 1.015 and one_day_return >= 0:
            return {
                "action": "buy",
                "confidence": 0.58,
                "entry_low": round(close * 0.985, 4),
                "entry_high": round(close * 1.015, 4),
                "stop_loss": round(close * 0.92, 4),
                "target_price": round(close * 1.14, 4),
                "operation_advice": "buy",
                "analysis_summary": f"{code} closed above its 20-day average in the OOS window.",
                "risk_summary": "Offline rule signal; validate with live LLM before trusting alpha.",
                "catalyst_summary": "Price momentum.",
                "invalidation": "Close back below the 20-day average.",
            }
        if close < ma20 * 0.975 and one_day_return <= 0:
            return {
                "action": "reduce",
                "confidence": 0.56,
                "entry_low": None,
                "entry_high": None,
                "stop_loss": None,
                "target_price": None,
                "operation_advice": "reduce",
                "analysis_summary": f"{code} closed below its 20-day average in the OOS window.",
                "risk_summary": "Momentum deterioration.",
                "catalyst_summary": "Price weakness.",
                "invalidation": "Recover above the 20-day average.",
            }
        return _rule_payload("watch", close, "No sufficiently strong local rule signal.")


def run_oos_backtest(
    *,
    db_path: Path = OOS_DSA_DB_PATH,
    ledger_db_path: Path,
    start: date = DEFAULT_OOS_START,
    end: date = DEFAULT_OOS_END,
    stock_pool: Sequence[str] = ExecutorConfig().stock_pool,
    signal_client: Optional[SignalClient] = None,
    generate_signals: bool = False,
    force_signals: bool = False,
    force_ledger: bool = False,
    max_days: Optional[int] = None,
    max_calls: Optional[int] = None,
    lookback_bars: int = DEFAULT_LOOKBACK_BARS,
    news_lookback_days: int = DEFAULT_NEWS_LOOKBACK_DAYS,
    expiry_trading_days: int = DEFAULT_EXPIRY_TRADING_DAYS,
    sleep_seconds: float = 0.0,
) -> OosBacktestResult:
    db_path = Path(db_path)
    ledger_db_path = Path(ledger_db_path)
    codes = tuple(str(code).strip().upper() for code in stock_pool if str(code).strip())
    if not codes:
        raise ValueError("stock_pool must not be empty")
    ensure_oos_backtest_schema(db_path)

    signal_summary = SignalGenerationSummary()
    if generate_signals:
        if signal_client is None:
            signal_client = DeepSeekSignalClient.from_key_file(DEEPSEEK_API_KEY_PATH)
        signal_summary = generate_oos_signals(
            db_path=db_path,
            start=start,
            end=end,
            stock_pool=codes,
            client=signal_client,
            force=force_signals,
            max_days=max_days,
            max_calls=max_calls,
            lookback_bars=max(1, lookback_bars),
            news_lookback_days=max(0, news_lookback_days),
            expiry_trading_days=max(1, expiry_trading_days),
            sleep_seconds=max(0.0, sleep_seconds),
        )

    if ledger_db_path.exists():
        if not force_ledger:
            raise FileExistsError(f"ledger already exists; pass force_ledger=True to replace it: {ledger_db_path}")
        ledger_db_path.unlink()
    ledger_db_path.parent.mkdir(parents=True, exist_ok=True)

    base = ExecutorConfig()
    config = replace(
        base,
        dsa_db_path=db_path,
        ledger_db_path=ledger_db_path,
        disciplined_db_path=None,
        use_disciplined_signals=False,
        stock_pool=codes,
    )
    engine = PaperEngine(config)
    stats = engine.backfill(start, end)
    metrics = metrics_from_ledger(ledger_db_path, config=config, start=start, end=end)
    warnings = _result_warnings(db_path, start, end, codes, signal_summary, metrics)
    return OosBacktestResult(
        db_path=db_path,
        ledger_db_path=ledger_db_path,
        report_generated_at=datetime.utcnow().isoformat(sep=" ", timespec="seconds"),
        stock_pool=codes,
        signal_generation=signal_summary,
        stats=dict(stats),
        metrics=metrics,
        warnings=warnings,
    )


def generate_oos_signals(
    *,
    db_path: Path,
    start: date,
    end: date,
    stock_pool: Sequence[str],
    client: SignalClient,
    force: bool = False,
    max_days: Optional[int] = None,
    max_calls: Optional[int] = None,
    lookback_bars: int = DEFAULT_LOOKBACK_BARS,
    news_lookback_days: int = DEFAULT_NEWS_LOOKBACK_DAYS,
    expiry_trading_days: int = DEFAULT_EXPIRY_TRADING_DAYS,
    sleep_seconds: float = 0.0,
) -> SignalGenerationSummary:
    ensure_oos_backtest_schema(db_path)
    errors: list[str] = []
    generated: list[GeneratedSignal] = []
    requested = 0
    skipped_existing = 0
    trading_dates = trading_dates_between(db_path, start, end)
    if max_days is not None:
        trading_dates = trading_dates[: max(0, max_days)]
    date_index = {day: index for index, day in enumerate(trading_dates)}
    calls_left = max_calls
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        for decision_day in trading_dates:
            for code in stock_pool:
                if calls_left is not None and calls_left <= 0:
                    return SignalGenerationSummary(
                        requested=requested,
                        generated=len(generated),
                        skipped_existing=skipped_existing,
                        errors=tuple(errors),
                        generated_signals=tuple(generated),
                    )
                if not force and _existing_oos_signal(conn, code, decision_day):
                    skipped_existing += 1
                    continue
                context = build_signal_context(
                    conn,
                    code=code,
                    decision_day=decision_day,
                    lookback_bars=lookback_bars,
                    news_lookback_days=news_lookback_days,
                )
                if not context["bars"]:
                    errors.append(f"{code}@{decision_day}:missing_bars")
                    continue
                requested += 1
                try:
                    payload = normalize_signal_payload(client.generate_signal(context), context)
                    source_report_id, signal_id = persist_oos_signal(
                        conn,
                        code=code,
                        decision_day=decision_day,
                        payload=payload,
                        expires_at=_expiry_datetime(trading_dates, date_index, decision_day, expiry_trading_days),
                    )
                    generated.append(
                        GeneratedSignal(
                            code=code,
                            decision_date=decision_day.isoformat(),
                            action=str(payload["action"]),
                            confidence=payload.get("confidence"),
                            source_report_id=source_report_id,
                            signal_id=signal_id,
                        )
                    )
                    if calls_left is not None:
                        calls_left -= 1
                    if sleep_seconds > 0:
                        time.sleep(sleep_seconds)
                except Exception as exc:  # noqa: BLE001 - report one failed symbol/date without aborting the run.
                    errors.append(f"{code}@{decision_day}:{type(exc).__name__}:{exc}")
    return SignalGenerationSummary(
        requested=requested,
        generated=len(generated),
        skipped_existing=skipped_existing,
        errors=tuple(errors),
        generated_signals=tuple(generated),
    )


def ensure_oos_backtest_schema(db_path: Path) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            create table if not exists analysis_history (
                id integer primary key autoincrement,
                code text not null,
                name text,
                operation_advice text,
                created_at text
            );
            create table if not exists decision_signals (
                id integer primary key autoincrement,
                stock_code text not null,
                stock_name text,
                action text not null,
                confidence real,
                entry_high real,
                entry_low real,
                stop_loss real,
                target_price real,
                status text not null,
                created_at text,
                expires_at text,
                source_report_id integer,
                metadata_json text,
                market text,
                source_type text,
                source_agent text,
                plan_quality text
            );
            create index if not exists ix_oos_backtest_signal_agent
                on decision_signals(source_agent, stock_code, created_at);
            """
        )
        _ensure_columns(
            conn,
            "analysis_history",
            {
                "query_id": "text",
                "report_type": "text",
                "sentiment_score": "integer",
                "trend_prediction": "text",
                "analysis_summary": "text",
                "raw_result": "text",
                "news_content": "text",
                "context_snapshot": "text",
                "ideal_buy": "real",
                "secondary_buy": "real",
                "stop_loss": "real",
                "take_profit": "real",
            },
        )
        _ensure_columns(
            conn,
            "decision_signals",
            {
                "market": "text",
                "source_type": "text",
                "source_agent": "text",
                "trace_id": "text",
                "market_phase": "text",
                "trigger_source": "text",
                "action_label": "text",
                "score": "integer",
                "horizon": "text",
                "invalidation": "text",
                "watch_conditions": "text",
                "reason": "text",
                "risk_summary": "text",
                "catalyst_summary": "text",
                "evidence_json": "text",
                "data_quality_summary_json": "text",
                "plan_quality": "text",
                "updated_at": "text",
            },
        )


def build_signal_context(
    conn: sqlite3.Connection,
    *,
    code: str,
    decision_day: date,
    lookback_bars: int,
    news_lookback_days: int,
) -> dict[str, Any]:
    bar_rows = conn.execute(
        """
        select code, date, open, high, low, close, volume, amount, pct_chg, ma5, ma10, ma20, volume_ratio
        from stock_daily
        where code = ? and date <= ?
        order by date desc
        limit ?
        """,
        (code, decision_day.isoformat(), lookback_bars),
    ).fetchall()
    bars = [_row_dict(row) for row in reversed(bar_rows)]
    news_start = decision_day - timedelta(days=news_lookback_days)
    has_news = conn.execute(
        "select 1 from sqlite_master where type = 'table' and name = 'news_intel'"
    ).fetchone()
    news: list[dict[str, Any]] = []
    if has_news is not None:
        news_rows = conn.execute(
            """
            select title, snippet, url, source, provider, published_date
            from news_intel
            where code = ?
              and published_date is not null
              and date(published_date) between ? and ?
            order by datetime(published_date) desc, id desc
            limit 20
            """,
            (code, news_start.isoformat(), decision_day.isoformat()),
        ).fetchall()
        news = [_row_dict(row) for row in news_rows]
    return {
        "code": code,
        "decision_date": decision_day.isoformat(),
        "bars": bars,
        "news": news,
        "constraints": {
            "no_future_data_after": decision_day.isoformat(),
            "execution_model": "signal generated after close; earliest execution is a later trading day",
        },
    }


def build_signal_prompt(context: Mapping[str, Any]) -> str:
    schema = {
        "action": "one of buy, add, hold, watch, reduce, sell, avoid",
        "confidence": "number from 0 to 1",
        "entry_low": "number or null",
        "entry_high": "number or null",
        "stop_loss": "number or null",
        "target_price": "number or null",
        "operation_advice": "short English or Chinese advice matching action",
        "analysis_summary": "brief, evidence-based summary",
        "risk_summary": "brief risk summary",
        "catalyst_summary": "brief catalyst summary",
        "invalidation": "condition that invalidates the signal",
    }
    return "\n".join(
        [
            "Generate one OOS trading signal for the supplied stock/date.",
            "Use only the JSON input. Never infer from later dates.",
            "Prefer watch/hold when evidence is thin.",
            "For buy/add, provide entry_low, entry_high, stop_loss, and target_price.",
            "For sell/reduce/avoid/watch/hold, price fields may be null.",
            "Return a single JSON object matching this schema:",
            json.dumps(schema, ensure_ascii=False, sort_keys=True),
            "",
            "Input:",
            json.dumps(context, ensure_ascii=False, sort_keys=True),
        ]
    )


def normalize_signal_payload(payload: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "watch").strip().lower()
    if action not in {"buy", "add", "hold", "watch", "reduce", "sell", "avoid"}:
        action = "watch"
    latest_close = None
    bars = list(context.get("bars") or [])
    if bars:
        latest_close = _float_or_none(bars[-1].get("close"))
    confidence = _clamp(_float_or_none(payload.get("confidence")), 0.0, 1.0)
    normalized = {
        "action": action,
        "confidence": confidence if confidence is not None else 0.5,
        "entry_low": _float_or_none(payload.get("entry_low")),
        "entry_high": _float_or_none(payload.get("entry_high")),
        "stop_loss": _float_or_none(payload.get("stop_loss")),
        "target_price": _float_or_none(payload.get("target_price")),
        "operation_advice": str(payload.get("operation_advice") or action).strip() or action,
        "analysis_summary": str(payload.get("analysis_summary") or "").strip(),
        "risk_summary": str(payload.get("risk_summary") or "").strip(),
        "catalyst_summary": str(payload.get("catalyst_summary") or "").strip(),
        "invalidation": str(payload.get("invalidation") or "").strip(),
    }
    if action in {"buy", "add"} and latest_close is not None and latest_close > 0:
        normalized["entry_low"] = normalized["entry_low"] or round(latest_close * 0.98, 4)
        normalized["entry_high"] = normalized["entry_high"] or round(latest_close * 1.02, 4)
        normalized["stop_loss"] = normalized["stop_loss"] or round(latest_close * 0.92, 4)
        normalized["target_price"] = normalized["target_price"] or round(latest_close * 1.12, 4)
    return normalized


def persist_oos_signal(
    conn: sqlite3.Connection,
    *,
    code: str,
    decision_day: date,
    payload: Mapping[str, Any],
    expires_at: datetime,
) -> tuple[int, int]:
    now = datetime.utcnow().isoformat(sep=" ")
    created_at = f"{decision_day.isoformat()} 16:10:00"
    raw_result = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    context_snapshot = json.dumps(
        {
            "oos": True,
            "decision_date": decision_day.isoformat(),
            "generated_at": now,
            "agent": OOS_SIGNAL_AGENT,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    analysis_cursor = conn.execute(
        """
        insert into analysis_history(
            query_id, code, name, report_type, sentiment_score, operation_advice,
            trend_prediction, analysis_summary, raw_result, news_content,
            context_snapshot, ideal_buy, secondary_buy, stop_loss, take_profit, created_at
        )
        values (?, ?, ?, 'oos_backtest', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"oos-{code}-{decision_day.isoformat()}",
            code,
            code,
            int(round(float(payload.get("confidence") or 0.0) * 100)),
            payload.get("operation_advice"),
            payload.get("action"),
            payload.get("analysis_summary"),
            raw_result,
            payload.get("catalyst_summary"),
            context_snapshot,
            payload.get("entry_low"),
            payload.get("entry_high"),
            payload.get("stop_loss"),
            payload.get("target_price"),
            created_at,
        ),
    )
    source_report_id = int(analysis_cursor.lastrowid)
    metadata = {
        "oos_backtest": True,
        "decision_date": decision_day.isoformat(),
        "agent": OOS_SIGNAL_AGENT,
        "raw_payload": dict(payload),
    }
    signal_cursor = conn.execute(
        """
        insert into decision_signals(
            stock_code, stock_name, market, source_type, source_agent, source_report_id,
            trace_id, market_phase, trigger_source, action, action_label, confidence,
            score, horizon, entry_low, entry_high, stop_loss, target_price,
            invalidation, watch_conditions, reason, risk_summary, catalyst_summary,
            evidence_json, data_quality_summary_json, plan_quality, status, expires_at,
            created_at, updated_at, metadata_json
        )
        values (?, ?, 'cn', 'oos_backtest', ?, ?, ?, 'close', 'oos_backtest',
                ?, ?, ?, ?, 'swing', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'oos_generated',
                'active', ?, ?, ?, ?)
        """,
        (
            code,
            code,
            OOS_SIGNAL_AGENT,
            source_report_id,
            f"oos-{code}-{decision_day.isoformat()}",
            payload.get("action"),
            payload.get("action"),
            payload.get("confidence"),
            int(round(float(payload.get("confidence") or 0.0) * 100)),
            payload.get("entry_low"),
            payload.get("entry_high"),
            payload.get("stop_loss"),
            payload.get("target_price"),
            payload.get("invalidation"),
            None,
            payload.get("analysis_summary"),
            payload.get("risk_summary"),
            payload.get("catalyst_summary"),
            raw_result,
            json.dumps({"lookahead_guard": "bars_and_news_lte_decision_date"}, sort_keys=True),
            expires_at.isoformat(sep=" "),
            created_at,
            now,
            json.dumps(metadata, ensure_ascii=False, sort_keys=True),
        ),
    )
    return source_report_id, int(signal_cursor.lastrowid)


def metrics_from_ledger(
    ledger_db_path: Path,
    *,
    config: ExecutorConfig,
    start: date,
    end: date,
) -> BacktestMetrics:
    ledger = PaperLedger(ledger_db_path, config=config)
    snapshots = ledger.snapshots_between(start, end) if Path(ledger_db_path).exists() else []
    trades = ledger.trades_between(start, end) if Path(ledger_db_path).exists() else []
    final_value = float(snapshots[-1]["total_value"]) if snapshots else None
    total_return = final_value / config.initial_cash - 1.0 if final_value is not None and config.initial_cash else None
    trading_days = len(snapshots)
    annualized = None
    if total_return is not None and trading_days > 0:
        annualized = (1.0 + total_return) ** (252.0 / trading_days) - 1.0
    values = [config.initial_cash] + [float(row["total_value"]) for row in snapshots]
    max_drawdown = _max_drawdown(values) if len(values) >= 2 else None
    returns = _daily_returns(values)
    sharpe = _sharpe(returns)
    sell_pnls = [float(row["realized_pnl"]) for row in trades if row["side"] == "sell" and row["realized_pnl"] is not None]
    wins = [pnl for pnl in sell_pnls if pnl > 0]
    losses = [pnl for pnl in sell_pnls if pnl < 0]
    expectancy = statistics.fmean(sell_pnls) if sell_pnls else None
    win_rate = len(wins) / len(sell_pnls) if sell_pnls else None
    profit_factor = None
    if losses:
        profit_factor = sum(wins) / abs(sum(losses))
    elif wins:
        profit_factor = math.inf
    return BacktestMetrics(
        start=start.isoformat(),
        end=end.isoformat(),
        trading_days=trading_days,
        snapshot_count=len(snapshots),
        trade_count=len(trades),
        buy_count=sum(1 for row in trades if row["side"] == "buy"),
        sell_count=sum(1 for row in trades if row["side"] == "sell"),
        initial_value=float(config.initial_cash),
        final_value=final_value,
        total_return=total_return,
        annualized_return=annualized,
        max_drawdown=max_drawdown,
        sharpe=sharpe,
        expectancy_per_trade=expectancy,
        win_rate=win_rate,
        profit_factor=profit_factor,
    )


def trading_dates_between(db_path: Path, start: date, end: date) -> list[date]:
    if not Path(db_path).exists():
        return []
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            select distinct date
            from stock_daily
            where date between ? and ?
            order by date
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    return [parsed for row in rows if (parsed := parse_date(row[0])) is not None]


def render_backtest_markdown(result: OosBacktestResult) -> str:
    metrics = result.metrics
    lines = [
        f"# OOS Backtest Report {result.report_generated_at}",
        "",
        f"- OOS DB: `{result.db_path}`",
        f"- Ledger DB: `{result.ledger_db_path}`",
        f"- Window: `{metrics.start}` to `{metrics.end}`",
        f"- Stock pool: `{', '.join(result.stock_pool)}`",
        "",
        "## Signal Generation",
        "",
        f"- Requested LLM calls: `{result.signal_generation.requested}`",
        f"- Generated signals: `{result.signal_generation.generated}`",
        f"- Skipped existing signals: `{result.signal_generation.skipped_existing}`",
        f"- Errors: `{len(result.signal_generation.errors)}`",
        "",
        "## Performance",
        "",
        "| metric | value |",
        "| --- | ---: |",
        f"| trading_days | {metrics.trading_days} |",
        f"| trades | {metrics.trade_count} |",
        f"| buys | {metrics.buy_count} |",
        f"| sells | {metrics.sell_count} |",
        f"| final_value | {_fmt_float(metrics.final_value)} |",
        f"| total_return | {_fmt_pct(metrics.total_return)} |",
        f"| annualized_return | {_fmt_pct(metrics.annualized_return)} |",
        f"| max_drawdown | {_fmt_pct(metrics.max_drawdown)} |",
        f"| sharpe | {_fmt_float(metrics.sharpe)} |",
        f"| expectancy_per_sell_trade | {_fmt_float(metrics.expectancy_per_trade)} |",
        f"| win_rate | {_fmt_pct(metrics.win_rate)} |",
        f"| profit_factor | {_fmt_float(metrics.profit_factor)} |",
        "",
        "## Executor Stats",
        "",
        "| key | value |",
        "| --- | ---: |",
    ]
    for key in sorted(result.stats):
        lines.append(f"| {key} | {result.stats[key]} |")
    if result.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in result.warnings)
    if result.signal_generation.errors:
        lines.extend(["", "## Signal Errors", ""])
        lines.extend(f"- {error}" for error in result.signal_generation.errors[:50])
        if len(result.signal_generation.errors) > 50:
            lines.append(f"- ... {len(result.signal_generation.errors) - 50} more")
    lines.extend(["", "## Verdict", "", _backtest_verdict(result)])
    return "\n".join(lines) + "\n"


def write_backtest_report(path: Path, result: OosBacktestResult) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_backtest_markdown(result), encoding="utf-8")
    return path


def _result_warnings(
    db_path: Path,
    start: date,
    end: date,
    stock_pool: Sequence[str],
    signal_summary: SignalGenerationSummary,
    metrics: BacktestMetrics,
) -> tuple[str, ...]:
    warnings: list[str] = []
    if signal_summary.requested == 0 and signal_summary.generated == 0:
        count = _signal_count(db_path, start, end, stock_pool)
        if count == 0:
            warnings.append("no_oos_signals_in_window; run with signal generation enabled before treating this as alpha evidence")
    if metrics.trade_count == 0:
        warnings.append("no_trades_executed; performance metrics are engineering-only")
    if metrics.snapshot_count == 0:
        warnings.append("no_portfolio_snapshots; check stock_daily coverage for the requested window")
    return tuple(warnings)


def _backtest_verdict(result: OosBacktestResult) -> str:
    metrics = result.metrics
    if metrics.trade_count <= 0 or metrics.total_return is None:
        return "OOS alpha is not established: no executable trade evidence was produced."
    if (
        metrics.total_return > 0
        and (metrics.sharpe is not None and metrics.sharpe > 0)
        and (metrics.expectancy_per_trade is not None and metrics.expectancy_per_trade > 0)
    ):
        return "OOS replay produced positive return, positive Sharpe, and positive realized expectancy for this dataset."
    return "OOS alpha is not established: at least one of return, Sharpe, or realized expectancy is non-positive."


def _existing_oos_signal(conn: sqlite3.Connection, code: str, decision_day: date) -> bool:
    row = conn.execute(
        """
        select 1
        from decision_signals
        where stock_code = ?
          and date(created_at) = ?
          and source_agent = ?
        limit 1
        """,
        (code, decision_day.isoformat(), OOS_SIGNAL_AGENT),
    ).fetchone()
    return row is not None


def _signal_count(db_path: Path, start: date, end: date, stock_pool: Sequence[str]) -> int:
    placeholders = ",".join("?" for _ in stock_pool)
    if not placeholders or not Path(db_path).exists():
        return 0
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            f"""
            select count(*) as count
            from decision_signals
            where date(created_at) between ? and ?
              and stock_code in ({placeholders})
            """,
            (start.isoformat(), end.isoformat(), *stock_pool),
        ).fetchone()
    return int(row[0] or 0)


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: Mapping[str, str]) -> None:
    existing = {row[1] for row in conn.execute(f"pragma table_info({table})").fetchall()}
    for column, column_type in columns.items():
        if column not in existing:
            conn.execute(f"alter table {table} add column {column} {column_type}")


def _expiry_datetime(
    trading_dates: Sequence[date],
    date_index: Mapping[date, int],
    decision_day: date,
    expiry_trading_days: int,
) -> datetime:
    index = date_index.get(decision_day, 0)
    expiry_index = min(len(trading_dates) - 1, index + expiry_trading_days)
    expiry_day = trading_dates[expiry_index] if trading_dates else decision_day
    return datetime.combine(expiry_day, datetime.min.time()).replace(hour=23, minute=59)


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _loads_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = "\n".join(line for line in stripped.splitlines() if not line.strip().startswith("```")).strip()
    if not stripped.startswith("{"):
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            stripped = stripped[start : end + 1]
    payload = json.loads(stripped)
    if not isinstance(payload, dict):
        raise ValueError("LLM response was not a JSON object")
    return payload


def _rule_payload(action: str, close: Optional[float], reason: str) -> dict[str, Any]:
    return {
        "action": action,
        "confidence": 0.45,
        "entry_low": round(close * 0.98, 4) if close else None,
        "entry_high": round(close * 1.02, 4) if close else None,
        "stop_loss": round(close * 0.93, 4) if close else None,
        "target_price": round(close * 1.10, 4) if close else None,
        "operation_advice": action,
        "analysis_summary": reason,
        "risk_summary": reason,
        "catalyst_summary": "",
        "invalidation": reason,
    }


def _float_or_none(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _clamp(value: Optional[float], low: float, high: float) -> Optional[float]:
    if value is None:
        return None
    return max(low, min(high, value))


def _daily_returns(values: Sequence[float]) -> list[float]:
    returns: list[float] = []
    for previous, current in zip(values, values[1:]):
        if previous:
            returns.append(current / previous - 1.0)
    return returns


def _max_drawdown(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst


def _sharpe(returns: Sequence[float]) -> Optional[float]:
    if len(returns) < 2:
        return None
    std = statistics.stdev(returns)
    if std == 0:
        return None
    return statistics.fmean(returns) / std * math.sqrt(252.0)


def _fmt_float(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    if value == math.inf:
        return "inf"
    return f"{value:.4f}"


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.4f}%"


def result_as_dict(result: OosBacktestResult) -> dict[str, Any]:
    payload = asdict(result)
    payload["db_path"] = str(result.db_path)
    payload["ledger_db_path"] = str(result.ledger_db_path)
    return payload
