from __future__ import annotations

from ..agents import builtin_planning_agent_names, get_builtin_planning_agent_contract
from .base import BaseTool

_PLANNING_AGENT_NAME_LABEL = " or ".join(builtin_planning_agent_names())


class AgentTool(BaseTool):
    name = "agent"
    description = "Launch a sub-agent to work on a subtask. Some command contexts may force sub-agents into read-only planning mode."
    read_only = False
    concurrency_safe = False
    input_schema = {
        "type": "object",
        "properties": {
            "agent_type": {
                "type": "string",
                "description": f"Optional named sub-agent type such as {_PLANNING_AGENT_NAME_LABEL}.",
            },
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
        agent_type = str(tool_input.get("agent_type") or "").strip() or None
        description = tool_input["description"]
        prompt = tool_input["prompt"]
        profile = (
            ctx.session.resolve_agent_runtime_profile(agent_type)
            if agent_type is not None
            else None
        )
        planning_only = bool(profile is not None and profile.get("planning_only"))
        if planning_only and bool(tool_input.get("run_in_background")):
            raise ValueError(
                f'Builtin planning agent "{agent_type}" is foreground-only and cannot run in the background.'
            )
        if planning_only and bool(tool_input.get("isolated_workspace")):
            raise ValueError(
                f'Builtin planning agent "{agent_type}" is foreground-only and cannot run in an isolated workspace.'
            )
        run_in_background = bool(
            tool_input["run_in_background"]
            if "run_in_background" in tool_input
            else profile is not None and profile["run_in_background"]
        )
        isolated_workspace = bool(
            tool_input["isolated_workspace"]
            if "isolated_workspace" in tool_input
            else profile is not None and profile["isolated_workspace"]
        )
        forced_read_only = bool(getattr(ctx.session, "requires_read_only_subagents", lambda: False)())
        profile_requires_read_only = bool(profile is not None and profile["read_only"])
        read_only = forced_read_only or profile_requires_read_only or bool(
            tool_input["read_only"]
            if "read_only" in tool_input
            else profile is not None and profile["read_only"]
        )
        model_override = (
            str(profile["model_override"]).strip()
            if profile is not None and profile["model_override"]
            else None
        )
        if run_in_background:
            task_id = ctx.session.launch_background_agent(
                description=description,
                prompt=prompt,
                isolated_workspace=isolated_workspace,
                read_only=read_only,
                model_override=model_override,
                agent_type=agent_type,
            )
            agent_line = f"agent_type: {agent_type}\n" if agent_type else ""
            return (
                f"Background agent launched.\n"
                f"task_id: {task_id}\n"
                f"{agent_line}"
                f"isolated_workspace: {isolated_workspace}\n"
                f"read_only: {read_only}\n"
                f'Use the task_get tool with "{task_id}" to inspect progress.'
            )
        result = ctx.session.run_subagent(
            description=description,
            prompt=prompt,
            isolated_workspace=isolated_workspace,
            read_only=read_only,
            model_override=model_override,
            agent_type=agent_type,
        )
        contract = get_builtin_planning_agent_contract(agent_type)
        if contract is not None:
            return {
                "status": "completed",
                "agent_type": contract.name,
                "workflow_phase": contract.workflow_phase,
                "contribution_kind": contract.contribution_kind,
                "role_summary": contract.role_summary,
                "expected_output_sections": list(contract.output_expectations),
                "main_thread_usage": contract.main_thread_usage,
                "result_markdown": result,
            }
        mode = "read-only planning" if read_only else "standard"
        agent_label = f" type={agent_type}" if agent_type else ""
        return f"Sub-agent result ({mode}{agent_label}):\n{result}"
