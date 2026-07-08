from __future__ import annotations

import hashlib
import csv
import email.utils
import http.cookiejar
import io
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from executor.config import DSA_DB_PATH, PROJECT_ROOT, RUNTIME_DIR
from executor.oos_builder import (
    copy_news_intel,
    copy_stock_daily,
    initialize_oos_schema,
    recompute_indicators,
    stock_coverage,
    upsert_news,
    upsert_stock_bar,
)
from executor.signal_reader import parse_date


US_OOS_DIR = RUNTIME_DIR / "oos"
US_OOS_DSA_DB_PATH = US_OOS_DIR / "stock_analysis_us_oos.db"
US_OOS_IMPORT_REPORT_DIR = US_OOS_DIR
US_R1_REQUIRED_OOS_END = date(2025, 12, 31)
ALPHAVANTAGE_API_URL = "https://www.alphavantage.co/query"
YAHOO_CHART_API_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
STOOQ_DAILY_CSV_URL = "https://stooq.com/q/d/l/"
VENDOR_DSA_ROOT = PROJECT_ROOT / "vendor" / "daily_stock_analysis"
DEFAULT_US_R1_CODES = ("AAPL", "MSFT", "NVDA", "JPM")

US_STOCK_NAMES = {
    "AAPL": "Apple Inc.",
    "MSFT": "Microsoft Corporation",
    "NVDA": "NVIDIA Corporation",
    "JPM": "JPMorgan Chase & Co.",
    "SPY": "SPDR S&P 500 ETF Trust",
    "SPCX": "SPAC and New Issue ETF",
}


@dataclass(frozen=True)
class UsOosCodeSummary:
    code: str
    name: str
    copied_bars: int = 0
    fetched_bars: int = 0
    copied_news: int = 0
    fetched_news: int = 0
    min_bar_date: Optional[str] = None
    max_bar_date: Optional[str] = None
    bar_count: int = 0
    news_min_date: Optional[str] = None
    news_max_date: Optional[str] = None
    news_count: int = 0
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class UsOosBuildSummary:
    db_path: Path
    source_db_path: Path
    start: date
    end: date
    generated_at: str
    codes: tuple[UsOosCodeSummary, ...]
    price_provider: str
    news_provider: str
    errors: tuple[str, ...] = ()

    @property
    def bars_ready(self) -> bool:
        latest_allowed_start = (self.start + timedelta(days=7)).isoformat()
        required_end = US_R1_REQUIRED_OOS_END.isoformat()
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


def build_us_oos_database(
    *,
    db_path: Path = US_OOS_DSA_DB_PATH,
    source_db_path: Path = DSA_DB_PATH,
    start: date = date(2024, 1, 1),
    end: date = US_R1_REQUIRED_OOS_END,
    codes: Sequence[str] = DEFAULT_US_R1_CODES,
    price_provider: str = "yahoochart",
    news_provider: str = "tavily",
    alpha_vantage_key_path: Optional[Path] = None,
    tavily_key_path: Optional[Path] = None,
    fetch_bars: bool = True,
    fetch_news: bool = True,
    copy_live_after_end: bool = True,
) -> UsOosBuildSummary:
    db_path = Path(db_path)
    source_db_path = Path(source_db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    initialize_oos_schema(db_path)
    global_errors: list[str] = []
    alpha_key = _read_alpha_vantage_key(alpha_vantage_key_path)
    tavily_keys = _read_tavily_keys(tavily_key_path)
    summaries: list[UsOosCodeSummary] = []

    normalized_codes = tuple(dict.fromkeys(str(code).strip().upper() for code in codes if str(code).strip()))
    for code in normalized_codes:
        errors: list[str] = []
        copied_bars = copy_stock_daily(source_db_path, db_path, code) if copy_live_after_end else 0
        copied_news = copy_news_intel(source_db_path, db_path, code, start=start, end=date.today()) if copy_live_after_end else 0
        fetched_bars = 0
        fetched_news = 0
        if fetch_bars:
            try:
                fetched_bars = import_us_daily(
                    db_path,
                    code,
                    start,
                    end,
                    provider=price_provider,
                    alpha_vantage_key=alpha_key,
                )
                time.sleep(0.2)
            except Exception as exc:  # noqa: BLE001 - per-symbol ingest errors belong in the report.
                errors.append(f"bars_{price_provider}:{type(exc).__name__}:{exc}")
        if fetch_news:
            try:
                fetched_news = import_us_news(
                    db_path,
                    code,
                    US_STOCK_NAMES.get(code, code),
                    start,
                    end,
                    provider=news_provider,
                    alpha_vantage_key=alpha_key,
                    tavily_api_keys=tavily_keys,
                )
                time.sleep(0.2)
            except Exception as exc:  # noqa: BLE001 - per-symbol ingest errors belong in the report.
                errors.append(f"news_{news_provider}:{type(exc).__name__}:{exc}")
        recompute_indicators(db_path, code)
        min_bar, max_bar, bar_count = stock_coverage(db_path, code)
        news_min, news_max, news_count = us_news_coverage(db_path, code, start, US_R1_REQUIRED_OOS_END)
        summaries.append(
            UsOosCodeSummary(
                code=code,
                name=US_STOCK_NAMES.get(code, code),
                copied_bars=copied_bars,
                fetched_bars=fetched_bars,
                copied_news=copied_news,
                fetched_news=fetched_news,
                min_bar_date=min_bar,
                max_bar_date=max_bar,
                bar_count=bar_count,
                news_min_date=news_min,
                news_max_date=news_max,
                news_count=news_count,
                errors=tuple(errors),
            )
        )
    if copy_live_after_end and not source_db_path.exists():
        global_errors.append(f"source_db_missing:{source_db_path}")
    return UsOosBuildSummary(
        db_path=db_path,
        source_db_path=source_db_path,
        start=start,
        end=end,
        generated_at=datetime.utcnow().isoformat(sep=" ", timespec="seconds"),
        codes=tuple(summaries),
        price_provider=price_provider,
        news_provider=news_provider,
        errors=tuple(global_errors),
    )


def import_us_daily(
    db_path: Path,
    code: str,
    start: date,
    end: date,
    *,
    provider: str,
    alpha_vantage_key: Optional[str] = None,
) -> int:
    provider = provider.strip().lower()
    if provider == "none":
        return 0
    if provider == "yfinance":
        return import_yfinance_daily(db_path, code, start, end)
    if provider in {"yahoochart", "yahoo-chart", "yahoo_chart"}:
        return import_yahoo_chart_daily(db_path, code, start, end)
    if provider == "stooq":
        return import_stooq_daily(db_path, code, start, end)
    if provider in {"alphavantage", "alpha-vantage", "alpha_vantage"}:
        if not alpha_vantage_key:
            raise RuntimeError("AlphaVantage API key is required for price provider")
        return import_alphavantage_daily(db_path, code, start, end, api_key=alpha_vantage_key)
    raise ValueError(f"unsupported US price provider: {provider}")


def import_us_news(
    db_path: Path,
    code: str,
    name: str,
    start: date,
    end: date,
    *,
    provider: str,
    alpha_vantage_key: Optional[str] = None,
    tavily_api_keys: Optional[Sequence[str]] = None,
) -> int:
    provider = provider.strip().lower()
    if provider == "none":
        return 0
    if provider == "yfinance":
        return import_yfinance_news(db_path, code, name, start, end)
    if provider == "tavily":
        if not tavily_api_keys:
            raise RuntimeError("Tavily API key is required for news provider")
        return import_tavily_news(db_path, code, name, start, end, api_keys=tavily_api_keys)
    if provider in {"alphavantage", "alpha-vantage", "alpha_vantage"}:
        if not alpha_vantage_key:
            raise RuntimeError("AlphaVantage API key is required for news provider")
        return import_alphavantage_news(db_path, code, name, start, end, api_key=alpha_vantage_key)
    raise ValueError(f"unsupported US news provider: {provider}")


def import_yfinance_daily(db_path: Path, code: str, start: date, end: date) -> int:
    _ensure_vendor_import_path()
    try:
        from data_provider.yfinance_fetcher import YfinanceFetcher  # type: ignore
    except Exception as exc:  # noqa: BLE001 - preserve import diagnostics for the report.
        raise RuntimeError(
            "vendor yfinance_fetcher is unavailable; run with vendor/daily_stock_analysis/.venv/bin/python "
            "or choose --price-provider alphavantage"
        ) from exc

    fetcher = YfinanceFetcher()
    # yfinance treats end as exclusive, so ask one calendar day beyond the target window.
    df = fetcher.get_daily_data(code, start.isoformat(), (end + timedelta(days=1)).isoformat())
    rows = df.to_dict(orient="records")
    with sqlite3.connect(db_path) as conn:
        for row in rows:
            day = _date_to_iso(row.get("date"))
            if day is None:
                continue
            parsed = parse_date(day)
            if parsed is None or parsed < start or parsed > end:
                continue
            upsert_stock_bar(
                conn,
                code=code,
                day=day,
                open_price=row.get("open"),
                high=row.get("high"),
                low=row.get("low"),
                close=row.get("close"),
                volume=row.get("volume"),
                amount=row.get("amount"),
                pct_chg=row.get("pct_chg"),
                data_source="yfinance_us_oos_daily",
            )
    return len(rows)


def import_yahoo_chart_daily(db_path: Path, code: str, start: date, end: date) -> int:
    params = {
        "period1": str(_unix_utc(start)),
        # Yahoo treats period2 as exclusive.
        "period2": str(_unix_utc(end + timedelta(days=1))),
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true",
    }
    payload = _http_json(YAHOO_CHART_API_URL.format(symbol=urllib.parse.quote(code.upper())), params=params)
    chart = payload.get("chart") if isinstance(payload, Mapping) else None
    error = chart.get("error") if isinstance(chart, Mapping) else None
    if error:
        raise RuntimeError(f"Yahoo chart API error: {error}")
    results = chart.get("result") if isinstance(chart, Mapping) else None
    if not results:
        raise RuntimeError(f"Yahoo chart returned no result for {code.upper()}")
    result = results[0]
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    adjclose = ((result.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose") or []
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    count = 0
    with sqlite3.connect(db_path) as conn:
        for idx, timestamp in enumerate(timestamps):
            day = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).date()
            if day < start or day > end:
                continue
            open_price = _list_float_or_none(opens, idx)
            high = _list_float_or_none(highs, idx)
            low = _list_float_or_none(lows, idx)
            close = _list_float_or_none(closes, idx)
            adjusted_close = _list_float_or_none(adjclose, idx)
            volume = _list_float_or_none(volumes, idx)
            if open_price is None or high is None or low is None or close is None:
                continue
            ratio = adjusted_close / close if adjusted_close is not None and close else 1.0
            adjusted_open = open_price * ratio
            adjusted_high = high * ratio
            adjusted_low = low * ratio
            adjusted_final_close = adjusted_close if adjusted_close is not None else close
            upsert_stock_bar(
                conn,
                code=code.upper(),
                day=day.isoformat(),
                open_price=adjusted_open,
                high=adjusted_high,
                low=adjusted_low,
                close=adjusted_final_close,
                volume=volume,
                amount=volume * adjusted_final_close if volume is not None else None,
                pct_chg=None,
                data_source="yahoo_chart_us_oos_daily",
            )
            count += 1
    if count == 0:
        raise RuntimeError(f"Yahoo chart returned no usable daily rows for {code.upper()} from {start} to {end}")
    return count


def import_stooq_daily(db_path: Path, code: str, start: date, end: date) -> int:
    params = {
        "s": _stooq_symbol(code),
        "i": "d",
        "d1": start.strftime("%Y%m%d"),
        "d2": end.strftime("%Y%m%d"),
    }
    text = _http_text(
        f"{STOOQ_DAILY_CSV_URL}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"},
    )
    count = 0
    with sqlite3.connect(db_path) as conn:
        for row in csv.DictReader(io.StringIO(text)):
            day = _date_to_iso(row.get("Date"))
            parsed = parse_date(day)
            if day is None or parsed is None or parsed < start or parsed > end:
                continue
            open_price = _float_or_none(row.get("Open"))
            high = _float_or_none(row.get("High"))
            low = _float_or_none(row.get("Low"))
            close = _float_or_none(row.get("Close"))
            volume = _float_or_none(row.get("Volume"))
            if open_price is None or high is None or low is None or close is None:
                continue
            upsert_stock_bar(
                conn,
                code=code.upper(),
                day=day,
                open_price=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
                amount=volume * close if volume is not None else None,
                pct_chg=None,
                data_source="stooq_us_oos_daily",
            )
            count += 1
    if count == 0:
        raise RuntimeError(f"Stooq returned no daily rows for {code.upper()} from {start} to {end}")
    return count


def import_alphavantage_daily(db_path: Path, code: str, start: date, end: date, *, api_key: str) -> int:
    params = {
        "function": "TIME_SERIES_DAILY_ADJUSTED",
        "symbol": code.upper(),
        "outputsize": "full",
        "apikey": api_key,
    }
    payload = _http_json(ALPHAVANTAGE_API_URL, params=params)
    if "Note" in payload:
        raise RuntimeError(f"AlphaVantage rate limited: {payload['Note']}")
    if "Error Message" in payload:
        raise RuntimeError(f"AlphaVantage API error: {payload['Error Message']}")
    rows = payload.get("Time Series (Daily)") or {}
    count = 0
    with sqlite3.connect(db_path) as conn:
        for day, values in rows.items():
            parsed = parse_date(day)
            if parsed is None or parsed < start or parsed > end:
                continue
            close = _float_or_none(values.get("4. close"))
            adjusted_close = _float_or_none(values.get("5. adjusted close")) or close
            open_price = _float_or_none(values.get("1. open"))
            high = _float_or_none(values.get("2. high"))
            low = _float_or_none(values.get("3. low"))
            volume = _float_or_none(values.get("6. volume"))
            upsert_stock_bar(
                conn,
                code=code.upper(),
                day=day,
                open_price=open_price,
                high=high,
                low=low,
                close=adjusted_close,
                volume=volume,
                amount=volume * adjusted_close if volume is not None and adjusted_close is not None else None,
                pct_chg=None,
                data_source="alphavantage_us_oos_daily",
            )
            count += 1
    return count


def import_yfinance_news(db_path: Path, code: str, name: str, start: date, end: date) -> int:
    _ensure_vendor_import_path()
    try:
        import yfinance as yf  # type: ignore
    except Exception as exc:  # noqa: BLE001 - preserve import diagnostics for the report.
        raise RuntimeError("yfinance is unavailable in this Python environment") from exc
    ticker = yf.Ticker(code.upper())
    raw_items = ticker.news or []
    count = 0
    with sqlite3.connect(db_path) as conn:
        for item in raw_items:
            normalized = _normalize_yfinance_news_item(item)
            published = parse_date(normalized.get("published_date"))
            if published is None or published < start or published > end:
                continue
            upsert_news(
                conn,
                code=code.upper(),
                name=name,
                dimension="company_news",
                query=f"{code.upper()} historical news",
                provider="Yahoo Finance",
                title=normalized["title"],
                snippet=normalized.get("snippet"),
                url=normalized["url"],
                source=normalized.get("source"),
                published_date=f"{published.isoformat()} 00:00:00",
                fetched_at=datetime.utcnow().isoformat(sep=" "),
                query_source="us_oos_builder",
            )
            count += 1
    return count


def import_tavily_news(
    db_path: Path,
    code: str,
    name: str,
    start: date,
    end: date,
    *,
    api_keys: Sequence[str],
) -> int:
    queries = (
        f"{code.upper()} {name} stock company news",
        f"{code.upper()} {name} earnings stock news {start.year} {end.year}",
    )
    seen: set[tuple[str, str]] = set()
    count = 0
    with sqlite3.connect(db_path) as conn:
        for query in queries:
            payload = _tavily_search(api_keys[0], query=query, start=start, end=end, max_results=20)
            for item in payload.get("results") or []:
                if not isinstance(item, Mapping):
                    continue
                published = _news_date_from_any(
                    item.get("published_date") or item.get("publishedDate") or item.get("date")
                )
                if published is None or published < start or published > end:
                    continue
                title = str(item.get("title") or "").strip()
                url = str(item.get("url") or "").strip()
                if not title or not url:
                    continue
                dedupe_key = (url, published.isoformat())
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                upsert_news(
                    conn,
                    code=code.upper(),
                    name=name,
                    dimension="company_news",
                    query=query,
                    provider="Tavily",
                    title=title,
                    snippet=str(item.get("content") or item.get("snippet") or "").strip() or title,
                    url=url,
                    source=str(item.get("source") or _domain_from_url(url) or "Tavily").strip(),
                    published_date=f"{published.isoformat()} 00:00:00",
                    fetched_at=datetime.utcnow().isoformat(sep=" "),
                    query_source="us_oos_builder",
                )
                count += 1
            if count > 0:
                break
    return count


def import_alphavantage_news(db_path: Path, code: str, name: str, start: date, end: date, *, api_key: str) -> int:
    params = {
        "function": "NEWS_SENTIMENT",
        "tickers": code.upper(),
        "time_from": f"{start:%Y%m%d}T0000",
        "time_to": f"{end:%Y%m%d}T2359",
        "limit": "1000",
        "apikey": api_key,
    }
    payload = _http_json(ALPHAVANTAGE_API_URL, params=params)
    if "Note" in payload:
        raise RuntimeError(f"AlphaVantage rate limited: {payload['Note']}")
    if "Error Message" in payload:
        raise RuntimeError(f"AlphaVantage API error: {payload['Error Message']}")
    items = payload.get("feed") or []
    count = 0
    with sqlite3.connect(db_path) as conn:
        for item in items:
            published = _alpha_news_date(item.get("time_published"))
            if published is None or published < start or published > end:
                continue
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            if not title or not url:
                continue
            upsert_news(
                conn,
                code=code.upper(),
                name=name,
                dimension="company_news",
                query=f"{code.upper()} historical news",
                provider="AlphaVantage",
                title=title,
                snippet=str(item.get("summary") or "").strip() or title,
                url=url,
                source=str(item.get("source") or "AlphaVantage").strip(),
                published_date=f"{published.isoformat()} 00:00:00",
                fetched_at=datetime.utcnow().isoformat(sep=" "),
                query_source="us_oos_builder",
            )
            count += 1
    return count


def us_news_coverage(db_path: Path, code: str, start: date, end: date) -> tuple[Optional[str], Optional[str], int]:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
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
    return row[0], row[1], int(row[2] or 0)


def render_us_oos_import_report(summary: UsOosBuildSummary) -> str:
    lines = [
        f"# US OOS Import Report {summary.generated_at}",
        "",
        f"- US OOS DB: `{summary.db_path}`",
        f"- Source DB: `{summary.source_db_path}`",
        f"- Fetch window: `{summary.start}` to `{summary.end}`",
        f"- R1 required OOS end: `{US_R1_REQUIRED_OOS_END}`",
        f"- Price provider: `{summary.price_provider}`",
        f"- News provider: `{summary.news_provider}`",
        f"- Bars ready for US R1: `{summary.bars_ready}`",
        f"- News metadata ready for US R1: `{summary.news_ready}`",
        "",
        "| code | name | copied_bars | fetched_bars | min_bar | max_bar | bars | copied_news | fetched_news | news_min | news_max | news_items | errors |",
        "| --- | --- | ---: | ---: | --- | --- | ---: | ---: | ---: | --- | --- | ---: | --- |",
    ]
    for item in summary.codes:
        lines.append(
            f"| {item.code} | {item.name} | {item.copied_bars} | {item.fetched_bars} | "
            f"{item.min_bar_date or 'N/A'} | {item.max_bar_date or 'N/A'} | {item.bar_count} | "
            f"{item.copied_news} | {item.fetched_news} | {item.news_min_date or 'N/A'} | "
            f"{item.news_max_date or 'N/A'} | {item.news_count} | {'; '.join(item.errors) or 'none'} |"
        )
    if summary.errors:
        lines.extend(["", "## Global Errors", ""])
        lines.extend(f"- {error}" for error in summary.errors)
    lines.extend(
        [
            "",
            "## R1 Readiness",
            "",
            "- Bars pass only when every requested US R1 code starts within the OOS start tolerance and extends through the required OOS end.",
            "- News metadata remains mandatory. If the selected provider cannot return dated historical news for 2024-2025, US R1 stays closed.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_us_oos_import_report(path: Path, summary: UsOosBuildSummary) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_us_oos_import_report(summary), encoding="utf-8")
    return path


def _ensure_vendor_import_path() -> None:
    root = str(VENDOR_DSA_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _read_alpha_vantage_key(path: Optional[Path]) -> Optional[str]:
    candidates = []
    if path is not None:
        candidates.append(Path(path))
    candidates.append(RUNTIME_DIR / "secrets" / "alphavantage_api_key.txt")
    for candidate in candidates:
        if candidate.exists():
            text = candidate.read_text(encoding="utf-8").strip()
            if text:
                return text
    return os.getenv("ALPHAVANTAGE_API_KEY")


def _read_tavily_keys(path: Optional[Path]) -> tuple[str, ...]:
    candidates = []
    if path is not None:
        candidates.append(Path(path))
    candidates.append(RUNTIME_DIR / "secrets" / "tavily_api_key.txt")
    raw_values: list[str] = []
    for candidate in candidates:
        if candidate.exists():
            raw_values.append(candidate.read_text(encoding="utf-8"))
    for env_name in ("TAVILY_API_KEYS", "TAVILY_API_KEY"):
        value = os.getenv(env_name)
        if value:
            raw_values.append(value)
    keys: list[str] = []
    for raw in raw_values:
        for item in raw.replace("\n", ",").split(","):
            key = item.strip()
            if key:
                keys.append(key)
    return tuple(dict.fromkeys(keys))


def _http_json(url: str, *, params: Mapping[str, str], timeout: float = 30.0) -> dict[str, Any]:
    full_url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        full_url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"URL error: {exc.reason}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("provider response was not a JSON object")
    return payload


def _http_text(url: str, *, headers: Optional[Mapping[str, str]] = None, timeout: float = 30.0) -> str:
    request_headers = dict(headers or {})
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    request = urllib.request.Request(url, headers=request_headers)
    try:
        with opener.open(request, timeout=timeout) as response:
            text = response.read().decode("utf-8")
        if "This site requires JavaScript to verify your browser" in text and "/__verify" in text:
            text = _http_text_after_stooq_verify(opener, url, request_headers, text, timeout=timeout)
        return text
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"URL error: {exc.reason}") from exc


def _http_text_after_stooq_verify(
    opener: urllib.request.OpenerDirector,
    url: str,
    headers: Mapping[str, str],
    challenge_html: str,
    *,
    timeout: float,
) -> str:
    match = re.search(r'const c="([^"]+)",d=(\d+),t="0"\.repeat\(d\)', challenge_html)
    if match is None:
        raise RuntimeError("Stooq verification challenge format was not recognized")
    challenge, difficulty_text = match.groups()
    nonce = _solve_stooq_challenge(challenge, int(difficulty_text))
    verify_url = urllib.parse.urljoin(url, "/__verify")
    verify_body = urllib.parse.urlencode({"c": challenge, "n": str(nonce)}).encode("utf-8")
    verify_headers = {
        "User-Agent": headers.get("User-Agent", "Mozilla/5.0"),
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": url,
    }
    verify_request = urllib.request.Request(verify_url, data=verify_body, headers=verify_headers, method="POST")
    with opener.open(verify_request, timeout=timeout) as response:
        if response.status >= 400:
            raise RuntimeError(f"Stooq verification failed with HTTP {response.status}")
        response.read()
    retry_request = urllib.request.Request(url, headers=dict(headers))
    with opener.open(retry_request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def _solve_stooq_challenge(challenge: str, difficulty: int) -> int:
    target = "0" * difficulty
    nonce = 0
    while True:
        digest = hashlib.sha256(f"{challenge}{nonce}".encode("utf-8")).hexdigest()
        if digest.startswith(target):
            return nonce
        nonce += 1


def _tavily_search(api_key: str, *, query: str, start: date, end: date, max_results: int) -> dict[str, Any]:
    try:
        from tavily import TavilyClient  # type: ignore
    except Exception as exc:  # noqa: BLE001 - preserve dependency diagnostics for the report.
        raise RuntimeError("tavily-python is unavailable in this Python environment") from exc
    response = TavilyClient(api_key=api_key).search(
        query=query,
        search_depth="advanced",
        topic="news",
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        max_results=max_results,
        include_answer=False,
        include_raw_content=False,
        timeout=30,
    )
    if not isinstance(response, dict):
        raise RuntimeError("Tavily response was not a JSON object")
    return response


def _normalize_yfinance_news_item(item: Mapping[str, Any]) -> dict[str, Any]:
    content = item.get("content") if isinstance(item.get("content"), Mapping) else {}
    title = str(item.get("title") or content.get("title") or "").strip()
    canonical = content.get("canonicalUrl") if isinstance(content.get("canonicalUrl"), Mapping) else {}
    link = str(item.get("link") or canonical.get("url") or "").strip()
    if not link:
        link = f"yfinance://news/{_stable_id(json.dumps(item, sort_keys=True, default=str))}"
    provider_payload = content.get("provider") if isinstance(content.get("provider"), Mapping) else {}
    provider = item.get("publisher") or provider_payload.get("displayName")
    published_raw = item.get("providerPublishTime") or content.get("pubDate") or content.get("displayTime")
    published = _news_date_from_any(published_raw)
    return {
        "title": title or "Yahoo Finance news item",
        "snippet": str(item.get("summary") or content.get("summary") or "").strip(),
        "url": link,
        "source": str(provider or "Yahoo Finance").strip(),
        "published_date": published.isoformat() if published is not None else None,
    }


def _news_date_from_any(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.utcfromtimestamp(float(value)).date()
    text = str(value).strip()
    parsed = parse_date(text)
    if parsed is None:
        try:
            return email.utils.parsedate_to_datetime(text).date()
        except (TypeError, ValueError):
            return None
    return parsed


def _alpha_news_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) >= 8 and text[:8].isdigit():
        return parse_date(f"{text[:4]}-{text[4:6]}-{text[6:8]}")
    return parse_date(text)


def _date_to_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "strftime"):
        try:
            return value.strftime("%Y-%m-%d")
        except Exception:
            pass
    parsed = parse_date(str(value))
    return parsed.isoformat() if parsed is not None else None


def _float_or_none(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _list_float_or_none(values: Sequence[Any], index: int) -> Optional[float]:
    if index >= len(values):
        return None
    return _float_or_none(values[index])


def _unix_utc(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())


def _stooq_symbol(code: str) -> str:
    symbol = code.strip().lower()
    return symbol if "." in symbol else f"{symbol}.us"


def _domain_from_url(url: str) -> Optional[str]:
    parsed = urllib.parse.urlparse(url)
    return parsed.netloc.replace("www.", "") or None


def _stable_id(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
