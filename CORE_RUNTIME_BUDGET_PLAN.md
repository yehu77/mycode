# Core Runtime Budget and Recovery Plan

This document is the completed implementation record for the local **core runtime**
budget, recovery, and runtime-progress line identified in
[CORE_RUNTIME_DIFF_MATRIX.md](./CORE_RUNTIME_DIFF_MATRIX.md).

It is intentionally limited to the local-first runtime path:

- query loop
- context usage / token budgeting
- compaction recovery
- tool execution progress / runtime events

It does **not** expand into:

- hosted transport breadth
- plugin / skills / agents product surfaces
- marketplace / distribution flows
- telemetry-complete parity with upstream product infrastructure

## Summary

The current Python implementation already has:

- a real turn loop in `claudecode_py/runtime/query_loop.py`
- runtime usage aggregation and compaction policy summaries
- manual and automatic compaction hooks
- rewindable history boundaries
- structured memory/status surfaces
- a usable runtime event stream

The high-value local runtime gaps this plan targeted were not “does the loop exist?” They were:

1. budget decisions are still spread across surface summaries and local gates
2. prompt-too-long recovery is still lighter than upstream
3. tool lifecycle events are usable but not yet rich enough to drive deeper runtime UX

This plan focused on those three gaps in that order, and the phases below now record the completed local-scope implementation.

## Scope Boundary

This plan covers:

- runtime budget state
- compact/retry recovery behavior
- tool progress and runtime event richness

This plan does **not** cover:

- remote websocket / hybrid transport parity
- product-grade telemetry or analytics parity
- upstream reactive-compact internals that depend on hosted infrastructure
- plugin/skill/agent surface improvements except where they consume the new runtime state

## Phases

### Phase 1: Unified Runtime Budget State
Status: Completed for current local scope

Goal:
make token/context budget decisions a real runtime mechanism instead of mostly a reporting surface.

Primary outcomes:

- introduce one shared runtime budget decision path that is consumed by the query loop
- consolidate current budget-related inputs:
  - provider usage
  - estimated token usage fallback
  - message-count thresholds
  - context-summary growth
  - compaction policy state
- normalize decisions into a small fixed vocabulary:
  - `ok`
  - `warning`
  - `continue_with_budget`
  - `compact_needed`
  - `hard_stop`
- make `/context`, `/status`, and memory surfaces consumers of this shared state rather than separate estimators

Implementation direction:

- center the budget decision in or adjacent to `claudecode_py/runtime/query_loop.py`
- keep `Session.compaction_policy_payload()` as an inspection surface, but have it read the same underlying runtime decision model
- prefer provider usage values when available
- preserve estimated fallback when provider usage is absent

Completed implementation notes:

- added `claudecode_py/runtime/budget.py` with shared `RuntimeBudgetState` and `compute_runtime_budget_state(...)`
- added non-persistent session-local runtime budget snapshot and runtime usage aggregation hooks on `Session`
- changed `runtime/query_loop.py` to consume the shared runtime budget state instead of branching directly on `compaction_policy_payload()`
- kept `/context`, `/status`, and compaction inspection surfaces backward-compatible by treating `compaction_policy_payload()` as an adapter over the shared runtime state
- kept the new budget state non-persistent: no transcript/schema migration, and restore recomputes from current session/runtime inputs

### Phase 2: Prompt-Too-Long and Compact Retry Recovery
Status: Completed for current local scope

Goal:
make prompt-too-long failures recover through a controlled compact-and-retry runtime path.

Primary outcomes:

- detect provider-side prompt-too-long or equivalent context-limit failures in the query loop
- trigger a bounded recovery sequence:
  1. record a runtime event
  2. compact older history into `context_summary`
  3. write a history boundary describing the recovery
  4. retry the same turn
- cap retry attempts to prevent infinite loops
- expose the recovery in:
  - `/history`
  - `/status`
  - memory lifecycle metadata

Implementation direction:

- keep the recovery path local and explicit
- reuse existing compaction and history-boundary machinery instead of adding a second compaction system
- distinguish:
  - manual compact
  - auto compact
  - recovery compact

Completed implementation notes:

- added `ProviderContextLimitError` and shared context-limit classification in `providers/errors.py`
- updated the OpenAI-compatible and Anthropic provider wrappers to classify prompt/context-limit failures before the existing retryable-network path
- introduced a main-turn-only compact-retry wrapper in `runtime/query_loop.py`
- recovery compaction now reuses the existing history compaction path with `trigger="recovery"` and normalized `prompt-too-long: ...` reasons
- left advisor/final-answer side calls outside the recovery wrapper so review prompts keep their existing semantics

### Phase 3: Budget Lifecycle Narrative Integration
Status: Completed for current local scope

Goal:
make the new runtime budget path visible across existing memory/status surfaces without introducing a new product shell.

Primary outcomes:

- `/context` clearly shows:
  - current budget state
  - why the state was chosen
  - whether the value came from provider or estimated fallback
- `/status` and memory surfaces show:
  - latest budget decision
  - whether compact was policy-driven or recovery-driven
  - whether the last turn continued under budget pressure
- stdio / remote payloads include compact budget-state summaries using the existing surface metadata model

Implementation direction:

- no new command family
- continue to route everything through existing memory/status payloads
- keep vocabulary stable across REPL/TUI/stdio/remote

Completed implementation notes:

- `/context` keeps the existing `automatic compaction policy` heading but now includes shared runtime-budget lines for state, reason, token source, last-turn tokens, and provider-usage visibility
- `/status` memory lifecycle now renders the same runtime-budget vocabulary and compact lifecycle semantics used by the shared session helper
- `memory_surface_payload()` and `status_surface_payload()` now expose additive `memory_budget_*` and `status_budget_*` fields without replacing the existing compaction contracts
- stdio and `RemoteSessionProxy` sync now carry the same additive budget fields, so REPL/headless/remote surfaces all read the same budget narrative

### Phase 4: Tool Lifecycle Event Depth
Status: Completed for current local scope

Goal:
make tool execution events rich enough to support deeper local runtime UX.

Primary outcomes:

- extend runtime events beyond:
  - `tool_started`
  - `tool_finished`
  - `tool_failed`
- add bounded new event semantics such as:
  - `tool_batch_started`
  - `tool_batch_finished`
  - `tool_waiting_for_approval`
  - `tool_result_summarized`
  - `budget_pressure`
  - `compact_recovery_started`
  - `compact_recovery_finished`
- keep event naming local and implementation-focused; do not try to reproduce upstream analytics/events one-to-one

Implementation direction:

- center changes in `claudecode_py/runtime/orchestrator.py` and `runtime/events.py`
- keep `service/stdio.py`, TUI, and remote as consumers of the same richer event stream
- do not add separate agent-only or plugin-only event layers

Completed implementation notes:

- `RuntimeEvent` now carries richer local lifecycle semantics for tool batches, approval wait, summarized tool results, budget pressure, and compact recovery
- `ToolOrchestrator` emits explicit batch and approval-wait events while keeping the existing started/finished/failed events intact
- the main query loop now emits summarized tool-result, budget-pressure, and compact-recovery lifecycle events without introducing a second progress store
- stdio event replay, bridge passthrough, remote parsing, session runtime summaries, and TUI tool/event panels all consume the same additive event fields

### Phase 5: Tool Progress and Runtime Consumers
Status: Completed for current local scope

Goal:
make the richer event stream usable across current local surfaces.

Primary outcomes:

- TUI can render more informative tool progress without needing new backend guesses
- remote/headless clients can distinguish:
  - active execution
  - approval wait
  - recovery compact
  - parallel read-only batch execution
- background-agent progress and handoff summaries can reuse the richer event model instead of deriving everything from transcript snapshots

Implementation direction:

- reuse `RuntimeEvent` as the canonical source
- avoid new side-channel progress stores unless strictly needed
- prefer compact structured fields over long free-text descriptions

Completed implementation notes:

- `Session` now maintains a non-persistent runtime-progress surface snapshot derived directly from richer runtime events, and query-loop execution routes all turn events through the same shared sink path
- `status_surface_payload()` exposes additive `status_runtime_*` summaries for active tool state, last tool outcome, parallel batches, result summaries, and compact-recovery state, which stdio and remote now forward unchanged
- background task metadata now derives its recent-activity and handoff summaries from the shared runtime-progress rules first, with transcript/change fallback only after compact recovery, budget pressure, waiting/running tool state, and tool-result summaries
- TUI status/dashboard output now surfaces runtime progress as a first-class active-workflow block instead of relying only on raw event/tool logs

### Phase 6: Final Runtime Budget Polish
Status: Completed for current local scope

Goal:
close the local runtime gap and leave a stable implementation record.

Primary outcomes:

- remove duplicated budget/compaction reasoning across runtime and surface helpers
- align wording across:
  - `/context`
  - `/status`
  - `/history`
  - stdio / remote metadata
  - TUI event/status rendering
- document the remaining runtime gaps explicitly as:
  - upstream hosted/product infrastructure breadth
  - not missing local runtime viability

Completed implementation notes:

- added a shared session-side runtime narrative formatting layer so `/context`, `/status`, TUI, and structured status/memory payloads all read the same budget, compact-lifecycle, and runtime-progress wording
- changed `/context` to keep its existing `automatic compaction policy` compatibility heading while sourcing the budget lines from the shared runtime narrative helper instead of local string assembly
- changed `/status` memory lifecycle and active workflow rendering to reuse the same normalized runtime-budget, compact-lifecycle, and runtime-progress labels used by the shared session helper
- normalized TUI memory/runtime labels to the same vocabulary as `/status`, without redesigning the layout or adding new runtime event kinds
- kept structured `status_*` and `memory_*` fields backward compatible while aligning their lifecycle labels, `none` normalization, and runtime-progress summaries with the same shared narrative source
- stabilized recovery-compact wording across `/history`, `/rewind show`, `/status`, and memory lifecycle summaries around the same `recovery` lifecycle label and compact `prompt-too-long: ...` reason format

## Important Interface Changes

Public/user-facing behavior should evolve as follows:

- `/context` becomes clearer about runtime budget state and token-source provenance
- `/status` and memory surfaces expose whether compact behavior was manual, policy-driven, or recovery-driven
- `/history` becomes able to explain compact-retry recovery boundaries
- no new command family is required

Internal/runtime changes should include:

- one shared budget-decision representation
- query-loop-owned compact retry path
- richer `RuntimeEvent` vocabulary
- shared budget/compaction metadata reused by REPL/TUI/stdio/remote

## Test Plan

### Runtime / query loop

- provider usage present:
  - budget decisions use real usage
  - token source is marked `provider`
- provider usage absent:
  - budget decisions use estimated fallback
  - token source is marked `estimated`
- warning / compact-needed / hard-stop decisions are stable and reproducible
- prompt-too-long recovery compacts and retries exactly within the allowed retry cap
- repeated failure exits cleanly with a clear error and preserved history boundary trail

### State / transcript / memory lifecycle

- recovery compact writes a distinct history boundary
- manual vs auto vs recovery compact remain distinguishable
- rewind/history/status surfaces can describe recovery boundaries without ambiguity
- saved session restore preserves the post-recovery state correctly

### Events / approvals / tool lifecycle

- richer tool events are emitted in deterministic order
- approval-wait events appear before approval resolution
- parallel read-only tool batches emit consistent batch lifecycle events
- budget-pressure and compact-recovery events are visible to stdio/remote/TUI consumers

### Regression

- existing `/compact`, `/rewind`, `/history`, `/status`, `/context` commands do not regress
- background follow-up / handoff / progress surfaces continue to work
- plugin/skills/agents/status/workspace payloads remain compatible unless intentionally extended
- no new hosted-transport assumptions are introduced

## Assumptions

- provider usage remains the preferred source of truth, with local estimation as fallback
- the new budget state is a runtime mechanism first and an inspection surface second
- prompt-too-long recovery stays bounded and local; it will not attempt full upstream reactive-compact parity
- runtime event richness is intended to improve local workflow consumers, not to recreate upstream telemetry infrastructure
- any remaining gap after this work is broader upstream runtime/product infrastructure or product-shell breadth, not failure of the local runtime core
