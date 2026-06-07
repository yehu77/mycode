# Provider-View Prefix Planner

## Summary

This line deepens the prompt-prefix runtime from a coarse `PromptPrefixAssemblyResult -> ProviderPromptCachePlan` conversion into a real provider-view planner. The planner now decides which prefix segments remain preserved, which message groups were downgraded, and how cache hinting should apply to the preserved subset rather than the whole request.

## Key Changes

- Added a runtime-local `ProviderViewPrefixPlan` above the raw prompt-prefix assembly.
- Planner decisions now operate at message-group granularity and keep a preserved-prefix baseline across turns.
- Cache-plan assembly now consumes the planner result instead of inspecting raw assembly state directly.
- Planner fields are exposed through `/context`, `/status workflow`, stdio, remote, and TUI payloads.
- Anthropic-compatible cache hinting continues to consume system/tool hint structure through the existing additive `cache_plan` path.

## Planner Contract

- `PromptPrefixAssemblyResult` remains the lower-level provider-view snapshot.
- `ProviderViewPrefixPlan` becomes the source of truth for:
  - preserved stable prefix segments
  - downgraded message-group count
  - reduction tier
  - planner reason
  - preserved-prefix signature
  - cache-eligible segment count
- Planner reason vocabulary is normalized around:
  - `none`
  - `dynamic_tail_only`
  - `replacement_reapply_only`
  - `artifact_indirection_active`
  - `microcompact_on_tail`
  - `microcompact_in_stable_prefix`
  - `tool_schema_drift`
  - `system_prompt_drift`
  - `full_compaction_required`

## Tests

- Dynamic-tail-only changes preserve the planner preserved-prefix signature.
- Tail-only microcompact keeps cache hinting enabled for the preserved prefix subset.
- Stable-prefix microcompact downgrades preserved message groups deterministically.
- Query loop emits planner events in addition to cache-hint events.
- Session and remote status surfaces expose aligned planner fields.

## Assumptions

- The planner is runtime-local and not persisted.
- Replacement and artifact records remain the only persisted prefix-preservation decisions.
- Anthropic remains the first real cache-hint consumer, but the planner itself stays provider-neutral.
