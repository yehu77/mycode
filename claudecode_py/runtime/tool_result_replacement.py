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
class ToolResultReductionCandidate:
    kind: str
    tool_use_id: str
    message_group_index: int
    estimated_tokens_saved: int
    estimated_chars_saved: int
    prefix_damage_score: int
    reason: str
    replacement_text: str
    original_content: str
    artifact_record: ToolResultArtifactRecord | None = None
    replacement_record: ToolResultReplacementRecord | None = None


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
    replaced_tokens_total: int
    artifact_tokens_saved: int
    microcompact_tokens_saved: int
    microcompacted_message_group_indices: tuple[int, ...]
    budget_reason: str


@dataclass(slots=True, frozen=True)
class FrozenToolResultReductionView:
    messages: list[dict[str, Any]]
    reapplied_count: int
    artifact_reuse_count: int


def reapply_frozen_tool_result_reductions(
    session: "Session",
    messages: list[dict[str, Any]],
) -> FrozenToolResultReductionView:
    working_messages = deepcopy(messages)
    _seen_ids, replacements, artifact_records = session.runtime_tool_result_replacement_state()
    replacement_map = dict(replacements)
    reapplied_count = 0
    artifact_reuse_count = 0
    for message in working_messages:
        if str(message.get("role") or "") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        reapplied, reused = _reapply_replacements(
            session,
            content,
            replacement_map,
            artifact_records,
        )
        reapplied_count += reapplied
        artifact_reuse_count += reused
    return FrozenToolResultReductionView(
        messages=working_messages,
        reapplied_count=reapplied_count,
        artifact_reuse_count=artifact_reuse_count,
    )


def build_tool_result_reduction_candidates(
    session: "Session",
    messages: list[dict[str, Any]],
) -> tuple[ToolResultReductionCandidate, ...]:
    seen_ids, replacements, _artifact_records = session.runtime_tool_result_replacement_state()
    replacement_map = dict(replacements)
    group_limit_chars = _tool_result_group_char_limit(session)
    artifact_threshold_chars = _tool_result_artifact_threshold(session, group_limit_chars)
    available: list[ToolResultReductionCandidate] = []

    for message_index, message in enumerate(messages):
        if str(message.get("role") or "") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        group_chars = sum(_content_block_chars(block) for block in content)
        if group_chars <= group_limit_chars:
            continue
        for block in content:
            if not isinstance(block, dict) or str(block.get("type") or "") != "tool_result":
                continue
            tool_use_id = str(block.get("tool_use_id") or "").strip()
            if not tool_use_id or tool_use_id in replacement_map or tool_use_id in seen_ids:
                continue
            raw_content = str(block.get("content") or "")
            if not raw_content:
                continue
            available.extend(
                _build_tool_result_candidates_for_block(
                    session,
                    tool_use_id=tool_use_id,
                    raw_content=raw_content,
                    message_group_index=message_index,
                    artifact_threshold_chars=artifact_threshold_chars,
                )
            )
    return tuple(available)


def apply_selected_tool_result_reductions(
    session: "Session",
    messages: list[dict[str, Any]],
    selected_candidates: tuple[ToolResultReductionCandidate, ...],
    *,
    reapplied_count: int = 0,
    artifact_reuse_count: int = 0,
    budget_reason: str = _REPLACEMENT_REASON,
) -> ToolResultBudgetResult:
    working_messages = deepcopy(messages)
    newly_replaced_records: list[ToolResultReplacementRecord] = []
    newly_artifact_records: list[ToolResultArtifactRecord] = []
    replacement_count = 0
    artifact_count = 0
    microcompact_count = 0
    replaced_chars_total = 0
    artifact_chars_saved = 0
    microcompact_chars_saved = 0
    replaced_tokens_total = 0
    artifact_tokens_saved = 0
    microcompact_tokens_saved = 0
    microcompacted_message_group_indices: set[int] = set()

    for candidate in selected_candidates:
        try:
            message = working_messages[candidate.message_group_index]
        except IndexError:
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        target_block = next(
            (
                block
                for block in content
                if isinstance(block, dict)
                and str(block.get("type") or "") == "tool_result"
                and str(block.get("tool_use_id") or "").strip() == candidate.tool_use_id
            ),
            None,
        )
        if target_block is None:
            continue
        target_block["content"] = candidate.replacement_text
        replacement_count += 1
        replaced_chars_total += candidate.estimated_chars_saved
        replaced_tokens_total += candidate.estimated_tokens_saved
        if candidate.kind == "artifact_indirection":
            artifact_count += 1
            artifact_chars_saved += candidate.estimated_chars_saved
            artifact_tokens_saved += candidate.estimated_tokens_saved
            if candidate.artifact_record is not None:
                newly_artifact_records.append(candidate.artifact_record)
            if candidate.replacement_record is not None:
                newly_replaced_records.append(candidate.replacement_record)
        else:
            microcompact_count += 1
            microcompact_chars_saved += candidate.estimated_chars_saved
            microcompact_tokens_saved += candidate.estimated_tokens_saved
            microcompacted_message_group_indices.add(candidate.message_group_index)
            if candidate.replacement_record is not None:
                newly_replaced_records.append(candidate.replacement_record)

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
        replaced_tokens_total=replaced_tokens_total,
        artifact_tokens_saved=artifact_tokens_saved,
        microcompact_tokens_saved=microcompact_tokens_saved,
        microcompacted_message_group_indices=tuple(sorted(microcompacted_message_group_indices)),
        budget_reason=budget_reason,
    )


def apply_tool_result_budget_to_messages(
    session: "Session",
    messages: list[dict[str, Any]],
) -> ToolResultBudgetResult:
    frozen_view = reapply_frozen_tool_result_reductions(session, messages)
    candidates = build_tool_result_reduction_candidates(session, frozen_view.messages)
    group_limit_chars = _tool_result_group_char_limit(session)
    selected_candidates = select_tool_result_reduction_candidates_for_groups(
        candidates,
        messages=frozen_view.messages,
        group_limit_chars=group_limit_chars,
    )
    return apply_selected_tool_result_reductions(
        session,
        frozen_view.messages,
        selected_candidates,
        reapplied_count=frozen_view.reapplied_count,
        artifact_reuse_count=frozen_view.artifact_reuse_count,
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


def tool_result_group_char_limit(session: "Session") -> int:
    return _tool_result_group_char_limit(session)


def estimate_tool_result_group_pressure_tokens(
    session: "Session",
    messages: list[dict[str, Any]],
) -> int:
    group_limit_chars = _tool_result_group_char_limit(session)
    total_overage_chars = 0
    for message in messages:
        if str(message.get("role") or "") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        group_chars = sum(_content_block_chars(block) for block in content)
        if group_chars > group_limit_chars:
            total_overage_chars += group_chars - group_limit_chars
    return max(total_overage_chars // 4, 0)


def select_tool_result_reduction_candidates_for_groups(
    candidates: tuple[ToolResultReductionCandidate, ...],
    *,
    messages: list[dict[str, Any]],
    group_limit_chars: int,
) -> tuple[ToolResultReductionCandidate, ...]:
    return _select_tool_result_reduction_candidates_for_compat(
        candidates,
        messages=messages,
        group_limit_chars=group_limit_chars,
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


def _build_tool_result_candidates_for_block(
    session: "Session",
    *,
    tool_use_id: str,
    raw_content: str,
    message_group_index: int,
    artifact_threshold_chars: int,
) -> list[ToolResultReductionCandidate]:
    available: list[ToolResultReductionCandidate] = []
    if len(raw_content) >= artifact_threshold_chars:
        artifact_record, replacement_record, replacement_text = _create_artifact_backed_replacement(
            session,
            tool_use_id=tool_use_id,
            content=raw_content,
        )
        saved_chars = max(len(raw_content) - len(replacement_text), 0)
        if saved_chars > 0:
            available.append(
                ToolResultReductionCandidate(
                    kind="artifact_indirection",
                    tool_use_id=tool_use_id,
                    message_group_index=message_group_index,
                    estimated_tokens_saved=max(
                        estimate_text_tokens(raw_content)
                        - estimate_text_tokens(replacement_text),
                        0,
                    ),
                    estimated_chars_saved=saved_chars,
                    prefix_damage_score=1,
                    reason="artifact_indirection",
                    replacement_text=replacement_text,
                    original_content=raw_content,
                    artifact_record=artifact_record,
                    replacement_record=replacement_record,
                )
            )
    replacement_text = build_tool_result_replacement_preview(
        tool_use_id=tool_use_id,
        content=raw_content,
    )
    saved_chars = max(len(raw_content) - len(replacement_text), 0)
    if saved_chars > 0:
        available.append(
            ToolResultReductionCandidate(
                kind="microcompact",
                tool_use_id=tool_use_id,
                message_group_index=message_group_index,
                estimated_tokens_saved=max(
                    estimate_text_tokens(raw_content)
                    - estimate_text_tokens(replacement_text),
                    0,
                ),
                estimated_chars_saved=saved_chars,
                prefix_damage_score=2,
                reason="microcompact",
                replacement_text=replacement_text,
                original_content=raw_content,
                replacement_record=ToolResultReplacementRecord(
                    tool_use_id=tool_use_id,
                    replacement=replacement_text,
                    original_size_chars=len(raw_content),
                    replacement_size_chars=len(replacement_text),
                    created_at=datetime.now(UTC).isoformat(),
                    reason=_REPLACEMENT_REASON,
                ),
            )
        )
    return available


def _select_tool_result_reduction_candidates_for_compat(
    candidates: tuple[ToolResultReductionCandidate, ...],
    *,
    messages: list[dict[str, Any]],
    group_limit_chars: int,
) -> tuple[ToolResultReductionCandidate, ...]:
    available = sorted(
        candidates,
        key=lambda item: (
            item.prefix_damage_score,
            -item.estimated_tokens_saved,
            -item.estimated_chars_saved,
            item.tool_use_id,
            item.kind,
        ),
    )
    group_chars: dict[int, int] = {}
    for index, message in enumerate(messages):
        content = message.get("content")
        if not isinstance(content, list):
            group_chars[index] = 0
            continue
        group_chars[index] = sum(_content_block_chars(block) for block in content)
    selected: list[ToolResultReductionCandidate] = []
    chosen_tool_use_ids: set[str] = set()
    for candidate in available:
        remaining_chars = int(group_chars.get(candidate.message_group_index, 0))
        if remaining_chars <= group_limit_chars:
            continue
        if candidate.tool_use_id in chosen_tool_use_ids:
            continue
        selected.append(candidate)
        chosen_tool_use_ids.add(candidate.tool_use_id)
        group_chars[candidate.message_group_index] = max(
            remaining_chars - candidate.estimated_chars_saved,
            0,
        )
    return tuple(selected)
