# Plugin Framework Plan

This document started as the next-stage implementation plan for the remaining
local plugin-framework parity gap after the history/rewind, local agents,
status dashboard, and workspace/context lines reached a stable local baseline.

For the current local scope, the plan below now serves primarily as:

- a completed implementation record for the local plugin workflow
- a reference for shared plugin vocabulary, contribution resolution,
  diagnostics, reload-state reporting, and structured metadata
- a scope marker before any future move into broader marketplace/distribution
  breadth

It is intentionally scoped to the local coding-agent goal of this repo. The
target is not hosted plugin-product parity. The target is a stronger local
plugin workflow across:

- REPL
- TUI
- stdio
- remote session proxy

## Current Baseline

Before this plan, the Python implementation already had:

- builtin plugin definitions
- project-local declarative plugins via `.pyclaude/plugins/<name>/plugin.json`
- plugin-backed REPL commands
- project-local plugin contributions for `skills`, `mcp_servers`, and
  config-only hooks
- `/plugins`, `/plugin`, and `/project-context plugins`
- enable/disable overrides stored in session state

Important current references:

- [PARITY_MATRIX.md](./PARITY_MATRIX.md)
- [STATUS_SURFACE_PLAN.md](./STATUS_SURFACE_PLAN.md)
- [WORKSPACE_CONTEXT_PLAN.md](./WORKSPACE_CONTEXT_PLAN.md)
- [claudecode_py/plugins/registry.py](./claudecode_py/plugins/registry.py)
- [claudecode_py/plugins/loader.py](./claudecode_py/plugins/loader.py)
- [claudecode_py/session.py](./claudecode_py/session.py)
- [claudecode_py/service/stdio.py](./claudecode_py/service/stdio.py)
- [claudecode_py/remote_session.py](./claudecode_py/remote_session.py)
- [claudecode_py/tui/state.py](./claudecode_py/tui/state.py)

## Current Read

The local plugin gap is no longer "can plugins load at all?" The remaining gap
was that plugin state and lifecycle details were still described differently
across command surfaces.

The completed local-scope work in this file focused on:

1. making `/plugins` the canonical plugin overview surface
2. making `/plugin show <name>` the canonical per-plugin detail surface
3. aligning `/project-context plugins`, `/config plugins`, `/status`, stdio,
   remote, and TUI to the same plugin vocabulary
4. making contribution resolution, diagnostics, reload-state deltas, and
   manual overrides explicit instead of implicit
5. exposing a structured `plugin_surface` payload for headless callers

This means the local plugin parity work is now mostly about inspection,
cohesion, and metadata reuse, not about adding executable plugin code or a
hosted plugin ecosystem.

## Problem Statement

The original gap was not missing plugin primitives. It was fragmentation:

- different wording across plugin-related surfaces
- weak detail around contribution types and conflict handling
- reload-state reporting that was present but not part of one coherent story
- no shared structured payload for stdio/remote/TUI callers

The completed work therefore prioritized:

- vocabulary
- diagnostics
- lifecycle clarity
- structured metadata
- dashboard integration

not marketplace/product breadth.

## Scope Boundary

This plan is intentionally limited to:

- builtin plugins
- project-local declarative external plugins
- session-local manual enable/disable overrides
- local inspection and lifecycle reporting

It does **not** attempt to reproduce:

- hosted marketplace/discovery/install flows
- arbitrary Python plugin code loading
- user-level/global plugin registries
- remote plugin distribution/product surfaces

## Implemented Phases

### Phase 1: Plugin Vocabulary and Surface Alignment
Status: Completed for current local scope

Completed outcomes:

- `/plugins`, `/plugin show`, `/project-context plugins`, `/config plugins`,
  `/status`, stdio/remote, and TUI now use the same core vocabulary:
  - `plugin registry`
  - `plugin source`
  - `plugin status`
  - `plugin contributions`
  - `plugin diagnostics`
  - `plugin reload state`
  - `manual plugin overrides`
- `/plugins` now acts as the canonical plugin overview surface.
- `/plugin show <name>` now acts as the canonical per-plugin detail surface.

### Phase 2: Contribution and Diagnostic Depth
Status: Completed for current local scope

Completed outcomes:

- per-plugin surfaces now expose:
  - contribution summary by type
  - default enablement state
  - effective enablement state
  - manual override state
  - per-contribution counts and names
- conflict policy is explicit:
  - builtin and project-local plugins must have unique names
  - same-name conflicts become diagnostics
  - builtin remains effective when a project-local plugin conflicts by name
- diagnostics now render consistently with:
  - `name`
  - `source`
  - `path`
  - `error`
  - recovery-oriented next actions

### Phase 3: Reload and Lifecycle Cohesion
Status: Completed for current local scope

Completed outcomes:

- plugin reload reporting now tracks and surfaces:
  - registry membership changes
  - enabled-set changes
  - diagnostic changes
  - contribution-set changes
- the same plugin reload story now appears through:
  - `/project-context reload-status`
  - `/project-context plugins`
  - `/status`
  - `/config plugins`
- saved-session and resumed-session flows continue to preserve manual
  enable/disable overrides while reloading the current workspace registry.

### Phase 4: Structured Plugin Payloads for Stdio / Remote
Status: Completed for current local scope

Completed outcomes:

- `Session.plugin_surface_payload()` now provides a shared structured plugin
  payload.
- stdio `session.describe` and `session.list_open` now expose `plugin_surface`.
- `RemoteSessionProxy` now caches and exposes the same data through
  `plugin_surface_payload()`.
- structured payload fields include:
  - registry summary
  - enabled/disabled counts
  - manual override counts
  - per-plugin contribution summaries
  - diagnostics summary
  - reload-state summary
  - plugin action groups

### Phase 5: TUI Plugin Workflow Depth
Status: Completed for current local scope

Completed outcomes:

- the TUI status/dashboard now includes a dedicated plugin block driven by the
  structured plugin payload
- the TUI now shows:
  - plugin registry summary
  - diagnostics summary
  - manual override summary
  - selected plugin summary
  - high-signal plugin actions

This remains an inspection-first TUI surface, not a plugin editor.

### Phase 6: Final Local Plugin Workflow Polish
Status: Completed for current local scope

Completed outcomes:

- duplicated plugin summary assembly was reduced in favor of shared registry
  payloads and shared plugin wording
- `/status` remains the top-level session dashboard
- `/plugins` remains the plugin drill-down surface
- `/project-context plugins` remains the project-context-focused plugin view
- the remaining parity gap is explicitly documented as
  marketplace/distribution breadth that is out of scope for this local plugin
  framework

## Resulting Local Plugin Model

For the completed local scope, the Python implementation now provides:

- builtin + project-local declarative plugin sources
- explicit plugin registry summaries and diagnostics
- explicit contribution-type inspection
- explicit same-name conflict handling
- shared reload-state reporting
- shared structured plugin metadata for headless callers
- shared TUI/REPL/remote vocabulary

That closes the most valuable local plugin-framework gap without expanding into
hosted marketplace or executable plugin-code scope.

## Remaining Gap Beyond This Plan

If plugin parity is revisited later, the remaining worthwhile steps are outside
the completed local scope in this file:

1. user/global plugin registries as a third source class
2. richer per-source precedence views if additional source classes exist
3. broader install/discovery/product workflows

These are separate scope expansions, not small continuations of the completed
local plugin plan recorded here.
