from __future__ import annotations

import argparse
import hashlib
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Optional, Sequence

from executor.us.config_us import FILL_MODEL_NEXT_OPEN, QUANT_DIR, UsExecutorConfig
from executor.us.ledger_us import TradeFill, UsPaperLedger
from executor.us.models_us import DecisionSignal, NextOpenFillModel, SlippageModel, UsFeeModel
from executor.us.rules_us import (
    cap_order_shares,
    first_exit_trigger,
)
from executor.us.signal_reader_us import UsSignalReader, parse_date


def _setup_logger(execution_date: date, log_dir: Path = QUANT_DIR) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("executor.us")
    logger.setLevel(logging.INFO)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    path = log_dir / f"executor_us_{execution_date:%Y%m%d}.log"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    return logger


def md5_file(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_fill_model(name: str) -> object:
    if name == FILL_MODEL_NEXT_OPEN:
        return NextOpenFillModel()
    raise ValueError(f"unsupported fill_model: {name}")


class UsPaperEngine:
    def __init__(self, config: Optional[UsExecutorConfig] = None) -> None:
        self.config = config or UsExecutorConfig()
        disciplined_db_path = self.config.disciplined_db_path or self.config.ledger_db_path
        self.reader = UsSignalReader(
            self.config.dsa_db_path,
            disciplined_db_path,
            stock_pool=self.config.stock_pool,
            market=self.config.market,
            use_disciplined_signals=self.config.use_disciplined_signals,
        )
        self.ledger = UsPaperLedger(self.config.ledger_db_path, config=self.config)
        self.fill_model = _build_fill_model(self.config.fill_model)
        self.slippage = SlippageModel(self.config.slippage_rate)
        self.fees = UsFeeModel(
            commission_per_share=self.config.commission_per_share,
            commission_rate=self.config.commission_rate,
            min_commission=self.config.min_commission,
            sec_fee_rate=self.config.sec_fee_rate,
        )

    def run_day(self, execution_date: date) -> Dict[str, int]:
        logger = _setup_logger(execution_date, self.config.ledger_db_path.parent)
        self.ledger.initialize()
        before_md5 = md5_file(self.config.dsa_db_path) if self.config.dsa_db_path.exists() else ""
        analysis_count = self.reader.analysis_count_on(execution_date)
        bars = self.reader.bars_on(execution_date)
        stats = {
            "analysis_count": analysis_count,
            "bars": len(bars),
            "data_gaps": 0,
            "s1_conflicts": 0,
            "open_candidates": 0,
            "exit_candidates": 0,
            "filled": 0,
            "unfilled": 0,
            "blocked": 0,
            "pending_exits": 0,
            "sells": 0,
        }

        logger.info("run_start execution_date=%s analysis_count=%s bars=%s", execution_date, analysis_count, len(bars))

        for signal, advice in self.reader.s1_conflicts(execution_date):
            stats["s1_conflicts"] += 1
            self.ledger.record_event(
                signal_id=signal.id,
                stock_code=signal.stock_code,
                event_date=execution_date,
                event_type="s1_conflict_skip",
                reason="advice_signal_action_mismatch",
                details={
                    "signal_action": signal.action,
                    "advice_action": advice.action,
                    "operation_advice": advice.operation_advice,
                    "source_report_id": advice.report_id,
                },
            )

        open_candidates = self.reader.open_candidates(execution_date)
        stats["open_candidates"] = len(open_candidates)
        self._record_data_gaps(execution_date, bars, stats)

        self._process_pending_exits(execution_date, logger, stats)
        self._process_position_triggers(execution_date, bars, logger, stats)
        self._process_exit_signals(execution_date, bars, logger, stats)

        if analysis_count <= 0 and not open_candidates:
            logger.info("new_openings_skipped reason=no_analysis_history_for_date")
        elif not open_candidates:
            logger.info("new_openings_skipped reason=no_open_candidates")
        else:
            # If the US bar is not persisted yet, rerun for the completed
            # execution_date after the DSA US batch lands the daily bar.
            if not bars:
                logger.info("new_openings_degraded reason=no_stock_daily_bars plan_b=next_day_0900")
            self._process_open_candidates(execution_date, bars, open_candidates, logger, stats)

        marks = {code: bar.close for code, bar in bars.items() if bar.close is not None}
        self.ledger.record_snapshot(execution_date, marks)
        self.ledger.settle_positions()

        after_md5 = md5_file(self.config.dsa_db_path) if self.config.dsa_db_path.exists() else ""
        logger.info("dsa_readonly_md5 before=%s after=%s unchanged=%s", before_md5, after_md5, before_md5 == after_md5)
        logger.info("run_done stats=%s", stats)
        return stats

    def backfill(self, start: date, end: Optional[date] = None) -> Dict[str, int]:
        latest = end or self.reader.latest_trading_date()
        totals: Dict[str, int] = {}
        if latest is None:
            return totals
        trading_dates = self.reader.trading_dates(start, latest)
        if not trading_dates and start <= latest:
            trading_dates = [start]
        for trading_day in trading_dates:
            stats = self.run_day(trading_day)
            for key, value in stats.items():
                totals[key] = totals.get(key, 0) + int(value)
        return totals

    def _process_open_candidates(
        self,
        execution_date: date,
        bars: Dict[str, object],
        candidates: Sequence[DecisionSignal],
        logger: logging.Logger,
        stats: Dict[str, int],
    ) -> None:
        for signal in candidates:
            if signal.expires_at is not None and execution_date > signal.expires_at.date():
                blocked = self.fill_model.expired_unfilled(signal, execution_date)
                self.ledger.record_order_attempt(
                    signal_id=signal.id,
                    stock_code=signal.stock_code,
                    trade_date=execution_date,
                    status=blocked.status,
                    reason=blocked.reason,
                )
                stats["blocked"] += 1
                continue

            bar = bars.get(signal.stock_code)
            fill = self.fill_model.buy_fill(signal, bar)
            if not fill.filled or fill.price is None:
                self.ledger.record_order_attempt(
                    signal_id=signal.id,
                    stock_code=signal.stock_code,
                    trade_date=execution_date,
                    status=fill.status,
                    reason=fill.reason,
                    price=fill.price,
                )
                stats["unfilled"] += 1
                continue

            exec_price = self.slippage.execution_price(
                fill.price,
                "buy",
                multiplier=self.config.open_slippage_multiplier,
            )
            portfolio_value = self._portfolio_value(bars)
            current_value = self._current_symbol_value(signal.stock_code, bars)
            shares = cap_order_shares(
                target_cash=min(self.config.per_signal_cash, self.ledger.get_cash()),
                price=exec_price,
                current_symbol_market_value=current_value,
                portfolio_value=portfolio_value,
                cap_rate=self.config.symbol_cap_rate,
                lot_size=self.config.lot_size,
            )
            if shares <= 0:
                self.ledger.record_order_attempt(
                    signal_id=signal.id,
                    stock_code=signal.stock_code,
                    trade_date=execution_date,
                    status="blocked",
                    reason="insufficient_cash_or_symbol_cap",
                    price=fill.price,
                )
                stats["blocked"] += 1
                continue

            gross = round(exec_price * shares, 2)
            commission, tax = self.fees.total_costs(gross, "buy", shares=shares)
            trade = TradeFill(
                signal_id=signal.id,
                stock_code=signal.stock_code,
                side="buy",
                trade_date=execution_date,
                shares=shares,
                fill_price=fill.price,
                exec_price=exec_price,
                gross_amount=gross,
                fees=commission,
                taxes=tax,
                cash_delta=-(gross + commission + tax),
                reason=fill.reason,
                created_at=datetime.utcnow(),
            )
            if self.ledger.apply_trade(trade, stop_loss=signal.stop_loss, target_price=signal.target_price):
                stats["filled"] += 1
                trigger = first_exit_trigger(bar, signal.stop_loss, signal.target_price)
                if trigger.reason != "none" and trigger.price is not None:
                    self._sell_position(
                        signal_id=signal.id,
                        stock_code=signal.stock_code,
                        execution_date=execution_date,
                        fill_price=trigger.price,
                        reason=trigger.reason,
                        max_shares=shares,
                        stats=stats,
                    )
            logger.info(
                "open_attempt signal_id=%s code=%s fill_status=%s reason=%s shares=%s",
                signal.id,
                signal.stock_code,
                fill.status,
                fill.reason,
                shares,
            )

    def _record_data_gaps(self, execution_date: date, bars: Dict[str, object], stats: Dict[str, int]) -> None:
        available = sorted(code for code in self.config.stock_pool if code in bars)
        missing = sorted(code for code in self.config.stock_pool if code not in bars)
        if not missing:
            self.ledger.delete_events(event_date=execution_date, event_type="data_gap")
            return
        reason = "no_stock_daily_bars_for_execution_date" if not bars else "missing_stock_daily_bars_for_pool"
        if self.ledger.record_event(
            signal_id=None,
            stock_code="__system__",
            event_date=execution_date,
            event_type="data_gap",
            reason=reason,
            details={
                "available": available,
                "missing": missing,
                "expected": list(self.config.stock_pool),
                "plan_b": "run DSA gap backfill, then rerun executor for the completed trading date",
            },
        ):
            stats["data_gaps"] += 1

    def _process_pending_exits(self, execution_date: date, logger: logging.Logger, stats: Dict[str, int]) -> None:
        for pending in self.ledger.open_pending_exits_before(execution_date):
            bar = self.reader.bar(pending["stock_code"], execution_date)
            if bar is None:
                continue
            self._sell_position(
                signal_id=pending["signal_id"],
                stock_code=pending["stock_code"],
                execution_date=execution_date,
                fill_price=bar.open,
                reason=pending["reason"],
                max_shares=pending["shares"],
                stats=stats,
            )
            self.ledger.close_pending_exit(int(pending["id"]))
            logger.info("pending_exit_executed id=%s code=%s date=%s", pending["id"], pending["stock_code"], execution_date)

    def _process_position_triggers(
        self,
        execution_date: date,
        bars: Dict[str, object],
        logger: logging.Logger,
        stats: Dict[str, int],
    ) -> None:
        for position in self.ledger.positions():
            bar = bars.get(position["stock_code"])
            if bar is None:
                continue
            trigger = first_exit_trigger(bar, position["stop_loss"], position["target_price"])
            if trigger.reason == "none" or trigger.price is None:
                continue
            self._sell_position(
                signal_id=position["source_signal_id"],
                stock_code=position["stock_code"],
                execution_date=execution_date,
                fill_price=trigger.price,
                reason=trigger.reason,
                max_shares=position["quantity"],
                stats=stats,
            )
            logger.info("position_exit_trigger code=%s reason=%s", position["stock_code"], trigger.reason)

    def _process_exit_signals(
        self,
        execution_date: date,
        bars: Dict[str, object],
        logger: logging.Logger,
        stats: Dict[str, int],
    ) -> None:
        candidates = self.reader.exit_candidates(execution_date)
        stats["exit_candidates"] = len(candidates)
        for signal in candidates:
            position = self.ledger.position(signal.stock_code)
            if position is None:
                self.ledger.record_event(
                    signal_id=signal.id,
                    stock_code=signal.stock_code,
                    event_date=execution_date,
                    event_type="exit_signal_no_position",
                    reason=f"{signal.action}_without_position",
                    details={"source_report_id": signal.source_report_id},
                )
                continue
            bar = bars.get(signal.stock_code)
            if bar is None:
                self.ledger.record_order_attempt(
                    signal_id=signal.id,
                    stock_code=signal.stock_code,
                    trade_date=execution_date,
                    status="unfilled",
                    reason="exit_signal_missing_bar",
                )
                stats["unfilled"] += 1
                continue
            max_shares = self._exit_signal_max_shares(position, signal.action)
            self._sell_position(
                signal_id=signal.id,
                stock_code=signal.stock_code,
                execution_date=execution_date,
                fill_price=bar.open,
                reason=f"signal_{signal.action}",
                max_shares=max_shares,
                stats=stats,
            )
            logger.info("exit_signal_processed signal_id=%s code=%s action=%s", signal.id, signal.stock_code, signal.action)

    def _exit_signal_max_shares(self, position: object, action: str) -> int:
        quantity = int(position["quantity"])
        if action == "reduce":
            shares = int(quantity * self.config.reduce_exit_rate)
            return shares - (shares % self.config.lot_size)
        return quantity

    def _sell_position(
        self,
        *,
        signal_id: Optional[int],
        stock_code: str,
        execution_date: date,
        fill_price: Optional[float],
        reason: str,
        max_shares: int,
        stats: Dict[str, int],
    ) -> None:
        if fill_price is None or fill_price <= 0:
            return
        position = self.ledger.position(stock_code)
        if position is None:
            return
        shares = min(int(position["quantity"]), int(max_shares))
        shares -= shares % self.config.lot_size
        if shares <= 0:
            return
        exec_price = self.slippage.execution_price(float(fill_price), "sell")
        gross = round(exec_price * shares, 2)
        commission, tax = self.fees.total_costs(gross, "sell", shares=shares)
        realized = gross - commission - tax - float(position["avg_cost"]) * shares
        trade = TradeFill(
            signal_id=signal_id,
            stock_code=stock_code,
            side="sell",
            trade_date=execution_date,
            shares=shares,
            fill_price=float(fill_price),
            exec_price=exec_price,
            gross_amount=gross,
            fees=commission,
            taxes=tax,
            cash_delta=gross - commission - tax,
            reason=reason,
            realized_pnl=realized,
            created_at=datetime.utcnow(),
        )
        if self.ledger.apply_trade(trade):
            stats["sells"] += 1

    def _current_symbol_value(self, stock_code: str, bars: Dict[str, object]) -> float:
        position = self.ledger.position(stock_code)
        if position is None:
            return 0.0
        mark = None
        bar = bars.get(stock_code)
        if bar is not None:
            mark = bar.close
        if mark is None:
            mark = position["avg_cost"]
        return float(mark) * int(position["quantity"])

    def _portfolio_value(self, bars: Dict[str, object]) -> float:
        value = self.ledger.get_cash()
        for position in self.ledger.positions():
            bar = bars.get(position["stock_code"])
            mark = bar.close if bar is not None and bar.close is not None else position["avg_cost"]
            value += float(mark) * int(position["quantity"])
        return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the isolated US paper executor.")
    parser.add_argument("--date", dest="execution_date", help="Execution date YYYY-MM-DD. Defaults to latest DSA stock_daily date.")
    parser.add_argument("--backfill-from", dest="backfill_from", help="Backfill from YYYY-MM-DD through --through/latest DSA date.")
    parser.add_argument("--through", dest="through", help="Backfill end date YYYY-MM-DD.")
    args = parser.parse_args()

    engine = UsPaperEngine()
    if args.backfill_from:
        start = parse_date(args.backfill_from)
        if start is None:
            raise SystemExit("--backfill-from must be YYYY-MM-DD")
        through = parse_date(args.through) if args.through else None
        print(engine.backfill(start, through))
        return

    if args.execution_date:
        execution_date = parse_date(args.execution_date)
    else:
        execution_date = engine.reader.latest_trading_date()
    if execution_date is None:
        raise SystemExit("No execution date available")
    print(engine.run_day(execution_date))


if __name__ == "__main__":
    main()
