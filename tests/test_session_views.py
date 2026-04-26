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
from claudecode_py.session import Session
from claudecode_py.state import AdvisorReviewSummary, PlanningArtifact, SessionState, WorkspaceFileChange
from claudecode_py.storage.transcript import load_transcript, save_transcript
from claudecode_py.tools.base import ToolContext
from claudecode_py.permissions import PermissionManager
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
        self.assertIn("1. user: hello", rendered)
        self.assertIn("tool_use=list_dir", rendered)
        self.assertIn("tool_result=1 block", rendered)

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
        self.assertIn("session_id:", rendered)

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
        self.assertIn("active_plan_supersedes:", config)
        self.assertIn("active_plan_derived_from_drift: yes", config)
        self.assertIn("planning lifecycle:", tasks)
        self.assertIn("scout tasks:", tasks)
        self.assertIn("execution tasks following active plan:", tasks)
        self.assertIn("active_plan_task=yes", tasks)
        self.assertIn("scout=architecture-boundaries", tasks)
        self.assertIn("task_role=execution", tasks)
        self.assertEqual(session.planning_artifacts()[0].superseded_by_artifact_id, session.planning_artifacts()[1].artifact_id)
        active_detail = session.describe_active_plan()
        self.assertIn("lineage_position:", active_detail)
        self.assertIn("derived_from_drift: yes", active_detail)
        self.assertIn("derivation_reason: Need a narrower runtime-only revision.", active_detail)
        self.assertIn("comparisons:", active_detail)
        self.assertIn("against_previous:", active_detail)
        self.assertIn("summary_diff:", active_detail)
        self.assertIn("latest_session_advisor_review:", active_detail)
        self.assertIn("recent_plan_drift_analysis:", active_detail)
        self.assertIn("pending_tools: apply_patch", active_detail)

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
        self.assertIn("mcp_retrying_servers: 1", rendered)

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
            config = session.describe_config()
            prompt = session.build_system_prompt()

            self.assertIn("Project memory text", memory)
            self.assertIn("review", skills)
            self.assertIn("status=enabled,auto", skills)
            self.assertIn("draft", skills)
            self.assertIn("status=inactive", skills)
            self.assertIn("tags=review,quality", skills)
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

    def test_describe_saved_sessions_lists_recent_transcripts(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_saved_sessions"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            config = SessionConfig(cwd=cwd, interactive=False)
            save_transcript(
                config,
                SessionState(
                    session_id="session-demo",
                    messages=[{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
                ),
            )
            session = Session(config)
            rendered = session.describe_saved_sessions()
            self.assertIn("session-demo", rendered)
            self.assertIn("messages=1", rendered)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

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
            self.assertEqual(child.state.original_cwd, str(cwd.resolve()))
            self.assertEqual(child.state.effective_cwd, str(child.config.cwd.resolve()))
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
            self.assertIn("created demo.txt (+1 -0)", changes)
            self.assertIn("+hello", changes)
            self.assertIn("(c1)", session.recent_change_entries()[0])
            detail = session.selected_change_detail()
            self.assertIn("change:", detail)
            self.assertIn("tool: write_file", detail)
            self.assertIn("files: 1", detail)
            self.assertIn("actions: create=1 update=0 delete=0", detail)
            self.assertIn("summary: Created demo.txt", detail)
            self.assertIn("Files", detail)
            self.assertIn("> 1. created demo.txt", detail)
            self.assertIn("Focused file (1/1)", detail)
            self.assertIn("--- a/demo.txt", detail)
            self.assertIn("+++ b/demo.txt", detail)
            self.assertIn("+hello", detail)

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
                    ),
                    WorkspaceFileChange(
                        path="b.py",
                        existed_before=True,
                        before_content="old_b\n",
                        after_content="new_b\n",
                    ),
                ],
            )
            detail = session.selected_change_detail(file_index=1)
            self.assertIn("files: 2", detail)
            self.assertIn("Files", detail)
            self.assertIn("  1. updated a.py", detail)
            self.assertIn("> 2. updated b.py", detail)
            self.assertIn("Focused file (2/2)", detail)
            self.assertIn("--- a/b.py", detail)
            self.assertIn("+++ b/b.py", detail)
            self.assertIn("-old_b", detail)
            self.assertIn("+new_b", detail)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)


if __name__ == "__main__":
    unittest.main()
