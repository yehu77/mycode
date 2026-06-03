from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any
import json

from ..context_usage import estimate_text_tokens
from ..state import ToolResultArtifactRecord, ToolResultReplacementRecord

if TYPE_CHECKING:
    from ..session import Session


_REPLACEMENT_REASON = "message_budget"


@dataclass(slots=True, frozen=True)
class ToolResultBudgetResult:
    messages: list[dict[str, Any]]
    newly_replaced_records: tuple[ToolResultReplacementRecord, ...]
    newly_artifact_records: tuple[ToolResultArtifactRecord, ...]
    reapplied_count: int
    artifact_reuse_count: int
    replacement_count: int
    artifact_count: int
    microcompact_count: int
    replaced_chars_total: int
    artifact_chars_saved: int
    microcompact_chars_saved: int
    budget_reason: str


def apply_tool_result_budget_to_messages(
    session: "Session",
    messages: list[dict[str, Any]],
) -> ToolResultBudgetResult:
    working_messages = deepcopy(messages)
    seen_ids, replacements, artifact_records = session.runtime_tool_result_replacement_state()
    replacement_map = dict(replacements)
    newly_replaced_records: list[ToolResultReplacementRecord] = []
    newly_artifact_records: list[ToolResultArtifactRecord] = []
    reapplied_count = 0
    artifact_reuse_count = 0
    replacement_count = 0
    artifact_count = 0
    microcompact_count = 0
    replaced_chars_total = 0
    artifact_chars_saved = 0
    microcompact_chars_saved = 0
    group_limit_chars = _tool_result_group_char_limit(session)
    artifact_threshold_chars = _tool_result_artifact_threshold(session, group_limit_chars)

    for message in working_messages:
        if str(message.get("role") or "") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        group_chars = sum(_content_block_chars(block) for block in content)
        reapplied, reused = _reapply_replacements(session, content, replacement_map, artifact_records)
        reapplied_count += reapplied
        artifact_reuse_count += reused
        group_chars = sum(_content_block_chars(block) for block in content)
        if group_chars <= group_limit_chars:
            continue

        candidates: list[dict[str, Any]] = []
        for index, block in enumerate(content):
            if not isinstance(block, dict) or str(block.get("type") or "") != "tool_result":
                continue
            tool_use_id = str(block.get("tool_use_id") or "").strip()
            if not tool_use_id or tool_use_id in replacement_map or tool_use_id in seen_ids:
                continue
            raw_content = str(block.get("content") or "")
            if not raw_content:
                continue
            candidates.append(
                {
                    "index": index,
                    "tool_use_id": tool_use_id,
                    "raw_content": raw_content,
                }
            )

        if group_chars > group_limit_chars and candidates:
            candidates.sort(key=lambda item: len(str(item["raw_content"])), reverse=True)
            artifactized_ids: set[str] = set()
            for candidate in candidates:
                if group_chars <= group_limit_chars:
                    break
                raw_content = str(candidate["raw_content"])
                if len(raw_content) < artifact_threshold_chars:
                    continue
                record, replacement_record, replacement_text = _create_artifact_backed_replacement(
                    session,
                    tool_use_id=str(candidate["tool_use_id"]),
                    content=raw_content,
                )
                replacement_chars = len(replacement_text)
                if replacement_chars >= len(raw_content):
                    continue
                content[int(candidate["index"])]["content"] = replacement_text
                replacement_map[str(candidate["tool_use_id"])] = replacement_text
                saved_chars = max(len(raw_content) - replacement_chars, 0)
                group_chars = max(group_chars - saved_chars, 0)
                replacement_count += 1
                artifact_count += 1
                replaced_chars_total += saved_chars
                artifact_chars_saved += saved_chars
                newly_artifact_records.append(record)
                newly_replaced_records.append(replacement_record)
                artifactized_ids.add(str(candidate["tool_use_id"]))

            if group_chars > group_limit_chars:
                for candidate in candidates:
                    if group_chars <= group_limit_chars:
                        break
                    tool_use_id = str(candidate["tool_use_id"])
                    if tool_use_id in artifactized_ids:
                        continue
                    raw_content = str(candidate["raw_content"])
                    replacement_text = build_tool_result_replacement_preview(
                        tool_use_id=tool_use_id,
                        content=raw_content,
                    )
                    replacement_chars = len(replacement_text)
                    if replacement_chars >= len(raw_content):
                        continue
                    content[int(candidate["index"])]["content"] = replacement_text
                    replacement_map[tool_use_id] = replacement_text
                    saved_chars = max(len(raw_content) - replacement_chars, 0)
                    group_chars = max(group_chars - saved_chars, 0)
                    replacement_count += 1
                    microcompact_count += 1
                    replaced_chars_total += saved_chars
                    microcompact_chars_saved += saved_chars
                    newly_replaced_records.append(
                        ToolResultReplacementRecord(
                            tool_use_id=tool_use_id,
                            replacement=replacement_text,
                            original_size_chars=len(raw_content),
                            replacement_size_chars=replacement_chars,
                            created_at=datetime.now(UTC).isoformat(),
                            reason=_REPLACEMENT_REASON,
                        )
                    )

        reapplied, reused = _reapply_replacements(session, content, replacement_map, artifact_records)
        reapplied_count += reapplied
        artifact_reuse_count += reused

    return ToolResultBudgetResult(
        messages=working_messages,
        newly_replaced_records=tuple(newly_replaced_records),
        newly_artifact_records=tuple(newly_artifact_records),
        reapplied_count=reapplied_count,
        artifact_reuse_count=artifact_reuse_count,
        replacement_count=replacement_count,
        artifact_count=artifact_count,
        microcompact_count=microcompact_count,
        replaced_chars_total=replaced_chars_total,
        artifact_chars_saved=artifact_chars_saved,
        microcompact_chars_saved=microcompact_chars_saved,
        budget_reason=_REPLACEMENT_REASON,
    )


def build_tool_result_replacement_preview(
    *,
    tool_use_id: str,
    content: str,
    artifact_path: str | None = None,
    artifact_missing: bool = False,
) -> str:
    compact = _compact_summary(content)
    lines = [
        "Tool result replaced for context budget.",
        f"tool_use_id={tool_use_id}",
        f"original_chars={len(str(content or ''))}",
        f"summary={compact}",
    ]
    if artifact_path:
        lines.append(f"artifact={artifact_path}")
    if artifact_missing:
        lines.append("artifact_status=missing")
    return "\n".join(lines)


def build_missing_artifact_replacement_preview(
    *,
    tool_use_id: str,
    artifact_path: str,
    original_size_chars: int,
    summary: str,
) -> str:
    compact = _compact_summary(summary)
    return "\n".join(
        [
            "Tool result replaced for context budget.",
            f"tool_use_id={tool_use_id}",
            f"original_chars={int(original_size_chars or 0)}",
            f"summary={compact}",
            f"artifact={artifact_path}",
            "artifact_status=missing",
        ]
    )


def estimate_provider_call_context_usage(
    session: "Session",
    messages_for_provider: list[dict[str, Any]],
):
    from ..context_usage import collect_context_usage

    return collect_context_usage(
        session,
        message_override=messages_for_provider,
        replacement_aware_provider_view=True,
    )


def _tool_result_group_char_limit(session: "Session") -> int:
    derived = max(int(session.config.max_tokens) // 10, 1) * 4
    return max(4000, min(16000, derived))


def _tool_result_artifact_threshold(session: "Session", group_limit_chars: int) -> int:
    derived = max(int(session.config.max_tokens) // 20, 1) * 4
    return max(6000, min(group_limit_chars, derived))


def _create_artifact_backed_replacement(
    session: "Session",
    *,
    tool_use_id: str,
    content: str,
) -> tuple[ToolResultArtifactRecord, ToolResultReplacementRecord, str]:
    artifact_dir = (
        Path(session.state.effective_cwd or session.config.cwd)
        / ".pyclaude"
        / "tool_result_artifacts"
        / session.state.session_id
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    content_sha = sha256(str(content).encode("utf-8")).hexdigest()
    filename = f"{tool_use_id}_{content_sha[:16]}.txt"
    artifact_path = (artifact_dir / filename).resolve()
    artifact_path.write_text(str(content), encoding="utf-8")
    artifact_path_text = _display_artifact_path(session, artifact_path)
    summary = _compact_summary(content)
    replacement_text = build_tool_result_replacement_preview(
        tool_use_id=tool_use_id,
        content=content,
        artifact_path=artifact_path_text,
    )
    artifact_record = ToolResultArtifactRecord(
        tool_use_id=tool_use_id,
        artifact_path=str(artifact_path),
        content_sha256=content_sha,
        original_size_chars=len(content),
        preview_size_chars=len(replacement_text),
        created_at=datetime.now(UTC).isoformat(),
        reason=_REPLACEMENT_REASON,
        summary=summary,
    )
    replacement_record = ToolResultReplacementRecord(
        tool_use_id=tool_use_id,
        replacement=replacement_text,
        original_size_chars=len(content),
        replacement_size_chars=len(replacement_text),
        created_at=datetime.now(UTC).isoformat(),
        reason=_REPLACEMENT_REASON,
    )
    return artifact_record, replacement_record, replacement_text


def _display_artifact_path(session: "Session", artifact_path: Path) -> str:
    try:
        root = Path(session.config.cwd).resolve()
        return str(artifact_path.relative_to(root))
    except ValueError:
        return str(artifact_path)


def _reapply_replacements(
    session: "Session",
    content: list[Any],
    replacement_map: dict[str, str],
    artifact_records: dict[str, ToolResultArtifactRecord],
) -> tuple[int, int]:
    reapplied = 0
    artifact_reused = 0
    for block in content:
        if not isinstance(block, dict) or str(block.get("type") or "") != "tool_result":
            continue
        tool_use_id = str(block.get("tool_use_id") or "").strip()
        if not tool_use_id or tool_use_id not in replacement_map:
            continue
        replacement_text = session.tool_result_replacement_text(tool_use_id) or replacement_map.get(
            tool_use_id
        )
        if replacement_text is None:
            continue
        if str(block.get("content") or "") != replacement_text:
            block["content"] = replacement_text
            reapplied += 1
        if tool_use_id in artifact_records:
            artifact_reused += 1
    return reapplied, artifact_reused


def _content_block_chars(block: Any) -> int:
    if not isinstance(block, dict):
        return len(json.dumps(block, ensure_ascii=True, sort_keys=True))
    block_type = str(block.get("type") or "unknown")
    if block_type == "text":
        return len(str(block.get("text") or ""))
    if block_type == "tool_use":
        return estimate_text_tokens(
            json.dumps(block.get("input") or {}, ensure_ascii=True, sort_keys=True)
        ) * 4
    if block_type == "tool_result":
        return len(str(block.get("content") or ""))
    return len(json.dumps(block, ensure_ascii=True, sort_keys=True))


def _compact_summary(content: str, *, limit: int = 160) -> str:
    compact = " ".join(str(content or "").split())
    if len(compact) > limit:
        compact = compact[: limit - 3].rstrip() + "..."
    return compact or "empty tool result"
