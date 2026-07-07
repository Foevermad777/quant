from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

MISSING_SOURCE_ATTRIBUTION = "missing_data_source_attribution"
MISSING_INVALID_CONDITIONS = "missing_invalid_conditions"
MISSING_SCENARIOS = "missing_base_bull_bear_scenarios"
REQUIRED_SCENARIOS = ("base", "bull", "bear")


@dataclass(frozen=True)
class GuardrailResult:
    accepted: bool
    signal: dict[str, Any]
    gate_reasons: tuple[str, ...]
    confidence_before: Optional[float]
    confidence_after: Optional[float]
    action: str


class DisciplineOutputValidator:
    """Validate DSA output against the hard discipline fields we own."""

    def validate(self, payload: Mapping[str, Any]) -> tuple[str, ...]:
        reasons: list[str] = []
        if not _has_source_attribution(payload):
            reasons.append(MISSING_SOURCE_ATTRIBUTION)
        if not _has_invalid_conditions(payload):
            reasons.append(MISSING_INVALID_CONDITIONS)
        if not _has_base_bull_bear(payload):
            reasons.append(MISSING_SCENARIOS)
        return tuple(reasons)


def gate_dsa_output(
    payload: Mapping[str, Any],
    *,
    mode: str = "reject",
    confidence_penalty: float = 0.25,
) -> GuardrailResult:
    if mode not in {"reject", "degrade"}:
        raise ValueError(f"unsupported guardrail mode: {mode}")
    signal = deepcopy(dict(payload))
    confidence_before = _coerce_confidence(signal.get("confidence"))
    reasons = DisciplineOutputValidator().validate(signal)
    if not reasons:
        _record_guardrail(signal, accepted=True, action="pass", reasons=(), confidence_after=confidence_before)
        return GuardrailResult(True, signal, (), confidence_before, confidence_before, "pass")

    if mode == "reject":
        _record_guardrail(signal, accepted=False, action="reject", reasons=reasons, confidence_after=confidence_before)
        return GuardrailResult(False, signal, reasons, confidence_before, confidence_before, "reject")

    confidence_after = _degrade_confidence(confidence_before, confidence_penalty)
    signal["confidence"] = confidence_after
    _record_guardrail(signal, accepted=True, action="degrade", reasons=reasons, confidence_after=confidence_after)
    return GuardrailResult(True, signal, reasons, confidence_before, confidence_after, "degrade")


def _record_guardrail(
    signal: dict[str, Any],
    *,
    accepted: bool,
    action: str,
    reasons: Sequence[str],
    confidence_after: Optional[float],
) -> None:
    signal["guardrail"] = {
        "accepted": accepted,
        "action": action,
        "reasons": list(reasons),
        "confidence_after": confidence_after,
    }


def _coerce_confidence(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _degrade_confidence(confidence: Optional[float], penalty: float) -> Optional[float]:
    if confidence is None:
        return None
    return round(max(0.0, confidence - penalty), 4)


def _has_source_attribution(payload: Mapping[str, Any]) -> bool:
    direct_keys = ("sources", "citations", "data_sources", "source_attribution")
    if any(_has_dated_source_item(payload.get(key)) for key in direct_keys):
        return True

    evidence = payload.get("evidence") or payload.get("evidence_json")
    if isinstance(evidence, Mapping):
        evidence = evidence.get("items") or evidence.get("sources") or evidence.get("news")
    return _has_dated_source_item(evidence)


def _has_invalid_conditions(payload: Mapping[str, Any]) -> bool:
    invalid_conditions = payload.get("invalid_conditions")
    if isinstance(invalid_conditions, list):
        return any(_has_structured_invalid_condition(item) for item in invalid_conditions)
    invalidations = payload.get("invalidations")
    if isinstance(invalidations, list):
        return any(_has_structured_invalid_condition(item) for item in invalidations)
    return False


def _has_base_bull_bear(payload: Mapping[str, Any]) -> bool:
    scenarios = payload.get("scenarios") or payload.get("scenario_analysis")
    if isinstance(scenarios, Mapping):
        normalized = {str(key).strip().lower() for key, value in scenarios.items() if _non_empty(value)}
        if all(name in normalized for name in REQUIRED_SCENARIOS):
            return True

    field_names = {str(key).strip().lower() for key, value in payload.items() if _non_empty(value)}
    required_field_sets = (
        {"base_scenario", "bull_scenario", "bear_scenario"},
        {"base_case", "bull_case", "bear_case"},
    )
    return any(required.issubset(field_names) for required in required_field_sets)


def _non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _has_dated_source_item(value: Any) -> bool:
    if isinstance(value, Mapping):
        date_value = value.get("published_date") or value.get("published_at") or value.get("date")
        source_value = value.get("source") or value.get("url") or value.get("claim")
        return _non_empty(date_value) and _non_empty(source_value)
    if isinstance(value, list):
        return any(_has_dated_source_item(item) for item in value)
    return False


def _has_structured_invalid_condition(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    condition = value.get("condition")
    trigger = value.get("trigger_price_or_data")
    condition_type = str(value.get("type") or "").strip().lower()
    return _non_empty(condition) and _non_empty(trigger) and condition_type in {"price", "data", "event"}
