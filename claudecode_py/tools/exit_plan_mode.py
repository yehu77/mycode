from __future__ import annotations

from ..permissions import ApprovalRequest
from .base import BaseTool, ToolExecutionPayload, ToolSessionMutation


class ExitPlanModeTool(BaseTool):
    name = "ExitPlanMode"
    description = "Request approval for the current session plan file and exit plan mode if approved."
    read_only = False
    concurrency_safe = False
    input_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def approval_request(self, tool_input: dict[str, object], ctx=None) -> ApprovalRequest:
        del tool_input, ctx
        return ApprovalRequest(
            tool_name=self.name,
            reason="Prepare the current session plan file for plan-mode exit approval.",
            risk_level="read",
            approval_key="read",
        )

    def execute(self, tool_input: dict, ctx):
        del tool_input
        if ctx.session.state.session_execution_mode != "main":
            raise ValueError("ExitPlanMode is only available in main sessions.")
        if not ctx.session.in_plan_mode():
            raise ValueError("ExitPlanMode can only be used while plan mode is active.")
        plan_file = ctx.session.get_plan_file_path()
        if not plan_file.exists():
            raise ValueError("Current session plan file does not exist.")
        plan_content = ctx.session.get_plan().strip()
        if not plan_content:
            raise ValueError("Current session plan file is empty.")
        return ToolExecutionPayload(
            result=(
                "Plan mode exit requested.\n"
                f"plan_file: {plan_file}\n"
                "Waiting for approval of the current session plan file."
            ),
            session_mutation=ToolSessionMutation(
                kind="plan_mode_exit_requested",
                source_tool_name=self.name,
                source_tool_call_id=ctx.tool_call_id,
                plan_file_path=str(plan_file),
                plan_content=plan_content,
            ),
        )
