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
from claudecode_py.permission_config import load_permission_rules
from claudecode_py.permissions import ApprovalRequest, ApprovalResult, PermissionDeniedError, PermissionManager
from claudecode_py.runtime.events import RuntimeEvent
from claudecode_py.session import Session
from claudecode_py.state import (
    AdvisorReviewSummary,
    HistoryBoundary,
    PlanningArtifact,
    SessionState,
    WorkspaceChangeSet,
    WorkspaceFileChange,
)
from claudecode_py.storage.background_sessions import (
    create_background_session,
    get_background_session_path,
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
        raise AssertionError(f"unexpected method: {method}")

    def close(self) -> None:
        return None


class CliCommandTests(unittest.TestCase):
    def test_parser_accepts_tui_command(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["tui"])

        self.assertEqual(args.command, "tui")

    def test_parser_accepts_permission_config_argument(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["--permission-config", "custom.json", "repl"])

        self.assertEqual(args.permission_config, "custom.json")

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

    def test_history_config_model_commands_support_slices(self) -> None:
        cwd = _make_tmp_dir("cli_history_config_model_slices")
        session = Session(SessionConfig(cwd=cwd, interactive=False, provider="openai-compatible", model="gpt-test"))
        try:
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

            handled_history, output_history = _handle_repl_command(session, "/history changes")
            handled_config, output_config = _handle_repl_command(session, "/config workspace")
            handled_model, output_model = _handle_repl_command(session, "/model advisor")
            handled_invalid, output_invalid = _handle_repl_command(session, "/history nope")

            self.assertTrue(handled_history)
            self.assertTrue(handled_config)
            self.assertTrue(handled_model)
            self.assertTrue(handled_invalid)
            assert output_history is not None
            assert output_config is not None
            assert output_model is not None
            assert output_invalid is not None
            self.assertIn("recent changes:", output_history)
            self.assertIn("Working set:", output_history)
            self.assertIn("workspace state:", output_config)
            self.assertIn("primary action:", output_config)
            self.assertIn("advisor_relationship:", output_model)
            self.assertIn("runtime model: gpt-test", output_model)
            self.assertEqual(output_invalid, "Usage: /history [all|messages|tasks|workspace|changes]")
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_compact_command_supports_status_preview_and_apply(self) -> None:
        cwd = _make_tmp_dir("cli_compact_surface")
        session = Session(
            SessionConfig(
                cwd=cwd,
                interactive=False,
                max_history_messages=4,
                history_keep_last_messages=2,
            )
        )
        try:
            session.state.context_summary = "Earlier summary"
            session.state.messages = [
                {"role": "user", "content": [{"type": "text", "text": "one"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "two"}]},
                {"role": "user", "content": [{"type": "text", "text": "three"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "four"}]},
            ]

            handled_status, output_status = _handle_repl_command(session, "/compact status")
            handled_preview, output_preview = _handle_repl_command(session, "/compact preview")
            handled_preview_instruction, output_preview_instruction = _handle_repl_command(
                session,
                "/compact preview keep only decisions and pending TODOs",
            )
            handled_apply, output_apply = _handle_repl_command(
                session,
                "/compact keep only decisions and pending TODOs",
            )
            handled_invalid, output_invalid = _handle_repl_command(session, "/compact status nope")

            self.assertTrue(handled_status)
            self.assertTrue(handled_preview)
            self.assertTrue(handled_preview_instruction)
            self.assertTrue(handled_apply)
            self.assertTrue(handled_invalid)
            assert output_status is not None
            assert output_preview is not None
            assert output_preview_instruction is not None
            assert output_apply is not None
            assert output_invalid is not None
            self.assertIn("history compaction status:", output_status)
            self.assertIn("compaction mode: local estimated summary", output_status)
            self.assertIn("would compact: yes", output_status)
            self.assertIn("boundary kind: compact", output_status)
            self.assertIn("history compaction preview:", output_preview)
            self.assertIn("- 1. user: one", output_preview)
            self.assertIn("compact instruction: keep only decisions and pending TODOs", output_preview_instruction)
            self.assertIn("history compacted:", output_apply)
            self.assertIn("compact instruction: keep only decisions and pending TODOs", output_apply)
            self.assertIn("boundary kind: compact", output_apply)
            self.assertEqual(len(session.state.messages), 2)
            self.assertIsNotNone(session.state.context_summary)
            self.assertEqual(
                output_invalid,
                "Usage: /compact [status|preview [instructions...]|<instructions...>]",
            )
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_rewind_command_lists_shows_and_applies_boundaries(self) -> None:
        cwd = _make_tmp_dir("cli_rewind_surface")
        session = Session(
            SessionConfig(
                cwd=cwd,
                interactive=False,
                max_history_messages=4,
                history_keep_last_messages=2,
            )
        )
        try:
            session.state.context_summary = "Earlier summary"
            session.state.messages = [
                {"role": "user", "content": [{"type": "text", "text": "one"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "two"}]},
                {"role": "user", "content": [{"type": "text", "text": "three"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "four"}]},
            ]
            _handle_repl_command(session, "/compact keep only decisions")

            handled_list, output_list = _handle_repl_command(session, "/rewind")
            handled_show, output_show = _handle_repl_command(session, "/rewind show 1")
            handled_apply, output_apply = _handle_repl_command(session, "/rewind apply 1")
            handled_invalid, output_invalid = _handle_repl_command(session, "/rewind nope")

            self.assertTrue(handled_list)
            self.assertTrue(handled_show)
            self.assertTrue(handled_apply)
            self.assertTrue(handled_invalid)
            assert output_list is not None
            assert output_show is not None
            assert output_apply is not None
            assert output_invalid is not None
            self.assertIn("rewind boundaries:", output_list)
            self.assertIn("snapshot_messages=4", output_list)
            self.assertIn("recommended flow:", output_list)
            self.assertIn("rewind boundary:", output_show)
            self.assertIn("kind: compact", output_show)
            self.assertIn("boundary kind: compact boundary", output_show)
            self.assertIn("timeline compare:", output_show)
            self.assertIn("pre-compact restore point", output_show)
            self.assertIn("restore effect:", output_show)
            self.assertIn("conversation rewound:", output_apply)
            self.assertIn("target boundary kind: compact", output_apply)
            self.assertEqual(len(session.state.messages), 4)
            self.assertEqual(
                output_invalid,
                "Usage: /rewind [list|show <n|boundary-id>|apply <n|boundary-id>]",
            )
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_status_command_supports_summary_workspace_workflow_and_resume(self) -> None:
        cwd = _make_tmp_dir("cli_status_surface")
        session = Session(SessionConfig(cwd=cwd, interactive=False, provider="openai-compatible", model="gpt-test"))
        try:
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
                "Inspect runtime flow",
                workspace_planned_paths=["runtime/session.py"],
            )
            session.record_planning_artifact(
                PlanningArtifact(
                    kind="ultraplan",
                    goal="map runtime",
                    summary="summary",
                    task_ids=[task.id],
                    used_read_only_subagents=True,
                )
            )

            handled_default, output_default = _handle_repl_command(session, "/status")
            handled_workspace, output_workspace = _handle_repl_command(session, "/status workspace")
            handled_workflow, output_workflow = _handle_repl_command(session, "/status workflow")
            handled_resume, output_resume = _handle_repl_command(session, "/status resume")
            handled_invalid, output_invalid = _handle_repl_command(session, "/status nope")

            self.assertTrue(handled_default)
            self.assertTrue(handled_workspace)
            self.assertTrue(handled_workflow)
            self.assertTrue(handled_resume)
            self.assertTrue(handled_invalid)
            assert output_default is not None
            assert output_workspace is not None
            assert output_workflow is not None
            assert output_resume is not None
            assert output_invalid is not None
            self.assertIn("session identity:", output_default)
            self.assertIn("workspace state:", output_default)
            self.assertIn("next actions:", output_default)
            self.assertIn("Current workspace", output_workspace)
            self.assertIn("session identity:", output_workflow)
            self.assertIn("Working set", output_workflow)
            self.assertIn("next_actions:", output_workflow)
            self.assertIn("go_to_change: /changes show", output_workflow)
            self.assertIn("go_to_task: /task show " + task.id, output_workflow)
            self.assertIn("go_to_plan: /plan", output_workflow)
            self.assertIn("stay_on_surface: /status workflow | /files focused | /diff focused", output_workflow)
            self.assertIn("session identity:", output_resume)
            self.assertIn("continuation category: saved resumable", output_resume)
            self.assertIn("memory preservation semantics:", output_resume)
            self.assertIn("- operation: resume", output_resume)
            self.assertIn("go_to_saved_resume: pyclaude --resume-session", output_resume)
            self.assertEqual(output_invalid, "Usage: /status [summary|workspace|workflow|resume]")
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_sessions_command_supports_show_summary_and_workspace(self) -> None:
        cwd = _make_tmp_dir("cli_sessions_show_detail")
        missing_cwd = cwd / ".pyclaude" / "workspaces" / "missing-agent"
        try:
            config = SessionConfig(cwd=cwd, interactive=False)
            save_transcript(
                config,
                SessionState(
                    session_id="session-demo",
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
                    history_boundaries=[
                        HistoryBoundary(
                            kind="compact",
                            trigger="manual",
                            summary="Compacted older turns",
                            compaction_mode="local_estimated_summary",
                            compacted_count=3,
                            kept_count=2,
                            context_summary_chars_after=120,
                        )
                    ],
                    messages=[{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
                ),
            )
            session = Session(config)
            try:
                handled_summary, output_summary = _handle_repl_command(session, "/sessions show latest summary")
                handled_workspace, output_workspace = _handle_repl_command(session, "/sessions show session-demo workspace")
                handled_detail, output_detail = _handle_repl_command(session, "/sessions show session-demo")

                self.assertTrue(handled_summary)
                self.assertTrue(handled_workspace)
                self.assertTrue(handled_detail)
                assert output_summary is not None
                assert output_workspace is not None
                assert output_detail is not None
                self.assertIn("saved session:", output_summary)
                self.assertIn("continuation category: saved resumable", output_summary)
                self.assertIn("active message history: yes", output_summary)
                self.assertIn("compacted context summary: no", output_summary)
                self.assertIn("history lifecycle:", output_summary)
                self.assertIn("- history boundaries: 1", output_summary)
                self.assertIn("- latest boundary: compact boundary", output_summary)
                self.assertIn("- latest rewindable boundary: none", output_summary)
                self.assertIn("- latest compact trigger: manual", output_summary)
                self.assertIn("next actions:", output_summary)
                self.assertIn("saved session workspace:", output_workspace)
                self.assertIn("workspace state: mode=snapshot health=unavailable", output_workspace)
                self.assertIn("workspace anomaly:", output_workspace)
                self.assertIn("- workspace anomaly: unavailable, cleanup failed, fallback active", output_workspace)
                self.assertIn("workspace recovery:", output_workspace)
                self.assertIn("primary action:", output_workspace)
                self.assertIn("resume path: pyclaude --resume-session session-demo repl", output_detail)
                self.assertIn("go_to_saved_resume: pyclaude --resume-session session-demo repl", output_detail)
                self.assertIn("workspace state: mode=snapshot health=unavailable", output_detail)
                self.assertIn("advisor activity: 1 review(s)", output_detail)
                self.assertIn("history lifecycle:", output_detail)
                self.assertIn("history boundaries:", output_detail)
                self.assertIn("compact boundary | trigger=manual", output_detail)
                self.assertIn("- latest compact trigger: manual", output_detail)
            finally:
                session.close()
        finally:
            _cleanup_dir(cwd)

    def test_permissions_command_adds_and_lists_session_rule(self) -> None:
        cwd = _make_tmp_dir("cli_permissions_list")
        session = Session(SessionConfig(cwd=cwd, interactive=False))

        try:
            handled_add, output_add = _handle_repl_command(session, "/permissions allow tool bash")
            handled_list, output_list = _handle_repl_command(session, "/permissions list")

            self.assertTrue(handled_add)
            self.assertTrue(handled_list)
            self.assertEqual(output_add, "Added session permission rule: allow:tool:bash")
            assert output_list is not None
            self.assertIn("session_rules: 1", output_list)
            self.assertIn("session:1 allow:tool:bash", output_list)
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_permissions_command_save_writes_workspace_config(self) -> None:
        cwd = _make_tmp_dir("cli_permissions_save")
        session = Session(SessionConfig(cwd=cwd, interactive=False))

        try:
            _handle_repl_command(session, "/permissions allow tool bash")
            handled_save, output_save = _handle_repl_command(session, "/permissions save")
            loaded = load_permission_rules(cwd)

            self.assertTrue(handled_save)
            assert output_save is not None
            self.assertIn(".pyclaude", output_save)
            self.assertEqual([rule.describe() for rule in loaded], ["allow:tool:bash"])

            handled_list, output_list = _handle_repl_command(session, "/permissions list")
            self.assertTrue(handled_list)
            assert output_list is not None
            self.assertIn("workspace_rules: 1", output_list)
            self.assertIn("session_rules: 0", output_list)

            reloaded = Session(SessionConfig(cwd=cwd, interactive=False))
            try:
                self.assertEqual(
                    [rule.describe() for rule in reloaded.permission_manager.workspace_rules],
                    ["allow:tool:bash"],
                )
            finally:
                reloaded.close()
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_permissions_command_export_writes_custom_path_without_clearing_session_rules(self) -> None:
        cwd = _make_tmp_dir("cli_permissions_export")
        session = Session(SessionConfig(cwd=cwd, interactive=False))

        try:
            _handle_repl_command(session, "/permissions allow tool bash")
            export_path = "exports/permissions-copy.json"
            handled_export, output_export = _handle_repl_command(
                session,
                f"/permissions export {export_path}",
            )

            self.assertTrue(handled_export)
            assert output_export is not None
            self.assertIn("permissions-copy.json", output_export)
            exported = load_permission_rules(cwd, config_path=(cwd / export_path).resolve())
            self.assertEqual([rule.describe() for rule in exported], ["allow:tool:bash"])
            self.assertEqual(
                [rule.describe() for rule in session.permission_manager.session_rules],
                ["allow:tool:bash"],
            )
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_workspaces_command_lists_orphans_and_cleanup_is_dry_run(self) -> None:
        cwd = _make_tmp_dir("cli_workspaces_cleanup")
        orphan_dir = cwd / ".pyclaude" / "workspaces" / "orphan-agent"
        orphan_dir.mkdir(parents=True, exist_ok=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))

        try:
            handled_list, output_list = _handle_repl_command(session, "/workspaces list")
            handled_cleanup, output_cleanup = _handle_repl_command(session, "/workspaces cleanup")

            self.assertTrue(handled_list)
            self.assertTrue(handled_cleanup)
            assert output_list is not None
            assert output_cleanup is not None
            self.assertIn("orphaned_isolated_workspaces: 1", output_list)
            self.assertIn("workspace=snapshot health=orphaned label=orphan-agent", output_list)
            self.assertIn(f"origin={cwd.resolve()}", output_list)
            self.assertIn("cleanup=none", output_list)
            self.assertIn("dry_run: yes", output_cleanup)
            self.assertIn("planned_deletions: 1", output_cleanup)
            self.assertIn("cleanup planned | Would delete 1 orphaned isolated workspace(s).", output_cleanup)
            self.assertIn("deleted: 0", output_cleanup)
            self.assertTrue(orphan_dir.exists())
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_workspaces_current_and_show_render_detailed_inventory(self) -> None:
        cwd = _make_tmp_dir("cli_workspaces_detail")
        workspace_dir = cwd / ".pyclaude" / "workspaces" / "detail-agent"
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

        try:
            handled_current, output_current = _handle_repl_command(session, "/workspaces current")
            handled_show, output_show = _handle_repl_command(session, "/workspaces show detail-agent")

            self.assertTrue(handled_current)
            self.assertTrue(handled_show)
            assert output_current is not None
            assert output_show is not None
            self.assertIn("Current workspace", output_current)
            self.assertIn("workspace state:", output_current)
            self.assertIn("workspace anomaly:", output_current)
            self.assertIn("workspace recovery:", output_current)
            self.assertIn("next actions:", output_current)
            self.assertIn("mode: snapshot", output_current)
            self.assertIn("health: unavailable", output_current)
            self.assertIn("workspace anomaly: unavailable, cleanup pending, fallback active", output_current)
            self.assertIn(
                "workspace_recommended_actions: /workspaces list, /workspaces repair workspace-detail-session, /workspaces cleanup",
                output_current,
            )
            self.assertIn("fallback_cwd:", output_current)
            self.assertIn("unavailable_reason: Workspace missing on disk.", output_current)
            self.assertIn("primary action: workspace_repair workspace-detail-session", output_current)
            self.assertIn("secondary action: workspace_cleanup_preview", output_current)
            self.assertIn("tertiary action: /workspaces list", output_current)

            self.assertIn("Isolated workspace detail", output_show)
            self.assertIn("selected: detail-agent", output_show)
            self.assertIn("matched_workspaces: 1", output_show)
            self.assertIn("workspace anomaly:", output_show)
            self.assertIn("workspace recovery:", output_show)
            self.assertIn("label: detail-agent", output_show)
            self.assertIn("session_ids: workspace-detail-session", output_show)
            self.assertIn("primary action: workspace_repair workspace-detail-session", output_show)
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_workspaces_cleanup_apply_deletes_after_approval(self) -> None:
        cwd = _make_tmp_dir("cli_workspaces_cleanup_apply")
        orphan_dir = cwd / ".pyclaude" / "workspaces" / "orphan-agent"
        orphan_dir.mkdir(parents=True, exist_ok=True)
        seen_requests: list[ApprovalRequest] = []

        def allow_handler(request: ApprovalRequest) -> ApprovalResult:
            seen_requests.append(request)
            return ApprovalResult(decision="allow", scope="once")

        session = Session(
            SessionConfig(cwd=cwd, interactive=False),
            permission_manager=PermissionManager(interactive=True, approval_handler=allow_handler),
        )

        try:
            handled, output = _handle_repl_command(session, "/workspaces cleanup apply orphan-agent")

            self.assertTrue(handled)
            assert output is not None
            self.assertEqual(len(seen_requests), 1)
            self.assertEqual(seen_requests[0].tool_name, "workspace_cleanup")
            self.assertEqual(seen_requests[0].risk_level, "delete")
            self.assertIn(".pyclaude/workspaces/orphan-agent", seen_requests[0].target_paths[0].replace("\\", "/"))
            self.assertIn("dry_run: no", output)
            self.assertIn("cleanup planned | Planned 1 orphaned isolated workspace deletion(s).", output)
            self.assertIn("deleted: 1", output)
            self.assertIn("cleanup applied | Deleted 1 orphaned isolated workspace(s).", output)
            self.assertFalse(orphan_dir.exists())
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_workspaces_cleanup_apply_requires_approval_in_non_interactive_mode(self) -> None:
        cwd = _make_tmp_dir("cli_workspaces_cleanup_denied")
        orphan_dir = cwd / ".pyclaude" / "worktrees" / "orphan-agent"
        orphan_dir.mkdir(parents=True, exist_ok=True)
        session = Session(
            SessionConfig(cwd=cwd, interactive=False),
            permission_manager=PermissionManager(interactive=False),
        )

        try:
            with self.assertRaises(PermissionDeniedError):
                _handle_repl_command(session, "/workspaces cleanup apply orphan-agent")
            self.assertTrue(orphan_dir.exists())
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_workspaces_repair_recreates_missing_snapshot_workspace(self) -> None:
        cwd = _make_tmp_dir("cli_workspaces_repair_snapshot")
        missing_dir = cwd / ".pyclaude" / "workspaces" / "missing-agent"
        (cwd / "demo.txt").write_text("hello\n", encoding="utf-8")
        session = Session(
            SessionConfig(cwd=cwd, interactive=False),
            state=SessionState(
                session_id="repair-cli",
                original_cwd=str(cwd.resolve()),
                effective_cwd=str(missing_dir.resolve()),
                workspace_mode="snapshot",
                workspace_label="missing-agent",
                workspace_cleanup_status="pending",
                workspace_unavailable=True,
                workspace_unavailable_reason="Isolated workspace is unavailable: expected missing snapshot.",
                workspace_fallback_cwd=str(cwd.resolve()),
            ),
        )

        try:
            handled, output = _handle_repl_command(session, "/workspaces repair missing-agent")

            self.assertTrue(handled)
            assert output is not None
            self.assertIn("planned_repairs: 1", output)
            self.assertIn("repaired: 1", output)
            self.assertIn("repair planned | Planned 1 isolated workspace repair(s).", output)
            self.assertTrue(Path(session.state.effective_cwd or "").exists())
            self.assertEqual(session.state.workspace_health, "cleanup_pending")
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_workspaces_repair_reports_origin_unavailable_with_stable_diagnostic(self) -> None:
        cwd = _make_tmp_dir("cli_workspaces_repair_missing_origin")
        missing_origin = cwd / "missing-origin"
        missing_workspace = cwd / ".pyclaude" / "workspaces" / "missing-agent"
        session = Session(
            SessionConfig(cwd=cwd, interactive=False),
            state=SessionState(
                session_id="repair-missing-origin",
                original_cwd=str(missing_origin.resolve()),
                effective_cwd=str(missing_workspace.resolve()),
                workspace_mode="snapshot",
                workspace_label="missing-agent",
                workspace_cleanup_status="pending",
                workspace_unavailable=True,
                workspace_unavailable_reason="Isolated workspace is unavailable: expected missing snapshot.",
                workspace_fallback_cwd=str(cwd.resolve()),
            ),
        )

        try:
            handled, output = _handle_repl_command(session, "/workspaces repair missing-agent")

            self.assertTrue(handled)
            assert output is not None
            self.assertIn("planned_repairs: 1", output)
            self.assertIn("repaired: 0", output)
            self.assertIn("repair failed | Failed to repair 1 isolated workspace(s).", output)
            self.assertIn("repair failed: origin unavailable", output)
            self.assertEqual(session.state.workspace_health, "unavailable")
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_session_restores_session_permission_rules_from_transcript(self) -> None:
        cwd = _make_tmp_dir("cli_permissions_restore")
        session = Session(SessionConfig(cwd=cwd, interactive=False))

        try:
            _handle_repl_command(session, "/permissions allow shell git status")
            restored_state, _ = load_latest_transcript(cwd)
            assert restored_state is not None

            restored = Session(SessionConfig(cwd=cwd, interactive=False), state=restored_state)
            try:
                self.assertEqual(
                    [rule.describe() for rule in restored.permission_manager.session_rules],
                    ["allow:shell:git status"],
                )
            finally:
                restored.close()
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_main_repl_honors_permission_config_argument(self) -> None:
        cwd = _make_tmp_dir("cli_permissions_arg")
        custom_path = cwd / "config" / "permissions-alt.json"
        custom_path.parent.mkdir(parents=True, exist_ok=True)
        custom_path.write_text(
            json.dumps(
                {
                    "rules": [
                        {"decision": "allow", "scope": "tool", "value": "bash"},
                    ]
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )

        try:
            with patch("claudecode_py.cli.run_repl", return_value=0) as run_repl:
                exit_code = main(
                    [
                        "--cwd",
                        str(cwd),
                        "--permission-config",
                        str(custom_path),
                        "repl",
                    ]
                )
            self.assertEqual(exit_code, 0)
            session_arg = run_repl.call_args.args[0]
            self.assertEqual(session_arg.config.permission_config_path, custom_path.resolve())
            self.assertEqual(
                [rule.describe() for rule in session_arg.permission_manager.workspace_rules],
                ["allow:tool:bash"],
            )
        finally:
            _cleanup_dir(cwd)

    def test_task_command_returns_task_detail(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        task = session.task_manager.create(
            "agent",
            "Implement runtime changes",
            task_role="execution",
            plan_execution_phase="running",
        )
        session.task_manager.set_progress(task.id, "Inspect runtime flow")
        session.task_manager.complete(task.id, "session.py\nquery_loop.py")

        handled, output = _handle_repl_command(session, f"/task show {task.id[:6]}")

        self.assertTrue(handled)
        assert output is not None
        self.assertIn(f"task_id: {task.id}", output)
        self.assertIn("status: completed", output)
        self.assertIn("output:", output)

    def test_task_command_supports_file_focus_and_invalid_selector(self) -> None:
        cwd = _make_tmp_dir("cli_task_file_focus")
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
        task = session.task_manager.create(
            "agent",
            "Inspect runtime flow",
            workspace_planned_paths=["a.py", "b.py"],
        )

        try:
            handled_focus, output_focus = _handle_repl_command(session, f"/task show {task.id[:6]} file 2")
            handled_invalid, output_invalid = _handle_repl_command(session, f"/task show {task.id[:6]} file 9")

            self.assertTrue(handled_focus)
            self.assertTrue(handled_invalid)
            assert output_focus is not None
            assert output_invalid is not None
            self.assertIn("focused file:", output_focus)
            self.assertIn("- focused file: b.py", output_focus)
            self.assertIn("- related change:", output_focus)
            self.assertIn("- next_actions:", output_focus)
            self.assertIn("go_to_change: /changes show", output_focus)
            self.assertIn("go_to_plan: none", output_focus)
            self.assertIn("stay_on_surface: /task show " + task.id, output_focus)
            self.assertIn("/changes show", output_focus)
            self.assertEqual(
                output_invalid,
                "Usage: /task show <id> [file <n>] | /task advisor <id> | /task drift <id>",
            )
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_task_and_changes_commands_preserve_focus_across_detail_views(self) -> None:
        cwd = _make_tmp_dir("cli_task_change_focus_preserve")
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
        task = session.task_manager.create(
            "agent",
            "Inspect runtime flow",
            workspace_planned_paths=["a.py", "b.py"],
        )

        try:
            _handled_change_focus, _output_change_focus = _handle_repl_command(session, "/changes show 1 file 2")
            handled_task_show, output_task_show = _handle_repl_command(session, f"/task show {task.id[:6]}")
            _handled_task_focus, _output_task_focus = _handle_repl_command(session, f"/task show {task.id[:6]} file 2")
            handled_change_show, output_change_show = _handle_repl_command(session, "/changes show 1")

            self.assertTrue(handled_task_show)
            self.assertTrue(handled_change_show)
            assert output_task_show is not None
            assert output_change_show is not None
            self.assertIn("- focused file: b.py", output_task_show)
            self.assertIn("stay_on_surface: /task show " + task.id + " file 2", output_task_show)
            self.assertIn("> 2. updated b.py", output_change_show)
            self.assertIn("inspect_focused_file: /files show 2", output_change_show)
            self.assertIn("inspect_task: /task show " + task.id + " file 2", output_change_show)
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_task_command_supports_advisor_and_drift_detail(self) -> None:
        cwd = _make_tmp_dir("cli_task_advisor_drift_file_context")
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        try:
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
                    advisor_status="block",
                    advisor_reason="Need a safer runtime-only pass first.",
                    advisor_risk_flags=["unsafe-write"],
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
                drift_status="block",
                drift_reason="Need a narrower runtime-only pass.",
                constraint_source="plan_drift_block",
                background_session_id="bg-123",
                background_reverse_hint="pyclaude ps bg-123 | pyclaude logs bg-123 summary",
            )
            session.state.constraint_reason = "Need a safer read-only pass first."
            session.state.last_plan_drift_context = "pending_tools: apply_patch"
            session.state.advisor_last_result = AdvisorReviewSummary(
                checkpoint="plan_drift",
                status="block",
                reason="Stay in runtime/session scope.",
                risk_flags=["plan-drift"],
            )

            handled_advisor, output_advisor = _handle_repl_command(session, f"/task advisor {task.id[:6]}")
            handled_drift, output_drift = _handle_repl_command(session, f"/task drift {task.id[:6]}")

            self.assertTrue(handled_advisor)
            self.assertTrue(handled_drift)
            assert output_advisor is not None
            assert output_drift is not None
            self.assertIn("advisor_review:", output_advisor)
            self.assertIn(f"artifact_id: {artifact.artifact_id}", output_advisor)
            self.assertIn("focused file:", output_advisor)
            self.assertIn("go_to_change: /changes show", output_advisor)
            self.assertIn("go_to_plan: /plan execution 1 file 1 | /plan execution | /plan advisor", output_advisor)
            self.assertIn("stay_on_surface: /task show " + task.id + " file 1", output_advisor)
            self.assertIn("/task advisor " + task.id, output_advisor)
            self.assertIn("background_linkage:", output_advisor)
            self.assertIn("- background_session_id: bg-123", output_advisor)
            self.assertIn("- background_reverse_hint: pyclaude ps bg-123 | pyclaude logs bg-123 summary", output_advisor)
            self.assertIn("drift_detail:", output_drift)
            self.assertIn("constraint_source: plan_drift_block", output_drift)
            self.assertIn("pending_tools: apply_patch", output_drift)
            self.assertIn("focused file:", output_drift)
            self.assertIn("go_to_change: /changes show", output_drift)
            self.assertIn("go_to_plan: /plan execution 1 file 1 | /plan execution | /plan advisor", output_drift)
            self.assertIn("stay_on_surface: /task show " + task.id + " file 1", output_drift)
            self.assertIn("/task drift " + task.id, output_drift)
            self.assertIn("background_linkage:", output_drift)
            self.assertIn("- background_session_id: bg-123", output_drift)
            self.assertIn("- background_reverse_hint: pyclaude ps bg-123 | pyclaude logs bg-123 summary", output_drift)
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_plan_commands_preserve_focus_across_plan_and_advisor_views(self) -> None:
        cwd = _make_tmp_dir("cli_plan_focus_preserve")
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
        execution = session.task_manager.create(
            "agent",
            "Implement runtime changes",
            task_role="execution",
            active_plan_id=artifact.artifact_id,
            active_plan_goal=artifact.goal,
            workspace_planned_paths=["a.py", "b.py"],
        )
        artifact.task_ids.append(execution.id)

        try:
            _handled_change_focus, _output_change_focus = _handle_repl_command(session, "/changes show 1 file 2")
            handled_plan, output_plan = _handle_repl_command(session, "/plan")
            handled_advisor, output_advisor = _handle_repl_command(session, "/plan advisor")

            self.assertTrue(handled_plan)
            self.assertTrue(handled_advisor)
            assert output_plan is not None
            assert output_advisor is not None
            self.assertIn("- focused file: b.py", output_plan)
            self.assertIn("- focused file: b.py", output_advisor)
            self.assertIn("go_to_plan: /plan file 2", output_advisor)
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_tasks_command_supports_filters_and_show_alias(self) -> None:
        cwd = _make_tmp_dir("cli_tasks_workflow_filters")
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        try:
            session.record_workspace_change(
                tool_name="apply_patch",
                summary="Update runtime file",
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
            change_task = session.task_manager.create("agent", "Inspect runtime change")
            context_task = session.task_manager.create(
                "agent",
                "Review context file",
                workspace_planned_paths=["docs/context.md"],
            )
            background_task = session.task_manager.create(
                "agent",
                "Background runtime review",
                task_role="background",
                background_session_id="bg-123",
                background_reverse_hint="pyclaude ps bg-123 | pyclaude logs bg-123 summary",
            )
            completed_task = session.task_manager.create("agent", "Done task")
            session.task_manager.complete(completed_task.id, "done")
            checklist = session.create_checklist_task(
                subject="Review runtime",
                description="Review current task flow",
                active_form="Reviewing runtime",
                status="pending",
            )

            handled_default, output_default = _handle_repl_command(session, "/tasks")
            handled_active, output_active = _handle_repl_command(session, "/tasks active")
            handled_changes, output_changes = _handle_repl_command(session, "/tasks changes")
            handled_context, output_context = _handle_repl_command(session, "/tasks context")
            handled_show, output_show = _handle_repl_command(session, f"/tasks show {change_task.id[:6]}")
            _handled_task_show, output_task_show = _handle_repl_command(session, f"/task show {change_task.id[:6]}")
            handled_invalid, output_invalid = _handle_repl_command(session, "/tasks nope")

            self.assertTrue(handled_default)
            self.assertTrue(handled_active)
            self.assertTrue(handled_changes)
            self.assertTrue(handled_context)
            self.assertTrue(handled_show)
            self.assertTrue(handled_invalid)
            assert output_default is not None
            assert output_active is not None
            assert output_changes is not None
            assert output_context is not None
            assert output_show is not None
            assert output_task_show is not None
            assert output_invalid is not None

            self.assertIn("task workflow overview:", output_default)
            self.assertIn("focused file: runtime/session.py", output_default)
            self.assertIn("related change:", output_default)
            self.assertIn("next_actions:", output_default)
            self.assertIn("go_to_task: /task show", output_default)
            self.assertIn("go_to_change: /changes show", output_default)
            self.assertIn("filter: active", output_active)
            self.assertIn(change_task.id, output_active)
            self.assertIn(context_task.id, output_active)
            self.assertIn(background_task.id, output_active)
            self.assertIn(str(checklist["id"]), output_active)
            self.assertNotIn(completed_task.id, output_active)
            self.assertIn("background session: bg-123", output_active)
            self.assertIn("background reverse hint: pyclaude ps bg-123 | pyclaude logs bg-123 summary", output_active)
            self.assertIn("filter: changes", output_changes)
            self.assertIn(change_task.id, output_changes)
            self.assertNotIn(context_task.id, output_changes)
            self.assertIn("filter: context", output_context)
            self.assertIn(context_task.id, output_context)
            self.assertIn("context-only: yes", output_context)
            self.assertEqual(output_show, output_task_show)
            self.assertEqual(output_invalid, "Usage: /tasks [list|active|changes|context|show <id>]")
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_plan_command_supports_file_focus_and_invalid_selector(self) -> None:
        cwd = _make_tmp_dir("cli_plan_file_focus")
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

        try:
            handled_focus, output_focus = _handle_repl_command(session, "/plan file 2")
            handled_invalid, output_invalid = _handle_repl_command(session, "/plan file 9")

            self.assertTrue(handled_focus)
            self.assertTrue(handled_invalid)
            assert output_focus is not None
            assert output_invalid is not None
            self.assertIn("focused file:", output_focus)
            self.assertIn("- focused file: notes.md", output_focus)
            self.assertIn("- context-only: yes", output_focus)
            self.assertEqual(output_invalid, "Usage: /plan file <n>")
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_plan_child_views_support_selector_and_file_focus(self) -> None:
        cwd = _make_tmp_dir("cli_plan_child_file_focus")
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

        try:
            handled_scout_focus, output_scout_focus = _handle_repl_command(session, "/plan scouts 1 file 2")
            handled_scout_invalid, output_scout_invalid = _handle_repl_command(session, "/plan scouts 1 file 9")
            handled_execution_focus, output_execution_focus = _handle_repl_command(session, "/plan execution 1 file 2")
            handled_execution_invalid, output_execution_invalid = _handle_repl_command(session, "/plan execution 1 file 9")

            self.assertTrue(handled_scout_focus)
            self.assertTrue(handled_scout_invalid)
            self.assertTrue(handled_execution_focus)
            self.assertTrue(handled_execution_invalid)
            assert output_scout_focus is not None
            assert output_scout_invalid is not None
            assert output_execution_focus is not None
            assert output_execution_invalid is not None
            self.assertIn("selected scout focused file:", output_scout_focus)
            self.assertIn("- focused file: notes.md", output_scout_focus)
            self.assertIn("- context-only: yes", output_scout_focus)
            self.assertEqual(output_scout_invalid, "Usage: /plan scouts [<n> [file <m>]]")
            self.assertIn("selected execution focused file:", output_execution_focus)
            self.assertIn("- focused file: notes.md", output_execution_focus)
            self.assertIn("- context-only: yes", output_execution_focus)
            self.assertEqual(output_execution_invalid, "Usage: /plan execution [<n> [file <m>]]")
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_plugins_command_returns_plugin_summary(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))

        handled, output = _handle_repl_command(session, "/plugins")

        self.assertTrue(handled)
        assert output is not None
        self.assertIn("plugin registry:", output)
        self.assertIn("registered plugins:", output)
        self.assertIn("review: plugin_status=enabled plugin_source=builtin", output)
        self.assertIn("commit: plugin_status=enabled plugin_source=builtin", output)
        self.assertIn("plugin diagnostics:", output)

    def test_plugin_command_returns_plugin_detail(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))

        handled, output = _handle_repl_command(session, "/plugin review")

        self.assertTrue(handled)
        assert output is not None
        self.assertIn("name: review", output)
        self.assertIn("plugin_id: review@builtin", output)
        self.assertIn("plugin source: builtin", output)
        self.assertIn("plugin contributions: commands", output)
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

    def test_plan_timeline_command_returns_timeline_summary(self) -> None:
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
            plan_status="on-plan",
        )
        session.task_manager.set_progress(execution.id, "Inspect runtime flow")
        session.state.advisor_last_result = AdvisorReviewSummary(
            checkpoint="plan_drift",
            status="revise",
            reason="Stay in runtime/session scope.",
        )
        session.state.last_plan_drift_status = "revise"
        session.state.last_plan_drift_context = "pending_tools: apply_patch"

        handled, output = _handle_repl_command(session, "/plan timeline")

        self.assertTrue(handled)
        assert output is not None
        self.assertIn("audit_summary:", output)
        self.assertIn("session_span:", output)
        self.assertIn("session_duration:", output)
        self.assertIn("timeline:", output)
        self.assertIn("[plan]", output)
        self.assertIn("[scout]", output)
        self.assertIn("[execution]", output)
        self.assertIn("[advisor]", output)
        self.assertIn("[drift]", output)

    def test_plan_timeline_command_supports_kind_filter(self) -> None:
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

        handled, output = _handle_repl_command(session, "/plan timeline execution")

        self.assertTrue(handled)
        assert output is not None
        self.assertIn("timeline_filter: execution", output)
        self.assertIn("audit_summary:", output)
        self.assertIn("task_count:", output)
        self.assertIn("[Execution Loop] entries=", output)
        self.assertIn("[execution]", output)
        self.assertNotIn("[scout]", output)

    def test_plan_timeline_command_supports_delta_and_focus_modes(self) -> None:
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

        handled, output = _handle_repl_command(
            session,
            f"/plan timeline execution delta=after-drift focus=task:{execution.id}",
        )

        self.assertTrue(handled)
        assert output is not None
        self.assertIn("timeline_filter: execution", output)
        self.assertIn("timeline_delta: after-drift", output)
        self.assertIn(f"timeline_focus: task:{execution.id}", output)
        self.assertIn(execution.id, output)

    def test_plan_timeline_command_supports_compare_mode(self) -> None:
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
            plan_status="on-plan",
        )
        session.task_manager.set_progress(execution.id, "Inspect runtime flow")

        handled, output = _handle_repl_command(
            session,
            "/plan timeline all compare=active-vs-previous",
        )

        self.assertTrue(handled)
        assert output is not None
        self.assertIn("timeline_compare: active-vs-previous", output)
        self.assertIn("compare_lens:", output)
        self.assertIn("> active:", output)
        self.assertIn("selected_timeline_compare_primary_action:", output)
        self.assertIn("phase:Plan Setup", output)

    def test_plan_timeline_command_supports_phase_filter(self) -> None:
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

        handled, output = _handle_repl_command(
            session,
            "/plan timeline all phase=execution-loop",
        )

        self.assertTrue(handled)
        assert output is not None
        self.assertIn("timeline_phase: execution-loop", output)
        self.assertIn("[Execution Loop]", output)
        self.assertNotIn("[Plan Setup]", output)

    def test_plan_audit_command_supports_previous_artifact(self) -> None:
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
                used_read_only_subagents=True,
            )
        )
        previous = session.active_planning_artifact()
        assert previous is not None
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="map runtime",
                summary="Current Architecture\n- runtime session",
                supersedes_artifact_id=previous.artifact_id,
                used_read_only_subagents=True,
            )
        )

        handled, output = _handle_repl_command(session, "/plan audit artifact=previous")

        self.assertTrue(handled)
        assert output is not None
        self.assertIn("lineage_audit_summary:", output)
        self.assertIn(f"selected_audit_artifact_id: {previous.artifact_id}", output)
        self.assertIn("selected_audit_goal: previous plan", output)

    def test_plan_timeline_command_supports_phase_local_compare(self) -> None:
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
        session.record_plan_drift_context("pending_tools: apply_patch")

        handled, output = _handle_repl_command(
            session,
            "/plan timeline all phase=execution-loop compare=active-vs-previous",
        )

        self.assertTrue(handled)
        assert output is not None
        self.assertIn("timeline_phase: execution-loop", output)
        self.assertIn("timeline_compare: active-vs-previous", output)
        self.assertIn("local:entries", output)
        self.assertIn("local:execution", output)
        self.assertNotIn("phase:Plan Setup", output)

    def test_plan_timeline_command_supports_phase_local_delta_mode(self) -> None:
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
        session.record_plan_drift_context("pending_tools: apply_patch")

        handled, output = _handle_repl_command(
            session,
            "/plan timeline all phase=execution-loop compare=after-drift-vs-all",
        )

        self.assertTrue(handled)
        assert output is not None
        self.assertIn("timeline_phase: execution-loop", output)
        self.assertIn("local:before-drift", output)
        self.assertIn("local:after-drift", output)
        self.assertIn("local:execution-change", output)

    def test_plan_timeline_command_surfaces_phase_local_audit_summary(self) -> None:
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
        session.record_plan_drift_context("pending_tools: apply_patch")

        handled, output = _handle_repl_command(
            session,
            "/plan timeline all phase=execution-loop",
        )

        self.assertTrue(handled)
        assert output is not None
        self.assertIn("phase_local_audit_summary:", output)
        self.assertIn("- phase: execution-loop", output)
        self.assertIn("- before_drift:", output)
        self.assertIn("- after_drift:", output)
        self.assertIn("- change_summary:", output)
        self.assertIn("- execution_task_ids: ", output)
        self.assertIn(execution.id, output)
        self.assertIn("- execution_task_actions: ", output)
        self.assertIn(f"{execution.id}=/task show {execution.id}", output)
        self.assertIn("- selected_phase_local_task_id: " + execution.id, output)
        self.assertIn("- selected_phase_local_task_position: 1/1", output)
        self.assertIn("- selected_phase_local_task_action: /task show " + execution.id, output)
        self.assertIn("- recent_drift_linked_task: " + execution.id, output)
        self.assertIn("- recent_drift_linked_task_action: /task drift " + execution.id, output)

    def test_plan_replay_command_supports_latest_and_at(self) -> None:
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
        scout = session.task_manager.create(
            "ultraplan_scout",
            "Scout architecture",
            planner_kind="ultraplan",
            task_role="scout",
            active_plan_id=artifact.artifact_id,
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
            plan_status="on-plan",
        )
        session.task_manager.complete(execution.id, "Inspect query_loop.py and session.py.")

        handled_latest, output_latest = _handle_repl_command(session, "/plan replay latest")
        handled_first, output_first = _handle_repl_command(session, "/plan replay at=1")

        self.assertTrue(handled_latest)
        self.assertTrue(handled_first)
        assert output_latest is not None
        assert output_first is not None
        self.assertIn("replay_source: timeline-entry", output_latest)
        self.assertIn("- kind: execution", output_latest)
        self.assertIn("replay_cursor: 3/3", output_latest)
        self.assertIn("replay_cursor: 1/3", output_first)
        self.assertIn("- kind: plan", output_first)

    def test_plan_replay_command_supports_compare_mode(self) -> None:
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

        handled, output = _handle_repl_command(
            session,
            "/plan replay compare=execution-vs-scout",
        )

        self.assertTrue(handled)
        assert output is not None
        self.assertIn("replay_source: compare-item", output)
        self.assertIn("replay_source_context: compare:execution", output)
        self.assertIn("selected_replay_entry:", output)

    def test_plan_replay_command_surfaces_lineage_replay_compare(self) -> None:
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
        session.task_manager.complete(execution.id, "Inspect query_loop.py and session.py.")

        handled, output = _handle_repl_command(
            session,
            "/plan replay compare=active-vs-previous",
        )

        self.assertTrue(handled)
        assert output is not None
        self.assertIn("replay_compare: active-vs-previous", output)
        self.assertIn("lineage_replay_compare:", output)
        self.assertIn(f"- current_artifact: {artifact.artifact_id} goal=current plan", output)
        self.assertIn(f"- previous_artifact: {previous.artifact_id} goal=previous plan", output)
        self.assertIn(f"- compare_current_replay: /plan replay latest all artifact={artifact.artifact_id}", output)
        self.assertIn(f"- compare_previous_replay: /plan replay latest all artifact={previous.artifact_id}", output)
        self.assertIn(f"- added_execution_tasks: {execution.id}", output)
        self.assertIn(f"- removed_scout_tasks: {previous_scout.id}", output)
        self.assertIn("- entry_delta_scout:", output)
        self.assertIn(f"actions=/task show {previous_scout.id} | /plan scouts", output)
        self.assertIn("- entry_delta_execution:", output)
        self.assertIn(f"actions=/task show {execution.id} | /task advisor {execution.id}", output)
        self.assertIn("- phase_entry_delta:Scout Research: added=0 removed=1", output)
        self.assertIn("- phase_entry_delta:Execution Loop: added=1 removed=0", output)

    def test_plan_replay_command_supports_previous_artifact(self) -> None:
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

        handled, output = _handle_repl_command(
            session,
            "/plan replay latest artifact=previous",
        )

        self.assertTrue(handled)
        assert output is not None
        self.assertIn("artifact_id: " + previous.artifact_id, output)
        self.assertIn("goal: previous plan", output)
        self.assertIn("- kind: scout", output)
        self.assertIn("selected_replay_primary_action: /task show " + previous_scout.id, output)

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
                task_ids=[task.id],
                advisor_status="approve",
            )
        )
        session.record_plan_drift_context(
            "active_plan_goal: second goal\n"
            "candidate_work_summary:\n"
            "focus query loop and session integration\n"
            "pending_tools: write_file"
        )
        current_artifact = session.active_planning_artifact()
        assert current_artifact is not None
        execution_task = session.task_manager.create(
            "agent",
            "Implement runtime changes",
            task_role="execution",
            active_plan_id=current_artifact.artifact_id,
            active_plan_goal=current_artifact.goal,
            plan_execution_mode="background_agent",
            plan_execution_phase="running",
            plan_status="on-plan",
        )
        session.task_manager.set_progress(execution_task.id, "Inspect runtime flow")
        older = session.planning_artifacts()[0]

        try:
            handled_show, output_show = _handle_repl_command(session, "/plan")
            handled_list, output_list = _handle_repl_command(session, "/plan list")
            handled_scouts, output_scouts = _handle_repl_command(session, "/plan scouts")
            handled_execution, output_execution = _handle_repl_command(session, "/plan execution")
            handled_advisor, output_advisor = _handle_repl_command(session, "/plan advisor")
            handled_lineage, output_lineage = _handle_repl_command(session, "/plan lineage")
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
            self.assertTrue(handled_scouts)
            self.assertTrue(handled_execution)
            self.assertTrue(handled_advisor)
            self.assertTrue(handled_lineage)
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
            self.assertIn("scout_outputs:", output_scouts or "")
            self.assertIn("Inspect session.py and runtime/context.py.", output_scouts or "")
            self.assertIn("selected_scout_summary:", output_scouts or "")
            self.assertIn("next_actions:", output_scouts or "")
            self.assertIn("go_to_task: /task show", output_scouts or "")
            self.assertIn("stay_on_surface: /plan scouts", output_scouts or "")
            self.assertIn("execution_tasks:", output_execution or "")
            self.assertIn("selected_execution_summary:", output_execution or "")
            self.assertIn("selected_execution_detail:", output_execution or "")
            self.assertIn("Implement runtime changes", output_execution or "")
            self.assertIn("phase: running", output_execution or "")
            self.assertIn("Inspect runtime flow", output_execution or "")
            self.assertIn("selected_execution_plan_advisor_action: /plan advisor", output_execution or "")
            self.assertIn("selected_execution_advisor_status_action: /advisor status", output_execution or "")
            self.assertIn("next_actions:", output_execution or "")
            self.assertIn("go_to_task: /task show", output_execution or "")
            self.assertIn("stay_on_surface: /plan execution | /plan advisor | /advisor status", output_execution or "")
            self.assertIn("advisor_review:", output_advisor or "")
            self.assertIn("lineage:", output_lineage or "")
            self.assertIn("current", output_lineage or "")
            self.assertIn("comparisons:", output_lineage or "")
            self.assertIn("next_actions:", output_lineage or "")
            self.assertIn("selected: /plan derive", output_lineage or "")
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

    def test_plan_scouts_and_lineage_require_active_plan(self) -> None:
        cwd = _make_tmp_dir("cli_plan_empty_views")
        session = Session(SessionConfig(cwd=cwd, interactive=False))

        try:
            handled_scouts, output_scouts = _handle_repl_command(session, "/plan scouts")
            handled_execution, output_execution = _handle_repl_command(session, "/plan execution")
            handled_advisor, output_advisor = _handle_repl_command(session, "/plan advisor")
            handled_lineage, output_lineage = _handle_repl_command(session, "/plan lineage")

            self.assertTrue(handled_scouts)
            self.assertTrue(handled_execution)
            self.assertTrue(handled_advisor)
            self.assertTrue(handled_lineage)
            self.assertEqual(output_scouts, "No active planning artifact for /plan scouts.")
            self.assertEqual(output_execution, "No active planning artifact for /plan execution.")
            self.assertEqual(output_advisor, "No active planning artifact for advisor detail.")
            self.assertEqual(output_lineage, "No active planning artifact for /plan lineage.")
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
        assert output is not None
        self.assertIn("Cleared conversation history only for this session.", output)
        self.assertIn("- task/plan/file focus: preserved", output)
        self.assertIn("- advisor review state: preserved", output)
        self.assertEqual(session.state.messages, [])
        self.assertIsNone(session.state.context_summary)

    def test_clear_command_supports_changes_symbol_plan_and_session(self) -> None:
        cwd = _make_tmp_dir("cli_clear_scoped")
        session = Session(
            SessionConfig(cwd=cwd, interactive=False),
            state=SessionState(
                context_summary="Earlier conversation summary",
                messages=[{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
            ),
        )
        try:
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
            session.describe_symbol_action_surface("Session")

            handled_changes, output_changes = _handle_repl_command(session, "/clear changes")
            handled_symbol, output_symbol = _handle_repl_command(session, "/clear symbol")
            handled_plan, output_plan = _handle_repl_command(session, "/clear plan")

            session.record_workspace_change(
                tool_name="apply_patch",
                summary="Update runtime flow again",
                file_changes=[
                    WorkspaceFileChange(
                        path="runtime/query_loop.py",
                        existed_before=True,
                        before_content="old\n",
                        after_content="new\n",
                        action_kind="update",
                    )
                ],
            )
            session.describe_symbol_action_surface("Session")
            previous_session_id = session.state.session_id
            handled_session, output_session = _handle_repl_command(session, "/clear session")
            handled_invalid, output_invalid = _handle_repl_command(session, "/clear nope")

            self.assertTrue(handled_changes)
            self.assertTrue(handled_symbol)
            self.assertTrue(handled_plan)
            self.assertTrue(handled_session)
            self.assertTrue(handled_invalid)
            self.assertEqual(output_changes, "Cleared recorded workspace changes for this session.")
            self.assertEqual(output_symbol, "Cleared active symbol surface.")
            self.assertIn("Cleared active planning artifact", output_plan)
            self.assertIn("Started a fresh local session.", output_session)
            self.assertIn("old_session_id:", output_session)
            self.assertIn("new_session_id:", output_session)
            self.assertEqual(output_invalid, "Usage: /clear [history|changes|symbol|plan|session]")
            self.assertNotEqual(session.state.session_id, previous_session_id)
            self.assertEqual(session.state.messages, [])
            self.assertEqual(session.state.recent_change_sets, [])
            self.assertIsNone(session.current_symbol_surface_payload())
        finally:
            session.close()
            _cleanup_dir(cwd)

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
            self.assertIn("next_actions:", output_changes)
            self.assertIn("go_to_change: /changes show", output_changes)
            self.assertIn("stay_on_surface: /changes show", output_changes)
            self.assertIn("/changes list", output_changes)
            self.assertIn("Undid 1 change(s).", output_undo)
            self.assertFalse((cwd / "demo.txt").exists())
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_changes_command_supports_stack_filters_show_and_working_set(self) -> None:
        cwd = _make_tmp_dir("cli_changes_detail")
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
        change_id = session.state.recent_change_sets[0].change_id[:8]
        session.undo_last_change()

        try:
            handled_undo, output_undo = _handle_repl_command(session, "/changes undo")
            handled_redo, output_redo = _handle_repl_command(session, "/changes redo")
            handled_show, output_show = _handle_repl_command(session, f"/changes show redo {change_id} file 2")
            handled_working_set, output_working_set = _handle_repl_command(session, "/changes working-set")

            self.assertTrue(handled_undo)
            self.assertTrue(handled_redo)
            self.assertTrue(handled_show)
            self.assertTrue(handled_working_set)
            assert output_undo is not None
            assert output_redo is not None
            assert output_show is not None
            assert output_working_set is not None
            self.assertEqual(output_undo, "No recorded workspace changes.")
            self.assertIn("Redo stack:", output_redo)
            self.assertNotIn("Working set:", output_redo)
            self.assertIn("change:", output_show)
            self.assertIn("Focused file (2/2)", output_show)
            self.assertIn("primary_path: b.py", output_show)
            self.assertIn("Working set:", output_working_set)
            self.assertIn("- file_count: 0", output_working_set)
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_changes_show_accepts_numeric_selector_and_reports_invalid_selector_with_usage(self) -> None:
        cwd = _make_tmp_dir("cli_changes_selector")
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        session.record_workspace_change(
            tool_name="apply_patch",
            summary="Created first file",
            file_changes=[
                WorkspaceFileChange(
                    path="first.txt",
                    existed_before=False,
                    before_content="",
                    after_content="one\n",
                    action_kind="create",
                )
            ],
        )
        session.record_workspace_change(
            tool_name="apply_patch",
            summary="Created second file",
            file_changes=[
                WorkspaceFileChange(
                    path="second.txt",
                    existed_before=False,
                    before_content="",
                    after_content="two\n",
                    action_kind="create",
                )
            ],
        )

        try:
            handled_show, output_show = _handle_repl_command(session, "/changes show 2")
            handled_invalid, output_invalid = _handle_repl_command(session, "/changes show 9")

            self.assertTrue(handled_show)
            self.assertTrue(handled_invalid)
            assert output_show is not None
            assert output_invalid is not None
            self.assertIn("summary: Created first file", output_show)
            self.assertIn("Focused file (1/1)", output_show)
            self.assertEqual(
                output_invalid,
                "Usage: /changes [list|undo|redo|show <index-or-change-id>|show redo <index-or-change-id>|show <index-or-change-id> file <n>|show redo <index-or-change-id> file <n>|working-set]",
            )
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_changes_show_renders_related_task_and_plan_action_groups(self) -> None:
        cwd = _make_tmp_dir("cli_changes_action_groups")
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

        try:
            handled_show, output_show = _handle_repl_command(session, "/changes show 1")

            self.assertTrue(handled_show)
            assert output_show is not None
            self.assertIn("next_actions:", output_show)
            self.assertIn("inspect_focused_file: /files show 1", output_show)
            self.assertIn("inspect_focused_diff: /changes show ", output_show)
            self.assertIn("inspect_task: /task show " + task.id, output_show)
            self.assertIn("/task show " + execution.id, output_show)
            self.assertIn("inspect_active_plan: /plan file 1", output_show)
            self.assertIn("/plan execution 1 file 1", output_show)
            self.assertIn("stay_on_surface: /changes show ", output_show)
            self.assertIn("/changes working-set", output_show)
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_context_command_supports_summary_files_focus_and_filters(self) -> None:
        cwd = _make_tmp_dir("cli_context_surface")
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

        try:
            handled_summary, output_summary = _handle_repl_command(session, "/context")
            handled_files, output_files = _handle_repl_command(session, "/context files")
            handled_task_focus, _output_task_focus = _handle_repl_command(session, f"/task show {task.id} file 2")
            handled_focused, output_focused = _handle_repl_command(session, "/context focused")
            handled_changes, output_changes = _handle_repl_command(session, "/context changes")
            handled_plan, output_plan = _handle_repl_command(session, "/context plan")
            handled_invalid, output_invalid = _handle_repl_command(session, "/context invalid")

            self.assertTrue(handled_summary)
            self.assertTrue(handled_files)
            self.assertTrue(handled_task_focus)
            self.assertTrue(handled_focused)
            self.assertTrue(handled_changes)
            self.assertTrue(handled_plan)
            self.assertTrue(handled_invalid)
            assert output_summary is not None
            assert output_files is not None
            assert output_focused is not None
            assert output_changes is not None
            assert output_plan is not None
            assert output_invalid is not None
            self.assertIn("## Context Usage", output_summary)
            self.assertIn("model:", output_summary)
            self.assertIn("estimated tokens:", output_summary)
            self.assertIn("percentage:", output_summary)
            self.assertIn("| Base instructions |", output_summary)
            self.assertIn("| Default tools |", output_summary)
            self.assertIn("/context now shows current context usage.", output_files)
            self.assertIn("Use /files context", output_files)
            self.assertIn("Use /files focused", output_focused)
            self.assertIn("Use /files changes", output_changes)
            self.assertIn("Use /files plan", output_plan)
            self.assertEqual(output_invalid, "Usage: /context [summary]")
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_add_dir_and_context_explicit_auto_commands(self) -> None:
        cwd = _make_tmp_dir("cli_add_dir_context")
        (cwd / "src").mkdir(parents=True, exist_ok=True)
        (cwd / "src" / "app.py").write_text("print('app')\n", encoding="utf-8")
        (cwd / "src" / "unused.py").write_text("print('unused')\n", encoding="utf-8")
        (cwd / "notes.md").write_text("notes\n", encoding="utf-8")
        (cwd / "todo.md").write_text("todo\n", encoding="utf-8")
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

        try:
            handled_add_dir, output_add_dir = _handle_repl_command(session, "/add-dir src")
            handled_add_file, output_add_file = _handle_repl_command(session, "/add-dir notes.md")
            handled_list, output_list = _handle_repl_command(session, "/add-dir list")
            handled_explicit, output_explicit = _handle_repl_command(session, "/files explicit")
            handled_auto, output_auto = _handle_repl_command(session, "/files auto")
            handled_files, output_files = _handle_repl_command(session, "/files")
            handled_remove, output_remove = _handle_repl_command(session, "/add-dir remove 2")
            handled_clear, output_clear = _handle_repl_command(session, "/add-dir clear")
            handled_invalid, output_invalid = _handle_repl_command(session, "/add-dir remove nope")

            self.assertTrue(handled_add_dir)
            self.assertTrue(handled_add_file)
            self.assertTrue(handled_list)
            self.assertTrue(handled_explicit)
            self.assertTrue(handled_auto)
            self.assertTrue(handled_files)
            self.assertTrue(handled_remove)
            self.assertTrue(handled_clear)
            self.assertTrue(handled_invalid)
            assert output_add_dir is not None
            assert output_add_file is not None
            assert output_list is not None
            assert output_explicit is not None
            assert output_auto is not None
            assert output_files is not None
            assert output_remove is not None
            assert output_clear is not None
            assert output_invalid is not None
            self.assertIn("explicit context path:", output_add_dir)
            self.assertIn("kind: directory", output_add_dir)
            self.assertIn("kind: file", output_add_file)
            self.assertIn("explicit context entries:", output_list)
            self.assertIn("unresolved entry count: 0", output_list)
            self.assertIn("explicit-context-contributed files: 2", output_list)
            self.assertIn("explicit-only files: 0", output_list)
            self.assertIn("automatic-only files: 1", output_list)
            self.assertIn("overlapping files: 2", output_list)
            self.assertIn("contributes_files=1", output_list)
            self.assertIn("inspect_automatic_context=/files auto", output_list)
            self.assertIn("cleanup_explicit_context=/add-dir remove <n> | /add-dir clear", output_list)
            self.assertIn("filter: explicit", output_explicit)
            self.assertIn("explicit-only files: 0", output_explicit)
            self.assertIn("automatic-only files: 1", output_explicit)
            self.assertIn("overlapping files: 2", output_explicit)
            self.assertIn("src/app.py", output_explicit)
            self.assertIn("notes.md", output_explicit)
            self.assertNotIn("todo.md", output_explicit)
            self.assertIn("filter: auto", output_auto)
            self.assertIn("explicit-only files: 0", output_auto)
            self.assertIn("automatic-only files: 1", output_auto)
            self.assertIn("overlapping files: 2", output_auto)
            self.assertIn("todo.md", output_auto)
            self.assertNotIn("src/unused.py", output_auto)
            self.assertIn("inspect_explicit_context=/files context", output_files)
            self.assertIn("Removed explicit context entry:", output_remove)
            self.assertEqual(output_clear, "Cleared explicit context entries for this session.")
            self.assertEqual(output_invalid, "Usage: /add-dir <path>|list|clear|remove <n>")
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_files_and_diff_commands_surface_working_set_and_diff_views(self) -> None:
        cwd = _make_tmp_dir("cli_files_diff")
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

        try:
            handled_files, output_files = _handle_repl_command(session, "/files")
            handled_files_working, output_files_working = _handle_repl_command(session, "/files working-set")
            handled_files_focused, output_files_focused = _handle_repl_command(session, "/files focused")
            handled_files_changes, output_files_changes = _handle_repl_command(session, "/files changes")
            handled_files_tasks, output_files_tasks = _handle_repl_command(session, "/files tasks")
            handled_files_plan, output_files_plan = _handle_repl_command(session, "/files plan")
            handled_files_show, output_files_show = _handle_repl_command(session, "/files show 2")
            handled_diff, output_diff = _handle_repl_command(session, "/diff")
            handled_diff_focused, output_diff_focused = _handle_repl_command(session, "/diff focused")
            handled_diff_working, output_diff_working = _handle_repl_command(session, "/diff working-set")
            handled_diff_change, output_diff_change = _handle_repl_command(session, "/diff change 1 file 1")
            handled_invalid_files, output_invalid_files = _handle_repl_command(session, "/files nope")
            handled_invalid_diff, output_invalid_diff = _handle_repl_command(session, "/diff nope")

            self.assertTrue(handled_files)
            self.assertTrue(handled_files_working)
            self.assertTrue(handled_files_focused)
            self.assertTrue(handled_files_changes)
            self.assertTrue(handled_files_tasks)
            self.assertTrue(handled_files_plan)
            self.assertTrue(handled_files_show)
            self.assertTrue(handled_diff)
            self.assertTrue(handled_diff_focused)
            self.assertTrue(handled_diff_working)
            self.assertTrue(handled_diff_change)
            self.assertTrue(handled_invalid_files)
            self.assertTrue(handled_invalid_diff)
            assert output_files is not None
            assert output_files_working is not None
            assert output_files_focused is not None
            assert output_files_changes is not None
            assert output_files_tasks is not None
            assert output_files_plan is not None
            assert output_files_show is not None
            assert output_diff is not None
            assert output_diff_focused is not None
            assert output_diff_working is not None
            assert output_diff_change is not None
            self.assertIn("working set:", output_files)
            self.assertIn("working set:", output_files_working)
            self.assertIn("inspect_focused_file=/files show 1", output_files)
            self.assertIn("inspect_change=/changes show", output_files)
            self.assertIn("focused file:", output_files_focused)
            self.assertIn("app.py", output_files_changes)
            self.assertNotIn("notes.md", output_files_changes)
            self.assertIn("filter: tasks", output_files_tasks)
            self.assertIn("notes.md", output_files_tasks)
            self.assertIn("filter: plan", output_files_plan)
            self.assertIn("notes.md", output_files_plan)
            self.assertIn("- focused file: notes.md", output_files_show)
            self.assertIn("- inspect_focused_diff: /diff focused", output_files_show)
            self.assertIn("diff summary:", output_diff)
            self.assertIn("working set diff-backed files: 1", output_diff)
            self.assertIn("focused file:", output_diff_focused)
            self.assertIn("- inspect_focused_file: /files focused", output_diff_focused)
            self.assertIn("working set diff:", output_diff_working)
            self.assertIn("change:", output_diff_change)
            self.assertEqual(
                output_invalid_files,
                "Usage: /files [context|working-set|focused|changes|tasks|plan|explicit|auto|show <n>]",
            )
            self.assertEqual(
                output_invalid_diff,
                "Usage: /diff [summary|focused|working-set|change <index-or-change-id> [file <n>]]",
            )
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
            self.assertIn("resources=1", output)
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
            self.assertIn("resources=1", output)
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
            self.assertIn("continuation=inactive only", ps_stdout.getvalue())
            self.assertIn("workspace=main", ps_stdout.getvalue())
            self.assertIn("cleanup=none", ps_stdout.getvalue())
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
            rendered = stdout.getvalue()
            self.assertIn("background session:", rendered)
            self.assertIn("continuation category: inactive only", rendered)
            self.assertIn("line-1", stdout.getvalue())
        finally:
            _cleanup_dir(cwd)

    def test_main_logs_summary_renders_header_only(self) -> None:
        cwd = _make_tmp_dir("cli_bg_logs_summary")
        record = create_background_session(
            cwd,
            prompt="hello",
            provider="anthropic",
            model="demo",
            status="completed",
        )
        Path(record.log_path).write_text("line-1\nline-2\n", encoding="utf-8")

        try:
            with redirect_stdout(StringIO()) as stdout:
                logs_exit = main(["--cwd", str(cwd), "logs", record.bg_id, "summary"])
            self.assertEqual(logs_exit, 0)
            rendered = stdout.getvalue()
            self.assertIn("background session:", rendered)
            self.assertIn("stay_on_surface: pyclaude ps | pyclaude logs", rendered)
            self.assertNotIn("line-1", rendered)
        finally:
            _cleanup_dir(cwd)

    def test_main_ps_detail_renders_selected_background_session(self) -> None:
        cwd = _make_tmp_dir("cli_bg_ps_detail")
        record = create_background_session(
            cwd,
            prompt="review pending changes",
            provider="anthropic",
            model="demo",
            status="failed",
        )
        update_background_session(
            cwd,
            record.bg_id,
            pid=1234,
            effective_cwd=str((cwd / ".pyclaude" / "worktrees" / "bg-demo").resolve()),
            workspace_mode="worktree",
            workspace_label="bg-demo",
            workspace_cleanup_status="failed",
            workspace_cleanup_error="PermissionError: cleanup blocked",
        )

        try:
            with redirect_stdout(StringIO()) as stdout:
                exit_code = main(["--cwd", str(cwd), "ps", record.bg_id])
            self.assertEqual(exit_code, 0)
            rendered = stdout.getvalue()
            self.assertIn("background session:", rendered)
            self.assertIn(f"background session id: {record.bg_id}", rendered)
            self.assertIn("prompt: review pending changes", rendered)
            self.assertIn("workspace mode: worktree", rendered)
            self.assertIn("workspace cleanup status: failed", rendered)
            self.assertIn("background workflow:", rendered)
            self.assertIn("current workflow: inactive background record for inspection", rendered)
            self.assertIn("next_actions:", rendered)
            self.assertIn(f"go_to_logs: pyclaude logs {record.bg_id} summary | pyclaude logs {record.bg_id}", rendered)
            self.assertIn("go_to_sessions_show: pyclaude sessions --limit 10", rendered)
            self.assertIn("go_to_live_attach: none", rendered)
            self.assertIn("stay_on_surface: pyclaude ps | pyclaude logs", rendered)
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
            session = dispatcher._sessions[session_id].session
            session.state.original_cwd = str(cwd.resolve())
            session.state.effective_cwd = str((cwd / ".pyclaude" / "worktrees" / "bg-demo").resolve())
            session.state.workspace_mode = "worktree"
            session.state.workspace_label = "bg-demo"
            session.state.workspace_created_at = "2026-05-02T00:00:00+00:00"
            session.state.workspace_cleanup_status = "pending"
            policy = session._compile_turn_command_policy(
                allowed_tool_names=("bash", "read_file"),
                allowed_bash_command_prefixes=("git status",),
                require_read_only_subagents=True,
                command_policy_name="read-only-subagent",
                command_policy_source="background-subagent",
            )
            assert policy is not None
            session.set_session_execution_contract(
                execution_mode="read-only-subagent",
                command_policy=policy,
                active_execution_constraint="read-only",
                constraint_source="session_execution_contract",
                constraint_reason="read-only background agent contract",
            )
            session.record_workspace_change(
                tool_name="apply_patch",
                summary="Update demo.py",
                file_changes=[
                    WorkspaceFileChange(
                        path="demo.py",
                        existed_before=True,
                        before_content="old\n",
                        after_content="new\n",
                        action_kind="update",
                    )
                ],
            )
            dispatcher._sessions[session_id].session.ask = lambda prompt, sink=None: (sink and sink(RuntimeEvent(kind="assistant_text", message=f"echo:{prompt}"))) or f"echo:{prompt}"  # type: ignore[method-assign]
            host, port = server.server_address
            update_background_session(
                cwd,
                record.bg_id,
                bridge_host=str(host),
                bridge_port=port,
                session_id=session_id,
                status="running",
                original_cwd=str(cwd.resolve()),
                effective_cwd=str((cwd / ".pyclaude" / "worktrees" / "bg-demo").resolve()),
                workspace_mode="worktree",
                workspace_label="bg-demo",
                workspace_created_at="2026-05-02T00:00:00+00:00",
                workspace_cleanup_status="pending",
                session_execution_mode=session.state.session_execution_mode,
                session_command_policy_name=session.state.session_command_policy_name,
                session_command_policy_source=session.state.session_command_policy_source,
                session_command_policy_allowed_tool_names=list(session.state.session_command_policy_allowed_tool_names),
                session_command_policy_allowed_bash_prefixes=list(session.state.session_command_policy_allowed_bash_prefixes),
                session_command_policy_require_read_only_subagents=True,
            )
            with patch("builtins.input", side_effect=["hello", "/exit"]):
                with redirect_stdout(StringIO()) as stdout:
                    exit_code = main(["--cwd", str(cwd), "attach", record.bg_id])
            self.assertEqual(exit_code, 0)
            rendered = stdout.getvalue()
            self.assertIn("PyClaudeCode REPL attached to background session", rendered)
            self.assertIn("workspace: workspace=worktree", rendered)
            self.assertIn("execution: execution=read-only-subagent", rendered)
            self.assertIn("policy=read-only-subagent", rendered)
            self.assertIn("read_only_subagents=yes", rendered)
            self.assertIn("label=bg-demo", rendered)
            self.assertIn("cleanup=pending", rendered)
            self.assertIn("session_execution_mode: read-only-subagent", rendered)
            self.assertIn("session_command_policy_name: read-only-subagent", rendered)
            self.assertIn("session_command_policy_allowed_bash_prefixes: git status", rendered)
            self.assertIn("task_surfaces:", rendered)
            self.assertIn("focused_file_context: source=recent_change", rendered)
            self.assertIn("path=demo.py", rendered)
            self.assertIn("primary=open_file demo.py:1", rendered)
            self.assertIn("checklist: 0", rendered)
            self.assertIn("workspace_maintenance: 0", rendered)
            self.assertIn("child_execution: 0", rendered)
            self.assertIn("background_execution: 0", rendered)
            self.assertIn("echo:hello", rendered)
        finally:
            server.close()
            thread.join(timeout=2)
            _cleanup_dir(cwd)

    def test_main_attach_completed_background_session_prints_resume_guidance(self) -> None:
        cwd = _make_tmp_dir("cli_bg_attach_completed")
        record = create_background_session(
            cwd,
            prompt="hello",
            provider="anthropic",
            model="demo",
            status="completed",
        )
        update_background_session(cwd, record.bg_id, session_id="saved-bg-session")

        try:
            with redirect_stdout(StringIO()) as stdout:
                exit_code = main(["--cwd", str(cwd), "attach", record.bg_id])
            self.assertEqual(exit_code, 1)
            rendered = stdout.getvalue()
            self.assertIn("Background session is no longer live", rendered)
            self.assertIn("--resume-session saved-bg-session repl", rendered)
        finally:
            _cleanup_dir(cwd)

    def test_main_ps_and_logs_render_saved_resumable_guidance(self) -> None:
        cwd = _make_tmp_dir("cli_bg_saved_resumable")
        save_transcript(
            SessionConfig(cwd=cwd, interactive=False),
            SessionState(
                session_id="saved-bg-session",
                messages=[{"role": "user", "content": [{"type": "text", "text": "resume"}]}],
            ),
        )
        record = create_background_session(
            cwd,
            prompt="hello",
            provider="anthropic",
            model="demo",
            status="completed",
        )
        update_background_session(cwd, record.bg_id, session_id="saved-bg-session")

        try:
            with redirect_stdout(StringIO()) as ps_stdout:
                ps_exit = main(["--cwd", str(cwd), "ps"])
            self.assertEqual(ps_exit, 0)
            rendered = ps_stdout.getvalue()
            self.assertIn("continuation=saved resumable", rendered)
            self.assertIn("go_to_saved_resume=pyclaude --resume-session saved-bg-session repl", rendered)

            with redirect_stdout(StringIO()) as logs_stdout:
                logs_exit = main(["--cwd", str(cwd), "logs", record.bg_id, "summary"])
            self.assertEqual(logs_exit, 0)
            logs_rendered = logs_stdout.getvalue()
            self.assertIn("continuation category: saved resumable", logs_rendered)
            self.assertIn(
                "go_to_saved_resume: pyclaude --resume-session saved-bg-session repl | pyclaude --resume-session saved-bg-session tui",
                logs_rendered,
            )
        finally:
            _cleanup_dir(cwd)

    def test_main_ps_detail_renders_background_metadata_foundation(self) -> None:
        cwd = _make_tmp_dir("cli_bg_metadata_detail")
        save_transcript(
            SessionConfig(cwd=cwd, interactive=False),
            SessionState(
                session_id="saved-bg-session",
                context_summary="Earlier compacted context",
                messages=[{"role": "user", "content": [{"type": "text", "text": "resume"}]}],
                saved_task_surface_counts={"checklist": 2, "background_execution": 1},
                active_planning_artifact_id="plan-123",
                recent_planning_artifacts=[
                    PlanningArtifact(
                        artifact_id="plan-123",
                        kind="ultraplan",
                        goal="inspect",
                        summary="summary",
                    ),
                ],
            ),
        )
        record = create_background_session(
            cwd,
            prompt="hello",
            provider="anthropic",
            model="demo",
            status="completed",
        )
        update_background_session(cwd, record.bg_id, session_id="saved-bg-session")

        try:
            with redirect_stdout(StringIO()) as stdout:
                exit_code = main(["--cwd", str(cwd), "ps", record.bg_id])
            self.assertEqual(exit_code, 0)
            rendered = stdout.getvalue()
            self.assertIn("background session source: saved_background", rendered)
            self.assertIn("live attachable: no", rendered)
            self.assertIn("saved resumable: yes", rendered)
            self.assertIn("inactive only: no", rendered)
            self.assertIn("primary action: pyclaude --resume-session saved-bg-session repl", rendered)
            self.assertIn(f"secondary action: pyclaude logs {record.bg_id}", rendered)
            self.assertIn("last known message count: 1", rendered)
            self.assertIn("last known context summary chars: 25", rendered)
            self.assertIn("task surfaces: checklist:2,background_execution:1", rendered)
            self.assertIn("has active plan: yes", rendered)
            self.assertIn("active plan id: plan-123", rendered)
            self.assertIn("planning artifact count: 1", rendered)
            self.assertIn("background workflow:", rendered)
            self.assertIn("current workflow: saved background session with resumable transcript", rendered)
            self.assertIn("active plan: plan-123 (ultraplan: inspect)", rendered)
            self.assertIn("working set: 0 file(s)", rendered)
            self.assertLess(
                rendered.index("go_to_saved_resume: pyclaude --resume-session saved-bg-session repl"),
                rendered.index(f"go_to_logs: pyclaude logs {record.bg_id} summary | pyclaude logs {record.bg_id}"),
            )
        finally:
            _cleanup_dir(cwd)

    def test_main_ps_and_logs_summary_share_background_metadata_payload(self) -> None:
        cwd = _make_tmp_dir("cli_bg_metadata_shared")
        save_transcript(
            SessionConfig(cwd=cwd, interactive=False),
            SessionState(
                session_id="saved-bg-session",
                context_summary="compact",
                messages=[{"role": "user", "content": [{"type": "text", "text": "resume"}]}],
            ),
        )
        record = create_background_session(
            cwd,
            prompt="hello",
            provider="anthropic",
            model="demo",
            status="completed",
        )
        update_background_session(cwd, record.bg_id, session_id="saved-bg-session")

        try:
            with redirect_stdout(StringIO()) as ps_stdout:
                ps_exit = main(["--cwd", str(cwd), "ps"])
            self.assertEqual(ps_exit, 0)
            ps_rendered = ps_stdout.getvalue()
            self.assertIn("source=saved_background", ps_rendered)
            self.assertIn("continuation=saved resumable", ps_rendered)
            self.assertIn("live_attachable=no", ps_rendered)
            self.assertIn("saved_resumable=yes", ps_rendered)
            self.assertIn("inactive_only=no", ps_rendered)
            self.assertIn("messages=1", ps_rendered)
            self.assertIn("context_summary_chars=7", ps_rendered)

            with redirect_stdout(StringIO()) as logs_stdout:
                logs_exit = main(["--cwd", str(cwd), "logs", record.bg_id, "summary"])
            self.assertEqual(logs_exit, 0)
            logs_rendered = logs_stdout.getvalue()
            self.assertIn("background session source: saved_background", logs_rendered)
            self.assertIn("continuation category: saved resumable", logs_rendered)
            self.assertIn("live attachable: no", logs_rendered)
            self.assertIn("saved resumable: yes", logs_rendered)
            self.assertIn("inactive only: no", logs_rendered)
            self.assertIn("last known message count: 1", logs_rendered)
            self.assertIn("last known context summary chars: 7", logs_rendered)
            self.assertIn("background workflow:", logs_rendered)
            self.assertIn("current workflow: saved background session with resumable transcript", logs_rendered)
        finally:
            _cleanup_dir(cwd)

    def test_main_ps_detail_live_background_workflow_is_attach_first(self) -> None:
        cwd = _make_tmp_dir("cli_bg_workflow_live")
        save_transcript(
            SessionConfig(cwd=cwd, interactive=False),
            SessionState(
                session_id="live-bg-session",
                messages=[{"role": "user", "content": [{"type": "text", "text": "resume"}]}],
                recent_change_sets=[
                    WorkspaceChangeSet(
                        tool_name="apply_patch",
                        summary="Update demo.py",
                        files=[
                            WorkspaceFileChange(
                                path="demo.py",
                                existed_before=True,
                                before_content="old\n",
                                after_content="new\n",
                                action_kind="update",
                            )
                        ],
                    )
                ],
                saved_task_records=[
                    {
                        "id": "task-live-1",
                        "kind": "agent",
                        "description": "Update demo.py",
                        "status": "running",
                        "progress_summary": "Running background agent",
                        "metadata": {
                            "task_role": "background",
                            "parent_session_id": "live-bg-session",
                        },
                    }
                ],
                saved_task_surface_counts={"background_execution": 1},
            ),
        )
        record = create_background_session(
            cwd,
            prompt="hello",
            provider="anthropic",
            model="demo",
            status="running",
        )
        update_background_session(
            cwd,
            record.bg_id,
            session_id="live-bg-session",
            bridge_host="127.0.0.1",
            bridge_port=8765,
        )

        try:
            with redirect_stdout(StringIO()) as stdout:
                exit_code = main(["--cwd", str(cwd), "ps", record.bg_id])
            self.assertEqual(exit_code, 0)
            rendered = stdout.getvalue()
            self.assertIn("background workflow:", rendered)
            self.assertIn("current workflow: attachable live background session", rendered)
            self.assertIn("background execution tasks: 1", rendered)
            self.assertIn("primary task: task-live-1 (background_execution: Update demo.py)", rendered)
            self.assertIn("primary task progress: Running background agent", rendered)
            self.assertIn("latest change: Update demo.py", rendered)
            self.assertIn("recent activity: Running background agent", rendered)
            self.assertIn("last tool: apply_patch", rendered)
            self.assertIn("progress summary: Running background agent", rendered)
            self.assertIn("completion state: running", rendered)
            self.assertIn("focused file: demo.py", rendered)
            self.assertIn("focused file source: recent_change", rendered)
            self.assertIn("go_to_task: /task show task-live-1 | /tasks active", rendered)
            self.assertIn("/tasks active", rendered)
            self.assertLess(
                rendered.index(f"go_to_live_attach: pyclaude attach {record.bg_id}"),
                rendered.index("go_to_task: /task show task-live-1 | /tasks active"),
            )
            self.assertLess(
                rendered.index("go_to_task: /task show task-live-1 | /tasks active"),
                rendered.index(f"go_to_logs: pyclaude logs {record.bg_id} summary | pyclaude logs {record.bg_id}"),
            )
        finally:
            _cleanup_dir(cwd)

    def test_main_agents_prints_lightweight_agent_definitions(self) -> None:
        cwd = _make_tmp_dir("cli_agents_surface")

        try:
            with redirect_stdout(StringIO()) as stdout:
                exit_code = main(["--cwd", str(cwd), "agents"])
            self.assertEqual(exit_code, 0)
            rendered = stdout.getvalue()
            self.assertIn("agent definitions:", rendered)
            self.assertIn("source summary:", rendered)
            self.assertIn("- builtin: definitions=4 effective=4 shadowed=0 root=builtin", rendered)
            self.assertIn("effective definitions:", rendered)
            self.assertIn("- default: source=builtin effective=yes override_state=base based_on=none", rendered)
            self.assertIn("- background: source=builtin effective=yes override_state=base based_on=none", rendered)
            self.assertIn("- isolated_workspace: source=builtin effective=yes override_state=base based_on=none", rendered)
            self.assertIn("- read_only_planning: source=builtin effective=yes override_state=base based_on=none", rendered)
            self.assertIn("diagnostics:", rendered)
            self.assertIn("- none", rendered)
            self.assertIn("resolution:", rendered)
            self.assertIn("- shadowing_policy: same-name project-local replaces builtin", rendered)
        finally:
            _cleanup_dir(cwd)

    def test_task_show_renders_background_linkage_hint(self) -> None:
        cwd = _make_tmp_dir("cli_task_background_linkage")
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        try:
            task = session.task_manager.create(
                "agent",
                "background work",
                parent_session_id=session.state.session_id,
                task_role="background",
                background_session_id="bg-123",
                background_reverse_hint="pyclaude ps bg-123 | pyclaude logs bg-123 summary",
            )

            handled, output = _handle_repl_command(session, f"/task show {task.id}")

            self.assertTrue(handled)
            assert output is not None
            self.assertIn("background_linkage:", output)
            self.assertIn("- background_session_id: bg-123", output)
            self.assertIn("- background_reverse_hint: pyclaude ps bg-123 | pyclaude logs bg-123 summary", output)
        finally:
            session.close()
            _cleanup_dir(cwd)

    def test_main_ps_list_renders_live_attachable_guidance(self) -> None:
        cwd = _make_tmp_dir("cli_bg_live_attachable")
        record = create_background_session(
            cwd,
            prompt="hello",
            provider="anthropic",
            model="demo",
            status="running",
        )
        update_background_session(
            cwd,
            record.bg_id,
            session_id="live-bg-session",
            bridge_host="127.0.0.1",
            bridge_port=8765,
        )

        try:
            with redirect_stdout(StringIO()) as stdout:
                exit_code = main(["--cwd", str(cwd), "ps"])
            self.assertEqual(exit_code, 0)
            rendered = stdout.getvalue()
            self.assertIn("continuation=live attachable", rendered)
            self.assertIn(f"go_to_live_attach=pyclaude attach {record.bg_id}", rendered)
        finally:
            _cleanup_dir(cwd)

    def test_main_ps_detail_and_logs_invalid_selector_return_error(self) -> None:
        cwd = _make_tmp_dir("cli_bg_invalid_selector")
        try:
            with redirect_stdout(StringIO()) as ps_stdout:
                ps_exit = main(["--cwd", str(cwd), "ps", "missing"])
            self.assertEqual(ps_exit, 1)
            self.assertIn('No background session found for "missing"', ps_stdout.getvalue())

            with redirect_stdout(StringIO()) as logs_stdout:
                logs_exit = main(["--cwd", str(cwd), "logs", "missing"])
            self.assertEqual(logs_exit, 1)
            self.assertIn('No background session found for "missing"', logs_stdout.getvalue())
        finally:
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
            session = dispatcher._sessions[session_id].session
            session.state.original_cwd = str(cwd.resolve())
            session.state.effective_cwd = str((cwd / ".pyclaude" / "workspaces" / "agent-demo").resolve())
            session.state.workspace_mode = "snapshot"
            session.state.workspace_label = "agent-demo"
            session.state.workspace_created_at = "2026-05-02T00:00:00+00:00"
            session.state.workspace_cleanup_status = "failed"
            session.state.workspace_cleanup_error = "PermissionError: cleanup blocked"
            host, port = server.server_address
            update_background_session(
                cwd,
                record.bg_id,
                bridge_host=str(host),
                bridge_port=port,
                session_id=session_id,
                status="running",
                original_cwd=str(cwd.resolve()),
                effective_cwd=str((cwd / ".pyclaude" / "workspaces" / "agent-demo").resolve()),
                workspace_mode="snapshot",
                workspace_label="agent-demo",
                workspace_created_at="2026-05-02T00:00:00+00:00",
                workspace_cleanup_status="failed",
                workspace_cleanup_error="PermissionError: cleanup blocked",
            )
            with patch("builtins.input", side_effect=["/config", "/exit"]):
                with redirect_stdout(StringIO()) as stdout:
                    exit_code = main(["--cwd", str(cwd), "attach", record.bg_id])
            self.assertEqual(exit_code, 0)
            rendered = stdout.getvalue()
            self.assertIn("cwd:", rendered)
            self.assertIn("session_id:", rendered)
            self.assertIn("workspace_cleanup_status: failed", rendered)
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
            session = dispatcher._sessions[session_id].session
            session.state.original_cwd = str(cwd.resolve())
            session.state.effective_cwd = str((cwd / ".pyclaude" / "worktrees" / "bg-demo").resolve())
            session.state.workspace_mode = "worktree"
            session.state.workspace_label = "bg-demo"
            session.state.workspace_created_at = "2026-05-02T00:00:00+00:00"
            session.state.workspace_cleanup_status = "pending"
            session.state.workspace_unavailable = True
            session.state.workspace_unavailable_reason = "Isolated workspace is unavailable: expected missing worktree."
            session.state.workspace_fallback_cwd = str(cwd.resolve())
            host, port = server.server_address
            update_background_session(
                cwd,
                record.bg_id,
                bridge_host=str(host),
                bridge_port=port,
                session_id=session_id,
                status="running",
                original_cwd=str(cwd.resolve()),
                effective_cwd=str((cwd / ".pyclaude" / "worktrees" / "bg-demo").resolve()),
                workspace_mode="worktree",
                workspace_label="bg-demo",
                workspace_created_at="2026-05-02T00:00:00+00:00",
                workspace_cleanup_status="pending",
            )
            with patch("claudecode_py.cli._launch_tui", return_value=0) as launch_tui:
                exit_code = main(["--cwd", str(cwd), "attach", record.bg_id, "--mode", "tui"])
            self.assertEqual(exit_code, 0)
            remote_session = launch_tui.call_args.args[0]
            self.assertEqual(remote_session.state.session_id, session_id)
            self.assertEqual(remote_session.config.cwd, cwd)
            self.assertEqual(remote_session.state.workspace_mode, "worktree")
            self.assertEqual(remote_session.state.workspace_label, "bg-demo")
            self.assertEqual(remote_session.state.original_cwd, str(cwd.resolve()))
            self.assertTrue(remote_session.state.workspace_unavailable)
            self.assertEqual(remote_session.state.workspace_fallback_cwd, str(cwd.resolve()))
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
            self.assertIn("cleanup=", rendered)
            self.assertIn("127.0.0.1", str(resolve_background_session(cwd, record.bg_id).bridge_host))
            resolved = resolve_background_session(cwd, record.bg_id)
            assert resolved is not None
            self.assertEqual(resolved.workspace_mode, "snapshot")
            self.assertEqual(Path(resolved.original_cwd), cwd)
            self.assertIsNotNone(resolved.workspace_label)
            self.assertIsNotNone(resolved.workspace_created_at)
            self.assertIn(resolved.workspace_cleanup_status, {"pending", "completed"})
        finally:
            _cleanup_dir(cwd)

    def test_background_session_loader_keeps_compat_with_old_registry_records(self) -> None:
        cwd = _make_tmp_dir("cli_bg_old_registry")
        record = create_background_session(
            cwd,
            prompt="hello",
            provider="anthropic",
            model="demo",
            status="running",
        )

        try:
            path = get_background_session_path(cwd, record.bg_id)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload.pop("workspace_label", None)
            payload.pop("workspace_created_at", None)
            payload.pop("workspace_cleanup_status", None)
            payload.pop("workspace_cleanup_error", None)
            path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

            loaded = resolve_background_session(cwd, record.bg_id)
            assert loaded is not None
            self.assertEqual(loaded.workspace_mode, "main")
            self.assertIsNone(loaded.workspace_label)
            self.assertEqual(loaded.workspace_cleanup_status, "none")
            self.assertIsNone(loaded.workspace_cleanup_error)
        finally:
            _cleanup_dir(cwd)

    def test_main_sessions_output_includes_workspace_metadata(self) -> None:
        cwd = _make_tmp_dir("cli_sessions_workspace")
        save_transcript(
            SessionConfig(cwd=cwd, interactive=False),
            SessionState(
                session_id="workspace-session",
                original_cwd=str(cwd.resolve()),
                effective_cwd=str((cwd / ".pyclaude" / "workspaces" / "agent-demo").resolve()),
                workspace_mode="snapshot",
                workspace_label="agent-demo",
                workspace_created_at="2026-05-02T00:00:00+00:00",
                workspace_cleanup_status="pending",
                messages=[{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
            ),
        )

        try:
            with redirect_stdout(StringIO()) as stdout:
                exit_code = main(["--cwd", str(cwd), "sessions"])
            self.assertEqual(exit_code, 0)
            rendered = stdout.getvalue()
            self.assertIn("workspace=snapshot", rendered)
            self.assertIn("health=cleanup_pending", rendered)
            self.assertIn("label=agent-demo", rendered)
            self.assertIn(f"origin={cwd.resolve()}", rendered)
            self.assertIn("cleanup=pending", rendered)
            self.assertIn("actions=/workspaces list | /workspaces repair workspace-session", rendered)
        finally:
            _cleanup_dir(cwd)

    def test_main_resume_repl_prints_workspace_summary_for_missing_isolated_cwd(self) -> None:
        cwd = _make_tmp_dir("cli_resume_workspace")
        missing_cwd = cwd / ".pyclaude" / "worktrees" / "missing-agent"
        save_transcript(
            SessionConfig(cwd=cwd, interactive=False),
            SessionState(
                session_id="resume-workspace",
                original_cwd=str(cwd.resolve()),
                effective_cwd=str(missing_cwd.resolve()),
                workspace_mode="worktree",
                workspace_label="missing-agent",
                workspace_created_at="2026-05-02T00:00:00+00:00",
                workspace_cleanup_status="failed",
                workspace_cleanup_error="PermissionError: cleanup blocked",
                messages=[{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
            ),
        )

        try:
            with patch("builtins.input", side_effect=["/exit"]):
                with redirect_stdout(StringIO()) as stdout:
                    exit_code = main(["--cwd", str(cwd), "--resume-session", "resume-workspace", "repl"])
            self.assertEqual(exit_code, 0)
            rendered = stdout.getvalue()
            self.assertIn("session_source: restored_saved", rendered)
            self.assertIn("resume_semantics: saved session resume restores state only", rendered)
            self.assertIn("workspace: workspace=worktree", rendered)
            self.assertIn("label=missing-agent", rendered)
            self.assertIn("cwd_exists=no", rendered)
            self.assertIn("cleanup=failed", rendered)
            self.assertIn("unavailable=yes", rendered)
            self.assertIn(f"fallback={cwd.resolve()}", rendered)
            self.assertIn("workspace_health: unavailable", rendered)
            self.assertIn(f"workspace_expected_effective_cwd: {missing_cwd.resolve()}", rendered)
            self.assertIn(f"workspace_fallback_cwd: {cwd.resolve()}", rendered)
            self.assertIn(
                "workspace_recommended_actions: /workspaces list, /workspaces repair resume-workspace, /workspaces cleanup",
                rendered,
            )
        finally:
            _cleanup_dir(cwd)

    def test_main_sessions_lists_saved_continuation_and_surface_hints(self) -> None:
        cwd = _make_tmp_dir("cli_saved_sessions_surface_hints")
        artifact = PlanningArtifact(kind="plan", goal="resume coding", summary="Continue work")
        save_transcript(
            SessionConfig(cwd=cwd, interactive=False),
            SessionState(
                session_id="saved-surface-session",
                active_planning_artifact_id=artifact.artifact_id,
                planning_artifact_history=[artifact],
                recent_planning_artifacts=[artifact],
                saved_task_surface_counts={
                    "checklist": 1,
                    "workspace_maintenance": 0,
                    "child_execution": 0,
                    "background_execution": 1,
                    "active_plan_execution": 0,
                    "other_task": 0,
                },
                messages=[{"role": "user", "content": [{"type": "text", "text": "resume"}]}],
            ),
        )

        try:
            with redirect_stdout(StringIO()) as stdout:
                exit_code = main(["--cwd", str(cwd), "sessions"])
            self.assertEqual(exit_code, 0)
            rendered = stdout.getvalue()
            self.assertIn("source=saved", rendered)
            self.assertIn("continue=pyclaude --resume-session saved-surface-session repl", rendered)
            self.assertIn(f"active_plan={artifact.artifact_id}", rendered)
            self.assertIn("task_surfaces=checklist:1,background_execution:1", rendered)
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

    def test_project_context_commands_render_summary_and_reload_status(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_cli_project_context"
        _cleanup_dir(cwd)
        (cwd / ".pyclaude" / "skills").mkdir(parents=True, exist_ok=True)
        (cwd / "CLAUDE.md").write_text("Project memory text", encoding="utf-8")
        (cwd / ".pyclaude" / "skills" / "review.md").write_text(
            "---\n"
            "auto_enable: true\n"
            "---\n\n"
            "Review carefully.",
            encoding="utf-8",
        )
        session = Session(SessionConfig(cwd=cwd, interactive=False))

        try:
            handled_summary, output_summary = _handle_repl_command(session, "/project-context")
            handled_skills, output_skills = _handle_repl_command(session, "/project-context skills")
            handled_reload_before, output_reload_before = _handle_repl_command(
                session, "/project-context reload-status"
            )
            handled_invalid, output_invalid = _handle_repl_command(session, "/project-context invalid")

            self.assertTrue(handled_summary)
            self.assertTrue(handled_skills)
            self.assertTrue(handled_reload_before)
            self.assertTrue(handled_invalid)
            assert output_summary is not None
            assert output_skills is not None
            assert output_reload_before is not None
            self.assertIn("project context:", output_summary)
            self.assertIn("project memory: loaded", output_summary)
            self.assertIn("skill registry:", output_summary)
            self.assertIn("skill registry:", output_skills)
            self.assertIn("registered skills:", output_skills)
            self.assertIn("No project-context reload has run in this live session.", output_reload_before)
            self.assertEqual(
                output_invalid,
                "Usage: /project-context [summary|memory|skills|plugins|reload-status]",
            )

            handled_refresh, output_refresh = _handle_repl_command(session, "/context-refresh")
            handled_reload_after, output_reload_after = _handle_repl_command(
                session, "/project-context reload-status"
            )
            self.assertTrue(handled_refresh)
            self.assertTrue(handled_reload_after)
            assert output_refresh is not None
            assert output_reload_after is not None
            self.assertIn("Reloaded project context.", output_refresh)
            self.assertIn("skill_content_changed=no", output_refresh)
            self.assertIn("errors: none", output_reload_after)
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
