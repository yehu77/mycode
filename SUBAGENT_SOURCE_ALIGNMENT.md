# Subagent Source-Depth Alignment

This document compares the current `python_claudecode` subagent runtime to the upstream Claude Code agent/subagent implementation in `../package/src-extracted/src/tools/AgentTool/*`, `../package/src-extracted/src/utils/forkedAgent.ts`, `../package/src-extracted/src/utils/sessionStorage.ts`, `../package/src-extracted/src/utils/swarm/*`, and related hook/runtime layers by **mechanism depth**, not by the mere presence of an `agent` tool.

It is the canonical place to answer:

- how close the Python subagent runtime is to upstream at the spawn/runtime/orchestration level
- which parts are already structurally aligned
- which parts are only local functional substitutes
- which remaining gaps are real local runtime deficits vs broader upstream breadth

## Method

This comparison is grounded in mechanism families rather than command names.

The main layers used here are:

- subagent spawn and runtime model
- parent-child context sharing
- foreground vs background execution
- subagent result model
- transcript, resume, and hydration behavior
- permission and tool policy propagation
- agent types, named subagents, and workflow roles
- hooks, telemetry, and orchestration depth

Depth verdict vocabulary:

- `Depth-aligned`
- `Locally aligned with narrower breadth`
- `Functional substitute, shallower mechanism`
- `Real local mechanism gap`
- `Broader upstream breadth`
- `Deliberately out of current local scope`

The goal is to separate:

- structural alignment to the same upstream idea
- local substitutes that achieve a similar outcome through simpler machinery
- broader upstream product/runtime breadth that should not be treated as missing local core runtime

## Subagent Mechanism Alignment Matrix

| Mechanism family | Upstream mechanism | Current `python_claudecode` | Depth verdict | Why this verdict | Next action classification |
|---|---|---|---|---|---|
| Subagent spawn and runtime model | Upstream `AgentTool` / `runAgent()` / `forkSubagent` run subagents through a dedicated agent runtime with forked context creation, agent IDs, sidechain recording, MCP/tool resolution, and richer lifecycle ownership. | Local `agent` tool spawns a real child session through `Session.run_subagent()` or `launch_background_agent()`, with `create_child_session()` handling the runtime split. | Locally aligned with narrower breadth | A real subagent runtime exists locally and is not just a fake prompt wrapper, but the runtime is still basically child-session orchestration rather than the broader upstream agent stack. | Real local mechanism gap |
| Parent-child context sharing | Upstream uses a deeper forked-context model with selective sharing and isolation of tool-use context, file-state cache, permission state, abort behavior, and cache-safe prompt inputs. | Local child sessions inherit runtime mode, execution contract, command-policy state, workspace mapping, and some planning/runtime flags through copied `SessionState` plus session-factory wiring. | Functional substitute, shallower mechanism | Useful inheritance exists locally, but it is much simpler than upstream selective forked-context composition and shared callback/state control. | Real local mechanism gap |
| Foreground vs background execution | Upstream distinguishes interactive subagents, async/background agents, workflow-run agents, in-process teammates, and broader shell/UI handling. | Local runtime distinguishes foreground `run_subagent()` and detached `launch_background_agent()`, plus `isolated_workspace` and `read_only` variants. | Locally aligned with narrower breadth | The foreground/background split is real locally, but the execution taxonomy is much narrower than upstream. | Real local mechanism gap |
| Subagent result model | Upstream foreground agent runs emit sidechain/subagent messages and richer runtime events; results are not reduced to a single final string contract. | Local foreground subagents return final text to the parent; background agents return `task_id`, and follow-up inspection happens through task surfaces. | Functional substitute, shallower mechanism | The current result contract is workable but much flatter than upstream sidechain/event-driven subagent result handling. | Real local mechanism gap |
| Transcript, resume, and hydration | Upstream has agent transcript paths, sidechain transcript recording, subagent metadata, and event hydration/reconstruction on resume. | Local child/background sessions have their own session IDs, transcript files, and task/background metadata, but not upstream-style sidechain hydration and subagent-event reconstruction. | Functional substitute, shallower mechanism | Persistence exists locally, but the deeper resume/hydration story is still well below upstream. | Real local mechanism gap |
| Permission and tool policy propagation | Upstream carries subagent-specific tool permission context, command restrictions, and classifier/tool gating into forked agent execution. | Local runtime propagates command-policy restrictions, `read-only-subagent` tool sets, bash prefix allowlists, plan-mode inheritance, and execution constraints into child sessions. | Locally aligned with narrower breadth | This is a real runtime mechanism locally, not just metadata, but it is still a narrower contract than the upstream permission/context system. | Real local mechanism gap |
| Agent types, named subagents, and workflow roles | Upstream supports broader agent definitions, named agents, teammates, in-process teammates, workflow roles, and agent-type-specific behavior. | Local runtime has builtin and project-local agent definitions plus execution labels like `child-session`, `read-only-subagent`, and `background-agent`, but the generic `agent` tool is still mostly one broad subagent type with flags. | Functional substitute, shallower mechanism | There is meaningful local agent-definition structure, but the runtime breadth and agent-type specialization are much shallower than upstream. | Real local mechanism gap |
| Hooks, telemetry, and orchestration depth | Upstream has `SubagentStart` / `SubagentStop` hooks, mailbox/queue integration, richer telemetry, and broader orchestration/runtime coupling. | Local runtime has task progress, background metadata, TUI/stdio/remote summaries, and task inspection, but no comparable hook lifecycle or subagent telemetry depth. | Real local mechanism gap | This is a concrete runtime-depth miss, not just broader product packaging. | Real local mechanism gap |

## Subsystem Deep Dives

### Subagent spawn and runtime model

**Upstream mechanism**  
Upstream subagents are not just recursive prompts. `AgentTool`, `runAgent()`, `forkSubagent`, and `createSubagentContext()` build a dedicated agent runtime with its own agent identity, tool-use context, cache-safe prompt inputs, sidechain transcript recording, and agent-scoped MCP/tool assembly.

**Current local implementation**  
The Python runtime already has a real subagent path:

- a real `agent` tool
- `Session.run_subagent()` for foreground execution
- `Session.launch_background_agent()` for detached execution
- `SessionFactory.create_child_session()` for child-session creation
- optional `isolated_workspace`
- optional `read_only` contract

So the local subagent line is not missing. It already runs real child sessions rather than only injecting advisory prompt text back into the parent.

**Depth verdict**  
`Locally aligned with narrower breadth`

**Remaining gap**  
The main gap is that local spawn/runtime still centers on child-session orchestration. Upstream has a deeper dedicated subagent runtime with broader context, transcript, event, and tool assembly semantics.

### Parent-child context sharing

**Upstream mechanism**  
Upstream forked-agent context creation is selective and explicit. It can isolate mutable state while sharing specific callbacks, request lineage, file-state cache, content replacement state, abort controllers, and tool-use context where needed.

**Current local implementation**  
The Python runtime copies or derives a practical subset of parent state into the child:

- runtime mode and plan-mode flags
- execution mode and command-policy state
- active execution constraint
- workspace/transcript path mapping
- plan file lineage for child sessions

This is sufficient for local child-session continuity, including plan mode inheritance and read-only-subagent policy propagation.

**Depth verdict**  
`Functional substitute, shallower mechanism`

**Remaining gap**  
The child inherits a useful snapshot, but it is still much simpler than upstream selective forked-context composition. There is no comparable local layer for explicit shared callbacks, shared/isolated caches, or richer request-lineage-aware context shaping.

### Foreground vs background execution

**Upstream mechanism**  
Upstream distinguishes several execution modes:

- interactive subagents
- async/background agents
- in-process teammates
- workflow-oriented agent paths
- UI/shell-specific agent handling

These are not just display labels; they affect lifecycle, prompts, permission handling, and result visibility.

**Current local implementation**  
The Python runtime has a real split:

- foreground `run_subagent()`
- background `launch_background_agent()`
- `isolated_workspace=True` snapshot mode
- `read_only=True` planning/inspection mode

Background agents are tracked through the task manager and background metadata surfaces instead of being invisible fire-and-forget calls.

**Depth verdict**  
`Locally aligned with narrower breadth`

**Remaining gap**  
The local split is useful and real, but upstream has a broader execution taxonomy and richer lifecycle/UI behavior than the current foreground/background pair plus flags.

### Subagent result model

**Upstream mechanism**  
Upstream subagent execution is sidechain-aware. The runtime can preserve agent-specific events, transcript slices, and richer outputs rather than reducing foreground agent work to one final text blob.

**Current local implementation**  
The Python runtime currently uses a simpler contract:

- foreground subagent: parent gets final text
- background subagent: caller gets `task_id`
- follow-up detail lives in `/tasks`, task tools, TUI, and background summaries

This is a functional local workflow, but the parent does not get a richer subagent conversation delta model.

**Depth verdict**  
`Functional substitute, shallower mechanism`

**Remaining gap**  
This is one of the clearest remaining real mechanism gaps. The local runtime still lacks an upstream-like subagent event/result model for foreground calls.

### Transcript, resume, and hydration

**Upstream mechanism**  
Upstream stores subagent transcripts under dedicated paths, persists agent metadata, and can hydrate subagent-side events back into resumed session state. Resume is not only about the main thread transcript.

**Current local implementation**  
The Python side already has real persistence pieces:

- child and background sessions have their own session IDs
- transcripts and background session metadata exist
- task metadata keeps parent/child linkage and reverse hints
- plan/file/runtime state can carry into children

But local resume still does not reproduce upstream subagent event hydration or sidechain reconstruction depth.

**Depth verdict**  
`Functional substitute, shallower mechanism`

**Remaining gap**  
The remaining deficit is not “no persistence.” It is specifically the absence of deeper subagent transcript hydration and resume reconstruction semantics.

### Permission and tool policy propagation

**Upstream mechanism**  
Upstream propagates subagent-specific permission context, tool gating, and other runtime restrictions into forked agent execution through a deeper permission/runtime model.

**Current local implementation**  
The Python runtime already does real propagation:

- `read-only-subagent` tool policy
- read-only bash prefixes
- command-policy inheritance
- plan-mode restrictions inherited into child sessions
- execution-mode-specific constraints

This meaningfully changes what the child can do, so it is not just descriptive metadata.

**Depth verdict**  
`Locally aligned with narrower breadth`

**Remaining gap**  
The local permission line is solid for current local scope, but it still sits below upstream classifier/tool-permission context depth and broader runtime coupling.

### Agent types, named subagents, and workflow roles

**Upstream mechanism**  
Upstream has broader agent identity and role machinery:

- richer agent definitions
- named agents
- teammates and in-process teammates
- workflow-specific roles
- agent-type-specific behavior and prompts

**Current local implementation**  
The Python runtime already has more than one flat `agent` concept:

- builtin agent definitions
- project-local agent manifests in `.pyclaude/agents/*.json`
- execution labels such as `child-session`, `read-only-subagent`, `background-agent`

However, the current generic `agent` tool still mainly exposes a single broad spawn path with `read_only`, `run_in_background`, and `isolated_workspace` flags rather than a real upstream-style agent-type runtime surface.

**Depth verdict**  
`Functional substitute, shallower mechanism`

**Remaining gap**  
This is not “no agent definition system.” The gap is that runtime specialization by named agent type or workflow role is still much shallower than upstream.

### Hooks, telemetry, and orchestration depth

**Upstream mechanism**  
Upstream has explicit subagent lifecycle hooks (`SubagentStart`, `SubagentStop`), mailbox/queue integration, richer telemetry, and wider orchestration coupling across the product shell and runtime.

**Current local implementation**  
The Python runtime currently offers:

- task/background metadata
- runtime progress surfaces
- TUI/stdio/remote summaries
- task and background inspection commands/tools

These are useful observability surfaces, but they are not the same thing as an upstream subagent hook and orchestration framework.

**Depth verdict**  
`Real local mechanism gap`

**Remaining gap**  
The missing hook/orchestration lifecycle is a genuine runtime-depth gap if the goal is deeper source reproduction, not merely broader hosted product breadth.

## What Is Closed Locally

For the current local-first scope, these lines are substantively closed:

- **Real foreground subagent execution via child sessions**  
  Foreground delegation is real and already runs through child sessions. Remaining difference is still a real mechanism miss around richer result/event semantics, not absence of execution.

- **Detached/background subagent execution path**  
  Background agents already exist with task tracking and session linkage. Remaining difference is mostly broader upstream orchestration breadth.

- **Isolated workspace option for subagents**  
  Child/background agents can already run on an isolated workspace snapshot. Remaining difference is mostly breadth and polish, not missing local core behavior.

- **Read-only subagent execution contract**  
  Local `read_only` subagents already compile into a meaningful execution contract with restricted tools and shell behavior. Remaining difference is narrower permission/runtime breadth.

- **Propagation of command-policy restrictions into child sessions**  
  Child sessions already inherit or receive real execution constraints. Remaining difference is a deeper upstream permission model, not missing propagation.

- **Plan-mode inheritance into child sessions**  
  Child sessions already inherit plan-mode runtime state and plan-mode restrictions. Remaining difference is broader upstream plan/subagent orchestration depth.

- **Task/status/background inspection surfaces for subagent work**  
  Local task tools, task surfaces, and background summaries already expose useful subagent progress and linkage. Remaining gap is deeper event/hydration/orchestration breadth.

These lines should no longer be described as if Python subagents are absent or purely superficial wrappers.

## What Remains a Real Local Mechanism Gap

The highest-value remaining subagent-specific local mechanism gaps are:

- deeper subagent result handling closer to upstream sidechain/event semantics
- stronger transcript/resume/hydration depth for subagent execution
- richer parent-child context sharing beyond simple child-session copy/inheritance
- broader runtime specialization by named agent type or workflow role where it materially changes execution
- explicit subagent lifecycle hooks and deeper orchestration coupling

These are real runtime-depth gaps, not just missing packaging or hosted surface area.

## What Is Broader Upstream Breadth

The following differences are real, but should not be treated as core local deficits:

- teammate mailbox ecosystems and broader swarm/team product flows
- wider hosted/remote shell integration around subagents
- broader telemetry and analytics ecosystems
- wider shell/UI agent panels and workflow-product affordances outside current local-first scope

These belong more to upstream product/runtime breadth than to absence of a local subagent runtime.

## Implications for Next Subagent Work

1. Do not describe the current Python subagent line as missing.  
   It already has a real `agent` tool, real foreground child-session execution, real background agents, isolated workspace support, and real read-only policy propagation.

2. Treat the next subagent work as depth follow-up, not basic enablement.  
   The best next lines are result-model depth, transcript/resume/hydration depth, and richer parent-child context sharing.

3. Keep broader upstream teammate/product breadth separate from core local runtime planning.  
   Wider swarm/mailbox/UI ecosystems are real, but they should not obscure that a usable local subagent runtime already exists.
