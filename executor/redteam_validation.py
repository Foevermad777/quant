from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from executor.config import (
    G5_COMPLETION_VERSION,
    G5_SCHEMA_VERSION,
    PROJECT_ROOT,
    ExecutorConfig,
)
from executor.discipline_completion import (
    DISCIPLINED_TEMPORAL_COLUMNS,
    backfill_disciplined_temporal_metadata,
)
from executor.engine import PaperEngine
from executor.ledger import PaperLedger
from executor.signal_reader import SignalReader, parse_date


TEMPORAL_REQUIRED_COLUMNS = tuple(DISCIPLINED_TEMPORAL_COLUMNS)


@dataclass(frozen=True)
class ValidationManifest:
    generated_at: str
    git_commit: str
    git_dirty: bool
    schema_version: str
    completion_version: str
    fill_model: str
    slippage_rate: float
    open_slippage_multiplier: float
    commission_rate: float
    min_commission: float
    stamp_tax_rate: float
    stock_pool: tuple[str, ...]
    use_disciplined_signals: bool

    def stable_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("generated_at", None)
        return payload

    def digest(self) -> str:
        encoded = json.dumps(self.stable_payload(), ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]


@dataclass(frozen=True)
class CoverageRow:
    code: str
    min_date: Optional[str]
    max_date: Optional[str]
    bar_count: int
    status: str
    reason: str
    news_min_date: Optional[str] = None
    news_max_date: Optional[str] = None
    news_count: int = 0


@dataclass(frozen=True)
class OosGateResult:
    status: str
    required_start: str
    required_end: str
    rows: list[CoverageRow]


@dataclass(frozen=True)
class TemporalViolation:
    source: str
    execution_date: str
    signal_id: int
    stock_code: str
    created_at: Optional[str]
    completed_at: Optional[str]
    reason: str


@dataclass(frozen=True)
class TemporalMetadataResult:
    status: str
    table_exists: bool
    columns_present: tuple[str, ...]
    missing_columns: tuple[str, ...]
    row_count: int
    rows_missing_metadata: int
    migrated_rows: int = 0


@dataclass(frozen=True)
class WalkForwardSlice:
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_days: int
    test_days: int


@dataclass(frozen=True)
class TemporalGateResult:
    status: str
    review_start: str
    review_end: str
    raw_violations: list[TemporalViolation]
    disciplined_violations: list[TemporalViolation]
    metadata: TemporalMetadataResult
    walk_forward_slices: list[WalkForwardSlice]


@dataclass(frozen=True)
class BuyDiagnostic:
    scenario: str
    signal_id: int
    stock_code: str
    trade_date: str
    shares: int
    entry_low: Optional[float]
    entry_high: Optional[float]
    entry_band_width_pct: Optional[float]
    fill_price: float
    exec_price: float
    exec_outside_band: Optional[bool]


@dataclass(frozen=True)
class StressScenarioResult:
    scenario: str
    status: str
    open_slippage_multiplier: float
    commission_rate: float
    min_commission: float
    stamp_tax_rate: float
    liquidity_impact_bps: float
    stats: dict[str, int]
    trade_count: int
    final_total_value: Optional[float]
    net_return: Optional[float]
    estimated_liquidity_impact: float
    adjusted_net_return: Optional[float]
    buy_diagnostics: list[BuyDiagnostic]


@dataclass(frozen=True)
class RedTeamValidationResult:
    manifest: ValidationManifest
    oos: OosGateResult
    temporal: TemporalGateResult
    stress: list[StressScenarioResult]


def build_manifest(config: ExecutorConfig) -> ValidationManifest:
    return ValidationManifest(
        generated_at=datetime.utcnow().isoformat(sep=" ", timespec="seconds"),
        git_commit=_git_commit(PROJECT_ROOT),
        git_dirty=_git_dirty(PROJECT_ROOT),
        schema_version=G5_SCHEMA_VERSION,
        completion_version=G5_COMPLETION_VERSION,
        fill_model=config.fill_model,
        slippage_rate=config.slippage_rate,
        open_slippage_multiplier=config.open_slippage_multiplier,
        commission_rate=config.commission_rate,
        min_commission=config.min_commission,
        stamp_tax_rate=config.stamp_tax_rate,
        stock_pool=tuple(str(code) for code in config.stock_pool),
        use_disciplined_signals=bool(config.use_disciplined_signals),
    )


def run_validation(
    config: Optional[ExecutorConfig] = None,
    *,
    oos_start: date = date(2024, 1, 1),
    oos_end: date = date(2025, 12, 31),
    review_start: Optional[date] = None,
    review_end: Optional[date] = None,
    train_days: int = 60,
    test_days: int = 20,
    liquidity_impact_bps: float = 0.0,
    migrate_temporal_metadata: bool = False,
) -> RedTeamValidationResult:
    config = config or ExecutorConfig()
    resolved_review_start, resolved_review_end = resolve_review_window(config, review_start, review_end)
    migrated_rows = 0
    if migrate_temporal_metadata:
        migrated_rows, _ = backfill_disciplined_temporal_metadata(config.ledger_db_path)
    return RedTeamValidationResult(
        manifest=build_manifest(config),
        oos=check_oos_gate(config, oos_start, oos_end),
        temporal=audit_temporal_gate(
            config,
            resolved_review_start,
            resolved_review_end,
            train_days=train_days,
            test_days=test_days,
            migrated_rows=migrated_rows,
        ),
        stress=run_stress_scenarios(
            config,
            resolved_review_start,
            resolved_review_end,
            liquidity_impact_bps=liquidity_impact_bps,
        ),
    )


def resolve_review_window(
    config: ExecutorConfig,
    start: Optional[date],
    end: Optional[date],
) -> tuple[date, date]:
    if start is not None and end is not None:
        return start, end
    dates = _trading_dates(config.dsa_db_path, min_date=None, max_date=None)
    if not dates:
        fallback = start or end or date.today()
        return fallback, fallback
    resolved_end = end or dates[-1]
    if start is not None:
        return start, resolved_end
    eligible = [item for item in dates if item <= resolved_end]
    if len(eligible) >= 2:
        return eligible[-2], eligible[-1]
    return eligible[0], eligible[0]


def check_oos_gate(config: ExecutorConfig, start: date, end: date) -> OosGateResult:
    rows: list[CoverageRow] = []
    latest_allowed_start = start + timedelta(days=7)
    if not config.dsa_db_path.exists():
        return OosGateResult(
            status="failed_closed",
            required_start=start.isoformat(),
            required_end=end.isoformat(),
            rows=[
                CoverageRow(
                    code="*",
                    min_date=None,
                    max_date=None,
                    bar_count=0,
                    status="failed_closed",
                    reason=f"missing_dsa_db:{config.dsa_db_path}",
                    news_min_date=None,
                    news_max_date=None,
                    news_count=0,
                )
            ],
        )
    with _connect_ro(config.dsa_db_path) as conn:
        for code in config.stock_pool:
            row = conn.execute(
                """
                select min(date) as min_date, max(date) as max_date, count(*) as count
                from stock_daily
                where code = ?
                """,
                (code,),
            ).fetchone()
            min_day = parse_date(row["min_date"] if row else None)
            max_day = parse_date(row["max_date"] if row else None)
            count = int(row["count"] if row else 0)
            news_row = _news_coverage(conn, str(code), start, end)
            news_min_day = parse_date(news_row["min_date"] if news_row else None)
            news_max_day = parse_date(news_row["max_date"] if news_row else None)
            news_count = int(news_row["count"] if news_row else 0)
            reasons = []
            if count == 0:
                reasons.append("missing_bars")
            if min_day is None or min_day > latest_allowed_start:
                reasons.append("start_after_required")
            if max_day is None or max_day < end:
                reasons.append("end_before_required")
            if news_count <= 0:
                reasons.append("missing_news_metadata")
            status = "pass" if not reasons else "failed_closed"
            rows.append(
                CoverageRow(
                    code=str(code),
                    min_date=min_day.isoformat() if min_day is not None else None,
                    max_date=max_day.isoformat() if max_day is not None else None,
                    bar_count=count,
                    status=status,
                    reason="ok" if not reasons else ",".join(reasons),
                    news_min_date=news_min_day.isoformat() if news_min_day is not None else None,
                    news_max_date=news_max_day.isoformat() if news_max_day is not None else None,
                    news_count=news_count,
                )
            )
    return OosGateResult(
        status="pass" if all(row.status == "pass" for row in rows) else "failed_closed",
        required_start=start.isoformat(),
        required_end=end.isoformat(),
        rows=rows,
    )


def audit_temporal_gate(
    config: ExecutorConfig,
    start: date,
    end: date,
    *,
    train_days: int,
    test_days: int,
    migrated_rows: int = 0,
) -> TemporalGateResult:
    dates = _trading_dates(config.dsa_db_path, min_date=start, max_date=end)
    if not dates and start <= end:
        dates = [start]
    raw_reader = SignalReader(config.dsa_db_path, use_disciplined_signals=False)
    disciplined_reader = SignalReader(
        config.dsa_db_path,
        config.disciplined_db_path or config.ledger_db_path,
        use_disciplined_signals=True,
    )
    raw_violations = _temporal_reader_violations(raw_reader, dates, "raw")
    disciplined_violations = _temporal_reader_violations(disciplined_reader, dates, "disciplined")
    metadata = audit_disciplined_temporal_metadata(config.ledger_db_path, migrated_rows=migrated_rows)
    slices = walk_forward_slices(_trading_dates(config.dsa_db_path, min_date=None, max_date=None), train_days, test_days)
    status = "pass"
    if raw_violations or disciplined_violations or metadata.status != "pass" or not slices:
        status = "failed_closed"
    return TemporalGateResult(
        status=status,
        review_start=start.isoformat(),
        review_end=end.isoformat(),
        raw_violations=raw_violations,
        disciplined_violations=disciplined_violations,
        metadata=metadata,
        walk_forward_slices=slices,
    )


def audit_disciplined_temporal_metadata(db_path: Path, *, migrated_rows: int = 0) -> TemporalMetadataResult:
    if not db_path.exists():
        return TemporalMetadataResult("failed_closed", False, (), TEMPORAL_REQUIRED_COLUMNS, 0, 0, migrated_rows)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        table = conn.execute(
            "select 1 from sqlite_master where type = 'table' and name = 'disciplined_signals'"
        ).fetchone()
        if table is None:
            return TemporalMetadataResult("failed_closed", False, (), TEMPORAL_REQUIRED_COLUMNS, 0, 0, migrated_rows)
        columns = tuple(row["name"] for row in conn.execute("pragma table_info(disciplined_signals)").fetchall())
        missing = tuple(column for column in TEMPORAL_REQUIRED_COLUMNS if column not in columns)
        row_count = int(conn.execute("select count(*) as count from disciplined_signals").fetchone()["count"])
        missing_rows = 0
        if not missing and row_count:
            predicate = " or ".join(f"{column} is null or trim({column}) = ''" for column in TEMPORAL_REQUIRED_COLUMNS)
            missing_rows = int(
                conn.execute(f"select count(*) as count from disciplined_signals where {predicate}").fetchone()["count"]
            )
    status = "pass" if not missing and missing_rows == 0 else "failed_closed"
    return TemporalMetadataResult(status, True, columns, missing, row_count, missing_rows, migrated_rows)


def walk_forward_slices(
    trading_dates: Sequence[date],
    train_days: int,
    test_days: int,
) -> list[WalkForwardSlice]:
    if train_days <= 0 or test_days <= 0:
        return []
    ordered = sorted(set(trading_dates))
    slices: list[WalkForwardSlice] = []
    start_idx = 0
    while start_idx + train_days + test_days <= len(ordered):
        train = ordered[start_idx : start_idx + train_days]
        test = ordered[start_idx + train_days : start_idx + train_days + test_days]
        slices.append(
            WalkForwardSlice(
                train_start=train[0].isoformat(),
                train_end=train[-1].isoformat(),
                test_start=test[0].isoformat(),
                test_end=test[-1].isoformat(),
                train_days=len(train),
                test_days=len(test),
            )
        )
        start_idx += test_days
    return slices


def run_stress_scenarios(
    config: ExecutorConfig,
    start: date,
    end: date,
    *,
    liquidity_impact_bps: float = 0.0,
) -> list[StressScenarioResult]:
    scenarios = [
        ("base", config.open_slippage_multiplier, config.commission_rate, config.min_commission, config.stamp_tax_rate),
        (
            "double_buy_slippage",
            config.open_slippage_multiplier * 2,
            config.commission_rate,
            config.min_commission,
            config.stamp_tax_rate,
        ),
        (
            "quad_buy_slippage",
            config.open_slippage_multiplier * 4,
            config.commission_rate,
            config.min_commission,
            config.stamp_tax_rate,
        ),
        (
            "double_all_friction",
            config.open_slippage_multiplier * 2,
            config.commission_rate * 2,
            config.min_commission * 2,
            config.stamp_tax_rate * 2,
        ),
    ]
    results: list[StressScenarioResult] = []
    disciplined_db_path = config.disciplined_db_path or config.ledger_db_path
    with tempfile.TemporaryDirectory(prefix="quant_redteam_stress_") as tmpdir:
        for name, slippage_multiplier, commission_rate, min_commission, stamp_tax_rate in scenarios:
            ledger_path = Path(tmpdir) / f"{name}.db"
            scenario_config = replace(
                config,
                ledger_db_path=ledger_path,
                disciplined_db_path=disciplined_db_path,
                open_slippage_multiplier=slippage_multiplier,
                commission_rate=commission_rate,
                min_commission=min_commission,
                stamp_tax_rate=stamp_tax_rate,
            )
            engine = PaperEngine(scenario_config)
            stats = engine.backfill(start, end)
            results.append(
                _stress_result_from_ledger(
                    name,
                    scenario_config,
                    stats,
                    start,
                    end,
                    liquidity_impact_bps=liquidity_impact_bps,
                )
            )
    return results


def render_markdown(result: RedTeamValidationResult) -> str:
    lines = [
        f"# R1-R3 Red-Team Validation {result.manifest.generated_at}",
        "",
        f"- Manifest hash: `{result.manifest.digest()}`",
        f"- Git commit: `{result.manifest.git_commit}`",
        f"- Git dirty: `{result.manifest.git_dirty}`",
        f"- G5 schema/completion: `{result.manifest.schema_version}` / `{result.manifest.completion_version}`",
        "",
        "## R1 OOS Blind Gate",
        "",
        f"- Status: `{result.oos.status}`",
        f"- Required window: `{result.oos.required_start}` to `{result.oos.required_end}`",
        "",
        "| code | min_date | max_date | bars | status | reason |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for row in result.oos.rows:
        lines.append(
            f"| {row.code} | {row.min_date or 'N/A'} | {row.max_date or 'N/A'} | "
            f"{row.bar_count} | {row.status} | {row.reason} |"
        )
    lines.extend(
        [
            "",
            "| code | news_min_date | news_max_date | news_items |",
            "| --- | --- | --- | ---: |",
        ]
    )
    for row in result.oos.rows:
        lines.append(
            f"| {row.code} | {row.news_min_date or 'N/A'} | {row.news_max_date or 'N/A'} | {row.news_count} |"
        )
    lines.extend(
        [
            "",
            "## R2 Temporal And Walk-Forward Gate",
            "",
            f"- Status: `{result.temporal.status}`",
            f"- Review window: `{result.temporal.review_start}` to `{result.temporal.review_end}`",
            f"- Raw temporal violations: `{len(result.temporal.raw_violations)}`",
            f"- Disciplined temporal violations: `{len(result.temporal.disciplined_violations)}`",
            f"- Disciplined metadata status: `{result.temporal.metadata.status}`",
            f"- Disciplined rows: `{result.temporal.metadata.row_count}`",
            f"- Rows missing metadata: `{result.temporal.metadata.rows_missing_metadata}`",
            f"- Metadata rows migrated this run: `{result.temporal.metadata.migrated_rows}`",
            f"- Missing metadata columns: `{', '.join(result.temporal.metadata.missing_columns) or 'none'}`",
            f"- Walk-forward slices available: `{len(result.temporal.walk_forward_slices)}`",
            "",
            "| train_start | train_end | test_start | test_end | train_days | test_days |",
            "| --- | --- | --- | --- | ---: | ---: |",
        ]
    )
    for item in result.temporal.walk_forward_slices[:12]:
        lines.append(
            f"| {item.train_start} | {item.train_end} | {item.test_start} | {item.test_end} | "
            f"{item.train_days} | {item.test_days} |"
        )
    if len(result.temporal.walk_forward_slices) > 12:
        lines.append(f"| ... | ... | ... | ... | ... | {len(result.temporal.walk_forward_slices) - 12} more slices |")
    lines.extend(
        [
            "",
            "## R3 Friction And Liquidity Stress Gate",
            "",
            "| scenario | status | open_slip_mult | comm | stamp_tax | trades | final_value | net_return | impact | adjusted_net_return |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in result.stress:
        lines.append(
            f"| {item.scenario} | {item.status} | {item.open_slippage_multiplier:.4f} | "
            f"{item.commission_rate:.6f} | {item.stamp_tax_rate:.6f} | {item.trade_count} | "
            f"{_fmt_float(item.final_total_value)} | {_fmt_pct(item.net_return)} | "
            f"{item.estimated_liquidity_impact:.2f} | {_fmt_pct(item.adjusted_net_return)} |"
        )
    diagnostics = [diag for item in result.stress for diag in item.buy_diagnostics]
    lines.extend(
        [
            "",
            "### Buy Diagnostics",
            "",
        ]
    )
    if diagnostics:
        lines.extend(
            [
                "| scenario | signal_id | code | date | shares | band_width | fill | exec | outside_band |",
                "| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | --- |",
            ]
        )
        for diag in diagnostics[:30]:
            lines.append(
                f"| {diag.scenario} | {diag.signal_id} | {diag.stock_code} | {diag.trade_date} | "
                f"{diag.shares} | {_fmt_pct(diag.entry_band_width_pct)} | {diag.fill_price:.4f} | "
                f"{diag.exec_price:.4f} | {diag.exec_outside_band} |"
            )
    else:
        lines.append("- No buy trades in stress replay window.")
    lines.extend(
        [
            "",
            "## Verdict",
            "",
            _verdict(result),
        ]
    )
    return "\n".join(lines) + "\n"


def write_report(path: Path, result: RedTeamValidationResult) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(result), encoding="utf-8")
    return path


def _stress_result_from_ledger(
    scenario: str,
    config: ExecutorConfig,
    stats: dict[str, int],
    start: date,
    end: date,
    *,
    liquidity_impact_bps: float,
) -> StressScenarioResult:
    ledger = PaperLedger(config.ledger_db_path, config=config)
    trades = ledger.trades_between(start, end)
    snapshots = ledger.snapshots_between(start, end)
    final_snapshot = snapshots[-1] if snapshots else None
    final_value = float(final_snapshot["total_value"]) if final_snapshot is not None else None
    net_return = final_value / config.initial_cash - 1.0 if final_value is not None and config.initial_cash else None
    liquidity_impact = round(
        sum(float(row["gross_amount"]) for row in trades) * max(0.0, liquidity_impact_bps) / 10000.0,
        2,
    )
    adjusted_return = None
    if final_value is not None and config.initial_cash:
        adjusted_return = (final_value - liquidity_impact) / config.initial_cash - 1.0
    diagnostics = [
        _buy_diagnostic(scenario, SignalReader(config.dsa_db_path, use_disciplined_signals=False), row)
        for row in trades
        if row["side"] == "buy"
    ]
    return StressScenarioResult(
        scenario=scenario,
        status="pass" if diagnostics else "no_trades",
        open_slippage_multiplier=config.open_slippage_multiplier,
        commission_rate=config.commission_rate,
        min_commission=config.min_commission,
        stamp_tax_rate=config.stamp_tax_rate,
        liquidity_impact_bps=liquidity_impact_bps,
        stats=dict(stats),
        trade_count=len(trades),
        final_total_value=final_value,
        net_return=net_return,
        estimated_liquidity_impact=liquidity_impact,
        adjusted_net_return=adjusted_return,
        buy_diagnostics=diagnostics,
    )


def _buy_diagnostic(scenario: str, reader: SignalReader, trade: sqlite3.Row) -> BuyDiagnostic:
    signal_id = int(trade["signal_id"])
    entry_low = None
    entry_high = None
    try:
        signal = reader.get_signal(signal_id)
        entry_low = signal.entry_low
        entry_high = signal.entry_high
    except Exception:  # noqa: BLE001 - diagnostic should not hide the stress run.
        pass
    band_width = None
    outside = None
    if entry_low is not None and entry_high is not None and entry_low > 0:
        band_width = (float(entry_high) - float(entry_low)) / float(entry_low)
        outside = float(trade["exec_price"]) < float(entry_low) or float(trade["exec_price"]) > float(entry_high)
    return BuyDiagnostic(
        scenario=scenario,
        signal_id=signal_id,
        stock_code=trade["stock_code"],
        trade_date=trade["trade_date"],
        shares=int(trade["shares"]),
        entry_low=entry_low,
        entry_high=entry_high,
        entry_band_width_pct=band_width,
        fill_price=float(trade["fill_price"]),
        exec_price=float(trade["exec_price"]),
        exec_outside_band=outside,
    )


def _temporal_reader_violations(
    reader: SignalReader,
    execution_dates: Sequence[date],
    source: str,
) -> list[TemporalViolation]:
    violations: list[TemporalViolation] = []
    for execution_date in execution_dates:
        try:
            signals = reader.active_signals_before(execution_date)
        except Exception as exc:  # noqa: BLE001 - report the gate failure as a violation.
            violations.append(
                TemporalViolation(
                    source=source,
                    execution_date=execution_date.isoformat(),
                    signal_id=-1,
                    stock_code="*",
                    created_at=None,
                    completed_at=None,
                    reason=f"reader_error:{exc.__class__.__name__}",
                )
            )
            continue
        for signal in signals:
            created_at = signal.created_at
            completed_at = signal.metadata.get("discipline", {}).get("completed_at")
            completed_date = parse_date(completed_at)
            if created_at is not None and created_at.date() >= execution_date:
                violations.append(
                    TemporalViolation(
                        source=source,
                        execution_date=execution_date.isoformat(),
                        signal_id=signal.id,
                        stock_code=signal.stock_code,
                        created_at=created_at.isoformat(sep=" "),
                        completed_at=completed_at,
                        reason="created_at_not_before_execution_date",
                    )
                )
            if source == "disciplined" and completed_date is not None and completed_date >= execution_date:
                violations.append(
                    TemporalViolation(
                        source=source,
                        execution_date=execution_date.isoformat(),
                        signal_id=signal.id,
                        stock_code=signal.stock_code,
                        created_at=created_at.isoformat(sep=" ") if created_at else None,
                        completed_at=completed_at,
                        reason="completed_at_not_before_execution_date",
                    )
                )
    return violations


def _trading_dates(db_path: Path, *, min_date: Optional[date], max_date: Optional[date]) -> list[date]:
    if not db_path.exists():
        return []
    predicates: list[str] = []
    params: list[str] = []
    if min_date is not None:
        predicates.append("date >= ?")
        params.append(min_date.isoformat())
    if max_date is not None:
        predicates.append("date <= ?")
        params.append(max_date.isoformat())
    where = " where " + " and ".join(predicates) if predicates else ""
    with _connect_ro(db_path) as conn:
        rows = conn.execute(f"select distinct date from stock_daily{where} order by date", params).fetchall()
    return [parsed for row in rows if (parsed := parse_date(row["date"])) is not None]


def _news_coverage(conn: sqlite3.Connection, code: str, start: date, end: date) -> sqlite3.Row:
    table = conn.execute("select 1 from sqlite_master where type = 'table' and name = 'news_intel'").fetchone()
    if table is None:
        return conn.execute(
            "select null as min_date, null as max_date, 0 as count"
        ).fetchone()
    return conn.execute(
        """
        select min(date(published_date)) as min_date,
               max(date(published_date)) as max_date,
               count(*) as count
        from news_intel
        where code = ?
          and published_date is not null
          and date(published_date) between ? and ?
        """,
        (code, start.isoformat(), end.isoformat()),
    ).fetchone()


def _connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _git_commit(cwd: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:  # noqa: BLE001 - manifest can still be useful without git.
        return "unknown"
    return result.stdout.strip() or "unknown"


def _git_dirty(cwd: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:  # noqa: BLE001 - dirty state is diagnostic only.
        return True
    return bool(result.stdout.strip())


def _fmt_float(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}"


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.4f}%"


def _verdict(result: RedTeamValidationResult) -> str:
    statuses = [result.oos.status, result.temporal.status]
    statuses.extend(item.status for item in result.stress)
    if result.oos.status == "pass" and result.temporal.status == "pass" and all(item.status == "pass" for item in result.stress):
        return "R1-R3 pass for this configured dataset and replay window."
    return "R1-R3 are not cleared. Treat current data as engineering evidence only, not alpha or tradability evidence."
