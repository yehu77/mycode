from pathlib import Path
from io import StringIO
import json
import shutil
import sys
import threading
import unittest
import uuid
from contextlib import redirect_stdout
from unittest.mock import patch
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.cli import REPL_COMMAND_HELP, _handle_repl_command, build_parser, main, run_repl, run_tui
from claudecode_py.commands import CommandExecution
from claudecode_py.config import SessionConfig
from claudecode_py.mcp import McpClient, McpRegistry, McpServerConfig, McpVerificationResult
from claudecode_py.permissions import ApprovalRequest
from claudecode_py.runtime.events import RuntimeEvent
from claudecode_py.session import Session
from claudecode_py.state import AdvisorReviewSummary, PlanningArtifact, SessionState, WorkspaceFileChange
from claudecode_py.storage.background_sessions import (
    create_background_session,
    resolve_background_session,
    update_background_session,
)
from claudecode_py.storage.transcript import load_latest_transcript, save_transcript


def _cleanup_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _make_tmp_dir(prefix: str) -> Path:
    root = Path(__file__).resolve().parent / "_tmp"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{prefix}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


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


class ReconnectableTransport:
    def __init__(self, fail: bool) -> None:
        self.fail = fail

    def request(self, method: str, params: dict | None = None) -> dict:
        if method == "initialize":
            if self.fail:
                return {"error": {"code": -1, "message": "offline"}}
            return {
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"version": "1.0.1"},
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
        raise AssertionError(f"unexpected method: {method}")

    def close(self) -> None:
        return None


class CliCommandTests(unittest.TestCase):
    def test_parser_accepts_tui_command(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["tui"])

        self.assertEqual(args.command, "tui")

    def test_help_command_returns_help_text(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))

        handled, output = _handle_repl_command(session, "/help")

        self.assertTrue(handled)
        self.assertEqual(output, REPL_COMMAND_HELP)

    def test_config_command_returns_config_summary(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))

        handled, output = _handle_repl_command(session, "/config")

        self.assertTrue(handled)
        assert output is not None
        self.assertIn("cwd:", output)
        self.assertIn("provider:", output)

    def test_plugins_command_returns_plugin_summary(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))

        handled, output = _handle_repl_command(session, "/plugins")

        self.assertTrue(handled)
        assert output is not None
        self.assertIn("review: status=enabled", output)
        self.assertIn("commit: status=enabled", output)

    def test_plugin_command_returns_plugin_detail(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))

        handled, output = _handle_repl_command(session, "/plugin review")

        self.assertTrue(handled)
        assert output is not None
        self.assertIn("name: review", output)
        self.assertIn("plugin_id: review@builtin", output)
        self.assertIn("command_names: /review", output)

    def test_install_command_returns_install_guidance(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))

        handled, output = _handle_repl_command(session, "/install all")

        self.assertTrue(handled)
        assert output is not None
        self.assertIn("profile: all", output)
        self.assertIn("pip install -e", output)

    def test_ultraplan_command_returns_prompt_execution(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))

        handled, output = _handle_repl_command(session, "/ultraplan map the session factory refactor")

        self.assertTrue(handled)
        self.assertIsInstance(output, CommandExecution)
        assert isinstance(output, CommandExecution)
        self.assertIn("map the session factory refactor", output.prompt)
        self.assertIn("sub-agents", output.prompt)
        self.assertIn("agent", output.allowed_tool_names)
        self.assertIn("outline_project", output.allowed_tool_names)
        self.assertIn("task_get", output.allowed_tool_names)
        self.assertTrue(output.require_read_only_subagents)

    def test_insights_command_summarizes_workspace_transcripts(self) -> None:
        cwd = _make_tmp_dir("cli_insights")
        save_transcript(
            SessionConfig(cwd=cwd, interactive=False),
            SessionState(
                session_id="session-a",
                context_summary="summary",
                advisor_model="claude-3-opus-latest",
                messages=[
                    {"role": "user", "content": [{"type": "text", "text": "inspect repo"}]},
                    {
                        "role": "assistant",
                        "content": [{"type": "tool_use", "name": "bash", "input": {"command": "git status"}}],
                    },
                ],
            ),
        )
        session = Session(SessionConfig(cwd=cwd, interactive=False))

        try:
            handled, output = _handle_repl_command(session, "/insights")

            self.assertTrue(handled)
            assert output is not None
            self.assertIn("workspace insights", output)
            self.assertIn("sessions: 1", output)
            self.assertIn("sessions_with_advisor: 1", output)
            self.assertIn("top_tools: bash=1", output)
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_insights_command_can_show_specific_session(self) -> None:
        cwd = _make_tmp_dir("cli_insights_session")
        save_transcript(
            SessionConfig(cwd=cwd, interactive=False),
            SessionState(
                session_id="session-abc123",
                enabled_plugin_names=["review"],
                messages=[
                    {"role": "user", "content": [{"type": "text", "text": "review change"}]},
                    {
                        "role": "assistant",
                        "content": [{"type": "tool_use", "name": "read_file", "input": {"path": "demo.py"}}],
                    },
                ],
            ),
        )
        session = Session(SessionConfig(cwd=cwd, interactive=False))

        try:
            handled, output = _handle_repl_command(session, "/insights abc")

            self.assertTrue(handled)
            assert output is not None
            self.assertIn("session insights", output)
            self.assertIn("session_id: session-abc123", output)
            self.assertIn("top_tools: read_file=1", output)
            self.assertIn("manually_enabled_plugins: review", output)
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_advisor_command_updates_session_state(self) -> None:
        cwd = _make_tmp_dir("cli_advisor")
        session = Session(SessionConfig(cwd=cwd, interactive=False))

        try:
            handled_set, output_set = _handle_repl_command(session, "/advisor sonnet")
            handled_show, output_show = _handle_repl_command(session, "/advisor")
            handled_unset, output_unset = _handle_repl_command(session, "/advisor unset")
            restored_state, _ = load_latest_transcript(cwd)

            self.assertTrue(handled_set)
            self.assertTrue(handled_show)
            self.assertTrue(handled_unset)
            self.assertIn("claude-3-7-sonnet-latest", output_set or "")
            self.assertIn("Advisor: claude-3-7-sonnet-latest", output_show or "")
            self.assertIn("Advisor disabled", output_unset or "")
            assert restored_state is not None
            self.assertIsNone(restored_state.advisor_model)
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_advisor_command_supports_mode_updates(self) -> None:
        cwd = _make_tmp_dir("cli_advisor_mode")
        session = Session(SessionConfig(cwd=cwd, interactive=False))

        try:
            handled_set, output_set = _handle_repl_command(session, "/advisor sonnet interactive-review")
            handled_show, output_show = _handle_repl_command(session, "/advisor")
            handled_mode, output_mode = _handle_repl_command(session, "/advisor mode final-review")

            self.assertTrue(handled_set)
            self.assertTrue(handled_show)
            self.assertTrue(handled_mode)
            self.assertIn("Mode: interactive-review", output_set or "")
            self.assertIn("Mode: interactive-review", output_show or "")
            self.assertIn("Advisor mode set to final-review", output_mode or "")
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_advisor_status_reports_constraint_context(self) -> None:
        cwd = _make_tmp_dir("cli_advisor_status")
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        session.state.advisor_model = "claude-3-opus-latest"
        session.state.advisor_mode = "interactive-review"
        session.activate_execution_constraint(
            mode="read-only",
            source="before_write_block",
            reason="Need a safer read-only pass first.",
            increment=True,
        )
        session.record_plan_drift_context(
            "active_plan_goal: map runtime\n"
            "pending_tools: write_file\n"
            "active_plan_vs_candidate_diff:\n"
            "  - add query loop guard"
        )
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="map runtime",
                summary="summary",
                used_read_only_subagents=True,
                derived_from_drift=True,
                derivation_reason="Need a safer rewrite sequence.",
                advisor_risk_flags=["unsafe-write"],
            )
        )

        try:
            handled, output = _handle_repl_command(session, "/advisor status")

            self.assertTrue(handled)
            assert output is not None
            self.assertIn("Execution constraints: read-only", output)
            self.assertIn("Constraint source: before_write_block", output)
            self.assertIn("Last plan drift analysis:", output)
            self.assertIn("pending_tools: write_file", output)
            self.assertIn("Active plan risk flags: unsafe-write", output)
            self.assertIn("Active plan derived from drift: yes", output)
            self.assertIn("Active plan derivation reason: Need a safer rewrite sequence.", output)
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_plan_commands_manage_active_artifact(self) -> None:
        cwd = _make_tmp_dir("cli_plan_commands")
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        task = session.task_manager.create(
            "ultraplan_scout",
            "Scout architecture",
            planner_kind="ultraplan",
            scout_category="architecture-boundaries",
        )
        session.task_manager.complete(task.id, "Inspect session.py and runtime/context.py.")
        first_artifact = PlanningArtifact(
            kind="ultraplan",
            goal="first goal",
            summary="Current Architecture\n- old module map\n\nImplementation Plan\n- update session.py",
            used_read_only_subagents=True,
            task_ids=[task.id],
            advisor_status="block",
            advisor_reason="Need more explicit module boundaries.",
            advisor_suggested_changes=["Name the impacted modules."],
            advisor_risk_flags=["underspecified"],
        )
        session.record_planning_artifact(first_artifact)
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="second goal",
                summary="Current Architecture\n- refined module map\n\nImplementation Plan\n- update session.py\n- update query_loop.py",
                supersedes_artifact_id=first_artifact.artifact_id,
                derived_from_drift=True,
                derivation_reason="Need a revised runtime sequence.",
                used_read_only_subagents=True,
                advisor_status="approve",
            )
        )
        session.record_plan_drift_context(
            "active_plan_goal: second goal\n"
            "candidate_work_summary:\n"
            "focus query loop and session integration\n"
            "pending_tools: write_file"
        )
        older = session.planning_artifacts()[0]

        try:
            handled_show, output_show = _handle_repl_command(session, "/plan")
            handled_list, output_list = _handle_repl_command(session, "/plan list")
            handled_use, output_use = _handle_repl_command(session, f"/plan use {older.artifact_id[:6]}")
            handled_detail, output_detail = _handle_repl_command(session, f"/plan show {older.artifact_id[:6]}")
            handled_latest_detail, output_latest_detail = _handle_repl_command(session, "/plan show latest")
            handled_derive, output_derive = _handle_repl_command(session, "/plan derive tighten the module boundaries")
            handled_revert, output_revert = _handle_repl_command(session, f"/plan revert {older.artifact_id[:6]}")
            handled_clear, output_clear = _handle_repl_command(session, "/plan clear")

            self.assertTrue(handled_show)
            self.assertTrue(handled_list)
            self.assertTrue(handled_use)
            self.assertTrue(handled_detail)
            self.assertTrue(handled_latest_detail)
            self.assertTrue(handled_derive)
            self.assertTrue(handled_revert)
            self.assertTrue(handled_clear)
            self.assertIn("artifact_id:", output_show or "")
            self.assertIn("planning artifacts:", output_list or "")
            self.assertIn("Active planning artifact set", output_use or "")
            self.assertIn(f"artifact_id: {older.artifact_id}", output_detail or "")
            self.assertIn("supersedes_artifact_id:", output_detail or "")
            self.assertIn("lineage:", output_detail or "")
            self.assertIn("comparisons:", output_detail or "")
            self.assertIn("against_next:", output_detail or "")
            self.assertIn("summary_diff:", output_detail or "")
            self.assertIn("next_actions:", output_detail or "")
            self.assertIn(f"/plan derive {older.goal}", output_detail or "")
            self.assertIn(f"/plan revert {session.planning_artifacts()[-1].artifact_id}", output_latest_detail or "")
            self.assertIn("against_previous:", output_latest_detail or "")
            self.assertIn("derived_from_drift: yes", output_latest_detail or "")
            self.assertIn("derivation_reason: Need a revised runtime sequence.", output_latest_detail or "")
            self.assertIn("lineage_position:", output_latest_detail or "")
            self.assertIn("recent_plan_drift_analysis:", output_show or "")
            self.assertIn("pending_tools: write_file", output_show or "")
            self.assertIn("advisor_review:", output_detail or "")
            self.assertIn("Scout architecture", output_detail or "")
            self.assertIn("Inspect session.py and runtime/context.py.", output_detail or "")
            self.assertIsInstance(output_derive, CommandExecution)
            assert isinstance(output_derive, CommandExecution)
            self.assertEqual(output_derive.metadata["supersede_artifact_id"], older.artifact_id)
            self.assertIn("Reactivated planning artifact", output_revert or "")
            self.assertIn("Cleared active planning artifact", output_clear or "")
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_insights_command_reports_advisor_and_ultraplan_metrics(self) -> None:
        cwd = _make_tmp_dir("cli_insights_planning")
        save_transcript(
            SessionConfig(cwd=cwd, interactive=False),
            SessionState(
                session_id="session-plan",
                advisor_model="claude-3-opus-latest",
                advisor_mode="interactive-review",
                advisor_review_history=[
                    AdvisorReviewSummary(
                        checkpoint="before_write",
                        status="block",
                        reason="unsafe",
                        suggested_changes=["revise"],
                        risk_flags=["unsafe-write"],
                        model="claude-3-opus-latest",
                    ),
                    AdvisorReviewSummary(
                        checkpoint="final_answer",
                        status="revise",
                        reason="tighten",
                        suggested_changes=["clarify"],
                        risk_flags=[],
                        model="claude-3-opus-latest",
                    ),
                ],
                recent_planning_artifacts=[
                    PlanningArtifact(
                        kind="ultraplan",
                        goal="map runtime",
                        summary="plan summary",
                        supersedes_artifact_id="plan-0",
                        used_read_only_subagents=True,
                        scout_categories=["architecture-boundaries"],
                        task_ids=["task-1"],
                    )
                ],
                messages=[{"role": "user", "content": [{"type": "text", "text": "plan"}]}],
            ),
        )
        session = Session(SessionConfig(cwd=cwd, interactive=False))

        try:
            handled, output = _handle_repl_command(session, "/insights")

            self.assertTrue(handled)
            assert output is not None
            self.assertIn("advisor_revisions: 1", output)
            self.assertIn("advisor_blocks: 1", output)
            self.assertIn("constraint_triggers: 0", output)
            self.assertIn("ultraplan_runs: 1", output)
            self.assertIn("ultraplan_with_read_only_subagents: 1", output)
            self.assertIn("derived_plans: 1", output)
            self.assertIn("sessions_with_planning_artifacts: 1", output)
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_clear_command_clears_history(self) -> None:
        session = Session(
            SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False),
            state=SessionState(
                context_summary="Earlier conversation summary",
                messages=[{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
            ),
        )

        handled, output = _handle_repl_command(session, "/clear")

        self.assertTrue(handled)
        self.assertEqual(output, "Cleared in-memory conversation history for this session.")
        self.assertEqual(session.state.messages, [])
        self.assertIsNone(session.state.context_summary)

    def test_changes_and_undo_commands_operate_on_recorded_workspace_changes(self) -> None:
        cwd = _make_tmp_dir("cli_changes")
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        (cwd / "demo.txt").write_text("hello", encoding="utf-8")
        session.record_workspace_change(
            tool_name="write_file",
            summary="Created demo.txt",
            file_changes=[
                WorkspaceFileChange(
                    path="demo.txt",
                    existed_before=False,
                    before_content="",
                    after_content="hello",
                )
            ],
        )

        try:
            handled_changes, output_changes = _handle_repl_command(session, "/changes")
            handled_undo, output_undo = _handle_repl_command(session, "/undo")

            self.assertTrue(handled_changes)
            self.assertTrue(handled_undo)
            assert output_changes is not None
            assert output_undo is not None
            self.assertIn("Undo stack:", output_changes)
            self.assertIn("Created demo.txt", output_changes)
            self.assertIn("Undid 1 change(s).", output_undo)
            self.assertFalse((cwd / "demo.txt").exists())
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_redo_command_reapplies_recent_change(self) -> None:
        cwd = _make_tmp_dir("cli_redo")
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        session.record_workspace_change(
            tool_name="write_file",
            summary="Created demo.txt",
            file_changes=[
                WorkspaceFileChange(
                    path="demo.txt",
                    existed_before=False,
                    before_content="",
                    after_content="hello",
                )
            ],
        )
        session.undo_last_change()

        try:
            handled_redo, output_redo = _handle_repl_command(session, "/redo")
            self.assertTrue(handled_redo)
            assert output_redo is not None
            self.assertIn("Redid 1 change(s).", output_redo)
            self.assertEqual((cwd / "demo.txt").read_text(encoding="utf-8"), "hello")
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_undo_command_accepts_change_id_prefix(self) -> None:
        cwd = _make_tmp_dir("cli_undo_by_id")
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        (cwd / "first.txt").write_text("one", encoding="utf-8")
        session.record_workspace_change(
            tool_name="write_file",
            summary="Created first.txt",
            file_changes=[
                WorkspaceFileChange(
                    path="first.txt",
                    existed_before=False,
                    before_content="",
                    after_content="one",
                )
            ],
        )
        change_id = session.state.recent_change_sets[0].change_id[:8]

        try:
            handled, output = _handle_repl_command(session, f"/undo {change_id}")
            self.assertTrue(handled)
            assert output is not None
            self.assertIn("Undid 1 change(s).", output)
            self.assertFalse((cwd / "first.txt").exists())
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_mcp_commands_render_server_and_tool_state(self) -> None:
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
        session.provider = type(
            "ProviderStub",
            (),
            {"capabilities": type("CapabilitiesStub", (), {"supports_tool_calling": True})()},
        )()

        handled_servers, output_servers = _handle_repl_command(session, "/mcp")
        handled_tools, output_tools = _handle_repl_command(session, "/mcp-tools")

        self.assertTrue(handled_servers)
        self.assertTrue(handled_tools)
        assert output_servers is not None
        assert output_tools is not None
        self.assertIn("docs: transport=stdio", output_servers)
        self.assertIn("docs.echo_text", output_tools)

    def test_mcp_refresh_command_reloads_configured_servers(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_cli_mcp_refresh"
        _cleanup_dir(cwd)
        (cwd / ".pyclaude").mkdir(parents=True)
        server_script = Path(__file__).resolve().parent / "fixtures" / "fake_mcp_server.py"
        config_path = cwd / ".pyclaude" / "mcp_servers.json"
        config_path.write_text(
            json.dumps(
                {
                    "servers": [
                        {
                            "name": "fake",
                            "transport": "stdio",
                            "command": sys.executable,
                            "args": [str(server_script)],
                        }
                    ]
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )

        session = Session(SessionConfig(cwd=cwd, interactive=False, mcp_config_path=config_path))
        try:
            handled, output = _handle_repl_command(session, "/mcp-refresh")
            self.assertTrue(handled)
            assert output is not None
            self.assertIn("servers=1", output)
            self.assertIn("tools=1", output)
            self.assertIn("fake.echo_text", session.describe_mcp_tools())
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_mcp_reconnect_command_recovers_failed_server(self) -> None:
        registry = McpRegistry()
        transport_state = {"fail": True}

        def build_client():
            return McpClient(
                config=McpServerConfig(name="docs", transport="stdio", command="demo"),
                transport=ReconnectableTransport(transport_state["fail"]),
            )

        registry.register_client(build_client(), client_factory=build_client)
        registry.connect_server("docs")
        session = Session(
            SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False),
            mcp_registry=registry,
        )
        session.provider = type(
            "ProviderStub",
            (),
            {"capabilities": type("CapabilitiesStub", (), {"supports_tool_calling": True})()},
        )()

        try:
            transport_state["fail"] = False
            handled, output = _handle_repl_command(session, "/mcp-reconnect docs")
            self.assertTrue(handled)
            assert output is not None
            self.assertIn('Reconnected MCP server "docs"', output)
            self.assertIn("docs.echo_text", session.describe_mcp_tools())
        finally:
            session.close()

    def test_mcp_call_command_executes_tool_directly(self) -> None:
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

        try:
            handled, output = _handle_repl_command(session, '/mcp-call docs echo_text {"text":"hello"}')
            self.assertTrue(handled)
            assert output is not None
            self.assertIn("ok: yes", output)
            self.assertIn("source: ok", output)
            self.assertIn("echo:hello", output)
        finally:
            session.close()

    def test_mcp_verify_command_reports_model_path(self) -> None:
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

        def fake_ask(prompt: str, sink=None) -> str:
            del prompt, sink
            return "guessed"

        try:
            with patch.object(session, "ask", side_effect=fake_ask):
                handled, output = _handle_repl_command(session, '/mcp-verify docs echo_text {"text":"hello"}')
            self.assertTrue(handled)
            assert output is not None
            self.assertIn("ok: no", output)
            self.assertIn("source: model", output)
            self.assertIn("tool_called: no", output)
        finally:
            session.close()

    def test_mcp_verify_command_reports_success_when_model_calls_tool(self) -> None:
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

        def fake_ask(prompt: str, sink=None) -> str:
            del prompt
            assert sink is not None
            sink(
                RuntimeEvent(
                    kind="tool_started",
                    message='{"text":"hello"}',
                    tool_name="mcp__docs__echo_text",
                )
            )
            sink(
                RuntimeEvent(
                    kind="tool_finished",
                    message="ok",
                    tool_name="mcp__docs__echo_text",
                )
            )
            return "echo:hello"

        try:
            with patch.object(session, "ask", side_effect=fake_ask):
                handled, output = _handle_repl_command(session, '/mcp-verify docs echo_text {"text":"hello"}')
            self.assertTrue(handled)
            assert output is not None
            self.assertIn("ok: yes", output)
            self.assertIn("source: ok", output)
            self.assertIn("tool_called: yes", output)
            self.assertIn("echo:hello", output)
        finally:
            session.close()

    def test_context_refresh_command_reloads_project_context(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_cli_context_refresh"
        _cleanup_dir(cwd)
        cwd.mkdir(parents=True)
        (cwd / "CLAUDE.md").write_text("Memory text", encoding="utf-8")
        session = Session(SessionConfig(cwd=cwd, interactive=False))

        try:
            handled, output = _handle_repl_command(session, "/context-refresh")
            self.assertTrue(handled)
            assert output is not None
            self.assertIn("memory=loaded", output)
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_review_command_returns_prompt_execution(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))

        try:
            handled, output = _handle_repl_command(session, "/review 42")
            self.assertTrue(handled)
            self.assertIsInstance(output, CommandExecution)
            assert isinstance(output, CommandExecution)
            self.assertIn("PR number or selector: 42", output.prompt)
        finally:
            session.close()

    def test_security_review_command_returns_prompt_execution(self) -> None:
        cwd = _make_tmp_dir("cli_security_review")
        session = Session(SessionConfig(cwd=cwd, interactive=False))

        try:
            with patch(
                "claudecode_py.commands.prompt_commands._run_shell_capture",
                side_effect=["status", "files", "commits", "diff"],
            ):
                handled, output = _handle_repl_command(session, "/security-review")
            self.assertTrue(handled)
            self.assertIsInstance(output, CommandExecution)
            assert isinstance(output, CommandExecution)
            self.assertIn("diff", output.prompt)
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_run_repl_executes_prompt_command_via_session(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        execution = CommandExecution(prompt="Review prompt", progress_message="Reviewing")

        try:
            with patch(
                "claudecode_py.cli._handle_repl_command",
                side_effect=[(True, execution), (False, None)],
            ):
                with patch.object(session, "run_command", return_value="done") as run_command:
                    with patch("builtins.input", side_effect=["/review 9", "/exit"]):
                        with redirect_stdout(StringIO()):
                            exit_code = run_repl(session)

            self.assertEqual(exit_code, 0)
            run_command.assert_called_once_with(execution, sink=unittest.mock.ANY)
        finally:
            session.close()

    def test_main_ask_background_launches_worker_and_records_session(self) -> None:
        cwd = _make_tmp_dir("cli_bg_launch")

        class PopenStub:
            pid = 4321

        try:
            with patch("claudecode_py.cli.subprocess.Popen", return_value=PopenStub()):
                with redirect_stdout(StringIO()) as stdout:
                    exit_code = main(["--cwd", str(cwd), "ask", "hello", "--background"])
            self.assertEqual(exit_code, 0)
            self.assertIn("Launched background session", stdout.getvalue())
            with redirect_stdout(StringIO()) as ps_stdout:
                ps_exit = main(["--cwd", str(cwd), "ps"])
            self.assertEqual(ps_exit, 0)
            self.assertIn("status=running", ps_stdout.getvalue())
        finally:
            _cleanup_dir(cwd)

    def test_main_logs_and_attach_render_background_log(self) -> None:
        cwd = _make_tmp_dir("cli_bg_logs")
        record = create_background_session(
            cwd,
            prompt="hello",
            provider="anthropic",
            model="demo",
            status="completed",
        )
        log_path = Path(record.log_path)
        log_path.write_text("line-1\nline-2\n", encoding="utf-8")

        try:
            with redirect_stdout(StringIO()) as stdout:
                logs_exit = main(["--cwd", str(cwd), "logs", record.bg_id])
            self.assertEqual(logs_exit, 0)
            self.assertIn("line-1", stdout.getvalue())
        finally:
            _cleanup_dir(cwd)

    def test_main_attach_reattaches_live_bridge_session(self) -> None:
        cwd = _make_tmp_dir("cli_bg_attach")
        record = create_background_session(
            cwd,
            prompt="hello",
            provider="anthropic",
            model="demo",
            status="running",
        )
        from claudecode_py.service import BridgeTcpServer, ServiceDispatcher

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        server = BridgeTcpServer("127.0.0.1", 0, dispatcher)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            created = dispatcher.handle({"jsonrpc": "2.0", "id": 1, "method": "session.create", "params": {}})
            session_id = created["result"]["session_id"]
            dispatcher._sessions[session_id].session.ask = lambda prompt, sink=None: (sink and sink(RuntimeEvent(kind="assistant_text", message=f"echo:{prompt}"))) or f"echo:{prompt}"  # type: ignore[method-assign]
            host, port = server.server_address
            update_background_session(
                cwd,
                record.bg_id,
                bridge_host=str(host),
                bridge_port=port,
                session_id=session_id,
                status="running",
            )
            with patch("builtins.input", side_effect=["hello", "/exit"]):
                with redirect_stdout(StringIO()) as stdout:
                    exit_code = main(["--cwd", str(cwd), "attach", record.bg_id])
            self.assertEqual(exit_code, 0)
            rendered = stdout.getvalue()
            self.assertIn("PyClaudeCode REPL attached to background session", rendered)
            self.assertIn("echo:hello", rendered)
        finally:
            server.close()
            thread.join(timeout=2)
            _cleanup_dir(cwd)

    def test_main_attach_executes_remote_slash_commands(self) -> None:
        cwd = _make_tmp_dir("cli_bg_attach_command")
        record = create_background_session(
            cwd,
            prompt="hello",
            provider="anthropic",
            model="demo",
            status="running",
        )
        from claudecode_py.service import BridgeTcpServer, ServiceDispatcher

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        server = BridgeTcpServer("127.0.0.1", 0, dispatcher)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            created = dispatcher.handle({"jsonrpc": "2.0", "id": 1, "method": "session.create", "params": {}})
            session_id = created["result"]["session_id"]
            host, port = server.server_address
            update_background_session(
                cwd,
                record.bg_id,
                bridge_host=str(host),
                bridge_port=port,
                session_id=session_id,
                status="running",
            )
            with patch("builtins.input", side_effect=["/config", "/exit"]):
                with redirect_stdout(StringIO()) as stdout:
                    exit_code = main(["--cwd", str(cwd), "attach", record.bg_id])
            self.assertEqual(exit_code, 0)
            rendered = stdout.getvalue()
            self.assertIn("cwd:", rendered)
            self.assertIn("session_id:", rendered)
        finally:
            server.close()
            thread.join(timeout=2)
            _cleanup_dir(cwd)

    def test_main_attach_tui_restores_remote_session(self) -> None:
        cwd = _make_tmp_dir("cli_bg_attach_tui")
        record = create_background_session(
            cwd,
            prompt="hello",
            provider="anthropic",
            model="demo",
            status="running",
        )
        from claudecode_py.service import BridgeTcpServer, ServiceDispatcher

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        server = BridgeTcpServer("127.0.0.1", 0, dispatcher)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            created = dispatcher.handle({"jsonrpc": "2.0", "id": 1, "method": "session.create", "params": {}})
            session_id = created["result"]["session_id"]
            host, port = server.server_address
            update_background_session(
                cwd,
                record.bg_id,
                bridge_host=str(host),
                bridge_port=port,
                session_id=session_id,
                status="running",
            )
            with patch("claudecode_py.cli._launch_tui", return_value=0) as launch_tui:
                exit_code = main(["--cwd", str(cwd), "attach", record.bg_id, "--mode", "tui"])
            self.assertEqual(exit_code, 0)
            remote_session = launch_tui.call_args.args[0]
            self.assertEqual(remote_session.state.session_id, session_id)
            self.assertEqual(remote_session.config.cwd, cwd)
        finally:
            server.close()
            thread.join(timeout=2)
            _cleanup_dir(cwd)

    def test_main_attach_can_resolve_pending_remote_approval(self) -> None:
        cwd = _make_tmp_dir("cli_bg_attach_approval")
        record = create_background_session(
            cwd,
            prompt="hello",
            provider="anthropic",
            model="demo",
            status="running",
        )
        from claudecode_py.service import BridgeTcpServer, ServiceDispatcher

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        server = BridgeTcpServer("127.0.0.1", 0, dispatcher)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            created = dispatcher.handle({"jsonrpc": "2.0", "id": 1, "method": "session.create", "params": {}})
            session_id = created["result"]["session_id"]
            host, port = server.server_address
            update_background_session(
                cwd,
                record.bg_id,
                bridge_host=str(host),
                bridge_port=port,
                session_id=session_id,
                status="running",
            )
            approval_result: dict[str, object] = {}

            def require() -> None:
                approval_result["result"] = dispatcher._sessions[session_id].request_approval(
                    ApprovalRequest(
                        tool_name="bash",
                        reason="Need approval",
                        risk_level="write",
                        approval_key="write",
                        details="preview",
                    )
                )

            approval_thread = threading.Thread(target=require)
            approval_thread.start()
            try:
                time.sleep(0.1)
                with patch("builtins.input", side_effect=["/approve-session", "/exit"]):
                    with redirect_stdout(StringIO()) as stdout:
                        exit_code = main(["--cwd", str(cwd), "attach", record.bg_id])
                self.assertEqual(exit_code, 0)
                rendered = stdout.getvalue()
                self.assertIn("Pending request for bash", rendered)
                self.assertIn("Approved bash (session).", rendered)
            finally:
                approval_thread.join(timeout=2)
            self.assertEqual(getattr(approval_result["result"], "decision", None), "allow")
            self.assertEqual(getattr(approval_result["result"], "scope", None), "session")
        finally:
            server.close()
            thread.join(timeout=2)
            _cleanup_dir(cwd)

    def test_main_attach_can_approve_prompt_started_in_same_repl(self) -> None:
        cwd = _make_tmp_dir("cli_bg_attach_approval_live")
        record = create_background_session(
            cwd,
            prompt="hello",
            provider="anthropic",
            model="demo",
            status="running",
        )
        from claudecode_py.service import BridgeTcpServer, ServiceDispatcher

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        server = BridgeTcpServer("127.0.0.1", 0, dispatcher)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            created = dispatcher.handle({"jsonrpc": "2.0", "id": 1, "method": "session.create", "params": {}})
            session_id = created["result"]["session_id"]
            record_session = dispatcher._sessions[session_id]

            def fake_ask(prompt, sink=None, **kwargs):
                del prompt, kwargs
                result = record_session.request_approval(
                    ApprovalRequest(
                        tool_name="bash",
                        reason="Need approval",
                        risk_level="write",
                        approval_key="write",
                        details="preview",
                    )
                )
                if result.decision == "allow" and sink is not None:
                    sink(RuntimeEvent(kind="assistant_text", message="approved"))
                return "approved"

            record_session.session.ask = fake_ask  # type: ignore[method-assign]
            host, port = server.server_address
            update_background_session(
                cwd,
                record.bg_id,
                bridge_host=str(host),
                bridge_port=port,
                session_id=session_id,
                status="running",
            )
            with patch("builtins.input", side_effect=["run", "/approve", "/exit"]):
                with redirect_stdout(StringIO()) as stdout:
                    exit_code = main(["--cwd", str(cwd), "attach", record.bg_id])
            self.assertEqual(exit_code, 0)
            rendered = stdout.getvalue()
            self.assertIn("requires approval", rendered)
            self.assertIn("Approved bash (once).", rendered)
            self.assertIn("approved", rendered)
        finally:
            server.close()
            thread.join(timeout=2)
            _cleanup_dir(cwd)

    def test_main_kill_updates_background_session(self) -> None:
        cwd = _make_tmp_dir("cli_bg_kill")
        record = create_background_session(
            cwd,
            prompt="hello",
            provider="anthropic",
            model="demo",
            status="running",
        )
        update_background_session(cwd, record.bg_id, pid=999)

        try:
            with patch("claudecode_py.cli._terminate_background_session") as terminate:
                with redirect_stdout(StringIO()) as stdout:
                    exit_code = main(["--cwd", str(cwd), "kill", record.bg_id])
            self.assertEqual(exit_code, 0)
            terminate.assert_called_once()
            self.assertIn("Stopped background session", stdout.getvalue())

            with redirect_stdout(StringIO()) as ps_stdout:
                main(["--cwd", str(cwd), "ps"])
            self.assertIn("status=stopped", ps_stdout.getvalue())
        finally:
            _cleanup_dir(cwd)

    def test_main_background_worker_updates_registry_and_writes_output(self) -> None:
        cwd = _make_tmp_dir("cli_bg_worker")
        record = create_background_session(
            cwd,
            prompt="hello",
            provider="anthropic",
            model="demo",
            status="queued",
        )

        class FakeBridgeServer:
            def __init__(self, host, port, dispatcher):
                del host, port
                self.dispatcher = dispatcher
                self.server_address = ("127.0.0.1", 8765)

            def serve_forever(self):
                time.sleep(0.05)

            def close(self):
                self.dispatcher.close()

        try:
            with patch("claudecode_py.cli.BridgeTcpServer", FakeBridgeServer):
                with patch("claudecode_py.session.Session.ask", return_value="done"):
                    with redirect_stdout(StringIO()) as stdout:
                        exit_code = main(["--cwd", str(cwd), "_bg-runner", "--bg-id", record.bg_id, "hello"])
            self.assertEqual(exit_code, 0)
            self.assertIn("done", stdout.getvalue())
            with redirect_stdout(StringIO()) as ps_stdout:
                main(["--cwd", str(cwd), "ps"])
            rendered = ps_stdout.getvalue()
            self.assertIn("status=running", rendered)
            self.assertIn("workspace=snapshot", rendered)
            self.assertIn("127.0.0.1", str(resolve_background_session(cwd, record.bg_id).bridge_host))
            resolved = resolve_background_session(cwd, record.bg_id)
            assert resolved is not None
            self.assertEqual(resolved.workspace_mode, "snapshot")
            self.assertEqual(Path(resolved.original_cwd), cwd)
        finally:
            _cleanup_dir(cwd)

    def test_skill_commands_enable_and_disable_loaded_skill(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_cli_skill_commands"
        _cleanup_dir(cwd)
        (cwd / ".pyclaude" / "skills").mkdir(parents=True, exist_ok=True)
        (cwd / ".pyclaude" / "skills" / "review.md").write_text(
            "Review carefully.",
            encoding="utf-8",
        )
        session = Session(SessionConfig(cwd=cwd, interactive=False))

        try:
            handled_enable, output_enable = _handle_repl_command(session, "/skills-enable review")
            handled_disable, output_disable = _handle_repl_command(
                session, "/skills-disable review"
            )

            self.assertTrue(handled_enable)
            self.assertTrue(handled_disable)
            self.assertEqual(output_enable, 'Enabled skill "review".')
            self.assertEqual(output_disable, 'Disabled skill "review".')
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_skill_commands_persist_state_to_transcript(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_cli_skill_persist"
        _cleanup_dir(cwd)
        (cwd / ".pyclaude" / "skills").mkdir(parents=True, exist_ok=True)
        (cwd / ".pyclaude" / "skills" / "review.md").write_text(
            "---\n"
            "auto_enable: true\n"
            "---\n\n"
            "Review carefully.",
            encoding="utf-8",
        )
        session = Session(SessionConfig(cwd=cwd, interactive=False))

        try:
            _handle_repl_command(session, "/skills-enable review")
            _handle_repl_command(session, "/skills-disable review")
            restored_state, _ = load_latest_transcript(cwd)

            assert restored_state is not None
            self.assertEqual(restored_state.enabled_skill_names, [])
            self.assertEqual(restored_state.disabled_skill_names, ["review"])
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_skills_reload_command_reloads_skill_files(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_cli_skills_reload"
        _cleanup_dir(cwd)
        (cwd / ".pyclaude" / "skills").mkdir(parents=True, exist_ok=True)
        skill_path = cwd / ".pyclaude" / "skills" / "review.md"
        skill_path.write_text("Original review guidance.", encoding="utf-8")
        session = Session(SessionConfig(cwd=cwd, interactive=False))

        try:
            skill_path.write_text("Updated review guidance.", encoding="utf-8")
            handled, output = _handle_repl_command(session, "/skills-reload")
            self.assertTrue(handled)
            assert output is not None
            self.assertIn("skills=6", output)
            self.assertIn("Updated review guidance.", session.describe_loaded_skills())
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_run_tui_reports_missing_textual_dependency(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))

        try:
            with patch("claudecode_py.cli._launch_tui", side_effect=RuntimeError('Missing dependency "textual". Install with: pip install -e .[tui]')):
                with self.assertRaises(RuntimeError):
                    run_tui(session)
        finally:
            session.close()

    def test_main_dispatches_tui_command(self) -> None:
        with patch("claudecode_py.cli._launch_tui", return_value=0) as launch_tui:
            exit_code = main(["--cwd", str(Path(__file__).resolve().parent), "tui"])

        self.assertEqual(exit_code, 0)
        launch_tui.assert_called_once()

    def test_main_dispatches_ask_command_through_headless_runner(self) -> None:
        with patch("claudecode_py.cli.run_headless") as run_headless:
            run_headless.return_value = type(
                "HeadlessResultLike",
                (),
                {
                    "events": [],
                    "output": "done",
                },
            )()
            with redirect_stdout(StringIO()):
                exit_code = main(["--cwd", str(Path(__file__).resolve().parent), "ask", "hello"])

        self.assertEqual(exit_code, 0)
        run_headless.assert_called_once()

    def test_main_lists_saved_sessions(self) -> None:
        cwd = _make_tmp_dir("cli_sessions")
        save_transcript(
            SessionConfig(cwd=cwd, interactive=False),
            SessionState(
                session_id="session-list",
                messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            ),
        )

        try:
            with redirect_stdout(StringIO()) as stdout:
                exit_code = main(["--cwd", str(cwd), "sessions"])
            self.assertEqual(exit_code, 0)
            self.assertIn("session-list", stdout.getvalue())
        finally:
            _cleanup_dir(cwd)

    def test_main_locate_symbol_prints_json(self) -> None:
        cwd = _make_tmp_dir("cli_locate_symbol")
        (cwd / "demo.py").write_text("def deploy():\n    return 1\n", encoding="utf-8")

        try:
            with redirect_stdout(StringIO()) as stdout:
                exit_code = main(
                    ["--cwd", str(cwd), "locate-symbol", "deploy", "--json"]
                )
            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "symbol_lookup")
            self.assertEqual(payload["payload"]["symbol"], "deploy")
            self.assertEqual(payload["payload"]["matches"][0]["path"], "demo.py")
        finally:
            _cleanup_dir(cwd)

    def test_main_locate_symbol_prints_json_for_js_ts(self) -> None:
        cwd = _make_tmp_dir("cli_locate_symbol_ts")
        (cwd / "ui.ts").write_text("export const deploy = () => 1\n", encoding="utf-8")

        try:
            with redirect_stdout(StringIO()) as stdout:
                exit_code = main(
                    ["--cwd", str(cwd), "locate-symbol", "deploy", "--json"]
                )
            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "symbol_lookup")
            self.assertEqual(payload["payload"]["matches"][0]["path"], "ui.ts")
        finally:
            _cleanup_dir(cwd)

    def test_main_references_prints_json(self) -> None:
        cwd = _make_tmp_dir("cli_references")
        (cwd / "demo.py").write_text(
            "def build():\n"
            "    return 1\n\n"
            "value = build()\n",
            encoding="utf-8",
        )

        try:
            with redirect_stdout(StringIO()) as stdout:
                exit_code = main(
                    ["--cwd", str(cwd), "references", "build", "--scope", "workspace", "--json"]
                )
            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "reference_lookup")
            self.assertEqual(payload["payload"]["symbol"], "build")
            self.assertEqual(payload["payload"]["references"][0]["path"], "demo.py")
        finally:
            _cleanup_dir(cwd)

    def test_main_open_file_prints_json(self) -> None:
        cwd = _make_tmp_dir("cli_open_file")
        (cwd / "demo.py").write_text("value = 1\n", encoding="utf-8")

        try:
            with redirect_stdout(StringIO()) as stdout:
                exit_code = main(
                    [
                        "--cwd",
                        str(cwd),
                        "open-file",
                        "demo.py",
                        "--line",
                        "3",
                        "--column",
                        "2",
                        "--json",
                    ]
                )
            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "editor_target")
            self.assertEqual(payload["payload"]["path"], "demo.py")
            self.assertEqual(payload["payload"]["line"], 3)
            self.assertEqual(payload["payload"]["column"], 2)
        finally:
            _cleanup_dir(cwd)

    def test_main_open_symbol_prints_json(self) -> None:
        cwd = _make_tmp_dir("cli_open_symbol")
        (cwd / "demo.py").write_text("def deploy():\n    return 1\n", encoding="utf-8")

        try:
            with redirect_stdout(StringIO()) as stdout:
                exit_code = main(
                    ["--cwd", str(cwd), "open-symbol", "deploy", "--json"]
                )
            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "editor_target")
            self.assertEqual(payload["payload"]["action"], "open_symbol")
            self.assertEqual(payload["payload"]["path"], "demo.py")
        finally:
            _cleanup_dir(cwd)

    def test_main_diff_targets_prints_json(self) -> None:
        cwd = _make_tmp_dir("cli_diff_targets")
        before_path = cwd / "before.txt"
        after_path = cwd / "after.txt"
        before_path.write_text("line1\nline2\n", encoding="utf-8")
        after_path.write_text("line1\nline2 changed\n", encoding="utf-8")

        try:
            with redirect_stdout(StringIO()) as stdout:
                exit_code = main(
                    [
                        "--cwd",
                        str(cwd),
                        "diff-targets",
                        "demo.py",
                        "--before-file",
                        str(before_path),
                        "--after-file",
                        str(after_path),
                        "--json",
                    ]
                )
            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "diff_targets")
            self.assertEqual(payload["payload"]["path"], "demo.py")
            self.assertGreaterEqual(len(payload["payload"]["hunks"]), 1)
        finally:
            _cleanup_dir(cwd)

    def test_main_reference_targets_prints_json(self) -> None:
        cwd = _make_tmp_dir("cli_reference_targets")
        (cwd / "demo.py").write_text(
            "def build():\n"
            "    return 1\n\n"
            "value = build()\n",
            encoding="utf-8",
        )

        try:
            with redirect_stdout(StringIO()) as stdout:
                exit_code = main(
                    [
                        "--cwd",
                        str(cwd),
                        "reference-targets",
                        "build",
                        "--scope",
                        "workspace",
                        "--json",
                    ]
                )
            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "reference_targets")
            self.assertEqual(payload["payload"]["symbol"], "build")
            self.assertEqual(payload["payload"]["targets"][0]["path"], "demo.py")
        finally:
            _cleanup_dir(cwd)

    def test_main_symbol_actions_prints_json(self) -> None:
        cwd = _make_tmp_dir("cli_symbol_actions")
        (cwd / "demo.py").write_text(
            "def build():\n"
            "    return 1\n\n"
            "value = build()\n",
            encoding="utf-8",
        )

        try:
            with redirect_stdout(StringIO()) as stdout:
                exit_code = main(
                    [
                        "--cwd",
                        str(cwd),
                        "symbol-actions",
                        "build",
                        "--json",
                    ]
                )
            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "symbol_actions")
            self.assertEqual(payload["payload"]["symbol"], "build")
            self.assertEqual(payload["payload"]["definitions"][0]["path"], "demo.py")
            self.assertEqual(payload["payload"]["references"][0]["path"], "demo.py")
        finally:
            _cleanup_dir(cwd)

    def test_main_mcp_call_prints_json(self) -> None:
        cwd = _make_tmp_dir("cli_mcp_call")
        server_script = Path(__file__).resolve().parent / "fixtures" / "fake_mcp_server.py"
        config_path = cwd / ".pyclaude" / "mcp_servers.json"
        (cwd / ".pyclaude").mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(
                {
                    "servers": [
                        {
                            "name": "fake",
                            "transport": "stdio",
                            "command": sys.executable,
                            "args": [str(server_script)],
                        }
                    ]
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )

        try:
            with redirect_stdout(StringIO()) as stdout:
                exit_code = main(
                    [
                        "--cwd",
                        str(cwd),
                        "mcp-call",
                        "fake",
                        "echo_text",
                        "--args",
                        '{"text":"hello"}',
                        "--json",
                    ]
                )
            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["server_name"], "fake")
            self.assertEqual(payload["tool_name"], "echo_text")
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["source"], "ok")
            self.assertIn("hello", payload["result_text"])
        finally:
            _cleanup_dir(cwd)

    def test_main_mcp_verify_prints_json(self) -> None:
        verification = McpVerificationResult(
            server_name="fake",
            tool_name="echo_text",
            mapped_tool_name="mcp__fake__echo_text",
            ok=False,
            source="model",
            output_text="guessed",
            error_text="Model did not invoke the required MCP tool.",
            tool_called=False,
        )
        with patch("claudecode_py.cli.Session.verify_mcp_tool_via_model", return_value=verification):
            with redirect_stdout(StringIO()) as stdout:
                exit_code = main(
                    [
                        "--cwd",
                        str(Path(__file__).resolve().parent),
                        "mcp-verify",
                        "fake",
                        "echo_text",
                        "--args",
                        "{}",
                        "--json",
                    ]
                )
        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["server_name"], "fake")
        self.assertEqual(payload["source"], "model")
        self.assertFalse(payload["ok"])

    def test_main_repl_can_resume_specific_session(self) -> None:
        cwd = _make_tmp_dir("cli_resume_session")
        save_transcript(
            SessionConfig(cwd=cwd, interactive=False),
            SessionState(
                session_id="resume-me",
                messages=[{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
            ),
        )

        try:
            with patch("claudecode_py.cli.run_repl", return_value=0) as run_repl:
                exit_code = main(
                    ["--cwd", str(cwd), "--resume-session", "resume-me", "repl"]
                )
            self.assertEqual(exit_code, 0)
            session_arg = run_repl.call_args.args[0]
            self.assertEqual(session_arg.state.session_id, "resume-me")
            self.assertEqual(session_arg.state.messages[0]["content"][0]["text"], "hello")
        finally:
            _cleanup_dir(cwd)


if __name__ == "__main__":
    unittest.main()
