from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from executor.config import DSA_DB_PATH, RUNTIME_DIR, ExecutorConfig
from executor.signal_reader import parse_date


OOS_DIR = RUNTIME_DIR / "oos"
OOS_DSA_DB_PATH = OOS_DIR / "stock_analysis_oos.db"
OOS_IMPORT_REPORT_DIR = OOS_DIR
TUSHARE_API_URL = "http://api.tushare.pro"
R1_REQUIRED_OOS_END = date(2025, 12, 31)

STOCK_NAMES = {
    "600519": "贵州茅台",
    "300750": "宁德时代",
    "601318": "中国平安",
    "600036": "招商银行",
    "600900": "长江电力",
}


@dataclass(frozen=True)
class OosCodeSummary:
    code: str
    name: str
    copied_bars: int = 0
    fetched_bars: int = 0
    copied_news: int = 0
    fetched_announcements: int = 0
    min_bar_date: Optional[str] = None
    max_bar_date: Optional[str] = None
    news_count: int = 0
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class OosBuildSummary:
    db_path: Path
    source_db_path: Path
    start: date
    end: date
    generated_at: str
    codes: tuple[OosCodeSummary, ...]
    errors: tuple[str, ...] = ()

    @property
    def bars_ready(self) -> bool:
        latest_allowed_start = (self.start + timedelta(days=7)).isoformat()
        required_end = R1_REQUIRED_OOS_END.isoformat()
        return all(
            item.min_bar_date is not None
            and item.min_bar_date <= latest_allowed_start
            and item.max_bar_date is not None
            and item.max_bar_date >= required_end
            for item in self.codes
        )

    @property
    def news_ready(self) -> bool:
        return all(item.news_count > 0 for item in self.codes)


class TushareHttpClient:
    def __init__(self, token: str, *, api_url: str = TUSHARE_API_URL, timeout: int = 30) -> None:
        self.token = token.strip()
        self.api_url = api_url
        self.timeout = timeout

    def query(self, api_name: str, *, fields: str = "", params: Optional[Mapping[str, Any]] = None) -> list[dict[str, Any]]:
        payload = {
            "api_name": api_name,
            "token": self.token,
            "params": dict(params or {}),
            "fields": fields,
        }
        request = urllib.request.Request(
            self.api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Tushare HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Tushare URL error: {exc.reason}") from exc
        if response_payload.get("code") != 0:
            raise RuntimeError(response_payload.get("msg") or f"Tushare API error {response_payload.get('code')}")
        data = response_payload.get("data") or {}
        columns = data.get("fields") or []
        items = data.get("items") or []
        return [dict(zip(columns, item)) for item in items]


def build_oos_database(
    *,
    db_path: Path = OOS_DSA_DB_PATH,
    source_db_path: Path = DSA_DB_PATH,
    token_path: Optional[Path] = None,
    start: date = date(2024, 1, 1),
    end: date = date(2025, 3, 13),
    codes: Sequence[str] = ExecutorConfig().stock_pool,
    fetch_bars: bool = True,
    fetch_announcements: bool = True,
    announcement_source: str = "tushare",
    copy_live_after_end: bool = True,
) -> OosBuildSummary:
    db_path = Path(db_path)
    source_db_path = Path(source_db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    initialize_oos_schema(db_path)
    global_errors: list[str] = []
    token = _read_secret(token_path) if token_path is not None else None
    client = TushareHttpClient(token) if token else None

    code_summaries: list[OosCodeSummary] = []
    for code in codes:
        errors: list[str] = []
        name = STOCK_NAMES.get(code, code)
        copied_bars = copy_stock_daily(source_db_path, db_path, code)
        copied_news = copy_news_intel(source_db_path, db_path, code, start=start, end=date.today())
        fetched_bars = 0
        fetched_announcements = 0
        if fetch_bars:
            if client is None:
                errors.append("missing_tushare_token_for_bars")
            else:
                try:
                    fetched_bars = import_tushare_daily(client, db_path, code, start, end)
                    time.sleep(0.25)
                except Exception as exc:  # noqa: BLE001 - report per-code ingest failure.
                    errors.append(f"bars:{type(exc).__name__}:{exc}")
        if fetch_announcements:
            if announcement_source == "akshare":
                try:
                    fetched_announcements = import_akshare_announcements(db_path, code, name, start, end)
                    time.sleep(0.25)
                except Exception as exc:  # noqa: BLE001 - report per-code ingest failure.
                    errors.append(f"announcements_akshare:{type(exc).__name__}:{exc}")
            elif client is None:
                errors.append("missing_tushare_token_for_announcements")
            else:
                try:
                    fetched_announcements = import_tushare_announcements(client, db_path, code, name, start, end)
                    time.sleep(0.25)
                except Exception as exc:  # noqa: BLE001 - report per-code ingest failure.
                    errors.append(f"announcements:{type(exc).__name__}:{exc}")
        recompute_indicators(db_path, code)
        coverage = stock_coverage(db_path, code)
        news_count = news_count_between(db_path, code, start, R1_REQUIRED_OOS_END)
        code_summaries.append(
            OosCodeSummary(
                code=code,
                name=name,
                copied_bars=copied_bars,
                fetched_bars=fetched_bars,
                copied_news=copied_news,
                fetched_announcements=fetched_announcements,
                min_bar_date=coverage[0],
                max_bar_date=coverage[1],
                news_count=news_count,
                errors=tuple(errors),
            )
        )
    if copy_live_after_end and not source_db_path.exists():
        global_errors.append(f"source_db_missing:{source_db_path}")
    return OosBuildSummary(
        db_path=db_path,
        source_db_path=source_db_path,
        start=start,
        end=end,
        generated_at=datetime.utcnow().isoformat(sep=" ", timespec="seconds"),
        codes=tuple(code_summaries),
        errors=tuple(global_errors),
    )


def initialize_oos_schema(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            create table if not exists stock_daily (
                id integer primary key autoincrement,
                code text not null,
                date text not null,
                open real,
                high real,
                low real,
                close real,
                volume real,
                amount real,
                pct_chg real,
                ma5 real,
                ma10 real,
                ma20 real,
                volume_ratio real,
                data_source text,
                created_at text,
                updated_at text,
                unique(code, date)
            );
            create index if not exists ix_oos_stock_daily_code on stock_daily(code);
            create index if not exists ix_oos_stock_daily_date on stock_daily(date);
            create index if not exists ix_oos_code_date on stock_daily(code, date);

            create table if not exists news_intel (
                id integer primary key autoincrement,
                query_id text,
                code text not null,
                name text,
                dimension text,
                query text,
                provider text,
                title text not null,
                snippet text,
                url text not null,
                source text,
                published_date text,
                fetched_at text,
                query_source text,
                requester_platform text,
                requester_user_id text,
                requester_user_name text,
                requester_chat_id text,
                requester_message_id text,
                requester_query text,
                unique(url)
            );
            create index if not exists ix_oos_news_code_pub on news_intel(code, published_date);
            create index if not exists ix_oos_news_provider on news_intel(provider);

            create table if not exists analysis_history (
                id integer primary key autoincrement,
                code text not null,
                name text,
                operation_advice text,
                created_at text
            );
            create table if not exists decision_signals (
                id integer primary key autoincrement,
                stock_code text not null,
                stock_name text,
                action text not null,
                confidence real,
                entry_high real,
                entry_low real,
                stop_loss real,
                target_price real,
                status text not null,
                created_at text,
                expires_at text,
                source_report_id integer,
                metadata_json text,
                market text,
                source_type text,
                source_agent text,
                plan_quality text
            );
            create table if not exists decision_signal_outcomes (
                id integer primary key autoincrement,
                signal_id integer not null,
                horizon text not null,
                eval_status text not null,
                anchor_date text,
                created_at text,
                updated_at text
            );
            """
        )


def copy_stock_daily(source_db_path: Path, db_path: Path, code: str) -> int:
    if not Path(source_db_path).exists():
        return 0
    with sqlite3.connect(source_db_path) as src, sqlite3.connect(db_path) as dst:
        src.row_factory = sqlite3.Row
        rows = src.execute("select * from stock_daily where code = ? order by date", (code,)).fetchall()
        for row in rows:
            upsert_stock_bar(
                dst,
                code=code,
                day=str(row["date"])[:10],
                open_price=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
                amount=row["amount"],
                pct_chg=row["pct_chg"],
                data_source=row["data_source"] or "live_copy",
            )
        return len(rows)


def copy_news_intel(source_db_path: Path, db_path: Path, code: str, *, start: date, end: date) -> int:
    if not Path(source_db_path).exists():
        return 0
    with sqlite3.connect(source_db_path) as src, sqlite3.connect(db_path) as dst:
        src.row_factory = sqlite3.Row
        rows = src.execute(
            """
            select *
            from news_intel
            where code = ?
              and published_date is not null
              and date(published_date) between ? and ?
            order by published_date, id
            """,
            (code, start.isoformat(), end.isoformat()),
        ).fetchall()
        for row in rows:
            upsert_news(
                dst,
                code=code,
                name=row["name"],
                dimension=row["dimension"],
                query=row["query"],
                provider=row["provider"] or "live_copy",
                title=row["title"],
                snippet=row["snippet"],
                url=row["url"],
                source=row["source"],
                published_date=_text_or_none(row["published_date"]),
                fetched_at=_text_or_none(row["fetched_at"]),
                query_source=row["query_source"],
            )
        return len(rows)


def import_tushare_daily(
    client: TushareHttpClient,
    db_path: Path,
    code: str,
    start: date,
    end: date,
) -> int:
    rows = client.query(
        "daily",
        fields="ts_code,trade_date,open,high,low,close,vol,amount,pct_chg",
        params={
            "ts_code": ts_code_for_a_share(code),
            "start_date": start.strftime("%Y%m%d"),
            "end_date": end.strftime("%Y%m%d"),
        },
    )
    with sqlite3.connect(db_path) as conn:
        for row in rows:
            day = _trade_date_to_iso(row.get("trade_date"))
            if day is None:
                continue
            volume = _float_or_none(row.get("vol"))
            amount = _float_or_none(row.get("amount"))
            upsert_stock_bar(
                conn,
                code=code,
                day=day,
                open_price=row.get("open"),
                high=row.get("high"),
                low=row.get("low"),
                close=row.get("close"),
                volume=volume * 100 if volume is not None else None,
                amount=amount * 1000 if amount is not None else None,
                pct_chg=row.get("pct_chg"),
                data_source="tushare_oos_daily",
            )
    return len(rows)


def import_tushare_announcements(
    client: TushareHttpClient,
    db_path: Path,
    code: str,
    name: str,
    start: date,
    end: date,
) -> int:
    rows = client.query(
        "anns_d",
        fields="ts_code,ann_date,title,url",
        params={
            "ts_code": ts_code_for_a_share(code),
            "start_date": start.strftime("%Y%m%d"),
            "end_date": end.strftime("%Y%m%d"),
        },
    )
    now = datetime.utcnow().isoformat(sep=" ")
    with sqlite3.connect(db_path) as conn:
        for row in rows:
            published = _trade_date_to_iso(row.get("ann_date"))
            title = str(row.get("title") or "").strip()
            if not published or not title:
                continue
            url = str(row.get("url") or "").strip()
            if not url:
                url = f"tushare://anns_d/{ts_code_for_a_share(code)}/{published}/{_stable_id(title)}"
            upsert_news(
                conn,
                code=code,
                name=name,
                dimension="announcement",
                query=f"{name} {code} 公告",
                provider="Tushare",
                title=title,
                snippet=title,
                url=url,
                source="Tushare anns_d",
                published_date=f"{published} 00:00:00",
                fetched_at=now,
                query_source="oos_builder",
            )
    return len(rows)


def import_akshare_announcements(
    db_path: Path,
    code: str,
    name: str,
    start: date,
    end: date,
) -> int:
    try:
        import akshare as ak  # type: ignore
    except ImportError as exc:
        raise RuntimeError("akshare is not installed in this Python environment") from exc

    df = ak.stock_individual_notice_report(
        security=code,
        symbol="全部",
        begin_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    )
    now = datetime.utcnow().isoformat(sep=" ")
    count = 0
    with sqlite3.connect(db_path) as conn:
        for item in df.to_dict(orient="records"):
            published = _trade_date_to_iso(item.get("公告日期"))
            title = str(item.get("公告标题") or "").strip()
            if not published or not title:
                continue
            announcement_type = str(item.get("公告类型") or "").strip() or "公告"
            url = str(item.get("网址") or "").strip()
            if not url:
                url = f"akshare://stock_individual_notice_report/{code}/{published}/{_stable_id(title)}"
            upsert_news(
                conn,
                code=code,
                name=str(item.get("名称") or name),
                dimension="announcement",
                query=f"{name} {code} {announcement_type}",
                provider="AkShare",
                title=title,
                snippet=announcement_type,
                url=url,
                source="Eastmoney announcements via AkShare",
                published_date=f"{published} 00:00:00",
                fetched_at=now,
                query_source="oos_builder",
            )
            count += 1
    return count


def upsert_stock_bar(
    conn: sqlite3.Connection,
    *,
    code: str,
    day: str,
    open_price: Any,
    high: Any,
    low: Any,
    close: Any,
    volume: Any,
    amount: Any,
    pct_chg: Any,
    data_source: str,
) -> None:
    now = datetime.utcnow().isoformat(sep=" ")
    conn.execute(
        """
        insert into stock_daily(
            code, date, open, high, low, close, volume, amount, pct_chg,
            data_source, created_at, updated_at
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(code, date) do update set
            open = excluded.open,
            high = excluded.high,
            low = excluded.low,
            close = excluded.close,
            volume = excluded.volume,
            amount = excluded.amount,
            pct_chg = excluded.pct_chg,
            data_source = excluded.data_source,
            updated_at = excluded.updated_at
        """,
        (
            code,
            day,
            _float_or_none(open_price),
            _float_or_none(high),
            _float_or_none(low),
            _float_or_none(close),
            _float_or_none(volume),
            _float_or_none(amount),
            _float_or_none(pct_chg),
            data_source,
            now,
            now,
        ),
    )


def upsert_news(
    conn: sqlite3.Connection,
    *,
    code: str,
    name: Optional[str],
    dimension: Optional[str],
    query: Optional[str],
    provider: str,
    title: str,
    snippet: Optional[str],
    url: str,
    source: Optional[str],
    published_date: Optional[str],
    fetched_at: Optional[str],
    query_source: Optional[str],
) -> None:
    conn.execute(
        """
        insert or ignore into news_intel(
            query_id, code, name, dimension, query, provider, title, snippet, url,
            source, published_date, fetched_at, query_source
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _stable_id(f"{code}|{provider}|{query}|{published_date}"),
            code,
            name,
            dimension,
            query,
            provider,
            title,
            snippet,
            url,
            source,
            published_date,
            fetched_at or datetime.utcnow().isoformat(sep=" "),
            query_source,
        ),
    )


def recompute_indicators(db_path: Path, code: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "select date, close, volume from stock_daily where code = ? order by date",
            (code,),
        ).fetchall()
        closes: list[float] = []
        volumes: list[float] = []
        for row in rows:
            close = _float_or_none(row["close"])
            volume = _float_or_none(row["volume"])
            closes.append(close if close is not None else 0.0)
            volumes.append(volume if volume is not None else 0.0)
            idx = len(closes) - 1
            ma5 = _mean(closes[max(0, idx - 4) : idx + 1])
            ma10 = _mean(closes[max(0, idx - 9) : idx + 1])
            ma20 = _mean(closes[max(0, idx - 19) : idx + 1])
            previous_volumes = volumes[max(0, idx - 5) : idx]
            avg_previous_volume = _mean(previous_volumes) if previous_volumes else None
            volume_ratio = 1.0 if not avg_previous_volume else volumes[idx] / avg_previous_volume
            conn.execute(
                """
                update stock_daily
                set ma5 = ?, ma10 = ?, ma20 = ?, volume_ratio = ?, updated_at = ?
                where code = ? and date = ?
                """,
                (
                    round(ma5, 2),
                    round(ma10, 2),
                    round(ma20, 2),
                    round(volume_ratio, 2),
                    datetime.utcnow().isoformat(sep=" "),
                    code,
                    row["date"],
                ),
            )


def stock_coverage(db_path: Path, code: str) -> tuple[Optional[str], Optional[str], int]:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "select min(date) as min_date, max(date) as max_date, count(*) as count from stock_daily where code = ?",
            (code,),
        ).fetchone()
    return row[0], row[1], int(row[2] or 0)


def news_count_between(db_path: Path, code: str, start: date, end: date) -> int:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            select count(*) as count
            from news_intel
            where code = ?
              and published_date is not null
              and date(published_date) between ? and ?
            """,
            (code, start.isoformat(), end.isoformat()),
        ).fetchone()
    return int(row[0] or 0)


def render_oos_import_report(summary: OosBuildSummary) -> str:
    lines = [
        f"# OOS Import Report {summary.generated_at}",
        "",
        f"- OOS DB: `{summary.db_path}`",
        f"- Source DB: `{summary.source_db_path}`",
        f"- Missing-window fetch: `{summary.start}` to `{summary.end}`",
        f"- R1 required OOS end: `{R1_REQUIRED_OOS_END}`",
        f"- Bars ready for R1 start: `{summary.bars_ready}`",
        f"- News metadata ready: `{summary.news_ready}`",
        "",
        "| code | name | copied_bars | fetched_bars | min_bar | max_bar | copied_news | announcements | news_items | errors |",
        "| --- | --- | ---: | ---: | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for item in summary.codes:
        lines.append(
            f"| {item.code} | {item.name} | {item.copied_bars} | {item.fetched_bars} | "
            f"{item.min_bar_date or 'N/A'} | {item.max_bar_date or 'N/A'} | {item.copied_news} | "
            f"{item.fetched_announcements} | {item.news_count} | {'; '.join(item.errors) or 'none'} |"
        )
    if summary.errors:
        lines.extend(["", "## Global Errors", ""])
        lines.extend(f"- {error}" for error in summary.errors)
    lines.extend(
        [
            "",
            "## R1 Readiness",
            "",
            "- Bars can clear R1 only if every code has a first trading bar within the start-date tolerance and extends through the required OOS end.",
            "- News/announcement metadata is treated as mandatory evidence; zero old news keeps R1 closed.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_oos_import_report(path: Path, summary: OosBuildSummary) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_oos_import_report(summary), encoding="utf-8")
    return path


def ts_code_for_a_share(code: str) -> str:
    normalized = code.strip().split(".")[0]
    suffix = "SH" if normalized.startswith("6") else "SZ"
    return f"{normalized}.{suffix}"


def _read_secret(path: Optional[Path]) -> Optional[str]:
    if path is None or not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def _trade_date_to_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    parsed = parse_date(text)
    return parsed.isoformat() if parsed is not None else None


def _float_or_none(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean(values: Sequence[float]) -> float:
    filtered = [value for value in values if value is not None]
    return sum(filtered) / len(filtered) if filtered else 0.0


def _stable_id(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _text_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)
