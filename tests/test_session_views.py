from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.config import SessionConfig
from claudecode_py.mcp import McpClient, McpRegistry, McpServerConfig
from claudecode_py.permissions import ApprovalResult, PermissionDeniedError
from claudecode_py.session import Session
from claudecode_py.session_factory import SessionFactory
from claudecode_py.state import AdvisorReviewSummary, PlanningArtifact, SessionState, WorkspaceChangeSet, WorkspaceFileChange
from claudecode_py.storage.transcript import load_transcript, load_transcript_by_session_id, save_transcript
from claudecode_py.tools.base import ToolContext
from claudecode_py.permissions import PermissionManager
from claudecode_py.runtime.events import RuntimeEvent
from claudecode_py.storage.background_sessions import create_background_session, update_background_session
from claudecode_py.tasks import TaskManager
from claudecode_py.tools.write_file import WriteFileTool


class FakeTransport:
    def request(self, method: str, params: dict | None = None) -> dict:
        if method == "initialize":
            return {
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"version": "1.0.0"},
                    "capabilities": {"tools": {}},
                }
            }
        if method == "tools/list":
            return {
                "result": {
                    "tools": [
                        {
                            "name": "echo_text",
                            "description": "Return text",
                            "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
                        }
                    ]
                }
            }
        if method == "resources/list":
            return {
                "result": {
                    "resources": [
                        {
                            "uri": "docs://guide",
                            "name": "Guide",
                            "mimeType": "text/plain",
                        }
                    ]
                }
            }
        if method == "tools/call":
            assert params is not None
            return {
                "result": {
                    "content": [{"type": "text", "text": f"echo:{params['arguments']['text']}"}],
                    "isError": False,
                }
            }
        raise AssertionError(f"unexpected method: {method}")

    def close(self) -> None:
        return None


class BrokenTransport:
    def request(self, method: str, params: dict | None = None) -> dict:
        if method == "initialize":
            return {"error": {"code": -1, "message": "connection failed"}}
        raise AssertionError(f"unexpected method: {method}")

    def close(self) -> None:
        return None


class SessionViewsTests(unittest.TestCase):
    def test_describe_tools_lists_registered_tools(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        rendered = session.describe_tools()
        self.assertIn("list_dir:", rendered)
        self.assertIn("read_file:", rendered)
        self.assertIn("task_stop:", rendered)

    def test_describe_permissions_explains_segment_aware_shell_rules(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))

        rendered = session.describe_permissions()

        self.assertIn("shell rules match the prefix of any analyzed bash command segment", rendered)
        self.assertIn("path rules match workspace-relative targets", rendered)
        self.assertIn("shell rules are permission-layer approvals; command mode policy is a separate per-turn restriction layer", rendered)

    def test_runtime_event_summary_includes_permission_and_command_mode_context(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))

        summary = session._summarize_runtime_event(
            RuntimeEvent(
                kind="tool_failed",
                message='Bash command is not allowed in command mode "review".',
                tool_name="bash",
                duration_ms=6,
                is_error=True,
                decision_reason="Matched ask rules: ask:shell:git diff [segment 1: git diff]",
                command_mode_name="review",
                command_mode_allowed_prefixes=("git diff", "git show"),
                command_mode_violating_segment="Set-Content out.txt",
                command_mode_violating_segment_index=2,
                command_mode_complex_features=("command_substitution",),
            )
        )

        self.assertIn('[tool:error] bash (6ms): Bash command is not allowed in command mode "review".', summary)
        self.assertIn("policy=Matched ask rules: ask:shell:git diff [segment 1: git diff]", summary)
        self.assertIn("mode=review", summary)
        self.assertIn("segment=segment 2: Set-Content out.txt", summary)
        self.assertIn("complex_feature=command_substitution", summary)

    def test_describe_history_summarizes_messages(self) -> None:
        state = SessionState(
            messages=[
                {"role": "user", "content": [{"type": "text", "text": "hello"}]},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "working on it"},
                        {"type": "tool_use", "id": "1", "name": "list_dir", "input": {"path": "."}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "1", "content": ".", "is_error": False}
                    ],
                },
            ]
        )
        session = Session(
            SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False),
            state=state,
        )
        rendered = session.describe_history()
        self.assertIn("recent messages:", rendered)
        self.assertIn("1. user: hello", rendered)
        self.assertIn("tool_use=list_dir", rendered)
        self.assertIn("tool_result=1 block", rendered)

    def test_describe_history_surfaces_tool_result_error_context(self) -> None:
        state = SessionState(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "1",
                            "content": (
                                'Bash command is not allowed in command mode "review". '
                                "[policy=Matched deny rules: deny:path:../outside.txt | "
                                "mode=review | segment=segment 2: Set-Content ../outside.txt]"
                            ),
                            "is_error": True,
                        }
                    ],
                }
            ]
        )
        session = Session(
            SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False),
            state=state,
        )

        rendered = session.describe_history()

        self.assertIn("tool_result=1 block", rendered)
        self.assertIn('tool_error=Bash command is not allowed in command mode "review".', rendered)
        self.assertIn("deny:path:../outside.txt", rendered)
        self.assertIn("mode=review", rendered)

    def test_describe_history_includes_workspace_audit_summaries(self) -> None:
        state = SessionState(
            messages=[
                {"role": "user", "content": [{"type": "text", "text": "clean orphaned workspaces"}]},
            ],
            recent_change_sets=[
                WorkspaceChangeSet(
                    tool_name="workspace_cleanup",
                    summary="Deleted 1 orphaned isolated workspace(s).",
                    change_kind="workspace_audit",
                    undoable=False,
                    files=[
                        WorkspaceFileChange(
                            path=".pyclaude/workspaces/orphan-agent",
                            existed_before=True,
                            before_content="",
                            after_content=None,
                            action_kind="delete",
                            change_mode="workspace_cleanup",
                        )
                    ],
                )
            ],
        )
        session = Session(
            SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False),
            state=state,
        )

        rendered = session.describe_history()

        self.assertIn("1. user: clean orphaned workspaces", rendered)
        self.assertIn("workspace audit:", rendered)
        self.assertIn("cleanup applied | Deleted 1 orphaned isolated workspace(s).", rendered)
        self.assertIn("paths=.pyclaude/workspaces/orphan-agent", rendered)

    def test_describe_history_can_render_workspace_audit_without_messages(self) -> None:
        state = SessionState(
            recent_change_sets=[
                WorkspaceChangeSet(
                    tool_name="workspace_cleanup",
                    summary="Deleted 2 orphaned isolated workspace(s).",
                    change_kind="workspace_audit",
                    undoable=False,
                    files=[
                        WorkspaceFileChange(
                            path=".pyclaude/workspaces/orphan-a",
                            existed_before=True,
                            before_content="",
                            after_content=None,
                            action_kind="delete",
                            change_mode="workspace_cleanup",
                        ),
                        WorkspaceFileChange(
                            path=".pyclaude/worktrees/orphan-b",
                            existed_before=True,
                            before_content="",
                            after_content=None,
                            action_kind="delete",
                            change_mode="workspace_cleanup",
                        ),
                    ],
                )
            ],
        )
        session = Session(
            SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False),
            state=state,
        )

        rendered = session.describe_history()

        self.assertIn("workspace audit:", rendered)
        self.assertIn("cleanup applied | Deleted 2 orphaned isolated workspace(s).", rendered)
        self.assertIn("paths=.pyclaude/workspaces/orphan-a, .pyclaude/worktrees/orphan-b", rendered)

    def test_describe_history_supports_filtered_sections(self) -> None:
        state = SessionState(
            messages=[{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
            recent_change_sets=[
                WorkspaceChangeSet(
                    tool_name="apply_patch",
                    summary="Updated runtime/session.py",
                    files=[
                        WorkspaceFileChange(
                            path="runtime/session.py",
                            existed_before=True,
                            before_content="old\n",
                            after_content="new\n",
                            action_kind="update",
                        )
                    ],
                )
            ],
        )
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False), state=state)

        rendered_messages = session.describe_history(section="messages")
        rendered_changes = session.describe_history(section="changes")

        self.assertIn("recent messages:", rendered_messages)
        self.assertNotIn("recent changes:", rendered_messages)
        self.assertIn("recent changes:", rendered_changes)
        self.assertIn("Working set:", rendered_changes)

    def test_describe_tasks_includes_workspace_audit_summaries(self) -> None:
        state = SessionState(
            recent_change_sets=[
                WorkspaceChangeSet(
                    tool_name="workspace_cleanup",
                    summary="Deleted 1 orphaned isolated workspace(s).",
                    change_kind="workspace_audit",
                    undoable=False,
                    files=[
                        WorkspaceFileChange(
                            path=".pyclaude/worktrees/orphan-agent",
                            existed_before=True,
                            before_content="",
                            after_content=None,
                            action_kind="delete",
                            change_mode="workspace_cleanup",
                        )
                    ],
                )
            ],
        )
        session = Session(
            SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False),
            state=state,
        )

        rendered = session.describe_tasks()

        self.assertIn("workspace_audit:", rendered)
        self.assertIn("cleanup applied | Deleted 1 orphaned isolated workspace(s).", rendered)
        self.assertIn("paths=.pyclaude/worktrees/orphan-agent", rendered)
        self.assertIn("No tasks.", rendered)

    def test_describe_tasks_distinguishes_task_surfaces(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        try:
            session.create_checklist_task(
                subject="Inspect runtime",
                description="Review session task surfaces",
                active_form="Inspecting runtime",
                status="pending",
            )
            workspace_task = session.task_manager.create(
                "workspace",
                "Clean orphaned isolated workspaces",
                workspace_action="cleanup",
                workspace_target="all",
            )
            child_task = session.task_manager.create(
                "ultraplan_scout",
                "Scout runtime boundaries",
                task_role="scout",
                child_execution_mode="read-only-subagent",
                child_command_policy_name="read-only-subagent",
                child_command_policy_allowed_tool_names=["read_file", "bash"],
                child_command_policy_allowed_bash_prefixes=["git status"],
                child_command_policy_require_read_only_subagents=True,
            )
            background_task = session.task_manager.create(
                "agent",
                "Run background scan",
                task_role="background",
                child_execution_mode="background-agent",
            )
            execution_task = session.task_manager.create(
                "plan_execution",
                "Apply runtime patch",
                task_role="execution",
                plan_execution_mode="interactive_turn",
            )

            rendered = session.describe_tasks()

            self.assertIn("task_surfaces:", rendered)
            self.assertIn("checklist: 1", rendered)
            self.assertIn("workspace_maintenance: 1", rendered)
            self.assertIn("child_execution: 1", rendered)
            self.assertIn("background_execution: 1", rendered)
            self.assertIn("active_plan_execution: 1", rendered)
            self.assertIn("workspace maintenance tasks:", rendered)
            self.assertIn("child execution tasks:", rendered)
            self.assertIn("background execution tasks:", rendered)
            self.assertIn("active plan execution tasks:", rendered)
            self.assertIn(f"{workspace_task.id}  status=running  kind=workspace", rendered)
            self.assertIn("task_surface=workspace_maintenance", rendered)
            self.assertIn(f"{child_task.id}  status=running  kind=ultraplan_scout", rendered)
            self.assertIn("task_surface=child_execution", rendered)
            self.assertIn("execution=read-only-subagent", rendered)
            self.assertIn("allowed_tools=2", rendered)
            self.assertIn("allowed_bash_prefixes=1", rendered)
            self.assertIn("read_only_subagents=yes", rendered)
            self.assertIn(f"{background_task.id}  status=running  kind=agent", rendered)
            self.assertIn("task_surface=background_execution", rendered)
            self.assertIn("execution=background-agent", rendered)
            self.assertIn(f"{execution_task.id}  status=running  kind=plan_execution", rendered)
            self.assertIn("task_surface=active_plan_execution", rendered)
        finally:
            session.close()

    def test_describe_tasks_supports_workflow_filters_and_next_actions(self) -> None:
        cwd = Path(__file__).resolve().parent / f"_tmp_task_workflow_{uuid4().hex}"
        cwd.mkdir(parents=True, exist_ok=True)
        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.record_workspace_change(
                tool_name="apply_patch",
                summary="Update runtime flow",
                file_changes=[
                    WorkspaceFileChange(
                        path="runtime/session.py",
                        existed_before=True,
                        before_content="old\n",
                        after_content="new\n",
                        action_kind="update",
                    )
                ],
            )
            change_task = session.task_manager.create(
                "plan_execution",
                "Apply runtime patch",
                task_role="execution",
                plan_execution_mode="interactive_turn",
            )
            context_task = session.task_manager.create(
                "agent",
                "Review docs context",
                workspace_planned_paths=["docs/context.md"],
            )

            rendered = session.describe_tasks()
            rendered_changes = session.describe_tasks(mode="changes")
            rendered_context = session.describe_tasks(mode="context")

            self.assertIn("task workflow overview:", rendered)
            self.assertIn(f"{change_task.id}  status=running  kind=plan_execution", rendered)
            self.assertIn("focused file: runtime/session.py", rendered)
            self.assertIn("related change:", rendered)
            self.assertIn("diff hunks: 1", rendered)
            self.assertIn("next_actions:", rendered)
            self.assertIn("go_to_task: /task show " + change_task.id, rendered)
            self.assertIn("/task advisor " + change_task.id, rendered)
            self.assertIn("/task drift " + change_task.id, rendered)
            self.assertIn("go_to_change: /changes show", rendered)
            self.assertIn("go_to_plan: /plan execution", rendered)
            self.assertIn("stay_on_surface: /tasks list | /tasks active | /tasks changes | /tasks context", rendered)
            self.assertIn("filter: changes", rendered_changes)
            self.assertIn(change_task.id, rendered_changes)
            self.assertNotIn(context_task.id, rendered_changes)
            self.assertIn("filter: context", rendered_context)
            self.assertIn(context_task.id, rendered_context)
            self.assertIn("focused file: docs/context.md", rendered_context)
            self.assertIn("context-only: yes", rendered_context)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_describe_history_includes_recent_task_activity_by_surface(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        try:
            checklist = session.create_checklist_task(
                subject="Inspect runtime",
                description="Review session task surfaces",
                active_form="Inspecting runtime",
                status="in_progress",
            )
            session.task_manager.create(
                "workspace",
                "Repair unavailable isolated workspaces",
                workspace_action="repair",
                workspace_target="all",
            )
            session.task_manager.create(
                "agent",
                "Run background scan",
                task_role="background",
                child_execution_mode="background-agent",
            )
            session.task_manager.create(
                "ultraplan_scout",
                "Scout runtime boundaries",
                task_role="scout",
                child_execution_mode="read-only-subagent",
            )

            rendered = session.describe_history()

            self.assertIn("recent task activity:", rendered)
            self.assertIn("task_surface=checklist", rendered)
            self.assertIn(f"task={checklist['id']}", rendered)
            self.assertIn("task_surface=workspace_maintenance", rendered)
            self.assertIn("task_surface=background_execution", rendered)
            self.assertIn("task_surface=child_execution", rendered)
            self.assertIn("read_only_subagents=yes", rendered)
        finally:
            session.close()

    def test_task_execution_detail_metadata_and_rendering_include_execution_contract(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        try:
            task = session.task_manager.create(
                "ultraplan_scout",
                "Scout runtime boundaries",
                task_role="scout",
                child_execution_mode="read-only-subagent",
                child_command_policy_name="read-only-subagent",
                child_command_policy_source="subagent",
                child_command_policy_allowed_tool_names=["read_file", "bash"],
                child_command_policy_allowed_bash_prefixes=["git status"],
                child_command_policy_require_read_only_subagents=True,
                workspace_mode="snapshot",
                workspace_health="cleanup_pending",
            )

            rendered = session.describe_task_detail(task.id)
            metadata = session.task_execution_detail_metadata(task.id)

            assert metadata is not None
            self.assertEqual(metadata["task_surface"], "child_execution")
            self.assertEqual(metadata["execution_mode"], "read-only-subagent")
            self.assertEqual(metadata["execution_policy"], "read-only-subagent")
            self.assertEqual(metadata["execution_policy_source"], "subagent")
            self.assertEqual(metadata["allowed_tools"], ["read_file", "bash"])
            self.assertEqual(metadata["allowed_bash_prefixes"], ["git status"])
            self.assertTrue(metadata["read_only_subagents"])
            self.assertEqual(metadata["workspace_mode"], "snapshot")
            self.assertEqual(metadata["workspace_health"], "cleanup_pending")
            self.assertIn("execution_contract:", rendered)
            self.assertIn("- task_surface: child_execution", rendered)
            self.assertIn("- execution_mode: read-only-subagent", rendered)
            self.assertIn("- execution_policy: read-only-subagent", rendered)
            self.assertIn("- execution_policy_source: subagent", rendered)
            self.assertIn("- allowed_tools: read_file, bash", rendered)
            self.assertIn("- allowed_bash_prefixes: git status", rendered)
            self.assertIn("- read_only_subagents: yes", rendered)
            self.assertIn("- workspace_mode: snapshot", rendered)
            self.assertIn("- workspace_health: cleanup_pending", rendered)
        finally:
            session.close()

    def test_describe_config_includes_runtime_settings(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
                provider="openai-compatible",
                model="gpt-test",
                max_turns=7,
            )
        )

        rendered = session.describe_config()

        self.assertIn("provider: openai-compatible", rendered)
        self.assertIn("model: gpt-test", rendered)
        self.assertIn("workspace_mode: main", rendered)
        self.assertIn("original_cwd:", rendered)
        self.assertIn("effective_cwd:", rendered)
        self.assertIn("mcp_config_path:", rendered)
        self.assertIn("mcp_servers: 0", rendered)
        self.assertIn("mcp_connected_servers: 0", rendered)
        self.assertIn("mcp_failed_servers: 0", rendered)
        self.assertIn("mcp_retrying_servers: 0", rendered)
        self.assertIn("max_turns: 7", rendered)
        self.assertIn("advisor_blocks: 0", rendered)
        self.assertIn("execution_constraints: normal", rendered)
        self.assertIn("session_execution_mode: main", rendered)
        self.assertIn("session_command_policy_name: none", rendered)
        self.assertIn("session_id:", rendered)

    def test_describe_config_supports_workspace_runtime_permission_plugin_and_mcp_slices(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
                provider="openai-compatible",
                model="gpt-test",
            )
        )

        rendered_workspace = session.describe_config(section="workspace")
        rendered_runtime = session.describe_config(section="runtime")
        rendered_permissions = session.describe_config(section="permissions")
        rendered_plugins = session.describe_config(section="plugins")
        rendered_mcp = session.describe_config(section="mcp")

        self.assertIn("current session:", rendered_workspace)
        self.assertIn("workspace_mode: main", rendered_workspace)
        self.assertIn("primary action:", rendered_workspace)
        self.assertIn("provider: openai-compatible", rendered_runtime)
        self.assertIn("model: gpt-test", rendered_runtime)
        self.assertIn("permission_mode:", rendered_permissions)
        self.assertIn("project_plugins:", rendered_plugins)
        self.assertIn("mcp_config_path:", rendered_mcp)
        self.assertNotIn("project_plugins:", rendered_mcp)

    def test_describe_provider_supports_advisor_slice(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
                provider="openai-compatible",
                model="gpt-test",
                api_key="k",
                base_url="https://example.test/v1",
            )
        )
        session.state.advisor_model = "advisor-test"
        session.state.advisor_mode = "interactive-review"

        rendered = session.describe_provider(section="advisor")

        self.assertIn("current session:", rendered)
        self.assertIn("runtime model: gpt-test", rendered)
        self.assertIn("advisor_model: advisor-test", rendered)
        self.assertIn("advisor_mode: interactive-review", rendered)
        self.assertIn("advisor_relationship: separate-advisor-model", rendered)

    def test_describe_status_supports_summary_workflow_and_resume(self) -> None:
        cwd = Path(__file__).resolve().parent / f"_tmp_status_view_{uuid4().hex}"
        cwd.mkdir(parents=True, exist_ok=True)
        try:
            session = Session(
                SessionConfig(
                    cwd=cwd,
                    interactive=False,
                    provider="openai-compatible",
                    model="gpt-test",
                )
            )
            session.record_workspace_change(
                tool_name="apply_patch",
                summary="Update runtime flow",
                file_changes=[
                    WorkspaceFileChange(
                        path="runtime/session.py",
                        existed_before=True,
                        before_content="old\n",
                        after_content="new\n",
                        action_kind="update",
                    )
                ],
            )
            session.task_manager.create("agent", "Inspect runtime flow")
            session.record_planning_artifact(
                PlanningArtifact(
                    kind="ultraplan",
                    goal="map runtime",
                    summary="summary",
                    used_read_only_subagents=True,
                )
            )

            summary = session.describe_status()
            workflow = session.describe_status(section="workflow")
            resume = session.describe_status(section="resume")

            self.assertIn("current session:", summary)
            self.assertIn("provider: openai-compatible", summary)
            self.assertIn("working set files:", summary)
            self.assertIn("mix: diff_backed=", summary)
            self.assertIn("focused file source:", summary)
            self.assertIn("next actions:", summary)
            self.assertIn("/files focused", summary)
            self.assertIn("workflow status:", workflow)
            self.assertIn("task surfaces:", workflow)
            self.assertIn("mix: diff_backed=", workflow)
            self.assertIn("Working set", workflow)
            self.assertIn("/diff focused", workflow)
            self.assertIn("resume status:", resume)
            self.assertIn("saved session:", resume)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_child_session_inherits_session_execution_contract(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        policy = session._compile_turn_command_policy(
            allowed_tool_names=("read_file", "bash"),
            allowed_bash_command_prefixes=("git status",),
            require_read_only_subagents=True,
            command_policy_name="read-only-subagent",
            command_policy_source="subagent",
        )
        assert policy is not None
        session.set_session_execution_contract(
            execution_mode="read-only-subagent",
            command_policy=policy,
            active_execution_constraint="read-only",
            constraint_source="session_execution_contract",
            constraint_reason="read-only subagent contract",
        )

        child = session.create_child_session(interactive=False)
        try:
            self.assertEqual(child.state.session_execution_mode, "child-session")
            self.assertEqual(child.state.session_command_policy_name, "read-only-subagent")
            self.assertEqual(child.state.session_command_policy_source, "subagent")
            self.assertEqual(child.state.session_command_policy_allowed_tool_names, ["bash", "read_file"])
            self.assertEqual(child.state.session_command_policy_allowed_bash_prefixes, ["git status"])
            self.assertTrue(child.state.session_command_policy_require_read_only_subagents)
            with child._command_execution_scope(
                allowed_tool_names=None,
                allowed_bash_command_prefixes=None,
                require_read_only_subagents=False,
                command_policy_name=None,
                command_policy_source=None,
            ):
                active_policy = child.active_command_policy()
                assert active_policy is not None
                self.assertEqual(active_policy.name, "read-only-subagent")
                self.assertTrue(active_policy.require_read_only_subagents)
                rejected = child.evaluate_bash_command_policy("git diff")
                self.assertFalse(rejected.allowed)
        finally:
            child.close()
            session.close()

    def test_describe_config_and_tasks_include_planning_lifecycle(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        previous = PlanningArtifact(
            kind="ultraplan",
            goal="previous plan",
            summary="Current Architecture\n- previous map",
            used_read_only_subagents=True,
        )
        session.record_planning_artifact(previous)
        task = session.task_manager.create(
            "ultraplan_scout",
            "Scout architecture",
            planner_kind="ultraplan",
            task_role="scout",
            scout_category="architecture-boundaries",
        )
        execution_task = session.task_manager.create(
            "agent",
            "Implement runtime changes",
            task_role="execution",
            active_plan_id="pending",
            plan_execution_mode="background_agent",
            plan_execution_phase="running",
            plan_status="on-plan",
        )
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="map runtime",
                summary="Current Architecture\n- updated map\n\nImplementation Plan\n- revise runtime",
                supersedes_artifact_id=previous.artifact_id,
                derived_from_drift=True,
                derivation_reason="Need a narrower runtime-only revision.",
                used_read_only_subagents=True,
                scout_categories=["architecture-boundaries"],
                task_ids=[task.id],
                advisor_status="block",
                advisor_risk_flags=["unsafe-write"],
            )
        )
        execution_task.metadata["active_plan_id"] = session.active_planning_artifact().artifact_id
        session.record_plan_drift_context(
            "active_plan_goal: map runtime\n"
            "candidate_work_summary:\n"
            "touch runtime/query_loop.py and session.py\n"
            "pending_tools: apply_patch"
        )
        session.state.advisor_last_result = AdvisorReviewSummary(
            checkpoint="plan_drift",
            status="block",
            reason="Stay in runtime/session scope.",
            risk_flags=["plan-drift"],
            suggested_changes=["Tighten the patch surface."],
            model="advisor-model",
        )
        session.set_turn_read_only_constraints_active(True)

        config = session.describe_config()
        tasks = session.describe_tasks()

        self.assertIn("execution_constraints: read-only", config)
        self.assertIn("active_plan_kind: ultraplan", config)
        self.assertIn("active_plan_goal: map runtime", config)
        self.assertIn("active_plan_advisor_status: block", config)
        self.assertIn("active_plan_execution_tasks: 1", config)
        self.assertIn("active_plan_supersedes:", config)
        self.assertIn("active_plan_derived_from_drift: yes", config)
        self.assertIn("planning lifecycle:", tasks)
        self.assertIn("task_surfaces:", tasks)
        self.assertIn("child execution tasks:", tasks)
        self.assertIn("background execution tasks:", tasks)
        self.assertIn("active_plan_task=yes", tasks)
        self.assertIn("scout=architecture-boundaries", tasks)
        self.assertIn("task_role=execution", tasks)
        self.assertIn("task_surface=background_execution", tasks)
        self.assertIn("mode=background_agent", tasks)
        self.assertIn("phase=running", tasks)
        self.assertIn("plan_status=on-plan", tasks)
        self.assertEqual(session.planning_artifacts()[0].superseded_by_artifact_id, session.planning_artifacts()[1].artifact_id)
        active_detail = session.describe_active_plan()
        scout_detail = session.describe_active_plan_scouts()
        execution_detail = session.describe_active_plan_execution()
        advisor_detail = session.describe_active_plan_advisor()
        lineage_detail = session.describe_active_plan_lineage()
        self.assertIn("lineage_position:", active_detail)
        self.assertIn("derived_from_drift: yes", active_detail)
        self.assertIn("derivation_reason: Need a narrower runtime-only revision.", active_detail)
        self.assertIn("execution_task_count: 1", active_detail)
        self.assertIn("execution_task_ids:", active_detail)
        self.assertIn("comparisons:", active_detail)
        self.assertIn("against_previous:", active_detail)
        self.assertIn("implementation_plan_diff:", active_detail)
        self.assertIn("summary_diff:", active_detail)
        self.assertIn("latest_session_advisor_review:", active_detail)
        self.assertIn("recent_plan_drift_analysis:", active_detail)
        self.assertIn("pending_tools: apply_patch", active_detail)
        self.assertNotIn("scout_outputs:", active_detail)
        self.assertIn("scout_outputs:", scout_detail)
        self.assertIn("selected_scout_detail:", scout_detail)
        self.assertIn("selected_scout_task_id:", scout_detail)
        self.assertIn("selected_scout_task_action: /task show", scout_detail)
        self.assertIn("Scout architecture", scout_detail)
        self.assertIn("task_id:", scout_detail)
        self.assertIn("kind:", scout_detail)
        self.assertIn("metadata:", scout_detail)
        self.assertIn("execution_tasks:", execution_detail)
        self.assertIn("selected_execution_detail:", execution_detail)
        self.assertIn("selected_execution_task_id:", execution_detail)
        self.assertIn("selected_execution_task_action: /task show", execution_detail)
        self.assertIn("Implement runtime changes", execution_detail)
        self.assertIn("mode: background_agent", execution_detail)
        self.assertIn("phase: running", execution_detail)
        self.assertIn("plan_status: on-plan", execution_detail)
        self.assertIn("advisor_review:", advisor_detail)
        self.assertIn("latest_session_advisor_review:", advisor_detail)
        self.assertIn("recent_plan_drift_analysis:", advisor_detail)
        self.assertIn("lineage:", lineage_detail)
        self.assertIn("current", lineage_detail)
        self.assertIn("actions=/plan show", lineage_detail)
        self.assertIn("selected_lineage_artifact_id:", lineage_detail)
        self.assertIn("selected_lineage_default_action:", lineage_detail)
        self.assertIn("next_actions:", lineage_detail)
        self.assertIn("selected: /plan derive", lineage_detail)

    def test_describe_tasks_and_task_detail_render_permission_context(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        task = session.task_manager.create(
            "agent",
            "Inspect runtime safeguards",
            task_role="execution",
        )
        session.task_manager.set_progress(
            task.id,
            '[tool:error] bash (6ms): Bash command is not allowed in command mode "review". '
            "[policy=Matched ask rules: ask:shell:git diff [segment 1: git diff] | "
            "mode=review | segment=segment 2: Set-Content out.txt]",
            permission_display_decision_reason=(
                "Matched ask rules: ask:shell:git diff [segment 1: git diff]"
            ),
            permission_display_permission_rules=["ask:shell:git diff [segment 1: git diff]"],
            permission_display_command_mode_name="review",
            permission_display_command_mode_allowed_prefixes=["git diff", "git show"],
            permission_display_command_mode_violating_segment="Set-Content out.txt",
            permission_display_command_mode_violating_segment_index=2,
            permission_display_command_mode_complex_features=["command_substitution"],
        )
        session.task_manager.fail(task.id, "Permission denied")

        tasks_rendered = session.describe_tasks()
        detail_rendered = session.describe_task_detail(task.id[:6])

        self.assertIn("policy: Matched ask rules: ask:shell:git diff [segment 1: git diff]", tasks_rendered)
        self.assertIn("matched_rules:", tasks_rendered)
        self.assertIn("command_mode:", tasks_rendered)
        self.assertIn("allowed_prefixes: git diff, git show", tasks_rendered)
        self.assertIn("violating_segment: segment 2: Set-Content out.txt", tasks_rendered)
        self.assertIn("complex_features: command_substitution", tasks_rendered)
        self.assertIn("permission_context:", detail_rendered)
        self.assertIn("policy: Matched ask rules: ask:shell:git diff [segment 1: git diff]", detail_rendered)
        self.assertIn("allowed_prefixes: git diff, git show", detail_rendered)
        self.assertIn("complex_features: command_substitution", detail_rendered)

    def test_active_plan_audit_renders_lineage_wide_summary(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        previous_scout = session.task_manager.create(
            "ultraplan_scout",
            "Scout previous runtime",
            planner_kind="ultraplan",
            task_role="scout",
            scout_category="architecture-boundaries",
        )
        session.task_manager.complete(previous_scout.id, "Inspect previous session.py.")
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="previous plan",
                summary="Current Architecture\n- previous runtime",
                task_ids=[previous_scout.id],
                advisor_status="approve",
                advisor_reason="Previous direction was stable.",
                used_read_only_subagents=True,
            )
        )
        previous = session.active_planning_artifact()
        assert previous is not None
        active_scout = session.task_manager.create(
            "ultraplan_scout",
            "Scout runtime changes",
            planner_kind="ultraplan",
            task_role="scout",
            scout_category="tests-regressions",
        )
        session.task_manager.complete(active_scout.id, "Inspect runtime/query_loop.py.")
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="map runtime",
                summary="Current Architecture\n- runtime session\n\nImplementation Plan\n- update query_loop.py",
                supersedes_artifact_id=previous.artifact_id,
                task_ids=[active_scout.id],
                advisor_status="block",
                advisor_reason="Need a narrower runtime-only pass first.",
                advisor_risk_flags=["unsafe-write"],
                used_read_only_subagents=True,
                derived_from_drift=True,
                derivation_reason="Runtime-only revision.",
            )
        )
        active = session.active_planning_artifact()
        assert active is not None
        execution = session.task_manager.create(
            "agent",
            "Implement runtime changes",
            task_role="execution",
            active_plan_id=active.artifact_id,
            active_plan_goal=active.goal,
            plan_execution_mode="interactive_turn",
            plan_execution_phase="running",
            plan_status="drifted",
            drift_status="block",
            constraint_source="plan_drift_block",
        )
        session.task_manager.set_progress(execution.id, "Touch runtime/query_loop.py and session.py")
        session.state.advisor_last_result = AdvisorReviewSummary(
            checkpoint="plan_drift",
            status="block",
            reason="Stay in runtime/session scope.",
            risk_flags=["plan-drift"],
        )
        session.record_plan_drift_context("pending_tools: apply_patch")

        rendered = session.describe_active_plan_audit()
        previous_rendered = session.describe_active_plan_audit(artifact_id="previous")

        self.assertIn("lineage_audit_summary:", rendered)
        self.assertIn("artifacts:", rendered)
        self.assertIn(f"selected_audit_artifact_id: {active.artifact_id}", rendered)
        self.assertIn(f"selected_audit_primary_action: /plan replay latest artifact={active.artifact_id}", rendered)
        self.assertIn(f"/plan timeline all artifact={previous.artifact_id}", rendered)
        self.assertIn("selected_artifact_phase_summaries:", rendered)
        self.assertIn("- Execution Loop:", rendered)
        self.assertIn("selected_artifact_deltas:", rendered)
        self.assertIn("derived_from_drift=yes", rendered)
        self.assertIn(f"selected_audit_artifact_id: {previous.artifact_id}", previous_rendered)
        self.assertIn("selected_audit_goal: previous plan", previous_rendered)

    def test_active_plan_timeline_renders_plan_scout_execution_advisor_and_drift(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        scout = session.task_manager.create(
            "ultraplan_scout",
            "Scout architecture",
            planner_kind="ultraplan",
            task_role="scout",
            scout_category="architecture-boundaries",
        )
        session.task_manager.complete(scout.id, "Inspect session.py.")
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="map runtime",
                summary="Current Architecture\n- session runtime\n\nImplementation Plan\n- update session.py",
                task_ids=[scout.id],
                advisor_status="approve",
                advisor_reason="Solid direction.",
                used_read_only_subagents=True,
            )
        )
        artifact = session.active_planning_artifact()
        assert artifact is not None
        execution = session.task_manager.create(
            "agent",
            "Implement runtime changes",
            task_role="execution",
            active_plan_id=artifact.artifact_id,
            active_plan_goal=artifact.goal,
            plan_execution_mode="interactive_turn",
            plan_execution_phase="running",
            plan_status="drifted",
            constraint_source="plan_drift_block",
        )
        session.task_manager.set_progress(execution.id, "Touch query_loop.py and session.py")
        session.state.advisor_last_result = AdvisorReviewSummary(
            checkpoint="plan_drift",
            status="block",
            reason="Stay in runtime/session scope.",
            risk_flags=["plan-drift"],
            suggested_changes=["Tighten the patch surface."],
            model="advisor-model",
        )
        session.record_plan_drift_context(
            "active_plan_goal: map runtime\n"
            "candidate_work_summary:\n"
            "touch runtime/query_loop.py and session.py\n"
            "pending_tools: apply_patch"
        )

        rendered = session.describe_active_plan_timeline_at(3)

        self.assertIn("audit_summary:", rendered)
        self.assertIn("task_count:", rendered)
        self.assertIn("session_span:", rendered)
        self.assertIn("session_duration:", rendered)
        self.assertIn("last_updated:", rendered)
        self.assertIn("latest_execution_status:", rendered)
        self.assertIn("latest_advisor_status:", rendered)
        self.assertIn("latest_drift_status:", rendered)
        self.assertIn("timeline:", rendered)
        self.assertIn("[plan]", rendered)
        self.assertIn("[scout]", rendered)
        self.assertIn("[execution]", rendered)
        self.assertIn("[advisor]", rendered)
        self.assertIn("[drift]", rendered)
        self.assertIn("pending_tools: apply_patch", rendered)
        self.assertIn("selected_timeline:", rendered)
        self.assertIn("selected_timeline_primary_action:", rendered)
        self.assertIn("selected_timeline_secondary_action:", rendered)
        self.assertIn("[Plan Setup] entries=", rendered)
        self.assertIn("tasks=", rendered)
        self.assertIn("span=", rendered)
        self.assertIn("duration=", rendered)
        self.assertIn("last_updated=", rendered)
        self.assertIn("[Scout Research] entries=", rendered)
        self.assertIn("[Execution Loop] entries=", rendered)
        self.assertIn("[Advisor & Drift] entries=", rendered)
        self.assertIn("next_actions:", rendered)
        self.assertIn("/plan execution", rendered)

    def test_active_plan_timeline_supports_kind_filter(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        scout = session.task_manager.create(
            "ultraplan_scout",
            "Scout architecture",
            planner_kind="ultraplan",
            task_role="scout",
            scout_category="architecture-boundaries",
        )
        session.task_manager.complete(scout.id, "Inspect session.py.")
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="map runtime",
                summary="Current Architecture\n- session runtime",
                task_ids=[scout.id],
                used_read_only_subagents=True,
            )
        )
        artifact = session.active_planning_artifact()
        assert artifact is not None
        execution = session.task_manager.create(
            "agent",
            "Implement runtime changes",
            task_role="execution",
            active_plan_id=artifact.artifact_id,
            active_plan_goal=artifact.goal,
            plan_execution_mode="interactive_turn",
            plan_execution_phase="running",
            plan_status="on-plan",
        )
        session.task_manager.set_progress(execution.id, "Inspect runtime flow")

        rendered = session.describe_active_plan_timeline(kind_filter="execution")

        self.assertIn("timeline_filter: execution", rendered)
        self.assertIn("timeline_delta: none", rendered)
        self.assertIn("timeline_focus: none", rendered)
        self.assertIn("audit_summary:", rendered)
        self.assertIn("session_duration:", rendered)
        self.assertIn("[Execution Loop] entries=", rendered)
        self.assertIn("[execution]", rendered)
        self.assertNotIn("[Scout Research]", rendered)
        self.assertNotIn("[scout]", rendered)

    def test_active_plan_timeline_supports_delta_and_focus_modes(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="previous plan",
                summary="Current Architecture\n- previous runtime",
                used_read_only_subagents=True,
            )
        )
        previous = session.active_planning_artifact()
        assert previous is not None
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="map runtime",
                summary="Current Architecture\n- session runtime",
                supersedes_artifact_id=previous.artifact_id,
                used_read_only_subagents=True,
            )
        )
        artifact = session.active_planning_artifact()
        assert artifact is not None
        scout = session.task_manager.create(
            "ultraplan_scout",
            "Scout architecture",
            planner_kind="ultraplan",
            task_role="scout",
            scout_category="architecture-boundaries",
        )
        session.task_manager.complete(scout.id, "Inspect session.py.")
        execution = session.task_manager.create(
            "agent",
            "Implement runtime changes",
            task_role="execution",
            active_plan_id=artifact.artifact_id,
            active_plan_goal=artifact.goal,
            plan_execution_mode="interactive_turn",
            plan_execution_phase="running",
            plan_status="drifted",
            drift_status="block",
            constraint_source="plan_drift_block",
        )
        session.task_manager.set_progress(execution.id, "Touch query_loop.py and session.py")
        session.state.advisor_last_result = AdvisorReviewSummary(
            checkpoint="plan_drift",
            status="block",
            reason="Stay in runtime/session scope.",
        )
        session.record_plan_drift_context("pending_tools: apply_patch")

        rendered_delta = session.describe_active_plan_timeline(
            delta_mode="after-drift",
        )
        rendered_focus = session.describe_active_plan_timeline(
            focus_mode=f"task:{execution.id}",
        )

        self.assertIn("timeline_delta: after-drift", rendered_delta)
        self.assertIn("[drift]", rendered_delta)
        self.assertIn("timeline_focus: task:" + execution.id, rendered_focus)
        self.assertIn(execution.id, rendered_focus)
        self.assertNotIn(scout.id, rendered_focus)

    def test_active_plan_timeline_supports_phase_filter(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="map runtime",
                summary="Current Architecture\n- session runtime",
                used_read_only_subagents=True,
            )
        )
        artifact = session.active_planning_artifact()
        assert artifact is not None
        execution = session.task_manager.create(
            "agent",
            "Implement runtime changes",
            task_role="execution",
            active_plan_id=artifact.artifact_id,
            active_plan_goal=artifact.goal,
            plan_execution_mode="interactive_turn",
            plan_execution_phase="running",
            plan_status="on-plan",
        )
        session.task_manager.set_progress(execution.id, "Inspect runtime flow")
        session.record_plan_drift_context("pending_tools: apply_patch")

        rendered = session.describe_active_plan_timeline(phase_filter="execution-loop")

        self.assertIn("timeline_phase: execution-loop", rendered)
        self.assertIn("[Execution Loop] entries=", rendered)
        self.assertIn("[execution]", rendered)
        self.assertNotIn("[Plan Setup]", rendered)
        self.assertNotIn("[Advisor & Drift]", rendered)

    def test_active_plan_timeline_supports_compare_lenses(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="previous plan",
                summary="Current Architecture\n- previous runtime",
                used_read_only_subagents=True,
            )
        )
        previous = session.active_planning_artifact()
        assert previous is not None
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="map runtime",
                summary="Current Architecture\n- session runtime",
                supersedes_artifact_id=previous.artifact_id,
                used_read_only_subagents=True,
            )
        )
        artifact = session.active_planning_artifact()
        assert artifact is not None
        scout = session.task_manager.create(
            "ultraplan_scout",
            "Scout architecture",
            planner_kind="ultraplan",
            task_role="scout",
            scout_category="architecture-boundaries",
        )
        session.task_manager.complete(scout.id, "Inspect session.py.")
        execution = session.task_manager.create(
            "agent",
            "Implement runtime changes",
            task_role="execution",
            active_plan_id=artifact.artifact_id,
            active_plan_goal=artifact.goal,
            plan_execution_mode="interactive_turn",
            plan_execution_phase="running",
            plan_status="drifted",
            drift_status="block",
            constraint_source="plan_drift_block",
        )
        session.task_manager.set_progress(execution.id, "Touch query_loop.py and session.py")
        session.state.advisor_last_result = AdvisorReviewSummary(
            checkpoint="plan_drift",
            status="block",
            reason="Stay in runtime/session scope.",
        )
        session.record_plan_drift_context("pending_tools: apply_patch")

        rendered = session.describe_active_plan_timeline(
            compare_mode="execution-vs-scout",
            selected_compare_index=5,
        )
        rendered_previous = session.describe_active_plan_timeline(
            compare_mode="active-vs-previous",
            selected_compare_index=5,
        )

        self.assertIn("timeline_compare: execution-vs-scout", rendered)
        self.assertIn("compare_lens:", rendered)
        self.assertIn("selected_timeline_compare:", rendered)
        self.assertIn("selected_timeline_compare_primary_action:", rendered)
        self.assertIn("selected_timeline_compare_secondary_action:", rendered)
        self.assertIn("- execution:", rendered)
        self.assertIn("- scout:", rendered)
        self.assertIn("phase:Execution Loop", rendered)
        self.assertIn("phase=execution-loop", rendered)
        self.assertIn("timeline_compare: active-vs-previous", rendered_previous)
        self.assertIn("- active:", rendered_previous)
        self.assertIn("- previous:", rendered_previous)
        self.assertIn("> phase:Plan Setup", rendered_previous)
        self.assertIn("selected_timeline_compare_label: phase:Plan Setup", rendered_previous)
        self.assertIn("phase=plan-setup", rendered_previous)

    def test_active_plan_timeline_phase_local_compare_focuses_on_slice(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="previous plan",
                summary="Current Architecture\n- previous runtime",
                used_read_only_subagents=True,
            )
        )
        previous = session.active_planning_artifact()
        assert previous is not None
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="map runtime",
                summary="Current Architecture\n- session runtime",
                supersedes_artifact_id=previous.artifact_id,
                used_read_only_subagents=True,
            )
        )
        artifact = session.active_planning_artifact()
        assert artifact is not None
        execution = session.task_manager.create(
            "agent",
            "Implement runtime changes",
            task_role="execution",
            active_plan_id=artifact.artifact_id,
            active_plan_goal=artifact.goal,
            plan_execution_mode="interactive_turn",
            plan_execution_phase="running",
            plan_status="drifted",
            drift_status="block",
            constraint_source="plan_drift_block",
        )
        session.task_manager.set_progress(execution.id, "Touch query_loop.py and session.py")
        session.state.advisor_last_result = AdvisorReviewSummary(
            checkpoint="plan_drift",
            status="block",
            reason="Stay in runtime/session scope.",
        )
        session.record_plan_drift_context("pending_tools: apply_patch")

        rendered = session.describe_active_plan_timeline(
            phase_filter="execution-loop",
            compare_mode="active-vs-previous",
            selected_compare_index=5,
        )

        self.assertIn("timeline_phase: execution-loop", rendered)
        self.assertIn("timeline_compare: active-vs-previous", rendered)
        self.assertIn("local:entries", rendered)
        self.assertIn("local:execution", rendered)
        self.assertIn("selected_timeline_compare_label: local:entries", rendered)
        self.assertIn("selected_timeline_compare_primary_action: /plan timeline all phase=execution-loop", rendered)
        self.assertNotIn("phase:Plan Setup", rendered)

    def test_active_plan_timeline_phase_local_delta_mode_surfaces_drift_slices(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="map runtime",
                summary="Current Architecture\n- session runtime",
                used_read_only_subagents=True,
            )
        )
        artifact = session.active_planning_artifact()
        assert artifact is not None
        execution = session.task_manager.create(
            "agent",
            "Implement runtime changes",
            task_role="execution",
            active_plan_id=artifact.artifact_id,
            active_plan_goal=artifact.goal,
            plan_execution_mode="interactive_turn",
            plan_execution_phase="running",
            plan_status="drifted",
            drift_status="block",
            constraint_source="plan_drift_block",
        )
        session.task_manager.set_progress(execution.id, "Touch query_loop.py and session.py")
        session.state.advisor_last_result = AdvisorReviewSummary(
            checkpoint="plan_drift",
            status="block",
            reason="Stay in runtime/session scope.",
        )
        session.record_plan_drift_context("pending_tools: apply_patch")

        rendered = session.describe_active_plan_timeline(
            phase_filter="execution-loop",
            compare_mode="after-drift-vs-all",
            selected_compare_index=10,
        )

        self.assertIn("local:before-drift", rendered)
        self.assertIn("local:after-drift", rendered)
        self.assertIn("local:execution-change", rendered)
        self.assertIn("selected_timeline_compare_label: local:before-drift", rendered)
        self.assertIn("selected_timeline_compare_primary_action: /plan timeline all phase=execution-loop delta=before-drift", rendered)
        self.assertIn("selected_timeline_compare_secondary_action: /plan timeline all phase=execution-loop delta=after-drift", rendered)

    def test_active_plan_timeline_phase_local_audit_summary_surfaces_drift_windows(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="map runtime",
                summary="Current Architecture\n- session runtime",
                used_read_only_subagents=True,
            )
        )
        artifact = session.active_planning_artifact()
        assert artifact is not None
        execution = session.task_manager.create(
            "agent",
            "Implement runtime changes",
            task_role="execution",
            active_plan_id=artifact.artifact_id,
            active_plan_goal=artifact.goal,
            plan_execution_mode="interactive_turn",
            plan_execution_phase="running",
            plan_status="drifted",
            drift_status="block",
            constraint_source="plan_drift_block",
        )
        session.task_manager.set_progress(execution.id, "Touch query_loop.py and session.py")
        execution_two = session.task_manager.create(
            "agent",
            "Apply runtime patch",
            task_role="execution",
            active_plan_id=artifact.artifact_id,
            active_plan_goal=artifact.goal,
            plan_execution_mode="interactive_turn",
            plan_execution_phase="revising",
            plan_status="drifted",
            drift_status="block",
            constraint_source="before_write_block",
        )
        session.task_manager.set_progress(execution_two.id, "Patch query_loop.py")
        session.record_plan_drift_context("pending_tools: apply_patch")

        rendered = session.describe_active_plan_timeline(
            phase_filter="execution-loop",
        )
        rendered_second = session.describe_active_plan_timeline(
            phase_filter="execution-loop",
            selected_phase_local_task_index=1,
        )

        self.assertIn("phase_local_audit_summary:", rendered)
        self.assertIn("- phase: execution-loop", rendered)
        self.assertIn("- before_drift:", rendered)
        self.assertIn("- after_drift:", rendered)
        self.assertIn("- change_summary:", rendered)
        self.assertIn("- execution_task_ids: ", rendered)
        self.assertIn(execution.id, rendered)
        self.assertIn("- execution_task_actions: ", rendered)
        self.assertIn(f"{execution.id}=/task show {execution.id}", rendered)
        self.assertIn("- selected_phase_local_task_id: " + execution.id, rendered)
        self.assertIn("- selected_phase_local_task_position: 1/2", rendered)
        self.assertIn("- selected_phase_local_task_action: /task show " + execution.id, rendered)
        self.assertIn("- recent_drift_linked_task: " + execution.id, rendered)
        self.assertIn("- recent_drift_linked_task_action: /task drift " + execution.id, rendered)
        self.assertIn("- selected_phase_local_task_id: " + execution_two.id, rendered_second)
        self.assertIn("- selected_phase_local_task_position: 2/2", rendered_second)

    def test_active_plan_replay_latest_renders_execution_linked_blocks(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="map runtime",
                summary="Current Architecture\n- session runtime",
                used_read_only_subagents=True,
                advisor_status="block",
                advisor_reason="Need narrower runtime-only scope.",
                advisor_risk_flags=["broad-write-scope"],
            )
        )
        artifact = session.active_planning_artifact()
        assert artifact is not None
        session.state.advisor_last_result = AdvisorReviewSummary(
            checkpoint="plan_drift",
            status="block",
            reason="Need a safer read-only pass first.",
            risk_flags=["unsafe-write"],
            suggested_changes=["Stay inside runtime files."],
        )
        task = session.task_manager.create(
            "agent",
            "Implement runtime changes",
            task_role="execution",
            active_plan_id=artifact.artifact_id,
            active_plan_goal=artifact.goal,
            plan_execution_mode="interactive_turn",
            plan_execution_phase="running",
            plan_status="drifted",
            drift_status="block",
            drift_reason="Need a narrower runtime-only pass.",
            constraint_source="plan_drift_block",
        )
        session.task_manager.complete(task.id, "Inspect query_loop.py and session.py.")
        session.record_plan_drift_context("pending_tools: apply_patch")

        rendered = session.describe_active_plan_replay(latest=True)

        self.assertIn("replay_artifact: " + artifact.artifact_id, rendered)
        self.assertIn("replay_source: timeline-entry", rendered)
        self.assertIn("replay_cursor:", rendered)
        self.assertIn("selected_replay_entry:", rendered)
        self.assertIn("- kind: execution", rendered)
        self.assertIn("linked_blocks:", rendered)
        self.assertIn("- execution_context:", rendered)
        self.assertIn("constraint_source: plan_drift_block", rendered)
        self.assertIn("drift_status: block", rendered)
        self.assertIn("selected_replay_primary_action: /task show " + task.id, rendered)
        self.assertIn("selected_replay_secondary_action: /task drift " + task.id, rendered)

    def test_active_plan_replay_compare_item_uses_compare_source(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="previous plan",
                summary="Current Architecture\n- previous runtime",
                used_read_only_subagents=True,
            )
        )
        previous = session.active_planning_artifact()
        assert previous is not None
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="current plan",
                summary="Current Architecture\n- current runtime",
                used_read_only_subagents=True,
                supersedes_artifact_id=previous.artifact_id,
            )
        )
        artifact = session.active_planning_artifact()
        assert artifact is not None
        scout = session.task_manager.create(
            "ultraplan_scout",
            "Scout architecture",
            planner_kind="ultraplan",
            task_role="scout",
            active_plan_id=artifact.artifact_id,
            scout_category="architecture-boundaries",
        )
        session.task_manager.complete(scout.id, "Inspect session.py.")

        rendered = session.describe_active_plan_replay(
            compare_mode="execution-vs-scout",
            selected_compare_index=1,
        )

        self.assertIn("replay_source: compare-item", rendered)
        self.assertIn("replay_source_context: compare:scout", rendered)
        self.assertIn("- kind: scout", rendered)
        self.assertIn("selected_replay_primary_action: /task show " + scout.id, rendered)

    def test_active_plan_replay_active_vs_previous_selected_previous_uses_previous_artifact(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="previous plan",
                summary="Current Architecture\n- previous runtime",
                used_read_only_subagents=True,
            )
        )
        previous = session.active_planning_artifact()
        assert previous is not None
        previous_scout = session.task_manager.create(
            "ultraplan_scout",
            "Scout previous runtime",
            planner_kind="ultraplan",
            task_role="scout",
            active_plan_id=previous.artifact_id,
            scout_category="architecture-boundaries",
        )
        session.task_manager.complete(previous_scout.id, "Inspect previous session.py.")
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="current plan",
                summary="Current Architecture\n- current runtime",
                supersedes_artifact_id=previous.artifact_id,
                used_read_only_subagents=True,
            )
        )
        artifact = session.active_planning_artifact()
        assert artifact is not None
        current_execution = session.task_manager.create(
            "agent",
            "Implement current runtime changes",
            task_role="execution",
            active_plan_id=artifact.artifact_id,
            active_plan_goal=artifact.goal,
            plan_execution_mode="interactive_turn",
            plan_execution_phase="running",
            plan_status="on-plan",
        )
        session.task_manager.complete(current_execution.id, "Inspect current query_loop.py.")

        rendered = session.describe_active_plan_replay(
            compare_mode="active-vs-previous",
            selected_compare_index=1,
        )

        self.assertIn("replay_source: compare-item", rendered)
        self.assertIn("replay_source_context: compare:previous", rendered)
        self.assertIn("replay_artifact: " + previous.artifact_id, rendered)
        self.assertIn("replay_goal: previous plan", rendered)
        self.assertIn("- kind: scout", rendered)
        self.assertIn("- task_id: " + previous_scout.id, rendered)
        self.assertIn("selected_replay_primary_action: /task show " + previous_scout.id, rendered)
        self.assertIn("lineage_replay_compare:", rendered)
        self.assertIn(f"- current_artifact: {artifact.artifact_id} goal=current plan", rendered)
        self.assertIn(f"- previous_artifact: {previous.artifact_id} goal=previous plan", rendered)
        self.assertIn("- selected_side: previous", rendered)
        self.assertIn(f"- compare_previous_replay: /plan replay latest all artifact={previous.artifact_id}", rendered)
        self.assertIn(f"- added_execution_tasks: {current_execution.id}", rendered)
        self.assertIn(f"- removed_scout_tasks: {previous_scout.id}", rendered)
        self.assertIn("- entry_delta_scout:", rendered)
        self.assertIn("removed:", rendered)
        self.assertIn(previous_scout.id, rendered)
        self.assertIn(f"actions=/task show {previous_scout.id} | /plan scouts", rendered)
        self.assertIn("- entry_delta_execution:", rendered)
        self.assertIn(current_execution.id, rendered)
        self.assertIn(f"actions=/task show {current_execution.id} | /task advisor {current_execution.id}", rendered)
        self.assertIn("- phase_entry_delta:Scout Research: added=0 removed=1", rendered)
        self.assertIn("- phase_entry_delta:Execution Loop: added=1 removed=0", rendered)

    def test_active_plan_replay_supports_previous_artifact_slice(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="previous plan",
                summary="Current Architecture\n- previous runtime",
                used_read_only_subagents=True,
            )
        )
        previous = session.active_planning_artifact()
        assert previous is not None
        previous_scout = session.task_manager.create(
            "ultraplan_scout",
            "Scout previous runtime",
            planner_kind="ultraplan",
            task_role="scout",
            active_plan_id=previous.artifact_id,
            scout_category="architecture-boundaries",
        )
        session.task_manager.complete(previous_scout.id, "Inspect previous session.py.")
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="current plan",
                summary="Current Architecture\n- current runtime",
                used_read_only_subagents=True,
                supersedes_artifact_id=previous.artifact_id,
            )
        )

        rendered = session.describe_active_plan_replay(
            latest=True,
            artifact_id="previous",
        )

        self.assertIn("artifact_id: " + previous.artifact_id, rendered)
        self.assertIn("goal: previous plan", rendered)
        self.assertIn("replay_artifact: " + previous.artifact_id, rendered)
        self.assertIn("replay_source: timeline-entry", rendered)
        self.assertIn("- kind: scout", rendered)
        self.assertIn("selected_replay_primary_action: /task show " + previous_scout.id, rendered)

    def test_active_plan_replay_phase_local_summary_preserves_selected_task_slice(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="map runtime",
                summary="Current Architecture\n- session runtime",
                used_read_only_subagents=True,
            )
        )
        artifact = session.active_planning_artifact()
        assert artifact is not None
        first = session.task_manager.create(
            "agent",
            "Implement runtime changes",
            task_role="execution",
            active_plan_id=artifact.artifact_id,
            active_plan_goal=artifact.goal,
            plan_execution_mode="interactive_turn",
            plan_execution_phase="running",
            plan_status="on-plan",
        )
        session.task_manager.complete(first.id, "Inspect query_loop.py.")
        second = session.task_manager.create(
            "agent",
            "Implement advisor changes",
            task_role="execution",
            active_plan_id=artifact.artifact_id,
            active_plan_goal=artifact.goal,
            plan_execution_mode="interactive_turn",
            plan_execution_phase="revising",
            plan_status="drifted",
            drift_status="block",
            drift_reason="Stay in runtime/session scope.",
        )
        session.task_manager.complete(second.id, "Inspect session.py.")

        rendered = session.describe_active_plan_replay(
            phase_filter="execution-loop",
            source_mode="phase-local-summary",
            selected_phase_local_task_index=1,
        )

        self.assertIn("replay_phase: execution-loop", rendered)
        self.assertIn("replay_source: phase-local-summary", rendered)
        self.assertIn("replay_source_context: phase-local-summary", rendered)
        self.assertIn("- task_id: " + second.id, rendered)
        self.assertIn("selected_replay_primary_action: /task show " + second.id, rendered)

    def test_timeline_and_replay_actions_preserve_focused_file_for_execution_tasks(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_timeline_focus_preserve"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.record_workspace_change(
                tool_name="apply_patch",
                summary="Update two files",
                file_changes=[
                    WorkspaceFileChange(
                        path="a.py",
                        existed_before=True,
                        before_content="old_a\n",
                        after_content="new_a\n",
                        action_kind="update",
                    ),
                    WorkspaceFileChange(
                        path="b.py",
                        existed_before=True,
                        before_content="old_b\n",
                        after_content="new_b\n",
                        action_kind="update",
                    ),
                ],
            )
            session.record_planning_artifact(
                PlanningArtifact(
                    kind="ultraplan",
                    goal="map runtime",
                    summary="summary",
                    used_read_only_subagents=True,
                )
            )
            artifact = session.active_planning_artifact()
            assert artifact is not None
            execution = session.task_manager.create(
                "agent",
                "Implement runtime changes",
                task_role="execution",
                active_plan_id=artifact.artifact_id,
                active_plan_goal=artifact.goal,
                plan_execution_mode="interactive_turn",
                plan_execution_phase="tool_loop",
                plan_status="on-plan",
                workspace_planned_paths=["a.py", "b.py"],
            )
            artifact.task_ids.append(execution.id)
            session.task_manager.complete(execution.id, "Inspect b.py.")

            session.remember_selected_change_context_focus(index=0, file_index=1, redo=False)
            rendered_timeline = session.describe_active_plan_timeline(
                kind_filter="execution",
                phase_filter="execution-loop",
            )
            rendered_replay = session.describe_active_plan_replay(
                kind_filter="execution",
                phase_filter="execution-loop",
                latest=True,
            )

            self.assertIn(f"selected_timeline_primary_action: /task show {execution.id} file 2", rendered_timeline)
            self.assertIn(f"selected_phase_local_task_action: /task show {execution.id} file 2", rendered_timeline)
            self.assertIn(f"selected_replay_primary_action: /task show {execution.id} file 2", rendered_replay)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_lineage_comparison_surfaces_plan_change_points(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        first = PlanningArtifact(
            kind="ultraplan",
            goal="first plan",
            summary=(
                "Current Architecture\n"
                "- session runtime\n\n"
                "Implementation Plan\n"
                "- update session.py\n\n"
                "Risks / Open Questions\n"
                "- permission coupling\n\n"
                "Verification Checklist\n"
                "- run session tests"
            ),
            used_read_only_subagents=True,
            advisor_status="block",
            advisor_risk_flags=["unsafe-write"],
        )
        session.record_planning_artifact(first)
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="second plan",
                summary=(
                    "Current Architecture\n"
                    "- session runtime\n"
                    "- remote attach\n\n"
                    "Implementation Plan\n"
                    "- update session.py\n"
                    "- update query_loop.py\n\n"
                    "Risks / Open Questions\n"
                    "- approval bridge\n\n"
                    "Verification Checklist\n"
                    "- run session tests\n"
                    "- run stdio tests"
                ),
                supersedes_artifact_id=first.artifact_id,
                used_read_only_subagents=True,
                derived_from_drift=True,
                derivation_reason="Need a narrower review loop.",
                advisor_status="approve",
            )
        )

        rendered = session.describe_active_plan_lineage()

        self.assertIn("comparisons:", rendered)
        self.assertIn("goal_changed:", rendered)
        self.assertIn("advisor_status_changed:", rendered)
        self.assertIn("derived_from_drift_changed:", rendered)
        self.assertIn("derivation_reason_changed:", rendered)
        self.assertIn("implementation_plan_diff:", rendered)
        self.assertIn("verification_checklist_diff:", rendered)
        self.assertIn("risks_open_questions_diff:", rendered)
        self.assertIn("reactivate_newer_revision:", rendered)
        self.assertIn("inspect_selected:", rendered)
        self.assertIn("inspect_active_plan:", rendered)
        self.assertIn("selected_lineage_default_action:", rendered)

    def test_active_plan_scout_detail_surfaces_failure_context(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        failed_task = session.task_manager.create(
            "ultraplan_scout",
            "Scout risks",
            planner_kind="ultraplan",
            task_role="scout",
            scout_category="risks-unknowns",
            workspace_mode="worktree",
            child_cwd="/tmp/worktree-1",
        )
        session.task_manager.set_progress(failed_task.id, "Queued scout")
        session.task_manager.fail(failed_task.id, "Traceback\nPermission denied")
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="map runtime",
                summary="summary",
                used_read_only_subagents=True,
                task_ids=[failed_task.id],
            )
        )

        rendered = session.describe_active_plan_scouts()

        self.assertIn("selected_scout_detail:", rendered)
        self.assertIn("status: failed", rendered)
        self.assertIn("progress_summary: Queued scout", rendered)
        self.assertIn("metadata: planner_kind=ultraplan", rendered)
        self.assertIn("workspace_mode=worktree", rendered)
        self.assertIn("child_cwd=/tmp/worktree-1", rendered)
        self.assertIn("error:", rendered)
        self.assertIn("Permission denied", rendered)

    def test_active_plan_scout_detail_supports_full_output_mode(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        task = session.task_manager.create(
            "ultraplan_scout",
            "Scout architecture",
            planner_kind="ultraplan",
            task_role="scout",
            scout_category="architecture-boundaries",
        )
        long_output = "\n".join(f"line-{index}" for index in range(80))
        session.task_manager.complete(task.id, long_output)
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="map runtime",
                summary="summary",
                used_read_only_subagents=True,
                task_ids=[task.id],
            )
        )

        compact = session.describe_active_plan_scouts_at(0, full_detail=False)
        full = session.describe_active_plan_scouts_at(0, full_detail=True)

        self.assertIn("detail_mode: compact", compact)
        self.assertIn("detail_mode: full", full)
        self.assertIn("line-0", compact)
        self.assertNotIn("line-79", compact)
        self.assertIn("line-79", full)

    def test_active_plan_execution_detail_surfaces_failure_context(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="map runtime",
                summary="summary",
                used_read_only_subagents=True,
            )
        )
        artifact = session.active_planning_artifact()
        assert artifact is not None
        failed_task = session.task_manager.create(
            "agent",
            "Implement runtime changes",
            task_role="execution",
            active_plan_id=artifact.artifact_id,
            active_plan_goal=artifact.goal,
            plan_execution_mode="interactive_turn",
            plan_execution_phase="tool_loop",
            plan_status="drifted",
            drift_status="block",
            drift_reason="Need a narrower runtime-only pass.",
            constraint_source="plan_drift_block",
            workspace_mode="worktree",
            child_cwd="/tmp/worktree-2",
        )
        session.task_manager.set_progress(failed_task.id, "Waiting for a safer read-only pass")
        session.task_manager.fail(failed_task.id, "Traceback\nPermission denied")
        session.record_plan_drift_context(
            "active_plan_goal: map runtime\n"
            "candidate_work_summary:\n"
            "touch query_loop.py and session.py\n"
            "pending_tools: write_file"
        )
        session.state.constraint_reason = "Need a safer read-only pass first."
        session.state.advisor_last_result = AdvisorReviewSummary(
            checkpoint="plan_drift",
            status="block",
            reason="Stay in runtime/session scope.",
            risk_flags=["unsafe-write"],
            suggested_changes=["Limit the patch to session/query_loop."],
            model="advisor-model",
        )

        rendered = session.describe_active_plan_execution()

        self.assertIn("selected_execution_detail:", rendered)
        self.assertIn("status: failed", rendered)
        self.assertIn("mode: interactive_turn", rendered)
        self.assertIn("phase: tool_loop", rendered)
        self.assertIn("plan_status: drifted", rendered)
        self.assertIn("progress_summary: Waiting for a safer read-only pass", rendered)
        self.assertIn("metadata: task_role=execution", rendered)
        self.assertIn("workspace_mode=worktree", rendered)
        self.assertIn("child_cwd=/tmp/worktree-2", rendered)
        self.assertIn("error:", rendered)
        self.assertIn("Permission denied", rendered)
        self.assertIn("selected_execution_context:", rendered)
        self.assertIn("linked_constraint_source: plan_drift_block", rendered)
        self.assertIn("linked_drift_status: block", rendered)
        self.assertIn("linked_drift_reason: Need a narrower runtime-only pass.", rendered)
        self.assertIn("linked_constraint_reason: Need a safer read-only pass first.", rendered)
        self.assertIn("linked_advisor_review:", rendered)
        self.assertIn("plan_drift/block", rendered)
        self.assertIn("linked_plan_drift_analysis:", rendered)
        self.assertIn("pending_tools: write_file", rendered)

    def test_active_plan_execution_detail_supports_full_output_mode(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="map runtime",
                summary="summary",
                used_read_only_subagents=True,
            )
        )
        artifact = session.active_planning_artifact()
        assert artifact is not None
        task = session.task_manager.create(
            "agent",
            "Implement runtime changes",
            task_role="execution",
            active_plan_id=artifact.artifact_id,
            active_plan_goal=artifact.goal,
            plan_execution_mode="interactive_turn",
            plan_execution_phase="completed",
            plan_status="on-plan",
        )
        long_output = "\n".join(f"line-{index}" for index in range(80))
        session.task_manager.complete(task.id, long_output)

        compact = session.describe_active_plan_execution_at(0, full_detail=False)
        full = session.describe_active_plan_execution_at(0, full_detail=True)

        self.assertIn("detail_mode: compact", compact)
        self.assertIn("detail_mode: full", full)
        self.assertIn("line-0", compact)
        self.assertNotIn("line-79", compact)
        self.assertIn("line-79", full)

    def test_describe_active_plan_execution_includes_selected_execution_file_context(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_plan_execution_file_context"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.record_workspace_change(
                tool_name="apply_patch",
                summary="Update runtime flow",
                file_changes=[
                    WorkspaceFileChange(
                        path="runtime/session.py",
                        existed_before=True,
                        before_content="old\n",
                        after_content="new\n",
                        action_kind="update",
                    )
                ],
            )
            session.record_planning_artifact(
                PlanningArtifact(
                    kind="ultraplan",
                    goal="map runtime",
                    summary="summary",
                    used_read_only_subagents=True,
                )
            )
            artifact = session.active_planning_artifact()
            assert artifact is not None
            task = session.task_manager.create(
                "agent",
                "Implement runtime changes",
                task_role="execution",
                active_plan_id=artifact.artifact_id,
                active_plan_goal=artifact.goal,
                workspace_planned_paths=["runtime/session.py"],
            )

            rendered = session.describe_active_plan_execution()

            self.assertIn("selected_execution_summary:", rendered)
            self.assertIn("- next_actions:", rendered)
            self.assertIn("- go_to_task: /task show " + task.id, rendered)
            self.assertIn("- go_to_change: /changes show", rendered)
            self.assertIn("- stay_on_surface: /plan execution | /plan advisor | /advisor status", rendered)
            self.assertIn("selected execution focused file:", rendered)
            self.assertIn("- focused file: runtime/session.py", rendered)
            self.assertIn("- related change:", rendered)
            self.assertIn("- next_actions:", rendered)
            self.assertIn("go_to_change: /changes show", rendered)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_describe_active_plan_scouts_includes_selected_scout_file_context(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_plan_scout_file_context"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.record_workspace_change(
                tool_name="apply_patch",
                summary="Update scout note",
                file_changes=[
                    WorkspaceFileChange(
                        path="notes.md",
                        existed_before=True,
                        before_content="old\n",
                        after_content="new\n",
                        action_kind="update",
                    )
                ],
            )
            session.record_planning_artifact(
                PlanningArtifact(
                    kind="ultraplan",
                    goal="map runtime",
                    summary="summary",
                    used_read_only_subagents=True,
                )
            )
            artifact = session.active_planning_artifact()
            assert artifact is not None
            scout = session.task_manager.create(
                "ultraplan_scout",
                "Scout runtime surface",
                task_role="scout",
                active_plan_id=artifact.artifact_id,
                active_plan_goal=artifact.goal,
            )
            artifact.task_ids.append(scout.id)

            rendered = session.describe_active_plan_scouts()

            self.assertIn("selected_scout_summary:", rendered)
            self.assertIn("- next_actions:", rendered)
            self.assertIn("- go_to_task: /task show " + scout.id, rendered)
            self.assertIn("- stay_on_surface: /plan scouts", rendered)
            self.assertIn("selected scout focused file:", rendered)
            self.assertIn("- focused file: notes.md", rendered)
            self.assertIn("- related change:", rendered)
            self.assertIn("- next_actions:", rendered)
            self.assertIn("go_to_change: /changes show", rendered)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_describe_active_plan_child_views_support_file_focus_selection(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_plan_child_file_focus"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.record_workspace_change(
                tool_name="apply_patch",
                summary="Update runtime flow",
                file_changes=[
                    WorkspaceFileChange(
                        path="runtime/session.py",
                        existed_before=True,
                        before_content="old\n",
                        after_content="new\n",
                        action_kind="update",
                    )
                ],
            )
            session.record_planning_artifact(
                PlanningArtifact(
                    kind="ultraplan",
                    goal="map runtime",
                    summary="summary",
                    used_read_only_subagents=True,
                )
            )
            artifact = session.active_planning_artifact()
            assert artifact is not None
            scout = session.task_manager.create(
                "ultraplan_scout",
                "Scout runtime surface",
                task_role="scout",
                active_plan_id=artifact.artifact_id,
                active_plan_goal=artifact.goal,
                workspace_planned_paths=["runtime/session.py", "notes.md"],
            )
            execution = session.task_manager.create(
                "agent",
                "Implement runtime changes",
                task_role="execution",
                active_plan_id=artifact.artifact_id,
                active_plan_goal=artifact.goal,
                workspace_planned_paths=["runtime/session.py", "notes.md"],
            )
            artifact.task_ids.extend([scout.id, execution.id])

            rendered_scout = session.describe_active_plan_scouts_at(0, file_index=1)
            rendered_execution = session.describe_active_plan_execution_at(0, file_index=1)

            self.assertIn("selected scout focused file:", rendered_scout)
            self.assertIn("- focused file: notes.md", rendered_scout)
            self.assertIn("- context-only: yes", rendered_scout)
            self.assertIn("selected execution focused file:", rendered_execution)
            self.assertIn("- focused file: notes.md", rendered_execution)
            self.assertIn("- context-only: yes", rendered_execution)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_describe_task_detail_renders_task_metadata(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="map runtime",
                summary="Current Architecture\n- session runtime",
                used_read_only_subagents=True,
                advisor_status="block",
                advisor_reason="Need a safer runtime-only pass first.",
                advisor_risk_flags=["unsafe-write"],
                derived_from_drift=True,
                derivation_reason="Need a narrower runtime-only revision.",
            )
        )
        artifact = session.active_planning_artifact()
        assert artifact is not None
        task = session.task_manager.create(
            "agent",
            "Implement runtime changes",
            task_role="execution",
            active_plan_id=artifact.artifact_id,
            active_plan_goal=artifact.goal,
            plan_execution_phase="running",
            plan_execution_mode="interactive_turn",
            plan_status="drifted",
            drift_status="block",
            drift_reason="Need a narrower runtime-only pass.",
            constraint_source="plan_drift_block",
        )
        session.task_manager.set_progress(task.id, "Inspect runtime flow")
        session.task_manager.complete(task.id, "session.py\nquery_loop.py")
        session.state.constraint_reason = "Need a safer read-only pass first."
        session.state.last_plan_drift_status = "block"
        session.state.last_plan_drift_reason = "Stay in runtime/session scope."
        session.state.last_plan_drift_context = (
            "active_plan_goal: map runtime\n"
            "candidate_work_summary:\n"
            "touch runtime/query_loop.py and session.py\n"
            "pending_tools: apply_patch"
        )
        session.state.advisor_last_result = AdvisorReviewSummary(
            checkpoint="plan_drift",
            status="block",
            reason="Stay in runtime/session scope.",
            risk_flags=["plan-drift"],
            suggested_changes=["Tighten the patch surface."],
            model="advisor-model",
        )

        rendered = session.describe_task_detail(task.id[:6])

        self.assertIn(f"task_id: {task.id}", rendered)
        self.assertIn("kind: agent", rendered)
        self.assertIn("status: completed", rendered)
        self.assertIn("progress_summary: Inspect runtime flow", rendered)
        self.assertIn("metadata:", rendered)
        self.assertIn("- task_role: execution", rendered)
        self.assertIn("- plan_execution_phase: running", rendered)
        self.assertIn("execution_context:", rendered)
        self.assertIn("- active_plan:", rendered)
        self.assertIn(f"artifact_id: {artifact.artifact_id}", rendered)
        self.assertIn("goal: map runtime", rendered)
        self.assertIn("advisor_status: block", rendered)
        self.assertIn("risk_flags: unsafe-write", rendered)
        self.assertIn("derived_from_drift: yes", rendered)
        self.assertIn("- advisor_context:", rendered)
        self.assertIn("active_plan_review:", rendered)
        self.assertIn("latest_session_review:", rendered)
        self.assertIn("plan_drift/block", rendered)
        self.assertIn("- drift_context:", rendered)
        self.assertIn(f"- active_plan_advisor_action: /task advisor {task.id}", rendered)
        self.assertIn(f"- drift_detail_action: /task drift {task.id}", rendered)
        self.assertIn("constraint_source: plan_drift_block", rendered)
        self.assertIn("constraint_reason: Need a safer read-only pass first.", rendered)
        self.assertIn("drift_status: block", rendered)
        self.assertIn("drift_reason: Need a narrower runtime-only pass.", rendered)
        self.assertIn("last_plan_drift_status: block", rendered)
        self.assertIn("analysis:", rendered)
        self.assertIn("pending_tools: apply_patch", rendered)
        self.assertIn("output:", rendered)
        self.assertIn("session.py", rendered)

        drift_detail = session.describe_task_drift_detail(task.id)
        advisor_detail = session.open_task_detail_advisor(task.id)
        self.assertIn("drift_detail:", drift_detail)
        self.assertIn("advisor_context:", drift_detail)
        self.assertIn("next_actions:", drift_detail)
        self.assertIn("go_to_change: none", drift_detail)
        self.assertIn("go_to_plan: /plan execution 1 | /plan execution | /plan advisor", drift_detail)
        self.assertIn("stay_on_surface: /task show " + task.id, drift_detail)
        self.assertIn("/task drift " + task.id, drift_detail)
        self.assertIn("navigation:", drift_detail)
        self.assertIn(f"/task advisor {task.id}", drift_detail)
        self.assertIn("/plan execution 1", drift_detail)
        self.assertIn("advisor_review:", advisor_detail)
        self.assertIn("next_actions:", advisor_detail)
        self.assertIn("go_to_change: none", advisor_detail)
        self.assertIn("go_to_plan: /plan execution 1 | /plan execution | /plan advisor", advisor_detail)
        self.assertIn("stay_on_surface: /task show " + task.id, advisor_detail)
        self.assertIn("/task advisor " + task.id, advisor_detail)
        self.assertIn("navigation:", advisor_detail)
        self.assertIn(f"/task drift {task.id}", advisor_detail)
        self.assertIn("/plan advisor", advisor_detail)

    def test_active_plan_execution_detail_includes_comparison_summary(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="map runtime",
                summary="summary",
                used_read_only_subagents=True,
            )
        )
        artifact = session.active_planning_artifact()
        assert artifact is not None
        task_a = session.task_manager.create(
            "agent",
            "Implement runtime changes",
            task_role="execution",
            active_plan_id=artifact.artifact_id,
            active_plan_goal=artifact.goal,
            plan_execution_mode="interactive_turn",
            plan_execution_phase="tool_loop",
            plan_status="on-plan",
        )
        task_b = session.task_manager.create(
            "agent",
            "Revise runtime patch",
            task_role="execution",
            active_plan_id=artifact.artifact_id,
            active_plan_goal=artifact.goal,
            plan_execution_mode="interactive_turn",
            plan_execution_phase="revising",
            plan_status="drifted",
        )
        session.task_manager.complete(task_a.id, "session.py\nruntime/context.py")
        session.task_manager.complete(task_b.id, "query_loop.py\npermissions.py")

        rendered = session.describe_active_plan_execution_at(0)

        self.assertIn("selected_execution_comparisons:", rendered)
        self.assertIn("against 2/2", rendered)
        self.assertIn("tool_loop -> revising", rendered)
        self.assertIn("phase_changed:", rendered)
        self.assertIn("plan_status_changed:", rendered)
        self.assertIn("detail_diff:", rendered)

    def test_active_plan_scout_detail_includes_comparison_summary(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        task_a = session.task_manager.create(
            "ultraplan_scout",
            "Scout architecture",
            planner_kind="ultraplan",
            task_role="scout",
            scout_category="architecture-boundaries",
        )
        task_b = session.task_manager.create(
            "ultraplan_scout",
            "Scout risks",
            planner_kind="ultraplan",
            task_role="scout",
            scout_category="risks-unknowns",
        )
        session.task_manager.complete(task_a.id, "session.py\nruntime/context.py")
        session.task_manager.complete(task_b.id, "permission bridge\nworkspace cleanup")
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="map runtime",
                summary="summary",
                used_read_only_subagents=True,
                task_ids=[task_a.id, task_b.id],
            )
        )

        rendered = session.describe_active_plan_scouts_at(0)

        self.assertIn("selected_scout_comparisons:", rendered)
        self.assertIn("against 2/2", rendered)
        self.assertIn("architecture-boundaries -> risks-unknowns", rendered)
        self.assertIn("detail_diff:", rendered)

    def test_describe_config_includes_mcp_health_counts(self) -> None:
        registry = McpRegistry()
        healthy_client = McpClient(
            config=McpServerConfig(name="docs", transport="stdio", command="demo"),
            transport=FakeTransport(),
        )
        broken_client = McpClient(
            config=McpServerConfig(name="broken", transport="stdio", command="demo"),
            transport=BrokenTransport(),
        )
        registry.register_client(healthy_client)
        registry.initialize_server("docs")
        registry.refresh_tools("docs")
        registry.register_client(broken_client)
        registry.connect_server("broken")
        session = Session(
            SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False),
            mcp_registry=registry,
        )

        rendered = session.describe_config()

        self.assertIn("mcp_servers: 2", rendered)
        self.assertIn("mcp_connected_servers: 1", rendered)
        self.assertIn("mcp_failed_servers: 1", rendered)
        self.assertIn("mcp_retrying_servers: 0", rendered)

    def test_clear_history_resets_messages_and_context_summary(self) -> None:
        state = SessionState(
            context_summary="Earlier conversation summary",
            messages=[
                {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            ],
        )
        session = Session(
            SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False),
            state=state,
        )

        session.clear_history()

        self.assertEqual(session.state.messages, [])
        self.assertIsNone(session.state.context_summary)

    def test_describe_and_apply_manual_history_compaction(self) -> None:
        state = SessionState(
            context_summary="Earlier summary",
            messages=[
                {"role": "user", "content": [{"type": "text", "text": "one"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "two"}]},
                {"role": "user", "content": [{"type": "text", "text": "three"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "four"}]},
            ],
        )
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
                max_history_messages=4,
                history_keep_last_messages=2,
            ),
            state=state,
        )

        status = session.describe_compact()
        preview = session.describe_compact(section="preview", instructions="keep only decisions")
        applied = session.compact_history_into_context_summary("keep only decisions")

        self.assertIn("history compaction status:", status)
        self.assertIn("compaction mode: local estimated summary", status)
        self.assertIn("active message history: yes", status)
        self.assertIn("compacted context summary: yes", status)
        self.assertIn("would compact: yes", status)
        self.assertIn("messages to compact: 2", status)
        self.assertIn("history compaction preview:", preview)
        self.assertIn("compact instruction: keep only decisions", preview)
        self.assertIn("compacted summary preview: Earlier summary", preview)
        self.assertIn("compacted message preview:", preview)
        self.assertIn("- 1. user: one", preview)
        self.assertIn("history compacted:", applied)
        self.assertIn("compact instruction: keep only decisions", applied)
        self.assertIn("compacted context summary: yes", applied)
        self.assertEqual(len(session.state.messages), 2)
        self.assertIsNotNone(session.state.context_summary)
        assert session.state.context_summary is not None
        self.assertIn("Earlier summary", session.state.context_summary)
        self.assertIn("Compact instruction: keep only decisions", session.state.context_summary)
        self.assertIn("Earlier conversation summary:", session.state.context_summary)
        self.assertIn("- 1. user: one", session.state.context_summary)

    def test_clear_session_reset_rotates_session_id_and_preserves_old_transcript(self) -> None:
        cwd = Path(__file__).resolve().parent / f"_tmp_session_clear_reset_{uuid4().hex}"
        cwd.mkdir(parents=True, exist_ok=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False), state=SessionState())
        try:
            session.state.context_summary = "Earlier conversation summary"
            session.state.messages = [
                {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            ]
            session.state.enabled_skill_names = ["repo-skill"]
            session.state.session_permission_rules = [{"decision": "allow", "scope": "tool", "value": "bash"}]
            session.state.active_planning_artifact_id = "plan-123"
            session.state.planning_artifact_history = [
                PlanningArtifact(kind="plan", goal="Ship feature", summary="Do the work", artifact_id="plan-123")
            ]
            session.state.recent_change_sets = [
                WorkspaceChangeSet(
                    tool_name="apply_patch",
                    summary="Update file",
                    files=[
                        WorkspaceFileChange(
                            path="demo.py",
                            existed_before=True,
                            before_content="old\n",
                            after_content="new\n",
                        )
                    ],
                )
            ]
            old_session_id = session.state.session_id
            session.persist_state()

            result = session.clear_session_reset()

            new_session_id = session.state.session_id
            self.assertNotEqual(new_session_id, old_session_id)
            self.assertEqual(result["old_session_id"], old_session_id)
            self.assertEqual(result["session_id"], new_session_id)
            self.assertEqual(session.state.messages, [])
            self.assertIsNone(session.state.context_summary)
            self.assertEqual(session.state.recent_change_sets, [])
            self.assertIsNone(session.state.active_planning_artifact_id)
            self.assertEqual(session.state.planning_artifact_history, [])
            self.assertEqual(session.state.enabled_skill_names, ["repo-skill"])
            self.assertEqual(session.state.session_permission_rules[0]["value"], "bash")

            old_state, _old_path = load_transcript_by_session_id(cwd, old_session_id)
            new_state, _new_path = load_transcript_by_session_id(cwd, new_session_id)
            assert old_state is not None
            assert new_state is not None
            self.assertEqual(old_state.context_summary, "Earlier conversation summary")
            self.assertEqual(len(old_state.messages), 1)
            self.assertIsNone(new_state.context_summary)
            self.assertEqual(new_state.messages, [])
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_describe_mcp_views(self) -> None:
        registry = McpRegistry()
        client = McpClient(
            config=McpServerConfig(name="docs", transport="stdio", command="demo"),
            transport=FakeTransport(),
        )
        registry.register_client(client)
        registry.initialize_server("docs")
        registry.refresh_tools("docs")
        session = Session(
            SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False),
            mcp_registry=registry,
        )

        servers = session.describe_mcp_servers()
        tools = session.describe_mcp_tools()

        self.assertIn("config_path:", servers)
        self.assertIn("summary: connected=1 failed=0 retrying=0", servers)
        self.assertIn("docs: transport=stdio", servers)
        self.assertIn("status=connected", servers)
        self.assertIn("resources=1", servers)
        self.assertIn("connected_at=", servers)
        self.assertIn("docs.echo_text", tools)

    def test_describe_mcp_views_include_auth_mode(self) -> None:
        registry = McpRegistry()
        client = McpClient(
            config=McpServerConfig(
                name="remote",
                transport="http",
                url="http://example.test/mcp",
                headers={"Authorization": "Bearer demo"},
                auth_mode="bearer",
            ),
            transport=FakeTransport(),
        )
        registry.register_client(client)
        registry.initialize_server("remote")
        registry.refresh_tools("remote")
        session = Session(
            SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False),
            mcp_registry=registry,
        )

        servers = session.describe_mcp_servers()

        self.assertIn("remote: transport=http", servers)
        self.assertIn("auth=bearer", servers)

    def test_describe_mcp_views_include_failed_server_state(self) -> None:
        registry = McpRegistry()
        client = McpClient(
            config=McpServerConfig(name="broken", transport="stdio", command="demo"),
            transport=BrokenTransport(),
        )
        registry.register_client(client)
        registry.connect_server("broken")
        session = Session(
            SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False),
            mcp_registry=registry,
        )

        servers = session.describe_mcp_servers()

        self.assertIn("status=failed", servers)
        self.assertIn("connection failed", servers)
        self.assertIn("failed_at=", servers)
        self.assertIn("failures=1", servers)

    def test_describe_mcp_tool_diagnostic_reports_success(self) -> None:
        registry = McpRegistry()
        client = McpClient(
            config=McpServerConfig(name="docs", transport="stdio", command="demo"),
            transport=FakeTransport(),
        )
        registry.register_client(client)
        registry.initialize_server("docs")
        registry.refresh_tools("docs")
        session = Session(
            SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False),
            mcp_registry=registry,
        )

        rendered = session.describe_mcp_tool_diagnostic("docs", "echo_text", arguments={"text": "hi"})

        self.assertIn("server: docs", rendered)
        self.assertIn("tool: echo_text", rendered)
        self.assertIn("ok: yes", rendered)
        self.assertIn("source: ok", rendered)
        self.assertIn("result:", rendered)
        self.assertIn("echo:hi", rendered)
        self.assertIn("next_steps:", rendered)
        self.assertIn("/mcp-verify docs echo_text", rendered)

    def test_describe_mcp_tool_diagnostic_reports_config_guidance(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))

        rendered = session.describe_mcp_tool_diagnostic("missing", "echo_text", arguments={"text": "hi"})

        self.assertIn("source: config", rendered)
        self.assertIn("next_steps:", rendered)
        self.assertIn(".pyclaude/mcp_servers.json", rendered)
        self.assertIn("/mcp and /mcp-tools", rendered)

    def test_describe_mcp_tool_diagnostic_reports_transport_guidance(self) -> None:
        registry = McpRegistry()
        client = McpClient(
            config=McpServerConfig(name="broken", transport="stdio", command="demo"),
            transport=BrokenTransport(),
        )
        registry.register_client(client)
        registry.connect_server("broken")
        session = Session(
            SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False),
            mcp_registry=registry,
        )

        rendered = session.describe_mcp_tool_diagnostic("broken", "echo_text", arguments={"text": "hi"})

        self.assertIn("source: transport", rendered)
        self.assertIn("next_steps:", rendered)
        self.assertIn("/mcp or /mcp-reconnect broken", rendered)

    def test_describe_mcp_verification_reports_model_guidance(self) -> None:
        registry = McpRegistry()
        client = McpClient(
            config=McpServerConfig(name="docs", transport="stdio", command="demo"),
            transport=FakeTransport(),
        )
        registry.register_client(client)
        registry.initialize_server("docs")
        registry.refresh_tools("docs")
        session = Session(
            SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False),
            mcp_registry=registry,
        )
        session.provider = SimpleNamespace(capabilities=SimpleNamespace(supports_tool_calling=False))

        rendered = session.describe_mcp_verification("docs", "echo_text", arguments={"text": "hi"})

        self.assertIn("source: model", rendered)
        self.assertIn("next_steps:", rendered)
        self.assertIn("preflight:", rendered)
        self.assertIn("  source: ok", rendered)
        self.assertIn("  transport: stdio", rendered)
        self.assertIn("/mcp-call docs echo_text", rendered)
        self.assertIn("tool-calling model", rendered)

    def test_describe_project_context_views(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_context"
        if cwd.exists():
            shutil.rmtree(cwd)
        (cwd / ".pyclaude" / "skills").mkdir(parents=True)
        (cwd / "CLAUDE.md").write_text("Project memory text", encoding="utf-8")
        (cwd / ".pyclaude" / "skills" / "review.md").write_text(
            "---\n"
            "description: Review code changes carefully\n"
            "auto_enable: true\n"
            "tags: review,quality\n"
            "---\n\n"
            "Always review changes carefully.",
            encoding="utf-8",
        )
        (cwd / ".pyclaude" / "skills" / "draft.md").write_text(
            "Draft user-facing text carefully.",
            encoding="utf-8",
        )

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            memory = session.describe_project_memory()
            skills = session.describe_loaded_skills()
            project_context = session.describe_project_context()
            config = session.describe_config()
            prompt = session.build_system_prompt()

            self.assertIn("project memory:", memory)
            self.assertIn("Project memory text", memory)
            self.assertIn("next_actions:", memory)
            self.assertIn("loaded skills:", skills)
            self.assertIn("active auto-enabled skills:", skills)
            self.assertIn("review", skills)
            self.assertIn("status=enabled,auto", skills)
            self.assertIn("draft", skills)
            self.assertIn("status=inactive", skills)
            self.assertIn("tags=review,quality", skills)
            self.assertIn("project context:", project_context)
            self.assertIn("loaded skills: 7", project_context)
            self.assertIn("project_memory: loaded", config)
            self.assertIn("project_skills: 7", config)
            self.assertIn("enabled_skills: 1", config)
            self.assertIn("Auto-enabled project skills", prompt)
            self.assertNotIn("Manually enabled project skills", prompt)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_skill_precedence_and_reload_preserve_manual_choices(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_skill_state"
        if cwd.exists():
            shutil.rmtree(cwd)
        (cwd / ".pyclaude" / "skills").mkdir(parents=True)
        (cwd / ".pyclaude" / "skills" / "review.md").write_text(
            "---\n"
            "auto_enable: true\n"
            "---\n\n"
            "Auto review guidance.",
            encoding="utf-8",
        )
        (cwd / ".pyclaude" / "skills" / "draft.md").write_text(
            "Draft guidance.",
            encoding="utf-8",
        )

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            self.assertIn("status=enabled,auto", session.describe_loaded_skills())

            session.disable_skill("review")
            session.enable_skill("draft")

            prompt = session.build_system_prompt()
            config = session.describe_config()
            skills = session.describe_loaded_skills()

            self.assertIn("status=disabled", skills)
            self.assertIn("status=enabled,manual", skills)
            self.assertNotIn("Auto review guidance.", prompt)
            self.assertIn("Manually enabled project skills", prompt)
            self.assertIn("Draft guidance.", prompt)
            self.assertIn("manual_enabled_skills: 1", config)
            self.assertIn("manual_disabled_skills: 1", config)

            session.reload_project_context()
            skills_after_reload = session.describe_loaded_skills()
            self.assertIn("status=disabled", skills_after_reload)
            self.assertIn("status=enabled,manual", skills_after_reload)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_project_context_reload_status_tracks_latest_reload(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_project_context_reload"
        if cwd.exists():
            shutil.rmtree(cwd)
        (cwd / ".pyclaude" / "skills").mkdir(parents=True)
        (cwd / ".pyclaude" / "skills" / "review.md").write_text(
            "Original review guidance.",
            encoding="utf-8",
        )

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            self.assertIn(
                "No project-context reload has run in this live session.",
                session.describe_project_context(section="reload-status"),
            )

            (cwd / ".pyclaude" / "skills" / "review.md").write_text(
                "Updated review guidance.",
                encoding="utf-8",
            )
            message = session.reload_project_context()
            reload_status = session.describe_project_context(section="reload-status")

            self.assertIn("Reloaded project context.", message)
            self.assertIn("skill set changed: no", reload_status)
            self.assertIn("errors: none", reload_status)
            self.assertIn("next_actions:", reload_status)
            self.assertIn("latest reload:", session.describe_project_context())
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_describe_saved_sessions_lists_recent_transcripts(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_saved_sessions"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        missing_cwd = cwd / ".pyclaude" / "worktrees" / "missing-agent"

        try:
            config = SessionConfig(cwd=cwd, interactive=False)
            save_transcript(
                config,
                SessionState(
                    session_id="session-demo",
                    session_execution_mode="read-only-subagent",
                    session_command_policy_name="read-only-subagent",
                    session_command_policy_source="subagent",
                    session_command_policy_allowed_tool_names=["bash", "read_file"],
                    session_command_policy_allowed_bash_prefixes=["git status"],
                    session_command_policy_require_read_only_subagents=True,
                    original_cwd=str(cwd.resolve()),
                    effective_cwd=str(missing_cwd.resolve()),
                    workspace_mode="worktree",
                    workspace_label="missing-agent",
                    workspace_cleanup_status="failed",
                    workspace_unavailable=True,
                    workspace_unavailable_reason="Isolated workspace is unavailable: expected missing worktree.",
                    workspace_fallback_cwd=str(cwd.resolve()),
                    messages=[{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
                ),
            )
            session = Session(config)
            rendered = session.describe_saved_sessions()
            self.assertIn("session-demo", rendered)
            self.assertIn("messages=1", rendered)
            self.assertIn("workspace=worktree", rendered)
            self.assertIn("label=missing-agent", rendered)
            self.assertIn(f"origin={cwd.resolve()}", rendered)
            self.assertIn(f"cwd={missing_cwd.resolve()}", rendered)
            self.assertIn("cleanup=failed", rendered)
            self.assertIn("cwd_exists=no", rendered)
            self.assertIn("unavailable=yes", rendered)
            self.assertIn(f"fallback={cwd.resolve()}", rendered)
            self.assertIn("execution=read-only-subagent", rendered)
            self.assertIn("policy=read-only-subagent", rendered)
            self.assertIn("read_only_subagents=yes", rendered)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_describe_saved_sessions_supports_detail_and_workspace_views(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_saved_sessions_detail"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        missing_cwd = cwd / ".pyclaude" / "workspaces" / "missing-agent"

        try:
            config = SessionConfig(cwd=cwd, interactive=False)
            save_transcript(
                config,
                SessionState(
                    session_id="saved-session-demo",
                    original_cwd=str(cwd.resolve()),
                    effective_cwd=str(missing_cwd.resolve()),
                    workspace_mode="snapshot",
                    workspace_label="missing-agent",
                    workspace_cleanup_status="failed",
                    workspace_unavailable=True,
                    workspace_unavailable_reason="expected missing snapshot",
                    workspace_fallback_cwd=str(cwd.resolve()),
                    advisor_mode="interactive-review",
                    advisor_review_history=[AdvisorReviewSummary(checkpoint="final", status="approve", reason="ok")],
                    messages=[{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
                ),
            )
            session = Session(config)
            detail = session.describe_saved_sessions(selector="saved-session-demo", section="detail")
            summary = session.describe_saved_sessions(selector="latest", section="summary")
            workspace = session.describe_saved_sessions(selector="saved-session-demo", section="workspace")

            self.assertIn("saved session:", detail)
            self.assertIn("resume path: pyclaude --resume-session saved-session-demo repl", detail)
            self.assertIn("advisor activity: 1 review(s)", detail)
            self.assertIn("saved session:", summary)
            self.assertIn("next actions:", summary)
            self.assertIn("saved session workspace:", workspace)
            self.assertIn("workspace health:", workspace)
            self.assertIn("primary action:", workspace)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_restore_session_with_missing_isolated_workspace_uses_fallback_and_blocks_writes(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_restore_missing_workspace"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        missing_cwd = cwd / ".pyclaude" / "worktrees" / "missing-agent"

        try:
            config = SessionConfig(cwd=cwd, interactive=False)
            save_transcript(
                config,
                SessionState(
                    session_id="resume-missing-workspace",
                    original_cwd=str(cwd.resolve()),
                    effective_cwd=str(missing_cwd.resolve()),
                    workspace_mode="worktree",
                    workspace_label="missing-agent",
                    workspace_cleanup_status="failed",
                    messages=[{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
                ),
            )
            factory = SessionFactory(load_mcp_from_config=False)
            session, restored_from = factory.create_or_restore_session(
                config,
                resume_session_id="resume-missing-workspace",
            )
            self.assertIsNotNone(restored_from)
            self.assertTrue(session.state.workspace_unavailable)
            self.assertEqual(session.state.workspace_fallback_cwd, str(cwd.resolve()))
            self.assertEqual(session.runtime_cwd(), cwd.resolve())
            self.assertEqual(session.config.cwd, cwd.resolve())
            self.assertEqual(session.state.effective_cwd, str(missing_cwd.resolve()))
            session.validate_tool_call_policy("read_file", {"path": "."})
            with self.assertRaises(PermissionDeniedError):
                session.validate_tool_call_policy("write_file", {"path": "demo.txt", "content": "hello"})
        finally:
            if cwd.exists():
                shutil.rmtree(cwd, ignore_errors=True)

    def test_create_child_session_can_use_isolated_workspace(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_isolated_child"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.txt").write_text("hello", encoding="utf-8")
        session = None
        child = None

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            child = session.create_child_session(interactive=False, isolated_workspace=True)
            self.assertNotEqual(child.config.cwd, session.config.cwd)
            self.assertTrue((child.config.cwd / "demo.txt").exists())
            self.assertIn(".pyclaude", str(child.config.cwd))
            self.assertIn(child.state.workspace_mode, {"snapshot", "worktree"})
            self.assertIsNotNone(child.state.workspace_label)
            self.assertIsNotNone(child.state.workspace_created_at)
            self.assertEqual(child.state.workspace_cleanup_status, "pending")
            self.assertEqual(child.state.original_cwd, str(cwd.resolve()))
            self.assertEqual(child.state.effective_cwd, str(child.config.cwd.resolve()))
            child.close()
            self.assertEqual(child.state.workspace_cleanup_status, "completed")
            child = None
        finally:
            if session is not None:
                session.close()
            if child is not None:
                child.close()
            if cwd.exists():
                shutil.rmtree(cwd, ignore_errors=True)

    def test_create_child_session_prefers_git_worktree_when_repo_is_clean(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        cwd = Path(__file__).resolve().parent / f"_tmp_session_worktree_child_{uuid4().hex}"
        cwd.mkdir(parents=True)
        (cwd / "demo.txt").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "init"], cwd=str(cwd), check=True, capture_output=True, text=True)
        subprocess.run(["git", "add", "demo.txt"], cwd=str(cwd), check=True, capture_output=True, text=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=PyClaude",
                "-c",
                "user.email=pyclaude@example.com",
                "commit",
                "-m",
                "init",
            ],
            cwd=str(cwd),
            check=True,
            capture_output=True,
            text=True,
        )
        session = None
        child = None

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            child = session.create_child_session(interactive=False, isolated_workspace=True)
            self.assertEqual(child.state.workspace_mode, "worktree")
            self.assertTrue((child.config.cwd / "demo.txt").exists())
        finally:
            if session is not None:
                session.close()
            if child is not None:
                child.close()
            if cwd.exists():
                shutil.rmtree(cwd, ignore_errors=True)

    def test_create_child_session_preserves_injected_mcp_tools(self) -> None:
        registry = McpRegistry()
        client = McpClient(
            config=McpServerConfig(name="docs", transport="stdio", command="demo"),
            transport=FakeTransport(),
        )
        registry.register_client(client)
        registry.initialize_server("docs")
        registry.refresh_tools("docs")
        session = Session(
            SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False),
            mcp_registry=registry,
        )
        child = None

        try:
            child = session.create_child_session(interactive=False)
            self.assertIn("docs.echo_text", child.describe_mcp_tools())
            child.close()
            child = None
            self.assertIn("docs.echo_text", session.describe_mcp_tools())
        finally:
            if child is not None:
                child.close()
            session.close()

    def test_describe_config_and_tasks_include_workspace_context(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_workspace_views"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            state = SessionState(
                original_cwd=str(cwd.resolve()),
                effective_cwd=str((cwd / ".pyclaude" / "workspaces" / "agent-demo").resolve()),
                workspace_mode="snapshot",
                workspace_label="agent-demo",
                workspace_created_at="2026-04-01T00:00:00+00:00",
                workspace_cleanup_status="pending",
            )
            session = Session(SessionConfig(cwd=cwd, interactive=False), state=state)
            config = session.describe_config()
            self.assertIn("workspace_mode: snapshot", config)
            self.assertIn("workspace_label: agent-demo", config)
            self.assertIn("workspace_created_at: 2026-04-01T00:00:00+00:00", config)
            self.assertIn("workspace_cleanup_status: pending", config)

            task = session.task_manager.create(
                "agent",
                "Inspect isolated workspace",
                task_role="execution",
                workspace_mode="snapshot",
                workspace_label="agent-demo",
                workspace_created_at="2026-04-01T00:00:00+00:00",
                original_cwd=str(cwd.resolve()),
                effective_cwd=str((cwd / ".pyclaude" / "workspaces" / "agent-demo").resolve()),
                workspace_cleanup_status="failed",
                workspace_cleanup_error="PermissionError: cleanup blocked",
            )
            session.task_manager.fail(task.id, "PermissionError: cleanup blocked")

            rendered_tasks = session.describe_tasks()
            self.assertIn("workspace=snapshot", rendered_tasks)
            self.assertIn("label=agent-demo", rendered_tasks)
            self.assertIn(f"origin={cwd.resolve()}", rendered_tasks)
            self.assertIn("cleanup=failed", rendered_tasks)

            rendered_detail = session.describe_task_detail(task.id)
            self.assertIn("workspace_context:", rendered_detail)
            self.assertIn("- workspace_mode: snapshot", rendered_detail)
            self.assertIn("- workspace_label: agent-demo", rendered_detail)
            self.assertIn(f"- original_cwd: {cwd.resolve()}", rendered_detail)
            self.assertIn("- cleanup_status: failed", rendered_detail)
            self.assertIn("- cleanup_error: PermissionError: cleanup blocked", rendered_detail)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd, ignore_errors=True)

    def test_describe_config_and_tasks_include_orphaned_workspace_diagnostics(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_orphaned_workspaces"
        if cwd.exists():
            shutil.rmtree(cwd)
        current_dir = cwd / ".pyclaude" / "worktrees" / "current-agent"
        transcript_dir = cwd / ".pyclaude" / "workspaces" / "saved-agent"
        background_dir = cwd / ".pyclaude" / "worktrees" / "bg-agent"
        orphan_dir = cwd / ".pyclaude" / "workspaces" / "orphan-agent"
        for path in (current_dir, transcript_dir, background_dir, orphan_dir):
            path.mkdir(parents=True, exist_ok=True)

        try:
            save_transcript(
                SessionConfig(cwd=cwd, interactive=False),
                SessionState(
                    session_id="saved-agent-session",
                    original_cwd=str(cwd.resolve()),
                    effective_cwd=str(transcript_dir.resolve()),
                    workspace_mode="snapshot",
                    workspace_label="saved-agent",
                    workspace_cleanup_status="pending",
                    messages=[{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
                ),
            )
            background = create_background_session(
                cwd,
                prompt="background scan",
                provider="openai-compatible",
                model="gpt-test",
                status="running",
            )
            update_background_session(
                cwd,
                background.bg_id,
                original_cwd=str(cwd.resolve()),
                effective_cwd=str(background_dir.resolve()),
                workspace_mode="worktree",
                workspace_label="bg-agent",
                workspace_cleanup_status="pending",
                status="running",
            )
            session = Session(
                SessionConfig(cwd=cwd, interactive=False),
                state=SessionState(
                    original_cwd=str(cwd.resolve()),
                    effective_cwd=str(current_dir.resolve()),
                    workspace_mode="worktree",
                    workspace_label="current-agent",
                    workspace_cleanup_status="pending",
                ),
            )

            rendered_config = session.describe_config()
            rendered_tasks = session.describe_tasks()

            self.assertIn("orphaned_isolated_workspaces: 1", rendered_config)
            self.assertIn("orphaned_isolated_workspaces: 1", rendered_tasks)
            self.assertIn("workspace=snapshot health=orphaned label=orphan-agent", rendered_config)
            self.assertIn(f"origin={cwd.resolve()}", rendered_config)
            self.assertIn("cleanup=none", rendered_config)
            self.assertIn(f"cwd={orphan_dir.resolve()}", rendered_config)
            self.assertIn("workspace=snapshot health=orphaned label=orphan-agent", rendered_tasks)
            self.assertNotIn(f"cwd={current_dir.resolve()}", rendered_tasks)
            self.assertNotIn(f"cwd={transcript_dir.resolve()}", rendered_tasks)
            self.assertNotIn(f"cwd={background_dir.resolve()}", rendered_tasks)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd, ignore_errors=True)

    def test_preview_orphaned_workspace_cleanup_is_dry_run(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_orphaned_cleanup_preview"
        if cwd.exists():
            shutil.rmtree(cwd)
        orphan_dir = cwd / ".pyclaude" / "worktrees" / "orphan-agent"
        orphan_dir.mkdir(parents=True, exist_ok=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            rendered = session.preview_orphaned_workspace_cleanup()
            self.assertIn("dry_run: yes", rendered)
            self.assertIn("planned_deletions: 1", rendered)
            self.assertIn("cleanup planned | Would delete 1 orphaned isolated workspace(s).", rendered)
            self.assertIn("workspace=worktree health=orphaned label=orphan-agent", rendered)
            self.assertIn("deleted: 0", rendered)
            self.assertTrue(orphan_dir.exists())
        finally:
            if cwd.exists():
                shutil.rmtree(cwd, ignore_errors=True)

    def test_workspace_cleanup_apply_records_audit_only_workspace_change(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_workspace_cleanup_audit"
        if cwd.exists():
            shutil.rmtree(cwd)
        orphan_dir = cwd / ".pyclaude" / "workspaces" / "orphan-agent"
        orphan_dir.mkdir(parents=True, exist_ok=True)

        try:
            session = Session(
                SessionConfig(cwd=cwd, interactive=False),
                permission_manager=PermissionManager(
                    interactive=True,
                    approval_handler=lambda _request: ApprovalResult(decision="allow", scope="once"),
                ),
            )
            rendered = session.apply_orphaned_workspace_cleanup("orphan-agent")
            changes = session.describe_recent_changes()
            detail = session.selected_change_detail()
            undo = session.undo_last_change()

            self.assertIn("deleted: 1", rendered)
            self.assertIn("tool=workspace_cleanup", changes)
            self.assertIn("kind=workspace_audit", changes)
            self.assertIn("undoable=no", changes)
            self.assertIn("kind: workspace_audit", detail)
            self.assertIn("undoable: no", detail)
            self.assertIn("Deleted 1 orphaned isolated workspace(s).", detail)
            self.assertIn("audit-only", undo)
            self.assertFalse(orphan_dir.exists())
        finally:
            if cwd.exists():
                shutil.rmtree(cwd, ignore_errors=True)

    def test_repair_unavailable_snapshot_workspace_updates_state_and_task_audit(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_workspace_repair_snapshot"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.txt").write_text("hello\n", encoding="utf-8")
        missing_cwd = cwd / ".pyclaude" / "workspaces" / "missing-agent"

        try:
            session = Session(
                SessionConfig(cwd=cwd, interactive=False),
                state=SessionState(
                    session_id="repair-snapshot",
                    original_cwd=str(cwd.resolve()),
                    effective_cwd=str(missing_cwd.resolve()),
                    workspace_mode="snapshot",
                    workspace_label="missing-agent",
                    workspace_cleanup_status="pending",
                    workspace_unavailable=True,
                    workspace_unavailable_reason="Isolated workspace is unavailable: expected missing snapshot.",
                    workspace_fallback_cwd=str(cwd.resolve()),
                ),
            )

            rendered = session.repair_isolated_workspaces("missing-agent")
            tasks = session.describe_tasks()
            history = session.describe_history()

            self.assertIn("planned_repairs: 1", rendered)
            self.assertIn("repaired: 1", rendered)
            self.assertIn("repair planned | Planned 1 isolated workspace repair(s).", rendered)
            self.assertIn("repair applied | Repaired 1 isolated workspace(s).", rendered)
            self.assertTrue(Path(session.state.effective_cwd or "").exists())
            self.assertEqual(session.state.workspace_mode, "snapshot")
            self.assertEqual(session.state.workspace_health, "cleanup_pending")
            self.assertFalse(session.state.workspace_unavailable)
            self.assertIn("workspace_action=repair", tasks)
            self.assertIn("health_after=cleanup_pending", tasks)
            self.assertIn("repair applied | Repaired 1 isolated workspace(s).", history)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd, ignore_errors=True)

    def test_repair_unavailable_workspace_reports_origin_unavailable(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_workspace_repair_missing_origin"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        missing_origin = cwd / "missing-origin"
        missing_cwd = cwd / ".pyclaude" / "workspaces" / "missing-agent"

        try:
            session = Session(
                SessionConfig(cwd=cwd, interactive=False),
                state=SessionState(
                    session_id="repair-missing-origin",
                    original_cwd=str(missing_origin.resolve()),
                    effective_cwd=str(missing_cwd.resolve()),
                    workspace_mode="snapshot",
                    workspace_label="missing-agent",
                    workspace_cleanup_status="pending",
                    workspace_unavailable=True,
                    workspace_unavailable_reason="Isolated workspace is unavailable: expected missing snapshot.",
                    workspace_fallback_cwd=str(cwd.resolve()),
                ),
            )

            rendered = session.repair_isolated_workspaces("missing-agent")
            tasks = session.describe_tasks()

            self.assertIn("planned_repairs: 1", rendered)
            self.assertIn("repaired: 0", rendered)
            self.assertIn("repair failed | Failed to repair 1 isolated workspace(s).", rendered)
            self.assertIn("repair failed: origin unavailable", rendered)
            self.assertIn("repair failed", tasks)
            self.assertIn("health_after=unavailable", tasks)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd, ignore_errors=True)

    def test_repair_unavailable_worktree_prefers_worktree_when_repo_is_clean(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        cwd = Path(__file__).resolve().parent / f"_tmp_session_workspace_repair_worktree_{uuid4().hex}"
        cwd.mkdir(parents=True)
        (cwd / "demo.txt").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "init"], cwd=str(cwd), check=True, capture_output=True, text=True)
        subprocess.run(["git", "add", "demo.txt"], cwd=str(cwd), check=True, capture_output=True, text=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=PyClaude",
                "-c",
                "user.email=pyclaude@example.com",
                "commit",
                "-m",
                "init",
            ],
            cwd=str(cwd),
            check=True,
            capture_output=True,
            text=True,
        )
        missing_cwd = cwd / ".pyclaude" / "worktrees" / "missing-agent"

        try:
            session = Session(
                SessionConfig(cwd=cwd, interactive=False),
                state=SessionState(
                    session_id="repair-worktree",
                    original_cwd=str(cwd.resolve()),
                    effective_cwd=str(missing_cwd.resolve()),
                    workspace_mode="worktree",
                    workspace_label="missing-agent",
                    workspace_cleanup_status="pending",
                    workspace_unavailable=True,
                    workspace_unavailable_reason="Isolated workspace is unavailable: expected missing worktree.",
                    workspace_fallback_cwd=str(cwd.resolve()),
                ),
            )

            rendered = session.repair_isolated_workspaces("missing-agent")

            self.assertIn("repaired: 1", rendered)
            self.assertEqual(session.state.workspace_mode, "worktree")
            self.assertTrue(Path(session.state.effective_cwd or "").exists())
            self.assertIn(".pyclaude\\worktrees", session.state.effective_cwd or "")
        finally:
            if cwd.exists():
                shutil.rmtree(cwd, ignore_errors=True)

    def test_describe_config_includes_selected_workspace_actions_for_unavailable_workspace(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_workspace_config_actions"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(
                SessionConfig(cwd=cwd, interactive=False),
                state=SessionState(
                    session_id="workspace-session",
                    original_cwd=str(cwd.resolve()),
                    effective_cwd=str((cwd / ".pyclaude" / "workspaces" / "missing-agent").resolve()),
                    workspace_mode="snapshot",
                    workspace_label="missing-agent",
                    workspace_cleanup_status="pending",
                    workspace_unavailable=True,
                    workspace_unavailable_reason="Isolated workspace is unavailable: expected missing snapshot.",
                    workspace_fallback_cwd=str(cwd.resolve()),
                ),
            )

            rendered = session.describe_config()

            self.assertIn("selected_workspace_primary_action: workspace_repair workspace-session", rendered)
            self.assertIn("selected_workspace_secondary_action: workspace_cleanup_preview", rendered)
            self.assertIn("selected_workspace_tertiary_action: /workspaces list", rendered)
            self.assertIn("selected_workspace_target: workspace-session", rendered)
            bundle = session.current_workspace_action_bundle()
            self.assertEqual(bundle["primary_action"], "workspace_repair workspace-session")
            self.assertEqual(bundle["secondary_action"], "workspace_cleanup_preview")
            self.assertEqual(bundle["tertiary_action"], "/workspaces list")
            self.assertEqual(bundle["target"], "workspace-session")
            self.assertEqual(bundle["workspace_health"], "unavailable")
        finally:
            if cwd.exists():
                shutil.rmtree(cwd, ignore_errors=True)

    def test_describe_task_detail_includes_selected_workspace_actions(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_workspace_task_actions"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            task = session.task_manager.create(
                "workspace",
                "Clean orphaned isolated workspaces",
                workspace_action="cleanup",
                workspace_target="orphan-agent",
                workspace_health_before="orphaned",
                workspace_health_after="healthy",
                workspace_planned_paths=["C:/tmp/orphan-agent"],
                workspace_applied_paths=["C:/tmp/orphan-agent"],
            )
            rendered = session.describe_task_detail(task.id)

            self.assertIn("workspace_actions:", rendered)
            self.assertIn("selected_workspace_primary_action: workspace_cleanup_preview", rendered)
            self.assertIn("selected_workspace_secondary_action: workspace_cleanup_apply orphan-agent", rendered)
            self.assertIn("selected_workspace_tertiary_action: /workspaces list", rendered)
            self.assertIn("selected_workspace_target: orphan-agent", rendered)
            self.assertIn("workspace_task_detail:", rendered)
            self.assertIn("workspace_health_before: orphaned", rendered)
            self.assertIn("workspace_health_after: healthy", rendered)
            self.assertIn("workspace_recommended_actions:", rendered)
            self.assertIn("- /workspaces list", rendered)
            self.assertIn("- /workspaces cleanup", rendered)
            self.assertIn("- /workspaces cleanup apply orphan-agent", rendered)
            self.assertIn("workspace_planned_paths:", rendered)
            self.assertIn("workspace_applied_paths:", rendered)
            bundle = session.task_workspace_action_bundle(task.id)
            assert bundle is not None
            self.assertEqual(bundle["primary_action"], "workspace_cleanup_preview")
            self.assertEqual(bundle["secondary_action"], "workspace_cleanup_apply orphan-agent")
            self.assertEqual(bundle["tertiary_action"], "/workspaces list")
            self.assertEqual(bundle["target"], "orphan-agent")
            self.assertEqual(bundle["workspace_health"], "healthy")
            detail_metadata = session.task_workspace_detail_metadata(task.id)
            assert detail_metadata is not None
            self.assertEqual(detail_metadata["workspace_action"], "cleanup")
            self.assertEqual(detail_metadata["workspace_target"], "orphan-agent")
            self.assertEqual(detail_metadata["workspace_health_before"], "orphaned")
            self.assertEqual(detail_metadata["workspace_health_after"], "healthy")
            self.assertEqual(detail_metadata["workspace_planned_paths"], ["C:/tmp/orphan-agent"])
            self.assertEqual(detail_metadata["workspace_applied_paths"], ["C:/tmp/orphan-agent"])
            self.assertEqual(
                detail_metadata["workspace_recommended_actions"],
                [
                    "/workspaces list",
                    "/workspaces cleanup",
                    "/workspaces cleanup apply orphan-agent",
                ],
            )
        finally:
            if cwd.exists():
                shutil.rmtree(cwd, ignore_errors=True)

    def test_task_workspace_action_bundle_for_repair_task(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_workspace_repair_bundle"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            task = session.task_manager.create(
                "workspace",
                "Repair missing isolated workspace",
                workspace_action="repair",
                workspace_target="missing-agent",
                workspace_health_before="unavailable",
                workspace_health_after="cleanup_pending",
            )

            bundle = session.task_workspace_action_bundle(task.id)

            assert bundle is not None
            self.assertEqual(bundle["primary_action"], "workspace_repair missing-agent")
            self.assertEqual(bundle["secondary_action"], "workspace_cleanup_preview")
            self.assertEqual(bundle["tertiary_action"], "/workspaces list")
            self.assertEqual(bundle["target"], "missing-agent")
            self.assertEqual(bundle["workspace_health"], "cleanup_pending")
        finally:
            if cwd.exists():
                shutil.rmtree(cwd, ignore_errors=True)

    def test_task_workspace_detail_metadata_includes_failure_reason(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_workspace_failure_detail"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            task = session.task_manager.create(
                "workspace",
                "Repair missing isolated workspace",
                workspace_action="repair",
                workspace_target="missing-agent",
                workspace_health_before="unavailable",
                workspace_health_after="unavailable",
                workspace_planned_paths=["C:/tmp/missing-agent"],
                workspace_applied_paths=[],
                workspace_failure_reason="missing-agent: repair backend failed",
            )

            detail_metadata = session.task_workspace_detail_metadata(task.id)
            rendered = session.describe_task_detail(task.id)

            assert detail_metadata is not None
            self.assertEqual(detail_metadata["workspace_planned_paths"], ["C:/tmp/missing-agent"])
            self.assertEqual(detail_metadata["workspace_applied_paths"], [])
            self.assertEqual(
                detail_metadata["workspace_failure_reason"],
                "missing-agent: repair backend failed",
            )
            self.assertEqual(
                detail_metadata["workspace_recommended_actions"],
                [
                    "/workspaces list",
                    "/workspaces repair missing-agent",
                    "/workspaces cleanup",
                ],
            )
            self.assertIn("workspace_recommended_actions:", rendered)
            self.assertIn("- /workspaces repair missing-agent", rendered)
            self.assertIn("workspace_failure_reason: missing-agent: repair backend failed", rendered)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd, ignore_errors=True)

    def test_describe_recent_changes_and_undo_last_change(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_recent_changes"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )

            WriteFileTool().execute({"path": "demo.txt", "content": "hello"}, ctx)

            changes = session.describe_recent_changes()
            self.assertIn("tool=write_file", changes)
            self.assertIn("Undo stack:", changes)
            self.assertIn("Working set:", changes)
            self.assertIn("primary_path: demo.txt", changes)
            self.assertIn("target=open_file demo.txt:1", changes)
            self.assertIn("created demo.txt (+1 -0)", changes)
            self.assertIn("+hello", changes)
            self.assertIn("(c1)", session.recent_change_entries()[0])
            detail = session.selected_change_detail()
            detail_metadata = session.selected_change_detail_metadata()
            self.assertIn("change:", detail)
            self.assertIn("tool: write_file", detail)
            self.assertIn("files: 1", detail)
            self.assertIn("actions: create=1 update=0 delete=0 move=0", detail)
            self.assertIn("summary: Created demo.txt", detail)
            self.assertIn("Files", detail)
            self.assertIn("> 1. created demo.txt", detail)
            self.assertIn("Focused file (1/1)", detail)
            self.assertIn("Focused file context:", detail)
            self.assertIn("primary_target: open_file demo.txt:1", detail)
            self.assertIn("--- a/demo.txt", detail)
            self.assertIn("+++ b/demo.txt", detail)
            self.assertIn("+hello", detail)
            self.assertEqual(detail_metadata["selected_change_id"], session.state.recent_change_sets[0].change_id)
            self.assertEqual(detail_metadata["file_context_files"][0]["change_id"], session.state.recent_change_sets[0].change_id[:8])
            self.assertEqual(detail_metadata["file_context_files"][0]["diff_target_count"], 1)

            undo_output = session.undo_last_change()
            self.assertIn("Undid 1 change(s).", undo_output)
            self.assertIn("- Created demo.txt", undo_output)
            self.assertFalse((cwd / "demo.txt").exists())
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_redo_last_undo_reapplies_change(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_redo_changes"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )

            WriteFileTool().execute({"path": "demo.txt", "content": "hello"}, ctx)
            session.undo_last_change()
            redo_output = session.redo_last_undo()

            self.assertIn("Redid 1 change(s).", redo_output)
            self.assertEqual((cwd / "demo.txt").read_text(encoding="utf-8"), "hello")
            self.assertNotIn("Redo stack:", session.describe_recent_changes())
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_undo_last_change_can_target_specific_change_id(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_selective_undo"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )

            WriteFileTool().execute({"path": "first.txt", "content": "one"}, ctx)
            WriteFileTool().execute({"path": "second.txt", "content": "two"}, ctx)
            first_change_id = session.state.recent_change_sets[0].change_id[:8]

            output = session.undo_last_change(first_change_id)

            self.assertIn("Undid 1 change(s).", output)
            self.assertFalse((cwd / "first.txt").exists())
            self.assertTrue((cwd / "second.txt").exists())
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_redo_last_undo_can_target_specific_change_id(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_selective_redo"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )

            WriteFileTool().execute({"path": "first.txt", "content": "one"}, ctx)
            WriteFileTool().execute({"path": "second.txt", "content": "two"}, ctx)
            first_change_id = session.state.recent_change_sets[0].change_id[:8]
            second_change_id = session.state.recent_change_sets[1].change_id[:8]
            session.undo_last_change(first_change_id)
            session.undo_last_change(second_change_id)

            output = session.redo_last_undo(first_change_id)

            self.assertIn("Redid 1 change(s).", output)
            self.assertTrue((cwd / "first.txt").exists())
            self.assertFalse((cwd / "second.txt").exists())
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_undo_multiple_changes_respects_stack_order(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_undo_order"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )

            WriteFileTool().execute({"path": "demo.txt", "content": "one"}, ctx)
            WriteFileTool().execute({"path": "demo.txt", "content": "two"}, ctx)
            session.undo_last_change("2")

            self.assertFalse((cwd / "demo.txt").exists())
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_recent_changes_persist_in_transcript(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_change_persist"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            ctx = ToolContext(
                cwd=cwd,
                permission_manager=PermissionManager(interactive=False),
                task_manager=TaskManager(),
                session=session,
            )
            WriteFileTool().execute({"path": "demo.txt", "content": "hello"}, ctx)

            transcript_path = save_transcript(session.config, session.state)
            restored = load_transcript(transcript_path)

            self.assertEqual(len(restored.recent_change_sets), 1)
            self.assertEqual(restored.recent_change_sets[0].tool_name, "write_file")
            self.assertEqual(restored.recent_change_sets[0].files[0].path, "demo.txt")
            self.assertEqual(restored.recent_change_sets[0].files[0].action_kind, "create")
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_selected_change_detail_can_focus_specific_file(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_change_focus"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.record_workspace_change(
                tool_name="apply_patch",
                summary="Updated two files",
                file_changes=[
                    WorkspaceFileChange(
                        path="a.py",
                        existed_before=True,
                        before_content="old_a\n",
                        after_content="new_a\n",
                        action_kind="update",
                    ),
                    WorkspaceFileChange(
                        path="b.py",
                        existed_before=True,
                        before_content="old_b\n",
                        after_content="new_b\n",
                        action_kind="update",
                    ),
                ],
            )
            detail = session.selected_change_detail(file_index=1)
            self.assertIn("files: 2", detail)
            self.assertIn("Files", detail)
            self.assertIn("  1. updated a.py", detail)
            self.assertIn("> 2. updated b.py", detail)
            self.assertIn("Focused file (2/2)", detail)
            self.assertIn("Focused file context:", detail)
            self.assertIn("primary_path: b.py", detail)
            self.assertIn("--- a/b.py", detail)
            self.assertIn("+++ b/b.py", detail)
            self.assertIn("-old_b", detail)
            self.assertIn("+new_b", detail)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_selected_change_detail_renders_related_task_and_plan_actions(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_change_actions"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.record_workspace_change(
                tool_name="apply_patch",
                summary="Update runtime flow",
                file_changes=[
                    WorkspaceFileChange(
                        path="runtime/session.py",
                        existed_before=True,
                        before_content="old\n",
                        after_content="new\n",
                        action_kind="update",
                    )
                ],
            )
            task = session.task_manager.create(
                "agent",
                "Implement runtime changes",
                workspace_planned_paths=["runtime/session.py"],
            )
            execution = session.task_manager.create(
                "agent",
                "Apply runtime patch",
                task_role="execution",
                active_plan_id="pending",
                active_plan_goal="map runtime",
                workspace_planned_paths=["runtime/session.py"],
            )
            session.record_planning_artifact(
                PlanningArtifact(
                    kind="ultraplan",
                    goal="map runtime",
                    summary="summary",
                    task_ids=[execution.id],
                    used_read_only_subagents=True,
                )
            )
            execution.metadata["active_plan_id"] = session.active_planning_artifact().artifact_id

            detail = session.selected_change_detail()

            self.assertIn("next_actions:", detail)
            self.assertIn("go_to_task: /task show " + task.id, detail)
            self.assertIn("/task show " + execution.id, detail)
            self.assertIn("go_to_plan: /plan file 1", detail)
            self.assertIn("/plan execution 1 file 1", detail)
            self.assertIn("stay_on_surface: /changes show ", detail)
            self.assertIn("/changes working-set", detail)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_selected_change_detail_preserves_current_task_focus_and_links_back_to_file_specific_task(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_change_focus_preserve"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.record_workspace_change(
                tool_name="apply_patch",
                summary="Updated two files",
                file_changes=[
                    WorkspaceFileChange(
                        path="a.py",
                        existed_before=True,
                        before_content="old_a\n",
                        after_content="new_a\n",
                        action_kind="update",
                    ),
                    WorkspaceFileChange(
                        path="b.py",
                        existed_before=True,
                        before_content="old_b\n",
                        after_content="new_b\n",
                        action_kind="update",
                    ),
                ],
            )
            task = session.task_manager.create(
                "agent",
                "Inspect runtime flow",
                workspace_planned_paths=["a.py", "b.py"],
            )

            session.remember_task_context_focus(task.id, file_index=1, preserve_current_focus=False)
            detail = session.selected_change_detail(preserve_current_focus=True)

            self.assertIn("> 2. updated b.py", detail)
            self.assertIn("Focused file (2/2)", detail)
            self.assertIn("primary_path: b.py", detail)
            self.assertIn("go_to_task: /task show " + task.id + " file 2", detail)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_describe_task_detail_includes_file_context(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_task_file_context"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.record_workspace_change(
                tool_name="apply_patch",
                summary="Update runtime flow",
                file_changes=[
                    WorkspaceFileChange(
                        path="runtime/session.py",
                        existed_before=True,
                        before_content="old\n",
                        after_content="new\n",
                        action_kind="update",
                    )
                ],
            )
            task = session.task_manager.create("agent", "Inspect runtime flow")

            rendered = session.describe_task_detail(task.id)
            payload = session.task_file_context_payload(task.id)

            self.assertIn("focused file:", rendered)
            self.assertIn("- focused file: runtime/session.py", rendered)
            self.assertIn("- related change:", rendered)
            self.assertIn("- diff hunks: 1", rendered)
            self.assertIn("- context-only: no", rendered)
            self.assertIn("- next_actions:", rendered)
            self.assertIn("go_to_change: /changes show", rendered)
            self.assertIn("go_to_plan: none", rendered)
            self.assertIn("stay_on_surface: /task show " + task.id, rendered)
            self.assertIn("file_context:", rendered)
            self.assertIn("primary_path: runtime/session.py", rendered)
            self.assertIn("target=open_file runtime/session.py:1", rendered)
            self.assertIn("summary=updated runtime/session.py", rendered)
            self.assertIsNotNone(payload)
            assert payload is not None
            self.assertEqual(payload["file_context_scope"], "task")
            self.assertEqual(payload["file_context_files"][0]["change_id"], session.state.recent_change_sets[0].change_id[:8])
            self.assertEqual(payload["file_context_files"][0]["diff_target_count"], 1)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_describe_task_detail_can_focus_context_only_file(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_task_context_only"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            task = session.task_manager.create(
                "agent",
                "Inspect notes",
                workspace_planned_paths=["notes.md"],
            )

            rendered = session.describe_task_detail(task.id)

            self.assertIn("focused file:", rendered)
            self.assertIn("- focused file: notes.md", rendered)
            self.assertIn("- in scope because: active task", rendered)
            self.assertIn("- context-only: yes", rendered)
            self.assertNotIn("- related change:", rendered)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_describe_task_detail_preserves_current_change_focus_and_file_specific_stay_actions(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_task_focus_preserve"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.record_workspace_change(
                tool_name="apply_patch",
                summary="Updated two files",
                file_changes=[
                    WorkspaceFileChange(
                        path="a.py",
                        existed_before=True,
                        before_content="old_a\n",
                        after_content="new_a\n",
                        action_kind="update",
                    ),
                    WorkspaceFileChange(
                        path="b.py",
                        existed_before=True,
                        before_content="old_b\n",
                        after_content="new_b\n",
                        action_kind="update",
                    ),
                ],
            )
            task = session.task_manager.create("agent", "Inspect runtime flow")

            session.remember_selected_change_context_focus(
                index=0,
                file_index=1,
                redo=False,
                preserve_current_focus=False,
            )
            rendered = session.describe_task_detail(task.id, preserve_current_focus=True)

            self.assertIn("- focused file: b.py", rendered)
            self.assertIn("stay_on_surface: /task show " + task.id + " file 2", rendered)
            self.assertIn("go_to_change: /changes show", rendered)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_working_set_payload_merges_scope_reasons_and_context_classification(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_working_set_scope_reasons"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.record_workspace_change(
                tool_name="apply_patch",
                summary="Update app flow",
                file_changes=[
                    WorkspaceFileChange(
                        path="app.py",
                        existed_before=True,
                        before_content="old\n",
                        after_content="new\n",
                        action_kind="update",
                    )
                ],
            )
            task = session.task_manager.create(
                "agent",
                "Inspect app flow",
                workspace_planned_paths=["app.py", "notes.md"],
            )
            session.record_planning_artifact(
                PlanningArtifact(
                    kind="ultraplan",
                    goal="map app flow",
                    summary="Implementation Plan\n- inspect app.py",
                    task_ids=[task.id],
                    used_read_only_subagents=True,
                )
            )
            session.current_symbol_surface_payload = lambda: {  # type: ignore[method-assign]
                "selected_symbol": "build_app",
                "selected_navigation_target": {
                    "action": "open_file",
                    "path": "app.py",
                    "line": 8,
                    "label": "symbol file",
                },
            }

            payload = session.working_set_payload(limit=5)

            self.assertEqual(payload["file_context_scope"], "session")
            files = payload["file_context_files"]
            self.assertGreaterEqual(len(files), 2)
            self.assertEqual(files[0]["path"], "app.py")
            self.assertEqual(
                files[0]["scope_reasons"],
                ["active task", "active plan", "recent change", "symbol navigation"],
            )
            self.assertTrue(files[0]["has_related_change"])
            self.assertTrue(files[0]["has_diff_hunks"])
            self.assertFalse(files[0]["is_context_only"])
            notes_item = next(item for item in files if item["path"] == "notes.md")
            self.assertEqual(notes_item["scope_reasons"], ["active task", "active plan"])
            self.assertFalse(notes_item["has_related_change"])
            self.assertFalse(notes_item["has_diff_hunks"])
            self.assertTrue(notes_item["is_context_only"])
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_describe_working_set_renders_scope_reasons_and_context_only(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_working_set_render"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.record_workspace_change(
                tool_name="apply_patch",
                summary="Update app flow",
                file_changes=[
                    WorkspaceFileChange(
                        path="app.py",
                        existed_before=True,
                        before_content="old\n",
                        after_content="new\n",
                        action_kind="update",
                    )
                ],
            )
            task = session.task_manager.create(
                "agent",
                "Inspect app flow",
                workspace_planned_paths=["app.py", "notes.md"],
            )
            session.record_planning_artifact(
                PlanningArtifact(
                    kind="ultraplan",
                    goal="map app flow",
                    summary="Implementation Plan\n- inspect app.py",
                    task_ids=[task.id],
                    used_read_only_subagents=True,
                )
            )

            rendered = session.describe_working_set(limit=5)

            self.assertIn("Working set:", rendered)
            self.assertIn("in_scope_because=active task, active plan, recent change", rendered)
            self.assertIn("change=", rendered)
            self.assertIn("diff_hunks=1", rendered)
            self.assertIn("context_only=yes", rendered)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_describe_context_summary_and_files_views(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_context_views"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.record_workspace_change(
                tool_name="apply_patch",
                summary="Update app flow",
                file_changes=[
                    WorkspaceFileChange(
                        path="app.py",
                        existed_before=True,
                        before_content="old\n",
                        after_content="new\n",
                        action_kind="update",
                    )
                ],
            )
            task = session.task_manager.create(
                "agent",
                "Inspect app flow",
                workspace_planned_paths=["app.py", "notes.md"],
            )
            session.record_planning_artifact(
                PlanningArtifact(
                    kind="ultraplan",
                    goal="map app flow",
                    summary="Implementation Plan\n- inspect app.py",
                    task_ids=[task.id],
                    used_read_only_subagents=True,
                )
            )

            summary = session.describe_context()
            files = session.describe_files()
            changes = session.describe_files(section="changes")
            tasks = session.describe_files(section="tasks")

            self.assertIn("## Context Usage", summary)
            self.assertIn("model:", summary)
            self.assertIn("estimated tokens:", summary)
            self.assertIn("| Base instructions |", summary)
            self.assertIn("| Default tools |", summary)
            self.assertIn("working set files:", files)
            self.assertIn("go_to_change=/changes show", files)
            self.assertIn("go_to_task=/task show " + task.id, files)
            self.assertIn("go_to_plan=/plan file 1", files)
            self.assertIn("filter: changes", changes)
            self.assertIn("app.py", changes)
            self.assertNotIn("notes.md", changes)
            self.assertIn("filter: tasks", tasks)
            self.assertIn("notes.md", tasks)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_explicit_context_entries_persist_and_extend_working_set(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_explicit_context"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "src").mkdir()
        (cwd / "src" / "app.py").write_text("print('app')\n", encoding="utf-8")
        (cwd / "src" / "unused.py").write_text("print('unused')\n", encoding="utf-8")
        (cwd / "notes.md").write_text("notes\n", encoding="utf-8")
        (cwd / "todo.md").write_text("todo\n", encoding="utf-8")

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.record_workspace_change(
                tool_name="apply_patch",
                summary="Update src app flow",
                file_changes=[
                    WorkspaceFileChange(
                        path="src/app.py",
                        existed_before=True,
                        before_content="old\n",
                        after_content="new\n",
                        action_kind="update",
                    )
                ],
            )
            task = session.task_manager.create(
                "agent",
                "Inspect app flow",
                workspace_planned_paths=["src/app.py", "notes.md", "todo.md"],
            )
            session.record_planning_artifact(
                PlanningArtifact(
                    kind="ultraplan",
                    goal="map app flow",
                    summary="Implementation Plan\n- inspect src/app.py",
                    task_ids=[task.id],
                    used_read_only_subagents=True,
                )
            )

            session.add_explicit_context_path("src")
            session.add_explicit_context_path("notes.md")
            save_path = save_transcript(session.config, session.state)
            loaded = load_transcript(save_path)
            payload = session.working_set_payload(limit=20)
            files = [item for item in payload.get("file_context_files", []) if isinstance(item, dict)]
            app_item = next(item for item in files if item["path"] == "src/app.py")
            notes_item = next(item for item in files if item["path"] == "notes.md")
            todo_item = next(item for item in files if item["path"] == "todo.md")

            summary = session.describe_context()
            explicit = session.describe_files(section="explicit")
            auto = session.describe_files(section="auto")
            status = session.describe_status(section="workflow")
            files_render = session.describe_files()

            self.assertEqual(len(loaded.explicit_context_entries), 2)
            self.assertEqual(loaded.explicit_context_entries[0].kind, "directory")
            self.assertEqual(loaded.explicit_context_entries[1].kind, "file")
            self.assertEqual(
                app_item["scope_reasons"],
                ["active task", "active plan", "recent change", "explicit context path"],
            )
            self.assertEqual(
                notes_item["scope_reasons"],
                ["active task", "active plan", "explicit context path"],
            )
            self.assertEqual(todo_item["scope_reasons"], ["active task", "active plan"])
            self.assertNotIn("src/unused.py", [item["path"] for item in files])
            self.assertIn("## Context Usage", summary)
            self.assertIn("| Base instructions |", summary)
            self.assertIn("| Default tools |", summary)
            self.assertIn("filter: explicit", explicit)
            self.assertIn("src/app.py", explicit)
            self.assertIn("notes.md", explicit)
            self.assertNotIn("todo.md", explicit)
            self.assertIn("filter: auto", auto)
            self.assertIn("todo.md", auto)
            self.assertNotIn("src/unused.py", auto)
            self.assertIn("explicit context entries: 2", status)
            self.assertIn("explicit-context files: 2", status)
            self.assertIn("explicit context path", files_render)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_explicit_context_unresolved_entries_render_safely(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_explicit_context_unresolved"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        target = cwd / "notes.md"
        target.write_text("notes\n", encoding="utf-8")

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.add_explicit_context_path("notes.md")
            target.unlink()

            listing = session.describe_explicit_context_paths()
            status = session.describe_status(section="workflow")

            self.assertIn("resolved=no", listing)
            self.assertIn("unresolved entry count: 1", listing)
            self.assertIn("explicit-context-contributed files: 0", listing)
            self.assertIn("contributes_files=0", listing)
            self.assertIn("unresolved explicit context entries: 1", status)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_files_and_diff_views_reuse_working_set_and_focus_model(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_files_diff_views"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.record_workspace_change(
                tool_name="apply_patch",
                summary="Update app flow",
                file_changes=[
                    WorkspaceFileChange(
                        path="app.py",
                        existed_before=True,
                        before_content="old\n",
                        after_content="new\n",
                        action_kind="update",
                    )
                ],
            )
            task = session.task_manager.create(
                "agent",
                "Inspect app flow",
                workspace_planned_paths=["app.py", "notes.md"],
            )
            session.record_planning_artifact(
                PlanningArtifact(
                    kind="ultraplan",
                    goal="map app flow",
                    summary="Implementation Plan\n- inspect app.py",
                    task_ids=[task.id],
                    used_read_only_subagents=True,
                )
            )

            files_context = session.describe_files()
            files_changes = session.describe_files(section="changes")
            diff_summary = session.describe_diff()
            diff_working_set = session.describe_diff(section="working-set")

            session.remember_task_context_focus(task.id, file_index=1)
            files_focused = session.describe_files(section="focused")
            files_tasks = session.describe_files(section="tasks")
            diff_focused = session.describe_diff(section="focused")

            self.assertIn("working set files:", files_context)
            self.assertIn("mix: diff_backed=", files_context)
            self.assertIn("1. app.py", files_context)
            self.assertIn("2. notes.md", files_context)
            self.assertIn("filter: changes", files_changes)
            self.assertIn("app.py", files_changes)
            self.assertNotIn("notes.md", files_changes)
            self.assertIn("diff summary:", diff_summary)
            self.assertIn("mix: diff_backed=", diff_summary)
            self.assertIn("diff-backed working-set files: 1", diff_summary)
            self.assertIn("diff-backed working set:", diff_working_set)
            self.assertIn("mix: diff_backed=", diff_working_set)
            self.assertIn("app.py", diff_working_set)
            self.assertIn("focused file:", files_focused)
            self.assertIn("- focused file: notes.md", files_focused)
            self.assertIn("filter: tasks", files_tasks)
            self.assertIn("1. notes.md", files_tasks)
            self.assertIn("stay_on_surface=/files focused", files_tasks)
            self.assertIn("focused file:", diff_focused)
            self.assertIn("- focused file: notes.md", diff_focused)
            self.assertIn("- diff status: no diff hunks on focused file", diff_focused)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_context_focus_prefers_selected_change_then_task_then_working_set(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_context_focus_precedence"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.record_workspace_change(
                tool_name="apply_patch",
                summary="Update app flow",
                file_changes=[
                    WorkspaceFileChange(
                        path="app.py",
                        existed_before=True,
                        before_content="old\n",
                        after_content="new\n",
                        action_kind="update",
                    )
                ],
            )
            task = session.task_manager.create(
                "agent",
                "Review notes",
                workspace_planned_paths=["notes.md"],
            )

            session.remember_task_context_focus(task.id, file_index=0)
            task_focused = session.describe_files(section="focused")
            history = session.describe_history(section="changes")
            session.remember_selected_change_context_focus(index=0, file_index=0, redo=False)
            change_focused = session.describe_files(section="focused")

            self.assertIn("focused file:", task_focused)
            self.assertIn("- focused file: notes.md", task_focused)
            self.assertIn("- go_to_task: /task show " + task.id, task_focused)
            self.assertIn("focused file context:", history)
            self.assertIn("- focused file: notes.md", history)
            self.assertIn("- go_to_task: /task show " + task.id, history)
            self.assertIn("- stay_on_surface: /history changes | /files focused | /diff focused | /status workflow", history)
            self.assertIn("focused file:", change_focused)
            self.assertIn("- focused file: app.py", change_focused)
            self.assertIn("- go_to_change: /changes show", change_focused)
            self.assertIn("- go_to_task: none", change_focused)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_resolve_file_context_helpers_return_selected_index_reordered_payload_and_focus(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_resolve_file_context_helpers"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.record_workspace_change(
                tool_name="apply_patch",
                summary="Update runtime flow",
                file_changes=[
                    WorkspaceFileChange(
                        path="runtime/session.py",
                        existed_before=True,
                        before_content="old\n",
                        after_content="new\n",
                        action_kind="update",
                    )
                ],
            )
            task = session.task_manager.create(
                "agent",
                "Inspect notes",
                workspace_planned_paths=["notes.md", "runtime/session.py"],
            )

            session.remember_task_context_focus(task.id, file_index=1, preserve_current_focus=False)
            task_context = session.resolve_task_file_context(task.id, file_index=1, preserve_current_focus=True)

            self.assertEqual(task_context["selected_index"], 1)
            self.assertEqual(task_context["focused_item"]["path"], "runtime/session.py")
            self.assertEqual(task_context["reordered_payload"]["file_context_files"][0]["path"], "runtime/session.py")

            session.remember_task_context_focus(task.id, file_index=0, preserve_current_focus=False)
            change_context = session.resolve_selected_change_file_context(
                index=0,
                file_index=0,
                preserve_current_focus=True,
            )

            self.assertEqual(change_context["selected_index"], 0)
            self.assertEqual(change_context["focused_item"]["path"], "runtime/session.py")
            self.assertEqual(change_context["reordered_payload"]["file_context_files"][0]["path"], "runtime/session.py")
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_workspace_detail_rendering_exposes_action_bundle_and_fallback(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_workspace_detail"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        workspace_dir = cwd / ".pyclaude" / "workspaces" / "detail-agent"
        try:
            session = Session(
                SessionConfig(cwd=cwd, interactive=False),
                state=SessionState(
                    session_id="workspace-detail-session",
                    original_cwd=str(cwd.resolve()),
                    effective_cwd=str(workspace_dir.resolve()),
                    workspace_mode="snapshot",
                    workspace_label="detail-agent",
                    workspace_cleanup_status="pending",
                    workspace_unavailable=True,
                    workspace_unavailable_reason="Workspace missing on disk.",
                    workspace_fallback_cwd=str(cwd.resolve()),
                ),
            )

            current = session.describe_current_workspace()
            detail = session.describe_workspace_inventory_detail("detail-agent")

            self.assertIn("Current workspace", current)
            self.assertIn("primary action: workspace_repair workspace-detail-session", current)
            self.assertIn("fallback_cwd:", current)
            self.assertIn("unavailable_reason: Workspace missing on disk.", current)

            self.assertIn("Isolated workspace detail", detail)
            self.assertIn("matched_workspaces: 1", detail)
            self.assertIn("primary action: workspace_repair workspace-detail-session", detail)
            self.assertIn("tertiary action: /workspaces list", detail)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_describe_active_plan_includes_file_context(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_plan_file_context"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.record_workspace_change(
                tool_name="apply_patch",
                summary="Update planner surface",
                file_changes=[
                    WorkspaceFileChange(
                        path="planner.py",
                        existed_before=True,
                        before_content="old\n",
                        after_content="new\n",
                        action_kind="update",
                    )
                ],
            )
            session.record_planning_artifact(
                PlanningArtifact(
                    kind="ultraplan",
                    goal="map runtime",
                    summary="Implementation Plan\n- inspect planner.py",
                    used_read_only_subagents=True,
                )
            )

            rendered = session.describe_active_plan()
            payload = session.active_plan_file_context_payload()

            self.assertIn("focused file:", rendered)
            self.assertIn("- focused file: planner.py", rendered)
            self.assertIn("- related change:", rendered)
            self.assertIn("- diff hunks: 1", rendered)
            self.assertIn("file_context:", rendered)
            self.assertIn("primary_path: planner.py", rendered)
            self.assertIn("target=open_file planner.py:1", rendered)
            self.assertIsNotNone(payload)
            assert payload is not None
            self.assertEqual(payload["file_context_scope"], "active_plan")
            self.assertEqual(payload["file_context_files"][0]["change_id"], session.state.recent_change_sets[0].change_id[:8])
            self.assertEqual(payload["file_context_files"][0]["diff_target_count"], 1)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_describe_active_plan_can_focus_context_only_file(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_plan_context_only"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.record_workspace_change(
                tool_name="apply_patch",
                summary="Update app flow",
                file_changes=[
                    WorkspaceFileChange(
                        path="app.py",
                        existed_before=True,
                        before_content="old\n",
                        after_content="new\n",
                        action_kind="update",
                    )
                ],
            )
            task = session.task_manager.create(
                "agent",
                "Inspect app flow",
                workspace_planned_paths=["app.py", "notes.md"],
            )
            session.record_planning_artifact(
                PlanningArtifact(
                    kind="ultraplan",
                    goal="map app flow",
                    summary="Implementation Plan\n- inspect app.py",
                    task_ids=[task.id],
                    used_read_only_subagents=True,
                )
            )

            rendered = session.describe_active_plan(file_index=1)

            self.assertIn("focused file:", rendered)
            self.assertIn("- focused file: notes.md", rendered)
            self.assertIn("- context-only: yes", rendered)
            self.assertIn("- in scope because: active plan", rendered)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_plan_surfaces_preserve_focus_from_selected_change(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_plan_focus_preserve"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.record_workspace_change(
                tool_name="apply_patch",
                summary="Update two files",
                file_changes=[
                    WorkspaceFileChange(
                        path="a.py",
                        existed_before=True,
                        before_content="old_a\n",
                        after_content="new_a\n",
                        action_kind="update",
                    ),
                    WorkspaceFileChange(
                        path="b.py",
                        existed_before=True,
                        before_content="old_b\n",
                        after_content="new_b\n",
                        action_kind="update",
                    ),
                ],
            )
            session.record_planning_artifact(
                PlanningArtifact(
                    kind="ultraplan",
                    goal="map runtime",
                    summary="Implementation Plan\n- inspect a.py\n- inspect b.py",
                    used_read_only_subagents=True,
                )
            )
            artifact = session.active_planning_artifact()
            assert artifact is not None
            scout = session.task_manager.create(
                "ultraplan_scout",
                "Scout runtime surface",
                task_role="scout",
                active_plan_id=artifact.artifact_id,
                active_plan_goal=artifact.goal,
                workspace_planned_paths=["a.py", "b.py"],
            )
            execution = session.task_manager.create(
                "agent",
                "Implement runtime changes",
                task_role="execution",
                active_plan_id=artifact.artifact_id,
                active_plan_goal=artifact.goal,
                workspace_planned_paths=["a.py", "b.py"],
            )
            artifact.task_ids.extend([scout.id, execution.id])

            session.remember_selected_change_context_focus(index=0, file_index=1, redo=False)
            rendered_plan = session.describe_active_plan()
            rendered_advisor = session.describe_active_plan_advisor()
            rendered_scouts = session.describe_active_plan_scouts()
            rendered_execution = session.describe_active_plan_execution()

            self.assertIn("- focused file: b.py", rendered_plan)
            self.assertIn("- focused file: b.py", rendered_advisor)
            self.assertIn("go_to_plan: /plan file 2", rendered_advisor)
            self.assertIn("- focused file: b.py", rendered_scouts)
            self.assertIn("- focused file: b.py", rendered_execution)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_selected_change_detail_collapses_move_cleanup_into_single_visible_move(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_move_change"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.record_workspace_change(
                tool_name="apply_patch",
                summary="Applied patch (1 file(s); move=1)",
                file_changes=[
                    WorkspaceFileChange(
                        path="before.txt",
                        existed_before=True,
                        before_content="one\n",
                        after_content=None,
                        action_kind="move_source",
                        source_path="after.txt",
                        change_mode="patch move",
                    ),
                    WorkspaceFileChange(
                        path="after.txt",
                        existed_before=False,
                        before_content="",
                        after_content="ONE\n",
                        action_kind="move",
                        source_path="before.txt",
                        change_mode="patch move",
                    ),
                ],
            )

            detail = session.selected_change_detail()

            self.assertIn("files: 1", detail)
            self.assertIn("actions: create=0 update=0 delete=0 move=1", detail)
            self.assertIn("> 1. moved before.txt -> after.txt", detail)
            self.assertIn("from: before.txt", detail)
            self.assertIn("mode: patch move", detail)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_describe_tasks_and_config_include_session_checklist(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_checklist_views"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.create_checklist_task(
                subject="Inspect runtime",
                description="Inspect session.py",
                active_form="Inspecting runtime",
            )
            session.create_checklist_task(
                subject="Run tests",
                description="Run focused tests",
                active_form="Running tests",
                status="in_progress",
            )

            tasks = session.describe_tasks()
            config = session.describe_config()

            self.assertIn("session_checklist:", tasks)
            self.assertIn("session_checklist_tasks: 2", tasks)
            self.assertIn("session_checklist_in_progress: 1", tasks)
            self.assertIn("subject=Inspect runtime", tasks)
            self.assertIn("subject=Run tests", tasks)
            self.assertIn("No background tasks.", tasks)
            self.assertIn("session_checklist_tasks: 2", config)
            self.assertIn("session_checklist_in_progress: 1", config)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_build_prompts_include_session_checklist_context(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_checklist_prompt"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.create_checklist_task(
                subject="Inspect runtime",
                description="Inspect session.py",
                active_form="Inspecting runtime",
                status="in_progress",
            )
            session.create_checklist_task(
                subject="Run tests",
                description="Run focused tests",
                active_form="Running tests",
            )

            system_prompt = session.build_system_prompt()
            turn_prompt = session.build_turn_prompt("Fix the checklist integration")

            self.assertIn("Session checklist guidance:", system_prompt)
            self.assertIn("Current session checklist:", system_prompt)
            self.assertIn("subject=Inspect runtime", system_prompt)
            self.assertIn("Before creating new checklist tasks, call session_task_list", system_prompt)
            self.assertIn("Before updating a specific checklist task, call session_task_get", system_prompt)
            self.assertIn("Session checklist to treat as active execution context:", turn_prompt)
            self.assertIn("status=in_progress", turn_prompt)
            self.assertIn("Call session_task_list before creating new checklist tasks", turn_prompt)
            self.assertIn("Call session_task_get before updating a specific checklist task", turn_prompt)
            self.assertIn("Current user request:", turn_prompt)
            self.assertIn("Fix the checklist integration", turn_prompt)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_build_prompts_include_recent_checklist_duplicate_guard_context(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_checklist_duplicate_prompt"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            created = session.create_checklist_task(
                subject="Inspect runtime",
                description="Inspect session.py",
                active_form="Inspecting runtime",
                status="in_progress",
            )
            duplicate = session.create_checklist_task(
                subject="Inspect runtime",
                description="Inspect session.py",
                active_form="Inspecting runtime",
            )

            self.assertFalse(duplicate["created"])

            system_prompt = session.build_system_prompt()
            turn_prompt = session.build_turn_prompt("Continue runtime work")

            self.assertIn("Recent checklist duplicate guard:", system_prompt)
            self.assertIn(f"matched_task_id={created['id']}", system_prompt)
            self.assertIn("reason=Matched existing checklist task by subject, description, and active_form.", system_prompt)
            self.assertIn("recommended_action=Call session_task_get for task", system_prompt)
            self.assertIn("next_step=Use session_task_get", system_prompt)
            self.assertIn("Recent checklist duplicate guard:", turn_prompt)
            self.assertIn("do not create another checklist task", turn_prompt)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_symbol_command_updates_current_symbol_surface_and_config(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_symbol_surface"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.py").write_text(
            "def build():\n"
            "    return 1\n\n"
            "value = build()\n",
            encoding="utf-8",
        )

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))

            handled, output = session.handle_repl_command("/symbol actions build")

            self.assertTrue(handled)
            self.assertIn("surface_kind: symbol_actions", str(output))
            self.assertIn("selected_symbol: build", str(output))
            self.assertIn("definitions:", str(output))
            self.assertIn("references:", str(output))
            self.assertIn("selected_symbol_primary_action: /symbol open primary", str(output))
            payload = session.current_symbol_surface_payload()
            self.assertIsNotNone(payload)
            self.assertEqual(payload["surface_kind"], "symbol_actions")
            config = session.describe_config()
            self.assertIn("symbol_surface_kind: symbol_actions", config)
            self.assertIn("symbol_selected_symbol: build", config)
            self.assertIn("selected_symbol_primary_action: /symbol open primary", config)
            self.assertIn("selected_symbol_secondary_action: /symbol open secondary", config)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_symbol_commands_can_cycle_selected_definition_and_reference(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        try:
            session._remember_symbol_surface(  # noqa: SLF001
                {
                    "surface_kind": "symbol_actions",
                    "selected_symbol": "build",
                    "definition_count": 2,
                    "reference_count": 2,
                    "definitions": [
                        {"action": "open_symbol", "path": "demo.py", "line": 1, "label": "definition one"},
                        {"action": "open_symbol", "path": "demo.py", "line": 10, "label": "definition two"},
                    ],
                    "references": [
                        {"action": "open_reference", "path": "demo.py", "line": 4, "label": "reference one"},
                        {"action": "open_reference", "path": "demo.py", "line": 20, "label": "reference two"},
                    ],
                    "selected_definition": {
                        "action": "open_symbol",
                        "path": "demo.py",
                        "line": 1,
                        "label": "definition one",
                    },
                    "selected_definition_index": 0,
                    "selected_reference": {
                        "action": "open_reference",
                        "path": "demo.py",
                        "line": 4,
                        "label": "reference one",
                    },
                    "selected_reference_index": 0,
                    "navigation_target": {
                        "action": "open_symbol",
                        "path": "demo.py",
                        "line": 1,
                        "label": "definition one",
                    },
                    "selected_navigation_target": {
                        "action": "open_symbol",
                        "path": "demo.py",
                        "line": 1,
                        "label": "definition one",
                    },
                }
            )

            handled, output = session.handle_repl_command("/symbol next definition")
            self.assertTrue(handled)
            self.assertIn("selected_definition_index: 2/2", str(output))
            payload = session.current_symbol_surface_payload()
            self.assertEqual(payload["selected_definition_index"], 1)
            self.assertEqual(payload["selected_navigation_target"]["line"], 10)

            handled, output = session.handle_repl_command("/symbol next reference")
            self.assertTrue(handled)
            self.assertIn("selected_reference_index: 2/2", str(output))
            payload = session.current_symbol_surface_payload()
            self.assertEqual(payload["selected_reference_index"], 1)

            config = session.describe_config()
            self.assertIn("symbol_selected_definition_index: 2/2", config)
            self.assertIn("symbol_selected_reference_index: 2/2", config)
        finally:
            session.close()

    def test_tool_specs_expose_checklist_behavior_guidance(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        try:
            specs = {spec["name"]: spec for spec in session.tool_specs()}
            self.assertIn("call session_task_list first", specs["session_task_create"]["description"].lower())
            self.assertIn("before updating", specs["session_task_get"]["description"].lower())
            self.assertIn("before creating new checklist tasks", specs["session_task_list"]["description"].lower())
            self.assertIn("prefer updating", specs["session_task_update"]["description"].lower())
            self.assertIn("rewrite the full checklist", specs["todo_write"]["description"].lower())
        finally:
            session.close()

    def test_describe_task_detail_renders_session_checklist_detail(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_checklist_detail"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            created = session.create_checklist_task(
                subject="Inspect runtime",
                description="Inspect session.py",
                active_form="Inspecting runtime",
                status="in_progress",
                owner="assistant",
                blocks=["task-b"],
                blocked_by=["task-a"],
                metadata={"area": "runtime"},
            )
            duplicate = session.create_checklist_task(
                subject="Inspect runtime",
                description="Inspect session.py",
                active_form="Inspecting runtime",
            )

            rendered = session.describe_task_detail(created["id"])
            metadata = session.checklist_task_detail_metadata(created["id"])
            duplicate_guard = session.checklist_duplicate_guard_payload()

            self.assertFalse(duplicate["created"])
            self.assertIn("kind: session_checklist", rendered)
            self.assertIn("subject: Inspect runtime", rendered)
            self.assertIn("active_form: Inspecting runtime", rendered)
            self.assertIn("checklist_actions:", rendered)
            self.assertIn(f"selected_checklist_primary_action: checklist_mark_completed {created['id']}", rendered)
            self.assertIn(f"selected_checklist_secondary_action: checklist_reopen {created['id']}", rendered)
            self.assertIn("checklist_task_detail:", rendered)
            self.assertIn("checklist_recommended_actions:", rendered)
            self.assertIn("checklist_blocks:", rendered)
            self.assertIn("- task-b", rendered)
            self.assertIn("checklist_blocked_by:", rendered)
            self.assertIn("- task-a", rendered)
            self.assertIn("checklist_metadata:", rendered)
            self.assertIn("- area: runtime", rendered)
            self.assertIn("checklist_duplicate_guard:", rendered)
            self.assertIn(f"checklist_duplicate_matched_task_id: {created['id']}", rendered)
            self.assertIn("checklist_duplicate_recommended_action:", rendered)
            self.assertIsNotNone(duplicate_guard)
            self.assertEqual(duplicate_guard["matched_task_id"], created["id"])
            self.assertIsNotNone(metadata)
            self.assertEqual(metadata["checklist_subject"], "Inspect runtime")
            self.assertEqual(metadata["checklist_status"], "in_progress")
            self.assertEqual(metadata["checklist_blocks"], ["task-b"])
            self.assertEqual(metadata["checklist_blocked_by"], ["task-a"])
            self.assertEqual(metadata["checklist_metadata"], {"area": "runtime"})
            self.assertEqual(metadata["checklist_primary_action"], f"checklist_mark_completed {created['id']}")
            self.assertEqual(metadata["checklist_secondary_action"], f"checklist_reopen {created['id']}")
            self.assertEqual(metadata["checklist_edit_subject_action"], f"checklist_set_subject {created['id']}")
            self.assertEqual(
                metadata["checklist_edit_description_action"],
                f"checklist_set_description {created['id']}",
            )
            self.assertEqual(metadata["checklist_edit_owner_action"], f"checklist_set_owner {created['id']}")
            self.assertEqual(
                metadata["checklist_edit_active_form_action"],
                f"checklist_set_active_form {created['id']}",
            )
            self.assertEqual(metadata["checklist_edit_blocks_action"], f"checklist_set_blocks {created['id']}")
            self.assertEqual(
                metadata["checklist_edit_blocked_by_action"],
                f"checklist_set_blocked_by {created['id']}",
            )
            self.assertEqual(
                metadata["checklist_edit_metadata_action"],
                f"checklist_set_metadata {created['id']}",
            )
            self.assertEqual(
                metadata["checklist_recommended_actions"],
                [
                    f"session_task_get {created['id']}",
                    f"session_task_update {created['id']} status=completed",
                    "session_task_list",
                ],
            )
            self.assertEqual(metadata["checklist_duplicate_matched_task_id"], created["id"])
            self.assertIn("session_task_get", metadata["checklist_duplicate_recommended_action"])
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_checklist_edit_helpers_update_fields_and_dependencies(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_checklist_edit_helpers"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            existing_a = session.create_checklist_task(
                subject="Dependency A",
                description="A",
                active_form="Doing A",
            )
            existing_b = session.create_checklist_task(
                subject="Dependency B",
                description="B",
                active_form="Doing B",
            )
            created = session.create_checklist_task(
                subject="Inspect runtime",
                description="Inspect session.py",
                active_form="Inspecting runtime",
                status="pending",
                owner="assistant",
                blocks=[existing_a["id"]],
                blocked_by=[existing_b["id"]],
            )

            subject_result = session.checklist_set_subject(created["id"], "Review runtime flow")
            description_result = session.checklist_set_description(created["id"], "")
            owner_result = session.checklist_set_owner(created["id"], "")
            active_form_result = session.checklist_set_active_form(created["id"], "Reviewing runtime")
            blocks_result = session.checklist_set_blocks(
                created["id"],
                f'{existing_a["id"]}, {existing_b["id"]}',
            )
            blocked_by_result = session.checklist_set_blocked_by(created["id"], "")
            metadata_result = session.checklist_set_metadata(created["id"], "area=runtime\npriority=high")
            updated = session.get_checklist_task(created["id"])

            self.assertEqual(
                subject_result,
                f'Updated checklist task "{created["id"]}" subject to "Review runtime flow".',
            )
            self.assertEqual(
                description_result,
                f'Cleared checklist task "{created["id"]}" description.',
            )
            self.assertEqual(owner_result, f'Cleared checklist task "{created["id"]}" owner.')
            self.assertEqual(
                active_form_result,
                f'Updated checklist task "{created["id"]}" active_form to "Reviewing runtime".',
            )
            self.assertEqual(
                blocks_result,
                f'Updated checklist task "{created["id"]}" blocks to: {existing_a["id"]}, {existing_b["id"]}.',
            )
            self.assertEqual(
                blocked_by_result,
                f'Cleared checklist task "{created["id"]}" blocked_by.',
            )
            self.assertEqual(
                metadata_result,
                f'Updated checklist task "{created["id"]}" metadata (2 entries).',
            )
            self.assertIsNotNone(updated)
            self.assertEqual(updated["subject"], "Review runtime flow")
            self.assertEqual(updated["description"], "")
            self.assertIsNone(updated["owner"])
            self.assertEqual(updated["active_form"], "Reviewing runtime")
            self.assertEqual(updated["blocks"], [existing_a["id"], existing_b["id"]])
            self.assertEqual(updated["blocked_by"], [])
            self.assertEqual(updated["metadata"], {"area": "runtime", "priority": "high"})
            self.assertEqual(
                session.checklist_set_subject(created["id"], "   "),
                "Checklist subject cannot be empty.",
            )
            self.assertEqual(
                session.checklist_set_active_form(created["id"], "   "),
                "Checklist active_form cannot be empty.",
            )
            self.assertEqual(
                session.checklist_set_metadata(created["id"], "broken-line"),
                "Checklist metadata lines must use key=value format.",
            )
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_restored_session_loads_persisted_session_checklist(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_checklist_restore"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.create_checklist_task(
                subject="Inspect runtime",
                description="Inspect session.py",
                active_form="Inspecting runtime",
                metadata={"area": "runtime"},
            )
            transcript_path = save_transcript(session.config, session.state)
            restored = Session(
                SessionConfig(cwd=cwd, interactive=False),
                state=load_transcript(transcript_path),
            )
            try:
                checklist = restored.checklist_tasks_payload()
                self.assertEqual(len(checklist), 1)
                self.assertEqual(checklist[0]["subject"], "Inspect runtime")
                self.assertEqual(checklist[0]["metadata"], {"area": "runtime"})
            finally:
                restored.close()
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)


if __name__ == "__main__":
    unittest.main()
