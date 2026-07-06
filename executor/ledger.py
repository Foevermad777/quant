from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from executor.config import ExecutorConfig


@dataclass(frozen=True)
class TradeFill:
    signal_id: Optional[int]
    stock_code: str
    side: str
    trade_date: date
    shares: int
    fill_price: float
    exec_price: float
    gross_amount: float
    fees: float
    taxes: float
    cash_delta: float
    reason: str
    created_at: datetime
    realized_pnl: Optional[float] = None


class PaperLedger:
    def __init__(self, db_path: Path, config: Optional[ExecutorConfig] = None) -> None:
        self.db_path = Path(db_path)
        self.config = config or ExecutorConfig(ledger_db_path=self.db_path)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma foreign_keys = on")
        return conn

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists account (
                    id integer primary key check (id = 1),
                    cash real not null,
                    initial_cash real not null,
                    updated_at text not null
                );

                create table if not exists positions (
                    stock_code text primary key,
                    quantity integer not null,
                    old_quantity integer not null,
                    avg_cost real not null,
                    stop_loss real,
                    target_price real,
                    source_signal_id integer,
                    updated_at text not null
                );

                create table if not exists trades (
                    id integer primary key autoincrement,
                    signal_id integer,
                    stock_code text not null,
                    side text not null check (side in ('buy', 'sell')),
                    trade_date text not null,
                    shares integer not null,
                    fill_price real not null,
                    exec_price real not null,
                    gross_amount real not null,
                    fees real not null,
                    taxes real not null,
                    cash_delta real not null,
                    realized_pnl real,
                    reason text not null,
                    created_at text not null,
                    unique(signal_id, trade_date, side)
                );

                create table if not exists order_attempts (
                    id integer primary key autoincrement,
                    signal_id integer,
                    stock_code text not null,
                    trade_date text not null,
                    status text not null,
                    reason text not null,
                    price real,
                    details_json text,
                    created_at text not null,
                    unique(signal_id, trade_date, status, reason)
                );

                create table if not exists signal_events (
                    id integer primary key autoincrement,
                    signal_id integer,
                    stock_code text not null,
                    event_date text not null,
                    event_type text not null,
                    reason text not null,
                    details_json text,
                    created_at text not null,
                    unique(signal_id, event_date, event_type, reason)
                );

                create table if not exists pending_exits (
                    id integer primary key autoincrement,
                    signal_id integer,
                    stock_code text not null,
                    shares integer not null,
                    stop_price real not null,
                    reason text not null,
                    triggered_date text not null,
                    earliest_trade_date text,
                    status text not null,
                    created_at text not null,
                    updated_at text not null,
                    unique(signal_id, stock_code, triggered_date, reason)
                );

                create table if not exists portfolio_snapshots (
                    snapshot_date text primary key,
                    cash real not null,
                    market_value real not null,
                    total_value real not null,
                    realized_pnl real not null,
                    unrealized_pnl real not null,
                    details_json text,
                    created_at text not null
                );
                """
            )
            now = datetime.utcnow().isoformat(sep=" ")
            conn.execute(
                """
                insert or ignore into account(id, cash, initial_cash, updated_at)
                values (1, ?, ?, ?)
                """,
                (self.config.initial_cash, self.config.initial_cash, now),
            )

    def record_trade(self, fill: TradeFill) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                insert or ignore into trades(
                    signal_id, stock_code, side, trade_date, shares, fill_price, exec_price,
                    gross_amount, fees, taxes, cash_delta, realized_pnl, reason, created_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fill.signal_id,
                    fill.stock_code,
                    fill.side,
                    fill.trade_date.isoformat(),
                    fill.shares,
                    fill.fill_price,
                    fill.exec_price,
                    fill.gross_amount,
                    fill.fees,
                    fill.taxes,
                    fill.cash_delta,
                    fill.realized_pnl,
                    fill.reason,
                    fill.created_at.isoformat(sep=" "),
                ),
            )
            return cursor.rowcount == 1

    def apply_trade(
        self,
        fill: TradeFill,
        *,
        stop_loss: Optional[float] = None,
        target_price: Optional[float] = None,
    ) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                insert or ignore into trades(
                    signal_id, stock_code, side, trade_date, shares, fill_price, exec_price,
                    gross_amount, fees, taxes, cash_delta, realized_pnl, reason, created_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fill.signal_id,
                    fill.stock_code,
                    fill.side,
                    fill.trade_date.isoformat(),
                    fill.shares,
                    fill.fill_price,
                    fill.exec_price,
                    fill.gross_amount,
                    fill.fees,
                    fill.taxes,
                    fill.cash_delta,
                    fill.realized_pnl,
                    fill.reason,
                    fill.created_at.isoformat(sep=" "),
                ),
            )
            if cursor.rowcount != 1:
                return False

            self._update_cash(conn, fill.cash_delta)
            if fill.side == "buy":
                self._apply_buy(conn, fill, stop_loss=stop_loss, target_price=target_price)
            else:
                self._apply_sell(conn, fill)
            return True

    def record_order_attempt(
        self,
        *,
        signal_id: Optional[int],
        stock_code: str,
        trade_date: date,
        status: str,
        reason: str,
        price: Optional[float] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        stored_signal_id = -1 if signal_id is None else signal_id
        with self._connect() as conn:
            cursor = conn.execute(
                """
                insert or ignore into order_attempts(
                    signal_id, stock_code, trade_date, status, reason, price, details_json, created_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored_signal_id,
                    stock_code,
                    trade_date.isoformat(),
                    status,
                    reason,
                    price,
                    json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
                    datetime.utcnow().isoformat(sep=" "),
                ),
            )
            return cursor.rowcount == 1

    def record_event(
        self,
        *,
        signal_id: Optional[int],
        stock_code: str,
        event_date: date,
        event_type: str,
        reason: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        stored_signal_id = -1 if signal_id is None else signal_id
        with self._connect() as conn:
            cursor = conn.execute(
                """
                insert or ignore into signal_events(
                    signal_id, stock_code, event_date, event_type, reason, details_json, created_at
                )
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored_signal_id,
                    stock_code,
                    event_date.isoformat(),
                    event_type,
                    reason,
                    json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
                    datetime.utcnow().isoformat(sep=" "),
                ),
            )
            return cursor.rowcount == 1

    def record_pending_exit(
        self,
        *,
        signal_id: Optional[int],
        stock_code: str,
        shares: int,
        stop_price: float,
        reason: str,
        triggered_date: date,
    ) -> bool:
        now = datetime.utcnow().isoformat(sep=" ")
        with self._connect() as conn:
            cursor = conn.execute(
                """
                insert or ignore into pending_exits(
                    signal_id, stock_code, shares, stop_price, reason, triggered_date,
                    earliest_trade_date, status, created_at, updated_at
                )
                values (?, ?, ?, ?, ?, ?, null, 'open', ?, ?)
                """,
                (
                    signal_id,
                    stock_code,
                    shares,
                    stop_price,
                    reason,
                    triggered_date.isoformat(),
                    now,
                    now,
                ),
            )
            return cursor.rowcount == 1

    def close_pending_exit(self, pending_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "update pending_exits set status = 'closed', updated_at = ? where id = ?",
                (datetime.utcnow().isoformat(sep=" "), pending_id),
            )

    def open_pending_exits_before(self, execution_date: date) -> List[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select *
                from pending_exits
                where status = 'open'
                  and date(triggered_date) < ?
                order by triggered_date, id
                """,
                (execution_date.isoformat(),),
            ).fetchall()
        return rows

    def get_cash(self) -> float:
        with self._connect() as conn:
            row = conn.execute("select cash from account where id = 1").fetchone()
        return float(row["cash"])

    def positions(self) -> List[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute("select * from positions where quantity > 0 order by stock_code").fetchall()
        return rows

    def position(self, stock_code: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            row = conn.execute("select * from positions where stock_code = ?", (stock_code,)).fetchone()
        return row

    def settle_positions(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "update positions set old_quantity = quantity, updated_at = ?",
                (datetime.utcnow().isoformat(sep=" "),),
            )

    def record_snapshot(self, snapshot_date: date, marks: Dict[str, float]) -> None:
        positions = self.positions()
        cash = self.get_cash()
        market_value = 0.0
        unrealized = 0.0
        details: Dict[str, Any] = {"marks": marks, "positions": []}
        for row in positions:
            mark = marks.get(row["stock_code"])
            if mark is None:
                continue
            value = row["quantity"] * mark
            market_value += value
            unrealized += (mark - row["avg_cost"]) * row["quantity"]
            details["positions"].append(
                {
                    "stock_code": row["stock_code"],
                    "quantity": row["quantity"],
                    "old_quantity": row["old_quantity"],
                    "avg_cost": row["avg_cost"],
                    "mark": mark,
                    "market_value": value,
                }
            )
        realized = self.realized_pnl()
        total = cash + market_value
        with self._connect() as conn:
            conn.execute(
                """
                insert into portfolio_snapshots(
                    snapshot_date, cash, market_value, total_value, realized_pnl,
                    unrealized_pnl, details_json, created_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(snapshot_date) do update set
                    cash = excluded.cash,
                    market_value = excluded.market_value,
                    total_value = excluded.total_value,
                    realized_pnl = excluded.realized_pnl,
                    unrealized_pnl = excluded.unrealized_pnl,
                    details_json = excluded.details_json,
                    created_at = excluded.created_at
                """,
                (
                    snapshot_date.isoformat(),
                    cash,
                    market_value,
                    total,
                    realized,
                    unrealized,
                    json.dumps(details, ensure_ascii=False, sort_keys=True),
                    datetime.utcnow().isoformat(sep=" "),
                ),
            )

    def latest_snapshot(self) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "select * from portfolio_snapshots order by snapshot_date desc limit 1"
            ).fetchone()

    def snapshots_between(self, start: date, end: date) -> List[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select *
                from portfolio_snapshots
                where snapshot_date between ? and ?
                order by snapshot_date
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        return rows

    def trades_between(self, start: date, end: date) -> List[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select *
                from trades
                where trade_date between ? and ?
                order by trade_date, id
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        return rows

    def trade_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("select count(*) as count from trades").fetchone()
        return int(row["count"])

    def realized_pnl(self) -> float:
        with self._connect() as conn:
            row = conn.execute("select coalesce(sum(realized_pnl), 0) as pnl from trades where side = 'sell'").fetchone()
        return float(row["pnl"] or 0.0)

    def _update_cash(self, conn: sqlite3.Connection, delta: float) -> None:
        conn.execute(
            "update account set cash = cash + ?, updated_at = ? where id = 1",
            (delta, datetime.utcnow().isoformat(sep=" ")),
        )

    def _apply_buy(
        self,
        conn: sqlite3.Connection,
        fill: TradeFill,
        *,
        stop_loss: Optional[float],
        target_price: Optional[float],
    ) -> None:
        row = conn.execute("select * from positions where stock_code = ?", (fill.stock_code,)).fetchone()
        cost_basis = abs(fill.cash_delta) / fill.shares
        now = datetime.utcnow().isoformat(sep=" ")
        if row is None:
            conn.execute(
                """
                insert into positions(
                    stock_code, quantity, old_quantity, avg_cost, stop_loss,
                    target_price, source_signal_id, updated_at
                )
                values (?, ?, 0, ?, ?, ?, ?, ?)
                """,
                (
                    fill.stock_code,
                    fill.shares,
                    cost_basis,
                    stop_loss,
                    target_price,
                    fill.signal_id,
                    now,
                ),
            )
            return

        new_qty = int(row["quantity"]) + fill.shares
        old_value = float(row["avg_cost"]) * int(row["quantity"])
        new_avg = (old_value + abs(fill.cash_delta)) / new_qty
        conn.execute(
            """
            update positions
            set quantity = ?, avg_cost = ?, stop_loss = coalesce(?, stop_loss),
                target_price = coalesce(?, target_price), source_signal_id = coalesce(?, source_signal_id),
                updated_at = ?
            where stock_code = ?
            """,
            (new_qty, new_avg, stop_loss, target_price, fill.signal_id, now, fill.stock_code),
        )

    def _apply_sell(self, conn: sqlite3.Connection, fill: TradeFill) -> None:
        row = conn.execute("select * from positions where stock_code = ?", (fill.stock_code,)).fetchone()
        if row is None:
            raise ValueError(f"cannot sell missing position: {fill.stock_code}")
        quantity = int(row["quantity"])
        old_quantity = int(row["old_quantity"])
        if fill.shares > min(quantity, old_quantity):
            raise ValueError("T+1 violation while applying sell")
        new_qty = quantity - fill.shares
        new_old = old_quantity - fill.shares
        conn.execute(
            """
            update positions
            set quantity = ?, old_quantity = ?, updated_at = ?
            where stock_code = ?
            """,
            (new_qty, new_old, datetime.utcnow().isoformat(sep=" "), fill.stock_code),
        )
