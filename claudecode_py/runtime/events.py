from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal
import json


EventKind = Literal[
    "assistant_text",
    "assistant_tool_call",
    "assistant_tool_result_ready",
    "plan_execution",
    "advisor",
    "advisor_review_started",
    "advisor_review_result",
    "advisor_revision_requested",
    "advisor_error",
    "context_compacted",
    "provider_retry",
    "tool_started",
    "tool_finished",
    "tool_failed",
    "tool_result",
]


@dataclass(slots=True)
class RuntimeEvent:
    kind: EventKind
    message: str
    tool_name: str | None = None
    tool_call_id: str | None = None
    duration_ms: int | None = None
    is_error: bool = False


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
