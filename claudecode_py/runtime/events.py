from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal
import json


EventKind = Literal[
    "assistant_text",
    "assistant_usage",
    "assistant_tool_call",
    "assistant_tool_result_ready",
    "plan_execution",
    "task_progress",
    "advisor",
    "advisor_review_started",
    "advisor_review_result",
    "advisor_revision_requested",
    "advisor_error",
    "context_compacted",
    "provider_retry",
    "tool_batch_started",
    "tool_batch_finished",
    "tool_waiting_for_approval",
    "tool_started",
    "tool_finished",
    "tool_failed",
    "tool_result",
    "tool_result_summarized",
    "tool_result_replacement_applied",
    "tool_result_replacement_reapplied",
    "tool_result_artifact_created",
    "tool_result_artifact_reused",
    "tool_result_microcompacted",
    "prompt_cache_hints_applied",
    "prompt_cache_hints_fallback",
    "prompt_prefix_planner_applied",
    "prompt_prefix_planner_downgraded",
    "budget_pressure",
    "compact_recovery_started",
    "compact_recovery_finished",
]


@dataclass(slots=True)
class RuntimeEvent:
    kind: EventKind
    message: str
    task_id: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    duration_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    usage_source: str | None = None
    batch_size: int | None = None
    batch_parallel: bool | None = None
    result_count: int | None = None
    budget_state: str | None = None
    budget_reason: str | None = None
    compaction_trigger: str | None = None
    approval_risk_level: str | None = None
    replacement_count: int | None = None
    replaced_chars_total: int | None = None
    replacement_reason: str | None = None
    artifact_count: int | None = None
    artifact_chars_saved: int | None = None
    microcompact_count: int | None = None
    microcompact_chars_saved: int | None = None
    is_error: bool = False
    command_mode_name: str | None = None
    command_mode_allowed_prefixes: tuple[str, ...] = ()
    command_mode_violating_segment: str | None = None
    command_mode_violating_segment_index: int | None = None
    command_mode_complex_features: tuple[str, ...] = ()
    decision_reason: str | None = None
    permission_rules: tuple[str, ...] = ()


EventSink = Callable[[RuntimeEvent], None]


def null_sink(_event: RuntimeEvent) -> None:
    return None


def summarize_tool_input(tool_input: dict[str, object], limit: int = 160) -> str:
    try:
        text = json.dumps(tool_input, ensure_ascii=True, sort_keys=True)
    except TypeError:
        text = repr(tool_input)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
