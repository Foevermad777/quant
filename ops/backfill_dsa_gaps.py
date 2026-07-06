#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, List, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DSA_DIR = PROJECT_ROOT / "vendor" / "daily_stock_analysis"
DSA_ENV = DSA_DIR / ".env"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(DSA_DIR) not in sys.path:
    sys.path.insert(0, str(DSA_DIR))

os.environ.setdefault("ENV_FILE", str(DSA_ENV))

from executor.config import ExecutorConfig  # noqa: E402
from executor.signal_reader import parse_date  # noqa: E402
from src.config import setup_env  # noqa: E402
from src.core.pipeline import StockAnalysisPipeline  # noqa: E402


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _latest_trading_date(db_path: Path) -> date | None:
    with _connect_ro(db_path) as conn:
        row = conn.execute("select max(date) as max_date from stock_daily").fetchone()
    return parse_date(row["max_date"] if row else None)


def _recent_observed_dates(db_path: Path, through: date, days: int) -> List[date]:
    with _connect_ro(db_path) as conn:
        rows = conn.execute(
            """
            select distinct date
            from stock_daily
            where date <= ?
            order by date desc
            limit ?
            """,
            (through.isoformat(), days),
        ).fetchall()
    dates = [parsed for row in rows if (parsed := parse_date(row["date"])) is not None]
    if through not in dates:
        dates.append(through)
    return sorted(dates)


def _present_codes(db_path: Path, target_date: date) -> set[str]:
    with _connect_ro(db_path) as conn:
        rows = conn.execute(
            "select distinct code from stock_daily where date = ?",
            (target_date.isoformat(),),
        ).fetchall()
    return {str(row["code"]) for row in rows}


def _missing_codes(db_path: Path, stock_pool: Sequence[str], target_date: date) -> List[str]:
    present = _present_codes(db_path, target_date)
    return [code for code in stock_pool if code not in present]


def _fetch_missing_codes(codes: Iterable[str], target_date: date, sleep_seconds: float) -> None:
    setup_env()
    pipeline = StockAnalysisPipeline(max_workers=1, query_source="dsa_gap_backfill")
    current_time = datetime.combine(target_date, datetime.min.time()).replace(hour=18)
    for index, code in enumerate(codes):
        if index > 0 and sleep_seconds > 0:
            time.sleep(sleep_seconds)
        ok, error = pipeline.fetch_and_save_stock_data(
            code,
            force_refresh=True,
            current_time=current_time,
        )
        status = "ok" if ok else "failed"
        print(f"backfill_fetch date={target_date.isoformat()} code={code} status={status} error={error or ''}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask DSA to refill recent stock_daily gaps for the executor stock pool.")
    parser.add_argument("--date", dest="target_date", help="Target date YYYY-MM-DD. Defaults to latest stock_daily date.")
    parser.add_argument("--days", type=int, default=10, help="Recent observed trading dates to scan.")
    parser.add_argument("--sleep-seconds", type=float, default=8.0, help="Pause between stock fetches to avoid dense retries.")
    parser.add_argument("--stock", dest="stocks", action="append", help="Override stock pool; repeat for multiple codes.")
    args = parser.parse_args()

    config = ExecutorConfig()
    target_date = parse_date(args.target_date) if args.target_date else _latest_trading_date(config.dsa_db_path)
    if target_date is None:
        print("backfill_abort reason=no_stock_daily_dates")
        return 1

    stock_pool = tuple(args.stocks) if args.stocks else tuple(config.stock_pool)
    scan_days = max(1, int(args.days))
    dates = _recent_observed_dates(config.dsa_db_path, target_date, scan_days)
    print(f"backfill_scan through={target_date.isoformat()} days={scan_days} stocks={','.join(stock_pool)}")

    for trading_date in dates:
        missing = _missing_codes(config.dsa_db_path, stock_pool, trading_date)
        if not missing:
            print(f"backfill_gap date={trading_date.isoformat()} missing=none")
            continue
        print(f"backfill_gap date={trading_date.isoformat()} missing={','.join(missing)}")
        _fetch_missing_codes(missing, trading_date, args.sleep_seconds)
        still_missing = _missing_codes(config.dsa_db_path, stock_pool, trading_date)
        print(
            f"backfill_result date={trading_date.isoformat()} "
            f"remaining={','.join(still_missing) if still_missing else 'none'}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
