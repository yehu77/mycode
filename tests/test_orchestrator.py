from pathlib import Path
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
from claudecode_py.tools.base import BaseTool, ToolContext
from claudecode_py.tools.read_file import ReadFileTool


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
            blocks = orchestrator.execute_tool_calls(
                [ToolCall(id="1", name="read_file", input={"path": "demo.txt"})],
                ctx,
            )
            self.assertFalse(blocks[0]["is_error"])
            self.assertIn("hello", blocks[0]["content"])
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
        blocks = orchestrator.execute_tool_calls(
            [ToolCall(id="echo-1", name="echo", input={"text": "hi"})],
            ctx,
            sink=events.append,
        )

        self.assertEqual(blocks[0]["content"], "hi")
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

        blocks = orchestrator.execute_tool_calls(
            [ToolCall(id="echo-1", name="echo", input={"text": "hi"})],
            ctx,
            sink=events.append,
        )

        self.assertTrue(blocks[0]["is_error"])
        self.assertEqual(events[1].kind, "tool_waiting_for_approval")
        self.assertEqual(events[1].approval_risk_level, "write")
        self.assertEqual(events[2].kind, "tool_failed")


if __name__ == "__main__":
    unittest.main()
