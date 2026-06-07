from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Any
import json

from ..prompts import SystemPromptBlock
from ..providers.capabilities import ProviderCapabilities
from ..context_usage import estimate_text_tokens
from .prompt_prefix import PrefixSegment, PromptPrefixAssemblyResult

if TYPE_CHECKING:
    from ..session import Session


ProviderCacheScope = str
ProviderCacheMode = str

_PLANNER_RESET_REASONS = {
    "tool_schema_drift",
    "system_prompt_drift",
    "full_compaction_required",
}

_PLANNER_DOWNGRADED_REASONS = {
    "replacement_reapply_only",
    "artifact_indirection_active",
    "microcompact_on_tail",
    "microcompact_in_stable_prefix",
    "tool_schema_drift",
    "system_prompt_drift",
    "full_compaction_required",
}


@dataclass(slots=True, frozen=True)
class ProviderCacheHint:
    kind: str
    cache_scope: ProviderCacheScope
    stable: bool
    signature: str
    summary: str


@dataclass(slots=True, frozen=True)
class ProviderViewPrefixPlan:
    assembly: PromptPrefixAssemblyResult
    stable_prefix_segments: tuple[PrefixSegment, ...]
    dynamic_tail_segments: tuple[PrefixSegment, ...]
    preserved_message_group_count: int
    downgraded_message_group_count: int
    selected_reduction_tier: str
    planner_mode: ProviderCacheMode
    planner_reason: str
    preserved_prefix_signature: str
    preserved_segment_count: int
    preserved_chars: int
    cache_eligible_segment_count: int
    cache_eligibility_summary: str
    stable_message_group_indices: tuple[int, ...]
    preserved_message_group_indices: tuple[int, ...]
    downgraded_message_group_indices: tuple[int, ...]
    previous_preserved_signature: str | None
    preserved_prefix_changed: bool


@dataclass(slots=True)
class ProviderPromptCachePlan:
    system_prompt: str
    tools: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    cache_hints: tuple[ProviderCacheHint, ...]
    provider_cache_supported: bool
    provider_cache_mode: ProviderCacheMode
    provider_cache_summary: str
    provider_cache_provider: str
    provider_cache_fallback_reason: str | None = None
    system_prompt_blocks: tuple[dict[str, Any], ...] = ()
    prefix_plan: ProviderViewPrefixPlan | None = None
    costed_plan: ProviderViewPrefixCostedPlan | None = None
    orchestration_mode: str = "disabled"
    orchestration_reason: str = "none"
    orchestration_selected_candidate_count: int = 0
    orchestration_selected_candidate_summary: str = "none"
    orchestration_remaining_estimated_overage: int = 0
    orchestration_requires_full_compaction: bool = False

    def mark_fallback(self, reason: str) -> None:
        reason_text = str(reason or "").strip() or "provider rejected cache hints"
        self.provider_cache_mode = "diagnostic_only"
        self.provider_cache_fallback_reason = reason_text


@dataclass(slots=True, frozen=True)
class ProviderViewReductionCandidate:
    kind: str
    estimated_tokens_saved: int
    estimated_chars_saved: int
    prefix_damage_score: int
    breaks_preserved_prefix: bool
    affects_message_group_indices: tuple[int, ...]
    reason: str
    candidate_id: str = ""
    selected: bool = False


@dataclass(slots=True, frozen=True)
class ProviderViewCostBreakdown:
    estimated_input_tokens: int
    estimated_stable_prefix_tokens: int
    estimated_dynamic_tail_tokens: int
    tool_schema_overhead_estimate: int
    system_prompt_overhead_estimate: int
    per_message_group_estimated_tokens: tuple[tuple[int, int], ...]


@dataclass(slots=True, frozen=True)
class ProviderViewPrefixCostedPlan:
    prefix_plan: ProviderViewPrefixPlan
    cost_breakdown: ProviderViewCostBreakdown
    target_tokens_to_shed: int
    available_reduction_candidates: tuple[ProviderViewReductionCandidate, ...]
    chosen_candidate_sequence: tuple[ProviderViewReductionCandidate, ...]
    estimated_tokens_shed: int
    estimated_chars_shed: int
    remaining_estimated_overage: int
    prefix_damage_score: int
    final_planner_verdict: str


def build_provider_view_prefix_plan(
    assembly: PromptPrefixAssemblyResult,
    *,
    provider_capabilities: ProviderCapabilities | None,
    previous_payload: dict[str, Any] | None = None,
) -> ProviderViewPrefixPlan:
    previous_payload = dict(previous_payload or {})
    supports_prompt_cache_hints = bool(
        getattr(provider_capabilities, "supports_prompt_cache_hints", False)
    )
    stable_message_segments = sorted(
        (
            (index, segment)
            for index, segment in (
                (_message_group_index(segment), segment)
                for segment in assembly.segments
                if segment.kind.startswith("message_group:")
            )
            if index is not None and segment.stable
        ),
        key=lambda item: item[0],
    )
    stable_message_indices = tuple(index for index, _segment in stable_message_segments)
    reduced_indices = _reduction_affected_message_group_indices(assembly)
    first_downgraded_stable = next(
        (index for index in stable_message_indices if index in reduced_indices),
        None,
    )
    current_prefix_indices = tuple(
        index
        for index in stable_message_indices
        if first_downgraded_stable is None or index < first_downgraded_stable
    )
    previous_preserved_count = int(
        previous_payload.get("prompt_prefix_preserved_message_group_count") or 0
    )
    previous_reason = str(previous_payload.get("prompt_prefix_planner_reason") or "none")
    if current_prefix_indices and _should_reset_preserved_prefix(previous_payload, previous_reason):
        preserved_message_indices = current_prefix_indices
    elif current_prefix_indices and previous_preserved_count > 0:
        preserved_message_indices = current_prefix_indices[:previous_preserved_count]
    else:
        preserved_message_indices = current_prefix_indices
    preserved_message_index_set = set(preserved_message_indices)

    stable_system_segments = [
        segment
        for segment in assembly.segments
        if segment.kind.startswith("system_prompt:") and segment.stable
    ]
    tool_schema_segments = [
        segment for segment in assembly.segments if segment.kind == "tool_schema_bundle"
    ]
    preserved_message_segments = [
        segment
        for index, segment in stable_message_segments
        if index in preserved_message_index_set
    ]
    preserved_segments = tuple(
        [*stable_system_segments, *tool_schema_segments, *preserved_message_segments]
    )
    preserved_segment_ids = {segment.segment_id for segment in preserved_segments}
    dynamic_tail_segments = tuple(
        segment for segment in assembly.segments if segment.segment_id not in preserved_segment_ids
    )
    previous_preserved_signature = (
        str(previous_payload.get("prompt_prefix_preserved_signature") or "").strip() or None
    )
    preserved_signature = _signature_for_parts(
        [segment.signature for segment in preserved_segments]
    )
    change_reason = _assembly_change_reason(assembly, previous_payload)
    planner_reason = _planner_reason(
        assembly=assembly,
        change_reason=change_reason,
        stable_message_indices=stable_message_indices,
        reduced_indices=reduced_indices,
        previous_preserved_signature=previous_preserved_signature,
        preserved_signature=preserved_signature,
    )
    planner_mode = _planner_mode(
        supports_prompt_cache_hints=supports_prompt_cache_hints,
        cache_eligible_segment_count=len(preserved_segments),
    )
    cache_summary = _planner_cache_summary(
        planner_reason=planner_reason,
        previous_preserved_signature=previous_preserved_signature,
        preserved_signature=preserved_signature,
    )
    return ProviderViewPrefixPlan(
        assembly=assembly,
        stable_prefix_segments=preserved_segments,
        dynamic_tail_segments=dynamic_tail_segments,
        preserved_message_group_count=len(preserved_message_indices),
        downgraded_message_group_count=len(reduced_indices),
        selected_reduction_tier=assembly.provider_view_reduction_tier,
        planner_mode=planner_mode,
        planner_reason=planner_reason,
        preserved_prefix_signature=preserved_signature,
        preserved_segment_count=len(preserved_segments),
        preserved_chars=sum(segment.char_count for segment in preserved_segments),
        cache_eligible_segment_count=len(preserved_segments),
        cache_eligibility_summary=cache_summary,
        stable_message_group_indices=stable_message_indices,
        preserved_message_group_indices=preserved_message_indices,
        downgraded_message_group_indices=tuple(sorted(reduced_indices)),
        previous_preserved_signature=previous_preserved_signature,
        preserved_prefix_changed=bool(previous_preserved_signature)
        and previous_preserved_signature != preserved_signature,
    )


def estimate_provider_view_cost(
    session: "Session",
    assembly: PromptPrefixAssemblyResult,
    prefix_plan: ProviderViewPrefixPlan,
) -> ProviderViewCostBreakdown:
    system_prompt_overhead_estimate = sum(
        estimate_text_tokens(block.text) for block in assembly.system_prompt_blocks
    )
    tool_schema_overhead_estimate = sum(
        estimate_text_tokens(_canonical_tool_schema(tool)) for tool in assembly.tools
    )
    per_message_group_estimated_tokens: list[tuple[int, int]] = []
    for segment in assembly.segments:
        message_index = _message_group_index(segment)
        if message_index is None:
            continue
        per_message_group_estimated_tokens.append(
            (message_index, estimate_text_tokens(_message_segment_payload(assembly, message_index)))
        )
    message_token_map = {index: tokens for index, tokens in per_message_group_estimated_tokens}
    stable_prefix_message_tokens = sum(
        message_token_map.get(index, 0)
        for index in prefix_plan.preserved_message_group_indices
    )
    estimated_input_tokens = (
        system_prompt_overhead_estimate
        + tool_schema_overhead_estimate
        + sum(tokens for _index, tokens in per_message_group_estimated_tokens)
    )
    estimated_stable_prefix_tokens = (
        system_prompt_overhead_estimate + tool_schema_overhead_estimate + stable_prefix_message_tokens
    )
    estimated_dynamic_tail_tokens = max(estimated_input_tokens - estimated_stable_prefix_tokens, 0)
    return ProviderViewCostBreakdown(
        estimated_input_tokens=estimated_input_tokens,
        estimated_stable_prefix_tokens=estimated_stable_prefix_tokens,
        estimated_dynamic_tail_tokens=estimated_dynamic_tail_tokens,
        tool_schema_overhead_estimate=tool_schema_overhead_estimate,
        system_prompt_overhead_estimate=system_prompt_overhead_estimate,
        per_message_group_estimated_tokens=tuple(per_message_group_estimated_tokens),
    )


def build_provider_view_costed_plan(
    session: "Session",
    prefix_plan: ProviderViewPrefixPlan,
    *,
    available_reduction_candidates: tuple[ProviderViewReductionCandidate, ...] | None = None,
    minimum_tokens_to_shed: int = 0,
) -> ProviderViewPrefixCostedPlan:
    assembly = prefix_plan.assembly
    cost_breakdown = estimate_provider_view_cost(session, assembly, prefix_plan)
    target_tokens_to_shed = max(
        cost_breakdown.estimated_input_tokens - int(session.config.max_tokens),
        int(minimum_tokens_to_shed or 0),
        0,
    )
    explicit_candidates = available_reduction_candidates is not None
    available_candidates = (
        available_reduction_candidates
        if available_reduction_candidates is not None
        else _build_reduction_candidates(prefix_plan, cost_breakdown, target_tokens_to_shed)
    )
    chosen_candidates = _select_reduction_candidates(
        available_candidates,
        target_tokens_to_shed=target_tokens_to_shed,
        select_under_budget_existing=not explicit_candidates,
    )
    estimated_tokens_shed = sum(candidate.estimated_tokens_saved for candidate in chosen_candidates)
    estimated_chars_shed = sum(candidate.estimated_chars_saved for candidate in chosen_candidates)
    remaining_overage = max(target_tokens_to_shed - estimated_tokens_shed, 0)
    prefix_damage_score = sum(candidate.prefix_damage_score for candidate in chosen_candidates)
    final_verdict = _final_planner_verdict(
        prefix_plan=prefix_plan,
        target_tokens_to_shed=target_tokens_to_shed,
        remaining_overage=remaining_overage,
        chosen_candidates=chosen_candidates,
    )
    selected_lookup = {
        candidate.candidate_id
        for candidate in chosen_candidates
    }
    normalized_candidates = tuple(
        ProviderViewReductionCandidate(
            kind=candidate.kind,
            estimated_tokens_saved=candidate.estimated_tokens_saved,
            estimated_chars_saved=candidate.estimated_chars_saved,
            prefix_damage_score=candidate.prefix_damage_score,
            breaks_preserved_prefix=candidate.breaks_preserved_prefix,
            affects_message_group_indices=candidate.affects_message_group_indices,
            reason=candidate.reason,
            candidate_id=candidate.candidate_id,
            selected=candidate.candidate_id in selected_lookup,
        )
        for candidate in available_candidates
    )
    return ProviderViewPrefixCostedPlan(
        prefix_plan=prefix_plan,
        cost_breakdown=cost_breakdown,
        target_tokens_to_shed=target_tokens_to_shed,
        available_reduction_candidates=normalized_candidates,
        chosen_candidate_sequence=tuple(chosen_candidates),
        estimated_tokens_shed=estimated_tokens_shed,
        estimated_chars_shed=estimated_chars_shed,
        remaining_estimated_overage=remaining_overage,
        prefix_damage_score=prefix_damage_score,
        final_planner_verdict=final_verdict,
    )


def build_provider_prompt_cache_plan(
    costed_plan: ProviderViewPrefixCostedPlan,
    *,
    provider_capabilities: ProviderCapabilities | None,
) -> ProviderPromptCachePlan:
    prefix_plan = costed_plan.prefix_plan
    assembly = prefix_plan.assembly
    provider_name = getattr(provider_capabilities, "provider", None) or "unknown"
    supports_prompt_cache_hints = bool(
        getattr(provider_capabilities, "supports_prompt_cache_hints", False)
    )
    supports_system_prompt_cache_blocks = bool(
        getattr(provider_capabilities, "supports_system_prompt_cache_blocks", False)
    )
    supports_tool_schema_cache_hints = bool(
        getattr(provider_capabilities, "supports_tool_schema_cache_hints", False)
    )

    stable_prefix_segment_ids = {
        segment.segment_id for segment in prefix_plan.stable_prefix_segments
    }
    cache_hints: list[ProviderCacheHint] = []
    system_prompt_blocks: list[dict[str, Any]] = []
    for index, block in enumerate(assembly.system_prompt_blocks):
        cache_scope = _provider_cache_scope_for_block(block)
        signature = _signature_for_block(block)
        segment_id = f"system_prompt:{index}:{block.kind}"
        is_preserved = segment_id in stable_prefix_segment_ids
        cache_hints.append(
            ProviderCacheHint(
                kind="system_prompt_block",
                cache_scope=cache_scope,
                stable=is_preserved,
                signature=signature,
                summary=f"{block.kind} ({block.cache_scope})",
            )
        )
        rendered_block: dict[str, Any] = {"type": "text", "text": block.text}
        if (
            prefix_plan.planner_mode == "provider_hinted"
            and supports_system_prompt_cache_blocks
            and cache_scope != "none"
            and is_preserved
        ):
            rendered_block["cache_control"] = {
                "type": "ephemeral",
                "scope": cache_scope,
            }
        system_prompt_blocks.append(rendered_block)

    tool_schema_preserved = any(
        segment.kind == "tool_schema_bundle" for segment in prefix_plan.stable_prefix_segments
    )
    cache_hints.append(
        ProviderCacheHint(
            kind="tool_schema_bundle",
            cache_scope="org",
            stable=tool_schema_preserved,
            signature=assembly.tool_schema_signature,
            summary=f"{len(assembly.tools)} tool(s)",
        )
    )
    if prefix_plan.preserved_message_group_count > 0:
        cache_hints.append(
            ProviderCacheHint(
                kind="message_prefix",
                cache_scope="org",
                stable=True,
                signature=prefix_plan.preserved_prefix_signature,
                summary=(
                    f"{prefix_plan.preserved_message_group_count} preserved message group(s); "
                    f"downgraded={prefix_plan.downgraded_message_group_count}"
                ),
            )
        )

    hinted_tools = [dict(tool) for tool in assembly.tools]
    if (
        prefix_plan.planner_mode == "provider_hinted"
        and supports_tool_schema_cache_hints
        and tool_schema_preserved
    ):
        for tool in hinted_tools:
            tool["cache_control"] = {"type": "ephemeral", "scope": "org"}

    return ProviderPromptCachePlan(
        system_prompt=assembly.system_prompt,
        tools=hinted_tools,
        messages=list(assembly.messages),
        cache_hints=tuple(cache_hints),
        provider_cache_supported=supports_prompt_cache_hints,
        provider_cache_mode=prefix_plan.planner_mode,
        provider_cache_summary=_costed_cache_summary(costed_plan),
        provider_cache_provider=str(provider_name),
        system_prompt_blocks=tuple(system_prompt_blocks),
        prefix_plan=prefix_plan,
        costed_plan=costed_plan,
    )


def prompt_prefix_cache_payload_from_plan(plan: ProviderPromptCachePlan | None) -> dict[str, Any]:
    if plan is None or plan.prefix_plan is None:
        return {
            "prompt_prefix_cache_mode": "disabled",
            "prompt_prefix_cache_supported": False,
            "prompt_prefix_cache_provider": "none",
            "prompt_prefix_cache_summary": "none",
            "prompt_prefix_cache_fallback_reason": "none",
            "prompt_prefix_planner_mode": "disabled",
            "prompt_prefix_planner_reason": "none",
            "prompt_prefix_preserved_signature": "none",
            "prompt_prefix_preserved_segment_count": 0,
            "prompt_prefix_preserved_message_group_count": 0,
            "prompt_prefix_downgraded_message_group_count": 0,
            "prompt_prefix_preserved_chars": 0,
            "prompt_prefix_cache_eligible_segment_count": 0,
            "prompt_prefix_planner_summary": "none",
            "prompt_prefix_costed_planner_mode": "disabled",
            "prompt_prefix_costed_planner_reason": "none",
            "prompt_prefix_target_tokens_to_shed": 0,
            "prompt_prefix_estimated_input_tokens": 0,
            "prompt_prefix_estimated_stable_prefix_tokens": 0,
            "prompt_prefix_estimated_dynamic_tail_tokens": 0,
            "prompt_prefix_selected_candidate_count": 0,
            "prompt_prefix_selected_candidate_summary": "none",
            "prompt_prefix_remaining_estimated_overage": 0,
            "prompt_prefix_prefix_damage_score": 0,
            "prompt_prefix_orchestration_mode": "disabled",
            "prompt_prefix_orchestration_reason": "none",
            "prompt_prefix_orchestration_selected_candidate_count": 0,
            "prompt_prefix_orchestration_selected_candidate_summary": "none",
            "prompt_prefix_orchestration_remaining_estimated_overage": 0,
            "prompt_prefix_orchestration_requires_full_compaction": False,
        }
    prefix_plan = plan.prefix_plan
    costed_plan = getattr(plan, "costed_plan", None)
    orchestration_mode = str(plan.orchestration_mode or "disabled")
    orchestration_reason = str(plan.orchestration_reason or "none")
    orchestration_selected_candidate_count = int(
        plan.orchestration_selected_candidate_count or 0
    )
    orchestration_selected_candidate_summary = str(
        plan.orchestration_selected_candidate_summary or "none"
    )
    orchestration_remaining_estimated_overage = int(
        plan.orchestration_remaining_estimated_overage or 0
    )
    orchestration_requires_full_compaction = bool(
        plan.orchestration_requires_full_compaction
    )
    if orchestration_mode == "disabled" and costed_plan is not None:
        orchestration_mode = (
            "selected"
            if costed_plan.chosen_candidate_sequence
            else "under_budget"
        )
        orchestration_reason = str(costed_plan.final_planner_verdict or "none")
        orchestration_selected_candidate_count = int(
            len(costed_plan.chosen_candidate_sequence)
        )
        orchestration_selected_candidate_summary = _selected_candidate_summary(costed_plan)
        orchestration_remaining_estimated_overage = int(
            costed_plan.remaining_estimated_overage
        )
        orchestration_requires_full_compaction = any(
            candidate.kind == "full_compaction"
            for candidate in costed_plan.chosen_candidate_sequence
        ) or costed_plan.remaining_estimated_overage > 0
    return {
        "prompt_prefix_cache_mode": str(plan.provider_cache_mode or "disabled"),
        "prompt_prefix_cache_supported": bool(plan.provider_cache_supported),
        "prompt_prefix_cache_provider": str(plan.provider_cache_provider or "unknown"),
        "prompt_prefix_cache_summary": str(plan.provider_cache_summary or "none"),
        "prompt_prefix_cache_fallback_reason": str(
            plan.provider_cache_fallback_reason or "none"
        ),
        "prompt_prefix_planner_mode": str(prefix_plan.planner_mode or "disabled"),
        "prompt_prefix_planner_reason": str(prefix_plan.planner_reason or "none"),
        "prompt_prefix_preserved_signature": str(
            prefix_plan.preserved_prefix_signature or "none"
        ),
        "prompt_prefix_preserved_segment_count": int(
            prefix_plan.preserved_segment_count or 0
        ),
        "prompt_prefix_preserved_message_group_count": int(
            prefix_plan.preserved_message_group_count or 0
        ),
        "prompt_prefix_downgraded_message_group_count": int(
            prefix_plan.downgraded_message_group_count or 0
        ),
        "prompt_prefix_preserved_chars": int(prefix_plan.preserved_chars or 0),
        "prompt_prefix_cache_eligible_segment_count": int(
            prefix_plan.cache_eligible_segment_count or 0
        ),
        "prompt_prefix_planner_summary": (
            f"preserved_groups={prefix_plan.preserved_message_group_count} "
            f"downgraded_groups={prefix_plan.downgraded_message_group_count} "
            f"eligible_segments={prefix_plan.cache_eligible_segment_count}"
        ),
        "prompt_prefix_costed_planner_mode": (
            "selected"
            if costed_plan is not None and costed_plan.chosen_candidate_sequence
            else ("under_budget" if costed_plan is not None else "disabled")
        ),
        "prompt_prefix_costed_planner_reason": (
            str(costed_plan.final_planner_verdict or "none") if costed_plan is not None else "none"
        ),
        "prompt_prefix_target_tokens_to_shed": int(
            costed_plan.target_tokens_to_shed if costed_plan is not None else 0
        ),
        "prompt_prefix_estimated_input_tokens": int(
            costed_plan.cost_breakdown.estimated_input_tokens if costed_plan is not None else 0
        ),
        "prompt_prefix_estimated_stable_prefix_tokens": int(
            costed_plan.cost_breakdown.estimated_stable_prefix_tokens if costed_plan is not None else 0
        ),
        "prompt_prefix_estimated_dynamic_tail_tokens": int(
            costed_plan.cost_breakdown.estimated_dynamic_tail_tokens if costed_plan is not None else 0
        ),
        "prompt_prefix_selected_candidate_count": int(
            len(costed_plan.chosen_candidate_sequence) if costed_plan is not None else 0
        ),
        "prompt_prefix_selected_candidate_summary": (
            _selected_candidate_summary(costed_plan) if costed_plan is not None else "none"
        ),
        "prompt_prefix_remaining_estimated_overage": int(
            costed_plan.remaining_estimated_overage if costed_plan is not None else 0
        ),
        "prompt_prefix_prefix_damage_score": int(
            costed_plan.prefix_damage_score if costed_plan is not None else 0
        ),
        "prompt_prefix_orchestration_mode": orchestration_mode,
        "prompt_prefix_orchestration_reason": orchestration_reason,
        "prompt_prefix_orchestration_selected_candidate_count": orchestration_selected_candidate_count,
        "prompt_prefix_orchestration_selected_candidate_summary": orchestration_selected_candidate_summary,
        "prompt_prefix_orchestration_remaining_estimated_overage": orchestration_remaining_estimated_overage,
        "prompt_prefix_orchestration_requires_full_compaction": orchestration_requires_full_compaction,
    }


def _planner_mode(
    *,
    supports_prompt_cache_hints: bool,
    cache_eligible_segment_count: int,
) -> ProviderCacheMode:
    if not supports_prompt_cache_hints:
        return "diagnostic_only"
    if cache_eligible_segment_count <= 0:
        return "disabled"
    return "provider_hinted"


def _costed_cache_summary(costed_plan: ProviderViewPrefixCostedPlan) -> str:
    if costed_plan.remaining_estimated_overage > 0:
        return "remaining overage"
    if costed_plan.target_tokens_to_shed <= 0:
        return costed_plan.prefix_plan.cache_eligibility_summary
    if costed_plan.chosen_candidate_sequence:
        return "prefix-preserving reduction"
    return costed_plan.prefix_plan.cache_eligibility_summary


def _planner_cache_summary(
    *,
    planner_reason: str,
    previous_preserved_signature: str | None,
    preserved_signature: str,
) -> str:
    if planner_reason in {"tool_schema_drift", "system_prompt_drift"}:
        return planner_reason.replace("_", " ")
    if planner_reason in {
        "microcompact_in_stable_prefix",
        "full_compaction_required",
    }:
        return "reduction tier active"
    if not previous_preserved_signature:
        return "stable prefix preserved"
    if previous_preserved_signature == preserved_signature:
        if planner_reason == "dynamic_tail_only":
            return "dynamic tail only"
        return "stable prefix preserved"
    if planner_reason in {"microcompact_on_tail", "replacement_reapply_only", "artifact_indirection_active"}:
        return "stable prefix preserved"
    return "stable prefix changed"


def _planner_reason(
    *,
    assembly: PromptPrefixAssemblyResult,
    change_reason: str,
    stable_message_indices: tuple[int, ...],
    reduced_indices: set[int],
    previous_preserved_signature: str | None,
    preserved_signature: str,
) -> str:
    if change_reason == "tool_schema_bundle":
        return "tool_schema_drift"
    if change_reason == "system_prompt_blocks":
        return "system_prompt_drift"
    if assembly.provider_view_reduction_tier == "full_compaction":
        return "full_compaction_required"
    if assembly.provider_view_reduction_tier == "microcompact":
        if any(index in reduced_indices for index in stable_message_indices):
            return "microcompact_in_stable_prefix"
        return "microcompact_on_tail"
    if assembly.provider_view_reduction_tier == "artifact_indirection":
        return "artifact_indirection_active"
    if assembly.provider_view_reduction_tier == "replacement":
        return "replacement_reapply_only"
    if (
        previous_preserved_signature is not None
        and previous_preserved_signature == preserved_signature
    ):
        return "dynamic_tail_only"
    return "none"


def _reduction_affected_message_group_indices(
    assembly: PromptPrefixAssemblyResult,
) -> set[int]:
    if assembly.provider_view_reduction_tier == "full_compaction":
        return {
            index
            for index, segment in (
                (_message_group_index(segment), segment)
                for segment in assembly.segments
                if segment.kind.startswith("message_group:")
            )
            if index is not None and segment.stable
        }
    return set(assembly.microcompacted_message_group_indices)


def _should_reset_preserved_prefix(
    previous_payload: dict[str, Any],
    previous_reason: str,
) -> bool:
    if not previous_payload:
        return True
    previous_mode = str(previous_payload.get("prompt_prefix_planner_mode") or "disabled")
    if previous_mode != "provider_hinted":
        return True
    return previous_reason in _PLANNER_RESET_REASONS


def _assembly_change_reason(
    assembly: PromptPrefixAssemblyResult,
    previous_payload: dict[str, Any] | None,
) -> str:
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
        return "none"
    if not changed:
        return "preserved"
    if previous_system_signature != assembly.static_system_prompt_signature:
        return "system_prompt_blocks"
    if previous_tool_signature != assembly.tool_schema_signature:
        return "tool_schema_bundle"
    return "provider_view_messages"


def _provider_cache_scope_for_block(block: SystemPromptBlock) -> ProviderCacheScope:
    if block.cache_scope == "global":
        return "global"
    if block.cache_scope == "session":
        return "org"
    if block.cache_scope == "dynamic":
        return "ephemeral"
    return "none"


def _message_group_index(segment: PrefixSegment) -> int | None:
    if not segment.segment_id.startswith("message:"):
        return None
    parts = segment.segment_id.split(":")
    if len(parts) < 3:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def _signature_for_block(block: SystemPromptBlock) -> str:
    return sha256(str(block.text or "").encode("utf-8")).hexdigest()[:16]


def _signature_for_parts(parts: list[str]) -> str:
    if not parts:
        return "none"
    digest = sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()[:16]


def planner_downgraded(plan: ProviderViewPrefixPlan | None) -> bool:
    if plan is None:
        return False
    return plan.planner_reason in _PLANNER_DOWNGRADED_REASONS


def _canonical_tool_schema(tool: dict[str, Any]) -> str:
    return json.dumps(tool, ensure_ascii=True, sort_keys=True)


def _message_segment_payload(assembly: PromptPrefixAssemblyResult, message_index: int) -> str:
    try:
        message = assembly.messages[message_index]
    except IndexError:
        return ""
    return json.dumps(message, ensure_ascii=True, sort_keys=True)


def _build_reduction_candidates(
    prefix_plan: ProviderViewPrefixPlan,
    cost_breakdown: ProviderViewCostBreakdown,
    target_tokens_to_shed: int,
) -> tuple[ProviderViewReductionCandidate, ...]:
    assembly = prefix_plan.assembly
    candidates: list[ProviderViewReductionCandidate] = []
    if assembly.replacement_count > 0:
        candidates.append(
            ProviderViewReductionCandidate(
                kind="replacement_reapply",
                estimated_tokens_saved=max(assembly.replaced_tokens_total, 0),
                estimated_chars_saved=max(assembly.replaced_chars_total, 0),
                prefix_damage_score=1,
                breaks_preserved_prefix=False,
                affects_message_group_indices=prefix_plan.downgraded_message_group_indices,
                reason="replacement_reapply",
                candidate_id="active:replacement_reapply",
            )
        )
    if assembly.artifact_count > 0:
        candidates.append(
            ProviderViewReductionCandidate(
                kind="artifact_indirection",
                estimated_tokens_saved=max(assembly.artifact_tokens_saved, 0),
                estimated_chars_saved=max(assembly.artifact_chars_saved, 0),
                prefix_damage_score=1,
                breaks_preserved_prefix=False,
                affects_message_group_indices=prefix_plan.downgraded_message_group_indices,
                reason="artifact_indirection",
                candidate_id="active:artifact_indirection",
            )
        )
    if assembly.microcompact_count > 0:
        breaks_prefix = prefix_plan.planner_reason == "microcompact_in_stable_prefix"
        candidates.append(
            ProviderViewReductionCandidate(
                kind="microcompact_prefix" if breaks_prefix else "microcompact_tail",
                estimated_tokens_saved=max(assembly.microcompact_tokens_saved, 0),
                estimated_chars_saved=max(assembly.microcompact_chars_saved, 0),
                prefix_damage_score=3 if breaks_prefix else 2,
                breaks_preserved_prefix=breaks_prefix,
                affects_message_group_indices=tuple(assembly.microcompacted_message_group_indices),
                reason=prefix_plan.planner_reason,
                candidate_id="active:microcompact",
            )
        )
    if target_tokens_to_shed > 0 or assembly.provider_view_reduction_tier == "full_compaction":
        candidates.append(
            ProviderViewReductionCandidate(
                kind="full_compaction",
                estimated_tokens_saved=max(
                    target_tokens_to_shed,
                    cost_breakdown.estimated_dynamic_tail_tokens,
                ),
                estimated_chars_saved=max(assembly.dynamic_tail_chars, 0),
                prefix_damage_score=10,
                breaks_preserved_prefix=True,
                affects_message_group_indices=prefix_plan.stable_message_group_indices,
                reason="full_compaction_required",
                candidate_id="active:full_compaction",
            )
        )
    if not candidates:
        candidates.append(
            ProviderViewReductionCandidate(
                kind="none",
                estimated_tokens_saved=0,
                estimated_chars_saved=0,
                prefix_damage_score=0,
                breaks_preserved_prefix=False,
                affects_message_group_indices=(),
                reason="none",
                candidate_id="active:none",
            )
        )
    return tuple(candidates)


def _select_reduction_candidates(
    candidates: tuple[ProviderViewReductionCandidate, ...],
    *,
    target_tokens_to_shed: int,
    select_under_budget_existing: bool,
) -> tuple[ProviderViewReductionCandidate, ...]:
    if target_tokens_to_shed <= 0:
        if not select_under_budget_existing:
            return ()
        return tuple(
            candidate
            for candidate in candidates
            if candidate.kind != "full_compaction"
            and candidate.kind != "none"
            and candidate.estimated_tokens_saved > 0
        )
    ordered = sorted(
        (candidate for candidate in candidates if candidate.kind != "none"),
        key=lambda candidate: (
            candidate.prefix_damage_score,
            len(candidate.affects_message_group_indices),
            0 if not candidate.breaks_preserved_prefix else 1,
            -candidate.estimated_tokens_saved,
        ),
    )
    selected: list[ProviderViewReductionCandidate] = []
    remaining = target_tokens_to_shed
    for candidate in ordered:
        if remaining <= 0:
            break
        selected.append(candidate)
        remaining = max(remaining - candidate.estimated_tokens_saved, 0)
        if candidate.kind == "full_compaction":
            break
    return tuple(selected)


def _final_planner_verdict(
    *,
    prefix_plan: ProviderViewPrefixPlan,
    target_tokens_to_shed: int,
    remaining_overage: int,
    chosen_candidates: tuple[ProviderViewReductionCandidate, ...],
) -> str:
    if remaining_overage > 0:
        return "remaining_overage"
    if any(candidate.kind == "full_compaction" for candidate in chosen_candidates):
        return "full_compaction_required"
    if any(candidate.kind.startswith("microcompact") for candidate in chosen_candidates):
        if any(candidate.kind == "microcompact_prefix" for candidate in chosen_candidates):
            return "microcompact_in_stable_prefix"
        return "microcompact_on_tail"
    if any(candidate.kind == "artifact_indirection" for candidate in chosen_candidates):
        return "artifact_indirection_active"
    if any(candidate.kind == "replacement_reapply" for candidate in chosen_candidates):
        return "replacement_reapply_only"
    if target_tokens_to_shed <= 0:
        return "under_budget"
    return prefix_plan.planner_reason


def _selected_candidate_summary(costed_plan: ProviderViewPrefixCostedPlan) -> str:
    if not costed_plan.chosen_candidate_sequence:
        return "none"
    parts = []
    for candidate in costed_plan.chosen_candidate_sequence:
        parts.append(
            f"{candidate.kind} shed_tokens={candidate.estimated_tokens_saved} "
            f"damage={candidate.prefix_damage_score}"
        )
    return "; ".join(parts)
