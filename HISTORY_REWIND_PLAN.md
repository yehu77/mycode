# History / Rewind UX Plan

This document started as the next-stage implementation plan for the remaining
history-navigation and rewind UX gap after the broader memory workflow work
reached a stable local-equivalent baseline.

For the current local scope, the plan below now serves primarily as:

- a completed implementation record for local rewind/history UX
- a reference for boundary browse, preview, lineage, and TUI rewind selection
- a scope marker before any future move into broader upstream history/product
  UX beyond the local workflow

It is intentionally scoped to the local coding-agent goal of this repo. The
target is not full hosted-product parity. The target is stronger local parity
for:

- rewindable conversation browsing
- explicit compact/reset/resume boundaries
- predictable restore semantics
- shared history lifecycle language across REPL, TUI, stdio, and remote

## Current Baseline

The current Python implementation already has the main rewind/memory building
blocks:

- `HistoryBoundary` persistence in session state and transcripts
- compact / fresh-session-reset / resume / rewind boundary recording
- `/rewind` list/show/apply
- `/history` boundary rows and rewind guidance
- unified memory-operation metadata across REPL, stdio, remote, and TUI
- scoped workflow-surface preservation/clearing rules after compact/rewind/reset

Important current references:

- [MEMORY_REPRO_PLAN.md](./MEMORY_REPRO_PLAN.md)
- [PARITY_MATRIX.md](./PARITY_MATRIX.md)
- [claudecode_py/session.py](./claudecode_py/session.py)
- [claudecode_py/session_components/change_history_views.py](./claudecode_py/session_components/change_history_views.py)
- [claudecode_py/session_components/summary_surfaces.py](./claudecode_py/session_components/summary_surfaces.py)
- [claudecode_py/tui/state.py](./claudecode_py/tui/state.py)

## Current Read

The gap is no longer "can we rewind?" The gap is now:

1. how well users can browse and compare history boundaries
2. how clearly the system explains what a rewind/compact/clear/resume boundary means
3. how consistent history lifecycle semantics are across all surfaces
4. whether TUI can make rewind operations as navigable as REPL/headless text

This means the next phase should focus on **history UX depth**, not on deeper
compact internals.

## Problem Statement

The current implementation has usable rewind mechanics, but it still behaves
more like a good local command surface than a fully coherent history-navigation
workflow.

The remaining parity gap is mostly about:

- browsing
- previewing
- understanding lifecycle transitions
- selecting a boundary in richer UI surfaces

not about introducing more memory-compaction machinery.

## Non-Goals

This plan does not target:

- reactive compact internals
- microcompact or hosted session-memory daemons
- full upstream hook buses
- hosted memory-file editing UX
- expanding into account/product/history cloud surfaces

## Phase 1: Boundary Browse and Preview

Priority:
Highest

Status:
Completed for current local scope

Goal:
Turn rewindable boundaries into a richer browse/preview surface before adding
more transport or UI breadth.

Key work:

- strengthen `/history messages` so boundaries are not only listed inline but
  also grouped or visually separated by lifecycle kind
- add a clearer boundary preview path through `/rewind show`
- make boundary preview explicitly include:
  - boundary kind
  - trigger
  - created time
  - message counts before/after
  - context-summary chars before/after
  - snapshot availability
  - target session id / target boundary id when relevant
- add a compact "restore effect" block:
  - restored messages
  - restored `context_summary`
  - workflow surfaces cleared/preserved
- add clearer "apply from here" guidance from `/history` into `/rewind show` and
  `/rewind apply`

Exit criteria:

- `/rewind show` is enough to decide whether a rewind should be applied
- `/history` makes boundaries feel like meaningful restore points, not just log rows

## Phase 2: Unified History Lifecycle Narrative

Priority:
High

Status:
Completed for current local scope

Goal:
Make compact / rewind / clear history / clear session / resume read as one
coherent lifecycle across all textual surfaces.

Key work:

- standardize language for these operations across:
  - `/history`
  - `/status summary`
  - `/status workflow`
  - `/status resume`
  - `/sessions show`
- define and reuse one consistent vocabulary:
  - `compact boundary`
  - `rewind boundary`
  - `fresh session reset`
  - `saved resume boundary`
  - `rewindable boundary`
  - `restore effect`
- ensure latest-boundary and latest-rewindable-boundary summaries are phrased
  consistently everywhere
- make `/sessions show` and saved-session detail better expose:
  - latest boundary kind
  - latest rewindable kind
  - latest restore/compact reason
  - whether the session currently has rewindable history
- remove remaining cases where one surface says "cleared", another says
  "fresh", and another says "reset" for the same effect unless the distinction
  is intentional

Exit criteria:

- a user can move between `/history`, `/status`, and `/sessions show` without
  re-learning the history terminology

## Phase 3: TUI History / Rewind Selection Surface

Priority:
High

Status:
Completed for current local scope

Goal:
Make rewind selection navigable in TUI rather than only text-driven through
commands.

Key work:

- add a TUI-local history boundary panel or status-adjacent selector surface
- expose:
  - rewindable boundary count
  - selected boundary id
  - selected boundary kind
  - selected boundary summary
  - selected boundary preview actions
- support keyboard navigation across rewindable boundaries
- support TUI actions for:
  - preview selected boundary
  - apply selected boundary
  - open related transcript/history detail
- keep selection behavior aligned with current structured metadata from
  `memory_surface_payload()`
- do not invent a second history model for TUI; it must consume the same
  boundary metadata that REPL/headless already use

Exit criteria:

- TUI users can browse and apply rewind boundaries without dropping to command-only flow

## Phase 4: Structured Boundary Detail for Stdio / Remote

Priority:
Medium

Status:
Completed for current local scope

Goal:
Expose richer rewind/history detail as structured payloads instead of only text.

Key work:

- add boundary-detail payloads for stdio `session.action(describe_rewind, ...)`
- include structured fields for selected boundary preview, not only `text`
- add remote-proxy synchronization for the same boundary preview metadata
- preserve existing text contracts for compatibility while adding richer fields
- ensure TUI can consume this structured data instead of parsing text if useful

Suggested payload fields:

- `boundary_id`
- `boundary_kind`
- `trigger`
- `created_at`
- `selector`
- `message_count_before`
- `message_count_after`
- `context_summary_chars_before`
- `context_summary_chars_after`
- `snapshot_message_count`
- `restore_effect_summary`
- `workflow_surface_policy`
- `apply_action`

Exit criteria:

- headless and remote callers can build rewind UI without scraping text

## Phase 5: History Comparison and Lineage Polish

Priority:
Medium

Status:
Completed for current local scope

Goal:
Make rewind boundaries feel more like a navigable timeline than isolated points.

Key work:

- show local lineage between:
  - compact
  - rewind
  - clear session
  - resume
- make it clearer which rewind boundaries are descendants of compacted history
- add lightweight compare-oriented summary:
  - "this boundary restored X messages and Y summary chars relative to current"
  - "this rewind targets pre-compact state" / "post-resume state"
- optionally add one compare-oriented `/rewind show` subsection rather than a
  new command

Exit criteria:

- the user can understand how a candidate rewind point sits in the broader session timeline

## Recommended Delivery Order

The recommended order is:

1. Phase 1: Boundary Browse and Preview
2. Phase 2: Unified History Lifecycle Narrative
3. Phase 3: TUI History / Rewind Selection Surface
4. Phase 4: Structured Boundary Detail for Stdio / Remote
5. Phase 5: History Comparison and Lineage Polish

This order is intentional:

- first improve decision quality before rewind apply
- then normalize language across existing surfaces
- then make the richer model usable in TUI
- then expose richer structured payloads
- only after that spend time on compare/lineage polish

## Test Strategy

### REPL / Session tests

- `/history` renders richer boundary browse sections
- `/rewind show <selector>` includes restore-effect details
- `/rewind apply <selector>` still preserves/clears workflow state according to existing rules
- `/status` and `/sessions show` use the same lifecycle terminology

### TUI tests

- history boundary selection state is stable
- selected boundary preview is rendered
- applying a selected boundary updates memory metadata correctly
- keyboard navigation preserves selection order and action availability

### Stdio / Remote tests

- `describe_rewind` returns richer structured detail in addition to text
- remote proxy syncs selected boundary metadata correctly
- no regressions in existing `rewind_show_action` / `rewind_apply_action` fields

### Regression

- compact / rewind / clear / resume persistence remains compatible with existing transcripts
- existing `/rewind` CLI syntax remains unchanged
- current memory metadata keys remain available unless explicitly superseded by additive fields

## Decision Rule

If there is a choice between:

- richer local history-navigation clarity

and:

- broader product-shell or hosted history features

prefer the local history-navigation path.

That remains the intended scope for this repo.
