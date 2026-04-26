from __future__ import annotations

from .base import BaseTool


class AgentTool(BaseTool):
    name = "agent"
    description = "Launch a sub-agent to work on a subtask. Some command contexts may force sub-agents into read-only planning mode."
    read_only = False
    concurrency_safe = False
    input_schema = {
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "Short description of the subtask."},
            "prompt": {"type": "string", "description": "Prompt for the sub-agent."},
            "run_in_background": {"type": "boolean", "description": "Whether to run the sub-agent in the background."},
            "isolated_workspace": {
                "type": "boolean",
                "description": "Whether to run the sub-agent in an isolated workspace snapshot.",
            },
            "read_only": {
                "type": "boolean",
                "description": "Whether the sub-agent must only inspect and plan without modifying files.",
            },
        },
        "required": ["description", "prompt"],
    }

    def execute(self, tool_input: dict, ctx):
        description = tool_input["description"]
        prompt = tool_input["prompt"]
        isolated_workspace = bool(tool_input.get("isolated_workspace", False))
        forced_read_only = bool(getattr(ctx.session, "requires_read_only_subagents", lambda: False)())
        read_only = forced_read_only or bool(tool_input.get("read_only", False))
        if bool(tool_input.get("run_in_background", False)):
            task_id = ctx.session.launch_background_agent(
                description=description,
                prompt=prompt,
                isolated_workspace=isolated_workspace,
                read_only=read_only,
            )
            return (
                f"Background agent launched.\n"
                f"task_id: {task_id}\n"
                f"isolated_workspace: {isolated_workspace}\n"
                f"read_only: {read_only}\n"
                f'Use the task_get tool with "{task_id}" to inspect progress.'
            )
        result = ctx.session.run_subagent(
            description=description,
            prompt=prompt,
            isolated_workspace=isolated_workspace,
            read_only=read_only,
        )
        mode = "read-only planning" if read_only else "standard"
        return f"Sub-agent result ({mode}):\n{result}"
