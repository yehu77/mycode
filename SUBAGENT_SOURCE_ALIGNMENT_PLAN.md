# Subagent Source-Depth Alignment Document Plan

## Summary

Add one new dedicated document for the subagent line:

- `python_claudecode/SUBAGENT_SOURCE_ALIGNMENT.md`

This document should become the canonical answer to:

- how close the current Python subagent runtime is to upstream Claude Code at **mechanism depth**
- which parts are already structurally aligned
- which parts are only local functional substitutes
- which remaining gaps are true local runtime deficits vs broader upstream breadth

Chosen shape:

- **matrix + deep dives**
- standalone doc, not merged into `CLAUDE.md`
- stronger verdict language than simple `Implemented / Partial / Missing`

The existing broader docs should only point to it:

- `UPSTREAM_SOURCE_ALIGNMENT.md`: add a short pointer that the subagent line now has its own source-depth analysis
- optionally `CLAUDE.md`: one short pointer in the agents/runtime status area
- do not duplicate the full matrix elsewhere

## Key Changes

### 1. New document structure

Create `SUBAGENT_SOURCE_ALIGNMENT.md` with this fixed structure:

1. `Method`
2. `Subagent Mechanism Alignment Matrix`
3. `Subsystem Deep Dives`
4. `What Is Closed Locally`
5. `What Remains a Real Local Mechanism Gap`
6. `What Is Broader Upstream Breadth`
7. `Implications for Next Subagent Work`

The matrix should be the fast-scan surface.  
The deep dives should justify each verdict with concrete upstream-vs-local mechanism descriptions.

### 2. Verdict vocabulary

Do not use feature-checklist wording as the primary language. Use:

- `Depth-aligned`
- `Locally aligned with narrower breadth`
- `Functional substitute, shallower mechanism`
- `Real local mechanism gap`
- `Broader upstream breadth`
- `Deliberately out of current local scope`

Each matrix row should have exactly these fields:

- `Mechanism family`
- `Upstream mechanism`
- `Current python_claudecode`
- `Depth verdict`
- `Why this verdict`
- `Next action classification`

### 3. Matrix axes to include

The matrix should compare at least these mechanism families:

- `Subagent spawn and runtime model`
  - upstream: forked agent context, agent runtime orchestration, task/tool-triggered agent spawn, richer subagent lifecycle ownership
  - local: `Agent` tool + `Session.run_subagent()` + child-session creation

- `Parent-child context sharing`
  - upstream: deeper forked-context model with selective inheritance/shared callbacks/state and subagent-specific context shaping
  - local: copied session state, command policy inheritance, plan/runtime mode carry-over, simpler child-session inheritance

- `Foreground vs background execution`
  - upstream: richer distinction between interactive subagents, detached/background agents, workflow-run agents, and broader UI/runtime treatment
  - local: foreground `run_subagent()` plus `launch_background_agent()`

- `Subagent result model`
  - upstream: sidechain/subagent event streams, richer result handling, not only final text
  - local: foreground returns final text, background returns `task_id`, task surfaces summarize execution afterward

- `Transcript, resume, and hydration`
  - upstream: subagent transcript paths, event hydration, resume routing, persisted subagent-side reconstruction
  - local: child sessions have their own session/transcript pathing and task metadata, but not upstream event-hydration depth

- `Permission and tool policy propagation`
  - upstream: deeper permission/tool gating per subagent context, classifier/tool permission context, broader policy semantics
  - local: command-policy propagation, `read-only-subagent`, allowed tool names, bash prefix restrictions, plan-mode inheritance

- `Agent types, named subagents, and workflow roles`
  - upstream: broader agent-type system, teammate/named subagent/workflow roles, agent-specific behaviors
  - local: basic builtin agent registry and execution labels like `child-session`, `read-only-subagent`, `background-agent`

- `Hooks, telemetry, and orchestration depth`
  - upstream: SubagentStart/SubagentStop hooks, queue/orchestration integration, broader telemetry and product-runtime handling
  - local: task/background metadata, status surfaces, runtime summaries, but no comparable hook/telemetry/runtime breadth

### 4. Deep-dive content rules

For each subsystem deep dive, use the same 4-part shape:

- `Upstream mechanism`
- `Current local implementation`
- `Depth verdict`
- `Remaining gap`

The deep dives should explicitly call out these important current local truths:

- local subagents are **not missing**
- the Python runtime already has a real `Agent` tool
- foreground subagents already run through a real child session path
- background agents already exist as a separate runtime path
- command-policy and read-only-subagent restrictions already propagate meaningfully

The deep dives should also explicitly call out these likely still-open local gaps:

- no upstream-style sidechain/subagent event-result model for foreground agent calls
- no upstream-depth subagent transcript hydration/resume model
- no broader named-agent / teammate / workflow-role breadth
- no upstream-depth hook and telemetry lifecycle
- no richer forked-context sharing model beyond the current child-session copy/inheritance path

### 5. Explicit “closed locally” section

Add one section that stops the repo from repeatedly understating the current local subagent line.

This section should say the following are now closed for the current local-first scope:

- real foreground subagent execution via child sessions
- detached/background subagent execution path
- isolated workspace option for subagents
- read-only subagent execution contract
- propagation of command-policy restrictions into child sessions
- plan-mode inheritance into child sessions
- task/status/background inspection surfaces for subagent work

For each item, state whether the remaining gap is:

- only broader upstream breadth, or
- still a real mechanism miss

### 6. Explicit “worth pursuing next” section

Separate the next work into two buckets.

`Real local mechanism gaps worth pursuing`:

- deeper subagent result model closer to upstream event/sidechain semantics
- stronger transcript/resume/hydration depth for child sessions and subagent outputs
- richer parent-child context sharing model
- broader agent-type/runtime-role semantics only where they materially change execution
- subagent lifecycle hooks if the runtime is pushed closer to upstream orchestration depth

`Broader upstream breadth not worth treating as core deficit`:

- teammate mailbox / wider workflow-product orchestration
- broader hosted/remote shell integration around subagents
- upstream telemetry ecosystems and product analytics
- wider UI/product panels and shell-specific agent affordances outside current local-first scope

### 7. Existing-doc pointer updates

Keep these updates minimal and additive:

- `UPSTREAM_SOURCE_ALIGNMENT.md`
  - add a short note in the background-agents / agent-runtime section that detailed subagent mechanism alignment lives in `SUBAGENT_SOURCE_ALIGNMENT.md`
- `CLAUDE.md`
  - add a one-line pointer in the agents/runtime milestone area if needed
- do not duplicate the new matrix into `PARITY_MATRIX.md`

## Important Content / Interface Changes

This is a documentation-only change.

New doc contract:

- `UPSTREAM_SOURCE_ALIGNMENT.md` remains the broad runtime/system comparison
- `SUBAGENT_SOURCE_ALIGNMENT.md` becomes the canonical deep subagent comparison
- `CLAUDE.md` remains the project ledger, not the place for detailed subagent source analysis

Recommended per-section shape inside `SUBAGENT_SOURCE_ALIGNMENT.md`:

- `Upstream mechanism`
- `Current local implementation`
- `Depth verdict`
- `Remaining gap`

This keeps the comparison concrete and avoids drifting back into surface-feature lists.

## Test Plan

- Document inventory
  - repo contains new `SUBAGENT_SOURCE_ALIGNMENT.md` after the follow-up doc-writing step

- Role clarity
  - `SUBAGENT_SOURCE_ALIGNMENT.md` is the canonical deep subagent comparison
  - `UPSTREAM_SOURCE_ALIGNMENT.md` remains the broader runtime/system alignment doc

- Content discipline
  - comparison is by mechanism family, not by slash-command list
  - every row distinguishes `depth` vs `breadth`
  - current local subagent runtime is not described as absent or purely superficial

- Status consistency
  - already-implemented local subagent runtime pieces are explicitly marked as closed for current local scope
  - remaining gaps are split into `real local mechanism gaps` vs `broader upstream breadth`

- No drift
  - no reintroduction of generic `Partial` wording as the primary verdict language
  - no contradiction with current code reality around `Agent` tool, child sessions, background agents, read-only-subagent policy, and isolated workspace support

## Assumptions

- The follow-up deep-dive file should be named `SUBAGENT_SOURCE_ALIGNMENT.md`.
- This plan file is maintainability/documentation planning only; it does not change runtime code.
- The document should be strong-judgment and mechanism-depth oriented, not a soft feature summary.
- The current Python subagent system should be described as having a real local runtime, but still clearly below upstream in event model, hydration depth, agent-type breadth, and orchestration lifecycle richness.
