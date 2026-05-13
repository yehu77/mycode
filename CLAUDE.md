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
- unified `/project-context` inspection for project memory, grouped skill state, plugin contributions, and session-local reload status
- focused file-context navigation across `Changes`, `Task Detail`, `Active Plan`, and `Status`
- derived session-level `Working Set` summary with in-scope reasons and modified-vs-context-only classification
- a dedicated `/context` REPL/headless surface for runtime-aligned context-usage inspection
- explicit `/files` and `/diff` REPL/headless surfaces for compact working-set inspection, focused-file context, and diff-backed work inspection
- TUI-local workflow integration around change/file/diff navigation with `Ctrl+Left/Right` and `F9/F10`
- deeper REPL/headless inspection for `/changes` and `/workspaces`, including change/file drill-down and detailed workspace inventory views
- deeper REPL/headless inspection for `/history`, `/sessions`, `/config`, and `/model`, including saved-session detail, session-state slices, and workspace/resume-focused views
- deeper REPL/headless overview through `/status [summary|workspace|workflow|resume]`
- materially decomposed `session.py` ownership across `workspace`, `task_detail`, `symbol_surface`, `advisor`, and `plan`

Operationally, this is beyond skeleton status. It is already a usable local coding-agent environment with a coherent local TUI workflow, not just raw session and tool plumbing.

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

- more depth in local coding-workflow navigation where upstream parity still matters beyond the now-implemented TUI and REPL inspection surfaces
- targeted ergonomics gaps beyond the current working-set and focused-file model
- smaller maintainability cleanups now that the largest `session.py` responsibilities have dedicated owners

## Current Repo Health

- Full test suite currently passes: `575 passed`
- Test collection is stabilized with `pytest.ini`
- Transient artifacts such as `pytest-cache-files-*`, `tests/_tmp*`, and test-local `.pyclaude` residue are excluded from collection
- Current documentation reflects the local-tool-first scope instead of implying full product parity
- Detailed upstream-to-Python parity tracking lives in `PARITY_MATRIX.md`

## Next-Stage Roadmap

1. Maintain repo and test stability.
   Keep full-suite green status as the baseline and prevent transient artifacts from polluting collection.

2. Continue local coding-workflow depth where parity still matters.
   Improve the remaining local ergonomics after the now-implemented TUI workflow, scoped `/clear`, manual `/compact`, explicit `/add-dir`, the deeper REPL `/changes`, `/workspaces`, `/history`, `/sessions`, `/config`, `/model`, `/status`, `/context`, `/project-context`, `/files`, and `/diff` surfaces, and the new background-session `ps/logs` inspection depth.

3. Refine parity tracking for upstream local-agent-relevant surfaces.
   Keep `PARITY_MATRIX.md` current so implementation work stays tied to the local coding-agent target rather than drifting toward product breadth.

4. Do only small, targeted maintainability follow-ups.
   Prefer narrow residue cleanup and ownership tightening over another large refactor wave unless a concrete parity blocker appears.

## Working Principle

When there is a tradeoff between product-surface breadth and local coding-agent depth, prefer the local coding-agent path.
