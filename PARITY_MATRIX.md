# Upstream-to-Python Parity Matrix

This document tracks parity between the upstream Claude Code source tree in `../package/src-extracted/src` and the current Python reimplementation in this directory.

It is intentionally scoped to the local coding-agent goal of this project.

For mechanism-depth comparison against the upstream source implementation, see `UPSTREAM_SOURCE_ALIGNMENT.md`. This matrix stays high-level and decision-oriented.

## Status Legend

- `Implemented`: usable local equivalent exists in the Python implementation
- `Partial`: some local equivalent exists, but scope or behavior is narrower than upstream
- `Out of Scope`: intentionally not part of the local-tool-first reproduction target

## Scope Rule

This matrix does not treat every upstream command as a required target.

The project target is:

- local prompt -> tool -> response workflow
- planning/advisor/task/workspace surfaces
- MCP and local extensibility
- local and remote session control for coding work

The project is not targeting full hosted product parity.

## Command Surface Matrix

| Upstream surface | Upstream source | Python equivalent | Status | Notes |
|---|---|---|---|---|
| Core interactive CLI | `src/entrypoints/cli.tsx`, `src/cli/*` | `claudecode_py/cli.py` with `ask`, `repl`, `tui` | Implemented | Main local entrypoints exist and are test-covered |
| Resume / saved sessions | `src/commands/resume/*`, `src/commands/session/*` | `sessions`, transcript restore, REPL/session resume | Implemented | Saved-session resume, latest-session restore, and live-vs-saved continuation semantics are implemented for the local workflow |
| Conversation clear / reset | `src/commands/clear/*`, `src/commands/rewind/*`, `src/commands/compact/*` | `/clear`, `/compact`, transcript/history reset, undo/redo change stack | Partial | Local reset/compaction is now much stronger, with scoped `/clear [history|changes|symbol|plan|session]`, manual `/compact [status|preview|<instruction>]`, rewindable history boundaries, preview-before-apply `/rewind`, unified history lifecycle wording across `/history`/`/status`/`/sessions show`, TUI rewind selection, and structured stdio/remote rewind payloads; the remaining gap is broader upstream rewind/history product UX rather than missing local lifecycle depth |
| Config / model / status | `src/commands/config/*`, `src/commands/model/*`, `src/commands/status/*` | `/config`, `/model`, `/history`, `/sessions`, `/status` | Partial | Local session-state inspection is now much stronger, with config/model slices, filtered history views, saved-session detail, a dedicated `/status` dashboard, structured `status_*` payloads for stdio/remote, TUI dashboard depth, runtime-health integration, and shared status action routing; upstream product/status breadth is still broader |
| Permissions | `src/commands/permissions/*` | `/permissions` | Implemented | Local rule management and persistence are present |
| Memory / skills | `src/commands/memory/*`, `src/commands/skills/*` | `/memory`, `/skills`, `/skills-enable`, `/skills-disable`, `/skills-reload`, `/project-context memory|skills|reload-status` | Implemented | Local memory/skill loading is in scope and now has grouped REPL/headless inspection plus session-local reload-status tracking |
| Plugin management | `src/commands/plugin/*`, `src/plugins/*` | `/plugins`, `/plugin`, `/project-context plugins`, built-in plus project-local declarative external plugins | Partial | Local plugin management now has a shared registry vocabulary across `/plugins`, `/plugin show`, `/project-context plugins`, `/config plugins`, `/status`, stdio/remote, and TUI, with explicit contribution summaries, same-name conflict diagnostics, reload-state reporting, manual override inspection, and structured `plugin_surface` metadata; the remaining gap is marketplace/discovery/distribution breadth rather than missing local plugin workflow depth |
| MCP management | `src/commands/mcp/*`, `src/cli/handlers/mcp.tsx` | `/mcp`, `/mcp-tools`, `/mcp-refresh`, `/mcp-reconnect`, `/mcp-call`, `/mcp-verify` | Implemented | Strong local parity for configured MCP servers and tool diagnostics |
| Planning / advisor | `src/commands/plan/*`, `src/commands/advisor.ts` | `/plan`, `/planning`, `/advisor` | Implemented | Mode-centric `/plan` runtime, session plan files, Enter/Exit plan-mode tools, Plan Mode V2 workflow attachments, builtin `Explore` / `Plan` planning agents, and secondary `/planning` artifact/timeline/replay/audit surfaces are all present for the local-first scope |
| Deep planning / review flows | `src/commands/ultraplan.tsx`, `review.ts`, `security-review.ts`, `commit.ts`, `init.ts`, `insights.ts`, `install.tsx` | `/ultraplan`, `/review`, `/security-review`, `/commit`, `/init`, `/insights`, `/install` | Implemented | Present as prompt-driven/built-in plugin commands |
| Tasks | `src/commands/tasks/*` | `/tasks`, `/task` | Implemented | Local task/checklist/task-detail surfaces are strong |
| Workspace / context / files | `src/commands/context/*`, `files/*`, `add-dir/*`, `diff/*` | `/context`, `/add-dir`, `/project-context`, `/files`, `/diff`, `/workspaces`, `/changes`, structured headless IDE/symbol actions | Partial | Local workspace, file-scope, and project-context inspection are strong, including a shared focused-file and session-level working-set model across `Changes`, `Task Detail`, `Active Plan`, and `Status`, runtime-aligned REPL/headless `/context` usage inspection, explicit local context curation through `/add-dir`, compact `/files` and `/diff` entry surfaces, `/project-context` memory/skills/plugins inspection, `/changes` drill-down, and `/workspaces current/show` detail views; remaining gap is broader upstream command/UI breadth |
| Symbol / IDE navigation | `src/commands/ide/*`, `context/*` | `locate-symbol`, `references`, `open-file`, `open-symbol`, `diff-targets`, `reference-targets`, `symbol-actions`, `/symbol` | Implemented | Python side adds strong headless/local navigation surfaces |
| Background agents / agents UI | `src/commands/agents/*`, `src/cli/handlers/agents.ts` | background `ask`, `ps`, `logs`, `attach`, `kill`, task manager | Partial | Local background-session control is now much stronger, with `ps` list/detail, `logs` header/summary views, continuation-state classification, grouped next actions, follow-up/queued-message steering, main-session handoff notifications, runtime-grade progress metadata, and builtin-plus-project-local `/agents` definition inspection; upstream agent/product surfaces are still broader |
| Remote / bridge | `src/commands/bridge/*`, `src/remote/*`, `src/cli/remoteIO.ts` | `serve-stdio`, `serve-bridge`, remote attach, bridge approval flow | Partial | Local remote attach exists via stdio/TCP bridge and is usable for local workflows, but hosted/upstream transport breadth is intentionally not the priority |
| Theme / output style / keybindings / vim | `src/commands/theme/*`, `output-style/*`, `keybindings/*`, `vim/*` | minimal TUI only | Out of Scope | Not central to the local coding-agent reproduction goal |
| Voice / mobile / desktop / chrome | `src/commands/voice/*`, `mobile/*`, `desktop/*`, `chrome/*` | none | Out of Scope | Product/distribution/device surfaces |
| Login / logout / oauth-refresh | `src/commands/login/*`, `logout/*`, `oauth-refresh/*` | none | Out of Scope | Hosted auth/account flows are intentionally excluded |
| Usage / rate limits / privacy / extra usage | `src/commands/usage/*`, `rate-limit-options/*`, `privacy-settings/*`, `extra-usage/*` | none | Out of Scope | Hosted product/account/commercial surface |
| GitHub / Slack app install | `src/commands/install-github-app/*`, `install-slack-app/*` | none | Out of Scope | Integration-install product flows are not a local runtime priority |
| Share / feedback / release-notes / upgrade | `src/commands/share/*`, `feedback/*`, `release-notes/*`, `upgrade/*` | none | Out of Scope | Product/distribution/feedback surface, not local runtime core |
| Internal / diagnostics / one-off maintenance commands | `backfill-sessions`, `heapdump`, `ctx_viz`, `debug-tool-call`, `mock-limits`, `perf-issue`, `summary`, `teleport`, etc. | none or ad hoc local diagnostics | Out of Scope | Not meaningful parity targets for this repo goal |

## Runtime and Subsystem Matrix

| Upstream subsystem | Upstream source area | Python equivalent | Status | Notes |
|---|---|---|---|---|
| Session runtime and message loop | `src/query/*`, `src/context/*`, `src/state/*`, `src/tools/*` | `claudecode_py/runtime/*`, `session.py`, `tools/*` | Implemented | This is one of the strongest parts of the Python port, now including a shared runtime budget state, compact-retry recovery for prompt-too-long failures, unified budget/progress narratives across `/context` and `/status`, richer runtime-progress summaries for local consumers, and a provider-view prompt-prefix planner with deterministic prefix signatures and reduction tiers |
| Tool orchestration and approvals | `src/tools/*`, permission/context layers | `runtime/context.py`, `runtime/query_loop.py`, tool set, permission manager | Implemented | Local tool-call loop and approval handling are in place, with richer lifecycle events for batches, approval wait, result summaries, budget pressure, and compact recovery now flowing through stdio/remote/TUI and background progress surfaces |
| Transcript / persistence | session/state/storage layers | `storage/transcript.py`, session persistence, checklist/task storage | Implemented | Local persistence is strong and test-covered |
| Provider abstraction | model/provider layers | `providers/anthropic.py`, `providers/openai_compatible.py` | Implemented | Practical local coverage is present |
| MCP transport/client/registry | `src/commands/mcp/*`, CLI handlers, services | `mcp/*` | Implemented | Strong local parity for configured MCP server use |
| Remote transport stack | `src/remote/*`, websocket/SSE/hybrid transports | `service/stdio.py`, `service/bridge.py`, remote session proxy | Partial | Local remote control exists, but upstream transport matrix is broader |
| TUI / terminal UI | `src/components/*`, `src/ink/*`, screens/keybindings | `tui/*` | Partial | Rich local TUI exists with focused-file navigation, file-context legends, a session-level `Working Set`, and connected `Changes` / `Task Detail` / `Active Plan` workflow; remaining gap is broader upstream UI/product breadth |
| Plugin framework | `src/plugins/*`, plugin command surfaces | `plugins/*` | Partial | Built-in and project-local declarative external plugins exist, with clearer local contribution and reload inspection; the remaining gap is marketplace/distribution/ecosystem breadth rather than absence of external plugins |
| Skills / bundled command prompts | `src/skills/*`, built-in plugin skills | `skills/*`, built-in prompt plugins | Partial | Local skill loading now has a coherent local workflow across REPL/TUI/stdio/remote: shared skill-registry vocabulary, explicit skill source/status/auto-enable/manual-override wording, same-name cross-source conflict diagnostics that keep the effective skill stable in prompt composition, stronger reload lifecycle reporting, structured `skills_surface` payloads, prompt-composition inspection, and a dedicated TUI skill-registry block; the remaining gap is broader upstream prompt-product breadth and packaging/distribution breadth |
| Workspace isolation | repo/workspace/task support layers | `workspace/isolation.py`, workspace audit/repair flows | Implemented | Python side is strong here and arguably more explicit in diagnostics |
| Symbol indexing / navigation | IDE/context/navigation surfaces | `indexing/*`, IDE action commands, `/symbol` | Implemented | Local parity is strong for Python/JS/TS repository navigation |
| Session continuity and resume UX | session restore / resume / attach surfaces | `session_factory.py`, `storage/transcript.py`, `service/stdio.py`, CLI resume/attach flow | Implemented | Saved-session restore, latest-session restore, workspace fallback handling, and live-vs-saved continuation semantics are implemented and aligned across CLI/service surfaces |
| Workspace / file-context ergonomics | local context, diff, and file-navigation surfaces | `session.py`, `session_components/*`, `tui/*` focused file-context and working-set workflow | Partial | Focused-file navigation and session-level working-set cohesion across change/task/plan/status surfaces are implemented, and REPL/headless `/files`, `/add-dir`, `/diff`, `/changes`, and `/workspaces` now expose deeper inspection and explicit context curation on top of the same local model while `/context` covers usage inspection; remaining work is additive workflow polish rather than basic capability gaps |
| Session architecture ownership | implicit session/state ownership across upstream modules | `session.py` plus `session_components/*` collaborators for workspace, task detail, symbol surface, advisor, plan, runtime state, history/memory, project-context, and background runtime | Implemented | `Session` now acts primarily as the facade/coordinator while the main runtime, history/memory, project-context, and background-progress slices have explicit owners; remaining work is narrow residue cleanup rather than a major ownership gap |

## Current Read of Overall Parity

### Already Strong

- local interactive runtime
- tool loop and approval handling
- planning/advisor/task surfaces
- workspace isolation and repair
- MCP integration
- symbol/index/navigation tooling
- detached background sessions and saved-session resume
- background-session inspection depth with `ps` detail, `logs` summary/tail, and explicit `live attachable` vs `saved resumable` vs `inactive only` continuation semantics
- focused local TUI workflow across `Changes`, `Task Detail`, `Active Plan`, and `Status`
- shared runtime budget, compact-retry recovery, and runtime-progress narratives across `/context`, `/status`, stdio/remote metadata, background progress, and TUI
- session-level working-set cohesion with in-scope reasoning and modified-vs-context-only signals
- dedicated `/context` runtime context-usage inspection above the shared prompt/tool chain
- explicit `/add-dir` curation of session-local context paths, persisted with saved sessions and folded back into the shared working-set model
- dedicated `/project-context` inspection for project memory, grouped skill state, plugin contributions, and latest reload outcome
- explicit `/files` and `/diff` entry surfaces for compact working-set and diff-backed inspection
- local REPL/headless inspection depth for `/changes`, `/workspaces`, `/history`, `/sessions`, `/config`, `/model`, `/status`, and manual `/compact` state

### Present but Narrower Than Upstream

- plugin system
- remote transport matrix
- TUI/product shell breadth outside the local workflow
- remaining file/context/diff breadth outside the now-implemented focused-file, working-set, `/context` usage, `/files`, and `/diff` workflow
- remaining local workflow polish beyond the current working-set, `/context` usage, `/files`, and `/diff` model
- broader hosted transport/runtime breadth and provider-native cache-control/product infrastructure beyond the now-complete local runtime budget/progress and prompt-prefix lines

### Explicitly Not Targeted

- hosted auth/account/commercial flows
- desktop/mobile/voice/product delivery features
- marketplace/distribution-heavy plugin flows
- social/product features such as share, release, and app-install workflows
- internal Anthropic maintenance or debugging commands

## Recommended Next Work Based on This Matrix

1. Continue using the `Partial` rows as the local-agent backlog.
   Focus on the remaining local workflow depth and ergonomics gaps, not on already-completed core milestones such as project-local plugin loading, session continuity, or focused-file navigation.

2. Keep `Out of Scope` rows out of the implementation backlog unless the local-tool goal changes.
   This avoids churn toward hosted product parity.

3. Track future work in two local-first buckets:
   - local workflow and command-surface refinement
   - targeted runtime/maintainability follow-up

4. Revisit this matrix after each substantial milestone.
   It should stay decision-oriented, not become a changelog dump.
