#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from executor.config import QUANT_DIR, ExecutorConfig
from executor.ledger import PaperLedger
from executor.signal_reader import SignalReader, parse_date, parse_datetime
from executor.time_guard import NewsTimingAudit, bar_available_at, classify_news_for_attribution


def profit_loss_ratio(pnls: Sequence[float]) -> Optional[float]:
    wins = sum(value for value in pnls if value > 0)
    losses = abs(sum(value for value in pnls if value < 0))
    if losses == 0:
        return None
    return round(wins / losses, 4)


def expectancy(pnls: Sequence[float]) -> Optional[float]:
    if not pnls:
        return None
    return round(sum(pnls) / len(pnls), 4)


def max_drawdown(values: Sequence[float]) -> float:
    peak: Optional[float] = None
    worst = 0.0
    for value in values:
        if value <= 0:
            continue
        if peak is None or value > peak:
            peak = value
        if peak:
            worst = max(worst, (peak - value) / peak)
    return round(worst, 6)


def bootstrap_mean_ci(
    values: Sequence[float],
    *,
    samples: int = 1000,
    confidence: float = 0.90,
    seed: int = 42,
) -> Optional[Tuple[float, float]]:
    if not values:
        return None
    rng = random.Random(seed)
    means: List[float] = []
    values_list = list(values)
    for _ in range(samples):
        draw = [rng.choice(values_list) for _ in values_list]
        means.append(sum(draw) / len(draw))
    means.sort()
    tail = (1.0 - confidence) / 2.0
    lower_idx = max(0, min(samples - 1, int(tail * samples)))
    upper_idx = max(0, min(samples - 1, int((1.0 - tail) * samples) - 1))
    return round(means[lower_idx], 6), round(means[upper_idx], 6)


def _connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _fmt_money(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"¥{value:,.2f}"


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.2f}%"


def _fmt_number(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"{value:.4f}"


def _date_arg(text: Optional[str], fallback: Optional[date] = None) -> date:
    if text:
        parsed = parse_date(text)
        if parsed is None:
            raise SystemExit(f"invalid date: {text}")
        return parsed
    if fallback is None:
        raise SystemExit("date is required")
    return fallback


def load_signal_stats(reader: SignalReader, start: date, end: date) -> Dict[str, Any]:
    stats: Dict[str, Any] = {
        "signal_count": 0,
        "outcome_by_horizon": [],
        "outcome_by_action_confidence": [],
        "s1_conflicts": [],
    }
    with _connect_ro(reader.db_path) as conn:
        row = conn.execute(
            "select count(*) as count from decision_signals where date(created_at) between ? and ?",
            (start.isoformat(), end.isoformat()),
        ).fetchone()
        stats["signal_count"] = int(row["count"] if row else 0)
        stats["outcome_by_horizon"] = conn.execute(
            """
            select horizon,
                   count(*) as total,
                   sum(case when direction_correct = 1 then 1 else 0 end) as wins
            from decision_signal_outcomes
            where date(coalesce(updated_at, created_at)) between ? and ?
              and eval_status = 'completed'
            group by horizon
            order by horizon
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        stats["outcome_by_action_confidence"] = conn.execute(
            """
            select s.action,
                   s.confidence,
                   o.horizon,
                   count(*) as total,
                   sum(case when o.direction_correct = 1 then 1 else 0 end) as wins
            from decision_signal_outcomes o
            join decision_signals s on s.id = o.signal_id
            where date(coalesce(o.updated_at, o.created_at)) between ? and ?
              and o.eval_status = 'completed'
            group by s.action, s.confidence, o.horizon
            order by s.action, s.confidence, o.horizon
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()

    review_cutoff = end + timedelta(days=1)
    for signal, advice in reader.s1_conflicts(review_cutoff):
        stats["s1_conflicts"].append(
            {
                "signal_id": signal.id,
                "stock_code": signal.stock_code,
                "signal_action": signal.action,
                "advice_action": advice.action,
                "operation_advice": advice.operation_advice,
            }
        )
    return stats


def load_paper_stats(ledger: PaperLedger, start: date, end: date) -> Dict[str, Any]:
    snapshots = ledger.snapshots_between(start, end)
    trades = ledger.trades_between(start, end)
    latest = snapshots[-1] if snapshots else None
    sell_trades = [row for row in trades if row["side"] == "sell"]
    pnls = [float(row["realized_pnl"]) for row in sell_trades if row["realized_pnl"] is not None]
    snapshot_values = [float(row["total_value"]) for row in snapshots]
    returns = _snapshot_returns(snapshot_values)
    avg_holding_days = _average_holding_days(trades)
    stop_sells = sum(1 for row in sell_trades if row["reason"] in {"stop_loss", "t1_stop_loss_pending", "ambiguous_stop_loss"})
    take_sells = sum(1 for row in sell_trades if row["reason"] == "take_profit")

    return {
        "cash": float(latest["cash"]) if latest else None,
        "market_value": float(latest["market_value"]) if latest else None,
        "total_value": float(latest["total_value"]) if latest else None,
        "realized_pnl": float(latest["realized_pnl"]) if latest else ledger.realized_pnl(),
        "unrealized_pnl": float(latest["unrealized_pnl"]) if latest else None,
        "trade_count": len(trades),
        "closed_trade_count": len(sell_trades),
        "profit_loss_ratio": profit_loss_ratio(pnls),
        "expectancy": expectancy(pnls),
        "max_drawdown": max_drawdown(snapshot_values),
        "stop_trigger_rate": stop_sells / len(sell_trades) if sell_trades else None,
        "take_profit_trigger_rate": take_sells / len(sell_trades) if sell_trades else None,
        "avg_holding_days": avg_holding_days,
        "returns": returns,
        "closed_trades": sell_trades,
    }


def load_ledger_gaps(ledger: PaperLedger, start: date, end: date) -> List[sqlite3.Row]:
    with ledger._connect() as conn:
        rows = conn.execute(
            """
            select *
            from signal_events
            where event_type = 'data_gap'
              and event_date between ? and ?
            order by event_date, id
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    return rows


def load_news_timing_audit(reader: SignalReader, start: date, end: date) -> List[NewsTimingAudit]:
    with _connect_ro(reader.db_path) as conn:
        table = conn.execute(
            "select name from sqlite_master where type = 'table' and name = 'news_intel'"
        ).fetchone()
        if table is None:
            return []
        rows = conn.execute(
            """
            select s.id as signal_id,
                   s.stock_code,
                   s.created_at as decision_timestamp,
                   o.horizon,
                   o.anchor_date,
                   n.title,
                   n.source,
                   n.published_date
            from decision_signal_outcomes o
            join decision_signals s on s.id = o.signal_id
            join news_intel n on n.code = s.stock_code
                            and date(n.published_date) = date(o.anchor_date)
            where date(coalesce(o.updated_at, o.created_at)) between ? and ?
              and o.anchor_date is not null
              and o.eval_status = 'completed'
            order by date(o.anchor_date), s.stock_code, datetime(n.published_date), n.id
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()

    audits: List[NewsTimingAudit] = []
    for row in rows:
        anchor_date = parse_date(row["anchor_date"])
        if anchor_date is None:
            continue
        decision_timestamp = parse_datetime(row["decision_timestamp"])
        published_at = parse_datetime(row["published_date"])
        status, reason = classify_news_for_attribution(
            published_at=published_at,
            decision_timestamp=decision_timestamp,
            anchor_date=anchor_date,
        )
        audits.append(
            NewsTimingAudit(
                signal_id=int(row["signal_id"]),
                stock_code=str(row["stock_code"]),
                horizon=str(row["horizon"]),
                anchor_date=anchor_date,
                decision_timestamp=decision_timestamp,
                bar_available_at=bar_available_at(anchor_date),
                news_title=str(row["title"] or ""),
                news_published_at=published_at,
                news_source=row["source"],
                attribution_status=status,
                reason=reason,
            )
        )
    return audits


def _snapshot_returns(values: Sequence[float]) -> List[float]:
    returns: List[float] = []
    for previous, current in zip(values, values[1:]):
        if previous > 0:
            returns.append(current / previous - 1.0)
    return returns


def _average_holding_days(trades: Sequence[sqlite3.Row]) -> Optional[float]:
    open_buys: Dict[str, List[date]] = {}
    holding_days: List[int] = []
    for row in trades:
        trade_date = parse_date(row["trade_date"])
        if trade_date is None:
            continue
        code = row["stock_code"]
        if row["side"] == "buy":
            open_buys.setdefault(code, []).append(trade_date)
        elif row["side"] == "sell" and open_buys.get(code):
            buy_date = open_buys[code].pop(0)
            holding_days.append((trade_date - buy_date).days)
    if not holding_days:
        return None
    return round(sum(holding_days) / len(holding_days), 2)


def load_benchmarks(reader: SignalReader, config: ExecutorConfig, start: date, end: date) -> Dict[str, Any]:
    equal_weight = _equal_weight_return(reader, config, start, end)
    hs300_start = parse_date(equal_weight.get("start_date")) if equal_weight.get("available") else None
    hs300_end = parse_date(equal_weight.get("end_date")) if equal_weight.get("available") else None
    return {
        "hs300": _hs300_return(config, hs300_start or start, hs300_end or end),
        "equal_weight": equal_weight,
    }


def _hs300_return(config: ExecutorConfig, start: date, end: date) -> Dict[str, Any]:
    errors = []
    for code in config.benchmark_codes:
        secid = _eastmoney_index_secid(code)
        if secid is not None:
            try:
                result = _benchmark_from_bars(
                    code=code,
                    bars=_fetch_eastmoney_index_bars(secid, start, end),
                    source="Eastmoney index kline, gross price return without fees/slippage",
                )
                if result is not None:
                    return result
            except Exception as exc:
                errors.append(f"{code}/eastmoney: {type(exc).__name__}: {exc}")
        symbol = _tencent_index_symbol(code)
        if symbol is not None:
            try:
                result = _benchmark_from_bars(
                    code=code,
                    bars=_fetch_tencent_index_bars(symbol, start, end),
                    source="Tencent index kline, gross price return without fees/slippage",
                )
                if result is not None:
                    return result
            except Exception as exc:
                errors.append(f"{code}/tencent: {type(exc).__name__}: {exc}")
    return {
        "available": False,
        "reason": "No HS300 bars fetched from external index source." + (f" Errors: {'; '.join(errors[:3])}" if errors else ""),
        "source": "Eastmoney/Tencent index kline",
    }


def _benchmark_from_bars(*, code: str, bars: Sequence[Dict[str, Any]], source: str) -> Optional[Dict[str, Any]]:
    if len(bars) < 1:
        return None
    first = bars[0]
    last = bars[-1]
    if first["open"] and last["close"]:
        return {
            "available": True,
            "code": code,
            "start_date": first["date"],
            "end_date": last["date"],
            "return": float(last["close"]) / float(first["open"]) - 1.0,
            "source": source,
        }
    return None


def _eastmoney_index_secid(code: str) -> Optional[str]:
    normalized = (code or "").strip().upper().replace(".", "")
    if normalized in {"000300", "SH000300", "000300SH"}:
        return "1.000300"
    if normalized in {"399300", "SZ399300", "399300SZ"}:
        return "0.399300"
    return None


def _tencent_index_symbol(code: str) -> Optional[str]:
    normalized = (code or "").strip().upper().replace(".", "")
    if normalized in {"000300", "SH000300", "000300SH"}:
        return "sh000300"
    if normalized in {"399300", "SZ399300", "399300SZ"}:
        return "sz399300"
    return None


def _fetch_eastmoney_index_bars(secid: str, start: date, end: date) -> List[Dict[str, Any]]:
    params = {
        "secid": secid,
        "klt": "101",
        "fqt": "1",
        "beg": start.strftime("%Y%m%d"),
        "end": end.strftime("%Y%m%d"),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urlencode(params)
    with urlopen(url, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    klines = ((payload.get("data") or {}).get("klines") or [])
    bars = []
    for item in klines:
        parts = str(item).split(",")
        if len(parts) < 5:
            continue
        bars.append(
            {
                "date": parts[0],
                "open": _coerce_float(parts[1]),
                "close": _coerce_float(parts[2]),
                "high": _coerce_float(parts[3]),
                "low": _coerce_float(parts[4]),
            }
        )
    return bars


def _fetch_tencent_index_bars(symbol: str, start: date, end: date) -> List[Dict[str, Any]]:
    params = {
        "param": f"{symbol},day,{start.isoformat()},{end.isoformat()},30,qfq",
    }
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?" + urlencode(params)
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json,text/plain,*/*"})
    with urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    data = payload.get("data") if isinstance(payload, dict) else None
    item = data.get(symbol) if isinstance(data, dict) else None
    rows = []
    if isinstance(item, dict):
        rows = item.get("qfqday") or item.get("day") or []
    bars = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 5:
            continue
        bars.append(
            {
                "date": str(row[0]),
                "open": _coerce_float(row[1]),
                "close": _coerce_float(row[2]),
                "high": _coerce_float(row[3]),
                "low": _coerce_float(row[4]),
            }
        )
    return bars


def _coerce_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _equal_weight_return(reader: SignalReader, config: ExecutorConfig, start: date, end: date) -> Dict[str, Any]:
    common_dates = []
    for trading_date in reader.trading_dates(start, end):
        if all(reader.bar(code, trading_date) is not None for code in config.stock_pool):
            common_dates.append(trading_date)
    if not common_dates:
        return {
            "available": False,
            "reason": "No common stock_pool bars in review range.",
            "source": "DSA stock_daily",
        }
    start_date = common_dates[0]
    end_date = common_dates[-1]
    details = []
    returns = []

    for code in config.stock_pool:
        start_bar = reader.bar(code, start_date)
        end_bar = reader.bar(code, end_date)
        if start_bar is None or end_bar is None or start_bar.open is None or end_bar.close is None:
            return {
                "available": False,
                "reason": f"Missing bar for {code} on aligned dates.",
                "source": "DSA stock_daily",
            }
        symbol_return = float(end_bar.close) / float(start_bar.open) - 1.0
        returns.append(symbol_return)
        details.append(
            {
                "code": code,
                "start_open": float(start_bar.open),
                "end_close": float(end_bar.close),
                "return": symbol_return,
            }
        )

    return {
        "available": True,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "return": sum(returns) / len(returns),
        "source": "DSA stock_daily, gross price return without fees/slippage",
        "details": details,
    }


def _bars_between(db_path: Path, code: str, start: date, end: date) -> List[sqlite3.Row]:
    with _connect_ro(db_path) as conn:
        return conn.execute(
            """
            select code, date, open, high, low, close
            from stock_daily
            where code = ?
              and date between ? and ?
            order by date
            """,
            (code, start.isoformat(), end.isoformat()),
        ).fetchall()


def closed_signal_reflections(paper_stats: Dict[str, Any], benchmarks: Dict[str, Any]) -> List[Dict[str, Any]]:
    hs300 = benchmarks.get("hs300", {})
    hs300_return = hs300.get("return") if hs300.get("available") else None
    reflections = []
    for row in paper_stats["closed_trades"]:
        denominator = float(row["gross_amount"]) - float(row["realized_pnl"] or 0.0)
        realized_return = float(row["realized_pnl"] or 0.0) / denominator if denominator > 0 else None
        reflections.append(
            {
                "signal_id": row["signal_id"],
                "stock_code": row["stock_code"],
                "trade_date": row["trade_date"],
                "reason": row["reason"],
                "realized_pnl": row["realized_pnl"],
                "realized_return": realized_return,
                "alpha_vs_hs300": realized_return - hs300_return if realized_return is not None and hs300_return is not None else None,
            }
        )
    return reflections


def render_report(
    *,
    start: date,
    end: date,
    signal_stats: Dict[str, Any],
    paper_stats: Dict[str, Any],
    benchmarks: Dict[str, Any],
    data_gaps: Sequence[sqlite3.Row],
    news_timing_audit: Sequence[NewsTimingAudit],
) -> str:
    ci = bootstrap_mean_ci(paper_stats["returns"])
    reflections = closed_signal_reflections(paper_stats, benchmarks)
    lines = [
        f"# Weekly Review {end:%Y%m%d}",
        "",
        f"Period: {start.isoformat()} to {end.isoformat()}",
        f"Generated: {datetime.utcnow().isoformat(sep=' ', timespec='seconds')} UTC",
        "",
        "## Signal 面",
        "",
        f"- Signals created in range: {signal_stats['signal_count']}",
        _render_outcome_by_horizon(signal_stats["outcome_by_horizon"]),
        _render_outcome_by_action_confidence(signal_stats["outcome_by_action_confidence"]),
        _render_s1_conflicts(signal_stats["s1_conflicts"]),
        "",
        "## 模拟盘面",
        "",
        f"- Cash: {_fmt_money(paper_stats['cash'])}",
        f"- Market value: {_fmt_money(paper_stats['market_value'])}",
        f"- Total value: {_fmt_money(paper_stats['total_value'])}",
        f"- Realized PnL: {_fmt_money(paper_stats['realized_pnl'])}",
        f"- Unrealized PnL: {_fmt_money(paper_stats['unrealized_pnl'])}",
        f"- Trades / closed trades: {paper_stats['trade_count']} / {paper_stats['closed_trade_count']}",
        f"- Profit/loss ratio: {_fmt_number(paper_stats['profit_loss_ratio'])}",
        f"- Expectancy: {_fmt_money(paper_stats['expectancy'])}",
        f"- Max drawdown: {_fmt_pct(paper_stats['max_drawdown'])}",
        f"- Stop-loss trigger rate: {_fmt_pct(paper_stats['stop_trigger_rate'])}",
        f"- Take-profit trigger rate: {_fmt_pct(paper_stats['take_profit_trigger_rate'])}",
        f"- Average holding days: {_fmt_number(paper_stats['avg_holding_days'])}",
        "",
        "## 双基线对比",
        "",
        _render_benchmark("HS300", benchmarks["hs300"]),
        _render_benchmark("Equal-weight 5-stock buy/hold", benchmarks["equal_weight"]),
        "",
        "## 统计诚实性",
        "",
        f"- Bootstrap 90% CI of daily paper returns: {_render_ci(ci)}",
        "- Method: numpy-free bootstrap resampling of daily portfolio returns with deterministic seed 42.",
        "",
        "## 延迟回填反思",
        "",
        _render_reflections(reflections),
        "",
        "## 时点校验",
        "",
        _render_news_timing_audit(news_timing_audit),
        "",
        "## 数据缺口",
        "",
        _render_data_gaps(data_gaps),
        "",
    ]
    return "\n".join(lines)


def _render_outcome_by_horizon(rows: Sequence[sqlite3.Row]) -> str:
    if not rows:
        return "- Outcome win rates T+1/T+3/T+5/T+10: unavailable, no completed DSA outcomes in range."
    parts = []
    for row in rows:
        total = int(row["total"])
        wins = int(row["wins"] or 0)
        parts.append(f"{row['horizon']}={wins}/{total} ({wins / total:.2%})")
    return "- Outcome win rates: " + "; ".join(parts)


def _render_outcome_by_action_confidence(rows: Sequence[sqlite3.Row]) -> str:
    if not rows:
        return "- Outcome by action/confidence: unavailable, no completed DSA outcomes in range."
    parts = []
    for row in rows:
        total = int(row["total"])
        wins = int(row["wins"] or 0)
        parts.append(f"{row['action']}/conf={row['confidence']}/{row['horizon']} {wins}/{total} ({wins / total:.2%})")
    return "- Outcome by action/confidence: " + "; ".join(parts)


def _render_s1_conflicts(conflicts: Sequence[Dict[str, Any]]) -> str:
    if not conflicts:
        return "- S1 conflicts: none detected among active prior signals."
    items = [
        f"{item['stock_code']} signal_id={item['signal_id']} signal={item['signal_action']} advice={item['advice_action']} text={item['operation_advice']}"
        for item in conflicts[:10]
    ]
    suffix = f" (+{len(conflicts) - 10} more)" if len(conflicts) > 10 else ""
    return "- S1 conflicts: " + "; ".join(items) + suffix


def _render_benchmark(label: str, benchmark: Dict[str, Any]) -> str:
    if not benchmark.get("available"):
        return f"- {label}: unavailable. {benchmark.get('reason')} Source: {benchmark.get('source')}"
    return (
        f"- {label}: {_fmt_pct(benchmark['return'])} "
        f"({benchmark['start_date']} to {benchmark['end_date']}). Source: {benchmark['source']}"
    )


def _render_ci(ci: Optional[Tuple[float, float]]) -> str:
    if ci is None:
        return "N/A, fewer than two portfolio snapshots in range."
    return f"{_fmt_pct(ci[0])} to {_fmt_pct(ci[1])}"


def _render_reflections(reflections: Sequence[Dict[str, Any]]) -> str:
    if not reflections:
        return "- No closed paper trades in range yet."
    lines = []
    for item in reflections:
        lines.append(
            "- "
            f"{item['stock_code']} signal_id={item['signal_id']} date={item['trade_date']} "
            f"reason={item['reason']} pnl={_fmt_money(item['realized_pnl'])} "
            f"return={_fmt_pct(item['realized_return'])} alpha_vs_hs300={_fmt_pct(item['alpha_vs_hs300'])}"
        )
    return "\n".join(lines)


def _fmt_dt(value: Optional[datetime]) -> str:
    if value is None:
        return "N/A"
    return value.isoformat(sep=" ", timespec="minutes")


def _render_news_timing_audit(rows: Sequence[NewsTimingAudit]) -> str:
    if not rows:
        return "- No same-anchor-day news timing evidence to audit in range."
    lines = [
        "| signal_id | code | horizon | anchor_date | decision_timestamp | bar_available_at | news_published_at | attribution_status | reason | title |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows[:20]:
        title = row.news_title.replace("|", "/")[:80]
        lines.append(
            "| "
            f"{row.signal_id} | {row.stock_code} | {row.horizon} | {row.anchor_date.isoformat()} | "
            f"{_fmt_dt(row.decision_timestamp)} | {_fmt_dt(row.bar_available_at)} | "
            f"{_fmt_dt(row.news_published_at)} | {row.attribution_status} | {row.reason} | {title} |"
        )
    if len(rows) > 20:
        lines.append(f"- Additional rows omitted: {len(rows) - 20}")
    return "\n".join(lines)


def _render_data_gaps(rows: Sequence[sqlite3.Row]) -> str:
    if not rows:
        return "- No executor data gaps recorded in range."
    return "\n".join(
        f"- {row['event_date']} {row['stock_code']} {row['reason']} {row['details_json'] or ''}"
        for row in rows
    )


def build_report(start: date, end: date, config: Optional[ExecutorConfig] = None) -> str:
    config = config or ExecutorConfig()
    reader = SignalReader(config.dsa_db_path)
    ledger = PaperLedger(config.ledger_db_path, config=config)
    ledger.initialize()
    signal_stats = load_signal_stats(reader, start, end)
    paper_stats = load_paper_stats(ledger, start, end)
    benchmarks = load_benchmarks(reader, config, start, end)
    data_gaps = load_ledger_gaps(ledger, start, end)
    news_timing_audit = load_news_timing_audit(reader, start, end)
    return render_report(
        start=start,
        end=end,
        signal_stats=signal_stats,
        paper_stats=paper_stats,
        benchmarks=benchmarks,
        data_gaps=data_gaps,
        news_timing_audit=news_timing_audit,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate weekly quant review markdown.")
    parser.add_argument("--start", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", help="End date YYYY-MM-DD")
    parser.add_argument("--output", help="Output markdown path")
    args = parser.parse_args()

    config = ExecutorConfig()
    reader = SignalReader(config.dsa_db_path)
    fallback_end = reader.latest_trading_date() or date.today()
    end = _date_arg(args.end, fallback=fallback_end)
    start = _date_arg(args.start, fallback=end - timedelta(days=6))
    report = build_report(start, end, config)

    output = Path(args.output) if args.output else QUANT_DIR / f"WEEKLY_REVIEW_{end:%Y%m%d}.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
