from __future__ import annotations

import argparse
import json
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from executor.config import (
    DEEPSEEK_API_KEY_PATH,
    DISCIPLINE_SKILL_PATH,
    DSA_DB_PATH,
    G5_DEFAULT_FALLBACK_MODEL,
    G5_COMPLETION_VERSION,
    G5_DEFAULT_MODEL,
    G5_SCHEMA_VERSION,
    GEMINI_API_KEY_PATH,
    PAPER_DB_PATH,
)
from executor.guardrails import GuardrailResult, gate_dsa_output
from executor.intent_resolution import normalize_action, resolve_intent
from executor.signal_reader import parse_datetime


GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEEPSEEK_CHAT_COMPLETIONS_URL = "https://api.deepseek.com/chat/completions"
GEMINI_PROXY_HOST = "127.0.0.1"
GEMINI_PROXY_PORT = 7890
ESTIMATED_COST_USD = 0.05
G5_DEFAULT_WORKERS = 4
G5_DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
G5_DEFAULT_FALLBACK_TIMEOUT_SECONDS = 20.0
G5_DEFAULT_SLOW_THRESHOLD_MS = 15_000
G5_DEFAULT_PRIMARY_FAILURE_THRESHOLD = 2
G5_PROXY_PREFLIGHT_TIMEOUT_SECONDS = 2.0
G5_SQLITE_BUSY_TIMEOUT_SECONDS = 10.0
A_SHARE_OPEN_TIME = datetime_time(9, 30, 0)
A_SHARE_CLOSE_TIME = datetime_time(15, 0, 0)
US_REGULAR_OPEN_TIME = datetime_time(9, 30, 0)
US_REGULAR_CLOSE_TIME = datetime_time(16, 0, 0)
CN_MARKET_TZ = ZoneInfo("Asia/Shanghai")
US_MARKET_TZ = ZoneInfo("America/New_York")
NAIVE_TIMESTAMP_DEFAULT_TZ = CN_MARKET_TZ
DISCIPLINED_TEMPORAL_COLUMNS = {
    "decision_timestamp": "text",
    "market_phase": "text",
    "data_asof": "text",
    "bar_cutoff": "text",
    "news_cutoff": "text",
}
DISCIPLINED_INTENT_COLUMNS = {
    "flat_account_action": "text",
    "holding_action": "text",
    "resolved_action": "text",
    "conflict_status": "text",
    "conflict_reason": "text",
}


@dataclass(frozen=True)
class MarketClock:
    market: str
    timezone: ZoneInfo
    open_time: datetime_time
    close_time: datetime_time


MARKET_CLOCKS = {
    "cn": MarketClock("cn", CN_MARKET_TZ, A_SHARE_OPEN_TIME, A_SHARE_CLOSE_TIME),
    "a": MarketClock("cn", CN_MARKET_TZ, A_SHARE_OPEN_TIME, A_SHARE_CLOSE_TIME),
    "a_share": MarketClock("cn", CN_MARKET_TZ, A_SHARE_OPEN_TIME, A_SHARE_CLOSE_TIME),
    "ashare": MarketClock("cn", CN_MARKET_TZ, A_SHARE_OPEN_TIME, A_SHARE_CLOSE_TIME),
    "us": MarketClock("us", US_MARKET_TZ, US_REGULAR_OPEN_TIME, US_REGULAR_CLOSE_TIME),
}


DISCIPLINE_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scenarios": {
            "type": "object",
            "properties": {
                "base": {
                    "type": "object",
                    "properties": {
                        "assumptions": {"type": "array", "items": {"type": "string"}},
                        "triggers": {"type": "array", "items": {"type": "string"}},
                        "key_risks": {"type": "array", "items": {"type": "string"}},
                        "probability": {"type": "number"},
                    },
                    "required": ["assumptions", "triggers", "key_risks", "probability"],
                },
                "bull": {
                    "type": "object",
                    "properties": {
                        "assumptions": {"type": "array", "items": {"type": "string"}},
                        "triggers": {"type": "array", "items": {"type": "string"}},
                        "key_risks": {"type": "array", "items": {"type": "string"}},
                        "probability": {"type": "number"},
                    },
                    "required": ["assumptions", "triggers", "key_risks", "probability"],
                },
                "bear": {
                    "type": "object",
                    "properties": {
                        "assumptions": {"type": "array", "items": {"type": "string"}},
                        "triggers": {"type": "array", "items": {"type": "string"}},
                        "key_risks": {"type": "array", "items": {"type": "string"}},
                        "probability": {"type": "number"},
                    },
                    "required": ["assumptions", "triggers", "key_risks", "probability"],
                },
            },
            "required": ["base", "bull", "bear"],
        },
        "invalid_conditions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "condition": {"type": "string"},
                    "trigger_price_or_data": {"type": "string"},
                    "type": {"type": "string", "enum": ["price", "data", "event"]},
                },
                "required": ["condition", "trigger_price_or_data", "type"],
            },
        },
        "source_attribution": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "source": {"type": "string"},
                    "published_date": {"type": "string"},
                },
                "required": ["claim", "source", "published_date"],
            },
        },
        "confidence": {"type": "number"},
        "confidence_rationale": {"type": "string"},
        "single_side_flag": {"type": "boolean"},
        "flat_account_action": {
            "type": "string",
            "enum": ["buy", "add", "sell", "reduce", "avoid", "hold", "watch", "alert"],
        },
        "holding_action": {
            "type": "string",
            "enum": ["buy", "add", "sell", "reduce", "avoid", "hold", "watch", "alert"],
        },
        "resolved_action": {
            "type": "string",
            "enum": ["buy", "add", "sell", "reduce", "avoid", "hold", "watch", "alert"],
        },
        "conflict_status": {
            "type": "string",
            "enum": ["hard_conflict", "conditional_entry", "position_context_split", "consistent"],
        },
        "conflict_reason": {"type": "string"},
        "normalized_terms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "original_term": {"type": "string"},
                    "normalized_term": {"type": "string"},
                },
                "required": ["original_term", "normalized_term"],
            },
        },
    },
    "required": [
        "scenarios",
        "invalid_conditions",
        "source_attribution",
        "confidence",
        "confidence_rationale",
        "single_side_flag",
        "flat_account_action",
        "holding_action",
        "resolved_action",
        "conflict_status",
        "conflict_reason",
    ],
}


@dataclass(frozen=True)
class CompletionContext:
    signal: dict[str, Any]
    analysis: dict[str, Any]
    dated_news: list[dict[str, Any]]
    undated_news: list[dict[str, Any]]


@dataclass(frozen=True)
class GeminiUsage:
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    total_tokens: Optional[int]
    latency_ms: int


@dataclass(frozen=True)
class CompletionSummary:
    source_signal_id: int
    stock_code: Optional[str]
    skipped: bool
    gate_accepted: bool
    gate_action: str
    gate_reasons: tuple[str, ...]
    model: str
    latency_ms: Optional[int]
    attempts: int = 1
    error: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_signal_id": self.source_signal_id,
            "stock_code": self.stock_code,
            "skipped": self.skipped,
            "gate_accepted": self.gate_accepted,
            "gate_action": self.gate_action,
            "gate_reasons": list(self.gate_reasons),
            "model": self.model,
            "latency_ms": self.latency_ms,
            "attempts": self.attempts,
            "error": self.error,
        }


class DsaSignalContextLoader:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        uri = f"file:{self.db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def active_signal_ids(
        self,
        stock_codes: Optional[Sequence[str]] = None,
        market: Optional[str] = None,
    ) -> list[int]:
        params: list[Any] = []
        predicate = "status = 'active'"
        if market:
            predicate += " and market = ?"
            params.append(str(market).strip().lower())
        if stock_codes:
            placeholders = ",".join("?" for _ in stock_codes)
            predicate += f" and stock_code in ({placeholders})"
            params.extend(stock_codes)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                select id
                from decision_signals
                where {predicate}
                order by datetime(created_at), id
                """,
                params,
            ).fetchall()
        return [int(row["id"]) for row in rows]

    def stock_code_for_signal(self, signal_id: int) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute("select stock_code from decision_signals where id = ?", (signal_id,)).fetchone()
        return str(row["stock_code"]) if row is not None else None

    def load(self, signal_id: int) -> CompletionContext:
        with self._connect() as conn:
            signal_row = conn.execute("select * from decision_signals where id = ?", (signal_id,)).fetchone()
            if signal_row is None:
                raise KeyError(f"DSA decision signal not found: {signal_id}")
            signal = _row_dict(signal_row)
            signal_created_at = parse_datetime(signal.get("created_at"))
            analysis_row = self._analysis_row(conn, signal)
            analysis = _row_dict(analysis_row) if analysis_row is not None else {}
            dated_news_predicate = "code = ? and published_date is not null and trim(published_date) != ''"
            dated_news_params: list[Any] = [signal["stock_code"]]
            if signal_created_at is not None:
                dated_news_predicate += " and datetime(published_date) <= datetime(?)"
                dated_news_params.append(signal_created_at.isoformat(sep=" "))
            dated_news = [
                _news_row_dict(row)
                for row in conn.execute(
                    f"""
                    select id, title, snippet, url, source, provider, published_date
                    from news_intel
                    where {dated_news_predicate}
                    order by datetime(published_date) desc, id desc
                    limit 25
                    """,
                    dated_news_params,
                ).fetchall()
            ]
            undated_news = [
                _news_row_dict(row)
                for row in conn.execute(
                    """
                    select id, title, snippet, url, source, provider, published_date
                    from news_intel
                    where code = ? and (published_date is null or trim(published_date) = '')
                    order by id desc
                    limit 10
                    """,
                    (signal["stock_code"],),
                ).fetchall()
            ]
        return CompletionContext(signal=signal, analysis=analysis, dated_news=dated_news, undated_news=undated_news)

    def _analysis_row(self, conn: sqlite3.Connection, signal: Mapping[str, Any]) -> Optional[sqlite3.Row]:
        source_report_id = signal.get("source_report_id")
        if source_report_id is not None:
            row = conn.execute("select * from analysis_history where id = ?", (source_report_id,)).fetchone()
            if row is not None:
                return row
        created_at = parse_datetime(signal.get("created_at"))
        created_at_text = created_at.isoformat(sep=" ") if created_at is not None else "9999-12-31"
        return conn.execute(
            """
            select *
            from analysis_history
            where code = ? and datetime(created_at) <= datetime(?)
            order by datetime(created_at) desc, id desc
            limit 1
            """,
            (signal["stock_code"], created_at_text),
        ).fetchone()


class DisciplinedSignalStore:
    def __init__(self, db_path: Path, *, busy_timeout_seconds: float = G5_SQLITE_BUSY_TIMEOUT_SECONDS) -> None:
        self.db_path = Path(db_path)
        self.busy_timeout_seconds = busy_timeout_seconds
        self._initialize_lock = threading.Lock()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=self.busy_timeout_seconds)
        conn.row_factory = sqlite3.Row
        conn.execute(f"pragma busy_timeout = {int(self.busy_timeout_seconds * 1000)}")
        return conn

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
            with self._connect() as conn:
                conn.execute("pragma journal_mode = wal")
                conn.execute("pragma synchronous = normal")
                conn.executescript(
                    """
                    create table if not exists disciplined_signals (
                        source_signal_id integer primary key,
                        source_report_id integer,
                        stock_code text not null,
                        stock_name text,
                        market text not null,
                        action text not null,
                        confidence real,
                        score integer,
                        entry_low real,
                        entry_high real,
                        stop_loss real,
                        target_price real,
                        status text not null,
                        created_at text,
                        expires_at text,
                        decision_timestamp text,
                        market_phase text,
                        data_asof text,
                        bar_cutoff text,
                        news_cutoff text,
                        plan_quality text,
                        schema_version text not null,
                        completion_version text not null,
                        completed_at text not null,
                        updated_at text not null,
                        model text not null,
                        prompt_tokens integer,
                        completion_tokens integer,
                        total_tokens integer,
                        latency_ms integer,
                        estimated_cost_usd real,
                        scenarios_json text not null,
                        invalid_conditions_json text not null,
                        source_attribution_json text not null,
                        confidence_rationale text,
                        single_side_flag integer not null,
                        flat_account_action text,
                        holding_action text,
                        resolved_action text,
                        conflict_status text,
                        conflict_reason text,
                        normalized_terms_json text,
                        completion_payload_json text not null,
                        raw_dsa_signal_json text not null,
                        dsa_analysis_json text not null,
                        dated_news_json text not null,
                        undated_news_json text not null,
                        guardrail_json text not null,
                        gate_accepted integer not null,
                        gate_action text not null,
                        gate_reasons_json text not null
                    );

                    create index if not exists ix_disciplined_signals_stock_status_time
                        on disciplined_signals(stock_code, status, created_at);
                    create index if not exists ix_disciplined_signals_gate
                        on disciplined_signals(gate_accepted, gate_action);
                    create index if not exists ix_disciplined_signals_completed_at
                        on disciplined_signals(completed_at);
                    """
                )
                ensure_disciplined_temporal_columns(conn)
                ensure_disciplined_intent_columns(conn)
            self._initialized = True

    def get(self, source_signal_id: int) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            try:
                return conn.execute(
                    "select * from disciplined_signals where source_signal_id = ?",
                    (source_signal_id,),
                ).fetchone()
            except sqlite3.OperationalError:
                return None

    def save(
        self,
        *,
        context: CompletionContext,
        completion_payload: Mapping[str, Any],
        guardrail_result: GuardrailResult,
        usage: GeminiUsage,
        model: str,
        force: bool = False,
    ) -> bool:
        self.initialize()
        signal = context.signal
        now = datetime.utcnow().isoformat(sep=" ")
        source_signal_id = int(signal["id"])
        temporal = discipline_temporal_metadata(context.signal, context.analysis)
        row = {
            "source_signal_id": source_signal_id,
            "source_report_id": signal.get("source_report_id"),
            "stock_code": signal["stock_code"],
            "stock_name": signal.get("stock_name"),
            "market": signal.get("market") or "cn",
            "action": signal["action"],
            "confidence": completion_payload.get("confidence"),
            "score": signal.get("score"),
            "entry_low": signal.get("entry_low"),
            "entry_high": signal.get("entry_high"),
            "stop_loss": signal.get("stop_loss"),
            "target_price": signal.get("target_price"),
            "status": signal.get("status") or "active",
            "created_at": _text_or_none(signal.get("created_at")),
            "expires_at": _text_or_none(signal.get("expires_at")),
            "decision_timestamp": temporal["decision_timestamp"],
            "market_phase": temporal["market_phase"],
            "data_asof": temporal["data_asof"],
            "bar_cutoff": temporal["bar_cutoff"],
            "news_cutoff": temporal["news_cutoff"],
            "plan_quality": signal.get("plan_quality"),
            "schema_version": G5_SCHEMA_VERSION,
            "completion_version": G5_COMPLETION_VERSION,
            "completed_at": now,
            "updated_at": now,
            "model": model,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
            "latency_ms": usage.latency_ms,
            "estimated_cost_usd": ESTIMATED_COST_USD,
            "scenarios_json": _json_dumps(completion_payload["scenarios"]),
            "invalid_conditions_json": _json_dumps(completion_payload["invalid_conditions"]),
            "source_attribution_json": _json_dumps(completion_payload["source_attribution"]),
            "confidence_rationale": completion_payload.get("confidence_rationale"),
            "single_side_flag": 1 if completion_payload.get("single_side_flag") else 0,
            "flat_account_action": completion_payload.get("flat_account_action"),
            "holding_action": completion_payload.get("holding_action"),
            "resolved_action": completion_payload.get("resolved_action"),
            "conflict_status": completion_payload.get("conflict_status"),
            "conflict_reason": completion_payload.get("conflict_reason"),
            "normalized_terms_json": _json_dumps(completion_payload.get("normalized_terms") or []),
            "completion_payload_json": _json_dumps(completion_payload),
            "raw_dsa_signal_json": _json_dumps(context.signal),
            "dsa_analysis_json": _json_dumps(context.analysis),
            "dated_news_json": _json_dumps(context.dated_news),
            "undated_news_json": _json_dumps(context.undated_news),
            "guardrail_json": _json_dumps(guardrail_result.signal.get("guardrail", {})),
            "gate_accepted": 1 if guardrail_result.accepted else 0,
            "gate_action": guardrail_result.action,
            "gate_reasons_json": _json_dumps(list(guardrail_result.gate_reasons)),
        }
        columns = list(row)
        placeholders = ",".join("?" for _ in columns)
        sql = f"""
            insert {"or replace" if force else "or ignore"} into disciplined_signals(
                {",".join(columns)}
            )
            values ({placeholders})
        """
        with self._connect() as conn:
            conn.execute("begin immediate")
            try:
                cursor = conn.execute(sql, [row[column] for column in columns])
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            return cursor.rowcount == 1


class GeminiStructuredClient:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = G5_DEFAULT_MODEL,
        proxy_host: str = GEMINI_PROXY_HOST,
        proxy_port: int = GEMINI_PROXY_PORT,
        timeout: float = G5_DEFAULT_REQUEST_TIMEOUT_SECONDS,
        preflight_timeout: float = G5_PROXY_PREFLIGHT_TIMEOUT_SECONDS,
    ) -> None:
        self.api_key = api_key.strip()
        self.model = model
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self.timeout = timeout
        self.preflight_timeout = preflight_timeout

    @classmethod
    def from_key_file(cls, key_path: Path, **kwargs: Any) -> "GeminiStructuredClient":
        api_key = Path(key_path).read_text(encoding="utf-8").strip()
        if not api_key:
            raise RuntimeError(f"Gemini API key file is empty: {key_path}")
        return cls(api_key, **kwargs)

    def generate_json(self, prompt: str, schema: Mapping[str, Any]) -> tuple[dict[str, Any], GeminiUsage]:
        self._preflight_proxy()
        request_payload = self._request_payload(prompt, schema, schema_style="legacy")
        try:
            return self._post_generate_content(request_payload)
        except RuntimeError as exc:
            if "http_status=400" not in str(exc):
                raise
            request_payload = self._request_payload(prompt, schema, schema_style="response_format")
            return self._post_generate_content(request_payload)

    def _preflight_proxy(self) -> None:
        with socket.create_connection((self.proxy_host, self.proxy_port), timeout=self.preflight_timeout):
            return

    def _request_payload(self, prompt: str, schema: Mapping[str, Any], *, schema_style: str) -> dict[str, Any]:
        generation_config: dict[str, Any] = {"temperature": 0.15}
        if schema_style == "legacy":
            generation_config["responseMimeType"] = "application/json"
            generation_config["responseSchema"] = schema
        elif schema_style == "response_format":
            generation_config["responseFormat"] = {
                "type": "json_schema",
                "jsonSchema": {"name": "discipline_signal", "schema": schema},
            }
        else:
            raise ValueError(f"unknown Gemini schema style: {schema_style}")
        return {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }

    def _post_generate_content(self, payload: Mapping[str, Any]) -> tuple[dict[str, Any], GeminiUsage]:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler(
                {
                    "http": f"http://{self.proxy_host}:{self.proxy_port}",
                    "https": f"http://{self.proxy_host}:{self.proxy_port}",
                }
            )
        )
        url = f"{GEMINI_API_URL.format(model=self.model)}?key={self.api_key}"
        request = urllib.request.Request(
            url,
            data=_json_dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        start = time.monotonic()
        try:
            with opener.open(request, timeout=self.timeout) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Gemini generateContent failed http_status={exc.code} body={body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Gemini generateContent failed url_error={exc.reason}") from exc
        latency_ms = int((time.monotonic() - start) * 1000)
        text = _candidate_text(response_payload)
        usage = _usage_from_response(response_payload, latency_ms=latency_ms)
        return _loads_completion_json(text), usage


class OpenAICompatibleStructuredClient:
    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        api_url: str,
        timeout: float = G5_DEFAULT_FALLBACK_TIMEOUT_SECONDS,
    ) -> None:
        self.api_key = api_key.strip()
        self.model = model
        self.api_url = api_url
        self.timeout = timeout

    @classmethod
    def from_key_file(cls, key_path: Path, **kwargs: Any) -> "OpenAICompatibleStructuredClient":
        api_key = Path(key_path).read_text(encoding="utf-8").strip()
        if not api_key:
            raise RuntimeError(f"fallback API key file is empty: {key_path}")
        return cls(api_key, **kwargs)

    def generate_json(self, prompt: str, schema: Mapping[str, Any]) -> tuple[dict[str, Any], GeminiUsage]:
        request_payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are the G5 discipline completion layer. Return only a valid JSON object. "
                        "Do not include markdown fences or explanatory text."
                    ),
                },
                {
                    "role": "user",
                    "content": "\n\n".join(
                        [
                            prompt,
                            "Response JSON schema:",
                            _json_dumps(schema),
                        ]
                    ),
                },
            ],
            "temperature": 0.15,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            self.api_url,
            data=_json_dumps(request_payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        # DeepSeek is reachable directly from the mainland; sharing Gemini's
        # proxy egress took the fallback down together with the primary on
        # 2026-07-09, so bypass HTTP(S)_PROXY env for this provider.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        start = time.monotonic()
        try:
            with opener.open(request, timeout=self.timeout) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"fallback chat completion failed http_status={exc.code} body={body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"fallback chat completion failed url_error={exc.reason}") from exc
        latency_ms = int((time.monotonic() - start) * 1000)
        text = _openai_chat_text(response_payload)
        usage = _openai_usage_from_response(response_payload, latency_ms=latency_ms)
        return _loads_completion_json(text), usage


class G5CircuitBreaker:
    def __init__(self, *, failure_threshold: int, slow_threshold_ms: int) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.slow_threshold_ms = max(1, slow_threshold_ms)
        self._lock = threading.Lock()
        self._consecutive_bad = 0

    def should_bypass_primary(self) -> bool:
        with self._lock:
            return self._consecutive_bad >= self.failure_threshold

    def record_success(self, latency_ms: Optional[int]) -> bool:
        if latency_ms is not None and latency_ms > self.slow_threshold_ms:
            return self.record_bad()
        with self._lock:
            self._consecutive_bad = 0
            return False

    def record_bad(self) -> bool:
        with self._lock:
            self._consecutive_bad += 1
            return self._consecutive_bad >= self.failure_threshold

    def record_fallback_success(self) -> None:
        with self._lock:
            self._consecutive_bad = self.failure_threshold


class RoutedStructuredClient:
    def __init__(
        self,
        *,
        primary: Any,
        fallback: Optional[Any],
        circuit_breaker: G5CircuitBreaker,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.circuit_breaker = circuit_breaker
        self._local = threading.local()
        self.model = str(getattr(primary, "model", "primary"))

    def generate_json(self, prompt: str, schema: Mapping[str, Any]) -> tuple[dict[str, Any], GeminiUsage]:
        if self.fallback is not None and self.circuit_breaker.should_bypass_primary():
            return self._generate_with(self.fallback, prompt, schema)
        try:
            payload, usage = self._generate_with(self.primary, prompt, schema)
        except Exception as exc:
            if self.fallback is None or not _is_fallback_trigger_error(exc):
                raise
            should_fallback = self.circuit_breaker.record_bad() or _is_immediate_fallback_error(exc)
            if not should_fallback:
                raise
            return self._generate_with(self.fallback, prompt, schema)
        self.circuit_breaker.record_success(usage.latency_ms)
        return payload, usage

    def _generate_with(self, client: Any, prompt: str, schema: Mapping[str, Any]) -> tuple[dict[str, Any], GeminiUsage]:
        payload, usage = client.generate_json(prompt, schema)
        self.model = str(getattr(client, "model", self.model))
        self._local.model = self.model
        if client is self.fallback:
            self.circuit_breaker.record_fallback_success()
        return payload, usage

    def actual_model(self) -> str:
        return str(getattr(self._local, "model", self.model))


class DisciplineCompleter:
    def __init__(
        self,
        *,
        dsa_db_path: Path = DSA_DB_PATH,
        store_db_path: Path = PAPER_DB_PATH,
        discipline_skill_path: Path = DISCIPLINE_SKILL_PATH,
        key_path: Path = GEMINI_API_KEY_PATH,
        model: str = G5_DEFAULT_MODEL,
        client: Optional[Any] = None,
        request_timeout_seconds: float = G5_DEFAULT_REQUEST_TIMEOUT_SECONDS,
        fallback_key_path: Optional[Path] = DEEPSEEK_API_KEY_PATH,
        fallback_model: str = G5_DEFAULT_FALLBACK_MODEL,
        fallback_provider: str = "deepseek",
        fallback_timeout_seconds: float = G5_DEFAULT_FALLBACK_TIMEOUT_SECONDS,
        slow_threshold_ms: int = G5_DEFAULT_SLOW_THRESHOLD_MS,
        primary_failure_threshold: int = G5_DEFAULT_PRIMARY_FAILURE_THRESHOLD,
    ) -> None:
        self.loader = DsaSignalContextLoader(dsa_db_path)
        self.store = DisciplinedSignalStore(store_db_path)
        self.discipline_skill_path = Path(discipline_skill_path)
        self.key_path = Path(key_path)
        self.model = model
        self.client = client
        self.request_timeout_seconds = request_timeout_seconds
        self.fallback_key_path = Path(fallback_key_path) if fallback_key_path is not None else None
        self.fallback_model = fallback_model
        self.fallback_provider = fallback_provider
        self.fallback_timeout_seconds = fallback_timeout_seconds
        self.slow_threshold_ms = slow_threshold_ms
        self.primary_failure_threshold = primary_failure_threshold
        self._client_lock = threading.Lock()
        self._structured_client: Optional[Any] = client

    def complete_signal(self, source_signal_id: int, *, force: bool = False) -> CompletionSummary:
        existing = self.store.get(source_signal_id)
        if existing is not None and not force:
            return CompletionSummary(
                source_signal_id=source_signal_id,
                stock_code=existing["stock_code"],
                skipped=True,
                gate_accepted=bool(existing["gate_accepted"]),
                gate_action=existing["gate_action"],
                gate_reasons=tuple(json.loads(existing["gate_reasons_json"] or "[]")),
                model=existing["model"],
                latency_ms=existing["latency_ms"],
                attempts=0,
            )

        context = self.loader.load(source_signal_id)
        prompt = build_completion_prompt(context, self.discipline_skill_path)
        client = self._structured_completion_client()
        structured, usage = client.generate_json(prompt, DISCIPLINE_RESPONSE_SCHEMA)
        actual_model = _client_model(client)
        completion_payload = normalize_completion_payload(structured, context)
        guardrail_result = gate_dsa_output(_guardrail_payload(context.signal, completion_payload), mode="reject")
        self.store.save(
            context=context,
            completion_payload=completion_payload,
            guardrail_result=guardrail_result,
            usage=usage,
            model=actual_model,
            force=force,
        )
        return CompletionSummary(
            source_signal_id=source_signal_id,
            stock_code=context.signal.get("stock_code"),
            skipped=False,
            gate_accepted=guardrail_result.accepted,
            gate_action=guardrail_result.action,
            gate_reasons=guardrail_result.gate_reasons,
            model=actual_model,
            latency_ms=usage.latency_ms,
        )

    def _structured_completion_client(self) -> Any:
        if self._structured_client is not None:
            return self._structured_client
        with self._client_lock:
            if self._structured_client is not None:
                return self._structured_client
            primary = GeminiStructuredClient.from_key_file(
                self.key_path,
                model=self.model,
                timeout=self.request_timeout_seconds,
            )
            fallback = self._fallback_client()
            if fallback is None:
                self._structured_client = primary
            else:
                self._structured_client = RoutedStructuredClient(
                    primary=primary,
                    fallback=fallback,
                    circuit_breaker=G5CircuitBreaker(
                        failure_threshold=self.primary_failure_threshold,
                        slow_threshold_ms=self.slow_threshold_ms,
                    ),
                )
            return self._structured_client

    def _fallback_client(self) -> Optional[Any]:
        if self.fallback_key_path is None or not self.fallback_key_path.exists():
            return None
        provider = (self.fallback_provider or "").strip().lower()
        if provider == "none":
            return None
        if provider == "deepseek":
            return OpenAICompatibleStructuredClient.from_key_file(
                self.fallback_key_path,
                model=self.fallback_model,
                api_url=DEEPSEEK_CHAT_COMPLETIONS_URL,
                timeout=self.fallback_timeout_seconds,
            )
        raise ValueError(f"unsupported fallback provider: {self.fallback_provider}")

    def complete_many(
        self,
        signal_ids: Iterable[int],
        *,
        force: bool = False,
        retries: int = 1,
        retry_delay_seconds: float = 1.0,
        workers: int = 1,
    ) -> list[CompletionSummary]:
        return self.complete_many_with_retries(
            signal_ids,
            force=force,
            retries=retries,
            retry_delay_seconds=retry_delay_seconds,
            workers=workers,
        )

    def complete_many_with_retries(
        self,
        signal_ids: Iterable[int],
        *,
        force: bool = False,
        retries: int = 1,
        retry_delay_seconds: float = 1.0,
        workers: int = 1,
    ) -> list[CompletionSummary]:
        signal_id_list = list(signal_ids)
        if not signal_id_list:
            return []
        max_attempts = max(1, retries + 1)
        worker_count = max(1, min(workers, len(signal_id_list)))
        if worker_count == 1:
            return [
                self._complete_signal_with_retries(
                    signal_id,
                    force=force,
                    max_attempts=max_attempts,
                    retry_delay_seconds=retry_delay_seconds,
                )
                for signal_id in signal_id_list
            ]

        self.store.initialize()
        summaries: list[Optional[CompletionSummary]] = [None] * len(signal_id_list)
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="g5-completion") as executor:
            futures = {
                executor.submit(
                    self._complete_signal_with_retries,
                    signal_id,
                    force=force,
                    max_attempts=max_attempts,
                    retry_delay_seconds=retry_delay_seconds,
                ): (index, signal_id)
                for index, signal_id in enumerate(signal_id_list)
            }
            for future in as_completed(futures):
                index, signal_id = futures[future]
                try:
                    summaries[index] = future.result()
                except Exception as exc:  # noqa: BLE001 - executor failure should not hide remaining summaries.
                    summaries[index] = self._failure_summary(signal_id, exc, attempts=max_attempts)
        return [summary for summary in summaries if summary is not None]

    def _complete_signal_with_retries(
        self,
        source_signal_id: int,
        *,
        force: bool,
        max_attempts: int,
        retry_delay_seconds: float,
    ) -> CompletionSummary:
        attempt = 1
        while True:
            try:
                summary = self.complete_signal(source_signal_id, force=force)
                return replace(summary, attempts=0 if summary.skipped else attempt)
            except Exception as exc:  # noqa: BLE001 - batch mode must isolate one signal failure.
                if attempt < max_attempts and _is_retryable_completion_error(exc):
                    if retry_delay_seconds > 0:
                        time.sleep(retry_delay_seconds)
                    attempt += 1
                    continue
                return self._failure_summary(source_signal_id, exc, attempts=attempt)

    def _failure_summary(self, source_signal_id: int, exc: Exception, *, attempts: int) -> CompletionSummary:
        return CompletionSummary(
            source_signal_id=source_signal_id,
            stock_code=self._safe_stock_code(source_signal_id),
            skipped=False,
            gate_accepted=False,
            gate_action="error",
            gate_reasons=(exc.__class__.__name__,),
            model=self._summary_model(),
            latency_ms=None,
            attempts=attempts,
            error=_safe_error_text(exc),
        )

    def _safe_stock_code(self, source_signal_id: int) -> Optional[str]:
        try:
            return self.loader.stock_code_for_signal(source_signal_id)
        except Exception:  # noqa: BLE001 - best-effort diagnostic only.
            return None

    def _summary_model(self) -> str:
        client = self._structured_client or self.client
        return _client_model(client) if client is not None else self.model


def build_completion_prompt(context: CompletionContext, discipline_skill_path: Path) -> str:
    discipline_text = Path(discipline_skill_path).read_text(encoding="utf-8")
    prompt_input = {
        "dsa_decision_signal": _signal_prompt_fields(context.signal),
        "dsa_analysis_history": _analysis_prompt_fields(context.analysis),
        "dated_news_intel_core_sources": context.dated_news,
        "undated_news_intel_clues_only": context.undated_news,
    }
    return "\n".join(
        [
            "You are the G5 discipline completion layer for a paper-trading executor.",
            "Do not change DSA source data. Complete missing discipline fields from the provided read-only inputs.",
            "Return only JSON that matches the provided response schema.",
            "Hard constraints:",
            "1. source_attribution may cite only dated_news_intel_core_sources. Use exact YYYY-MM-DD dates from those rows.",
            "2. undated_news_intel_clues_only may influence caution, but must never be cited as a core source.",
            "3. invalid_conditions must be structured objects with condition, trigger_price_or_data, and type.",
            "4. scenarios must contain base, bull, and bear, each with assumptions, triggers, key_risks, and probability.",
            "5. If evidence is thin, lower confidence and describe the gap in confidence_rationale.",
            "6. If the DSA text is one-sided, set single_side_flag=true and lower confidence.",
            "7. Resolve execution intent separately for a flat account and an existing holder.",
            "8. Use flat_account_action for accounts with no current position, holding_action for accounts already holding the symbol, and resolved_action as the default flat-account execution action.",
            "9. If text says holders should hold but flat accounts should wait for a pullback, return flat_account_action=watch, holding_action=hold, resolved_action=watch, conflict_status=position_context_split.",
            "10. If text explicitly says watch/wait/do not buy while DSA action is buy/add, use conflict_status=hard_conflict. If text says buy only on dips/pullback/staged entry, use conflict_status=conditional_entry and do not make resolved_action buy.",
            "",
            "Discipline framework text:",
            discipline_text,
            "",
            "Read-only input JSON:",
            _json_dumps(prompt_input),
        ]
    )


def normalize_completion_payload(payload: Mapping[str, Any], context: CompletionContext) -> dict[str, Any]:
    normalized = dict(payload)
    confidence = _coerce_confidence(normalized.get("confidence"), context.signal.get("confidence"))
    if normalized.get("single_side_flag") and confidence is not None:
        original_confidence = _coerce_confidence(context.signal.get("confidence"), confidence)
        confidence = min(confidence, original_confidence if original_confidence is not None else confidence, 0.5)
    normalized["confidence"] = confidence
    normalized["source_attribution"] = _normalize_source_attribution(
        normalized.get("source_attribution") or [],
        context.dated_news,
    )
    normalized["invalid_conditions"] = _normalize_invalid_conditions(normalized.get("invalid_conditions") or [])
    normalized["scenarios"] = normalized.get("scenarios") or {}
    normalized["confidence_rationale"] = str(normalized.get("confidence_rationale") or "").strip()
    normalized["single_side_flag"] = bool(normalized.get("single_side_flag"))
    resolution = resolve_intent(
        signal_action=context.signal.get("action"),
        operation_advice=_analysis_intent_text(context.analysis),
        metadata=normalized,
        has_position=False,
    )
    normalized["flat_account_action"] = normalize_action(resolution.flat_account_action)
    normalized["holding_action"] = normalize_action(resolution.holding_action)
    normalized["resolved_action"] = normalize_action(resolution.resolved_action)
    normalized["conflict_status"] = resolution.conflict_status
    normalized["conflict_reason"] = resolution.conflict_reason
    normalized["normalized_terms"] = normalized.get("normalized_terms") or []
    return normalized


def _guardrail_payload(signal: Mapping[str, Any], completion_payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "stock_code": signal.get("stock_code"),
        "source_signal_id": signal.get("id"),
        "action": signal.get("action"),
        "confidence": completion_payload.get("confidence"),
        "source_attribution": completion_payload.get("source_attribution"),
        "invalid_conditions": completion_payload.get("invalid_conditions"),
        "scenarios": completion_payload.get("scenarios"),
        "confidence_rationale": completion_payload.get("confidence_rationale"),
        "single_side_flag": completion_payload.get("single_side_flag"),
    }


def _signal_prompt_fields(signal: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "stock_code",
        "stock_name",
        "action",
        "confidence",
        "score",
        "entry_low",
        "entry_high",
        "stop_loss",
        "target_price",
        "reason",
        "risk_summary",
        "catalyst_summary",
        "invalidation",
        "source_report_id",
        "status",
        "created_at",
        "expires_at",
        "metadata_json",
    )
    return {key: signal.get(key) for key in keys}


def _analysis_prompt_fields(analysis: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "code",
        "name",
        "operation_advice",
        "sentiment_score",
        "analysis_summary",
        "news_content",
        "trend_prediction",
        "created_at",
    )
    return {key: analysis.get(key) for key in keys}


def _analysis_intent_text(analysis: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("operation_advice", "analysis_summary", "trend_prediction"):
        value = analysis.get(key)
        if value:
            parts.append(str(value))
    return "\n".join(parts)


def _normalize_source_attribution(items: Any, dated_news: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    allowed_dates = {_published_date_yyyy_mm_dd(item.get("published_date")) for item in dated_news}
    allowed_dates.discard(None)
    normalized: list[dict[str, str]] = []
    if not isinstance(items, list):
        return normalized
    for item in items:
        if not isinstance(item, Mapping):
            continue
        published_date = _published_date_yyyy_mm_dd(item.get("published_date"))
        if published_date not in allowed_dates:
            continue
        claim = str(item.get("claim") or "").strip()
        source = str(item.get("source") or "").strip()
        if not claim or not source:
            continue
        normalized.append({"claim": claim, "source": source, "published_date": published_date})
    return normalized


def _normalize_invalid_conditions(items: Any) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    if not isinstance(items, list):
        return normalized
    for item in items:
        if not isinstance(item, Mapping):
            continue
        condition_type = str(item.get("type") or "").strip().lower()
        condition = str(item.get("condition") or "").strip()
        trigger = str(item.get("trigger_price_or_data") or "").strip()
        if condition and trigger and condition_type in {"price", "data", "event"}:
            normalized.append(
                {
                    "condition": condition,
                    "trigger_price_or_data": trigger,
                    "type": condition_type,
                }
            )
    return normalized


def _candidate_text(response_payload: Mapping[str, Any]) -> str:
    try:
        parts = response_payload["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Gemini response has no candidate content: {response_payload}") from exc
    for part in parts:
        if isinstance(part, Mapping) and "text" in part:
            return str(part["text"])
    raise RuntimeError(f"Gemini response has no text part: {response_payload}")


def _openai_chat_text(response_payload: Mapping[str, Any]) -> str:
    try:
        content = response_payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"fallback response has no message content: {response_payload}") from exc
    return str(content)


def _usage_from_response(response_payload: Mapping[str, Any], *, latency_ms: int) -> GeminiUsage:
    usage = response_payload.get("usageMetadata") if isinstance(response_payload, Mapping) else {}
    if not isinstance(usage, Mapping):
        usage = {}
    prompt_tokens = _optional_int(usage.get("promptTokenCount"))
    completion_tokens = _optional_int(usage.get("candidatesTokenCount") or usage.get("completionTokenCount"))
    total_tokens = _optional_int(usage.get("totalTokenCount"))
    return GeminiUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        latency_ms=latency_ms,
    )


def _openai_usage_from_response(response_payload: Mapping[str, Any], *, latency_ms: int) -> GeminiUsage:
    usage = response_payload.get("usage") if isinstance(response_payload, Mapping) else {}
    if not isinstance(usage, Mapping):
        usage = {}
    return GeminiUsage(
        prompt_tokens=_optional_int(usage.get("prompt_tokens")),
        completion_tokens=_optional_int(usage.get("completion_tokens")),
        total_tokens=_optional_int(usage.get("total_tokens")),
        latency_ms=latency_ms,
    )


def _loads_completion_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = [line for line in stripped.splitlines() if not line.strip().startswith("```")]
        stripped = "\n".join(lines).strip()
    if not stripped.startswith("{"):
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            stripped = stripped[start : end + 1]
    payload = json.loads(stripped)
    if not isinstance(payload, dict):
        raise ValueError("Gemini completion was not a JSON object")
    return payload


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _news_row_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = _row_dict(row)
    item["published_date"] = _published_date_yyyy_mm_dd(item.get("published_date"))
    return item


def _published_date_yyyy_mm_dd(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:10]


def _coerce_confidence(value: Any, fallback: Any = None) -> Optional[float]:
    candidate = value if value is not None else fallback
    if candidate is None:
        return None
    try:
        return max(0.0, min(1.0, float(candidate)))
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_retryable_completion_error(exc: BaseException) -> bool:
    retryable_types = (TimeoutError, socket.timeout, urllib.error.URLError, json.JSONDecodeError)
    retryable_markers = (
        "timed out",
        "timeout",
        "url_error=",
        "connection reset",
        "connection aborted",
        "temporarily unavailable",
        "http_status=408",
        "http_status=409",
        "http_status=429",
        "http_status=500",
        "http_status=502",
        "http_status=503",
        "http_status=504",
    )
    current: Optional[BaseException] = exc
    while current is not None:
        if isinstance(current, retryable_types):
            return True
        if isinstance(current, RuntimeError):
            text = str(current).lower()
            if any(marker in text for marker in retryable_markers):
                return True
        current = current.__cause__ or current.__context__
    return False


def _is_fallback_trigger_error(exc: BaseException) -> bool:
    return _is_retryable_completion_error(exc) or _is_immediate_fallback_error(exc)


def _is_immediate_fallback_error(exc: BaseException) -> bool:
    immediate_markers = (
        "http_status=401",
        "http_status=403",
        "http_status=429",
        "quota",
        "resource_exhausted",
        "permission_denied",
        "unauthenticated",
        "invalid api key",
        "api key not valid",
        "api_key_invalid",
    )
    current: Optional[BaseException] = exc
    while current is not None:
        text = str(current).lower()
        if any(marker in text for marker in immediate_markers):
            return True
        current = current.__cause__ or current.__context__
    return False


def _client_model(client: Any) -> str:
    if client is None:
        return G5_DEFAULT_MODEL
    actual_model = getattr(client, "actual_model", None)
    if callable(actual_model):
        return str(actual_model())
    return str(getattr(client, "model", G5_DEFAULT_MODEL))


def _safe_error_text(exc: BaseException, *, limit: int = 500) -> str:
    text = f"{exc.__class__.__name__}: {exc}"
    return text if len(text) <= limit else text[: limit - 3] + "..."


def discipline_temporal_metadata(
    signal: Mapping[str, Any],
    analysis: Optional[Mapping[str, Any]] = None,
) -> dict[str, Optional[str]]:
    decision_timestamp = parse_datetime(signal.get("created_at"))
    if decision_timestamp is None and analysis is not None:
        decision_timestamp = parse_datetime(analysis.get("created_at"))
    clock = _market_clock(signal.get("market"))
    decision_utc = _as_utc(decision_timestamp)
    decision_market = decision_utc.astimezone(clock.timezone) if decision_utc is not None else None
    market_phase = _market_phase(decision_market, clock)
    data_asof = _data_asof_date(decision_market, clock)
    bar_cutoff = (
        datetime.combine(data_asof, clock.close_time, tzinfo=clock.timezone).astimezone(timezone.utc)
        if data_asof is not None
        else None
    )
    return {
        "decision_timestamp": _datetime_text(decision_utc),
        "market_phase": market_phase,
        "data_asof": data_asof.isoformat() if data_asof is not None else None,
        "bar_cutoff": _datetime_text(bar_cutoff),
        "news_cutoff": _datetime_text(decision_utc),
    }


def ensure_disciplined_temporal_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("pragma table_info(disciplined_signals)").fetchall()}
    for column, column_type in DISCIPLINED_TEMPORAL_COLUMNS.items():
        if column not in existing:
            conn.execute(f"alter table disciplined_signals add column {column} {column_type}")


def ensure_disciplined_intent_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("pragma table_info(disciplined_signals)").fetchall()}
    for column, column_type in DISCIPLINED_INTENT_COLUMNS.items():
        if column not in existing:
            conn.execute(f"alter table disciplined_signals add column {column} {column_type}")


def backfill_disciplined_temporal_metadata(db_path: Path) -> tuple[int, int]:
    path = Path(db_path)
    if not path.exists():
        return 0, 0
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        table = conn.execute(
            "select 1 from sqlite_master where type = 'table' and name = 'disciplined_signals'"
        ).fetchone()
        if table is None:
            return 0, 0
        ensure_disciplined_temporal_columns(conn)
        ensure_disciplined_intent_columns(conn)
        rows = conn.execute(
            """
            select source_signal_id, raw_dsa_signal_json, dsa_analysis_json
            from disciplined_signals
            where decision_timestamp is null
               or market_phase is null
               or data_asof is null
               or bar_cutoff is null
               or news_cutoff is null
            order by source_signal_id
            """
        ).fetchall()
        updated = 0
        for row in rows:
            signal = _loads_mapping(row["raw_dsa_signal_json"])
            analysis = _loads_mapping(row["dsa_analysis_json"])
            temporal = discipline_temporal_metadata(signal, analysis)
            conn.execute(
                """
                update disciplined_signals
                set decision_timestamp = ?,
                    market_phase = ?,
                    data_asof = ?,
                    bar_cutoff = ?,
                    news_cutoff = ?
                where source_signal_id = ?
                """,
                (
                    temporal["decision_timestamp"],
                    temporal["market_phase"],
                    temporal["data_asof"],
                    temporal["bar_cutoff"],
                    temporal["news_cutoff"],
                    row["source_signal_id"],
                ),
            )
            updated += 1
        total_row = conn.execute("select count(*) as count from disciplined_signals").fetchone()
    return updated, int(total_row["count"] if total_row else 0)


def _market_clock(market: Any) -> MarketClock:
    key = str(market or "cn").strip().lower()
    return MARKET_CLOCKS.get(key, MARKET_CLOCKS["cn"])


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=NAIVE_TIMESTAMP_DEFAULT_TZ)
    return aware.astimezone(timezone.utc)


def _market_phase(value: Optional[datetime], clock: MarketClock) -> Optional[str]:
    if value is None:
        return None
    current_time = value.time()
    if current_time < clock.open_time:
        return "preopen"
    if current_time < clock.close_time:
        return "intraday"
    return "postclose"


def _data_asof_date(value: Optional[datetime], clock: MarketClock):
    if value is None:
        return None
    if value.time() >= clock.close_time:
        return value.date()
    return (value - timedelta(days=1)).date()


def _datetime_text(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(sep=" ", timespec="milliseconds")


def _loads_mapping(text: Any) -> dict[str, Any]:
    try:
        payload = json.loads(text or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _text_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Complete DSA signals into G5 disciplined signals.")
    parser.add_argument("--signal-id", type=int, action="append", dest="signal_ids", help="DSA decision_signals.id to complete.")
    parser.add_argument("--all-active", action="store_true", help="Complete all active DSA decision_signals.")
    parser.add_argument("--stock-code", action="append", dest="stock_codes", help="Limit --all-active to one stock code. Repeatable.")
    parser.add_argument("--market", default=None, help="Limit --all-active to a single market (e.g. cn, us). Keeps CN/US disciplined stores isolated.")
    parser.add_argument("--force", action="store_true", help="Replace an existing disciplined signal for the same source_signal_id.")
    parser.add_argument("--dsa-db", type=Path, default=DSA_DB_PATH)
    parser.add_argument("--store-db", type=Path, default=PAPER_DB_PATH)
    parser.add_argument("--key-path", type=Path, default=GEMINI_API_KEY_PATH)
    parser.add_argument("--model", default=G5_DEFAULT_MODEL)
    parser.add_argument(
        "--fallback-provider",
        default="deepseek",
        choices=("deepseek", "none"),
        help="Fallback route after Gemini timeout/slow/quota failures.",
    )
    parser.add_argument("--fallback-key-path", type=Path, default=DEEPSEEK_API_KEY_PATH)
    parser.add_argument("--fallback-model", default=G5_DEFAULT_FALLBACK_MODEL)
    parser.add_argument("--fallback-timeout-seconds", type=float, default=G5_DEFAULT_FALLBACK_TIMEOUT_SECONDS)
    parser.add_argument(
        "--slow-threshold-ms",
        type=int,
        default=G5_DEFAULT_SLOW_THRESHOLD_MS,
        help="Gemini latency above this is counted as a breaker failure.",
    )
    parser.add_argument(
        "--primary-failure-threshold",
        type=int,
        default=G5_DEFAULT_PRIMARY_FAILURE_THRESHOLD,
        help="Consecutive Gemini failures/slow responses before fallback bypass.",
    )
    parser.add_argument("--retries", type=int, default=1, help="Per-signal retries for transient Gemini/network failures.")
    parser.add_argument("--retry-delay-seconds", type=float, default=1.0, help="Delay before retrying one signal.")
    parser.add_argument(
        "--workers",
        type=int,
        default=G5_DEFAULT_WORKERS,
        help="Parallel G5 completions for independent signals.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=G5_DEFAULT_REQUEST_TIMEOUT_SECONDS,
        help="Per-request Gemini timeout. Bounds tail latency during market stress.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    completer = DisciplineCompleter(
        dsa_db_path=args.dsa_db,
        store_db_path=args.store_db,
        key_path=args.key_path,
        model=args.model,
        request_timeout_seconds=max(1.0, args.timeout_seconds),
        fallback_key_path=args.fallback_key_path,
        fallback_model=args.fallback_model,
        fallback_provider=args.fallback_provider,
        fallback_timeout_seconds=max(1.0, args.fallback_timeout_seconds),
        slow_threshold_ms=max(1, args.slow_threshold_ms),
        primary_failure_threshold=max(1, args.primary_failure_threshold),
    )
    signal_ids = list(args.signal_ids or [])
    if args.all_active:
        signal_ids.extend(completer.loader.active_signal_ids(args.stock_codes, market=args.market))
    signal_ids = sorted(set(signal_ids))
    if not signal_ids:
        raise SystemExit("Provide --signal-id or --all-active.")
    summaries = completer.complete_many(
        signal_ids,
        force=args.force,
        retries=max(0, args.retries),
        retry_delay_seconds=max(0.0, args.retry_delay_seconds),
        workers=max(1, args.workers),
    )
    print(_json_dumps([summary.as_dict() for summary in summaries]))
    return 1 if any(summary.error for summary in summaries) else 0


if __name__ == "__main__":
    raise SystemExit(main())
