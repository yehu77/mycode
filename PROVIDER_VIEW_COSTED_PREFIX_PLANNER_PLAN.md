# Provider-View Costed Prefix Planner

## Summary

This line deepens the existing provider-view prefix planner into a costed planner. The runtime no longer stops at "which prefix survived"; it also estimates provider-view input cost, computes how much context needs to be shed, summarizes available reduction candidates, and reports the selected reduction path together with its estimated prefix damage.

The implementation stays runtime-local and provider-abstracted:

- no transcript schema changes
- no provider signature changes beyond the existing additive `cache_plan`
- no changes to persisted replacement or artifact records
- no hosted cache or provider-side dry-run counting

## Implemented

- Added `ProviderViewPrefixCostedPlan`, `ProviderViewReductionCandidate`, and `ProviderViewCostBreakdown` in [runtime/provider_cache.py](/C:/Users/86177/Desktop/claude/claude-code-v-2.1.88-main/python_claudecode/claudecode_py/runtime/provider_cache.py).
- Added heuristic provider-view costing for:
  - total estimated input tokens
  - stable-prefix estimated tokens
  - dynamic-tail estimated tokens
  - per-message-group estimated tokens
  - tool-schema and system-prompt overhead
- Kept the existing `PromptPrefixAssemblyResult -> ProviderViewPrefixPlan` stack and layered the costed planner above it.
- Extended prompt-prefix payloads and workflow narratives with:
  - `prompt_prefix_costed_planner_mode`
  - `prompt_prefix_costed_planner_reason`
  - `prompt_prefix_target_tokens_to_shed`
  - `prompt_prefix_estimated_input_tokens`
  - `prompt_prefix_estimated_stable_prefix_tokens`
  - `prompt_prefix_estimated_dynamic_tail_tokens`
  - `prompt_prefix_selected_candidate_count`
  - `prompt_prefix_selected_candidate_summary`
  - `prompt_prefix_remaining_estimated_overage`
  - `prompt_prefix_prefix_damage_score`
- Kept provider cache semantics downstream of the planner so Anthropic-compatible cache hinting still operates on the final preserved subset.
- Added coverage in session, remote, provider-planner, and TUI tests.

## Current Boundary

- The first version uses local heuristics from current context-usage estimation rather than provider-side token counting.
- Replacement and artifact reductions are still applied earlier in the provider-view preparation path; the costed planner currently models and reports them rather than becoming the sole mutation controller.
- Candidate selection is message-group aware and cost-aware, but it is still intentionally conservative and does not attempt exhaustive search.
- Full compaction remains the outer recovery boundary for actual prompt-too-long recovery.

## Remaining Gap

The remaining gap is narrower and structural rather than broad feature parity:

- move more of the provider-view mutation path from "apply then report" toward "plan then apply"
- deepen candidate search so tail-only reductions win more systematically over prefix-breaking reductions
- tighten provider-view budgeting around the exact preserved subset that survives planning

Beyond that, the larger remaining difference is broader upstream runtime breadth, not absence of a local costed prefix planner.
