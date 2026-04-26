from __future__ import annotations

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


def compose_system_prompt(
    *,
    base_prompt: str,
    project_context: ProjectContext,
    auto_enabled_skills: list[LoadedSkill],
    manually_enabled_skills: list[LoadedSkill],
    context_summary: str | None,
    planning_context: str | None = None,
) -> str:
    parts = [base_prompt]
    if project_context.memory_content:
        parts.append(
            "Project memory:\n"
            f"{project_context.memory_content}"
        )
    if auto_enabled_skills:
        skill_lines = []
        for skill in auto_enabled_skills:
            skill_lines.append(f"- {skill.name} ({skill.path.name})")
            if skill.content:
                skill_lines.append(skill.content)
        parts.append("Auto-enabled project skills:\n" + "\n".join(skill_lines))
    if manually_enabled_skills:
        skill_lines = []
        for skill in manually_enabled_skills:
            skill_lines.append(f"- {skill.name} ({skill.path.name})")
            if skill.content:
                skill_lines.append(skill.content)
        parts.append("Manually enabled project skills:\n" + "\n".join(skill_lines))
    if context_summary:
        parts.append("Compacted conversation context from earlier turns:\n" + context_summary)
    if planning_context:
        parts.append("Recent planning artifact to reuse when relevant:\n" + planning_context)
    return "\n\n".join(part for part in parts if part)
