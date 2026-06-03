from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable


@dataclass(slots=True)
class HistoryCompactionRequest:
    messages: list[dict[str, Any]]
    existing_summary: str
    keep_last: int
    max_summary_chars: int
    instructions: str | None = None
    replacement_records_seen: int = 0
    replacement_records_active: int = 0
    artifact_records_active: int = 0
    replaced_tool_result_ids: list[str] | None = None


@dataclass(slots=True)
class HistoryCompactionResult:
    keep_last: int
    message_count: int
    would_compact: bool
    compacted_count: int
    kept_count: int
    compacted_lines: list[str]
    kept_messages: list[dict[str, Any]]
    has_existing_summary: bool
    existing_summary_chars: int
    merged_summary: str
    merged_summary_chars: int
    instructions: str | None
    replacement_records_seen: int
    replacement_records_active: int
    artifact_records_active: int
    replaced_tool_result_ids: list[str]
    replacement_summary_lines: list[str]
    artifact_summary_lines: list[str]
    replacement_aware_compaction: bool

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


def build_history_compaction_result(
    request: HistoryCompactionRequest,
    *,
    summarize_message: Callable[[dict[str, Any]], str],
) -> HistoryCompactionResult:
    keep_last = max(1, request.keep_last)
    messages = list(request.messages)
    message_count = len(messages)
    would_compact = message_count > keep_last
    compacted_messages = messages[:-keep_last] if would_compact else []
    kept_messages = messages[-keep_last:] if would_compact else messages
    compacted_lines = [
        f"- {index}. {message.get('role', 'unknown')}: {summarize_message(message)}"
        for index, message in enumerate(compacted_messages, start=1)
    ]
    replacement_summary_lines = _replacement_summary_lines(request)
    artifact_summary_lines = _artifact_summary_lines(request)
    new_summary = _compose_compacted_summary(
        compacted_lines,
        instructions=request.instructions,
        replacement_summary_lines=replacement_summary_lines,
        artifact_summary_lines=artifact_summary_lines,
    )
    merged_summary = merge_context_summary(
        request.existing_summary,
        new_summary,
        max_summary_chars=request.max_summary_chars,
    )
    return HistoryCompactionResult(
        keep_last=keep_last,
        message_count=message_count,
        would_compact=would_compact,
        compacted_count=len(compacted_messages),
        kept_count=len(kept_messages),
        compacted_lines=compacted_lines,
        kept_messages=list(kept_messages),
        has_existing_summary=bool(request.existing_summary),
        existing_summary_chars=len(request.existing_summary),
        merged_summary=merged_summary,
        merged_summary_chars=len(merged_summary),
        instructions=_normalized_instructions(request.instructions),
        replacement_records_seen=int(request.replacement_records_seen or 0),
        replacement_records_active=int(request.replacement_records_active or 0),
        artifact_records_active=int(request.artifact_records_active or 0),
        replaced_tool_result_ids=list(request.replaced_tool_result_ids or []),
        replacement_summary_lines=replacement_summary_lines,
        artifact_summary_lines=artifact_summary_lines,
        replacement_aware_compaction=bool(
            int(request.replacement_records_active or 0) or int(request.artifact_records_active or 0)
        ),
    )


def merge_context_summary(
    existing_summary: str,
    new_summary: str,
    *,
    max_summary_chars: int,
) -> str:
    merged_summary = (
        f"{existing_summary}\n\n{new_summary}".strip()
        if existing_summary and new_summary
        else (existing_summary or new_summary)
    )
    if len(merged_summary) > max_summary_chars:
        truncated_prefix = "[older compacted context truncated]\n"
        kept_tail = max(0, max_summary_chars - len(truncated_prefix))
        merged_summary = truncated_prefix + merged_summary[-kept_tail:]
    return merged_summary


def _compose_compacted_summary(
    compacted_lines: list[str],
    *,
    instructions: str | None,
    replacement_summary_lines: list[str] | None = None,
    artifact_summary_lines: list[str] | None = None,
) -> str:
    if not compacted_lines:
        return ""
    lines: list[str] = []
    normalized_instructions = _normalized_instructions(instructions)
    if normalized_instructions:
        lines.append(f"Compact instruction: {normalized_instructions}")
    if replacement_summary_lines:
        lines.extend(replacement_summary_lines)
    if artifact_summary_lines:
        lines.extend(artifact_summary_lines)
    lines.append("Earlier conversation summary:")
    lines.extend(compacted_lines)
    return "\n".join(lines)


def _normalized_instructions(instructions: str | None) -> str | None:
    value = str(instructions or "").strip()
    return value or None


def _replacement_summary_lines(request: HistoryCompactionRequest) -> list[str]:
    active = int(request.replacement_records_active or 0)
    seen = int(request.replacement_records_seen or 0)
    artifact_active = int(request.artifact_records_active or 0)
    replaced_ids = list(request.replaced_tool_result_ids or [])
    if active <= 0 and seen <= 0 and artifact_active <= 0:
        return []
    lines = [
        f"Tool-result replacements: seen={seen} active={active}",
        "Replacement-aware compaction: " + ("yes" if (active > 0 or artifact_active > 0) else "no"),
    ]
    if replaced_ids:
        lines.append("Replacement-active tool_use_ids: " + ", ".join(replaced_ids))
    return lines


def _artifact_summary_lines(request: HistoryCompactionRequest) -> list[str]:
    active = int(request.artifact_records_active or 0)
    if active <= 0:
        return []
    return [f"Tool-result artifacts: active={active}"]
