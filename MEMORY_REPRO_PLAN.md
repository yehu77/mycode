# Claude Code Memory Reproduction Plan

This document started as the next-stage implementation plan for reproducing
Claude Code's memory-related behavior in the Python reimplementation.

For the current local scope, the plan below now serves primarily as:

- a completed implementation record for the local memory workflow
- a reference for the design boundaries of compact / rewind / clear / resume
- a scope marker before any future move into deeper upstream reactive compact or
  hosted session-memory machinery

It is intentionally scoped to the local coding-agent goal of this repo. The target is not hosted-product parity. The target is stronger local parity for:

- conversation rewind/reset semantics
- context compaction behavior
- session memory continuity across long REPL/headless workflows
- visibility into what is in context and why

## Current Baseline

The current Python implementation already has meaningful local equivalents for part of the memory story:

- project memory loading and inspection through `/memory` and `/project-context`
- skill loading and reload-status tracking
- runtime-aligned `/context` usage inspection
- local `context_summary` support
- manual `/compact`
- scoped `/clear [history|changes|symbol|plan|session]`
- transcript persistence and session resume

Important current references:

- [PARITY_MATRIX.md](./PARITY_MATRIX.md)
- [README.md](./README.md)
- [claudecode_py/history_compaction.py](./claudecode_py/history_compaction.py)
- [claudecode_py/context_usage.py](./claudecode_py/context_usage.py)
- [claudecode_py/session.py](./claudecode_py/session.py)

## Current Read

The repo is in a split state:

- `Memory / skills` is effectively implemented for the local target.
- `Conversation clear / reset` is still partial because upstream rewind/history UX is broader.

In practical terms:

- project-level memory exists
- compacted conversation summary exists
- context usage inspection exists
- a full upstream-style rewind + reactive/session-memory compaction stack does not yet exist

## Upstream Audit Snapshot

This section records the first-pass upstream audit for the memory-related command family.

Primary upstream references reviewed:

- `package/src-extracted/src/commands/compact/compact.ts`
- `package/src-extracted/src/commands/rewind/rewind.ts`
- `package/src-extracted/src/commands/clear/conversation.ts`
- `package/src-extracted/src/commands/clear/caches.ts`
- `package/src-extracted/src/commands/memory/memory.tsx`

### What Upstream Actually Does

#### `/compact`

Upstream `/compact` is not just "summarize old messages".

Observed behavior from `commands/compact/compact.ts`:

- first trims messages to those after the active compact boundary
- tries `session memory compaction` first when no custom instructions are provided
- can route through a `reactive compact` path in reactive-only mode
- otherwise runs a `microcompact` pass before full compaction
- merges pre-compact hook instructions with user instructions
- performs post-compact cleanup
- resets summarized-message tracking
- suppresses immediate compact warnings after success
- rebuilds display text for transcript/UI visibility

This means upstream compaction is a layered pipeline:

1. boundary projection
2. optional session-memory compaction
3. optional reactive path
4. microcompact preprocessing
5. full conversation compaction
6. cleanup and warning-state updates

#### `/rewind`

Upstream `/rewind` is currently a very thin entrypoint, but it is still important semantically.

Observed behavior from `commands/rewind/rewind.ts`:

- it opens a message selector through `context.openMessageSelector()`
- it returns `skip`, meaning the command is UX/control flow rather than content-producing text

That indicates upstream rewind is not modeled as "clear some history and print text". It is a first-class history-navigation action.

#### `/clear`

Upstream `/clear` is much broader than the current Python local equivalent.

Observed behavior from `commands/clear/conversation.ts` and `commands/clear/caches.ts`:

- executes session-end hooks before clearing
- emits cache-eviction hints
- preserves selected background/agent task state across clear
- clears messages and proactive context-blocked state
- clears large sets of session caches
- resets cwd to original cwd
- clears discovered skills and loaded nested memory-path state
- regenerates session identity
- rebinds per-agent task transcript symlinks
- re-persists mode/worktree state
- executes session-start hooks after clearing
- may reinsert hook-generated messages into the fresh conversation

The cache cleanup path also explicitly resets:

- context caches
- commands/skills cache
- system prompt injection
- prompt cache-break tracking
- memory file cache read reason
- file suggestion caches
- repository and git-dir caches
- dynamic skills
- LSP diagnostic state
- tool-search, web-fetch, skill prompt, and agent-definition caches

This means upstream `/clear` is not just a message wipe or session-id reset. It is a broader "fresh conversation lifecycle restart".

#### `/memory`

Upstream `/memory` is not primarily an inspection command.

Observed behavior from `commands/memory/memory.tsx`:

- opens a memory-file selector UI
- creates the target memory file if needed
- launches the editor for that file
- communicates which editor is being used

So upstream `/memory` is closer to "edit Claude memory files" than "show loaded memory state".

### Current Python Comparison

The current Python implementation covers a narrower local-equivalent slice.

#### Python `/compact`

Current behavior:

- builds a local preview from `state.messages`
- keeps the last `N` messages
- converts older messages into summary lines
- merges them into `context_summary`
- truncates by character budget
- persists the new summary
- supports optional one-shot compact instructions

Strengths:

- simple
- deterministic
- easy to inspect and test
- already integrated with `/context`

Current limitations relative to upstream:

- no rewind boundary model
- no message-selector-driven rewind workflow
- no session-memory compaction path
- no reactive-only compact path
- no microcompact preprocessing
- no pre/post compact hook lifecycle
- no durable compact event model beyond the summary itself
- no compact-boundary-aware projection before compaction

#### Python `/clear`

Current behavior:

- `/clear history` clears `messages` and `context_summary`
- `/clear session` creates a fresh session id and preserves selected session configuration/workspace state
- local workflow state can be reset in narrower slices

Strengths:

- local semantics are clear
- fresh-session reset is already implemented
- remote/stdio surfaces already follow the Python reset model

Current limitations relative to upstream:

- no hook lifecycle around clear
- no broader cache-reset reason model
- no explicit memory-cache load-reason semantics
- no rewindable pre-clear boundary model
- no restored hook-generated post-clear seed messages
- less differentiation between conversation lifecycle restart and scoped local state reset

#### Python `/memory`

Current behavior:

- `/memory` shows loaded project memory
- `/project-context` exposes memory/skills/plugins/reload-status inspection
- `/skills-reload` reloads memory and skills from disk

Strength:

- strong inspection surface for local runtime understanding

Current limitation relative to upstream:

- no dedicated memory-file editing flow
- `/memory` semantics differ from upstream command intent

### First-Pass Gap Ranking

Ranked by parity value for this repo's local workflow target:

1. rewind boundary model and history selector semantics
2. compact event model and compact-boundary visibility
3. automatic compact policy with explicit trigger reason
4. clearer lifecycle distinction between compact, clear-history, and fresh-session reset
5. optional deeper compaction layers such as microcompact/session-memory compaction
6. upstream-style memory-file editing UX

The last item matters for command parity, but it is not the highest-value local coding-workflow gap.

## Problem Statement

The current implementation has a solid local compaction primitive, but it still behaves like a bounded local summary mechanism rather than Claude Code's broader memory workflow.

The main gap is not "can we summarize old messages?" The main gap is:

1. how history boundaries are represented
2. how users move back to an earlier conversation point
3. when compaction should happen
4. what is preserved across compaction vs reset
5. how compaction and reset semantics line up across REPL, TUI, stdio, bridge, and saved transcripts

## Upstream-Informed Target

For this repo, the right target is not a literal reproduction of every upstream subsystem. The right target is a staged local equivalent of the following behavior groups:

1. Rewindable conversation history
2. Explicit and intelligible compact boundaries
3. More realistic automatic compact policy
4. Session-memory continuity that feels stable in long coding sessions
5. Unified metadata across REPL/TUI/headless surfaces

## Non-Goals

This plan does not target:

- hosted account or usage-product memory features
- exact upstream hook buses or telemetry internals
- full hosted transport parity
- large speculative rewrites of transcript schema unless a later phase clearly requires it
- rebuilding every upstream compact internals module one-to-one

## Design Principles

1. Prefer local workflow parity over internal name parity.
2. Preserve transcript compatibility unless a migration is unavoidable.
3. Keep `/context` as the source of truth for "what is currently in context".
4. Make history boundaries visible instead of implicit.
5. Align REPL, TUI, stdio, bridge, and remote attach semantics whenever possible.
6. Add new behavior in layers so each phase can ship independently.

## Phase Plan

### Phase 0: Documentation and Behavior Audit

Goal:
Establish one precise implementation target before changing behavior again.

Deliverables:

- enumerate current Python behavior for `/compact`, `/clear`, `/history`, `/sessions show`, `/status resume`, and transcript restore
- enumerate upstream behavior slices from:
  - `src/commands/compact/*`
  - `src/commands/rewind/*`
  - `src/commands/clear/*`
- record which parts are:
  - already implemented locally
  - worth reproducing
  - intentionally out of scope

Implementation notes:

- keep this plan file updated as the canonical staging document
- if useful, add a short parity addendum in `PARITY_MATRIX.md` once actual implementation lands

Exit criteria:

- concrete gap list exists
- next phases can point to explicit target behaviors rather than vague "memory improvements"

### Phase 1: Rewind and History Boundary UX

Priority:
Highest

Goal:
Reproduce the most important missing user-facing memory behavior first: moving to an earlier conversation boundary and understanding what happened to history.

User-visible target:

- users can inspect and select meaningful rewind/reset boundaries
- users can tell the difference between:
  - normal turns
  - compacted-away turns
  - fresh-session reset boundaries
  - resumed transcript boundaries

Scope:

- introduce a local rewind workflow
- make compaction and reset boundaries visible in `/history`
- make saved session and transcript views show those boundaries clearly

Proposed implementation:

1. Add rewind boundary metadata to in-memory session state first.
2. Render rewind-capable history surfaces without requiring TUI-only affordances.
3. Support a local rewind action that restores message history to a selected earlier boundary.
4. Treat fresh-session reset as a first-class boundary, not just a destructive clear.

Suggested file areas:

- `claudecode_py/session.py`
- `claudecode_py/storage/transcript.py`
- `claudecode_py/session_components/change_history_views.py`
- `claudecode_py/session_components/summary_surfaces.py`
- command handlers for `/history`, `/sessions`, and future `/rewind` equivalent behavior

Open design decision:

- whether rewind should be exposed as a dedicated `/rewind` command or first staged through richer `/history` selection and follow-up commands

Recommendation:

- implement the behavior first
- keep command-surface changes minimal until the semantics are stable

Exit criteria:

- history surface can show rewindable boundaries
- rewind operation is possible locally
- rewind and fresh-session reset are clearly distinct
- resume/transcript inspection remains compatible

### Phase 2: Stronger Compaction Model

Priority:
High

Goal:
Move from simple local summary compaction toward a more structured memory-preserving compact workflow.

Current state:

- older messages are summarized into `context_summary`
- the summary is merged and truncated by char budget
- optional one-shot compact instructions are supported

Missing depth:

- richer compact metadata
- explicit compact boundaries in transcript/history
- more realistic distinction between pre-compact and post-compact state
- better visibility into what was kept vs summarized vs discarded

Proposed implementation:

1. Introduce a compact event record with metadata such as:
   - compact type
   - kept message count
   - compacted message count
   - instruction used
   - resulting summary size
   - transcript/session boundary marker
2. Store compact events in a way that can be rendered in `/history` and `/sessions show`.
3. Upgrade `/compact preview` and `/compact status` to render the same metadata model.
4. Distinguish:
   - manual compact
   - auto compact
   - reset-driven history drop
5. Keep `context_summary` as the main runtime input until a later phase proves a richer structure is needed.

Suggested file areas:

- `claudecode_py/history_compaction.py`
- `claudecode_py/session.py`
- transcript serialization paths
- history/session summary surfaces

Exit criteria:

- compact events are first-class renderable objects
- history/session surfaces can explain compact boundaries
- `/compact preview` and `/compact status` use the same metadata semantics as persisted history

### Phase 3: Automatic Compact Policy

Priority:
High

Goal:
Reduce the gap between purely manual compaction and an upstream-like long-session experience.

Current state:

- `/context` estimates usage from the actual runtime chain
- manual compaction exists
- there is mention of an existing auto-compaction path, but it is not yet a full upstream-style policy surface

Target behavior:

- compaction can happen automatically based on stable local policy
- users can understand why compaction triggered
- compact warnings and compact execution line up with actual context pressure

Proposed implementation:

1. Define a compact trigger policy based on:
   - estimated context percentage
   - message count
   - optional hard caps for summary growth
2. Add a pre-compact warning state that can be surfaced in REPL/TUI/headless views.
3. Trigger auto compact through the same core compaction engine as manual compact.
4. Record auto compact as a distinct compact event kind.
5. Surface recent compact reason in:
   - `/status`
   - `/history`
   - `/context`

Design constraint:

- do not introduce tokenizer dependencies just for this phase
- continue to use the repo's stable approximate token accounting unless later evidence shows it is insufficient

Exit criteria:

- automatic compact policy exists
- compact trigger reason is visible
- manual and automatic compact share the same event model

### Phase 4: Session-Memory Continuity Semantics

Priority:
Medium-High

Goal:
Make long-running local sessions feel like they have a coherent memory model rather than several loosely related history features.

This phase is about semantics, not just commands.

Questions to answer:

- what survives compact?
- what survives rewind?
- what survives fresh-session reset?
- what survives resume into a saved transcript?
- what happens to active plan/task/advisor context when history changes?

Proposed implementation:

1. Formalize memory-preservation rules for:
   - `messages`
   - `context_summary`
   - planning artifact
   - advisor state
   - task/checklist references
   - working-set and explicit context paths
2. Make these rules observable in summary surfaces.
3. Ensure REPL, TUI, stdio, and remote session proxy all update consistently after:
   - compact
   - rewind
   - clear history
   - clear session
4. Prevent stale surface state after memory-changing operations.

Likely affected areas:

- `session.py`
- remote session proxy
- stdio service actions
- TUI state synchronization
- session summary surfaces

Exit criteria:

- compact/rewind/reset no longer leave ambiguous surface state behind
- session continuity feels coherent across local and attached surfaces

### Phase 5: TUI and Headless Metadata Parity

Priority:
Medium

Goal:
Normalize how memory-related state is represented across textual and interactive surfaces.

Target:

- TUI panels and REPL/headless views should agree on:
  - whether history was compacted
  - whether a rewind boundary exists
  - whether the current session is post-reset
  - why compaction happened

Proposed implementation:

1. Add one shared memory-status metadata shape used by:
   - `/status`
   - `/history`
   - `/sessions show`
   - TUI status/history panels
2. Reuse the same field names in textual renderers and TUI state where practical.
3. Avoid separate one-off parsing logic for memory surfaces if a structured payload can be shared.

Exit criteria:

- REPL/TUI/headless memory metadata tells the same story
- fewer text-only ad hoc interpretations of memory state remain

### Phase 6: Optional Deep Parity Layer

Priority:
Optional

Goal:
Only if prior phases expose a real need, evaluate whether to reproduce more of the upstream compact stack.

Possible candidates:

- microcompact-style preprocessing
- more specialized session-memory compaction paths
- finer compact cleanup semantics
- more explicit cache-reset reasons

This phase should not start automatically.

Start only if:

- the earlier local-equivalent phases expose real quality gaps
- or upstream parity becomes the primary project goal over local simplicity

### Phase 6 Decision

Status:
Completed assessment

Decision:
Do **not** enter a new implementation phase for deeper upstream compact internals right now.

Recommendation:
Treat the current Python memory workflow as the practical parity endpoint for the local coding-agent scope of this repo.

Why:

1. The remaining upstream compact layers are mostly runtime/product infrastructure, not missing user-facing memory semantics.
2. The Python port now already covers the main user-visible conversation-memory behaviors:
   - local compaction
   - compact event metadata
   - history boundaries
   - rewind selection and apply
   - fresh-session reset
   - resume boundary handling
   - automatic compact policy
   - REPL/TUI/headless metadata parity
3. The highest-cost remaining upstream pieces are tightly coupled to Anthropic-specific APIs, cache-editing behavior, and app-global cleanup hooks that do not map cleanly to the Python runtime.

Upstream findings from the reassessment:

- `services/compact/autoCompact.ts`
  - adds threshold buffers, circuit-breakers, reactive-only gating, context-collapse gating, and query-source recursion guards
  - this is partly behavior, but much of it exists to coordinate other upstream systems we do not have
- `services/compact/microCompact.ts`
  - is primarily a cache-editing / tool-result-clearing layer
  - heavily tied to API-side cache semantics, time-based cache expiry, and tool-result pruning
- `services/compact/postCompactCleanup.ts`
  - is mostly cache invalidation and module-level state cleanup after compaction
  - useful in the upstream app because multiple subsystems share global state; low direct parity value for the Python port
- `services/SessionMemory/sessionMemory.ts` and `services/compact/sessionMemoryCompact.ts`
  - add a background-maintained session-memory file and a specialized compaction path that preserves a recent suffix while relying on that file
  - this is the most substantial remaining capability, but it is also a separate memory product layer rather than a prerequisite for the current conversation-memory workflow
- `commands/rewind/rewind.ts`
  - is mainly a selector-driven UX entrypoint, not a deeper state model than what is now implemented locally
- `commands/clear/conversation.ts`
  - is a broad app/session reset with task/cache/plugin/worktree side effects that mostly exceed the Python port's current responsibility boundary

Conclusion by candidate:

- `reactive compact parity`
  - defer
  - value is moderate, but it depends on API failure-driven retry paths and upstream context-collapse/cache interactions
- `microcompact parity`
  - do not pursue for current scope
  - this is mostly an Anthropic cache-editing optimization layer
- `post/pre compact hook parity`
  - do not pursue for current scope
  - upstream benefit comes from shared app-global state cleanup not present here
- `session-memory file editor / extraction loop`
  - optional future project, but not required for declaring the current local memory workflow “functionally reproduced”

When to reopen Phase 6:

- if the project goal changes from “local workflow parity” to “upstream runtime parity”
- if real user sessions show that local `context_summary` quality degrades without a session-memory file
- if remote/headless usage exposes concrete failures that require reactive compact rather than the current proactive policy
- if a future provider/backend adds cache-editing semantics worth exploiting in Python

## Cross-Phase Data Model Guidance

To avoid repeated churn, use one consistent conceptual model across phases:

- `history event`
  - normal message turn
  - compact event
  - reset boundary
  - rewind boundary
  - resume boundary
- `memory state`
  - live messages
  - compacted summary
  - retained artifacts
  - preserved workspace context
- `memory operation`
  - manual compact
  - auto compact
  - rewind
  - clear history
  - clear session

This does not require all of these to be persisted immediately in Phase 1. It does mean the implementation should aim toward this shape instead of adding unrelated one-off flags each time.

## Testing Strategy

Each phase should land with explicit tests.

### Core tests

- compact preserves recent window and updates `context_summary`
- compact event metadata renders consistently
- auto compact uses the same metadata path as manual compact
- rewind restores the intended earlier history boundary
- clear history does not behave like clear session
- clear session creates a new session identity boundary
- resume respects old transcript boundaries

### Surface tests

- `/history` renders compact/reset/rewind boundaries clearly
- `/sessions show` exposes memory-related boundary metadata
- `/status` exposes recent memory state consistently
- `/context` reflects current compacted summary contribution
- TUI status/history panels match REPL/headless semantics

### Remote/service tests

- stdio memory-changing actions update session state correctly
- remote session proxy updates local state after compact/rewind/reset
- open-session maps do not become stale after session identity changes

## Recommended Delivery Order

Recommended execution order for the next stage:

1. Phase 0
2. Phase 1
3. Phase 2
4. Phase 3
5. Phase 4
6. Phase 5
7. Phase 6 only if needed

If schedule is tight, the minimum high-value slice is:

1. Phase 1
2. Phase 2
3. Phase 3

That combination alone would materially narrow the current parity gap.

## What to Avoid

Avoid these failure modes:

- implementing tokenizer-heavy machinery too early
- tying memory semantics to TUI-only controls
- introducing schema churn before the boundary model is stable
- conflating project memory with conversation memory
- adding several separate compact representations for REPL, TUI, and stdio
- overfitting to upstream internal module names instead of local behavior

## Implementation Checklist

Use this as the short execution checklist for the next stage.

- [x] Audit current Python memory-related behavior and document exact gaps
- [x] Define rewind boundary model
- [x] Render boundary metadata in `/history`
- [x] Add local rewind behavior
- [x] Introduce compact event metadata
- [x] Unify `/compact preview` and `/compact status` with persisted compact metadata
- [x] Add automatic compact trigger policy
- [x] Expose recent compact reason in `/status` and `/context`
- [x] Normalize memory-preservation semantics after compact/rewind/reset/resume
- [x] Align REPL/TUI/headless metadata views
- [x] Reassess whether deeper upstream compact internals are still necessary

## Recommended First Concrete Milestone

If work starts immediately, the best first milestone is:

`Phase 1 + compact event metadata foundation`

That means:

- make rewind/reset/compact boundaries visible
- add the first durable compact-boundary metadata shape
- do not yet overbuild the auto policy layer

This gives the next phase a stable foundation and turns the current local compaction feature into a much more upstream-like memory workflow.

## Immediate Backlog From Audit

This is the recommended first implementation backlog directly derived from the upstream audit.

### Milestone A: Boundary Model

- [x] define local history boundary types:
  - `compact`
  - `fresh_session_reset`
  - `resume`
  - `rewind`
- [x] decide minimum persistence shape for those boundaries
- [x] render those boundaries in `/history`
- [x] render those boundaries in `/sessions show`

### Milestone B: Compact Event Foundation

- [x] add a compact event payload with:
  - trigger kind
  - compacted count
  - kept count
  - instruction
  - merged summary size
- [x] make `/compact preview` and `/compact status` render from that payload
- [x] persist enough of the compact event for history/session inspection

### Milestone C: Rewindable Local Workflow

- [x] add a rewind selection model
- [x] support rewind to a selected boundary in local session state
- [x] distinguish rewind from:
  - `/clear history`
  - `/clear session`
  - `/compact`
- [x] expose the rewind result in `/status resume` or `/history`

### Milestone D: Lifecycle Semantics Cleanup

- [x] define what survives:
  - compact
  - rewind
  - clear history
  - clear session
- [x] normalize task/plan/advisor/file-focus behavior after each operation
- [x] ensure stdio and remote session proxy follow the same semantics

### Explicit Deferrals

These should wait until the above is stable:

- reactive compact parity
- microcompact parity
- pre/post compact hook parity
- upstream-style memory-file editor UX
