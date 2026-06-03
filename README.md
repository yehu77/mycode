# Python ClaudeCode

This directory contains the active Python reimplementation of the local Claude Code workflow, guided by the extracted upstream sources in `../package/src-extracted/src`.

## Scope

This project is aimed at a local developer tool first.

The goal is to reproduce the useful local coding-agent behavior of Claude Code on a Python runtime: interactive prompting, tool orchestration, planning, task tracking, workspace isolation, MCP integration, and local/remote session control.

Hosted product flows are intentionally out of scope unless they are required to support local tool behavior.

## Current Status

- The core local coding-agent runtime is implemented and usable.
- The full Python test suite currently passes: `628 passed`.
- Project-local external plugin loading, session/resume continuity, and session-level working-set/file-context navigation are implemented and validated.
- local plugin workflow depth now includes shared `/plugins` and `/plugin show` vocabulary, explicit contribution and diagnostics inspection, reload-state reporting, structured stdio/remote plugin metadata, and TUI plugin dashboard integration
- `/context` now provides a dedicated REPL/headless context-usage surface aligned with the runtime prompt/tool chain.
- `/project-context` now provides a dedicated REPL/headless project memory / skills / plugins / reload-status inspection surface above the local context workflow.
- `/files` and `/diff` now provide explicit REPL/headless entry surfaces for working-set files, focused file context, and diff-backed local work.
- `/compact` now provides a manual conversation-compaction surface above the existing `context_summary` and auto-compaction path.
- `/add-dir` now provides explicit local context curation on top of the shared working-set/file-context model.
- REPL/headless inspection depth now covers `/history`, `/sessions`, `/config`, `/model`, and `/status` slice views in addition to the deeper `/changes` and `/workspaces` surfaces.
- rewind/history UX now includes structured boundary browsing, preview-before-apply, TUI rewind selection, and aligned stdio/remote rewind metadata.
- background-agent local scope now includes follow-up steering, main-session handoff notifications, runtime-grade progress metadata, and project-local agent definition inspection through `/agents`.
- `/status` now acts as a shared session dashboard across REPL, TUI, stdio, and remote surfaces, including memory lifecycle, background notifications, runtime health, and stable action routing.
- the local skills workflow now includes shared registry/reload/prompt-composition inspection across REPL, TUI, stdio, and remote, plus a dedicated TUI `Skill Registry` block.
- Recent test hygiene work added `pytest.ini` collection guards and excludes transient cache/temp artifacts such as `pytest-cache-files-*`, `tests/_tmp*`, and test-local `.pyclaude` residue.

## Current Capabilities

### CLI, REPL, TUI, and Background Sessions

- `pyclaude ask`, `repl`, and `tui`
- detached sessions with `ask --background`, `ps`, `logs`, `attach`, and `kill`
- background-session inspection depth now includes `ps <id>` detail and `logs <id> [summary|tail]` with continuation-state and next-action guidance
- local slash-command workflow for planning, tasks, workspaces, symbols, plugins, permissions, MCP, and insights
- deeper REPL/headless inspection for `/changes`, `/workspaces`, `/history`, `/sessions`, `/config`, and `/model` without switching to TUI
- deeper REPL/headless context-usage inspection through `/context`
- explicit REPL/headless working-set and diff entry surfaces through `/files [context|working-set|focused|changes|tasks|plan|explicit|auto|show <n>]` and `/diff [summary|focused|working-set|change ...]`
- deeper REPL/headless overview through `/status [summary|workspace|workflow|resume]`
- scoped local reset paths through `/clear [history|changes|symbol|plan|session]`
- manual history compaction through `/compact [status|preview]`
- explicit local context curation through `/add-dir <path>|list|clear|remove <n>`
- shared local session state across REPL, TUI, attach, stdio, and bridge surfaces
- saved-session resume and live background attach with aligned local session metadata
- background session surfaces now classify continuation state as `live attachable`, `saved resumable`, or `inactive only`

### Runtime, Query Loop, and Tool Orchestration

- shared session runtime with transcript persistence
- tool-calling query loop with approval flow and change tracking
- default tool surfaces plus plugin-backed commands
- task/checklist storage and task-detail views
- isolated child/background workspaces with health tracking and cleanup/repair flows
- focused local file-context and change/diff navigation built on the shared `file_context` model
- derived session-level working-set scope with `in scope because`, related-change, diff-hunk, and context-only signals
- a dedicated `/context` surface that estimates current prompt/tool context usage from the real runtime input chain
- compact `/files` and `/diff` surfaces layered on top of the same focused-file and working-set model
- explicit context paths that persist with saved sessions and contribute to the same working-set model with `explicit context path` scope reasoning

### Providers and MCP

- Anthropic provider support
- OpenAI-compatible provider support
- MCP server loading from `.pyclaude/mcp_servers.json`
- MCP tool exposure inside the local runtime
- stdio service and TCP bridge for remote session access
- project-local external plugins via `.pyclaude/plugins/<name>/plugin.json` for declarative `skills` and `mcp_servers`
- unified project-context inspection through `/project-context [summary|memory|skills|plugins|reload-status]`

### Planning, Advisor, Tasks, Workspaces, and Symbols

- `/advisor` final-review and interactive-review modes
- `/ultraplan` read-only multi-scout planning flow
- reusable planning artifacts and active-plan management through `/plan`
- execution/scout task tracking, replay, timeline, and lineage audit views
- `/task`, `/tasks`, `/workspaces`, and `/symbol` local surfaces with TUI/remote visibility
- `Changes`, `Task Detail`, `Active Plan`, and `Status` share one focused-file/working-set model with primary/diff target navigation
- `/changes` now supports stack-filtered summaries, change drill-down, per-file drill-down, and session-level `working set` rendering
- `/workspaces` now supports concise list view plus detailed `current` and `show <label|session-id|all>` inspection surfaces
- `/history` now supports filtered audit views for messages, task activity, workspace audit, and recent changes
- `/sessions` now supports saved-session detail, compact summary, and workspace-focused inspection paths
- `/config` and `/model` now support narrower runtime/workspace/permissions/plugins/MCP/advisor inspection slices instead of only one large dump
- `/status` now provides a compact current-session overview plus `workspace`, `workflow`, and `resume` slices
- `/status` now also exposes a stronger unified dashboard vocabulary, structured `status_*` metadata for stdio/remote, TUI dashboard depth, runtime-health summaries, and shared status action families
- `/context` now provides estimated context-usage summary for system prompt sections, messages, and tool definitions
- `/add-dir` now provides explicit context-path curation, while `/files explicit|auto` exposes explicit-vs-automatic working-set scope directly
- `/clear` now supports scoped local reset for history, changes, symbol surface, active plan, or lightweight session workflow state
- `/project-context` now provides project memory, grouped skill-state, plugin-contribution, and latest reload-status inspection without leaving the REPL
- `/files` now provides a compact file/workingset context surface, while `/diff` provides a compact diff-backed work surface
- `/rewind` and `/history` now provide boundary preview, lineage, compare summaries, and aligned TUI/stdio/remote rewind selection metadata
- `/agents` now provides builtin plus project-local definition inspection with source grouping, same-name shadowing, diagnostics, and effective-resolution summaries

### Remote Attach and Headless Surfaces

- JSON-RPC stdio service for structured session access
- TCP bridge for attach/reattach flows
- bridged approval handling during attached remote operation
- structured symbol and MCP-oriented headless commands
- remote attach exists to support the local workflow, but local single-session usability remains the primary direction

## Built-in Command Surface

Main entrypoints:

- `ask`
- `repl`
- `tui`
- `serve-stdio`
- `serve-bridge`
- `sessions`
- `ps`
- `logs`
- `attach`
- `kill`

Common local slash-command surfaces:

- `/advisor`
- `/plan`
- `/task`
- `/tasks`
- `/workspaces`
- `/symbol`
- `/plugins`
- `/project-context`
- `/files`
- `/diff`
- `/add-dir`
- `/permissions`
- `/mcp`
- `/insights`
- `/review`
- `/commit`
- `/security-review`
- `/init`
- `/install`
- `/ultraplan`

## What This Project Is Not

- It is not a reproduction of hosted auth, account, usage, rate-limit, or subscription flows.
- It is not trying to recreate desktop/mobile distribution or other product-delivery surfaces.
- It is not aiming for one-to-one parity with every upstream command in the extracted TypeScript source tree.

## Install

```bash
cd python_claudecode
python -m venv .venv
. .venv/Scripts/activate
pip install -e .[all]
```

Optional extras:

- `.[anthropic]`
- `.[openai]`
- `.[tui]`
- `.[mcp-remote]`

## Quick Start

Run an interactive REPL:

```bash
pyclaude repl
```

Run a single prompt:

```bash
pyclaude ask "Summarize this repository"
```

Launch and reattach to a background session:

```bash
pyclaude ask "Review the pending changes" --background
pyclaude ps
pyclaude ps <session-id>
pyclaude logs <session-id> summary
pyclaude attach <session-id>
```

Inspect workspaces:

```bash
pyclaude repl
/workspaces list
/workspaces current
/workspaces show <label|session-id|all>
/workspaces cleanup
/workspaces repair <label|session|all>
```

Inspect recorded changes:

```bash
pyclaude repl
/changes
/changes undo
/changes redo
/changes show <index-or-change-id>
/changes show <index-or-change-id> file <n>
/changes working-set
/files
/files show 2
/diff
/diff working-set
``` 

Inspect tasks and symbol state:

```bash
pyclaude repl
/tasks
/symbol actions <name>
/symbol next definition
/symbol next reference
```

Inspect session state and saved-session detail:

```bash
pyclaude repl
/context
/files
/files explicit
/files focused
/add-dir src
/add-dir list
/project-context
/project-context skills
/project-context reload-status
/history
/history workspace
/status
/status workflow
/compact status
/compact preview
/sessions show latest
/sessions show <session-id-prefix> workspace
/config runtime
/model advisor
/clear session
```

Start the TUI:

```bash
pyclaude tui
```

## Local Navigation

The current TUI workflow is built around one shared focused-file and working-set model.

- `Changes`, `Task Detail`, and `Active Plan` all surface the same focused file context
- `Status` exposes the session-level `Working Set`, including why files are in scope and whether they are modified or context-only
- `Ctrl+Left/Right` moves the focused file within the active surface
- `F9` opens the focused primary target
- `F10` opens the focused diff target when available, otherwise falls back to the primary target

Each focused-file block also shows a navigation legend, so you can see the current `F9/F10` target before navigating. The same model now highlights:

- `in scope because`
- `related change`
- `diff hunks`
- `context-only`

The local REPL/headless workflow now exposes the same inspection depth for changes, workspace state, and session-state surfaces:

- `/changes` can filter undo vs redo stacks, drill into a selected change, drill into a selected file inside that change, or render only the session-level `Working set`
- `/files` can render the compact working-set inventory, show only change-backed files, or focus one working-set item directly
- `/diff` can summarize diff-backed work, focus the current diff-oriented file view, show only diff-backed working-set files, or delegate into `/changes show ...`
- `/workspaces current` shows the current session workspace with health, effective/fallback cwd, and primary/secondary/tertiary actions
- `/workspaces show <label|session-id|all>` renders detailed isolated-workspace inventory entries without leaving the REPL
- `/history` can render all recent state together or filter to `messages`, `tasks`, `workspace`, or `changes`
- `/status` can render a compact current-session overview or focus on `workspace`, `workflow`, or `resume`
- `/sessions show latest|<id>` can render saved-session detail, compact summary, or workspace-focused resume metadata
- `/config` can focus on `workspace`, `runtime`, `permissions`, `plugins`, or `mcp`
- `/model advisor` shows the runtime-vs-advisor model relationship directly
- `/clear` can now reset only `history`, `changes`, `symbol`, `plan`, or the lightweight local `session` workflow state instead of forcing one all-or-nothing clear
- `/compact` can manually compact older message history into `context_summary`, preview what would be compacted, or show current compaction status
- `/add-dir` can add, remove, list, and clear explicit context paths, and `/files explicit|auto` shows how that layer affects the current working set
- `/project-context` can summarize current project memory/skills/plugins and show the latest session-local reload outcome after `/context-refresh` or `/skills-reload`

## External Plugin Examples

Project-local external plugins live under:

```text
.pyclaude/plugins/<plugin-name>/plugin.json
```

Minimal example plugins are included in this repository at:

```text
examples/external-plugin/docs/plugin.json
examples/external-plugin/mcp-echo/plugin.json
```

### Skill-Only Example

To try the skill-only example in a workspace:

1. Create `.pyclaude/plugins/docs/` inside your target workspace.
2. Copy `examples/external-plugin/docs/plugin.json` into that directory as `plugin.json`.
3. Start `pyclaude` in the target workspace and reload project context:

```text
pyclaude repl
/skills-reload
/plugins
/plugin show docs
/skills
```

That example defines one auto-enabled skill, so after `/skills-reload` you should see:

- an external plugin named `docs`
- a loaded skill named `docs-style`

Example manifest:

```json
{
  "name": "docs",
  "description": "Example project-local external plugin.",
  "version": "0.1.0",
  "skills": [
    {
      "name": "docs-style",
      "description": "Use stable user-facing terminology in documentation.",
      "content": "Prefer stable user-facing terminology. Keep docs concise, concrete, and implementation-aware.",
      "auto_enable": true,
      "tags": ["docs", "style"]
    }
  ]
}
```

### MCP-Backed Example

To try the MCP-backed example in a workspace:

1. Create `.pyclaude/plugins/mcp-echo/` inside your target workspace.
2. Copy both of these files into that directory:
   - `examples/external-plugin/mcp-echo/plugin.json`
   - `examples/external-plugin/mcp-echo/server.py`
3. Start `pyclaude` in the target workspace.
4. Reload project context and MCP config:

```text
pyclaude repl
/skills-reload
/plugins
/plugin show mcp-echo
/mcp-refresh
/mcp
/mcp-tools
/mcp-call plugin-echo echo_text {"text":"hello"}
```

If the example is loaded correctly, you should see:

- an external plugin named `mcp-echo`
- an MCP server named `plugin-echo`
- an MCP tool named `plugin-echo.echo_text`
- a direct MCP call result containing `echo:hello`

Example manifest:

```json
{
  "name": "mcp-echo",
  "description": "Example project-local external plugin with a local stdio MCP server.",
  "version": "0.1.0",
  "mcp_servers": [
    {
      "name": "plugin-echo",
      "transport": "stdio",
      "command": "python",
      "args": ["server.py"]
    }
  ]
}
```

This example is intended for local testing of plugin-injected MCP loading, not for production deployment.

External plugins are v1 data-only plugins. They can provide skills, MCP server definitions, metadata, and hook names, but they do not load arbitrary Python code.

### External Plugin Manifest Reference

Project-local external plugins use this layout:

```text
.pyclaude/plugins/<plugin-name>/plugin.json
```

Supported top-level manifest fields:

- `name`: plugin name used in `/plugins` and `/plugin show <name>`
- `description`: short plugin description
- `version`: optional version string, defaulting to `0.1.0`
- `skills`: optional list of declarative skill definitions
- `mcp_servers`: optional list of declarative MCP server definitions
- `hooks`: optional list of hook names; accepted as config data only in v1

Minimal manifest:

```json
{
  "name": "docs",
  "description": "Example project-local external plugin."
}
```

`skills` entries support:

- `name`
- `description`
- `content`
- `auto_enable`
- `tags`

Minimal skill example:

```json
{
  "name": "docs",
  "description": "Example project-local external plugin.",
  "skills": [
    {
      "name": "docs-style",
      "description": "Use stable user-facing terminology in documentation.",
      "content": "Prefer stable user-facing terminology.",
      "auto_enable": true,
      "tags": ["docs"]
    }
  ]
}
```

`mcp_servers` entries support the same declarative MCP fields already accepted by the runtime loader, including:

- `name`
- `transport`
- `command`
- `args`
- `env`
- `headers`
- `auth`
- `cwd`
- `url`
- `timeout_sec`

Minimal MCP server example:

```json
{
  "name": "mcp-echo",
  "description": "Example project-local external plugin with a local stdio MCP server.",
  "mcp_servers": [
    {
      "name": "plugin-echo",
      "transport": "stdio",
      "command": "python",
      "args": ["server.py"]
    }
  ]
}
```

`hooks` is a list of hook names such as:

```json
{
  "name": "docs",
  "description": "Example project-local external plugin.",
  "hooks": ["before_final_answer"]
}
```

Current external plugin constraints:

- project-local only
- data-only
- no arbitrary Python plugin loading
- no custom executable command handlers
- no marketplace, browse, install, or trust flows

## Verification

Run the canonical test command from `python_claudecode/`:

```bash
python -m pytest -q
```

If you want to confirm collection behavior separately:

```bash
python -m pytest --collect-only -q
```

## Related Documents

- [../README.md](../README.md): repository overview and project positioning
- [CLAUDE.md](CLAUDE.md): current implementation status and next-stage roadmap
- [PARITY_MATRIX.md](PARITY_MATRIX.md): upstream-to-Python parity tracking for local-agent-relevant surfaces
