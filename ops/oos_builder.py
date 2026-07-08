#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from executor.config import DSA_DB_PATH, RUNTIME_DIR
from executor.oos_builder import (
    OOS_DSA_DB_PATH,
    OOS_IMPORT_REPORT_DIR,
    build_oos_database,
    render_oos_import_report,
    write_oos_import_report,
)
from executor.signal_reader import parse_date

TUSHARE_TOKEN_PATH = RUNTIME_DIR / "secrets" / "tushare_token.txt"


def _date_arg(text: str) -> date:
    parsed = parse_date(text)
    if parsed is None:
        raise argparse.ArgumentTypeError(f"invalid date: {text}")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an isolated OOS database for R1 validation.")
    parser.add_argument("--db", type=Path, default=OOS_DSA_DB_PATH)
    parser.add_argument("--source-db", type=Path, default=DSA_DB_PATH)
    parser.add_argument("--token-path", type=Path, default=TUSHARE_TOKEN_PATH)
    parser.add_argument("--start", type=_date_arg, default=date(2024, 1, 1))
    parser.add_argument("--end", type=_date_arg, default=date(2025, 3, 13))
    parser.add_argument("--stock-code", action="append", dest="codes")
    parser.add_argument("--skip-bars", action="store_true")
    parser.add_argument("--skip-announcements", action="store_true")
    parser.add_argument("--announcement-source", choices=("tushare", "akshare"), default="tushare")
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    from executor.config import ExecutorConfig

    codes = tuple(args.codes or ExecutorConfig().stock_pool)
    summary = build_oos_database(
        db_path=args.db,
        source_db_path=args.source_db,
        token_path=args.token_path,
        start=args.start,
        end=args.end,
        codes=codes,
        fetch_bars=not args.skip_bars,
        fetch_announcements=not args.skip_announcements,
        announcement_source=args.announcement_source,
    )
    output = args.output or OOS_IMPORT_REPORT_DIR / f"OOS_IMPORT_REPORT_{date.today():%Y%m%d}.md"
    write_oos_import_report(output, summary)
    print(output)
    print()
    print(render_oos_import_report(summary))
    return 0 if summary.bars_ready and summary.news_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
