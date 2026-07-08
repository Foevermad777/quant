#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from executor.config import DSA_DB_PATH
from executor.signal_reader import parse_date
from executor.us.oos_builder_us import (
    DEFAULT_US_R1_CODES,
    US_OOS_DSA_DB_PATH,
    US_OOS_IMPORT_REPORT_DIR,
    US_R1_REQUIRED_OOS_END,
    build_us_oos_database,
    render_us_oos_import_report,
    write_us_oos_import_report,
)


def _date_arg(text: str) -> date:
    parsed = parse_date(text)
    if parsed is None:
        raise argparse.ArgumentTypeError(f"invalid date: {text}")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill an isolated US OOS database for R1 validation.")
    parser.add_argument("--db", type=Path, default=US_OOS_DSA_DB_PATH)
    parser.add_argument("--source-db", type=Path, default=DSA_DB_PATH)
    parser.add_argument("--start", type=_date_arg, default=date(2024, 1, 1))
    parser.add_argument("--end", type=_date_arg, default=US_R1_REQUIRED_OOS_END)
    parser.add_argument("--stock-code", action="append", dest="codes")
    parser.add_argument(
        "--price-provider",
        choices=("yahoochart", "stooq", "yfinance", "alphavantage", "none"),
        default="yahoochart",
    )
    parser.add_argument("--news-provider", choices=("tavily", "alphavantage", "yfinance", "none"), default="tavily")
    parser.add_argument("--alpha-vantage-key-path", type=Path)
    parser.add_argument("--tavily-key-path", type=Path)
    parser.add_argument("--skip-bars", action="store_true")
    parser.add_argument("--skip-news", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    codes = tuple(args.codes or DEFAULT_US_R1_CODES)
    summary = build_us_oos_database(
        db_path=args.db,
        source_db_path=args.source_db,
        start=args.start,
        end=args.end,
        codes=codes,
        price_provider=args.price_provider,
        news_provider=args.news_provider,
        alpha_vantage_key_path=args.alpha_vantage_key_path,
        tavily_key_path=args.tavily_key_path,
        fetch_bars=not args.skip_bars,
        fetch_news=not args.skip_news,
    )
    output = args.output or US_OOS_IMPORT_REPORT_DIR / f"US_OOS_IMPORT_REPORT_{date.today():%Y%m%d}.md"
    write_us_oos_import_report(output, summary)
    print(output)
    print()
    print(render_us_oos_import_report(summary))
    return 0 if summary.bars_ready and summary.news_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
