# Status Surface Plan

This document started as the next-stage implementation plan for the remaining
`Config / model / status` parity gap after the history/rewind and local agents
lines reached a stable local-equivalent baseline.

For the current local scope, the plan below now serves primarily as:

- a completed implementation record for the unified session-status dashboard
- a reference for status vocabulary, structured payloads, runtime-health
  summaries, and status action cohesion
- a scope marker before any future move into broader upstream status/product
  breadth

It is intentionally scoped to the local coding-agent goal of this repo. The
target is not full hosted-product status parity. The target is a stronger local
session-control and session-inspection surface across:

- REPL
- TUI
- stdio
- remote session proxy

## Current Baseline

The current Python implementation already has substantial status-related pieces:

- `/config`
- `/model`
- `/status`
- `/status summary`
- `/status workflow`
- `/status resume`
- `/sessions show`
- `/history` / `/rewind` lifecycle metadata
- background handoff notifications
- runtime-aligned `/context` usage inspection
- working-set / focused-file / plan / task / changes workflow metadata
- plugin / skills / project-context reload-state inspection

Important current references:

- [PARITY_MATRIX.md](./PARITY_MATRIX.md)
- [MEMORY_REPRO_PLAN.md](./MEMORY_REPRO_PLAN.md)
- [AGENTS_REPRO_PLAN.md](./AGENTS_REPRO_PLAN.md)
- [HISTORY_REWIND_PLAN.md](./HISTORY_REWIND_PLAN.md)
- [claudecode_py/session.py](./claudecode_py/session.py)
- [claudecode_py/session_components/summary_surfaces.py](./claudecode_py/session_components/summary_surfaces.py)
- [claudecode_py/tui/state.py](./claudecode_py/tui/state.py)

## Current Read

The main gap is no longer missing data. The gap is that status-related metadata
is still spread across several command surfaces and lifecycle-specific views.

The remaining parity gap is mostly about:

1. turning `/status` into a real session operating dashboard
2. unifying config/model/memory/background/workspace vocabulary in one place
3. giving TUI and headless callers the same structured session-status story
4. making next actions from `/status` more stable and central

This means the next phase should focus on **session operating surface depth**,
not on adding more isolated inspection commands.

## Problem Statement

The current implementation has strong local inspection depth, but `/status`
still behaves more like a good summary page than the primary control and
orientation surface for a running coding session.

The remaining parity gap is mostly about:

- aggregation
- hierarchy
- clarity
- actionability
- shared structured status payloads

not about introducing new runtime subsystems.

## Non-Goals

This plan does not target:

- hosted account/commercial status
- rate-limit or billing product surfaces
- auth/login/product-distribution status
- marketplace/distribution shell breadth
- a second control plane separate from the existing session model

## Phase 1: Unified Session Status Vocabulary

Priority:
Highest

Status:
Completed for current local scope

Goal:
Normalize the language and top-level structure used by `/config`, `/model`,
`/status`, `/sessions show`, and TUI status surfaces.

Key work:

- define one shared vocabulary for:
  - active model
  - provider
  - session mode
  - context usage
  - memory lifecycle
  - background notifications
  - working set
  - focused file
  - active plan
  - active task
  - project-context reload health
- align heading names and field labels across:
  - `/status summary`
  - `/status workflow`
  - `/status resume`
  - `/config`
  - `/model`
  - `/sessions show`
  - TUI status blocks
- remove remaining cases where similar state is described with different labels
  depending on which surface the user is on
- establish one shared rendering contract for:
  - `session identity`
  - `execution state`
  - `memory lifecycle`
  - `background state`
  - `workspace state`

Exit criteria:

- users can move between status/config/model/session surfaces without
  re-learning terminology

## Phase 2: `/status` as Primary Session Dashboard

Priority:
Highest

Status:
Completed for current local scope

Goal:
Turn `/status` into the primary aggregated session operating surface rather
than a lighter summary companion.

Key work:

- give `/status` one stable top-level structure, such as:
  - session identity
  - model/provider
  - memory lifecycle
  - background notifications
  - workspace/working-set state
  - plan/task state
  - project-context health
  - next actions
- ensure `/status summary` and `/status workflow` are specializations of the
  same model, not separately assembled stories
- make `/status` explicitly answer:
  - what model/provider am I using?
  - how full is context and what is memory state?
  - are there background completions/failures I should react to?
  - what file/task/plan is currently in focus?
  - what changed recently?
  - what should I do next?
- make `/status` the recommended place to re-orient after:
  - resume
  - rewind
  - compact
  - clear session
  - background completion handoff

Exit criteria:

- `/status` is sufficient as the primary "where am I and what next?" surface
  for an active local session

## Phase 3: Structured Status Payload for Stdio / Remote

Priority:
High

Status:
Completed for current local scope

Goal:
Expose richer status detail as structured payloads instead of only textual
summaries.

Key work:

- add a structured session-status payload for stdio and remote callers
- ensure it covers:
  - model/provider metadata
  - memory lifecycle metadata
  - context usage summary
  - background notification summary
  - working-set summary
  - focused-file summary
  - task/plan summary
  - project-context reload summary
  - recommended next actions
- keep existing text output stable for compatibility
- do not force TUI or remote to parse text for status blocks when the data
  already exists structurally
- align field names with the current `memory_*` and `background_*` style where
  helpful, instead of inventing a second naming scheme

Suggested payload fields:

- `status_session_id`
- `status_provider`
- `status_model`
- `status_mode`
- `status_context_usage`
- `status_memory_summary`
- `status_background_summary`
- `status_working_set_summary`
- `status_focused_file_summary`
- `status_plan_summary`
- `status_task_summary`
- `status_project_context_summary`
- `status_next_actions`

Exit criteria:

- headless and remote callers can build a coherent status UI without scraping
  `/status` text

## Phase 4: TUI Session Dashboard Depth

Priority:
High

Status:
Completed for current local scope

Goal:
Upgrade the TUI status area from a set of informative blocks into a stronger
session dashboard.

Key work:

- reorganize TUI status blocks around the same hierarchy as `/status`
- ensure the TUI shows:
  - latest memory lifecycle state
  - current background notifications
  - current working-set/focused-file summary
  - active plan/task summary
  - project-context reload health
  - stable next actions
- tighten navigation between:
  - status
  - changes
  - task detail
  - active plan
  - history/rewind
  - background sessions
- prefer consuming the structured status payload from Phase 3 rather than
  rebuilding parallel status logic inside TUI

Current local scope delivered:

- TUI status rendering now follows the same top-level hierarchy as `/status`
- the status panel consumes `status_*` structured metadata from `Session`
- dashboard sections surface memory lifecycle, background notifications,
  workspace state, active workflow, and project-context health in one place
- existing bindings remain unchanged while labels and grouping align with the
  new status vocabulary

Exit criteria:

- TUI status can act as a real session home surface rather than only a compact
  metadata sidebar

## Phase 5: Project-Context and Runtime Health Integration

Priority:
Medium

Status:
Completed for current local scope

Goal:
Make `/status` the place where project-context and runtime health issues surface
first, instead of forcing users to visit many separate commands.

Key work:

- fold in compact summaries for:
  - latest project-context reload result
  - memory/skills/plugin refresh health
  - MCP connectivity/refresh health
  - permission mode summary
  - workspace isolation/health anomalies when present
- ensure these summaries remain compact and actionable, not verbose dumps
- surface only the latest/highest-signal issue by default, with links/actions to
  drill down into:
  - `/project-context`
  - `/plugins`
  - `/skills`
  - `/mcp`
  - `/permissions`
  - `/workspaces`

Current local scope delivered:

- `/status` now folds in compact runtime-health summaries for project-context
  reloads, skills/plugins state, MCP connectivity, permission mode, and
  workspace anomalies
- `status_*` structured payloads now include the same runtime-health fields for
  stdio, remote, and TUI consumers
- TUI status dashboard surfaces the same compact project-context/runtime-health
  lines as the REPL status views
- next actions now explicitly point to `/project-context`, `/plugins`, `/skills`,
  `/mcp`, `/permissions`, and `/workspaces current` as health drill-downs

Exit criteria:

- `/status` becomes the first place users look for local runtime health, not
  just conversational state

## Phase 6: Status Action Cohesion and Final Polish

Priority:
Medium

Status:
Completed for current local scope

Goal:
Make next actions across all status-related surfaces stable and central.

Key work:

- standardize action groups emitted from:
  - `/status`
  - `/status workflow`
  - `/status resume`
  - `/sessions show`
  - TUI status dashboard
- ensure they converge on a common set of action families:
  - go to focused file
  - inspect changes
  - inspect task
  - inspect active plan
  - inspect history/rewind
  - inspect background handoff
  - inspect project-context health
- reduce duplicated action assembly logic between status and other surfaces
- make `/status` the safest recovery point after major lifecycle transitions

Current local scope delivered:

- status-related surfaces now share a common action-family model for focused
  file, changes, tasks, active plan, history/rewind, background handoff, and
  project-context/runtime health
- `/status`, `/status workflow`, `/status resume`, `/sessions show`, stdio,
  remote, and the TUI status dashboard all consume the same status action
  vocabulary
- structured `status_action_groups` now ship with the status payload so
  headless and remote consumers no longer need to scrape the text panel to find
  stable recovery routes
- saved-session detail and workspace surfaces now align with the same action
  families instead of using ad hoc resume-only next-action wording

Exit criteria:

- status surfaces provide stable action routing and feel like the central
  recovery/orientation point for the session

## Recommended Delivery Order

The recommended order is:

1. Phase 1: Unified Session Status Vocabulary
2. Phase 2: `/status` as Primary Session Dashboard
3. Phase 3: Structured Status Payload for Stdio / Remote
4. Phase 4: TUI Session Dashboard Depth
5. Phase 5: Project-Context and Runtime Health Integration
6. Phase 6: Status Action Cohesion and Final Polish

This order is intentional:

- first align language
- then strengthen the textual dashboard
- then expose the same model structurally
- then deepen TUI on top of the structured model
- then fold in more runtime health
- only after that do final cohesion and polish

## Test Strategy

### REPL / Session tests

- `/status`, `/status summary`, `/status workflow`, and `/status resume`
  share aligned vocabulary and stable section ordering
- `/config` and `/model` remain compatible while aligning with the new status
  terminology
- latest background handoff, memory lifecycle, working-set, plan, and task
  summaries appear in the expected status blocks

### Stdio / Remote tests

- structured status payloads are returned alongside text
- remote proxy syncs the same status fields without textual scraping
- existing callers remain compatible with additive fields

### TUI tests

- status panel reflects the same structured status hierarchy
- navigation/actions from status remain stable after rewind/resume/background
  completion
- memory/background/project-context sections stay synchronized with structured
  payloads

### Regression

- no regressions to existing `/config`, `/model`, `/history`, `/sessions`,
  `/rewind`, `/project-context`, `/plugins`, `/skills`, or background surfaces
- existing saved sessions and transcripts remain compatible
- action labels stay consistent with current workflow routing unless explicitly
  upgraded in a coordinated way

## Decision Rule

If there is a choice between:

- improving `/status` as the central local session dashboard

and:

- adding more peripheral product-style inspection breadth

prefer the central status-dashboard path.

That remains the intended scope for this repo.
