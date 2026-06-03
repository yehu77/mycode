from pathlib import Path
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.config import SessionConfig
from claudecode_py.permissions import PermissionManager
from claudecode_py.session import Session
from claudecode_py.tasks import TaskManager
from claudecode_py.tools.base import ToolContext
from claudecode_py.tools.agent import AgentTool
from claudecode_py.tools.task_tools import TaskGetTool, TaskListTool, TaskStopTool, TaskWaitTool


class TaskToolsTests(unittest.TestCase):
    def test_task_stop_tool_stops_running_task(self) -> None:
        cwd = Path(__file__).resolve().parent
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        task_manager = TaskManager()
        task = task_manager.create("agent", "background work")
        ctx = ToolContext(
            cwd=cwd,
            permission_manager=PermissionManager(interactive=False),
            task_manager=task_manager,
            session=session,
        )

        stop_result = TaskStopTool().execute({"task_id": task.id}, ctx)
        task_info = TaskGetTool().execute({"task_id": task.id}, ctx)

        self.assertIn(f"Stopped task {task.id}", stop_result)
        self.assertIn("status: stopped", task_info)

    def test_task_wait_tool_waits_for_completion(self) -> None:
        cwd = Path(__file__).resolve().parent
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        task_manager = TaskManager()
        task = task_manager.create("agent", "background work")
        ctx = ToolContext(
            cwd=cwd,
            permission_manager=PermissionManager(interactive=False),
            task_manager=task_manager,
            session=session,
        )

        def complete_later() -> None:
            time.sleep(0.05)
            task_manager.complete(task.id, "done")

        thread = threading.Thread(target=complete_later, daemon=True)
        thread.start()
        wait_result = TaskWaitTool().execute({"task_id": task.id, "timeout_sec": 1}, ctx)
        thread.join(timeout=1)

        self.assertIn("status: completed", wait_result)
        self.assertIn("output_tail:\ndone", wait_result)

    def test_task_tools_render_background_reverse_hint(self) -> None:
        cwd = Path(__file__).resolve().parent
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        task_manager = TaskManager()
        task = task_manager.create(
            "agent",
            "background work",
            parent_session_id="session-bg",
            task_role="background",
        )
        ctx = ToolContext(
            cwd=cwd,
            permission_manager=PermissionManager(interactive=False),
            task_manager=task_manager,
            session=session,
        )

        listed = ctx.task_manager.list()
        self.assertEqual(len(listed), 1)
        task_list_output = TaskListTool().execute({}, ctx)
        task_get_output = TaskGetTool().execute({"task_id": task.id}, ctx)

        self.assertIn("background_session_id=session-bg", task_list_output)
        self.assertIn("background_reverse_hint=owning_session=session-bg; actions=/tasks active | /status workflow", task_list_output)
        self.assertIn("background_session_id: session-bg", task_get_output)
        self.assertIn("background_reverse_hint: owning_session=session-bg; actions=/tasks active | /status workflow", task_get_output)

    def test_task_tools_prefer_explicit_background_session_link(self) -> None:
        cwd = Path(__file__).resolve().parent
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        task_manager = TaskManager()
        task = task_manager.create(
            "agent",
            "background work",
            parent_session_id="session-bg",
            task_role="background",
            background_session_id="bg-123",
            background_reverse_hint="pyclaude ps bg-123 | pyclaude logs bg-123 summary",
        )
        ctx = ToolContext(
            cwd=cwd,
            permission_manager=PermissionManager(interactive=False),
            task_manager=task_manager,
            session=session,
        )

        task_list_output = TaskListTool().execute({}, ctx)
        task_get_output = TaskGetTool().execute({"task_id": task.id}, ctx)

        self.assertIn("background_session_id=bg-123", task_list_output)
        self.assertIn("background_reverse_hint=pyclaude ps bg-123 | pyclaude logs bg-123 summary", task_list_output)
        self.assertIn("background_session_id: bg-123", task_get_output)
        self.assertIn("background_reverse_hint: pyclaude ps bg-123 | pyclaude logs bg-123 summary", task_get_output)

    def test_agent_tool_passes_isolated_workspace_flag(self) -> None:
        cwd = Path(__file__).resolve().parent

        class FakeSession:
            def launch_background_agent(
                self,
                *,
                description: str,
                prompt: str,
                isolated_workspace: bool = False,
                read_only: bool = False,
            ):
                self.captured = (description, prompt, isolated_workspace, read_only)
                return "task-1"

        fake_session = FakeSession()
        ctx = ToolContext(
            cwd=cwd,
            permission_manager=PermissionManager(interactive=False),
            task_manager=TaskManager(),
            session=fake_session,  # type: ignore[arg-type]
        )

        result = AgentTool().execute(
            {
                "description": "demo",
                "prompt": "analyze",
                "run_in_background": True,
                "isolated_workspace": True,
            },
            ctx,
        )

        self.assertEqual(fake_session.captured, ("demo", "analyze", True, False))
        self.assertIn("isolated_workspace: True", result)

    def test_agent_tool_forces_read_only_when_session_requires_it(self) -> None:
        cwd = Path(__file__).resolve().parent

        class FakeSession:
            def requires_read_only_subagents(self) -> bool:
                return True

            def run_subagent(
                self,
                *,
                description: str,
                prompt: str,
                isolated_workspace: bool = False,
                read_only: bool = False,
            ):
                self.captured = (description, prompt, isolated_workspace, read_only)
                return "planned"

        fake_session = FakeSession()
        ctx = ToolContext(
            cwd=cwd,
            permission_manager=PermissionManager(interactive=False),
            task_manager=TaskManager(),
            session=fake_session,  # type: ignore[arg-type]
        )

        result = AgentTool().execute(
            {
                "description": "map the runtime",
                "prompt": "inspect the session factory",
                "read_only": False,
            },
            ctx,
        )

        self.assertEqual(
            fake_session.captured,
            ("map the runtime", "inspect the session factory", False, True),
        )
        self.assertIn("Sub-agent result (read-only planning):", result)


if __name__ == "__main__":
    unittest.main()
