from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .skills import LoadedSkill, ProjectContext


SYSTEM_PROMPT_TEMPLATE = """You are PyClaudeCode, a coding agent running in a local repository.

Current workspace root:
- absolute path: {cwd}
- workspace name: {workspace_name}

Interpretation rules for paths:
- The workspace root itself is already `{workspace_name}`.
- If the user says "current project", "current repo", "current directory", or "{workspace_name}", they usually mean the workspace root itself, not a nested child directory.
- Prefer listing "." before assuming a nested directory with the same name as the repo.

Your job is to help the user analyze code, edit files, and run commands safely.

Rules:
- Prefer using tools over guessing.
- Use list_dir for directory structure questions.
- Read files before editing them.
- Prefer apply_patch for coordinated multi-file edits.
- Keep edits minimal and relevant.
- Do not use dangerous commands unless the user explicitly asked for them or the permission layer approved them.
- When you finish, explain what changed concisely.
"""


@dataclass(slots=True, frozen=True)
class SystemPromptBlock:
    text: str
    cache_scope: Literal["global", "session", "dynamic", "none"]
    kind: Literal["prefix", "static", "dynamic", "context_summary", "planning", "skills", "metadata"]


def compose_system_prompt_blocks(
    *,
    base_prompt: str,
    project_context: ProjectContext,
    auto_enabled_skills: list[LoadedSkill],
    manually_enabled_skills: list[LoadedSkill],
    context_summary: str | None,
    planning_context: str | None = None,
) -> list[SystemPromptBlock]:
    parts = [
        SystemPromptBlock(
            text=base_prompt,
            cache_scope="session",
            kind="prefix",
        )
    ]
    if project_context.memory_content:
        parts.append(
            SystemPromptBlock(
                text="Project memory:\n" f"{project_context.memory_content}",
                cache_scope="session",
                kind="static",
            )
        )
    if auto_enabled_skills:
        skill_lines = []
        for skill in auto_enabled_skills:
            skill_lines.append(f"- {skill.name} ({skill.path.name})")
            if skill.content:
                skill_lines.append(skill.content)
        parts.append(
            SystemPromptBlock(
                text="Auto-enabled project skills:\n" + "\n".join(skill_lines),
                cache_scope="session",
                kind="skills",
            )
        )
    if manually_enabled_skills:
        skill_lines = []
        for skill in manually_enabled_skills:
            skill_lines.append(f"- {skill.name} ({skill.path.name})")
            if skill.content:
                skill_lines.append(skill.content)
        parts.append(
            SystemPromptBlock(
                text="Manually enabled project skills:\n" + "\n".join(skill_lines),
                cache_scope="session",
                kind="skills",
            )
        )
    if context_summary:
        parts.append(
            SystemPromptBlock(
                text="Compacted conversation context from earlier turns:\n" + context_summary,
                cache_scope="dynamic",
                kind="context_summary",
            )
        )
    if planning_context:
        parts.append(
            SystemPromptBlock(
                text="Recent planning artifact to reuse when relevant:\n" + planning_context,
                cache_scope="dynamic",
                kind="planning",
            )
        )
    return [part for part in parts if part.text.strip()]


def render_system_prompt_blocks(blocks: list[SystemPromptBlock]) -> str:
    return "\n\n".join(block.text for block in blocks if block.text.strip())


def compose_system_prompt(
    *,
    base_prompt: str,
    project_context: ProjectContext,
    auto_enabled_skills: list[LoadedSkill],
    manually_enabled_skills: list[LoadedSkill],
    context_summary: str | None,
    planning_context: str | None = None,
) -> str:
    return render_system_prompt_blocks(
        compose_system_prompt_blocks(
            base_prompt=base_prompt,
            project_context=project_context,
            auto_enabled_skills=auto_enabled_skills,
            manually_enabled_skills=manually_enabled_skills,
            context_summary=context_summary,
            planning_context=planning_context,
        )
    )
