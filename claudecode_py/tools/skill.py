from __future__ import annotations

from ..skills import (
    build_skill_command_execution,
    find_user_invocable_skill,
    is_model_invocable_skill,
)
from .base import BaseTool, ToolContextUpdate, ToolExecutionPayload


class SkillTool(BaseTool):
    name = "skill"
    description = "Execute a known user-invocable skill by name with optional arguments."
    read_only = False
    concurrency_safe = False
    search_terms = ("skill", "slash command", "workflow prompt", "invocable skill")
    input_schema = {
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "Exact skill name. A leading slash is allowed but not required.",
            },
            "args": {
                "type": "string",
                "description": "Arguments to bind into the skill prompt before execution.",
            },
        },
        "required": ["skill"],
    }

    def execute(self, tool_input: dict, ctx):
        skill_name = str(tool_input["skill"]).strip()
        args = str(tool_input.get("args", "") or "")
        skill = find_user_invocable_skill(ctx.session.project_context.skills, skill_name)
        normalized = skill_name[1:] if skill_name.startswith("/") else skill_name
        if skill is None:
            raise ValueError(f'Unknown user-invocable skill "{normalized}".')
        if not is_model_invocable_skill(skill):
            raise ValueError(
                f'Skill "{normalized}" cannot be used with the skill tool due to '
                "disable-model-invocation."
            )
        execution = build_skill_command_execution(skill, args)
        metadata = execution.metadata or {}
        if metadata.get("command_kind") != "skill-fork":
            injected_message = {
                "role": "user",
                "content": [{"type": "text", "text": execution.prompt}],
                "source_kind": "skill_tool_inline",
                "source_tool_name": self.name,
                "source_tool_use_id": ctx.tool_call_id,
                "skill_name": skill.name,
                "skill_execution_context": "inline",
            }
            return ToolExecutionPayload(
                result={
                    "success": True,
                    "skill": skill.name,
                    "status": "inline",
                    "injected_message_count": 1,
                    "allowed_tool_names": list(execution.allowed_tool_names or ()),
                    "allowed_bash_command_prefixes": list(
                        execution.allowed_bash_command_prefixes or ()
                    ),
                },
                new_messages=[injected_message],
                context_update=ToolContextUpdate(
                    allowed_tool_names=execution.allowed_tool_names,
                    allowed_bash_command_prefixes=execution.allowed_bash_command_prefixes,
                    require_read_only_subagents=execution.require_read_only_subagents,
                    source="skill_tool_inline",
                    skill_name=skill.name,
                    model_override=skill.model or None,
                    effort_override=skill.effort or None,
                ),
            )
        fork_result = ctx.session.run_forked_skill_mutation(
            execution,
            tool_name=self.name,
            tool_use_id=ctx.tool_call_id,
        )
        return ToolExecutionPayload(
            result={
                "success": True,
                "skill": skill.name,
                "status": "fork",
                "injected_message_count": fork_result.injected_message_count,
                "allowed_tool_names": list(execution.allowed_tool_names or ()),
                "allowed_bash_command_prefixes": list(execution.allowed_bash_command_prefixes or ()),
            },
            new_messages=fork_result.new_messages,
            context_update=fork_result.context_update,
        )
