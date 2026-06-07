from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .provider_cache import (
    ProviderPromptCachePlan,
    ProviderViewPrefixCostedPlan,
    ProviderViewPrefixPlan,
    ProviderViewReductionCandidate,
    build_provider_prompt_cache_plan,
    build_provider_view_costed_plan,
    build_provider_view_prefix_plan,
)
from .tool_result_replacement import (
    FrozenToolResultReductionView,
    ToolResultBudgetResult,
    ToolResultReductionCandidate,
    apply_selected_tool_result_reductions,
    build_tool_result_reduction_candidates,
    estimate_tool_result_group_pressure_tokens,
    reapply_frozen_tool_result_reductions,
    select_tool_result_reduction_candidates_for_groups,
    tool_result_group_char_limit,
)

if TYPE_CHECKING:
    from ..session import Session


@dataclass(slots=True, frozen=True)
class ProviderViewReductionOrchestrationResult:
    raw_messages: list[dict[str, Any]]
    frozen_view: FrozenToolResultReductionView
    prefix_plan: ProviderViewPrefixPlan
    costed_plan: ProviderViewPrefixCostedPlan
    available_selectable_candidates: tuple[ToolResultReductionCandidate, ...]
    chosen_selectable_candidates: tuple[ToolResultReductionCandidate, ...]
    final_reduction_result: ToolResultBudgetResult
    final_messages: list[dict[str, Any]]
    final_assembly: Any
    final_cache_plan: ProviderPromptCachePlan
    requires_full_compaction: bool


def build_provider_view_reduction_orchestration(
    session: "Session",
    *,
    raw_messages: list[dict[str, Any]],
    previous_payload: dict[str, Any] | None = None,
    compacted_before_provider: bool = False,
) -> ProviderViewReductionOrchestrationResult:
    frozen_view = reapply_frozen_tool_result_reductions(session, raw_messages)
    frozen_assembly = session.build_provider_prompt_assembly(
        messages=frozen_view.messages,
        compacted_before_provider=compacted_before_provider,
    )
    prefix_plan = build_provider_view_prefix_plan(
        frozen_assembly,
        provider_capabilities=getattr(session.provider, "capabilities", None),
        previous_payload=previous_payload,
    )
    selectable_candidates = build_tool_result_reduction_candidates(
        session,
        frozen_view.messages,
    )
    provider_candidates = _build_provider_reduction_candidates(
        prefix_plan,
        selectable_candidates,
        include_full_compaction=True,
    )
    costed_plan = build_provider_view_costed_plan(
        session,
        prefix_plan,
        available_reduction_candidates=provider_candidates,
        minimum_tokens_to_shed=estimate_tool_result_group_pressure_tokens(
            session,
            frozen_view.messages,
        ),
    )
    selected_candidate_ids = {
        candidate.candidate_id
        for candidate in costed_plan.chosen_candidate_sequence
        if candidate.candidate_id
    }
    chosen_selectable_candidates = tuple(
        candidate
        for candidate in selectable_candidates
        if _tool_candidate_id(candidate) in selected_candidate_ids
    )
    if not chosen_selectable_candidates and selectable_candidates:
        chosen_selectable_candidates = select_tool_result_reduction_candidates_for_groups(
            selectable_candidates,
            messages=frozen_view.messages,
            group_limit_chars=tool_result_group_char_limit(session),
        )
    final_reduction_result = apply_selected_tool_result_reductions(
        session,
        frozen_view.messages,
        chosen_selectable_candidates,
        reapplied_count=frozen_view.reapplied_count,
        artifact_reuse_count=frozen_view.artifact_reuse_count,
    )
    final_assembly = session.build_provider_prompt_assembly(
        messages=final_reduction_result.messages,
        replacement_result=final_reduction_result,
        compacted_before_provider=compacted_before_provider,
    )
    final_cache_plan = build_provider_prompt_cache_plan(
        session.build_provider_view_costed_plan(
            final_assembly,
            previous_payload=previous_payload,
        ),
        provider_capabilities=getattr(session.provider, "capabilities", None),
    )
    final_cache_plan.orchestration_mode = (
        "selected" if chosen_selectable_candidates else "under_budget"
    )
    final_cache_plan.orchestration_reason = str(
        costed_plan.final_planner_verdict or "none"
    )
    final_cache_plan.orchestration_selected_candidate_count = len(
        chosen_selectable_candidates
    )
    final_cache_plan.orchestration_selected_candidate_summary = (
        _selected_orchestration_candidate_summary(chosen_selectable_candidates)
    )
    final_cache_plan.orchestration_remaining_estimated_overage = int(
        costed_plan.remaining_estimated_overage
    )
    final_cache_plan.orchestration_requires_full_compaction = any(
        candidate.kind == "full_compaction"
        for candidate in costed_plan.chosen_candidate_sequence
    ) or costed_plan.remaining_estimated_overage > 0
    return ProviderViewReductionOrchestrationResult(
        raw_messages=list(raw_messages),
        frozen_view=frozen_view,
        prefix_plan=prefix_plan,
        costed_plan=costed_plan,
        available_selectable_candidates=selectable_candidates,
        chosen_selectable_candidates=chosen_selectable_candidates,
        final_reduction_result=final_reduction_result,
        final_messages=final_reduction_result.messages,
        final_assembly=final_assembly,
        final_cache_plan=final_cache_plan,
        requires_full_compaction=final_cache_plan.orchestration_requires_full_compaction,
    )


def _build_provider_reduction_candidates(
    prefix_plan: ProviderViewPrefixPlan,
    selectable_candidates: tuple[ToolResultReductionCandidate, ...],
    *,
    include_full_compaction: bool,
) -> tuple[ProviderViewReductionCandidate, ...]:
    preserved_indices = set(prefix_plan.preserved_message_group_indices)
    provider_candidates: list[ProviderViewReductionCandidate] = []
    for candidate in selectable_candidates:
        is_preserved_group = candidate.message_group_index in preserved_indices
        if candidate.kind == "artifact_indirection":
            kind = "artifact_indirection"
            reason = "artifact_indirection_active"
            prefix_damage_score = 1 if not is_preserved_group else 2
            breaks_prefix = False
        else:
            kind = "microcompact_prefix" if is_preserved_group else "microcompact_tail"
            reason = "microcompact_in_stable_prefix" if is_preserved_group else "microcompact_on_tail"
            prefix_damage_score = 3 if is_preserved_group else 2
            breaks_prefix = is_preserved_group
        provider_candidates.append(
            ProviderViewReductionCandidate(
                kind=kind,
                estimated_tokens_saved=candidate.estimated_tokens_saved,
                estimated_chars_saved=candidate.estimated_chars_saved,
                prefix_damage_score=prefix_damage_score,
                breaks_preserved_prefix=breaks_prefix,
                affects_message_group_indices=(candidate.message_group_index,),
                reason=reason,
                candidate_id=_tool_candidate_id(candidate),
            )
        )
    if include_full_compaction:
        provider_candidates.append(
            ProviderViewReductionCandidate(
                kind="full_compaction",
                estimated_tokens_saved=max(
                    prefix_plan.assembly.dynamic_tail_chars // 4,
                    0,
                ),
                estimated_chars_saved=max(prefix_plan.assembly.dynamic_tail_chars, 0),
                prefix_damage_score=10,
                breaks_preserved_prefix=True,
                affects_message_group_indices=prefix_plan.stable_message_group_indices,
                reason="full_compaction_required",
                candidate_id="full_compaction",
            )
        )
    return tuple(provider_candidates)


def _tool_candidate_id(candidate: ToolResultReductionCandidate) -> str:
    return f"{candidate.kind}:{candidate.tool_use_id}:{candidate.message_group_index}"


def _selected_orchestration_candidate_summary(
    candidates: tuple[ToolResultReductionCandidate, ...],
) -> str:
    if not candidates:
        return "none"
    return "; ".join(
        f"{candidate.kind} tool_use_id={candidate.tool_use_id} "
        f"shed_tokens={candidate.estimated_tokens_saved} "
        f"damage={candidate.prefix_damage_score}"
        for candidate in candidates
    )
