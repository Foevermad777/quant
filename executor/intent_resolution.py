from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


ENTRY_ACTIONS = {"buy", "add"}
EXIT_ACTIONS = {"sell", "reduce", "avoid"}
NEUTRAL_ACTIONS = {"hold", "watch", "alert"}
KNOWN_ACTIONS = ENTRY_ACTIONS | EXIT_ACTIONS | NEUTRAL_ACTIONS | {"unknown"}
CONFLICT_STATUSES = {"hard_conflict", "conditional_entry", "position_context_split", "consistent"}


@dataclass(frozen=True)
class IntentResolution:
    flat_account_action: str
    holding_action: str
    resolved_action: str
    effective_action: str
    conflict_status: str
    conflict_reason: str
    source: str

    def as_details(self) -> dict[str, str]:
        return {
            "flat_account_action": self.flat_account_action,
            "holding_action": self.holding_action,
            "resolved_action": self.resolved_action,
            "effective_action": self.effective_action,
            "conflict_status": self.conflict_status,
            "conflict_reason": self.conflict_reason,
            "intent_source": self.source,
        }


def normalize_action(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "long": "buy",
        "entry": "buy",
        "open": "buy",
        "open_long": "buy",
        "accumulate": "add",
        "scale_in": "add",
        "trim": "reduce",
        "take_profit": "reduce",
        "close": "sell",
        "clear": "sell",
        "liquidate": "sell",
        "stay_out": "watch",
        "wait": "watch",
        "observe": "watch",
        "no_trade": "watch",
        "none": "watch",
    }
    normalized = aliases.get(text, text)
    return normalized if normalized in KNOWN_ACTIONS else "unknown"


def advice_to_action(advice: Optional[str]) -> str:
    text = (advice or "").strip().lower()
    if not text:
        return "unknown"
    if _has_any(text, ("卖出", "清仓", "strong sell", "sell", "liquidate")):
        return "sell"
    if _has_any(text, ("减仓", "reduce", "trim")):
        return "reduce"
    if _has_any(text, ("避免", "回避", "avoid")):
        return "avoid"
    if _has_any(text, _NO_BUY_TOKENS + _CONDITIONAL_ENTRY_TOKENS):
        return "watch"
    if _has_any(text, ("加仓", "add", "scale in")):
        return "add"
    if _has_any(text, ("买入", "建仓", "buy")):
        return "buy"
    if _has_any(text, ("持有", "hold")):
        return "hold"
    if _has_any(text, ("观望", "等待", "watch", "wait", "observe")):
        return "watch"
    return "unknown"


def action_group(action: str) -> str:
    normalized = normalize_action(action)
    if normalized in ENTRY_ACTIONS:
        return "entry"
    if normalized in EXIT_ACTIONS:
        return "exit"
    if normalized in NEUTRAL_ACTIONS:
        return "neutral"
    return "unknown"


def resolve_intent(
    *,
    signal_action: Any,
    operation_advice: Optional[str],
    metadata: Optional[Mapping[str, Any]] = None,
    has_position: bool = False,
) -> IntentResolution:
    signal_action_text = normalize_action(signal_action)
    payload = _g5_intent_payload(metadata)
    if payload is not None:
        flat_action = normalize_action(payload.get("flat_account_action"))
        holding_action = normalize_action(payload.get("holding_action"))
        resolved_action = normalize_action(payload.get("resolved_action"))
        if flat_action == "unknown":
            flat_action = resolved_action
        if holding_action == "unknown":
            holding_action = resolved_action
        if resolved_action == "unknown":
            resolved_action = flat_action
        status = _normalize_conflict_status(payload.get("conflict_status"))
        derived_status = classify_conflict_status(
            signal_action=signal_action_text,
            operation_advice=operation_advice,
            flat_account_action=flat_action,
            holding_action=holding_action,
            resolved_action=resolved_action,
        )
        if status == "unknown" or (status == "consistent" and derived_status != "consistent"):
            status = derived_status
        reason = str(payload.get("conflict_reason") or "").strip()
        if not reason:
            reason = _default_conflict_reason(status, operation_advice)
        effective = _select_effective_action(
            flat_account_action=flat_action,
            holding_action=holding_action,
            resolved_action=resolved_action,
            has_position=has_position,
        )
        return IntentResolution(
            flat_account_action=flat_action,
            holding_action=holding_action,
            resolved_action=resolved_action,
            effective_action=effective,
            conflict_status=status,
            conflict_reason=reason,
            source="g5",
        )

    legacy_action = advice_to_action(operation_advice)
    status = classify_conflict_status(
        signal_action=signal_action_text,
        operation_advice=operation_advice,
        flat_account_action=legacy_action,
        holding_action=legacy_action,
        resolved_action=legacy_action,
    )
    if status == "position_context_split":
        status = "hard_conflict"
    reason = _default_conflict_reason(status, operation_advice)
    return IntentResolution(
        flat_account_action=legacy_action,
        holding_action=legacy_action,
        resolved_action=legacy_action,
        effective_action=legacy_action,
        conflict_status=status,
        conflict_reason=reason,
        source="legacy_operation_advice",
    )


def classify_conflict_status(
    *,
    signal_action: Any,
    operation_advice: Optional[str],
    flat_account_action: Any,
    holding_action: Any,
    resolved_action: Any,
) -> str:
    signal_group = action_group(str(signal_action or ""))
    flat = normalize_action(flat_account_action)
    holding = normalize_action(holding_action)
    resolved = normalize_action(resolved_action)
    resolved_group = action_group(resolved)
    text = (operation_advice or "").strip().lower()

    if signal_group == "unknown" or resolved_group == "unknown":
        return "hard_conflict"
    if signal_group == resolved_group:
        return "consistent"
    if signal_group == "entry":
        if _has_position_split_text(text) or (flat != holding and holding in NEUTRAL_ACTIONS | EXIT_ACTIONS):
            return "position_context_split"
        if _has_any(text, _CONDITIONAL_ENTRY_TOKENS):
            return "conditional_entry"
        if _has_any(text, _NO_BUY_TOKENS + _WATCH_TOKENS):
            return "hard_conflict"
    if signal_group == "exit" and resolved_group != "exit":
        return "hard_conflict"
    if signal_group == "neutral" and resolved_group in {"entry", "exit"}:
        return "hard_conflict"
    return "hard_conflict"


def _select_effective_action(
    *,
    flat_account_action: str,
    holding_action: str,
    resolved_action: str,
    has_position: bool,
) -> str:
    if has_position and holding_action != "unknown":
        return holding_action
    if resolved_action != "unknown":
        return resolved_action
    return flat_account_action


def _normalize_conflict_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in CONFLICT_STATUSES else "unknown"


def _g5_intent_payload(metadata: Optional[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    if not isinstance(metadata, Mapping):
        return None
    nested = metadata.get("intent_resolution")
    if isinstance(nested, Mapping) and _has_intent_fields(nested):
        return nested
    return metadata if _has_intent_fields(metadata) else None


def _has_intent_fields(payload: Mapping[str, Any]) -> bool:
    return any(
        key in payload
        for key in (
            "flat_account_action",
            "holding_action",
            "resolved_action",
            "conflict_status",
        )
    )


def _default_conflict_reason(status: str, operation_advice: Optional[str]) -> str:
    text = (operation_advice or "").strip()
    if status == "consistent":
        return "structured action and resolved intent are aligned"
    if status == "conditional_entry":
        return "entry requires pullback/dip/staged-entry conditions before execution"
    if status == "position_context_split":
        return "intent differs for flat accounts and existing holders"
    if text:
        return f"structured action conflicts with advice text: {text[:120]}"
    return "structured action conflicts with unresolved advice text"


def _has_position_split_text(text: str) -> bool:
    return _has_any(text, _HOLDER_CONTEXT_TOKENS) and _has_any(text, _FLAT_CONTEXT_TOKENS)


def _has_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


_NO_BUY_TOKENS = (
    "不买",
    "不建议买",
    "不要买",
    "别买",
    "暂不买",
    "暂不买入",
    "不宜买",
    "不追",
    "别追",
    "勿追",
    "避免追高",
    "not buy",
    "no buy",
    "do not buy",
    "don't buy",
    "avoid buying",
)

_CONDITIONAL_ENTRY_TOKENS = (
    "逢低",
    "低吸",
    "回踩",
    "等待回调",
    "等待企稳",
    "企稳后",
    "分批",
    "支撑位",
    "若",
    "如果",
    "条件",
    "on dips",
    "buy the dip",
    "pullback",
    "wait for",
    "scale in",
    "staged",
)

_WATCH_TOKENS = (
    "观望",
    "等待",
    "观察",
    "暂缓",
    "谨慎",
    "watch",
    "wait",
    "observe",
    "stand aside",
)

_HOLDER_CONTEXT_TOKENS = (
    "持仓者",
    "已有持仓",
    "已经持仓",
    "有持仓",
    "已持有",
    "持有者",
    "has_position",
    "existing holder",
    "existing holders",
    "already holding",
)

_FLAT_CONTEXT_TOKENS = (
    "空仓者",
    "空仓",
    "未持仓",
    "无持仓",
    "无仓位",
    "no_position",
    "flat account",
    "new position",
    "no position",
)
