# Upstream Source-Depth Alignment

This document compares `python_claudecode` to the upstream Claude Code source tree in `../package/src-extracted/src` by **implementation depth** rather than by visible commands or feature checklists.

It is the canonical place to answer questions like:

- how close the local query/runtime loop is to upstream
- whether prompt assembly is structurally aligned or only functionally similar
- whether tool orchestration, compaction, replacement, and runtime events are deep matches or local substitutes
- which remaining gaps are true local mechanism gaps vs broader upstream hosted/product breadth

## Method

The comparison is grounded in upstream source strata rather than slash-command families. The main strata used here are:

- `query` runtime orchestration, token budgeting, recovery, continuation, and provider-view assembly
- `context` shaping, prompt injection, system-context composition, and dynamic context inputs
- `tools` pool construction, schema stability, filtering, approval/orchestration, and lifecycle semantics
- `state` / history / memory / rewind / compaction architecture
- runtime surfaces and structured consumers across REPL, TUI, stdio, and remote
- plugin / skills / project-context architecture
- background agents and local detached workflow
- transport and product breadth that depends on hosted/platform scope

Depth verdict vocabulary:

- `Depth-aligned`
- `Locally aligned with narrower breadth`
- `Functional substitute, shallower mechanism`
- `Upstream-only breadth`
- `Deliberately out of local scope`

The goal is to distinguish:

- structural alignment to the same kind of upstream mechanism
- local substitutes that achieve similar workflow outcomes through simpler machinery
- upstream breadth that should not be treated as a missing local runtime core

## Mechanism Alignment Matrix

| Mechanism family | Upstream anchor | Local anchor | Depth verdict | Read |
|---|---|---|---|---|
| Query/runtime engine | `src/query.ts`, `src/query/tokenBudget.ts`, `src/context.ts` | `claudecode_py/runtime/query_loop.py`, `claudecode_py/runtime/prompt_prefix.py` | Locally aligned with narrower breadth | The local runtime now has provider-view assembly, budget/recovery, replacement/artifact/microcompact, and prompt-prefix signatures, but upstream still has broader continuation and feature-gated runtime breadth. |
| Prompt/context assembly | `src/context.ts`, query-side prompt assembly, upstream cache-sensitive prompt composition | `claudecode_py/prompts.py`, `claudecode_py/runtime/context.py`, `claudecode_py/runtime/prompt_prefix.py` | Locally aligned with narrower breadth | The local runtime has explicit prompt blocks, dynamic boundary semantics, provider-view assembly, and deterministic prefix signatures, but not provider-native cache-control wire behavior. |
| Tool pool and orchestration | `src/tools.ts`, tool pool assembly, tool filtering, presets, lifecycle semantics | `claudecode_py/tools/*`, `claudecode_py/runtime/orchestrator.py`, `claudecode_py/runtime/tool_schema_cache.py` | Locally aligned with narrower breadth | The local tool loop, approval model, schema stability, lifecycle events, and plugin/MCP injection are structurally parallel, but the upstream tool ecosystem and feature-gated breadth are wider. |
| State/history/memory/compaction | `src/state/*`, `src/history.ts`, query-side collapse/snip/compact behavior | `claudecode_py/storage/transcript.py`, `claudecode_py/history_compaction.py`, `claudecode_py/session_components/history_memory.py` | Locally aligned with narrower breadth | Transcript persistence, rewind boundaries, compact lifecycle, replacement-aware compaction, and resume semantics are strong locally; upstream still has broader history/product breadth. |
| Runtime surfaces and consumers | upstream CLI/Ink/screens/remote consumers | `claudecode_py/context_usage.py`, `claudecode_py/session_components/summary_surfaces.py`, `claudecode_py/tui/*`, `claudecode_py/service/*` | Functional substitute, shallower mechanism | Local REPL/TUI/stdio/remote surfaces are coherent and share structured payloads, but upstream shell breadth and transport/product integration remain broader. |
| Plugin / skills / project-context | `src/plugins/*`, `src/skills/*`, bundled/plugin prompt architecture | `claudecode_py/plugins/*`, `claudecode_py/skills/*`, `claudecode_py/session_components/project_context.py` | Functional substitute, shallower mechanism | The local architecture is coherent for builtin plus project-local declarative extensibility, but upstream packaging, distribution, and productized ecosystem breadth are wider. Detailed skill mechanism-depth alignment is tracked separately in `SKILL_SOURCE_ALIGNMENT.md`. |
| Background agents / detached workflow | `src/commands/agents/*`, background/task summary modules, task surfaces | background `ask`, `ps`, `logs`, `attach`, `/agents`, `background_metadata.py` | Functional substitute, shallower mechanism | The local detached workflow is usable and observability-rich, but upstream agent orchestration breadth is still larger. |
| Remote / transport / hosted shell | `src/remote/*`, broader transports, hosted bridge/product shell | stdio service, TCP bridge, remote session proxy | Upstream-only breadth | The local implementation supports attachable local workflows, not the broader hosted transport/product matrix. |

## Subsystem Deep Dives

### Query/runtime orchestration

**Upstream mechanism**  
The upstream `query` layer is a deep runtime owner: provider-call assembly, token-budget continuation, recovery paths, feature-gated compact/collapse behaviors, and model/tool coordination all flow through one orchestration layer.

**Local implementation**  
The Python runtime now has a real provider-view query loop rather than a shallow prompt wrapper. It includes:

- shared runtime budget state and budget-pressure surfaces
- prompt-too-long compact-retry recovery
- replacement-aware provider-view assembly
- artifact indirection and balanced microcompact before full compaction
- deterministic prompt-prefix signatures and reduction tiers
- runtime event emission and structured progress consumers

**Depth verdict**  
`Locally aligned with narrower breadth`

**Remaining gap**  
The remaining gap is not basic runtime ownership. It is mostly:

- provider-native continuation/cache behavior
- finer-grained upstream feature-flag/runtime breadth
- broader hosted transport/product integrations outside local-first scope

### Prompt/context assembly and prefix preservation

**Upstream mechanism**  
Upstream `context` and query assembly treat system context, dynamic context, tool schemas, and history as a cache-sensitive provider-view prefix problem rather than a single monolithic prompt string.

**Local implementation**  
The Python runtime now mirrors that structural idea locally:

- system prompt blocks with cache-scope and boundary semantics
- session-level tool-schema cache with stable ordering
- provider-view prompt-prefix assembly result
- deterministic static/system/tool/stable-prefix signatures
- replacement/artifact/microcompact-aware reduction tiers

**Depth verdict**  
`Locally aligned with narrower breadth`

**Remaining gap**  
The main remaining mechanism gap is deeper provider-native cache-control semantics. The rest of the difference is broader upstream cache/runtime infrastructure.

### Tool pool, schema stability, approval, and lifecycle events

**Upstream mechanism**  
Upstream constructs tool pools from multiple sources, filters them through permission and feature layers, and cares about schema-byte stability because the tool bundle sits near the front of provider context.

**Local implementation**  
The Python runtime now has:

- stable tool schema ordering and session-level schema caching
- provider-ready canonical tool specs with per-call overlay separation
- explicit approval wait vs execution start lifecycle
- batch-start/batch-finish/result-summary/budget-pressure/recovery events
- plugin- and MCP-backed tool injection into the shared local tool pool

**Depth verdict**  
`Locally aligned with narrower breadth`

**Remaining gap**  
Remaining difference is mostly breadth: more upstream tool families, more feature-gated tool presets, and more product-specific tool ecology.

### State, history, memory, rewind, and compaction

**Upstream mechanism**  
Upstream has persistent session state, history operations, context collapse/snip behaviors, and broader product-facing history workflows.

**Local implementation**  
The Python side now has a coherent local memory lifecycle:

- transcript persistence and saved-session resume
- history boundaries with preview-before-apply rewind
- manual `/compact` plus runtime auto-compaction/recovery
- replacement-aware and artifact-aware compaction summaries
- shared memory/history narratives across REPL, TUI, stdio, and remote

**Depth verdict**  
`Locally aligned with narrower breadth`

**Remaining gap**  
The remaining gap is mostly broader upstream history/product breadth, not missing local state machinery.

### Runtime surfaces and structured consumers

**Upstream mechanism**  
Upstream has broader CLI/Ink/screens/remote product surfaces consuming runtime state through a wider shell and transport stack.

**Local implementation**  
The Python implementation has a stronger local-first surface unification than a typical prototype:

- `/status`, `/context`, `/history`, `/sessions`, `/config`, `/model`, `/project-context`
- aligned `status_*`, `memory_*`, runtime-progress, and prompt-prefix payloads
- TUI dashboard integration
- stdio and bridge-backed remote consumers

**Depth verdict**  
`Functional substitute, shallower mechanism`

**Remaining gap**  
The gap here is mostly shell breadth and transport/product surface breadth, not absence of local observability.

### Plugin, skills, and project-context architecture

**Upstream mechanism**  
Upstream has bundled plugins/skills, broader prompt-product packaging, and more productized extensibility surfaces.

**Local implementation**  
The Python repo now has a coherent local architecture for:

- builtin plus project-local declarative plugins
- builtin, project-local, and plugin-contributed skills
- grouped project-context inspection and reload-state reporting
- structured metadata shared across REPL, TUI, stdio, and remote

**Depth verdict**  
`Functional substitute, shallower mechanism`

**Remaining gap**  
Remaining difference is ecosystem/distribution breadth rather than a missing local extensibility architecture.

Detailed skill runtime depth, including `/.claude/skills`, `/<skill-name>`, the local `skill` tool, and the remaining `paths` / richer-frontmatter / broader skill-runtime gaps, is tracked separately in `SKILL_SOURCE_ALIGNMENT.md`.

### Background agents and detached workflow

**Upstream mechanism**  
Upstream agent surfaces cover broader task/product workflows and additional runtime infrastructure around detached work.

**Local implementation**  
The Python local workflow has:

- background `ask`, `ps`, `logs`, `attach`, `kill`
- continuation-state classification
- runtime-derived progress summaries
- follow-up steering and handoff notifications
- builtin plus project-local `/agents` inspection

**Depth verdict**  
`Functional substitute, shallower mechanism`

**Remaining gap**  
The remaining difference is broader agent/product breadth more than missing detached local workflow mechanics.

Detailed subagent mechanism-depth alignment, including foreground child-session execution, background agents, result-model gaps, transcript/resume depth, permission propagation, and agent-type/runtime-role breadth, is tracked separately in `SUBAGENT_SOURCE_ALIGNMENT.md`.

### Hosted/remote/product breadth

**Upstream mechanism**  
Upstream includes remote transport breadth, hosted product/account flows, marketplace/distribution layers, and wider shell/product infrastructure.

**Local implementation**  
The Python implementation intentionally stops at local-first runtime and attachable local remote control.

**Depth verdict**  
`Upstream-only breadth`

**Remaining gap**  
This should not be treated as a core local runtime deficit unless the project target changes away from local-first reproduction.

## What Is Actually Closed Locally

The following lines are substantively closed for the current local scope:

- **Runtime budget / recovery / runtime-progress line**  
  Shared runtime budget state, prompt-too-long compact-retry recovery, and structured runtime-progress consumers are implemented. Remaining difference is mostly upstream runtime breadth.

- **Prompt-prefix / provider-view assembly line**  
  Prompt blocks, tool-schema cache, deterministic prefix signatures, replacement/artifact indirection, and balanced microcompact are implemented. Remaining difference is mainly provider-native cache-control semantics.

- **Memory / history / rewind / compact lifecycle**  
  Transcript persistence, rewind boundaries, compact preview/apply, replacement-aware compaction narrative, and resume semantics are implemented. Remaining difference is broader upstream history/product breadth.

- **Plugin / skills / project-context local architecture**  
  Builtin plus project-local declarative extensibility is coherent and shared across local consumers. Remaining difference is packaging, marketplace, and broader product breadth.

- **Session architecture ownership cleanup**  
  `Session` now acts mainly as a facade/coordinator with explicit collaborators for the major runtime/history/project-context/background slices. Remaining follow-up is narrow cleanup only.

These lines should no longer be treated as unresolved core parity deficits in local-first planning.

## What Remains Upstream-Only

### Remaining local mechanism gaps worth pursuing

- deeper provider-native cache-control semantics
- finer provider-view prefix planner behavior
- continued TUI/workflow consumption polish of the already-implemented structured state

These are the remaining mechanism-depth opportunities that still meaningfully improve local source-depth alignment.

### Broader upstream breadth not worth treating as a core local deficit

- broader hosted/remote transport matrix
- provider-native cloud/account/product integrations
- wider marketplace/distribution/telemetry infrastructure
- broader UI/shell breadth beyond current local-first scope

These differences are real, but they should be tracked as upstream product/runtime breadth rather than as missing local runtime core mechanics.

## Implications for Next Work

1. Do not reopen already-closed local runtime lines just because upstream breadth is larger.  
   Budget/recovery, prompt-prefix preservation, history/rewind lifecycle, and local extensibility are already structurally real.

2. Treat remaining runtime work as selective mechanism follow-up, not another broad parity drive.  
   The highest-value runtime follow-up is deeper provider-native cache behavior and finer prefix planning semantics.

3. Prefer workflow/TUI consumption improvements over broader hosted/product emulation.  
   The repo already has rich structured state; consuming it better is now more credible than widening transport or marketplace/product breadth.

4. Keep `PARITY_MATRIX.md` high-level.  
   Use this document for source-depth judgments, and keep the matrix focused on decision-oriented parity tracking.
