from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


Message = dict[str, Any]
ContentBlock = dict[str, Any]


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(slots=True)
class AssistantResponse:
    content: list[ContentBlock]
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None


StreamEventKind = Literal["text_delta", "response"]


@dataclass(slots=True)
class ProviderStreamEvent:
    kind: StreamEventKind
    text: str = ""
    response: AssistantResponse | None = None
