"""Shadow evaluation of the corrected intent taxonomy (2026-07-21).

Records, per execution date, what the corrected conflict-status taxonomy
(`intent_resolution.corrected_conflict_status`) WOULD have promoted to a
resting limit plan and whether that plan would have filled — without touching
production selection or the ledger. Feeds the >=10-trading-day evaluation
gating the v2 taxonomy cutover (see PROJECT_LOG 2026-07-21).

Reuses the real per-market readers (S1 gate, `_latest_by_symbol`) and the real
`LimitFillModel` so the recorded counterfactual walks the exact production
code path. Writes only to its own `shadow_intent_decisions` table inside the
market's paper db; CN and US stay fully isolated (separate stores, separate
runs), mirroring the executor layout.

CLI:
    python -m executor.shadow_intent --market cn                # today's probe
    python -m executor.shadow_intent --market us --date 2026-07-20
    python -m executor.shadow_intent --market cn --start 2026-07-14 --end 2026-07-21 --mode backfill
    python -m executor.shadow_intent --market cn --report
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from executor.intent_resolution import action_group, corrected_conflict_status

SCHEMA_VERSION = "shadow-intent-v1"

_CREATE_TABLE = """
create table if not exists shadow_intent_decisions (
    id integer primary key autoincrement,
    run_date text not null,
    market text not null,
    signal_id integer not null,
    stock_code text not null,
    source_status text not null,
    shadow_status text not null,
    reclassified integer not null,
    in_production integer not null,
    promoted integer not null,
    drop_reason text,
    limit_price real,
    stop_loss real,
    target_price real,
    expires_at text,
    bar_open real,
    bar_low real,
    fill_status text,
    fill_reason text,
    fill_price real,
    recorded_mode text not null,
    schema_version text not null,
    details_json text not null,
    created_at text not null,
    unique(run_date, signal_id)
);
"""


@dataclass(frozen=True)
class MarketContext:
    market: str
    reader: Any
    fill_model: Any
    store_db_path: Path


def _cn_context(dsa_db: Optional[Path], store_db: Optional[Path]) -> MarketContext:
    from executor.config import ExecutorConfig
    from executor.models import LimitFillModel
    from executor.signal_reader import SignalReader

    cfg = ExecutorConfig()
    dsa_path = dsa_db or cfg.dsa_db_path
    store_path = store_db or (cfg.disciplined_db_path or cfg.ledger_db_path)
    reader = SignalReader(
        dsa_path,
        store_path,
        stock_pool=cfg.stock_pool,
        market=cfg.market,
        use_disciplined_signals=cfg.use_disciplined_signals,
    )
    return MarketContext("cn", reader, LimitFillModel(), Path(store_path))


def _us_context(dsa_db: Optional[Path], store_db: Optional[Path]) -> MarketContext:
    from executor.us.config_us import UsExecutorConfig
    from executor.us.models_us import LimitFillModel
    from executor.us.signal_reader_us import UsSignalReader

    cfg = UsExecutorConfig()
    dsa_path = dsa_db or cfg.dsa_db_path
    store_path = store_db or cfg.disciplined_db_path
    reader = UsSignalReader(
        dsa_path,
        store_path,
        stock_pool=cfg.stock_pool,
        market=cfg.market,
        use_disciplined_signals=cfg.use_disciplined_signals,
    )
    return MarketContext("us", reader, LimitFillModel(), Path(store_path))


def build_context(market: str, *, dsa_db: Optional[Path] = None, store_db: Optional[Path] = None) -> MarketContext:
    if market == "cn":
        return _cn_context(dsa_db, store_db)
    if market == "us":
        return _us_context(dsa_db, store_db)
    raise ValueError(f"unknown market: {market!r}")


def _held_symbols(store_db_path: Path, as_of: date) -> set[str]:
    """Point-in-time held set, rebuilt from the trades ledger.

    Reading the positions table would leak TODAY's holdings into backfilled
    dates (and silently drop their candidates), so holdings are reconstructed
    as of the execution date: trades strictly before it — the engine computes
    held_symbols before applying the day's fills.
    """
    try:
        with sqlite3.connect(f"file:{store_db_path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                select stock_code,
                       sum(case when side = 'buy' then shares else -shares end) as qty
                from trades
                where trade_date < ?
                group by stock_code
                having qty > 0
                """,
                (as_of.isoformat(),),
            ).fetchall()
    except sqlite3.Error:
        return set()
    return {str(row["stock_code"]) for row in rows}


def _shadow_status(signal: Any, advice: Any) -> str:
    has_plan = (
        signal.entry_high is not None
        and signal.entry_high > 0
        and signal.expires_at is not None
    )
    return corrected_conflict_status(
        conflict_status=advice.conflict_status,
        conflict_reason=advice.conflict_reason,
        signal_action=signal.action,
        flat_account_action=advice.flat_account_action,
        has_executable_entry_plan=has_plan,
    )


def evaluate_day(context: MarketContext, execution_date: date, *, now_iso: str, mode: str) -> List[Dict[str, Any]]:
    """Compute per-signal shadow decisions for one execution date."""
    reader = context.reader
    held = _held_symbols(context.store_db_path, execution_date)
    production_ids = {signal.id for signal in reader.open_candidates(execution_date, held_symbols=held)}

    considered: List[Dict[str, Any]] = []
    shadow_promotable: Dict[str, Any] = {}
    for signal in reader.active_signals_before(execution_date):
        advice = reader.advice_for_signal(signal, has_position=signal.stock_code in held)
        shadow_status = _shadow_status(signal, advice)
        reclassified = shadow_status != advice.conflict_status
        in_production = signal.id in production_ids

        drop_reason = None
        promotable = False
        if in_production:
            promotable = True
        elif shadow_status == "conditional_entry" and action_group(signal.action) == "entry":
            if signal.stock_code in held:
                drop_reason = "symbol_held"
            else:
                promotable = True
        elif reclassified:
            drop_reason = "reclassified_not_promotable"

        if not (reclassified or promotable or in_production):
            continue

        entry = {
            "signal": signal,
            "advice": advice,
            "shadow_status": shadow_status,
            "reclassified": reclassified,
            "in_production": in_production,
            "promotable": promotable,
            "drop_reason": drop_reason,
        }
        considered.append(entry)
        if promotable:
            current = shadow_promotable.get(signal.stock_code)
            if current is None or _signal_order_key(signal) > _signal_order_key(current["signal"]):
                shadow_promotable[signal.stock_code] = entry

    promoted_ids = {entry["signal"].id for entry in shadow_promotable.values()}
    rows: List[Dict[str, Any]] = []
    for entry in considered:
        signal = entry["signal"]
        promoted = signal.id in promoted_ids
        if entry["promotable"] and not promoted:
            entry["drop_reason"] = "superseded_by_newer_signal"

        bar = reader.bar(signal.stock_code, execution_date)
        fill_status = fill_reason = fill_price = None
        if promoted:
            fill = context.fill_model.buy_fill(signal, bar)
            fill_status, fill_reason = fill.status, fill.reason
            fill_price = fill.price
        rows.append(
            {
                "run_date": execution_date.isoformat(),
                "market": context.market,
                "signal_id": signal.id,
                "stock_code": signal.stock_code,
                "source_status": advice_status(entry["advice"]),
                "shadow_status": entry["shadow_status"],
                "reclassified": int(entry["reclassified"]),
                "in_production": int(entry["in_production"]),
                "promoted": int(promoted),
                "drop_reason": entry["drop_reason"],
                "limit_price": signal.entry_high,
                "stop_loss": signal.stop_loss,
                "target_price": signal.target_price,
                "expires_at": signal.expires_at.isoformat(sep=" ") if signal.expires_at else None,
                "bar_open": getattr(bar, "open", None),
                "bar_low": getattr(bar, "low", None),
                "fill_status": fill_status,
                "fill_reason": fill_reason,
                "fill_price": fill_price,
                "recorded_mode": mode,
                "schema_version": SCHEMA_VERSION,
                "details_json": json.dumps(
                    {
                        "signal_action": signal.action,
                        "flat_account_action": entry["advice"].flat_account_action,
                        "conflict_reason": (entry["advice"].conflict_reason or "")[:300],
                    },
                    ensure_ascii=False,
                ),
                "created_at": now_iso,
            }
        )
    return rows


def advice_status(advice: Any) -> str:
    return str(advice.conflict_status or "unknown")


def _signal_order_key(signal: Any) -> tuple:
    from datetime import datetime

    return (signal.created_at or datetime.min, signal.id)


def record_rows(store_db_path: Path, rows: Sequence[Dict[str, Any]]) -> int:
    with sqlite3.connect(store_db_path) as conn:
        conn.execute(_CREATE_TABLE)
        for row in rows:
            conn.execute(
                """
                insert or replace into shadow_intent_decisions (
                    run_date, market, signal_id, stock_code, source_status, shadow_status,
                    reclassified, in_production, promoted, drop_reason, limit_price,
                    stop_loss, target_price, expires_at, bar_open, bar_low,
                    fill_status, fill_reason, fill_price, recorded_mode, schema_version,
                    details_json, created_at
                ) values (
                    :run_date, :market, :signal_id, :stock_code, :source_status, :shadow_status,
                    :reclassified, :in_production, :promoted, :drop_reason, :limit_price,
                    :stop_loss, :target_price, :expires_at, :bar_open, :bar_low,
                    :fill_status, :fill_reason, :fill_price, :recorded_mode, :schema_version,
                    :details_json, :created_at
                )
                """,
                row,
            )
    return len(rows)


def _first_fill_rows(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """One row per (signal, first filled day): resting plans log daily, count fills once."""
    return conn.execute(
        """
        select * from shadow_intent_decisions d
        where fill_status = 'filled'
          and not exists (
            select 1 from shadow_intent_decisions earlier
            where earlier.signal_id = d.signal_id
              and earlier.fill_status = 'filled'
              and earlier.run_date < d.run_date
          )
        order by run_date, stock_code
        """
    ).fetchall()


def build_report(context: MarketContext, *, knife_days: int = 2) -> Dict[str, Any]:
    with sqlite3.connect(f"file:{context.store_db_path}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        try:
            totals = conn.execute(
                """
                select recorded_mode,
                       count(distinct run_date) as days,
                       sum(reclassified) as reclassified,
                       sum(promoted) as promoted,
                       sum(case when promoted = 1 and in_production = 0 then 1 else 0 end) as shadow_only,
                       sum(case when fill_status = 'filled' then 1 else 0 end) as fill_rows
                from shadow_intent_decisions
                group by recorded_mode
                """
            ).fetchall()
            fills = _first_fill_rows(conn)
        except sqlite3.OperationalError:
            return {"available": False, "market": context.market}

    reader = context.reader
    fill_details = []
    knives = 0
    for row in fills:
        fill_day = date.fromisoformat(row["run_date"])
        stop = row["stop_loss"]
        target = row["target_price"]
        outcome, outcome_day, days_checked = "open", None, 0
        probe_day = fill_day
        for _ in range(30):
            probe_day = _next_bar_date(reader, row["stock_code"], probe_day)
            if probe_day is None:
                break
            bar = reader.bar(row["stock_code"], probe_day)
            days_checked += 1
            if stop is not None and bar.low is not None and bar.low <= stop:
                outcome, outcome_day = "stop_loss", probe_day.isoformat()
                break
            if target is not None and bar.high is not None and bar.high >= target:
                outcome, outcome_day = "take_profit", probe_day.isoformat()
                break
        is_knife = outcome == "stop_loss" and days_checked <= knife_days
        knives += int(is_knife)
        last_close = _latest_close(reader, row["stock_code"])
        fill_details.append(
            {
                "signal_id": row["signal_id"],
                "stock_code": row["stock_code"],
                "fill_date": row["run_date"],
                "fill_price": row["fill_price"],
                "in_production": bool(row["in_production"]),
                "outcome": outcome,
                "outcome_day": outcome_day,
                "knife": is_knife,
                "mark_return_pct": (
                    round((last_close / row["fill_price"] - 1) * 100, 2)
                    if last_close and row["fill_price"] else None
                ),
            }
        )
    return {
        "available": True,
        "market": context.market,
        "totals_by_mode": [dict(row) for row in totals],
        "fills": fill_details,
        "fill_count": len(fill_details),
        "knife_count": knives,
        "knife_rate": round(knives / len(fill_details), 3) if fill_details else None,
    }


def _next_bar_date(reader: Any, stock_code: str, after: date) -> Optional[date]:
    with reader._connect() as conn:  # noqa: SLF001 - same read-only handle the reader uses
        row = conn.execute(
            "select min(date) as d from stock_daily where code = ? and date > ?",
            (stock_code, after.isoformat()),
        ).fetchone()
    value = row["d"] if row else None
    return date.fromisoformat(str(value)[:10]) if value else None


def _latest_close(reader: Any, stock_code: str) -> Optional[float]:
    with reader._connect() as conn:  # noqa: SLF001
        row = conn.execute(
            "select close from stock_daily where code = ? order by date desc limit 1",
            (stock_code,),
        ).fetchone()
    return float(row["close"]) if row and row["close"] is not None else None


def _trading_dates(reader: Any, start: date, end: date) -> List[date]:
    with reader._connect() as conn:  # noqa: SLF001
        rows = conn.execute(
            "select distinct date from stock_daily where date >= ? and date <= ? order by date",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    return [date.fromisoformat(str(row[0])[:10]) for row in rows]


def main(argv: Optional[Sequence[str]] = None) -> int:
    from datetime import datetime

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=("cn", "us"), required=True)
    parser.add_argument("--date", help="Execution date YYYY-MM-DD. Defaults to latest DSA stock_daily date (same as the engine).")
    parser.add_argument("--start", help="Backfill range start YYYY-MM-DD (inclusive). Requires --end.")
    parser.add_argument("--end", help="Backfill range end YYYY-MM-DD (inclusive).")
    parser.add_argument("--mode", choices=("live", "backfill"), default=None, help="Recorded mode tag. Defaults: live for --date/today, backfill for --start/--end.")
    parser.add_argument("--report", action="store_true", help="Print the shadow evaluation report as JSON and exit.")
    parser.add_argument("--dsa-db", type=Path, default=None)
    parser.add_argument("--store-db", type=Path, default=None)
    args = parser.parse_args(argv)

    context = build_context(args.market, dsa_db=args.dsa_db, store_db=args.store_db)

    if args.report:
        print(json.dumps(build_report(context), ensure_ascii=False, indent=2))
        return 0

    if args.start or args.end:
        if not (args.start and args.end):
            raise SystemExit("--start and --end must be provided together")
        days = _trading_dates(context.reader, date.fromisoformat(args.start), date.fromisoformat(args.end))
        mode = args.mode or "backfill"
    else:
        if args.date:
            day = date.fromisoformat(args.date)
        else:
            day = context.reader.latest_trading_date()
        if day is None:
            raise SystemExit("no trading date available in stock_daily")
        days = [day]
        mode = args.mode or "live"

    now_iso = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    total = 0
    for day in days:
        rows = evaluate_day(context, day, now_iso=now_iso, mode=mode)
        total += record_rows(context.store_db_path, rows)
        promoted = sum(row["promoted"] for row in rows)
        shadow_only = sum(1 for row in rows if row["promoted"] and not row["in_production"])
        filled = sum(1 for row in rows if row["fill_status"] == "filled")
        print(
            f"shadow_intent market={context.market} date={day} rows={len(rows)} "
            f"promoted={promoted} shadow_only={shadow_only} filled={filled} mode={mode}"
        )
    print(f"shadow_intent market={context.market} recorded_rows={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
