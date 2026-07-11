#!/usr/bin/env python3
"""Prepare one persisted, auditable market context for an isolated DSA batch."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


BLOCKED_EXIT_CODE = 68
PROJECT_DIR = Path(__file__).resolve().parents[1]
VENDOR_DIR = PROJECT_DIR / "vendor" / "daily_stock_analysis"
logger = logging.getLogger("prepare_dsa_market_context")


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)


def _safe_run_id(value: Optional[str]) -> str:
    raw = value or f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:8]}"
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_")
    return (normalized or uuid.uuid4().hex[:12])[:32]


def _digest(value: Any) -> str:
    text = str(value or "").strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""


def _context_payload(context: Any, *, action: str) -> dict[str, Any]:
    history_id = getattr(context, "history_id", None)
    query_id = str(getattr(context, "query_id", None) or "").strip()
    if not isinstance(history_id, int) or history_id <= 0 or not query_id:
        raise RuntimeError("prepared market context is not backed by persisted history")
    trade_date = getattr(context, "trade_date", None)
    return {
        "status": "ok",
        "action": action,
        "region": str(getattr(context, "region", "") or ""),
        "trade_date": trade_date.isoformat() if trade_date is not None else "",
        "history_id": history_id,
        "query_id": query_id,
        "source": str(getattr(context, "source", "") or ""),
        "summary_sha256": _digest(getattr(context, "summary", "")),
        "full_report_sha256": _digest(getattr(context, "full_report", "")),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _load_exact_context(
    service_cls: Any,
    *,
    db: Any,
    region: str,
    target_date: Any,
    query_id: str,
    config: Any,
    notifier: Any,
    analyzer: Any,
    search_service: Any,
) -> Any:
    reader = service_cls(db_manager=db)
    return reader.get_context(
        region=region,
        config=config,
        notifier=notifier,
        analyzer=analyzer,
        search_service=search_service,
        allow_generate=False,
        target_date=target_date,
        current_query_id=query_id,
        require_query_id_match=True,
    )


def prepare_context(args: argparse.Namespace) -> dict[str, Any]:
    os.chdir(VENDOR_DIR)
    vendor_path = str(VENDOR_DIR)
    if vendor_path not in sys.path:
        sys.path.insert(0, vendor_path)

    import main as dsa_main
    from src.config import get_config
    from src.core.market_review import run_market_review
    from src.core.market_review_lock import (
        release_market_review_lock,
        try_acquire_market_review_lock,
    )
    from src.core.market_review_runtime import build_market_review_runtime
    from src.core.trading_calendar import (
        get_effective_trading_date,
        get_open_markets_today,
    )
    from src.services.daily_market_context import DailyMarketContextService
    from src.storage import DatabaseManager

    dsa_main._bootstrap_environment()
    config = get_config()
    target_date = get_effective_trading_date(
        args.region,
        current_time=datetime.now(timezone.utc),
    )
    if args.skip_closed_market and args.region not in get_open_markets_today():
        return {
            "status": "skipped",
            "action": "market_closed",
            "region": args.region,
            "trade_date": target_date.isoformat(),
            "history_id": None,
            "query_id": None,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    db = DatabaseManager.get_instance()
    service = DailyMarketContextService(db_manager=db)
    existing = service.load_persisted_context(
        region=args.region,
        target_date=target_date,
        report_language=getattr(config, "report_language", "zh"),
        query_id_marker=f"shared_market_{args.region}_",
    )
    if existing is not None and not args.force_refresh:
        try:
            return _context_payload(existing, action="reused")
        except RuntimeError:
            logger.warning("Existing context is not auditable; generating a persisted replacement")

    notifier, analyzer, search_service = build_market_review_runtime(config)
    run_id = _safe_run_id(args.run_id)
    query_id = f"shared_market_{args.region}_{run_id}"
    lock_token = try_acquire_market_review_lock(config)
    if lock_token is not None:
        try:
            result = run_market_review(
                config=config,
                notifier=notifier,
                analyzer=analyzer,
                search_service=search_service,
                send_notification=args.notify,
                merge_notification=False,
                override_region=args.region,
                query_id=query_id,
                return_structured=True,
                save_report_file=True,
                persist_history=True,
                trigger_source="daily_shared_market_context",
                context_trade_date=target_date,
            )
        finally:
            release_market_review_lock(lock_token)
        if result is None:
            raise RuntimeError("market review returned no persisted result")
        persisted = _load_exact_context(
            DailyMarketContextService,
            db=db,
            region=args.region,
            target_date=target_date,
            query_id=query_id,
            config=config,
            notifier=notifier,
            analyzer=analyzer,
            search_service=search_service,
        )
    else:
        generated = service.get_context(
            region=args.region,
            config=config,
            notifier=notifier,
            analyzer=analyzer,
            search_service=search_service,
            force_refresh=args.force_refresh,
            allow_generate=True,
            persist_market_review_history=True,
            target_date=target_date,
            current_query_id=query_id,
            require_query_id_match=True,
        )
        if generated is not None and getattr(generated, "source", "") == "analysis_history":
            persisted = generated
        else:
            persisted = _load_exact_context(
                DailyMarketContextService,
                db=db,
                region=args.region,
                target_date=target_date,
                query_id=f"market_context_{query_id}_{args.region}",
                config=config,
                notifier=notifier,
                analyzer=analyzer,
                search_service=search_service,
            )

    if persisted is None:
        raise RuntimeError("market context was generated but could not be reloaded from history")
    return _context_payload(persisted, action="generated")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare one shared DSA market context.")
    parser.add_argument("--region", choices=("cn", "us"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--skip-closed-market", action="store_true")
    parser.add_argument("--notify", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    try:
        payload = prepare_context(args)
        exit_code = 0
    except Exception as exc:
        logger.exception("Shared market context preparation failed")
        payload = {
            "status": "blocked",
            "action": "prepare_failed",
            "region": args.region,
            "error_type": type(exc).__name__,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        exit_code = BLOCKED_EXIT_CODE

    _atomic_write(args.output, payload)
    print(
        "market_context "
        f"status={payload.get('status')} action={payload.get('action')} "
        f"region={payload.get('region')} trade_date={payload.get('trade_date', '')} "
        f"history_id={payload.get('history_id') or 'none'} "
        f"query_id={payload.get('query_id') or 'none'} output={args.output}"
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
