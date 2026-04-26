from __future__ import annotations

from ..commands.local_commands import handle_advisor_command, handle_install_command, handle_insights_command
from ..commands.prompt_commands import (
    build_commit_execution,
    build_init_execution,
    build_review_execution,
    build_security_review_execution,
    build_ultraplan_execution,
)
from ..commands.registry import ReplCommand
from .registry import PluginDefinition, PluginRegistry, PluginSkillDefinition


def build_builtin_plugin_registry() -> PluginRegistry:
    registry = PluginRegistry()
    for plugin in (
        _build_advisor_plugin(),
        _build_review_plugin(),
        _build_commit_plugin(),
        _build_init_plugin(),
        _build_install_plugin(),
        _build_insights_plugin(),
        _build_security_review_plugin(),
        _build_ultraplan_plugin(),
    ):
        registry.add_plugin(plugin)
    return registry


def _build_advisor_plugin() -> PluginDefinition:
    return PluginDefinition(
        name="advisor",
        description="Configure advisor-model preferences for future runtime use.",
        hooks=("before_final_answer",),
        commands=(
            ReplCommand(
                "/advisor",
                "Show or change the advisor model preference",
                lambda session, args: handle_advisor_command(session, args),
            ),
        ),
    )


def _build_review_plugin() -> PluginDefinition:
    return PluginDefinition(
        name="review",
        description="Prompt-driven pull request review commands.",
        skills=(
            PluginSkillDefinition(
                name="review-pr",
                description="Guidance for reviewing pull requests with findings-first output.",
                content=(
                    "When reviewing a pull request, prioritize correctness, regressions, "
                    "security issues, and test gaps. Report findings first with file and line references."
                ),
            ),
        ),
        commands=(
            ReplCommand(
                "/review",
                "Review a pull request",
                lambda session, args: build_review_execution(session, args),
            ),
        ),
    )


def _build_commit_plugin() -> PluginDefinition:
    return PluginDefinition(
        name="commit",
        description="Prompt-driven git commit creation commands.",
        skills=(
            PluginSkillDefinition(
                name="commit-style",
                description="Guidance for drafting concise repository-consistent commit messages.",
                content=(
                    "When creating commits, follow the repository's recent commit style, "
                    "keep the message concise, and focus on the intent of the change."
                ),
            ),
        ),
        commands=(
            ReplCommand(
                "/commit",
                "Create a git commit for the current workspace changes",
                lambda session, args: build_commit_execution(session, args),
            ),
        ),
    )


def _build_init_plugin() -> PluginDefinition:
    return PluginDefinition(
        name="init",
        description="Prompt-driven CLAUDE.md initialization for the current repository.",
        skills=(
            PluginSkillDefinition(
                name="claude-md",
                description="Guidance for writing concise, repo-specific CLAUDE.md files.",
                content=(
                    "When creating CLAUDE.md, prioritize non-obvious commands, architecture, "
                    "workflow gotchas, and repo-specific conventions. Omit generic advice."
                ),
            ),
        ),
        commands=(
            ReplCommand(
                "/init",
                "Create or improve CLAUDE.md for the current repository",
                lambda session, args: build_init_execution(session, args),
            ),
        ),
    )


def _build_install_plugin() -> PluginDefinition:
    return PluginDefinition(
        name="install",
        description="Local install guidance for this Python Claude Code clone.",
        commands=(
            ReplCommand(
                "/install",
                "Show editable install commands and optional dependency status",
                lambda session, args: handle_install_command(session, args),
            ),
        ),
    )


def _build_insights_plugin() -> PluginDefinition:
    return PluginDefinition(
        name="insights",
        description="Analyze saved sessions and transcripts in the current workspace.",
        commands=(
            ReplCommand(
                "/insights",
                "Analyze saved session transcripts for the current workspace",
                lambda session, args: handle_insights_command(session, args),
            ),
        ),
    )


def _build_security_review_plugin() -> PluginDefinition:
    return PluginDefinition(
        name="security-review",
        description="Prompt-driven security review commands.",
        skills=(
            PluginSkillDefinition(
                name="security-review",
                description="Guidance for concrete, exploitability-focused change review.",
                content=(
                    "Focus on concrete, high-confidence security vulnerabilities introduced by the change. "
                    "Prioritize exploitability, trust boundaries, injection, auth, secret handling, and exposure."
                ),
            ),
        ),
        commands=(
            ReplCommand(
                "/security-review",
                "Run a security-focused review of the current changes",
                lambda session, args: build_security_review_execution(session, args),
            ),
        ),
    )


def _build_ultraplan_plugin() -> PluginDefinition:
    return PluginDefinition(
        name="ultraplan",
        description="Deep read-only planning workflow for complex coding tasks.",
        hooks=("before_plan", "after_plan"),
        skills=(
            PluginSkillDefinition(
                name="ultraplan",
                description="Guidance for high-rigor implementation planning before coding.",
                content=(
                    "Before complex changes, investigate the codebase, identify constraints and risks, "
                    "then produce a concrete implementation and verification plan before editing."
                ),
            ),
        ),
        commands=(
            ReplCommand(
                "/ultraplan",
                "Build a deep implementation plan for a coding task",
                lambda session, args: build_ultraplan_execution(session, args),
            ),
        ),
    )
