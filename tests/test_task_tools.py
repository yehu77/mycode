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
                model_override: str | None = None,
                agent_type: str | None = None,
            ):
                self.captured = (
                    description,
                    prompt,
                    isolated_workspace,
                    read_only,
                    model_override,
                    agent_type,
                )
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

        self.assertEqual(fake_session.captured, ("demo", "analyze", True, False, None, None))
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
                model_override: str | None = None,
                agent_type: str | None = None,
            ):
                self.captured = (
                    description,
                    prompt,
                    isolated_workspace,
                    read_only,
                    model_override,
                    agent_type,
                )
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
            ("map the runtime", "inspect the session factory", False, True, None, None),
        )
        self.assertIn("Sub-agent result (read-only planning):", result)

    def test_agent_tool_named_explore_agent_forces_read_only_profile(self) -> None:
        cwd = Path(__file__).resolve().parent

        class FakeSession:
            def resolve_agent_runtime_profile(self, agent_type: str | None):
                self.requested_agent_type = agent_type
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
            ):
                self.captured = (
                    description,
                    prompt,
                    isolated_workspace,
                    read_only,
                    model_override,
                    agent_type,
                )
                return "recon"

        fake_session = FakeSession()
        ctx = ToolContext(
            cwd=cwd,
            permission_manager=PermissionManager(interactive=False),
            task_manager=TaskManager(),
            session=fake_session,  # type: ignore[arg-type]
        )

        result = AgentTool().execute(
            {
                "agent_type": "Explore",
                "description": "map existing runtime paths",
                "prompt": "find reuse candidates",
                "read_only": False,
            },
            ctx,
        )

        payload = result

        self.assertEqual(fake_session.requested_agent_type, "Explore")
        self.assertEqual(
            fake_session.captured,
            ("map existing runtime paths", "find reuse candidates", False, True, None, "Explore"),
        )
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["agent_type"], "Explore")
        self.assertEqual(payload["workflow_phase"], "phase_1_initial_understanding")
        self.assertEqual(payload["contribution_kind"], "reconnaissance_findings")
        self.assertIn("Phase 1 reconnaissance", payload["role_summary"])
        self.assertIn("Relevant files and modules", payload["expected_output_sections"])
        self.assertIn("Phase 2 design", payload["main_thread_usage"])
        self.assertEqual(payload["result_markdown"], "recon")

    def test_agent_tool_named_plan_agent_resolves_read_only_profile(self) -> None:
        cwd = Path(__file__).resolve().parent

        class FakeSession:
            def resolve_agent_runtime_profile(self, agent_type: str | None):
                self.requested_agent_type = agent_type
                return {
                    "name": "Plan",
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
            ):
                self.captured = (
                    description,
                    prompt,
                    isolated_workspace,
                    read_only,
                    model_override,
                    agent_type,
                )
                return "designed"

        fake_session = FakeSession()
        ctx = ToolContext(
            cwd=cwd,
            permission_manager=PermissionManager(interactive=False),
            task_manager=TaskManager(),
            session=fake_session,  # type: ignore[arg-type]
        )

        result = AgentTool().execute(
            {
                "agent_type": "Plan",
                "description": "design the implementation",
                "prompt": "use the exploration results",
                "read_only": False,
            },
            ctx,
        )

        payload = result

        self.assertEqual(fake_session.requested_agent_type, "Plan")
        self.assertEqual(
            fake_session.captured,
            ("design the implementation", "use the exploration results", False, True, None, "Plan"),
        )
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["agent_type"], "Plan")
        self.assertEqual(payload["workflow_phase"], "phase_2_design")
        self.assertEqual(payload["contribution_kind"], "implementation_design")
        self.assertIn("Phase 2 design agent", payload["role_summary"])
        self.assertIn("Ordered implementation steps", payload["expected_output_sections"])
        self.assertIn("final plan writing", payload["main_thread_usage"])
        self.assertEqual(payload["result_markdown"], "designed")

    def test_agent_tool_named_planning_agent_rejects_background_override(self) -> None:
        cwd = Path(__file__).resolve().parent

        class FakeSession:
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

        ctx = ToolContext(
            cwd=cwd,
            permission_manager=PermissionManager(interactive=False),
            task_manager=TaskManager(),
            session=FakeSession(),  # type: ignore[arg-type]
        )

        with self.assertRaises(ValueError) as denied:
            AgentTool().execute(
                {
                    "agent_type": "Explore",
                    "description": "map existing runtime paths",
                    "prompt": "find reuse candidates",
                    "run_in_background": True,
                },
                ctx,
            )
        self.assertIn("foreground-only", str(denied.exception))

    def test_agent_tool_named_planning_agent_rejects_isolated_workspace_override(self) -> None:
        cwd = Path(__file__).resolve().parent

        class FakeSession:
            def resolve_agent_runtime_profile(self, agent_type: str | None):
                return {
                    "name": "Plan",
                    "execution": "child-session",
                    "tool_policy": "read-only-subagent",
                    "model_override": None,
                    "read_only": True,
                    "run_in_background": False,
                    "isolated_workspace": False,
                    "planning_only": True,
                }

        ctx = ToolContext(
            cwd=cwd,
            permission_manager=PermissionManager(interactive=False),
            task_manager=TaskManager(),
            session=FakeSession(),  # type: ignore[arg-type]
        )

        with self.assertRaises(ValueError) as denied:
            AgentTool().execute(
                {
                    "agent_type": "Plan",
                    "description": "design the implementation",
                    "prompt": "use the exploration results",
                    "isolated_workspace": True,
                },
                ctx,
            )
        self.assertIn("isolated workspace", str(denied.exception))


if __name__ == "__main__":
    unittest.main()
