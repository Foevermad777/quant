#!/usr/bin/env python3
"""Verify a DSA daily run against analysis_history (the business ground truth).

The daily wrappers previously judged success by parsing "成功: N" lines out of
run logs; on 2026-07-09 that let a 0/5 day report status=ok. This script asks
the only question that matters: did this run write an analysis row for every
stock in the pool?

Exit codes: 0 = all stocks covered, 3 = gaps found, 2 = verification error.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import List, Sequence, Tuple


def parse_stocks(raw: str) -> List[str]:
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


def query_analyzed(db_path: Path, stocks: Sequence[str], since: str) -> List[str]:
    placeholders = ",".join("?" for _ in stocks)
    sql = (
        "select distinct upper(code) from analysis_history "
        "where (report_type is null or report_type != 'market_review') "
        "and created_at >= ? "
        f"and upper(code) in ({placeholders})"
    )
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(sql, (since, *stocks)).fetchall()
    finally:
        conn.close()
    return sorted({str(row[0]) for row in rows})


def verify(db_path: Path, stocks: Sequence[str], since: str) -> Tuple[List[str], List[str]]:
    analyzed = query_analyzed(db_path, stocks, since)
    analyzed_set = set(analyzed)
    missing = [code for code in stocks if code not in analyzed_set]
    return analyzed, missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, required=True, help="Path to stock_analysis.db")
    parser.add_argument("--stocks", required=True, help="Comma-separated stock pool for this run")
    parser.add_argument(
        "--since",
        required=True,
        help="Local timestamp 'YYYY-MM-DD HH:MM:SS'; only rows created at/after it count as this run's output",
    )
    parser.add_argument("--output", type=Path, help="Optional JSON status file")
    args = parser.parse_args()

    stocks = parse_stocks(args.stocks)
    if not stocks:
        print("db_verify status=error reason=empty_stock_pool")
        return 2

    try:
        analyzed, missing = verify(args.db, stocks, args.since)
    except sqlite3.Error as exc:
        print(f"db_verify status=error reason=sqlite error={exc}")
        return 2

    status = "ok" if not missing else "missing"
    result = {
        "status": status,
        "since": args.since,
        "expected": len(stocks),
        "analyzed": analyzed,
        "analyzed_count": len(analyzed),
        "missing": missing,
        "missing_count": len(missing),
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temp_path = args.output.with_suffix(args.output.suffix + ".tmp")
        temp_path.write_text(json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n", encoding="utf-8")
        temp_path.replace(args.output)
    print(
        "db_verify "
        f"status={status} expected={len(stocks)} analyzed={len(analyzed)} "
        f"missing={','.join(missing) if missing else 'none'} since={args.since!r}"
    )
    return 0 if not missing else 3


if __name__ == "__main__":
    sys.exit(main())
