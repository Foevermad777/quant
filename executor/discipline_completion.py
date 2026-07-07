from __future__ import annotations

import argparse
import json
import socket
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from executor.config import (
    DISCIPLINE_SKILL_PATH,
    DSA_DB_PATH,
    G5_COMPLETION_VERSION,
    G5_DEFAULT_MODEL,
    G5_SCHEMA_VERSION,
    GEMINI_API_KEY_PATH,
    PAPER_DB_PATH,
)
from executor.guardrails import GuardrailResult, gate_dsa_output
from executor.signal_reader import parse_datetime


GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_PROXY_HOST = "127.0.0.1"
GEMINI_PROXY_PORT = 7890
ESTIMATED_COST_USD = 0.05


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
        }


class DsaSignalContextLoader:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        uri = f"file:{self.db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def active_signal_ids(self, stock_codes: Optional[Sequence[str]] = None) -> list[int]:
        params: list[Any] = []
        predicate = "status = 'active'"
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

    def load(self, signal_id: int) -> CompletionContext:
        with self._connect() as conn:
            signal_row = conn.execute("select * from decision_signals where id = ?", (signal_id,)).fetchone()
            if signal_row is None:
                raise KeyError(f"DSA decision signal not found: {signal_id}")
            signal = _row_dict(signal_row)
            analysis_row = self._analysis_row(conn, signal)
            analysis = _row_dict(analysis_row) if analysis_row is not None else {}
            dated_news = [
                _news_row_dict(row)
                for row in conn.execute(
                    """
                    select id, title, snippet, url, source, provider, published_date
                    from news_intel
                    where code = ? and published_date is not null and trim(published_date) != ''
                    order by datetime(published_date) desc, id desc
                    limit 25
                    """,
                    (signal["stock_code"],),
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
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        with self._connect() as conn:
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
            cursor = conn.execute(sql, [row[column] for column in columns])
            return cursor.rowcount == 1


class GeminiStructuredClient:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = G5_DEFAULT_MODEL,
        proxy_host: str = GEMINI_PROXY_HOST,
        proxy_port: int = GEMINI_PROXY_PORT,
        timeout: int = 120,
    ) -> None:
        self.api_key = api_key.strip()
        self.model = model
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self.timeout = timeout

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
        with socket.create_connection((self.proxy_host, self.proxy_port), timeout=3):
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


class DisciplineCompleter:
    def __init__(
        self,
        *,
        dsa_db_path: Path = DSA_DB_PATH,
        store_db_path: Path = PAPER_DB_PATH,
        discipline_skill_path: Path = DISCIPLINE_SKILL_PATH,
        key_path: Path = GEMINI_API_KEY_PATH,
        model: str = G5_DEFAULT_MODEL,
        client: Optional[GeminiStructuredClient] = None,
    ) -> None:
        self.loader = DsaSignalContextLoader(dsa_db_path)
        self.store = DisciplinedSignalStore(store_db_path)
        self.discipline_skill_path = Path(discipline_skill_path)
        self.key_path = Path(key_path)
        self.model = model
        self.client = client

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
            )

        context = self.loader.load(source_signal_id)
        prompt = build_completion_prompt(context, self.discipline_skill_path)
        client = self.client or GeminiStructuredClient.from_key_file(self.key_path, model=self.model)
        structured, usage = client.generate_json(prompt, DISCIPLINE_RESPONSE_SCHEMA)
        completion_payload = normalize_completion_payload(structured, context)
        guardrail_result = gate_dsa_output(_guardrail_payload(context.signal, completion_payload), mode="reject")
        self.store.save(
            context=context,
            completion_payload=completion_payload,
            guardrail_result=guardrail_result,
            usage=usage,
            model=client.model,
            force=force,
        )
        return CompletionSummary(
            source_signal_id=source_signal_id,
            stock_code=context.signal.get("stock_code"),
            skipped=False,
            gate_accepted=guardrail_result.accepted,
            gate_action=guardrail_result.action,
            gate_reasons=guardrail_result.gate_reasons,
            model=client.model,
            latency_ms=usage.latency_ms,
        )

    def complete_many(self, signal_ids: Iterable[int], *, force: bool = False) -> list[CompletionSummary]:
        return [self.complete_signal(signal_id, force=force) for signal_id in signal_ids]


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
    parser.add_argument("--force", action="store_true", help="Replace an existing disciplined signal for the same source_signal_id.")
    parser.add_argument("--dsa-db", type=Path, default=DSA_DB_PATH)
    parser.add_argument("--store-db", type=Path, default=PAPER_DB_PATH)
    parser.add_argument("--key-path", type=Path, default=GEMINI_API_KEY_PATH)
    parser.add_argument("--model", default=G5_DEFAULT_MODEL)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    completer = DisciplineCompleter(
        dsa_db_path=args.dsa_db,
        store_db_path=args.store_db,
        key_path=args.key_path,
        model=args.model,
    )
    signal_ids = list(args.signal_ids or [])
    if args.all_active:
        signal_ids.extend(completer.loader.active_signal_ids(args.stock_codes))
    signal_ids = sorted(set(signal_ids))
    if not signal_ids:
        raise SystemExit("Provide --signal-id or --all-active.")
    summaries = completer.complete_many(signal_ids, force=args.force)
    print(_json_dumps([summary.as_dict() for summary in summaries]))


if __name__ == "__main__":
    main()
