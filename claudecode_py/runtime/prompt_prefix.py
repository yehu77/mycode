from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, TYPE_CHECKING

from ..prompts import SystemPromptBlock, render_system_prompt_blocks
from .tool_result_replacement import ToolResultBudgetResult
from .tool_schema_cache import canonical_json

if TYPE_CHECKING:
    from ..session import Session


PrefixReductionTier = str


@dataclass(slots=True, frozen=True)
class PrefixSegment:
    segment_id: str
    kind: str
    stable: bool
    char_count: int
    signature: str
    summary: str


@dataclass(slots=True, frozen=True)
class PromptPrefixAssemblyResult:
    system_prompt: str
    system_prompt_blocks: tuple[SystemPromptBlock, ...]
    tools: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    segments: tuple[PrefixSegment, ...]
    static_system_prompt_signature: str
    tool_schema_signature: str
    stable_prefix_signature: str
    prompt_prefix_segment_count: int
    stable_prefix_segment_count: int
    dynamic_tail_segment_count: int
    stable_prefix_chars: int
    dynamic_tail_chars: int
    provider_view_reduction_tier: PrefixReductionTier
    provider_view_reduction_reason: str
    replacement_aware_provider_view: bool
    microcompact_aware_provider_view: bool
    provider_view_message_count: int
    microcompacted_message_group_count: int
    microcompacted_message_group_indices: tuple[int, ...]
    replacement_count: int
    artifact_count: int
    microcompact_count: int
    replaced_chars_total: int
    artifact_chars_saved: int
    microcompact_chars_saved: int
    replaced_tokens_total: int
    artifact_tokens_saved: int
    microcompact_tokens_saved: int


def build_prompt_prefix_assembly(
    *,
    session: "Session",
    messages: list[dict[str, Any]],
    system_prompt_blocks: list[SystemPromptBlock],
    tools: list[dict[str, Any]],
    replacement_result: ToolResultBudgetResult | None = None,
    active_replacement_count: int = 0,
    active_artifact_count: int = 0,
    compacted_before_provider: bool = False,
) -> PromptPrefixAssemblyResult:
    tool_schema_signature = _signature_for_json(tools)
    static_blocks = [
        block.text for block in system_prompt_blocks if block.cache_scope in {"session", "global"}
    ]
    static_system_prompt_signature = _signature_for_parts(static_blocks)

    segments: list[PrefixSegment] = []
    stable_segment_signatures: list[str] = []

    for index, block in enumerate(system_prompt_blocks):
        stable = block.cache_scope in {"session", "global"}
        text = str(block.text or "")
        signature = _signature_for_text(text)
        segments.append(
            PrefixSegment(
                segment_id=f"system_prompt:{index}:{block.kind}",
                kind=f"system_prompt:{block.kind}",
                stable=stable,
                char_count=len(text),
                signature=signature,
                summary=f"{block.kind} ({block.cache_scope})",
            )
        )
        if stable:
            stable_segment_signatures.append(signature)

    tool_bundle = canonical_json(tools)
    segments.append(
        PrefixSegment(
            segment_id="tool_schema:bundle",
            kind="tool_schema_bundle",
            stable=True,
            char_count=len(tool_bundle),
            signature=tool_schema_signature,
            summary=f"{len(tools)} tool(s)",
        )
    )
    stable_segment_signatures.append(tool_schema_signature)

    last_message_index = len(messages) - 1
    for index, message in enumerate(messages):
        role = str(message.get("role") or "unknown")
        stable = index != last_message_index
        serialized = canonical_json(message)
        signature = _signature_for_text(serialized)
        segments.append(
            PrefixSegment(
                segment_id=f"message:{index}:{role}",
                kind=f"message_group:{role}",
                stable=stable,
                char_count=len(serialized),
                signature=signature,
                summary=f"{role} message group {index + 1}",
            )
        )
        if stable:
            stable_segment_signatures.append(signature)

    replacement_count = 0
    artifact_count = 0
    microcompact_count = 0
    replacement_reason = "none"
    if replacement_result is not None:
        replacement_count = int(replacement_result.replacement_count or 0)
        artifact_count = int(replacement_result.artifact_count or 0)
        microcompact_count = int(replacement_result.microcompact_count or 0)
        replacement_reason = str(replacement_result.budget_reason or "").strip() or "message_budget"
        if replacement_result.replacement_count or replacement_result.reapplied_count:
            segments.append(
                PrefixSegment(
                    segment_id="overlay:replacement",
                    kind="replacement_overlay",
                    stable=False,
                    char_count=max(int(replacement_result.replaced_chars_total or 0), 0),
                    signature=_signature_for_text(
                        f"replacement:{replacement_result.replacement_count}:{replacement_result.reapplied_count}"
                    ),
                    summary=(
                        f"replacement count={replacement_result.replacement_count} "
                        f"reapplied={replacement_result.reapplied_count}"
                    ),
                )
            )
        if replacement_result.artifact_count or replacement_result.artifact_reuse_count:
            segments.append(
                PrefixSegment(
                    segment_id="overlay:artifact",
                    kind="artifact_overlay",
                    stable=False,
                    char_count=max(int(replacement_result.artifact_chars_saved or 0), 0),
                    signature=_signature_for_text(
                        f"artifact:{replacement_result.artifact_count}:{replacement_result.artifact_reuse_count}"
                    ),
                    summary=(
                        f"artifact count={replacement_result.artifact_count} "
                        f"reused={replacement_result.artifact_reuse_count}"
                    ),
                )
            )
        if replacement_result.microcompact_count:
            segments.append(
                PrefixSegment(
                    segment_id="overlay:microcompact",
                    kind="microcompact_overlay",
                    stable=False,
                    char_count=max(int(replacement_result.microcompact_chars_saved or 0), 0),
                    signature=_signature_for_text(
                        f"microcompact:{replacement_result.microcompact_count}:{replacement_result.microcompact_chars_saved}"
                    ),
                    summary=(
                        f"microcompact count={replacement_result.microcompact_count} "
                        f"shed_chars={replacement_result.microcompact_chars_saved}"
                    ),
                )
            )

    stable_prefix_signature = _signature_for_parts(stable_segment_signatures)
    stable_prefix_chars = sum(segment.char_count for segment in segments if segment.stable)
    dynamic_tail_chars = sum(segment.char_count for segment in segments if not segment.stable)
    reduction_tier = _provider_view_reduction_tier(
        compacted_before_provider=compacted_before_provider,
        replacement_result=replacement_result,
        active_replacement_count=active_replacement_count,
        active_artifact_count=active_artifact_count,
    )
    return PromptPrefixAssemblyResult(
        system_prompt=render_system_prompt_blocks(system_prompt_blocks),
        system_prompt_blocks=tuple(system_prompt_blocks),
        tools=list(tools),
        messages=list(messages),
        segments=tuple(segments),
        static_system_prompt_signature=static_system_prompt_signature,
        tool_schema_signature=tool_schema_signature,
        stable_prefix_signature=stable_prefix_signature,
        prompt_prefix_segment_count=len(segments),
        stable_prefix_segment_count=sum(1 for segment in segments if segment.stable),
        dynamic_tail_segment_count=sum(1 for segment in segments if not segment.stable),
        stable_prefix_chars=stable_prefix_chars,
        dynamic_tail_chars=dynamic_tail_chars,
        provider_view_reduction_tier=reduction_tier,
        provider_view_reduction_reason=(
            "history compaction was required before provider call"
            if compacted_before_provider
            else replacement_reason
        ),
        replacement_aware_provider_view=bool(
            replacement_result is not None or active_replacement_count or active_artifact_count
        ),
        microcompact_aware_provider_view=bool(microcompact_count),
        provider_view_message_count=len(messages),
        microcompacted_message_group_count=len(
            replacement_result.microcompacted_message_group_indices
        )
        if replacement_result is not None
        else 0,
        microcompacted_message_group_indices=(
            tuple(replacement_result.microcompacted_message_group_indices)
            if replacement_result is not None
            else ()
        ),
        replacement_count=replacement_count or int(active_replacement_count or 0),
        artifact_count=artifact_count or int(active_artifact_count or 0),
        microcompact_count=microcompact_count,
        replaced_chars_total=max(int(replacement_result.replaced_chars_total or 0), 0)
        if replacement_result is not None
        else 0,
        artifact_chars_saved=max(int(replacement_result.artifact_chars_saved or 0), 0)
        if replacement_result is not None
        else 0,
        microcompact_chars_saved=max(int(replacement_result.microcompact_chars_saved or 0), 0)
        if replacement_result is not None
        else 0,
        replaced_tokens_total=max(int(replacement_result.replaced_tokens_total or 0), 0)
        if replacement_result is not None
        else 0,
        artifact_tokens_saved=max(int(replacement_result.artifact_tokens_saved or 0), 0)
        if replacement_result is not None
        else 0,
        microcompact_tokens_saved=max(int(replacement_result.microcompact_tokens_saved or 0), 0)
        if replacement_result is not None
        else 0,
    )


def prompt_prefix_surface_payload_from_assembly(
    assembly: PromptPrefixAssemblyResult,
    *,
    previous_payload: dict[str, Any] | None = None,
    source: str,
) -> dict[str, Any]:
    previous_payload = dict(previous_payload or {})
    previous_signature = str(previous_payload.get("prompt_prefix_signature") or "").strip() or None
    previous_system_signature = (
        str(previous_payload.get("prompt_prefix_static_system_signature") or "").strip() or None
    )
    previous_tool_signature = (
        str(previous_payload.get("prompt_prefix_tool_schema_signature") or "").strip() or None
    )
    changed = bool(previous_signature) and previous_signature != assembly.stable_prefix_signature
    if not previous_signature:
        change_reason = "none"
    elif not changed:
        change_reason = "preserved"
    elif previous_system_signature != assembly.static_system_prompt_signature:
        change_reason = "system_prompt_blocks"
    elif previous_tool_signature != assembly.tool_schema_signature:
        change_reason = "tool_schema_bundle"
    else:
        change_reason = "provider_view_messages"
    return {
        "prompt_prefix_source": source,
        "prompt_prefix_segment_count": assembly.prompt_prefix_segment_count,
        "prompt_prefix_stable_segment_count": assembly.stable_prefix_segment_count,
        "prompt_prefix_dynamic_tail_segment_count": assembly.dynamic_tail_segment_count,
        "prompt_prefix_stable_chars": assembly.stable_prefix_chars,
        "prompt_prefix_dynamic_tail_chars": assembly.dynamic_tail_chars,
        "prompt_prefix_reduction_tier": assembly.provider_view_reduction_tier,
        "prompt_prefix_reduction_reason": assembly.provider_view_reduction_reason,
        "prompt_prefix_signature": assembly.stable_prefix_signature,
        "prompt_prefix_previous_signature": previous_signature or "none",
        "prompt_prefix_changed": changed,
        "prompt_prefix_change_reason": change_reason,
        "prompt_prefix_provider_view_summary": (
            f"replacement-aware={'yes' if assembly.replacement_aware_provider_view else 'no'} "
            f"microcompact-aware={'yes' if assembly.microcompact_aware_provider_view else 'no'}"
        ),
        "prompt_prefix_replacement_aware_provider_view": assembly.replacement_aware_provider_view,
        "prompt_prefix_microcompact_aware_provider_view": assembly.microcompact_aware_provider_view,
        "prompt_prefix_static_system_signature": assembly.static_system_prompt_signature,
        "prompt_prefix_tool_schema_signature": assembly.tool_schema_signature,
    }


def _provider_view_reduction_tier(
    *,
    compacted_before_provider: bool,
    replacement_result: ToolResultBudgetResult | None,
    active_replacement_count: int,
    active_artifact_count: int,
) -> PrefixReductionTier:
    if compacted_before_provider:
        return "full_compaction"
    if replacement_result is not None:
        if int(replacement_result.microcompact_count or 0) > 0:
            return "microcompact"
        if int(replacement_result.artifact_count or 0) > 0 or int(
            replacement_result.artifact_reuse_count or 0
        ) > 0:
            return "artifact_indirection"
        if int(replacement_result.replacement_count or 0) > 0 or int(
            replacement_result.reapplied_count or 0
        ) > 0:
            return "replacement"
    if int(active_artifact_count or 0) > 0:
        return "artifact_indirection"
    if int(active_replacement_count or 0) > 0:
        return "replacement"
    return "none"


def _signature_for_json(value: Any) -> str:
    return _signature_for_text(canonical_json(value))


def _signature_for_parts(parts: list[str]) -> str:
    digest = sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()[:16]


def _signature_for_text(text: str) -> str:
    return sha256(str(text or "").encode("utf-8")).hexdigest()[:16]
