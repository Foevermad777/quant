#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from executor.config import ExecutorConfig
from executor.oos_backtest_runner import (
    DEFAULT_OOS_END,
    DEFAULT_OOS_START,
    OOS_BACKTEST_REPORT_DIR,
    OOS_DSA_DB_PATH,
    RuleSignalClient,
    render_backtest_markdown,
    run_oos_backtest,
    write_backtest_report,
)
from executor.signal_reader import parse_date


def _date_arg(text: str) -> date:
    parsed = parse_date(text)
    if parsed is None:
        raise argparse.ArgumentTypeError(f"invalid date: {text}")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an isolated historical OOS backtest replay.")
    parser.add_argument("--db", type=Path, default=OOS_DSA_DB_PATH, help="OOS DSA-like SQLite database.")
    parser.add_argument("--ledger-db", type=Path, help="Output ledger DB. Must not already exist unless --force-ledger.")
    parser.add_argument("--report", type=Path, help="Markdown report output path.")
    parser.add_argument("--start", type=_date_arg, default=DEFAULT_OOS_START)
    parser.add_argument("--end", type=_date_arg, default=DEFAULT_OOS_END)
    parser.add_argument("--stock-code", action="append", dest="codes", help="Repeat to limit the stock pool.")
    parser.add_argument("--generate-signals", action="store_true", help="Generate OOS signals before replay.")
    parser.add_argument("--mock-signals", action="store_true", help="Use deterministic local signals instead of DeepSeek.")
    parser.add_argument("--force-signals", action="store_true", help="Generate even when OOS runner signals already exist.")
    parser.add_argument("--force-ledger", action="store_true", help="Replace an existing output ledger DB.")
    parser.add_argument("--max-days", type=int, help="Limit signal generation to the first N trading days.")
    parser.add_argument("--max-calls", type=int, help="Limit total LLM/mock signal calls.")
    parser.add_argument("--lookback-bars", type=int, default=60)
    parser.add_argument("--news-lookback-days", type=int, default=30)
    parser.add_argument("--expiry-trading-days", type=int, default=5)
    parser.add_argument("--sleep-seconds", type=float, default=0.0, help="Delay between generated signals.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    today = date.today()
    ledger_db = args.ledger_db or OOS_BACKTEST_REPORT_DIR / f"oos_backtest_{today:%Y%m%d}.db"
    report = args.report or OOS_BACKTEST_REPORT_DIR / f"OOS_BACKTEST_REPORT_{today:%Y%m%d}.md"
    codes = tuple(args.codes or ExecutorConfig().stock_pool)
    client = RuleSignalClient() if args.mock_signals else None
    result = run_oos_backtest(
        db_path=args.db,
        ledger_db_path=ledger_db,
        start=args.start,
        end=args.end,
        stock_pool=codes,
        signal_client=client,
        generate_signals=args.generate_signals or args.mock_signals,
        force_signals=args.force_signals,
        force_ledger=args.force_ledger,
        max_days=args.max_days,
        max_calls=args.max_calls,
        lookback_bars=args.lookback_bars,
        news_lookback_days=args.news_lookback_days,
        expiry_trading_days=args.expiry_trading_days,
        sleep_seconds=args.sleep_seconds,
    )
    output = write_backtest_report(report, result)
    print(output)
    print()
    print(render_backtest_markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
