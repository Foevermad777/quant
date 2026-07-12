#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping, Optional


BLOCKED_EXIT_CODE = 69
PROJECT_DIR = Path(__file__).resolve().parents[1]
SECRETS_DIR = PROJECT_DIR / "runtime_data" / "secrets"


@dataclass(frozen=True)
class ProbeResult:
    name: str
    ok: bool
    latency_ms: int
    error_type: Optional[str] = None
    http_status: Optional[int] = None
    detail: Optional[str] = None


@dataclass(frozen=True)
class RouteDecision:
    status: str
    llm: Optional[str]
    market_data: Optional[str]
    news: Optional[str]
    reasons: tuple[str, ...]


def classify_http_failure(provider: str, status: int, body: str) -> str:
    normalized = (body or "").lower()
    if status == 429 or "resource_exhausted" in normalized or "quota" in normalized:
        return "quota"
    if provider == "gemini" and status == 400 and "location is not supported" in normalized:
        return "region_unsupported"
    if status in {401, 403}:
        return "authentication"
    if status >= 500:
        return "upstream"
    return "http_error"


def select_routes(probes: Mapping[str, ProbeResult], region: str = "us") -> RouteDecision:
    reasons: list[str] = []
    gemini_ok = probes.get("gemini", ProbeResult("gemini", False, 0)).ok
    deepseek_ok = probes.get("deepseek", ProbeResult("deepseek", False, 0)).ok
    tavily_ok = probes.get("tavily", ProbeResult("tavily", False, 0)).ok
    bocha_ok = probes.get("bocha", ProbeResult("bocha", False, 0)).ok

    if not gemini_ok:
        reasons.append("gemini_unavailable")
    if not deepseek_ok:
        reasons.append("deepseek_unavailable")

    llm = "gemini" if gemini_ok else "deepseek" if deepseek_ok else None

    if region == "cn":
        # CN market data comes from direct-connection domestic multi-source
        # failover (efinance/akshare/tushare/tencent) and is not probed here;
        # only the LLM route can zero out a CN day.
        if not bocha_ok:
            reasons.append("bocha_unavailable")
        if not tavily_ok:
            reasons.append("tavily_unavailable")
        news = "bocha" if bocha_ok else "tavily" if tavily_ok else None
        if llm is None:
            reasons.append("no_usable_llm")
        if news is None:
            reasons.append("no_usable_news")
        status = "blocked" if llm is None else "degraded" if reasons else "ok"
        return RouteDecision(
            status=status,
            llm=llm,
            market_data="domestic",
            news=news,
            reasons=tuple(reasons),
        )

    yahoo_ok = probes.get("yahoo", ProbeResult("yahoo", False, 0)).ok
    nasdaq_ok = probes.get("nasdaq", ProbeResult("nasdaq", False, 0)).ok

    if not yahoo_ok:
        reasons.append("yahoo_unavailable")
    if not nasdaq_ok:
        reasons.append("nasdaq_unavailable")
    if not tavily_ok:
        reasons.append("tavily_unavailable")
    if not bocha_ok:
        reasons.append("bocha_unavailable")

    market_data = "yahoo" if yahoo_ok else "nasdaq" if nasdaq_ok else None
    news = "tavily" if tavily_ok else "bocha" if bocha_ok else None

    if llm is None:
        reasons.append("no_usable_llm")
    if market_data is None:
        reasons.append("no_usable_market_data")
    if news is None:
        reasons.append("no_usable_news")

    if llm is None or market_data is None:
        status = "blocked"
    elif reasons:
        status = "degraded"
    else:
        status = "ok"

    return RouteDecision(
        status=status,
        llm=llm,
        market_data=market_data,
        news=news,
        reasons=tuple(reasons),
    )


def _read_key(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _proxy_opener(host: str, port: int) -> urllib.request.OpenerDirector:
    proxy_url = f"http://{host}:{port}"
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    )


def _direct_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _request_json(
    *,
    name: str,
    request: urllib.request.Request,
    opener: urllib.request.OpenerDirector,
    timeout: float,
    validate: Callable[[dict], bool],
) -> ProbeResult:
    started = time.monotonic()
    try:
        with opener.open(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not validate(payload):
            return ProbeResult(
                name=name,
                ok=False,
                latency_ms=int((time.monotonic() - started) * 1000),
                error_type="invalid_response",
            )
        return ProbeResult(
            name=name,
            ok=True,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="replace")
        return ProbeResult(
            name=name,
            ok=False,
            latency_ms=int((time.monotonic() - started) * 1000),
            error_type=classify_http_failure(name, exc.code, body),
            http_status=exc.code,
        )
    except (TimeoutError, socket.timeout):
        return ProbeResult(
            name=name,
            ok=False,
            latency_ms=int((time.monotonic() - started) * 1000),
            error_type="timeout",
        )
    except (urllib.error.URLError, ssl.SSLError, ConnectionError, OSError, ValueError, json.JSONDecodeError):
        return ProbeResult(
            name=name,
            ok=False,
            latency_ms=int((time.monotonic() - started) * 1000),
            error_type="transport",
        )


def probe_gemini(
    api_key: str,
    *,
    model: str,
    proxy_host: str,
    proxy_port: int,
    timeout: float,
) -> ProbeResult:
    if not api_key:
        return ProbeResult("gemini", False, 0, "missing_credentials")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": "Reply with exactly OK."}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 8},
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return _request_json(
        name="gemini",
        request=request,
        opener=_proxy_opener(proxy_host, proxy_port),
        timeout=timeout,
        validate=lambda data: bool(data.get("candidates")),
    )


def probe_deepseek(api_key: str, *, model: str, timeout: float) -> ProbeResult:
    if not api_key:
        return ProbeResult("deepseek", False, 0, "missing_credentials")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly OK."}],
        "temperature": 0,
        "max_tokens": 4,
        "stream": False,
    }
    request = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    return _request_json(
        name="deepseek",
        request=request,
        opener=_direct_opener(),
        timeout=timeout,
        validate=lambda data: bool(data.get("choices")),
    )


def probe_yahoo(
    symbol: str,
    *,
    proxy_host: str,
    proxy_port: int,
    timeout: float,
) -> ProbeResult:
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{symbol}?range=5d&interval=1d"
    )
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 DSA preflight"})
    return _request_json(
        name="yahoo",
        request=request,
        opener=_proxy_opener(proxy_host, proxy_port),
        timeout=timeout,
        validate=lambda data: bool(data.get("chart", {}).get("result")),
    )


def probe_nasdaq(symbol: str, *, timeout: float) -> ProbeResult:
    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=10)
    query = urllib.parse.urlencode(
        {
            "assetclass": "stocks",
            "fromdate": start_date.isoformat(),
            "todate": today.isoformat(),
            "limit": 20,
        }
    )
    request = urllib.request.Request(
        f"https://api.nasdaq.com/api/quote/{symbol.upper()}/historical?{query}",
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://www.nasdaq.com",
        },
    )
    return _request_json(
        name="nasdaq",
        request=request,
        opener=_direct_opener(),
        timeout=timeout,
        validate=lambda data: bool(
            (data.get("status") or {}).get("rCode") == 200
            and ((data.get("data") or {}).get("tradesTable") or {}).get("rows")
        ),
    )


def probe_tavily(api_key: str, *, symbol: str, timeout: float) -> ProbeResult:
    if not api_key:
        return ProbeResult("tavily", False, 0, "missing_credentials")
    payload = {
        "api_key": api_key,
        "query": f"{symbol} stock latest news",
        "search_depth": "basic",
        "topic": "news",
        "max_results": 1,
        "include_answer": False,
        "include_raw_content": False,
        "days": 7,
    }
    request = urllib.request.Request(
        "https://api.tavily.com/search",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return _request_json(
        name="tavily",
        request=request,
        opener=_direct_opener(),
        timeout=timeout,
        validate=lambda data: isinstance(data.get("results"), list),
    )


def probe_bocha(api_key: str, *, symbol: str, timeout: float) -> ProbeResult:
    if not api_key:
        return ProbeResult("bocha", False, 0, "missing_credentials")
    payload = {
        "query": f"{symbol} stock latest news",
        "freshness": "oneWeek",
        "summary": False,
        "count": 1,
    }
    request = urllib.request.Request(
        "https://api.bocha.cn/v1/web-search",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    return _request_json(
        name="bocha",
        request=request,
        opener=_direct_opener(),
        timeout=timeout,
        validate=lambda data: bool(
            data.get("code") == 200
            and isinstance(
                ((data.get("data") or {}).get("webPages") or {}).get("value"),
                list,
            )
        ),
    )
def run_preflight(args: argparse.Namespace) -> tuple[dict, RouteDecision]:
    gemini_key = _read_key(args.gemini_key_file)
    deepseek_key = _read_key(args.deepseek_key_file)
    tavily_key = _read_key(args.tavily_key_file)
    bocha_key = _read_key(args.bocha_key_file)
    jobs = {
        "gemini": lambda: probe_gemini(
            gemini_key,
            model=args.gemini_model,
            proxy_host=args.proxy_host,
            proxy_port=args.proxy_port,
            timeout=args.timeout,
        ),
        "deepseek": lambda: probe_deepseek(
            deepseek_key,
            model=args.deepseek_model,
            timeout=args.timeout,
        ),
        "tavily": lambda: probe_tavily(tavily_key, symbol=args.symbol, timeout=args.timeout),
        "bocha": lambda: probe_bocha(bocha_key, symbol=args.symbol, timeout=args.timeout),
    }
    if args.region == "us":
        jobs["yahoo"] = lambda: probe_yahoo(
            args.symbol,
            proxy_host=args.proxy_host,
            proxy_port=args.proxy_port,
            timeout=args.timeout,
        )
        jobs["nasdaq"] = lambda: probe_nasdaq(args.symbol, timeout=args.timeout)
    probes: dict[str, ProbeResult] = {}
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {pool.submit(job): name for name, job in jobs.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                probes[name] = future.result()
            except Exception:
                probes[name] = ProbeResult(name, False, 0, "internal_error")

    decision = select_routes(probes, region=args.region)
    result = {
        "status": decision.status,
        "routes": {
            "llm": decision.llm,
            "market_data": decision.market_data,
            "news": decision.news,
        },
        "reasons": list(decision.reasons),
        "probes": {name: asdict(probes[name]) for name in sorted(probes)},
        "region": args.region,
        "symbol": args.symbol,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    return result, decision


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe DSA providers and select safe runtime routes (CN or US track).")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--region", choices=("us", "cn"), default="us")
    parser.add_argument("--gemini-key-file", type=Path, default=SECRETS_DIR / "gemini_api_key.txt")
    parser.add_argument("--deepseek-key-file", type=Path, default=SECRETS_DIR / "deepseek_api_key.txt")
    parser.add_argument("--tavily-key-file", type=Path, default=SECRETS_DIR / "tavily_api_key.txt")
    parser.add_argument("--bocha-key-file", type=Path, default=SECRETS_DIR / "bocha_api_key.txt")
    parser.add_argument("--gemini-model", default="gemini-3.5-flash")
    parser.add_argument("--deepseek-model", default="deepseek-chat")
    parser.add_argument("--proxy-host", default="127.0.0.1")
    parser.add_argument("--proxy-port", type=int, default=7890)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--symbol", default=None, help="Probe symbol; defaults to AAPL (us) / 600519 (cn).")
    args = parser.parse_args()
    if args.symbol is None:
        args.symbol = "AAPL" if args.region == "us" else "600519"
    return args


def main() -> int:
    args = parse_args()
    result, decision = run_preflight(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = args.output.with_suffix(args.output.suffix + ".tmp")
    temp_path.write_text(json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n", encoding="utf-8")
    temp_path.replace(args.output)
    reasons = ",".join(decision.reasons) or "none"
    print(
        "preflight "
        f"status={decision.status} llm={decision.llm or 'none'} "
        f"market_data={decision.market_data or 'none'} news={decision.news or 'none'} "
        f"reasons={reasons} output={args.output}"
    )
    return BLOCKED_EXIT_CODE if decision.status == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
