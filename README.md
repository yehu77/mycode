# Python ClaudeCode

`python_claudecode/` is a local-first Python reimplementation of the Claude Code workflow.

It is built for people who want a coding agent they can run in their own workspace with:

- interactive chat and tool use
- file-aware coding workflows
- planning and task tracking
- background sessions
- MCP integration
- local plugins and skills

This project aims at **useful local workflow parity**, not full hosted product parity.

## What You Can Do

- Ask one-off questions with `pyclaude ask`
- Work interactively in a REPL with slash commands
- Use a TUI for file, plan, change, and status inspection
- Run long jobs in background sessions and reattach later
- Track tasks, plans, and change history inside the same session
- Add MCP tools and project-local plugins
- Inspect the agent's current context, files, diffs, and session state

## What This Is Not

- It is not a hosted Claude product clone.
- It does not try to reproduce login, billing, subscriptions, or account flows.
- It does not aim for one-to-one parity with every upstream command.

## Install

From `python_claudecode/`:

```bash
python -m venv .venv
```

Activate the virtual environment:

- Windows PowerShell: `.\.venv\Scripts\Activate.ps1`
- macOS/Linux: `source .venv/bin/activate`

Then install:

```bash
pip install -e .[all]
```

Optional extras:

- `.[anthropic]`
- `.[openai]`
- `.[tui]`
- `.[mcp-remote]`

## Quick Start

Run the interactive REPL:

```bash
pyclaude repl
```

Run a single prompt:

```bash
pyclaude ask "Summarize this repository"
```

Start the TUI:

```bash
pyclaude tui
```

## Common Workflows

### 1. Explore a repo

```bash
pyclaude repl
/context
/files
/diff
/status
```

Use this flow when you want to understand what is in scope, what changed, and what the session currently knows.

### 2. Work with a plan

```bash
pyclaude repl
/plan add retry logic to the provider path
/plan open
/planning
/planning timeline
```

Use `/plan` to enter plan mode, write or refine the current session plan file, and exit through plan approval. Use `/planning` for artifact-oriented inspection such as active plan detail, timeline, replay, audit, and lineage.

### 3. Review changes

```bash
pyclaude repl
/changes
/diff
/review
/advisor
```

Use this flow when you want change inspection, review, and advisor-style feedback in one session.

### 4. Run a background job

```bash
pyclaude ask "Review the pending changes" --background
pyclaude ps
pyclaude logs <session-id> summary
pyclaude attach <session-id>
```

Use background sessions for long-running or interruptible work.

## Commands You Will Actually Use

Main entrypoints:

- `pyclaude ask`
- `pyclaude repl`
- `pyclaude tui`
- `pyclaude ps`
- `pyclaude logs`
- `pyclaude attach`

Most useful REPL commands:

- `/status`
- `/context`
- `/files`
- `/diff`
- `/changes`
- `/tasks`
- `/plan`
- `/planning`
- `/ultraplan`
- `/advisor`
- `/project-context`
- `/mcp`
- `/plugins`
- `/history`
- `/compact`
- `/clear`

## File and Context Workflow

The local workflow is built around explicit context and visible file state.

- `/context` shows prompt/context usage
- `/files` shows the current working set
- `/diff` shows diff-backed work
- `/add-dir <path>` adds explicit context paths
- `/changes` shows tracked change history
- `/workspaces` shows isolated or background workspaces

This is one of the strongest parts of the current Python implementation.

## Planning, Tasks, and Review

The Python version already supports a serious planning workflow:

- `/plan` for mode-centric planning with a real session plan file, plan-mode restrictions, and approval-driven exit
- `/planning` for active plan inspection, replay, timeline, lineage, and reuse
- `/ultraplan` for larger read-only multi-scout planning
- `/tasks` and `/task` for checklist-style task tracking
- `/advisor` for final-review and interactive-review flows
- `/review` and `/security-review` for code review workflows

It supports planning well, but it is still a local coding workflow tool rather than a hosted multi-user planning product.

## Plugins, Skills, and MCP

You can extend the local runtime in three main ways:

- project-local plugins in `.pyclaude/plugins/<name>/plugin.json`
- skills loaded into the session
- MCP servers from `.pyclaude/mcp_servers.json`

Useful commands:

- `/plugins`
- `/plugin show <name>`
- `/skills`
- `/skills-reload`
- `/mcp`
- `/mcp-tools`
- `/mcp-refresh`

Example external plugins live under:

```text
examples/external-plugin/
```

Current plugin scope is intentionally conservative:

- project-local
- declarative
- data-only
- no arbitrary Python plugin loading

## TUI and Remote Use

The TUI is useful when you want a stronger session dashboard and file-oriented workflow. It exposes:

- current status
- active plan
- task detail
- change inspection
- focused file navigation

There is also a stdio service and TCP bridge for structured remote/local attach workflows, but the primary target remains local single-user coding work.

## Current Project Status

- The core local runtime is implemented and usable.
- The Python test suite is currently green.
- Planning, history, status, files, diffs, plugins, skills, MCP, and background sessions are all part of the working local flow.

For engineering depth and parity tracking, use the docs below rather than treating this README as an implementation ledger.

## Verification

From `python_claudecode/`:

```bash
python -m pytest -q
```

## Related Documents

- [CLAUDE.md](CLAUDE.md): engineering ledger, current boundaries, and next-stage direction
- [PARITY_MATRIX.md](PARITY_MATRIX.md): high-level parity tracking against the upstream source tree
- [UPSTREAM_SOURCE_ALIGNMENT.md](UPSTREAM_SOURCE_ALIGNMENT.md): deeper mechanism-level comparison with the upstream implementation
