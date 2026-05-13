from __future__ import annotations

from pathlib import Path
import os
import re
import subprocess
from typing import Any

from .registry import CommandExecution

_REVIEW_ALLOWED_TOOLS = (
    "bash",
    "list_dir",
    "read_file",
    "outline_file",
    "outline_project",
    "glob",
    "grep",
    "find_symbol",
    "find_references",
)

_REVIEW_ALLOWED_BASH_PREFIXES = (
    "gh pr list",
    "gh pr view",
    "gh pr diff",
    "git status",
    "git diff",
    "git log",
    "git show",
)

_COMMIT_ALLOWED_TOOLS = ("bash",)
_COMMIT_ALLOWED_BASH_PREFIXES = ("git add", "git status", "git commit")
_SECURITY_REVIEW_ALLOWED_TOOLS = (
    "bash",
    "read_file",
    "glob",
    "grep",
    "list_dir",
    "task_list",
    "task_get",
    "task_wait",
)
_SECURITY_REVIEW_ALLOWED_BASH_PREFIXES = (
    "git status",
    "git diff",
    "git log",
    "git show",
    "git remote show",
)
_ULTRAPLAN_ALLOWED_TOOLS = (
    "agent",
    "bash",
    "list_dir",
    "read_file",
    "outline_file",
    "outline_project",
    "glob",
    "grep",
    "find_symbol",
    "find_references",
    "find_symbol_graph",
    "find_callers",
    "find_callees",
    "task_list",
    "task_get",
    "task_wait",
)
_ULTRAPLAN_ALLOWED_BASH_PREFIXES = (
    "git status",
    "git diff",
    "git log",
    "git show",
    "git branch",
)
_INIT_ALLOWED_TOOLS = (
    "list_dir",
    "read_file",
    "outline_file",
    "outline_project",
    "glob",
    "grep",
    "find_symbol",
    "find_references",
    "write_file",
    "edit_file",
    "apply_patch",
)

_COMMIT_PROMPT_TEMPLATE = """## Context

- Current git status:
```
!`git status`
```
- Current git diff (staged and unstaged changes):
```
!`git diff HEAD`
```
- Current branch:
```
!`git branch --show-current`
```
- Recent commits:
```
!`git log --oneline -10`
```

## Git Safety Protocol

- NEVER update git config
- NEVER use --no-verify, --no-gpg-sign, or other hook-skipping flags unless the user explicitly asked for them
- ALWAYS create a new commit; never use `git commit --amend`
- Do not create an empty commit when there are no changes
- Avoid interactive git flags like `-i`

## Your task

Based on the current workspace changes, create a single git commit.

1. Analyze the staged and unstaged changes and draft a commit message.
2. Follow the repository's recent commit style.
3. Keep the message concise and focused on the intent of the change.
4. Stage the relevant files and create the commit.
5. Use only the bash tool and do not do anything unrelated to the commit.
"""

_REVIEW_PROMPT_TEMPLATE = """You are an expert code reviewer. Follow these steps:

1. If no PR number is provided, run `gh pr list` to show open PRs.
2. If a PR number is provided, run `gh pr view <number>` to inspect the PR.
3. Run `gh pr diff <number>` to inspect the diff.
4. Analyze the changes and produce a concise but thorough review.

Focus on:
- correctness
- regressions
- project conventions
- performance implications
- test coverage gaps
- security-relevant issues

Output requirements:
- findings first, ordered by severity
- include file and line references when possible
- if there are no findings, say so explicitly
- keep any summary short

PR number or selector: {args}
"""

_SECURITY_REVIEW_PROMPT_TEMPLATE = """You are a senior security engineer conducting a focused security review of the pending changes on this branch.

GIT STATUS:
```
!`git status`
```

FILES MODIFIED:
```
!`git diff --name-only HEAD`
```

COMMITS:
```
!`git log --no-decorate -10`
```

DIFF CONTENT:
```
!`git diff HEAD`
```

OBJECTIVE:
- Identify concrete, high-confidence security vulnerabilities introduced by the current changes.
- Ignore generic style issues and speculative concerns.
- Focus on exploitability, privilege boundaries, injection, authz/authn, secret handling, and data exposure.

OUTPUT FORMAT:
- Findings first, ordered by severity.
- Include file and line references.
- For each finding, include severity, exploit scenario, and recommendation.
- If there are no concrete findings, say so explicitly.

CONSTRAINTS:
- Do not modify files.
- Use only read-oriented tools and the allowed git inspection commands.
"""

_INIT_PROMPT_TEMPLATE = """Create or improve the repository guidance file for future Claude Code sessions.

Your task:
1. Inspect the codebase using file tools. Read the minimum set of files needed to understand the project:
   - README and manifest files
   - build/test/lint config
   - CI config
   - existing CLAUDE.md, AGENTS.md, Cursor/Copilot rules, or similar AI-assistant guidance files
2. Write or update CLAUDE.md at the repository root.
3. Keep CLAUDE.md concise. Only include repository-specific information that would prevent mistakes or save significant setup time.
4. Include:
   - non-obvious build, test, and lint commands
   - high-level architecture or workflow facts that require reading multiple files
   - important repo-specific conventions, gotchas, and environment setup details
5. Exclude:
   - generic coding advice
   - exhaustive file listings
   - information that is obvious from a quick directory listing
6. If CLAUDE.md already exists, improve it instead of replacing it blindly.
7. Do not modify any file other than CLAUDE.md.

Required file header:
# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.
"""

_ULTRAPLAN_PROMPT_TEMPLATE = """You are running an ultraplan session for a coding task.

Your objective is to produce an unusually strong implementation plan before any code is written.

Rules:
1. Do not modify files.
2. Investigate the codebase deeply using only the allowed read-oriented tools.
3. Prefer evidence from the current repository over assumptions.
4. Call out uncertainty explicitly where the codebase does not provide enough information.
5. You may use sub-agents for parallel investigation, but every sub-agent is read-only and must only return findings and planning input.

What to do:
1. Restate the user's goal in precise technical terms.
2. Inspect the relevant code, architecture, config, and tests.
3. Identify the concrete modules, data flows, and integration points involved.
4. Identify constraints, risks, likely regressions, and missing information.
5. When helpful, delegate bounded read-only research subtasks to sub-agents and synthesize their results.
6. Produce a step-by-step implementation plan.
7. Include a verification plan with exact tests, commands, or checks to run.

Output format:
- Findings first: current architecture and constraints.
- Then a numbered implementation plan.
- Then risks/open questions.
- Then a verification checklist.

User request:
{request}
"""


def build_review_execution(_session: "Session", args: str) -> CommandExecution:
    review_args = args.strip() or "(none provided)"
    return CommandExecution(
        prompt=_REVIEW_PROMPT_TEMPLATE.format(args=review_args),
        allowed_tool_names=_REVIEW_ALLOWED_TOOLS,
        allowed_bash_command_prefixes=_REVIEW_ALLOWED_BASH_PREFIXES,
        progress_message="Reviewing pull request",
        metadata={
            "command_policy_name": "review",
            "command_policy_source": "repl:/review",
        },
    )


def build_commit_execution(session: "Session", args: str) -> str | CommandExecution:
    if args.strip():
        return "Usage: /commit"
    return CommandExecution(
        prompt=render_prompt_with_command_output(
            _COMMIT_PROMPT_TEMPLATE,
            cwd=session.config.cwd,
        ),
        allowed_tool_names=_COMMIT_ALLOWED_TOOLS,
        allowed_bash_command_prefixes=_COMMIT_ALLOWED_BASH_PREFIXES,
        progress_message="Creating commit",
        metadata={
            "command_policy_name": "commit",
            "command_policy_source": "repl:/commit",
        },
    )


def build_security_review_execution(session: "Session", args: str) -> str | CommandExecution:
    if args.strip():
        return "Usage: /security-review"
    return CommandExecution(
        prompt=render_prompt_with_command_output(
            _SECURITY_REVIEW_PROMPT_TEMPLATE,
            cwd=session.config.cwd,
        ),
        allowed_tool_names=_SECURITY_REVIEW_ALLOWED_TOOLS,
        allowed_bash_command_prefixes=_SECURITY_REVIEW_ALLOWED_BASH_PREFIXES,
        progress_message="Running security review",
        metadata={
            "command_policy_name": "security-review",
            "command_policy_source": "repl:/security-review",
        },
    )


def build_init_execution(_session: "Session", args: str) -> str | CommandExecution:
    if args.strip():
        return "Usage: /init"
    return CommandExecution(
        prompt=_INIT_PROMPT_TEMPLATE,
        allowed_tool_names=_INIT_ALLOWED_TOOLS,
        progress_message="Initializing CLAUDE.md",
    )


def build_ultraplan_execution(
    _session: "Session",
    args: str,
    *,
    metadata_extra: dict[str, Any] | None = None,
    derivation_context: dict[str, str] | None = None,
    progress_message: str = "Building ultraplan",
) -> str | CommandExecution:
    request = args.strip()
    if not request:
        return "Usage: /ultraplan <goal>"
    prompt = _ULTRAPLAN_PROMPT_TEMPLATE.format(request=request)
    if derivation_context is not None:
        prior_summary = derivation_context.get("summary", "").strip()
        if len(prior_summary) > 900:
            prior_summary = prior_summary[:897] + "..."
        derivation_reason = derivation_context.get("derivation_reason", "").strip()
        drift_context = derivation_context.get("last_plan_drift_context", "").strip()
        if len(drift_context) > 900:
            drift_context = drift_context[:897] + "..."
        derivation_lines = [
            "You are revising an existing active plan instead of planning from scratch.",
            f"Previous artifact: {derivation_context.get('artifact_id', '(unknown)')}",
            f"Previous goal: {derivation_context.get('goal', '(unknown)')}",
            "Carry forward valid parts of the previous plan, fix weak sections, and make the revised plan explicit.",
        ]
        if derivation_reason:
            derivation_lines.append(f"Why the plan is being revised: {derivation_reason}")
        if prior_summary:
            derivation_lines.extend(["Previous plan summary:", prior_summary])
        if drift_context:
            derivation_lines.extend(["Recent plan drift analysis:", drift_context])
        prompt = "\n".join(derivation_lines) + "\n\n" + prompt
    metadata = {
        "command_kind": "ultraplan",
        "command_policy_name": "ultraplan",
        "command_policy_source": "repl:/ultraplan",
        "goal": request,
        "scout_categories": [
            "architecture-boundaries",
            "data-flow-interfaces",
            "tests-regressions",
            "risks-unknowns",
        ],
    }
    if metadata_extra:
        metadata.update(metadata_extra)
    return CommandExecution(
        prompt=prompt,
        allowed_tool_names=_ULTRAPLAN_ALLOWED_TOOLS,
        allowed_bash_command_prefixes=_ULTRAPLAN_ALLOWED_BASH_PREFIXES,
        require_read_only_subagents=True,
        progress_message=progress_message,
        metadata=metadata,
    )


def render_prompt_with_command_output(template: str, *, cwd: Path) -> str:
    return re.sub(
        r"!\`([^`]+)\`",
        lambda match: _run_shell_capture(cwd, match.group(1).strip()),
        template,
    )


def _run_shell_capture(cwd: Path, command: str) -> str:
    if os.name == "nt":
        argv = ["powershell", "-NoProfile", "-Command", command]
    else:
        argv = ["bash", "-lc", command]
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return f"[command failed: {type(exc).__name__}: {exc}]"

    parts: list[str] = []
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    if stdout:
        parts.append(stdout[:12000])
    if stderr:
        parts.append(stderr[:4000])
    if not parts:
        parts.append(f"[command exited with code {completed.returncode}]")
    return "\n".join(parts)


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..session import Session
