from __future__ import annotations

from ..permissions import ApprovalRequest
from .base import BaseTool, ToolExecutionPayload, ToolSessionMutation


class EnterPlanModeTool(BaseTool):
    name = "EnterPlanMode"
    description = "Enter plan mode for non-trivial implementation tasks that need read-only exploration and plan approval before repo edits."
    read_only = False
    concurrency_safe = False
    input_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def approval_request(self, tool_input: dict[str, object], ctx=None) -> ApprovalRequest:
        del tool_input
        current_mode = "unknown"
        if ctx is not None:
            current_mode = ctx.session.session_runtime_mode()
        return ApprovalRequest(
            tool_name=self.name,
            reason="Claude wants to enter plan mode.",
            risk_level="mode",
            approval_key="enter_plan_mode",
            details=(
                "Entering plan mode means read-only exploration plus editing only the current "
                "session plan file.\n"
                f"current_mode: {current_mode}"
            ),
        )

    def execute(self, tool_input: dict, ctx):
        del tool_input
        if ctx.session.state.session_execution_mode != "main":
            raise ValueError("EnterPlanMode is only available in main sessions.")
        if ctx.session.in_plan_mode():
            raise ValueError("Session is already in plan mode.")
        plan_file = ctx.session.enter_plan_mode()
        workflow_mode = ctx.session.plan_workflow_mode()
        workflow_hint = (
            "Detailed iterative interview workflow instructions will appear in the next provider call."
            if workflow_mode == "interview"
            else "Detailed 5-phase workflow instructions will appear in the next provider call."
        )
        return ToolExecutionPayload(
            result=(
                "Plan mode enabled.\n"
                f"plan_file: {plan_file}\n"
                "You are now in read-only exploration mode.\n"
                "- Explore with read-only tools and read-only shell commands.\n"
                "- Only modify the current session plan file.\n"
                "- Use ask_user_question if you need clarification.\n"
                "- When the plan is ready, call ExitPlanMode.\n"
                f"- {workflow_hint}"
            ),
            session_mutation=ToolSessionMutation(
                kind="plan_mode_entered",
                source_tool_name=self.name,
                source_tool_call_id=ctx.tool_call_id,
                plan_file_path=str(plan_file),
            ),
        )
