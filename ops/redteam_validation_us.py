#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from executor.us.config_us import ACCEPTANCE_DIR, UsExecutorConfig
from executor.us.redteam_validation import render_markdown, run_validation, write_report
from executor.us.signal_reader_us import parse_date


def _date_arg(text: str) -> date:
    parsed = parse_date(text)
    if parsed is None:
        raise argparse.ArgumentTypeError(f"invalid date: {text}")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run US R1-R3 red-team validation gates.")
    parser.add_argument("--oos-start", type=_date_arg, default=date(2024, 1, 1))
    parser.add_argument("--oos-end", type=_date_arg, default=date(2025, 12, 31))
    parser.add_argument("--review-start", type=_date_arg)
    parser.add_argument("--review-end", type=_date_arg)
    parser.add_argument("--train-days", type=int, default=60)
    parser.add_argument("--test-days", type=int, default=20)
    parser.add_argument("--liquidity-impact-bps", type=float, default=0.0)
    parser.add_argument("--migrate-temporal-metadata", action="store_true")
    parser.add_argument("--dsa-db", type=Path)
    parser.add_argument("--ledger-db", type=Path)
    parser.add_argument("--oos-stock-code", action="append", dest="oos_stock_pool")
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    base_config = UsExecutorConfig()
    config = UsExecutorConfig(
        dsa_db_path=args.dsa_db or base_config.dsa_db_path,
        ledger_db_path=args.ledger_db or base_config.ledger_db_path,
        disciplined_db_path=args.ledger_db or base_config.disciplined_db_path,
        use_disciplined_signals=base_config.use_disciplined_signals,
        initial_cash=base_config.initial_cash,
        per_signal_cash=base_config.per_signal_cash,
        symbol_cap_rate=base_config.symbol_cap_rate,
        lot_size=base_config.lot_size,
        fill_model=base_config.fill_model,
        slippage_rate=base_config.slippage_rate,
        open_slippage_multiplier=base_config.open_slippage_multiplier,
        commission_per_share=base_config.commission_per_share,
        commission_rate=base_config.commission_rate,
        min_commission=base_config.min_commission,
        sec_fee_rate=base_config.sec_fee_rate,
        reduce_exit_rate=base_config.reduce_exit_rate,
        benchmark_codes=base_config.benchmark_codes,
        stock_pool=base_config.stock_pool,
        market=base_config.market,
        t_plus=base_config.t_plus,
        bar_available_time=base_config.bar_available_time,
        bar_available_timezone=base_config.bar_available_timezone,
        honor_luld=base_config.honor_luld,
    )
    result = run_validation(
        config,
        oos_start=args.oos_start,
        oos_end=args.oos_end,
        review_start=args.review_start,
        review_end=args.review_end,
        train_days=max(1, args.train_days),
        test_days=max(1, args.test_days),
        liquidity_impact_bps=max(0.0, args.liquidity_impact_bps),
        migrate_temporal_metadata=args.migrate_temporal_metadata,
        oos_stock_pool=tuple(args.oos_stock_pool) if args.oos_stock_pool else None,
    )
    if args.output:
        output = write_report(args.output, result)
        print(output)
    else:
        default_output = ACCEPTANCE_DIR / f"US_R1_R3_REDTEAM_VALIDATION_{date.today():%Y%m%d}.md"
        output = write_report(default_output, result)
        print(output)
        print()
        print(render_markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
