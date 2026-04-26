# Python ClaudeCode

This directory contains a Python reimplementation of the local Claude Code workflow, guided by the extracted upstream sources in `../package/src-extracted/src`.

It is still a port-in-progress, but it is no longer just a minimal skeleton. The current implementation covers the main local coding-agent loop and a large part of the product surface around it.

## Current Capabilities

- CLI, REPL, and optional Textual TUI
- shared session runtime and Anthropic-like internal message model
- tool-calling query loop with permission control and workspace change tracking
- Anthropic and OpenAI-compatible providers
- MCP loading from `.pyclaude/mcp_servers.json`
- background sessions with `ask --background`, `ps`, `logs`, `attach`, and `kill`
- live attach / reattach through the bridge service, including bridged approval flow
- structured headless commands for symbol lookup, references, IDE targets, and MCP diagnosis
- built-in plugin registry and plugin-backed commands
- advisor modes with final-answer review and interactive checkpoints before plan / write / final answer
- `/ultraplan` multi-scout read-only planning mode with reusable planning artifacts
- `/plan` management commands for active planning artifacts
- transcript persistence, session insights, local skills, and isolated workspaces
- workspace metadata and advisor constraint visibility in `/config`, `/tasks`, `/insights`, attach, and TUI

## Built-in Plugin Commands

The default built-in plugins currently provide:

- `/review`
- `/commit`
- `/security-review`
- `/init`
- `/install`
- `/advisor`
- `/insights`
- `/plan`
- `/ultraplan`

`/advisor` supports:

- `/advisor`
- `/advisor status`
- `/advisor <model> [final-review|interactive-review]`
- `/advisor mode <final-review|interactive-review>`
- `/advisor off`

`/plan` supports:

- `/plan`
- `/plan list`
- `/plan show [id|latest]`
- `/plan use <id>`
- `/plan clear`

`/ultraplan <goal>` now runs a fixed two-phase planning flow:

- scout phase: launches read-only sub-agents for architecture, interfaces, tests, and risks
- synthesis phase: merges scout results into a structured implementation plan

Plugin state can be inspected and changed with:

- `/plugins`
- `/plugin list`
- `/plugin show <name>`
- `/plugin enable <name>`
- `/plugin disable <name>`

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

Launch a detached background session:

```bash
pyclaude ask "Review the pending changes" --background
pyclaude ps
pyclaude attach <session-id>
```

Start the TUI:

```bash
pyclaude tui
```

## CLI Surface

Main interactive commands:

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

Structured/headless commands:

- `locate-symbol`
- `references`
- `open-file`
- `open-symbol`
- `diff-targets`
- `reference-targets`
- `symbol-actions`
- `mcp-call`
- `mcp-verify`

## Providers

### Anthropic

```bash
pyclaude --provider anthropic repl
```

Environment variables:

```bash
ANTHROPIC_API_KEY=...
```

### OpenAI-compatible

Works with OpenAI and other APIs that implement the Chat Completions tool-calling shape.

```bash
pyclaude --provider openai-compatible --base-url https://api.openai.com/v1 repl
```

Environment variables:

```bash
PYCLAUDE_PROVIDER=openai-compatible
PYCLAUDE_API_KEY=...
PYCLAUDE_BASE_URL=https://api.openai.com/v1
PYCLAUDE_MODEL=gpt-4.1-mini
```

## Use a `.env` File

Create `python_claudecode/.env`:

```bash
copy .env.example .env
```

Then set values such as:

```bash
PYCLAUDE_PROVIDER=openai-compatible
PYCLAUDE_API_KEY=your-real-key
PYCLAUDE_BASE_URL=https://api.openai.com/v1
PYCLAUDE_MODEL=gpt-4.1-mini
```

The CLI auto-loads `.env` from the selected `--cwd` before building the session config.

## Workspace Data

Runtime data is stored under `.pyclaude/` in the chosen working directory.

Common contents:

- `.pyclaude/sessions/`: saved transcripts
- `.pyclaude/background_sessions/`: detached session registry and logs
- `.pyclaude/skills/`: local markdown skills
- `.pyclaude/mcp_servers.json`: MCP server configuration
- `.pyclaude/workspaces/`: isolated workspace snapshots
- `.pyclaude/worktrees/`: git worktree-backed isolated workspaces when the repo is clean

Saved transcripts now also persist advisor review summaries, active planning artifact state, workspace mode metadata, and planning history, which are surfaced by `/insights`.

## Notes and Gaps

- This is a Python product clone, not a literal upstream source port.
- The architecture is intentionally aligned with the upstream concepts: session runtime, tool registry, providers, orchestrator, permissions, tasks, plugins, and remote attach.
- Some heavier product areas from upstream Claude Code are still incomplete or intentionally simplified.
- The codebase is optimized around local coding-agent workflows first; parity for every upstream command or UI surface is not the goal.
