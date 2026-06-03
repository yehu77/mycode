# Agents Reproduction Plan

This document started as the working implementation plan for the next
local-workflow parity line after the memory workflow.

For the current local scope, the plan below is now substantially completed. It
should be read as:

- a record of what the local background-agent milestone covered
- a reference for what is already implemented
- a boundary marker before any future move into broader multi-source,
  teammate/swarm, or hosted agent-product work

It is intentionally scoped to the Python reimplementation goal of this repo:

- strong local background-session workflow
- coherent attach/resume/logs/task inspection
- shared metadata across REPL, TUI, stdio, and remote surfaces

It is not a commitment to reproduce the entire upstream hosted agent product shell.

## Goal

For the completed local scope, the goal was to close the most valuable
remaining parity gap in the `Background agents / agents UI` line by turning the
current detached-session support into a more unified agent workflow surface.

In practical terms, that means improving:

- background session inspection
- continuation guidance
- attach/resume semantics
- task/background linkage
- structured metadata parity across local surfaces

without drifting into marketplace/product/UI breadth that is out of scope for
this repo.

That local-scope goal is now complete; future work on agents should be planned
from the remaining-agent backlog below, not by reopening the completed phases
in this file.

## Remaining-Agent Backlog Beyond This Plan

After the completed local background-agent scope, the main remaining parity
gaps are no longer about detached-session basics. They are mostly about broader
agent-definition breadth or upstream teammate/product layers.

Current read:

- builtin + project-local agent definitions are implemented, with real source
  grouping, diagnostics, and same-name shadowing
- local background follow-up, handoff, runtime progress, and status/TUI/remote
  parity are implemented for the current scope
- the next worthwhile agent step only appears if the repo wants to go beyond
  the current builtin + project-local definition model or expand into
  teammate/swarm territory

Recommended backlog ordering if agents are revisited:

1. multi-source agent definitions beyond builtin + project-local
   - add user/global definitions only if the repo wants a third source class
   - then add richer precedence and per-source resolution views
2. product-grade agent progress presentation
   - denser progress-line UX on top of the existing runtime metadata
3. coordinator / teammate panel UX
   - only if the runtime expands beyond background sessions
4. teammate/swarm execution model
   - separate scope expansion, not a small continuation of the local plan

Still not recommended for the current scope:

- hosted session-ingress / transport breadth
- teammate approval / shutdown lifecycle before teammate runtime exists
- full create/edit agent-management product shell

## Upstream Reference Points

The upstream source tree has two different "agents" layers. They should not be treated as equally important for this repo.

### Agent Definition / Menu / Config Layer

These files are mostly about configured agents, menus, and creation/editing flows:

- `package/src-extracted/src/commands/agents/agents.tsx`
- `package/src-extracted/src/cli/handlers/agents.ts`
- `package/src-extracted/src/components/agents/*`
- `package/src-extracted/src/tools/AgentTool/*`

What they cover:

- agent definitions and sources
- model/tool/memory configuration
- override resolution
- menu-driven viewing and editing
- richer product-facing agent UX

This layer is useful as reference, but it is **not** the first parity target for the Python port.

### Background Session / Attach / Lifecycle Layer

These files are closer to the Python repo's current local-workflow goal:

- `package/src-extracted/src/utils/background/remote/remoteSession.ts`
- `package/src-extracted/src/services/api/sessionIngress.ts`
- `package/src-extracted/src/bridge/sessionRunner.ts`
- `package/src-extracted/src/commands/session/*`
- `package/src-extracted/src/utils/sessionRestore.ts`
- `package/src-extracted/src/utils/sessionStorage.ts`
- `package/src-extracted/src/services/AgentSummary/agentSummary.ts`

What they cover:

- live background work
- attach and reconnect behavior
- saved resume vs live attach distinctions
- background lifecycle and inspection
- background summaries
- state continuity across session boundaries

This layer is the main reference target for the next Python milestone.

## Current Python Capability

The Python implementation already has a meaningful detached-session foundation.

### CLI / Entry Surface

Primary file:

- `python_claudecode/claudecode_py/cli.py`

Current commands:

- `ask --background`
- `ps`
- `ps <session>`
- `logs <session> [summary|tail]`
- `attach <session> [repl|tui]`
- `kill <session>`
- `serve-stdio`
- `serve-bridge`

### Background Session Persistence

Primary file:

- `python_claudecode/claudecode_py/storage/background_sessions.py`

Current persisted fields already include:

- background id
- session id
- prompt
- provider/model
- status
- pid
- transcript path
- log path
- workspace mode / health / cleanup / fallback
- execution contract metadata
- bridge endpoint metadata

### Continuation Semantics

Primary file:

- `python_claudecode/claudecode_py/cli.py`

Current behavior already distinguishes:

- `live attachable`
- `saved resumable`
- `inactive only`

and renders grouped actions such as:

- `go_to_live_attach`
- `go_to_saved_resume`
- `stay_on_surface`

### Session-Local Task Surfaces

Primary file:

- `python_claudecode/claudecode_py/tools/task_tools.py`

Current task tools:

- `task_list`
- `task_get`
- `task_stop`
- `task_wait`

These are useful, but they are not yet fully unified with background-session inspection.

## Most Valuable Remaining Gaps

The current gap is not "missing background support." The gap is that background execution is still represented by several adjacent but not fully unified surfaces.

### Gap A: Background Sessions Are Still Registry-Centric

`ps` and `logs` are currently much closer to:

- registry inspection
- status inspection
- continuation hint rendering

than to the richer workflow surfaces used for:

- `Status`
- `Changes`
- `Task Detail`
- `Active Plan`

What is missing:

- workflow-oriented summary of background work
- clearer "what should I do next?" behavior
- stronger relation to tasks/plan/changes/files

### Gap B: Background Sessions and Tasks Are Parallel Surfaces

Right now there are two adjacent models:

- detached background sessions
- in-session task manager tasks

They are both useful, but they are not yet one coherent execution model.

Symptoms:

- `ps` answers "what background session exists?"
- `task_list` answers "what tasks exist?"
- `logs` answers "what got logged?"
- `task_get` answers "what task output exists?"

This separation is acceptable as an internal model, but it is still too fragmented as a user workflow.

### Gap C: Continuation Category Exists, but Continuation Workflow Is Still Thin

The categories:

- `live attachable`
- `saved resumable`
- `inactive only`

are already in place, but the actions after classification are still shallower than the main session workflow.

What is still missing:

- stronger attach-first detail behavior for live sessions
- stronger inspect-first behavior for inactive ones
- explicit background-origin summary after saved resume
- more stable action groups after attach/resume/logs transitions

### Gap D: Background Metadata Is Not Yet a First-Class Shared Schema

The memory line already has a shared metadata story across:

- REPL
- TUI
- stdio
- remote proxy

The background/agent line does not yet have the same level of structured parity.

This limits:

- TUI integration
- stdio inspection richness
- remote-session parity
- future workflow consistency

## Recommended Direction

The next implementation line should focus on **background session lifecycle and workflow parity**, not on upstream agent-creation UI breadth.

That means:

- do not prioritize `components/agents/*` reproduction
- do not prioritize full upstream agent-definition editing flows
- do prioritize background execution inspection and continuity

## Phased Implementation Plan

## Phase 1: Background Session Metadata Foundation

Priority:
High

Goal:
Turn background sessions from simple registry entries into a stable structured metadata source for downstream workflow surfaces.

Status:
Foundation complete on the CLI side.

Current implementation snapshot:

- shared derived metadata payload now lives in a reusable module instead of `cli.py`
- `ps`, `ps <id>`, and `logs <id> summary` now read continuation/source/action fields from the same payload
- transcript-derived fields are now surfaced when available:
  - `background_last_known_message_count`
  - `background_last_known_context_summary_chars`
  - `background_task_surface_counts`
  - `background_has_active_plan`

Remaining Phase 1 work:

- expand the shared payload to stdio/remote/TUI surfaces when Phase 2 or Phase 4 needs it

Target:

- define one shared background-session metadata shape
- render `ps` list/detail and `logs summary` from that shape

Implementation:

1. Add a derived background metadata payload
   - likely on the CLI/background-session side first
   - can be partially derived from the existing registry record

2. Include at least:
   - `background_session_source`
   - `background_continuation_category`
   - `background_live_attachable`
   - `background_saved_resumable`
   - `background_inactive_only`
   - `background_primary_action`
   - `background_secondary_action`
   - `background_stay_on_surface`
   - `background_last_known_message_count`
   - `background_last_known_context_summary_chars`
   - `background_task_surface_counts`
   - `background_has_active_plan`
   - `background_workspace_health`

3. Make `ps` list/detail and `logs summary` read from this shared payload instead of each one assembling ad hoc status bits.

Exit criteria:

- `ps`
- `ps <id>`
- `logs <id> summary`

all tell the same continuation story for the same session.

Tests:

- list/detail/summary render the same continuation category for the same background session
- live attachable branch is covered
- saved resumable branch is covered
- inactive only branch is covered

## Phase 2: Background Workflow Surface

Priority:
High

Goal:
Make background session inspection look and behave more like the main local workflow surfaces.

Status:
Completed for the current local background workflow scope.

Current implementation snapshot:

- `ps <id>` and `logs <id> summary` now render a dedicated `background workflow` block
- the block now includes:
  - current workflow summary
  - task surface counts
  - active plan summary
  - recent change summary
  - working-set / focused-file summary when transcript state provides it
  - grouped next actions with live/saved/inactive ordering
- action groups now include:
  - `go_to_live_attach`
  - `go_to_saved_resume`
  - `go_to_logs`
  - `go_to_sessions_show`
  - `go_to_history`
  - `stay_on_surface`

Progress notes:

- the workflow payload now carries shared background metadata into CLI, stdio, remote, and TUI
- workflow blocks now also expose recent activity, progress summary, and completion state

Target:

- `ps <id>` detail
- `logs <id> summary`

should render a workflow-oriented summary, not only status fields.

Implementation:

1. Add a workflow block for background session detail that can include:
   - current workflow summary
   - task surface counts
   - active plan summary
   - recent change summary
   - working-set or focused-file summary when available
   - grouped next actions

2. Standardize next-action groups for background sessions:
   - `go_to_live_attach`
   - `go_to_saved_resume`
   - `go_to_logs`
   - `go_to_sessions_show`
   - `go_to_history`
   - `stay_on_surface`

3. Reuse the existing workflow/action-group rendering style where practical.

Exit criteria:

- live background detail is attach-first
- saved-resumable detail is resume-first
- inactive-only detail is inspect-first

Tests:

- `ps <id>` detail for a live session shows attach-first action order
- saved-resumable detail shows resume-first action order
- inactive-only detail points to logs/session inspection instead of attach

## Phase 3: Task / Background Unification

Priority:
High

Goal:
Reduce the split between background sessions and task surfaces.

Status:
Completed for the current local task/background scope.

Current implementation snapshot:

- background workflow payload now derives task linkage from `saved_task_records`
- background detail can now expose:
  - `background_execution_count`
  - `active_plan_execution_count`
  - `primary_task`
  - `go_to_task`
- task tool output now prefers explicit `background_session_id` linkage when available
- `/task show` now exposes the same background reverse hint block for session-native task surfaces
- `task advisor`, `task drift`, and `tasks active/changes/context` workflow entries now expose the same background linkage hints

Progress notes:

- explicit `bg_id` linkage now flows into task metadata
- `/task show`, `task advisor`, `task drift`, and task listings now expose the same background linkage hints

Implementation:

1. Add background session -> task summary linkage:
   - primary task id
   - task surface counts
   - progress summary
   - background execution count

2. Add task -> background linkage:
   - background session id
   - continuation category
   - attach action
   - resume action

3. Where a background task belongs to active plan execution, expose that relationship:
   - `go_to_plan`
   - `go_to_task`
   - background-aware execution summary

Exit criteria:

- background detail can show task-oriented execution state
- task detail can trace back to the owning background session when applicable

Tests:

- background session detail shows primary task id when available
- task detail shows background linkage when applicable
- active plan execution tasks do not regress in current workflow rendering

## Phase 4: TUI / Stdio / Remote Metadata Parity

Status:
Completed for the current local-background scope.

Priority:
Medium

Goal:
Promote background-agent state to the same metadata-parity level already achieved for memory surfaces.

Implementation:

1. Expose shared background metadata through stdio/session describe and open-session listing.

2. Sync the same metadata through remote session proxies.

3. Add a background workflow block to TUI status or a related panel.

4. Normalize names across surfaces, for example:
   - `background_session_id`
   - `background_continuation_category`
   - `background_primary_action`
   - `background_resume_action`
   - `background_attach_action`
   - `background_task_surface_counts`
   - `background_has_active_plan`

Exit criteria:

- REPL
- TUI
- stdio
- remote

all present the same background continuation story.

Tests:

- stdio and remote reflect the same continuation category for a given session
- attach after live background work refreshes metadata correctly
- clear/stop/end transitions refresh background metadata correctly

Progress notes:

- `Session.background_surface_payload()` now exposes a shared live background payload
- `session.create` / `session.describe` / `session.list_open` now include background metadata
- `RemoteSessionProxy` now syncs and exposes `background_surface_payload()`
- TUI status now renders a `Background` block from the same structured metadata

## Next-Stage Backlog

After Phase 4, the highest-value remaining work is no longer generic background metadata.
The remaining parity gap is mostly in upstream agent lifecycle and inspection UX.

The recommended order is:

1. live agent steering
2. live progress / summarization inspection
3. completion / notification lifecycle
4. `/agents` definition inspection

### 1. Live Agent Steering

Priority:
Highest

Status:
Completed for the current local steering scope.

Why:

- upstream has a steerable background-agent panel rather than only registry/detail views
- upstream supports keyboard-driven selection, view switching, and action dispatch for running agents
- this is the most direct remaining gap in day-to-day local coding workflow

Upstream reference points:

- `src/components/CoordinatorAgentStatus.tsx`
- `src/hooks/useBackgroundTaskNavigation.ts`
- `src/tasks/LocalAgentTask/LocalAgentTask.tsx`
- `src/tasks/InProcessTeammateTask/InProcessTeammateTask.tsx`

Current local gap:

- we can `attach`, inspect `ps`, inspect `logs`, and stop a background session
- we do not yet have a true "live steering" surface for a running agent without fully reattaching

Recommended scope:

- add a background-agent steering surface in REPL/TUI terms
- allow deterministic next actions for:
  - view transcript snapshot
  - open logs
  - attach
  - stop / dismiss
  - return to main session
- if the bridge contract is already sufficient, evaluate a minimal follow-up / queued-message path for live background sessions

Current progress:

- first slice landed as a shared `background_registry_payload()` for the current workspace
- stdio / remote / TUI status can now expose recent background sessions plus selected steering actions without requiring full attach
- TUI now supports background-session selection plus primary/secondary/logs action dispatch
- attach and saved-resume steering paths can now switch the active TUI session without leaving the app

Exit criteria:

- running background agents can be inspected and navigated as active workflow items
- the user does not need to jump directly from `ps` to full attach just to understand current live state

### 2. Live Progress / Summarization Inspection

Priority:
High

Status:
Completed for the current derived-metadata scope.

Why:

- upstream exposes richer progress lines for local agents
- upstream periodically summarizes agent progress instead of relying only on transcript snapshots
- this is the most useful inspection-depth gap after steering

Upstream reference points:

- `src/services/AgentSummary/agentSummary.ts`
- `src/components/AgentProgressLine.tsx`
- `src/tasks/LocalAgentTask/LocalAgentTask.tsx`

Current local gap:

- background workflow surfaces show a good static summary
- they do not yet expose an upstream-like progress line with recent activity, tokens, tool-use count, and compact live summary

Current progress:

- shared background payloads now expose:
  - `background_progress_summary`
  - `background_recent_activity`
  - `background_last_tool`
  - `background_tool_use_count`
- these fields are derived from transcript state, runtime task snapshots, and recent change/tool records

Recommended scope:

- add structured fields for:
  - `background_progress_summary`
  - `background_recent_activity`
  - `background_last_tool`
  - `background_tool_use_count`
  - `background_token_count`
- decide whether to derive these from existing runtime events first, or add a lightweight periodic summarizer second

Exit criteria:

- background surfaces can tell the user what the agent is doing now, not just what session it belongs to

### 3. Completion / Notification Lifecycle

Priority:
High

Status:
Completed for the current local inspection/handoff scope.

Why:

- upstream treats agent completion/failure as part of the main lifecycle, not just log termination
- local parity still underserves "what happened when the agent finished?"

Upstream reference points:

- `src/tasks/LocalAgentTask/LocalAgentTask.tsx`
- task notification / queued notification flows in query/task infrastructure

Current local gap:

- we have status changes and logs
- we do not yet surface compact completion/failure summaries and handoff-friendly next actions as coherently as upstream

Current progress:

- shared background payloads now expose:
  - `background_completion_state`
  - `background_completion_summary`
  - `background_failure_reason`
  - `background_result_pointer`
  - `background_transcript_pointer`
- CLI, stdio, remote, and TUI now surface the same completion summary fields

Recommended scope:

- add structured completion metadata:
  - `background_completion_summary`
  - `background_failure_summary`
  - `background_result_pointer`
  - `background_transcript_pointer`
- ensure the main session can surface these as follow-up-friendly notifications or summary lines

Exit criteria:

- completed and failed background agents leave behind a coherent inspection and handoff trail

### 4. `/agents` Definition Inspection

Priority:
Medium

Status:
Completed for the initial lightweight inspection scope.

Why:

- upstream does expose configured-agent inspection surfaces
- but this is less central than lifecycle and live inspection for the current repo goal

Upstream reference points:

- `src/cli/handlers/agents.ts`
- `src/commands/agents/agents.tsx`

Current local gap:

- we do not yet have a direct local equivalent for inspecting configured/custom agent definitions, source grouping, override behavior, and memory/model declarations

Current progress:

- a lightweight `/agents` / `pyclaude agents` inspection surface now lists the current local built-in agent modes
- stdio `session.view(view=\"agents\")` now exposes the same definition summary text

Recommended scope:

- add a lightweight local `/agents` or `agents` inspection surface
- show:
  - active agent definitions
  - source grouping
  - override/shadowing state
  - model / memory summary

Exit criteria:

- agent-definition inspection exists without dragging the repo into full upstream product-menu reproduction

## Updated Delivery Order

1. Phase 1
2. Phase 2
3. Phase 3
4. Phase 4
5. Live agent steering
6. Live progress / summarization inspection
7. Completion / notification lifecycle
8. `/agents` definition inspection

## Explicit Deferrals

These should not be part of the first implementation wave for this plan:

- upstream `components/agents/*` reproduction
- full agent creation / editing wizard parity
- marketplace/discovery agent UX
- Anthropic-specific swarm / team-memory product flows
- hosted remote transport breadth beyond current local bridge/stdio support

This order still matters.

Why:

- metadata first prevented surface drift
- CLI/detail workflow stabilized before TUI/remote parity work
- lifecycle and inspection work now build on a shared background payload instead of forking per surface

## Testing Strategy

Each phase should land with explicit tests.

### Core tests

- background continuation category classification remains stable
- attachable vs resumable vs inactive behavior is rendered consistently
- background session detail exposes the same semantics as logs summary

### Workflow tests

- background detail next-actions are deterministic
- background sessions connect coherently to task/plan/status inspection
- task surfaces expose background-origin metadata where relevant

### Service / Remote tests

- stdio describe/list returns structured background metadata
- remote proxy refreshes category/actions after attach/resume/stop
- no stale session-id or stale continuation-state behavior after lifecycle transitions

## Decision Rule

If a future implementation choice trades off:

- broader agent-product UI parity

against:

- deeper local background workflow coherence

prefer the local workflow path.

That is the intended scope for this repo.

## Post-Phase Backlog

The current four phases and first lifecycle backlog are complete for the local background-session scope.
The next work should stay on the same local-workflow path instead of branching into full upstream product-shell breadth.

The recommended next order is:

1. live follow-up / queued-message steering
2. main-session handoff / notification surface
3. runtime-grade progress metadata
4. deeper `/agents` definition inspection

### 1. Live Follow-Up / Queued-Message Steering

Priority:
Highest

Status:
Completed for the current structured-action local scope.

Why:

- current background-agent surfaces are now inspectable and steerable for attach/resume/logs/stop
- the most direct remaining local parity gap is still "can I steer a live background agent without fully attaching?"
- this is the clearest transition from observable background work to controllable background work

Recommended scope:

- add structured background follow-up actions through stdio / remote first
- support:
  - `send_followup`
  - `queue_message`
  - `cancel_pending_followup`
- let TUI background selection trigger these actions directly
- keep CLI expansion minimal or defer it

Current progress:

- background follow-up actions now exist as shared workspace-scoped session actions:
  - `background_send_followup`
  - `background_queue_message`
  - `background_cancel_pending_followup`
- pending queued follow-ups are now persisted on background-session records
- stdio / remote / TUI now expose and consume the same follow-up metadata and actions
- TUI background selection now supports queued follow-up input without requiring full attach

Exit criteria:

- a live background session can receive a follow-up prompt without requiring full attach
- queued follow-ups can be inspected and canceled through the same shared background metadata story

### 2. Main-Session Handoff / Notification Surface

Priority:
High

Status:
Completed for the current main-session notification scope.

Why:

- background completion is already inspectable from background-facing surfaces
- the next gap is that the main session still does not naturally surface those completions/failures

Recommended scope:

- surface recent background completion/failure in `/status` and related workflow summaries
- add recent background notifications to TUI status
- expose recent background handoff summaries in stdio / session.describe
- standardize next actions around:
  - inspect transcript
  - inspect task
  - inspect changes
  - resume saved session

Current progress:

- shared `background_handoff` metadata now derives recent completed/failed/cancelled background sessions
- `/status` summary and workflow now surface recent background notifications and latest handoff summary
- stdio `session.create` / `session.describe` / `session.list_open` now include the same handoff metadata
- remote session proxies now sync the same handoff payload
- TUI status now renders a `Background Notifications` block with transcript/task/changes/resume actions

Exit criteria:

- a user can notice and inspect recent background outcomes from the main workflow without first going through `ps` or `logs`

### 3. Runtime-Grade Progress Metadata

Priority:
Medium

Status:
Completed for the current runtime-line local scope.

Why:

- current progress fields are useful but still mostly transcript-derived
- upstream parity is closer to a runtime progress line than to a snapshot-only summary

Recommended scope:

- add:
  - `background_token_count`
  - more stable `background_recent_activity`
  - richer `background_last_tool_input` / `background_last_tool_summary`
- prefer deriving from existing runtime events before adding any periodic summarizer

Current progress:

- provider/runtime schemas now carry optional token usage, with provider totals preferred and stable estimated fallback when usage is unavailable
- background task sinks now maintain a runtime progress snapshot on task metadata instead of relying only on transcript-derived summaries
- live and saved background surfaces now expose:
  - `background_token_count`
  - `background_token_count_source`
  - `background_recent_activity`
  - `background_recent_activity_kind`
  - `background_last_tool_input`
  - `background_last_tool_summary`
  - `background_progress_updated_at`
- stdio / remote / TUI status now consume the same richer progress payload
- the fallback strategy is now explicit: provider usage first, estimated runtime-line tokens otherwise

Exit criteria:

- progress surfaces answer "what is the agent doing now?" with stronger runtime fidelity than transcript snapshots alone

### 4. Deeper `/agents` Definition Inspection

Priority:
Medium

Status:
Completed for the current builtin-definition local scope.

Why:

- a lightweight `/agents` surface now exists
- the remaining value is in multi-source definitions and override visibility, not in a full menu product shell

Recommended scope:

- only continue if the repo starts supporting real multi-source agent definitions
- add:
  - source grouping
  - override / shadowing state
  - model override visibility
  - memory / skills summary by source

Current progress:

- `/agents`, `pyclaude agents`, and `session.view(view="agents")` now render a grouped definition inspection surface instead of a flat builtin list
- project-local definitions are now loaded from `.pyclaude/agents/*.json` and merged with builtin definitions
- the surface now includes:
  - source summaries
  - effective-definition summaries
  - override / shadowing resolution text
  - model override visibility
  - memory / skills summaries by source
- builtin and project-local sources now participate in real same-name shadowing
- invalid project-local definitions now surface as diagnostics in `/agents`

Exit criteria:

- `/agents` can explain where definitions come from and how overrides are resolved without becoming a full agent editor
