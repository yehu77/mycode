from pathlib import Path
import json
import shutil
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.config import SessionConfig
from claudecode_py.permissions import PermissionDeniedError, PermissionManager
from claudecode_py.runtime.events import RuntimeEvent
from claudecode_py.runtime.orchestrator import ToolOrchestrator
from claudecode_py.session import Session
from claudecode_py.tasks import TaskManager
from claudecode_py.models import ToolCall
from claudecode_py.tools.base import (
    BaseTool,
    ToolContext,
    ToolContextUpdate,
    ToolExecutionPayload,
    ToolSessionMutation,
)
from claudecode_py.tools.read_file import ReadFileTool
from claudecode_py.tools.agent import AgentTool


class OrchestratorTests(unittest.TestCase):
    def test_orchestrator_runs_read_tool(self) -> None:
        tmp_path = Path(__file__).resolve().parent / "_tmp_orchestrator"
        if tmp_path.exists():
            shutil.rmtree(tmp_path)
        tmp_path.mkdir()
        file_path = tmp_path / "demo.txt"
        file_path.write_text("hello", encoding="utf-8")

        try:
            tool = ReadFileTool()
            orchestrator = ToolOrchestrator([tool])
            session = Session(SessionConfig(cwd=tmp_path, interactive=False))
            ctx = ToolContext(
                cwd=tmp_path,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )
            batch = orchestrator.execute_tool_calls(
                [ToolCall(id="1", name="read_file", input={"path": "demo.txt"})],
                ctx,
            )
            self.assertFalse(batch.tool_result_blocks[0]["is_error"])
            self.assertIn("hello", batch.tool_result_blocks[0]["content"])
        finally:
            if tmp_path.exists():
                shutil.rmtree(tmp_path)

    def test_orchestrator_emits_tool_events(self) -> None:
        class EchoTool(BaseTool):
            name = "echo"
            description = "Echo input."
            read_only = True
            concurrency_safe = True
            input_schema = {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            }

            def execute(self, tool_input: dict, ctx: ToolContext):
                return tool_input["text"]

        tmp_path = Path(__file__).resolve().parent
        session = Session(SessionConfig(cwd=tmp_path, interactive=False))
        ctx = ToolContext(
            cwd=tmp_path,
            permission_manager=PermissionManager(interactive=False),
            task_manager=TaskManager(),
            session=session,
        )
        events: list[RuntimeEvent] = []
        orchestrator = ToolOrchestrator([EchoTool()])
        batch = orchestrator.execute_tool_calls(
            [ToolCall(id="echo-1", name="echo", input={"text": "hi"})],
            ctx,
            sink=events.append,
        )

        self.assertEqual(batch.tool_result_blocks[0]["content"], "hi")
        self.assertEqual(
            [event.kind for event in events],
            [
                "tool_batch_started",
                "tool_waiting_for_approval",
                "tool_started",
                "tool_finished",
                "tool_batch_finished",
            ],
        )
        self.assertEqual(events[0].batch_size, 1)
        self.assertTrue(events[0].batch_parallel)
        self.assertEqual(events[1].approval_risk_level, "read")
        self.assertEqual(events[1].tool_name, "echo")
        self.assertEqual(events[2].tool_name, "echo")

    def test_orchestrator_emits_parallel_batch_lifecycle(self) -> None:
        class EchoTool(BaseTool):
            name = "echo"
            description = "Echo input."
            read_only = True
            concurrency_safe = True
            input_schema = {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            }

            def execute(self, tool_input: dict, ctx: ToolContext):
                return tool_input["text"]

        tmp_path = Path(__file__).resolve().parent
        session = Session(SessionConfig(cwd=tmp_path, interactive=False))
        ctx = ToolContext(
            cwd=tmp_path,
            permission_manager=PermissionManager(interactive=False),
            task_manager=TaskManager(),
            session=session,
        )
        events: list[RuntimeEvent] = []
        orchestrator = ToolOrchestrator([EchoTool()])

        orchestrator.execute_tool_calls(
            [
                ToolCall(id="echo-1", name="echo", input={"text": "hi"}),
                ToolCall(id="echo-2", name="echo", input={"text": "there"}),
            ],
            ctx,
            sink=events.append,
        )

        self.assertEqual(events[0].kind, "tool_batch_started")
        self.assertEqual(events[0].batch_size, 2)
        self.assertTrue(events[0].batch_parallel)
        self.assertEqual(events[-1].kind, "tool_batch_finished")
        self.assertEqual(events[-1].result_count, 2)

    def test_orchestrator_emits_waiting_then_failed_on_approval_deny(self) -> None:
        class EchoTool(BaseTool):
            name = "echo"
            description = "Echo input."
            read_only = False
            concurrency_safe = False
            risk_level = "write"
            input_schema = {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            }

            def execute(self, tool_input: dict, ctx: ToolContext):
                return tool_input["text"]

        class DenyPermissionManager:
            def require_approval(self, request) -> None:
                raise PermissionDeniedError(f'Denied {request.tool_name}')

        tmp_path = Path(__file__).resolve().parent
        session = Session(SessionConfig(cwd=tmp_path, interactive=False))
        ctx = ToolContext(
            cwd=tmp_path,
            permission_manager=DenyPermissionManager(),
            task_manager=TaskManager(),
            session=session,
        )
        events: list[RuntimeEvent] = []
        orchestrator = ToolOrchestrator([EchoTool()])

        batch = orchestrator.execute_tool_calls(
            [ToolCall(id="echo-1", name="echo", input={"text": "hi"})],
            ctx,
            sink=events.append,
        )

        self.assertTrue(batch.tool_result_blocks[0]["is_error"])
        self.assertEqual(events[1].kind, "tool_waiting_for_approval")
        self.assertEqual(events[1].approval_risk_level, "write")
        self.assertEqual(events[2].kind, "tool_failed")

    def test_orchestrator_returns_inline_messages_and_context_updates(self) -> None:
        class InlineTool(BaseTool):
            name = "inline"
            description = "Return inline updates."
            read_only = True
            concurrency_safe = True
            input_schema = {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            }

            def execute(self, tool_input: dict, ctx: ToolContext):
                return ToolExecutionPayload(
                    result={"status": "inline"},
                    new_messages=[
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": str(tool_input["text"])}],
                            "source_kind": "skill_tool_inline",
                            "source_tool_name": "skill",
                            "source_tool_use_id": ctx.tool_call_id,
                            "skill_name": "ship",
                            "skill_execution_context": "inline",
                        }
                    ],
                    context_update=ToolContextUpdate(
                        allowed_tool_names=("read_file", "bash"),
                        source="skill_tool_inline",
                        skill_name="ship",
                    ),
                    session_mutation=ToolSessionMutation(
                        kind="plan_mode_entered",
                        source_tool_name="inline",
                        source_tool_call_id=ctx.tool_call_id,
                        plan_file_path="plan.md",
                    ),
                )

        tmp_path = Path(__file__).resolve().parent
        session = Session(SessionConfig(cwd=tmp_path, interactive=False))
        ctx = ToolContext(
            cwd=tmp_path,
            permission_manager=PermissionManager(interactive=False),
            task_manager=TaskManager(),
            session=session,
        )
        orchestrator = ToolOrchestrator([InlineTool()])

        batch = orchestrator.execute_tool_calls(
            [ToolCall(id="inline-1", name="inline", input={"text": "hello"})],
            ctx,
        )

        self.assertEqual(batch.tool_result_blocks[0]["content"], '{\n  "status": "inline"\n}')
        self.assertEqual(batch.inline_message_count, 1)
        self.assertEqual(batch.new_messages[0]["source_tool_use_id"], "inline-1")
        self.assertEqual(batch.context_updates[0].skill_name, "ship")
        self.assertEqual(batch.session_mutations[0].kind, "plan_mode_entered")
        self.assertEqual(batch.session_mutations[0].source_tool_call_id, "inline-1")

    def test_orchestrator_formats_planning_agent_result_as_structured_json(self) -> None:
        tmp_path = Path(__file__).resolve().parent

        class AllowPermissionManager:
            def require_approval(self, request) -> None:
                return None

        class FakeSession:
            def validate_tool_call_policy(self, tool_name: str, tool_input: dict) -> None:
                return None

            def resolve_agent_runtime_profile(self, agent_type: str | None):
                return {
                    "name": "Explore",
                    "execution": "child-session",
                    "tool_policy": "read-only-subagent",
                    "model_override": None,
                    "read_only": True,
                    "run_in_background": False,
                    "isolated_workspace": False,
                    "planning_only": True,
                }

            def run_subagent(
                self,
                *,
                description: str,
                prompt: str,
                isolated_workspace: bool = False,
                read_only: bool = False,
                model_override: str | None = None,
                agent_type: str | None = None,
            ) -> str:
                return "## Relevant Files\n- session.py"

        ctx = ToolContext(
            cwd=tmp_path,
            permission_manager=AllowPermissionManager(),
            task_manager=TaskManager(),
            session=FakeSession(),  # type: ignore[arg-type]
        )
        orchestrator = ToolOrchestrator([AgentTool()])

        batch = orchestrator.execute_tool_calls(
            [
                ToolCall(
                    id="agent-1",
                    name="agent",
                    input={
                        "agent_type": "Explore",
                        "description": "map existing runtime paths",
                        "prompt": "find reuse candidates",
                    },
                )
            ],
            ctx,
        )

        payload = json.loads(batch.tool_result_blocks[0]["content"])
        self.assertEqual(payload["agent_type"], "Explore")
        self.assertEqual(payload["workflow_phase"], "phase_1_initial_understanding")
        self.assertEqual(payload["contribution_kind"], "reconnaissance_findings")
        self.assertEqual(payload["result_markdown"], "## Relevant Files\n- session.py")


if __name__ == "__main__":
    unittest.main()
