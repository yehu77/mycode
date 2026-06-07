# Provider-View Reduction Orchestration

## Summary

This line upgrades provider-view reduction from an apply-first helper into a true plan-first orchestration for main-turn and prompt-too-long recovery calls.

The runtime now separates:

- frozen replacement and artifact reapply
- fresh selectable reduction candidate generation
- costed candidate selection
- selected reduction application
- final provider-view assembly and cache-plan rebuild

It stays local-first:

- no transcript schema changes
- no provider signature changes beyond the existing additive `cache_plan`
- no broader advisor or review side-call rewiring in this phase

## Implemented

- Split tool-result reduction flow in [runtime/tool_result_replacement.py](/C:/Users/86177/Desktop/claude/claude-code-v-2.1.88-main/python_claudecode/claudecode_py/runtime/tool_result_replacement.py) into:
  - `reapply_frozen_tool_result_reductions(...)`
  - `build_tool_result_reduction_candidates(...)`
  - `apply_selected_tool_result_reductions(...)`
- Added [runtime/reduction_orchestration.py](/C:/Users/86177/Desktop/claude/claude-code-v-2.1.88-main/python_claudecode/claudecode_py/runtime/reduction_orchestration.py) with `ProviderViewReductionOrchestrationResult` and `build_provider_view_reduction_orchestration(...)`.
- Changed the main provider-call and compact-retry recovery paths in [runtime/query_loop.py](/C:/Users/86177/Desktop/claude/claude-code-v-2.1.88-main/python_claudecode/claudecode_py/runtime/query_loop.py) to orchestrate before applying fresh reductions.
- Tightened [runtime/provider_cache.py](/C:/Users/86177/Desktop/claude/claude-code-v-2.1.88-main/python_claudecode/claudecode_py/runtime/provider_cache.py) so costed planning can consume explicitly selectable candidates rather than treating already-applied reductions as current-turn selections.
- Added orchestration summary fields to prompt-prefix surfaces, status payloads, remote mirrors, and TUI workflow/status views.

## Current Boundary

- Main turn and prompt-too-long compact-retry recovery now use orchestration.
- Advisor and review side calls still use the current simpler provider-call preparation path.
- Costing remains heuristic and local-first; there is still no provider-side dry-run token counting.
- Full compaction remains the outer recovery fallback when selected reductions cannot restore enough headroom.

## Remaining Gap

The remaining gap is mostly planner depth, not missing orchestration:

- deeper candidate search and better multi-candidate sequencing
- tighter coupling between selected reductions and final preserved-prefix continuity metrics
- optional expansion of orchestration to narrower non-main provider-call paths if that ever becomes worthwhile

The broader remaining difference from upstream is still wider hosted and product breadth, not lack of a local provider-view reduction orchestration.
