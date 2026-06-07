# Python ClaudeCode Status

This file is the working engineering ledger for the Python ClaudeCode reproduction effort.

## Goal

Build and maintain a locally usable coding agent in Python that reproduces the core Claude Code local workflow:

- prompt -> model -> tool loop
- planning, read-only scout flows, and execution review surfaces
- task and checklist tracking
- workspace isolation, repair, and cleanup
- MCP integration
- session persistence, saved resume, and live background attach

The goal is not full hosted product parity.

## Current Implementation Coverage

The current Python implementation already covers the main local runtime shape:

- CLI, REPL, and optional TUI entrypoints
- background sessions with `ps`, `logs`, `attach`, and `kill`
- background-session inspection depth with `ps <id>` detail and `logs <id> [summary|tail]`, including continuation-state and next-action grouping
- shared session runtime, transcript persistence, and saved-session resume
- tool-calling query loop with approvals, change tracking, and undo/redo surfaces
- scoped local reset through `/clear [history|changes|symbol|plan|session]`
- manual conversation compaction through `/compact [status|preview]` on top of the shared `context_summary` path
- explicit context-path curation through `/add-dir` plus explicit-vs-automatic `/files` slices on top of the shared working-set model
- Anthropic and OpenAI-compatible provider paths
- MCP registration, discovery, tool exposure, stdio service, and TCP bridge
- planning, advisor, task, workspace, symbol, and change-inspection surfaces
- project-local external plugin loading via `.pyclaude/plugins/<name>/plugin.json`
- shared local plugin workflow vocabulary across `/plugins`, `/plugin show`, `/project-context plugins`, `/config plugins`, `/status`, stdio/remote, and TUI
- unified `/project-context` inspection for project memory, grouped skill state, plugin contributions, and session-local reload status
- a completed local skills workflow with shared skill-registry vocabulary, explicit source/resolution diagnostics, reload-state reporting, prompt-composition inspection, structured stdio/remote payloads, and TUI skill-registry depth
- focused file-context navigation across `Changes`, `Task Detail`, `Active Plan`, and `Status`
- derived session-level `Working Set` summary with in-scope reasons and modified-vs-context-only classification
- a dedicated `/context` REPL/headless surface for runtime-aligned context-usage inspection
- explicit `/files` and `/diff` REPL/headless surfaces for compact working-set inspection, focused-file context, and diff-backed work inspection
- TUI-local workflow integration around change/file/diff navigation with `Ctrl+Left/Right` and `F9/F10`
- deeper REPL/headless inspection for `/changes` and `/workspaces`, including change/file drill-down and detailed workspace inventory views
- deeper REPL/headless inspection for `/history`, `/sessions`, `/config`, and `/model`, including saved-session detail, session-state slices, and workspace/resume-focused views
- deeper REPL/headless overview through `/status [summary|workspace|workflow|resume]`
- materially decomposed `session.py` ownership across `workspace`, `task_detail`, `symbol_surface`, `advisor`, and `plan`
- the remaining deep ownership slices in `session.py` now also have dedicated collaborators for runtime state, history/memory, project-context, and background runtime progress
- shared runtime budget state, prompt-too-long compact-retry recovery, and richer runtime-progress events/consumers across REPL, TUI, stdio, remote, and background progress surfaces
- provider-view prompt assembly with prompt blocks, tool-schema caching, deterministic prefix signatures, reduction tiers, replacement/artifact indirection, and replacement-aware microcompact

Operationally, this is beyond skeleton status. It is already a usable local coding-agent environment with a coherent local TUI workflow, not just raw session and tool plumbing.

## Completed Milestones

### Memory / Clear / Compact / Rewind / Resume

- Implemented: local memory lifecycle now covers scoped `/clear`, manual `/compact`, rewindable history boundaries, preview-before-apply `/rewind`, and aligned resume semantics across REPL, TUI, stdio, and remote.
- Current Boundary: the local workflow now has strong memory/history lifecycle depth without depending on hosted session-memory infrastructure.
- Remaining Gap: broader upstream rewind/history product UX and deeper hosted memory machinery remain out of local-first scope.

### Status Surface and Structured Status Payloads

- Implemented: `/status` is now the shared session dashboard with summary/workspace/workflow/resume slices, structured `status_*` payloads, runtime-health summaries, and shared action routing across local consumers.
- Current Boundary: local status inspection is coherent across REPL, TUI, stdio, and remote without expanding into hosted product dashboards.
- Remaining Gap: broader upstream status/product breadth remains outside the local-first target.

### Workspace / Context / Files Workflow

- Implemented: the repo now has a shared working-set and focused-file model across `Changes`, `Task Detail`, `Active Plan`, and `Status`, plus runtime-aligned `/context`, explicit `/add-dir`, compact `/files`, `/diff`, `/changes`, and `/workspaces` surfaces.
- Current Boundary: workspace/context/file inspection is deep for local coding workflows and coherent across REPL, TUI, stdio, and remote.
- Remaining Gap: the remaining gap is additive workflow polish and broader upstream command/UI breadth, not missing local file/workspace mechanics.

### Local Plugin Framework

- Implemented: local plugin workflow now has a shared registry vocabulary across `/plugins`, `/plugin show`, `/project-context plugins`, `/config plugins`, `/status`, stdio/remote, and TUI with contribution summaries, diagnostics, reload-state reporting, and manual override inspection.
- Current Boundary: built-in plus project-local declarative plugins are fully part of the local workflow.
- Remaining Gap: marketplace/discovery/distribution breadth remains out of scope.

### Skills / Bundled Prompts Workflow

- Implemented: local skills now have shared registry/source/status/reload/prompt-composition inspection across REPL, TUI, stdio, and remote, including conflict diagnostics and dedicated TUI skill-registry depth.
- Current Boundary: builtin, project-local, and plugin-contributed skills now form one coherent local workflow.
- Remaining Gap: broader upstream prompt-product breadth and packaging/distribution breadth remain out of local-first scope.

### Background Agents / Local Agent Workflow

- Implemented: local background-session control now includes strong `ps`/`logs` inspection, continuation-state classification, follow-up steering, handoff notifications, runtime-grade progress metadata, and builtin-plus-project-local `/agents` definition inspection.
- Current Boundary: local background agents are usable as a strong detached-session workflow without expanding into hosted multi-actor product surfaces.
- Remaining Gap: broader agent/product shell breadth remains outside the current target.

### Core Runtime Budget / Recovery / Runtime Progress

- Implemented: the main query loop now has a shared runtime budget state, compact-retry recovery for prompt-too-long failures, unified budget and compact-lifecycle narratives, and richer runtime-progress events and summaries for local consumers.
- Current Boundary: local runtime budgeting, recovery, and progress reporting are completed for the current provider-view workflow.
- Remaining Gap: broader hosted/runtime transport breadth and provider-native cache behavior remain outside the now-complete local runtime line.

### Prompt-Prefix / Provider-View Assembly

- Implemented: provider-view prompt assembly now uses prompt blocks, tool-schema caching, deterministic prefix signatures, explicit reduction tiers, and replacement/artifact/microcompact-aware prompt-prefix summaries across `/context`, `/status`, TUI, stdio, and remote.
- Current Boundary: local prompt-prefix preservation and diagnostics are implemented without changing provider wire shape or adding hosted cache infrastructure.
- Remaining Gap: provider-native cache-control wire behavior and broader hosted cache/runtime breadth remain outside the local-first scope.

### Session Architecture Ownership Cleanup

- Implemented: `Session` now acts primarily as a facade/coordinator while workspace, task-detail, symbol, advisor, plan, runtime-state, history/memory, project-context, and background-runtime slices have explicit collaborators.
- Current Boundary: the main local ownership gap in `session.py` is closed through the existing delegate pattern rather than a second architecture style.
- Remaining Gap: only narrow residue cleanup remains; this is no longer a major active backlog line.

## Known Gaps and Boundaries

### Intentional Non-Goals

- hosted login/logout/account/subscription flows
- usage billing, product distribution, desktop/mobile product surfaces
- strict one-to-one parity with every upstream command branch

### Implemented With Bounded Scope

- project-local external plugin loading via `.pyclaude/plugins/<name>/plugin.json`
- declarative external plugin support for `skills`, `mcp_servers`, metadata, and config-only `hooks`
- remote attach flows that exist to support local workflows, but are not the current primary product direction

### Remaining Local-Tool Gaps

- smaller maintainability cleanups around the now-deeper runtime, prompt-prefix, and surface helpers
- additive TUI workflow polish beyond the already-implemented local dashboard and inspection depth
- selective, capability-gated experiments only if we later choose to get closer to provider-native cache-control/runtime breadth without widening the default local-first scope

## Current Repo Health

- Full test suite currently passes: `604 passed, 1 skipped`
- Test collection is stabilized with `pytest.ini`
- Transient artifacts such as `pytest-cache-files-*`, `tests/_tmp*`, and test-local `.pyclaude` residue are excluded from collection
- Current documentation reflects the local-tool-first scope instead of implying full product parity
- Detailed upstream-to-Python parity tracking lives in `PARITY_MATRIX.md`
- Source-depth alignment to the upstream implementation is tracked separately in `UPSTREAM_SOURCE_ALIGNMENT.md`

## Next-Stage Roadmap

1. Maintain repo and test stability.
   Keep full-suite green status as the baseline and prevent transient artifacts from polluting collection.

2. Continue local workflow polish where it materially improves the existing tool.
   The most credible next depth line is TUI/local workflow refinement that reuses the current structured payloads instead of expanding hosted/product breadth.

3. Do only narrow maintainability follow-up after the now-complete session-ownership cleanup.
   Prefer small residue cleanup over another large architectural refactor unless a concrete parity blocker appears.

4. Refine parity tracking and only pursue narrower upstream-runtime experiments selectively.
   Keep `PARITY_MATRIX.md` current, and if runtime parity is revisited, prefer small capability-gated prompt/cache behavior experiments over broader hosted transport or product-surface expansion.

## Working Principle

When there is a tradeoff between product-surface breadth and local coding-agent depth, prefer the local coding-agent path.
