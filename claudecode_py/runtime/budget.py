from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


RuntimeBudgetDecision = Literal[
    "ok",
    "warning",
    "continue_with_budget",
    "compact_needed",
    "hard_stop",
]


@dataclass(slots=True, frozen=True)
class RuntimeBudgetState:
    budget_state: RuntimeBudgetDecision
    budget_reason: str | None
    context_tokens_estimated: int
    context_percentage: float
    message_count: int
    message_limit: int
    context_summary_chars: int
    context_summary_limit: int
    warning_message_threshold: int
    warning_summary_threshold: int
    auto_summary_threshold: int
    would_compact: bool
    last_turn_token_count: int | None
    last_turn_token_source: str | None
    provider_usage_seen: bool
    should_warn: bool
    should_compact: bool
    should_stop: bool

    def to_payload(self) -> dict[str, object]:
        return asdict(self)


def compute_runtime_budget_state(
    *,
    context_tokens_estimated: int,
    context_percentage: float,
    message_count: int,
    message_limit: int,
    context_summary_chars: int,
    context_summary_limit: int,
    warning_message_threshold: int,
    warning_summary_threshold: int,
    auto_summary_threshold: int,
    warning_context_percentage: float,
    auto_context_percentage: float,
    would_compact: bool,
    last_turn_token_count: int | None,
    last_turn_token_source: str | None,
    provider_usage_seen: bool,
) -> RuntimeBudgetState:
    warning_reasons: list[str] = []
    compact_reasons: list[str] = []

    if would_compact and context_percentage >= auto_context_percentage:
        compact_reasons.append(
            f"context usage {context_percentage:.1f}% >= {auto_context_percentage:.1f}%"
        )
    elif would_compact and context_percentage >= warning_context_percentage:
        warning_reasons.append(
            f"context usage {context_percentage:.1f}% >= {warning_context_percentage:.1f}%"
        )

    if would_compact and message_count > message_limit:
        compact_reasons.append(f"message count {message_count} > {message_limit}")
    elif would_compact and message_count >= warning_message_threshold:
        warning_reasons.append(
            f"message count {message_count} >= warning threshold {warning_message_threshold}"
        )

    if context_summary_chars >= auto_summary_threshold:
        compact_reasons.append(
            f"context summary chars {context_summary_chars} >= {auto_summary_threshold}"
        )
    elif context_summary_chars >= warning_summary_threshold:
        warning_reasons.append(
            "context summary chars "
            f"{context_summary_chars} >= warning threshold {warning_summary_threshold}"
        )

    budget_state: RuntimeBudgetDecision = "ok"
    budget_reason: str | None = None
    should_warn = False
    should_compact = False
    should_stop = False

    if compact_reasons:
        budget_reason = "; ".join(compact_reasons)
        if would_compact:
            budget_state = "compact_needed"
            should_compact = True
        else:
            budget_state = "hard_stop"
            should_stop = True
    elif warning_reasons:
        budget_state = "warning"
        budget_reason = "; ".join(warning_reasons)
        should_warn = True

    return RuntimeBudgetState(
        budget_state=budget_state,
        budget_reason=budget_reason,
        context_tokens_estimated=int(context_tokens_estimated),
        context_percentage=float(context_percentage),
        message_count=int(message_count),
        message_limit=max(int(message_limit), 1),
        context_summary_chars=max(int(context_summary_chars), 0),
        context_summary_limit=max(int(context_summary_limit), 1),
        warning_message_threshold=max(int(warning_message_threshold), 1),
        warning_summary_threshold=max(int(warning_summary_threshold), 1),
        auto_summary_threshold=max(int(auto_summary_threshold), 1),
        would_compact=bool(would_compact),
        last_turn_token_count=(
            None if last_turn_token_count is None else max(int(last_turn_token_count), 0)
        ),
        last_turn_token_source=str(last_turn_token_source or "").strip() or None,
        provider_usage_seen=bool(provider_usage_seen),
        should_warn=should_warn,
        should_compact=should_compact,
        should_stop=should_stop,
    )
