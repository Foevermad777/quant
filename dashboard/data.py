from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence


CN_POOL = ("600519", "300750", "601318", "600036", "600900")
US_POOL = ("AAPL", "NVDA", "MSFT", "JPM", "SPCX")

# A signal is genuinely live only if it is active AND not past its expiry day.
# Mirrors executor/signal_reader.py and executor/us/signal_reader_us.py so the
# dashboard reports what the executors would actually act on. expires_at is
# stored in UTC, so 'now' must be UTC too: with localtime, Beijing 00:00-08:00
# sits one calendar day ahead of UTC and US signals show expired up to a day
# before the executor drops them.
_UNEXPIRED_PREDICATE = (
    "status = 'active' and (expires_at is null or date(expires_at) >= date('now'))"
)


@dataclass(frozen=True)
class DashboardPaths:
    project_root: Path
    dsa_db: Path
    cn_ledger_db: Path
    us_ledger_db: Path
    quant_dir: Path
    logs_dir: Path

    @classmethod
    def from_project_root(cls, project_root: Path) -> "DashboardPaths":
        root = project_root.resolve()
        runtime_dir = root / "runtime_data"
        quant_dir = runtime_dir / "quant"
        return cls(
            project_root=root,
            dsa_db=runtime_dir / "dsa" / "stock_analysis.db",
            cn_ledger_db=quant_dir / "paper.db",
            us_ledger_db=quant_dir / "paper_us.db",
            quant_dir=quant_dir,
            logs_dir=runtime_dir / "logs",
        )


def default_paths() -> DashboardPaths:
    return DashboardPaths.from_project_root(Path(__file__).resolve().parents[1])


def build_overview(paths: DashboardPaths | None = None) -> dict[str, Any]:
    resolved = paths or default_paths()
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "project_root": str(resolved.project_root),
        "scan": scan_summary(resolved.dsa_db),
        "executors": {
            "cn": ledger_summary(
                label="A股执行器",
                market="cn",
                db_path=resolved.cn_ledger_db,
                pool=CN_POOL,
                currency="CNY",
            ),
            "us": ledger_summary(
                label="美股执行器",
                market="us",
                db_path=resolved.us_ledger_db,
                pool=US_POOL,
                currency="USD",
            ),
        },
        "logs": logs_summary(resolved),
    }


def scan_summary(db_path: Path) -> dict[str, Any]:
    summary: dict[str, Any] = _base_file_summary(db_path)
    summary.update(
        {
            "counts": {},
            "latest_analysis_at": None,
            "latest_signal_at": None,
            "recent_analyses": [],
            "recent_signals": [],
            "market_reviews": [],
            "active_signals_by_market_action": [],
            "pool_analysis": {"cn": [], "us": []},
            "pool_signals": {"cn": [], "us": []},
            "errors": [],
        }
    )
    if not summary["available"]:
        return summary

    try:
        with _connect_readonly(db_path) as conn:
            if _table_exists(conn, "analysis_history"):
                summary["counts"]["analysis_history"] = _scalar_int(conn, "select count(*) from analysis_history")
                # analysis_history.created_at is LOCAL time (unlike the UTC
                # decision_signals timestamps), so the 24h window must anchor on
                # localtime — against plain 'now' (UTC) it silently spans ~32h.
                summary["counts"]["analysis_24h"] = _scalar_int(
                    conn,
                    """
                    select count(*) from analysis_history
                    where datetime(created_at) >= datetime('now', 'localtime', '-24 hours')
                    """,
                )
                summary["latest_analysis_at"] = _scalar(
                    conn,
                    "select max(created_at) from analysis_history",
                )
                summary["recent_analyses"] = _rows(
                    conn,
                    """
                    select id, code, name, report_type, operation_advice, sentiment_score,
                           trend_prediction, ideal_buy, secondary_buy, stop_loss, take_profit,
                           created_at, substr(coalesce(analysis_summary, ''), 1, 220) as analysis_summary
                    from analysis_history
                    order by datetime(created_at) desc, id desc
                    limit 16
                    """,
                )
                summary["market_reviews"] = _rows(
                    conn,
                    """
                    select id, code, name, operation_advice, sentiment_score, created_at,
                           substr(coalesce(analysis_summary, ''), 1, 220) as analysis_summary
                    from analysis_history
                    where report_type = 'market_review' or code = 'MARKET'
                    order by datetime(created_at) desc, id desc
                    limit 5
                    """,
                )
                summary["pool_analysis"] = {
                    "cn": _latest_analysis_for_codes(conn, CN_POOL),
                    "us": _latest_analysis_for_codes(conn, US_POOL),
                }

            if _table_exists(conn, "decision_signals"):
                summary["counts"]["decision_signals"] = _scalar_int(conn, "select count(*) from decision_signals")
                # status alone lags reality (the DSA batch flips it after the
                # fact), so an "active" count can include already-expired
                # plans. expires_at is the truth the executors act on; keep the
                # dashboard on the same predicate. Boundary matches the readers:
                # still valid through the expiry day.
                summary["counts"]["active_signals"] = _scalar_int(
                    conn,
                    f"select count(*) from decision_signals where {_UNEXPIRED_PREDICATE}",
                )
                summary["latest_signal_at"] = _scalar(conn, "select max(created_at) from decision_signals")
                summary["recent_signals"] = _rows(
                    conn,
                    """
                    select id, stock_code, stock_name, market, action, action_label, confidence,
                           score, horizon, entry_low, entry_high, stop_loss, target_price,
                           plan_quality, status, created_at, expires_at,
                           substr(coalesce(reason, ''), 1, 220) as reason
                    from decision_signals
                    order by datetime(created_at) desc, id desc
                    limit 16
                    """,
                )
                summary["active_signals_by_market_action"] = _rows(
                    conn,
                    f"""
                    select market, action, count(*) as count
                    from decision_signals
                    where {_UNEXPIRED_PREDICATE}
                    group by market, action
                    order by market, action
                    """,
                )
                summary["pool_signals"] = {
                    "cn": _latest_signals_for_codes(conn, CN_POOL, "cn"),
                    "us": _latest_signals_for_codes(conn, US_POOL, "us"),
                }
    except sqlite3.Error as exc:
        summary["available"] = False
        summary["errors"].append(str(exc))
    return summary


def ledger_summary(
    *,
    label: str,
    market: str,
    db_path: Path,
    pool: Sequence[str],
    currency: str,
) -> dict[str, Any]:
    summary: dict[str, Any] = _base_file_summary(db_path)
    summary.update(
        {
            "label": label,
            "market": market,
            "currency": currency,
            "pool": list(pool),
            "account": None,
            "latest_snapshot": None,
            "return_rate": None,
            "positions": [],
            "recent_trades": [],
            "recent_order_attempts": [],
            "recent_events": [],
            "pending_exits": [],
            "portfolio_series": [],
            "counts": {},
            "order_attempts_by_status": [],
            "events_by_type": [],
            "discipline": {
                "available": False,
                "latest_completed_at": None,
                "gate_counts": [],
                "recent": [],
            },
            "latest_activity_at": None,
            "errors": [],
        }
    )
    if not summary["available"]:
        return summary

    try:
        with _connect_readonly(db_path) as conn:
            if _table_exists(conn, "account"):
                summary["account"] = _row(
                    conn,
                    "select id, cash, initial_cash, updated_at from account where id = 1",
                )
            if _table_exists(conn, "portfolio_snapshots"):
                summary["latest_snapshot"] = _row(
                    conn,
                    """
                    select snapshot_date, cash, market_value, total_value,
                           realized_pnl, unrealized_pnl, created_at
                    from portfolio_snapshots
                    order by date(snapshot_date) desc, datetime(created_at) desc
                    limit 1
                    """,
                )
                series = _rows(
                    conn,
                    """
                    select snapshot_date, cash, market_value, total_value,
                           realized_pnl, unrealized_pnl
                    from portfolio_snapshots
                    order by date(snapshot_date) desc
                    limit 60
                    """,
                )
                summary["portfolio_series"] = list(reversed(series))
            if _table_exists(conn, "positions"):
                summary["positions"] = _rows(
                    conn,
                    """
                    select stock_code, quantity, old_quantity, avg_cost, stop_loss,
                           target_price, source_signal_id, updated_at
                    from positions
                    order by stock_code
                    limit 50
                    """,
                )
            if _table_exists(conn, "trades"):
                summary["counts"]["trades"] = _scalar_int(conn, "select count(*) from trades")
                summary["recent_trades"] = _rows(
                    conn,
                    """
                    select id, signal_id, stock_code, side, trade_date, shares, fill_price,
                           exec_price, gross_amount, fees, taxes, cash_delta, realized_pnl,
                           reason, created_at
                    from trades
                    order by datetime(created_at) desc, id desc
                    limit 12
                    """,
                )
            if _table_exists(conn, "order_attempts"):
                summary["counts"]["order_attempts"] = _scalar_int(conn, "select count(*) from order_attempts")
                summary["order_attempts_by_status"] = _rows(
                    conn,
                    """
                    select status, count(*) as count
                    from order_attempts
                    group by status
                    order by count desc, status
                    """,
                )
                summary["recent_order_attempts"] = _rows(
                    conn,
                    """
                    select id, signal_id, stock_code, trade_date, status, reason, price, created_at
                    from order_attempts
                    order by datetime(created_at) desc, id desc
                    limit 12
                    """,
                )
            if _table_exists(conn, "signal_events"):
                summary["counts"]["signal_events"] = _scalar_int(conn, "select count(*) from signal_events")
                summary["events_by_type"] = _rows(
                    conn,
                    """
                    select event_type, count(*) as count
                    from signal_events
                    group by event_type
                    order by count desc, event_type
                    """,
                )
                summary["recent_events"] = _rows(
                    conn,
                    """
                    select id, signal_id, stock_code, event_date, event_type, reason, created_at
                    from signal_events
                    order by datetime(created_at) desc, id desc
                    limit 12
                    """,
                )
            if _table_exists(conn, "pending_exits"):
                summary["pending_exits"] = _rows(
                    conn,
                    """
                    select id, signal_id, stock_code, shares, stop_price, reason,
                           triggered_date, earliest_trade_date, status, updated_at
                    from pending_exits
                    where status != 'closed'
                    order by datetime(updated_at) desc, id desc
                    limit 12
                    """,
                )
            if _table_exists(conn, "disciplined_signals"):
                discipline = summary["discipline"]
                discipline["available"] = True
                discipline["latest_completed_at"] = _scalar(
                    conn,
                    "select max(completed_at) from disciplined_signals",
                )
                discipline["gate_counts"] = _rows(
                    conn,
                    """
                    select gate_action, gate_accepted, count(*) as count
                    from disciplined_signals
                    group by gate_action, gate_accepted
                    order by count desc, gate_action
                    """,
                )
                discipline["recent"] = _rows(
                    conn,
                    """
                    select source_signal_id, stock_code, stock_name, market, action,
                           confidence, score, status, plan_quality, gate_action,
                           gate_accepted, model, total_tokens, estimated_cost_usd,
                           completed_at
                    from disciplined_signals
                    order by datetime(completed_at) desc, source_signal_id desc
                    limit 12
                    """,
                )

            summary["latest_activity_at"] = _latest_activity(conn)
            summary["return_rate"] = _return_rate(summary.get("account"), summary.get("latest_snapshot"))
    except sqlite3.Error as exc:
        summary["available"] = False
        summary["errors"].append(str(exc))
    return summary


def logs_summary(paths: DashboardPaths) -> dict[str, Any]:
    return {
        "dsa_daily": _latest_log(paths.logs_dir.glob("stock_analysis_[0-9]*.log")),
        "cn_executor": _latest_log(
            path for path in paths.quant_dir.glob("executor_*.log") if not path.name.startswith("executor_us_")
        ),
        "cn_launcher": _log_file(paths.logs_dir / "executor_daily_launcher.log"),
        "cn_g5": _latest_log(paths.logs_dir.glob("g5_discipline_completion_*.log")),
        "us_dsa_daily": _latest_log(paths.logs_dir.glob("us_dsa_daily_[0-9]*.log")),
        "us_executor": _latest_log(paths.quant_dir.glob("executor_us_*.log")),
        "us_launcher": _log_file(paths.logs_dir / "us_executor_daily_launcher.log"),
        "us_g5": _latest_log(paths.logs_dir.glob("us_g5_discipline_completion_*.log")),
    }


def _connect_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=1.5)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma query_only = on")
    conn.execute("pragma busy_timeout = 1500")
    return conn


def _base_file_summary(path: Path) -> dict[str, Any]:
    summary = {
        "path": str(path),
        "available": path.exists(),
        "mtime": None,
        "size_bytes": None,
    }
    if path.exists():
        stat = path.stat()
        summary["mtime"] = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds")
        summary["size_bytes"] = stat.st_size
    return summary


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _scalar(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    return row[0]


def _scalar_int(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> int:
    value = _scalar(conn, sql, params)
    return int(value or 0)


def _row(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row is not None else None


def _rows(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _latest_analysis_for_codes(conn: sqlite3.Connection, codes: Sequence[str]) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in codes)
    return _rows(
        conn,
        f"""
        select id, code, name, report_type, operation_advice, sentiment_score,
               trend_prediction, ideal_buy, secondary_buy, stop_loss, take_profit,
               created_at, substr(coalesce(analysis_summary, ''), 1, 180) as analysis_summary
        from analysis_history h
        where h.code in ({placeholders})
          and h.report_type = 'simple'
          and h.id = (
            select h2.id
            from analysis_history h2
            where h2.code = h.code and h2.report_type = 'simple'
            order by datetime(h2.created_at) desc, h2.id desc
            limit 1
          )
        order by h.code
        """,
        tuple(codes),
    )


def _latest_signals_for_codes(
    conn: sqlite3.Connection,
    codes: Sequence[str],
    market: str,
) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in codes)
    return _rows(
        conn,
        f"""
        select id, stock_code, stock_name, market, action, confidence, score,
               entry_low, entry_high, stop_loss, target_price, plan_quality,
               status, created_at, expires_at, substr(coalesce(reason, ''), 1, 180) as reason
        from decision_signals s
        where s.stock_code in ({placeholders})
          and s.market = ?
          and s.id = (
            select s2.id
            from decision_signals s2
            where s2.stock_code = s.stock_code and s2.market = s.market
            order by datetime(s2.created_at) desc, s2.id desc
            limit 1
          )
        order by s.stock_code
        """,
        tuple(codes) + (market,),
    )


def _latest_activity(conn: sqlite3.Connection) -> str | None:
    candidates: list[str] = []
    for table_name, column in (
        ("account", "updated_at"),
        ("portfolio_snapshots", "created_at"),
        ("positions", "updated_at"),
        ("trades", "created_at"),
        ("order_attempts", "created_at"),
        ("signal_events", "created_at"),
        ("pending_exits", "updated_at"),
        ("disciplined_signals", "completed_at"),
    ):
        if _table_exists(conn, table_name):
            value = _scalar(conn, f"select max({column}) from {table_name}")
            if value:
                candidates.append(str(value))
    return max(candidates) if candidates else None


def _return_rate(account: dict[str, Any] | None, snapshot: dict[str, Any] | None) -> float | None:
    if not account:
        return None
    initial_cash = account.get("initial_cash")
    if not initial_cash:
        return None
    current_value = snapshot.get("total_value") if snapshot else account.get("cash")
    if current_value is None:
        return None
    return (float(current_value) - float(initial_cash)) / float(initial_cash)


def _latest_log(paths: Iterable[Path]) -> dict[str, Any]:
    candidates = [path for path in paths if path.is_file()]
    if not candidates:
        return {"available": False, "path": None, "mtime": None, "tail": []}
    return _log_file(max(candidates, key=lambda path: path.stat().st_mtime))


def _log_file(path: Path, *, line_count: int = 12) -> dict[str, Any]:
    if not path.exists():
        return {"available": False, "path": str(path), "mtime": None, "tail": []}
    stat = path.stat()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return {
            "available": False,
            "path": str(path),
            "mtime": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
            "tail": [],
            "error": str(exc),
        }
    return {
        "available": True,
        "path": str(path),
        "mtime": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
        "size_bytes": stat.st_size,
        "tail": [_clip_line(line) for line in lines[-line_count:]],
    }


def _clip_line(line: str, *, max_chars: int = 1200) -> str:
    if len(line) <= max_chars:
        return line
    return f"{line[:max_chars]} ... [truncated {len(line) - max_chars} chars]"
