from io import StringIO
from pathlib import Path
import json
import shutil
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.config import SessionConfig
from claudecode_py.interactions import QuestionOption, UserQuestion, UserQuestionRequest
from claudecode_py.permissions import ApprovalRequest, ApprovalResult, PermissionManager
from claudecode_py.runtime.events import RuntimeEvent
from claudecode_py.state import AdvisorReviewSummary, PlanningArtifact, SessionState, WorkspaceFileChange
from claudecode_py.service import JsonRpcStdioService, ServiceDispatcher
from claudecode_py.storage.transcript import save_transcript


class StdioServiceTests(unittest.TestCase):
    def test_dispatcher_reports_service_protocol_metadata(self) -> None:
        dispatcher = ServiceDispatcher(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        try:
            response = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "service.hello",
                    "params": {},
                }
            )
            self.assertEqual(response["result"]["protocol"], "pyclaude-stdio-service")
            self.assertEqual(response["result"]["version"], "0.1")
            self.assertEqual(response["result"]["schema_version"], 1)
            self.assertIn("session.ask", response["result"]["methods"])
            self.assertTrue(response["result"]["capabilities"]["events_polling"])
            self.assertEqual(response["meta"]["schema_version"], 1)
        finally:
            dispatcher.close()

    def test_dispatcher_can_create_ask_and_close_session(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_session"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.py").write_text("def deploy():\n    return 1\n", encoding="utf-8")

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            dispatcher._sessions[session_id].session.ask = (  # type: ignore[method-assign]
                lambda prompt, sink=None: (sink and sink(RuntimeEvent(kind="assistant_text", message="hello"))) or "hello"
            )

            asked = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.ask",
                    "params": {"session_id": session_id, "prompt": "hello"},
                }
            )
            self.assertEqual(asked["result"]["kind"], "run_result")
            self.assertEqual(asked["result"]["session_id"], session_id)
            described = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 22,
                    "method": "session.describe",
                    "params": {"session_id": session_id},
                }
            )
            self.assertEqual(described["result"]["subscriber_count"], 0)
            self.assertEqual(described["result"]["workspace_mode"], "main")
            self.assertEqual(described["result"]["workspace_health"], "healthy")
            self.assertEqual(described["result"]["workspace_cleanup_status"], "none")
            self.assertFalse(described["result"]["workspace_unavailable"])
            self.assertEqual(described["result"]["workspace_primary_action"], "none")
            self.assertEqual(described["result"]["workspace_secondary_action"], "none")
            self.assertEqual(described["result"]["workspace_tertiary_action"], "/workspaces list")
            self.assertEqual(described["result"]["workspace_action_target"], session_id)
            self.assertIsNone(described["result"]["symbol_surface_kind"])
            self.assertEqual(described["result"]["symbol_primary_action"], "none")
            self.assertEqual(described["result"]["symbol_secondary_action"], "none")
            self.assertEqual(described["result"]["symbol_tertiary_action"], "/symbol clear")
            self.assertEqual(described["result"]["session_execution_mode"], "main")
            self.assertIsNone(described["result"]["session_command_policy_name"])
            self.assertEqual(described["result"]["session_command_policy_allowed_tool_names"], [])
            self.assertFalse(described["result"]["session_command_policy_require_read_only_subagents"])
            self.assertEqual(described["result"]["session_execution_summary"], "execution=main")
            self.assertEqual(
                described["result"]["task_surface_counts"],
                {
                    "checklist": 0,
                    "workspace_maintenance": 0,
                    "child_execution": 0,
                    "background_execution": 0,
                    "active_plan_execution": 0,
                    "other_task": 0,
                },
            )
            self.assertEqual(described["result"]["working_set_file_count"], 0)
            self.assertEqual(described["result"]["working_set_scope"], "session")
            self.assertEqual(described["result"]["working_set_sources"], [])
            self.assertEqual(described["result"]["working_set_files"], [])
            self.assertEqual(described["result"]["focused_file_context_source"], "working_set")
            self.assertIsNone(described["result"]["focused_file_context_path"])
            self.assertEqual(described["result"]["focused_file_context_scope_reasons"], [])
            self.assertFalse(described["result"]["focused_file_context_has_related_change"])
            self.assertFalse(described["result"]["focused_file_context_has_diff_hunks"])
            self.assertFalse(described["result"]["focused_file_context_is_context_only"])
            self.assertIsNone(described["result"]["focused_file_context_summary"])

            closed = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "session.close",
                    "params": {"session_id": session_id},
                }
            )
            self.assertTrue(closed["result"]["closed"])
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_session_describe_includes_current_symbol_surface_metadata(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_symbol_describe"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.py").write_text(
            "def build():\n"
            "    return 1\n\n"
            "value = build()\n",
            encoding="utf-8",
        )

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            dispatcher._sessions[session_id].session.handle_repl_command("/symbol actions build")

            described = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.describe",
                    "params": {"session_id": session_id},
                }
            )

            self.assertEqual(described["result"]["symbol_surface_kind"], "symbol_actions")
            self.assertEqual(described["result"]["symbol_selected_symbol"], "build")
            self.assertEqual(described["result"]["symbol_definition_count"], 1)
            self.assertEqual(described["result"]["symbol_reference_count"], 1)
            self.assertEqual(described["result"]["symbol_selected_definition_index"], 0)
            self.assertEqual(described["result"]["symbol_selected_reference_index"], 0)
            self.assertEqual(len(described["result"]["symbol_definitions"]), 1)
            self.assertEqual(len(described["result"]["symbol_references"]), 1)
            self.assertEqual(described["result"]["symbol_primary_action"], "/symbol open primary")
            self.assertEqual(described["result"]["symbol_secondary_action"], "/symbol open secondary")
            self.assertEqual(described["result"]["symbol_tertiary_action"], "/symbol clear")
            self.assertEqual(described["result"]["symbol_navigation_target"]["action"], "open_symbol")
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_session_describe_includes_working_set_metadata(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_working_set"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            session = dispatcher._sessions[session_id].session
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
            described = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.describe",
                    "params": {"session_id": session_id},
                }
            )

            self.assertEqual(described["result"]["working_set_file_count"], 1)
            self.assertEqual(described["result"]["working_set_primary_path"], "demo.py")
            self.assertEqual(described["result"]["working_set_primary_target"]["action"], "open_file")
            self.assertEqual(described["result"]["working_set_primary_target"]["path"], "demo.py")
            self.assertEqual(described["result"]["working_set_primary_diff_targets"]["path"], "demo.py")
            self.assertEqual(described["result"]["working_set_files"][0]["diff_target_count"], 1)
            self.assertEqual(described["result"]["focused_file_context_source"], "working_set")
            self.assertEqual(described["result"]["focused_file_context_path"], "demo.py")
            self.assertEqual(described["result"]["focused_file_context_primary_target"]["action"], "open_file")
            self.assertEqual(described["result"]["focused_file_context_secondary_target"]["path"], "demo.py")
            self.assertIn("source=working_set", described["result"]["focused_file_context_summary"])
            self.assertIn("path=demo.py", described["result"]["focused_file_context_summary"])
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_session_action_can_cycle_symbol_reference_selection(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_symbol_cycle"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle({"jsonrpc": "2.0", "id": 1, "method": "session.create", "params": {}})
            session_id = created["result"]["session_id"]
            session = dispatcher._sessions[session_id].session
            session._remember_symbol_surface(  # noqa: SLF001
                {
                    "surface_kind": "symbol_references",
                    "selected_symbol": "build",
                    "reference_count": 2,
                    "references": [
                        {"symbol": "build", "path": "demo.py", "line": 3, "text": "build()"},
                        {"symbol": "build", "path": "demo.py", "line": 8, "text": "build()"},
                    ],
                    "reference_targets": [
                        {"action": "open_reference", "path": "demo.py", "line": 3, "label": "ref one"},
                        {"action": "open_reference", "path": "demo.py", "line": 8, "label": "ref two"},
                    ],
                    "selected_reference": {"symbol": "build", "path": "demo.py", "line": 3, "text": "build()"},
                    "selected_reference_index": 0,
                    "selected_navigation_target": {
                        "action": "open_reference",
                        "path": "demo.py",
                        "line": 3,
                        "label": "ref one",
                    },
                }
            )

            response = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.action",
                    "params": {
                        "session_id": session_id,
                        "action": "symbol_surface_select_next_reference",
                    },
                }
            )

            self.assertIn("selected_reference_index: 2/2", response["result"]["text"])
            payload = session.current_symbol_surface_payload()
            self.assertEqual(payload["selected_reference_index"], 1)
            self.assertEqual(payload["selected_navigation_target"]["line"], 8)
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_dispatcher_supports_remote_command_and_action_methods(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_command"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]

            help_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.command",
                    "params": {"session_id": session_id, "prompt": "/help"},
                }
            )
            self.assertTrue(help_result["result"]["handled"])
            self.assertEqual(help_result["result"]["output_kind"], "text")
            self.assertIn("/review", help_result["result"]["output"])

            action_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "session.action",
                    "params": {
                        "session_id": session_id,
                        "action": "reload_project_context",
                    },
                }
            )
            self.assertIn("Reloaded project context.", action_result["result"]["text"])

            view_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "session.view",
                    "params": {"session_id": session_id, "view": "config"},
                }
            )
            self.assertIn("session_id:", view_result["result"]["text"])

            session = dispatcher._sessions[session_id].session
            task = session.task_manager.create(
                "ultraplan_scout",
                "Scout architecture",
                planner_kind="ultraplan",
                scout_category="architecture-boundaries",
            )
            session.task_manager.complete(task.id, "Inspect session.py.")
            session.record_planning_artifact(
                PlanningArtifact(
                    kind="ultraplan",
                    goal="map runtime",
                    summary="summary",
                    derived_from_drift=True,
                    derivation_reason="Need a safer runtime-only revision.",
                    used_read_only_subagents=True,
                    task_ids=[task.id],
                    advisor_status="approve",
                    advisor_reason="Solid plan.",
                )
            )
            artifact = session.active_planning_artifact()
            assert artifact is not None
            execution_task = session.task_manager.create(
                "agent",
                "Implement runtime changes",
                task_role="execution",
                active_plan_id=artifact.artifact_id,
                active_plan_goal=artifact.goal,
                plan_execution_mode="interactive_turn",
                plan_execution_phase="running",
                plan_status="on-plan",
                drift_status="block",
                drift_reason="Need a narrower runtime-only pass.",
                constraint_source="plan_drift_block",
            )
            session.task_manager.set_progress(execution_task.id, "Inspect runtime flow")
            session.state.advisor_model = "advisor-model"
            session.state.advisor_mode = "interactive-review"
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
            active_plan_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "session.view",
                    "params": {"session_id": session_id, "view": "active_plan"},
                }
            )
            self.assertIn("artifact_id:", active_plan_result["result"]["text"])
            self.assertIn("derived_from_drift: yes", active_plan_result["result"]["text"])
            scouts_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 55,
                    "method": "session.view",
                    "params": {
                        "session_id": session_id,
                        "view": "active_plan_scouts",
                        "selected_index": 0,
                    },
                }
            )
            self.assertIn("scout_outputs:", scouts_result["result"]["text"])
            self.assertIn("Inspect session.py.", scouts_result["result"]["text"])
            self.assertIn("selected_scout_detail:", scouts_result["result"]["text"])
            self.assertIn("output:", scouts_result["result"]["text"])
            self.assertIn("detail_mode: compact", scouts_result["result"]["text"])
            scouts_full_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 551,
                    "method": "session.view",
                    "params": {
                        "session_id": session_id,
                        "view": "active_plan_scouts",
                        "selected_index": 0,
                        "full_detail": True,
                    },
                }
            )
            self.assertIn("detail_mode: full", scouts_full_result["result"]["text"])
            execution_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 552,
                    "method": "session.view",
                    "params": {
                        "session_id": session_id,
                        "view": "active_plan_execution",
                        "selected_index": 0,
                    },
                }
            )
            self.assertIn("execution_tasks:", execution_result["result"]["text"])
            timeline_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 553,
                    "method": "session.view",
                    "params": {
                        "session_id": session_id,
                        "view": "active_plan_timeline",
                        "selected_index": 1,
                        "selected_compare_index": 2,
                        "kind_filter": "execution",
                        "delta_mode": "after-drift",
                        "focus_mode": "execution",
                        "compare_mode": "after-drift-vs-all",
                    },
                }
            )
            self.assertIn("timeline:", timeline_result["result"]["text"])
            self.assertIn("timeline_filter: execution", timeline_result["result"]["text"])
            self.assertIn("timeline_delta: after-drift", timeline_result["result"]["text"])
            self.assertIn("timeline_focus: execution", timeline_result["result"]["text"])
            self.assertIn("timeline_compare: after-drift-vs-all", timeline_result["result"]["text"])
            self.assertIn("compare_lens:", timeline_result["result"]["text"])
            self.assertIn("selected_timeline_compare: 3/", timeline_result["result"]["text"])
            self.assertIn("selected_timeline_compare_primary_action:", timeline_result["result"]["text"])
            self.assertIn("[Execution Loop]", timeline_result["result"]["text"])
            self.assertIn("[execution]", timeline_result["result"]["text"])
            self.assertIn("selected_timeline_primary_action:", timeline_result["result"]["text"])
            replay_view_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 5531,
                    "method": "session.view",
                    "params": {
                        "session_id": session_id,
                        "view": "active_plan_replay",
                        "latest": True,
                    },
                }
            )
            self.assertIn("replay_artifact:", replay_view_result["result"]["text"])
            self.assertIn("selected_replay_entry:", replay_view_result["result"]["text"])
            self.assertIn("selected_execution_detail:", execution_result["result"]["text"])
            self.assertIn("Implement runtime changes", execution_result["result"]["text"])
            self.assertIn("detail_mode: compact", execution_result["result"]["text"])
            execution_full_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 553,
                    "method": "session.view",
                    "params": {
                        "session_id": session_id,
                        "view": "active_plan_execution",
                        "selected_index": 0,
                        "full_detail": True,
                    },
                }
            )
            self.assertIn("detail_mode: full", execution_full_result["result"]["text"])
            task_detail_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 554,
                    "method": "session.action",
                    "params": {
                        "session_id": session_id,
                        "action": "open_task_detail",
                        "args": execution_task.id,
                    },
                }
            )
            self.assertIn(f"task_id: {execution_task.id}", task_detail_result["result"]["text"])
            self.assertIn("progress_summary: Inspect runtime flow", task_detail_result["result"]["text"])
            self.assertIn("- task_role: execution", task_detail_result["result"]["text"])
            self.assertIn("execution_context:", task_detail_result["result"]["text"])
            self.assertIn("- active_plan:", task_detail_result["result"]["text"])
            self.assertIn("latest_session_review:", task_detail_result["result"]["text"])
            self.assertIn("- drift_context:", task_detail_result["result"]["text"])
            self.assertIn("pending_tools: apply_patch", task_detail_result["result"]["text"])
            task_detail_view_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 5541,
                    "method": "session.view",
                    "params": {
                        "session_id": session_id,
                        "view": "task_detail",
                        "task_id": execution_task.id,
                    },
                }
            )
            self.assertEqual(task_detail_view_result["result"]["text"], task_detail_result["result"]["text"])
            self.assertNotIn("workspace_primary_action", task_detail_view_result["result"])
            self.assertNotIn("execution_policy", task_detail_view_result["result"])
            task_drift_view_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 5542,
                    "method": "session.view",
                    "params": {
                        "session_id": session_id,
                        "view": "task_drift_detail",
                        "task_id": execution_task.id,
                    },
                }
            )
            self.assertIn("drift_detail:", task_drift_view_result["result"]["text"])
            self.assertIn("advisor_context:", task_drift_view_result["result"]["text"])
            task_advisor_action_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 5543,
                    "method": "session.action",
                    "params": {
                        "session_id": session_id,
                        "action": "open_task_detail_advisor",
                        "args": execution_task.id,
                    },
                }
            )
            self.assertIn("advisor_review:", task_advisor_action_result["result"]["text"])
            task_drift_action_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 5544,
                    "method": "session.action",
                    "params": {
                        "session_id": session_id,
                        "action": "open_task_drift_detail",
                        "args": execution_task.id,
                    },
                }
            )
            self.assertIn("drift_detail:", task_drift_action_result["result"]["text"])
            phase_local_execution_action_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 5545,
                    "method": "session.action",
                    "params": {
                        "session_id": session_id,
                        "action": "open_phase_local_execution_task",
                    },
                }
            )
            self.assertIn(f"task_id: {execution_task.id}", phase_local_execution_action_result["result"]["text"])
            phase_local_drift_action_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 5546,
                    "method": "session.action",
                    "params": {
                        "session_id": session_id,
                        "action": "open_phase_local_recent_drift_task",
                    },
                }
            )
            self.assertIn("drift_detail:", phase_local_drift_action_result["result"]["text"])
            focus_timeline_task_action_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 5547,
                    "method": "session.action",
                    "params": {
                        "session_id": session_id,
                        "action": "focus_active_plan_timeline_task",
                        "args": execution_task.id,
                    },
                }
            )
            self.assertIn(f"timeline_focus: task:{execution_task.id}", focus_timeline_task_action_result["result"]["text"])
            clear_timeline_focus_action_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 5548,
                    "method": "session.action",
                    "params": {
                        "session_id": session_id,
                        "action": "clear_active_plan_timeline_focus",
                    },
                }
            )
            self.assertIn("timeline_focus: none", clear_timeline_focus_action_result["result"]["text"])
            lineage_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 56,
                    "method": "session.view",
                    "params": {"session_id": session_id, "view": "active_plan_lineage"},
                }
            )
            self.assertIn("lineage:", lineage_result["result"]["text"])
            self.assertIn("current", lineage_result["result"]["text"])
            advisor_detail_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 57,
                    "method": "session.view",
                    "params": {"session_id": session_id, "view": "active_plan_advisor"},
                }
            )
            self.assertIn("advisor_review:", advisor_detail_result["result"]["text"])
            advisor_action_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 571,
                    "method": "session.action",
                    "params": {"session_id": session_id, "action": "open_active_plan_advisor"},
                }
            )
            self.assertIn("advisor_review:", advisor_action_result["result"]["text"])
            advisor_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "session.view",
                    "params": {"session_id": session_id, "view": "advisor_status"},
                }
            )
            self.assertIn("Advisor: advisor-model", advisor_result["result"]["text"])
            advisor_status_action_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 61,
                    "method": "session.action",
                    "params": {"session_id": session_id, "action": "show_advisor_status"},
                }
            )
            self.assertIn("Advisor: advisor-model", advisor_status_action_result["result"]["text"])
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_dispatcher_supports_workspace_session_actions(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_workspace_actions"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.txt").write_text("hello\n", encoding="utf-8")
        orphan_dir = cwd / ".pyclaude" / "workspaces" / "orphan-agent"
        orphan_dir.mkdir(parents=True, exist_ok=True)
        missing_cwd = cwd / ".pyclaude" / "workspaces" / "missing-agent"

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            session = dispatcher._sessions[session_id].session
            session.permission_manager = PermissionManager(
                interactive=True,
                approval_handler=lambda _request: ApprovalResult(decision="allow", scope="once"),
            )
            session.state.workspace_mode = "snapshot"
            session.state.workspace_label = "missing-agent"
            session.state.workspace_unavailable = True
            session.state.workspace_unavailable_reason = "Isolated workspace is unavailable: expected missing snapshot."
            session.state.workspace_fallback_cwd = str(cwd.resolve())
            session.state.workspace_cleanup_status = "pending"
            session.state.workspace_health = "unavailable"
            session.state.original_cwd = str(cwd.resolve())
            session.state.effective_cwd = str(missing_cwd.resolve())

            preview = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.action",
                    "params": {"session_id": session_id, "action": "workspace_cleanup_preview"},
                }
            )
            self.assertIn("dry_run: yes", preview["result"]["text"])
            self.assertIn("cleanup planned | Would delete 1 orphaned isolated workspace(s).", preview["result"]["text"])

            cleanup = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "session.action",
                    "params": {
                        "session_id": session_id,
                        "action": "workspace_cleanup_apply",
                        "args": "orphan-agent",
                    },
                }
            )
            self.assertIn("dry_run: no", cleanup["result"]["text"])
            self.assertIn("deleted: 1", cleanup["result"]["text"])
            self.assertFalse(orphan_dir.exists())

            repair = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "session.action",
                    "params": {
                        "session_id": session_id,
                        "action": "workspace_repair",
                        "args": "missing-agent",
                    },
                }
            )
            self.assertIn("planned_repairs: 1", repair["result"]["text"])
            self.assertIn("repaired: 1", repair["result"]["text"])
            events = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 41,
                    "method": "session.events",
                    "params": {"session_id": session_id, "after_seq": 0, "limit": 20},
                }
            )
            task_events = [
                item
                for item in events["result"]["events"]
                if item["kind"] == "task_progress"
            ]
            self.assertGreaterEqual(len(task_events), 2)
            self.assertTrue(all(item["task_id"] for item in task_events))
            self.assertTrue(
                any("repair planned" in item["message"] for item in task_events)
            )
            self.assertTrue(
                any("repair applied" in item["message"] for item in task_events)
            )

            described = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "session.describe",
                    "params": {"session_id": session_id},
                }
            )
            self.assertEqual(described["result"]["workspace_health"], "cleanup_pending")
            self.assertEqual(described["result"]["workspace_mode"], "snapshot")
            self.assertFalse(described["result"]["workspace_unavailable"])
            self.assertEqual(described["result"]["workspace_primary_action"], "none")
            self.assertEqual(described["result"]["workspace_secondary_action"], "none")
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd, ignore_errors=True)

    def test_session_action_clear_session_reset_rotates_open_session_id(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_clear_session_reset"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            session = dispatcher._sessions[session_id].session
            session.state.messages = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
            session.state.context_summary = "Earlier conversation summary"
            session.persist_state()

            reset = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.action",
                    "params": {"session_id": session_id, "action": "clear_session_reset"},
                }
            )

            new_session_id = reset["result"]["session_id"]
            self.assertEqual(reset["result"]["old_session_id"], session_id)
            self.assertNotEqual(new_session_id, session_id)
            self.assertNotIn(session_id, dispatcher._sessions)
            self.assertIn(new_session_id, dispatcher._sessions)
            self.assertIn(new_session_id, str(reset["result"]["transcript_path"]))
            self.assertEqual(dispatcher._sessions[new_session_id].session.state.messages, [])
            self.assertIsNone(dispatcher._sessions[new_session_id].session.state.context_summary)

            described = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "session.describe",
                    "params": {"session_id": new_session_id},
                }
            )
            self.assertEqual(described["result"]["session_id"], new_session_id)

            missing = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "session.describe",
                    "params": {"session_id": session_id},
                }
            )
            self.assertEqual(missing["error"]["data"]["session_id"], session_id)
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd, ignore_errors=True)

    def test_session_view_task_detail_includes_workspace_action_metadata(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_workspace_task_detail"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            session = dispatcher._sessions[session_id].session
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

            detail = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.view",
                    "params": {
                        "session_id": session_id,
                        "view": "task_detail",
                        "task_id": task.id,
                    },
                }
            )

            self.assertEqual(detail["result"]["workspace_primary_action"], "workspace_cleanup_preview")
            self.assertEqual(
                detail["result"]["workspace_secondary_action"],
                "workspace_cleanup_apply orphan-agent",
            )
            self.assertEqual(detail["result"]["workspace_tertiary_action"], "/workspaces list")
            self.assertEqual(detail["result"]["workspace_action_target"], "orphan-agent")
            self.assertEqual(detail["result"]["workspace_health"], "healthy")
            self.assertEqual(detail["result"]["workspace_action"], "cleanup")
            self.assertEqual(detail["result"]["workspace_target"], "orphan-agent")
            self.assertEqual(detail["result"]["workspace_health_before"], "orphaned")
            self.assertEqual(detail["result"]["workspace_health_after"], "healthy")
            self.assertEqual(detail["result"]["workspace_planned_paths"], ["C:/tmp/orphan-agent"])
            self.assertEqual(detail["result"]["workspace_applied_paths"], ["C:/tmp/orphan-agent"])
            self.assertEqual(
                detail["result"]["workspace_recommended_actions"],
                [
                    "/workspaces list",
                    "/workspaces cleanup",
                    "/workspaces cleanup apply orphan-agent",
                ],
            )
            self.assertIsNone(detail["result"]["workspace_failure_reason"])
            self.assertGreaterEqual(detail["result"]["file_context_file_count"], 1)
            self.assertEqual(detail["result"]["file_context_primary_path"], "C:/tmp/orphan-agent")
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd, ignore_errors=True)

    def test_session_change_detail_includes_file_context_metadata(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_change_context"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            session = dispatcher._sessions[session_id].session
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

            detail = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.change_view",
                    "params": {"session_id": session_id, "view": "detail"},
                }
            )

            self.assertEqual(detail["result"]["file_context_file_count"], 1)
            self.assertEqual(detail["result"]["file_context_primary_path"], "demo.py")
            self.assertEqual(detail["result"]["file_context_primary_target"]["action"], "open_file")
            self.assertEqual(detail["result"]["file_context_primary_diff_targets"]["path"], "demo.py")
            self.assertIn("Focused file context:", detail["result"]["text"])
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd, ignore_errors=True)

    def test_dispatcher_active_plan_replay_supports_previous_artifact(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_replay_previous"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            session = dispatcher._sessions[session_id].session
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
            scout = session.task_manager.create(
                "ultraplan_scout",
                "Scout previous runtime",
                planner_kind="ultraplan",
                task_role="scout",
                active_plan_id=previous.artifact_id,
                scout_category="architecture-boundaries",
            )
            session.task_manager.complete(scout.id, "Inspect previous session.py.")
            session.record_planning_artifact(
                PlanningArtifact(
                    kind="ultraplan",
                    goal="current plan",
                    summary="Current Architecture\n- current runtime",
                    used_read_only_subagents=True,
                    supersedes_artifact_id=previous.artifact_id,
                )
            )

            replay_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.view",
                    "params": {
                        "session_id": session_id,
                        "view": "active_plan_replay",
                        "artifact_id": "previous",
                        "latest": True,
                    },
                }
            )

            text = replay_result["result"]["text"]
            self.assertIn("artifact_id: " + previous.artifact_id, text)
            self.assertIn("goal: previous plan", text)
            self.assertIn("- kind: scout", text)
            self.assertIn("selected_replay_primary_action: /task show " + scout.id, text)
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_session_view_task_detail_includes_execution_contract_metadata(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_execution_task_detail"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 3001,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            session = dispatcher._sessions[session_id].session
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

            detail = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 3002,
                    "method": "session.view",
                    "params": {
                        "session_id": session_id,
                        "view": "task_detail",
                        "task_id": task.id,
                    },
                }
            )

            result = detail["result"]
            self.assertEqual(result["task_surface"], "child_execution")
            self.assertEqual(result["execution_mode"], "read-only-subagent")
            self.assertEqual(result["execution_policy"], "read-only-subagent")
            self.assertEqual(result["execution_policy_source"], "subagent")
            self.assertEqual(result["allowed_tools"], ["read_file", "bash"])
            self.assertEqual(result["allowed_bash_prefixes"], ["git status"])
            self.assertTrue(result["read_only_subagents"])
            self.assertEqual(result["workspace_mode"], "snapshot")
            self.assertEqual(result["workspace_health"], "cleanup_pending")
            self.assertIn("execution_contract:", result["text"])
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_dispatcher_active_plan_replay_compare_can_target_previous_artifact(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_replay_compare_previous"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            session = dispatcher._sessions[session_id].session
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

            replay_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.view",
                    "params": {
                        "session_id": session_id,
                        "view": "active_plan_replay",
                        "compare_mode": "active-vs-previous",
                        "selected_compare_index": 1,
                    },
                }
            )

            text = replay_result["result"]["text"]
            self.assertIn("replay_source: compare-item", text)
            self.assertIn("replay_source_context: compare:previous", text)
            self.assertIn("replay_artifact: " + previous.artifact_id, text)
            self.assertIn("- kind: scout", text)
            self.assertIn("selected_replay_primary_action: /task show " + previous_scout.id, text)
            self.assertIn("lineage_replay_compare:", text)
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_dispatcher_active_plan_audit_supports_selected_artifact(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_plan_audit"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            session = dispatcher._sessions[session_id].session
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

            audit_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.view",
                    "params": {
                        "session_id": session_id,
                        "view": "active_plan_audit",
                        "artifact_id": "previous",
                    },
                }
            )

            text = audit_result["result"]["text"]
            self.assertIn("lineage_audit_summary:", text)
            self.assertIn(f"selected_audit_artifact_id: {previous.artifact_id}", text)
            self.assertIn("selected_audit_goal: previous plan", text)
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_dispatcher_serializes_and_executes_command_metadata(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_run_command"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            session = dispatcher._sessions[session_id].session

            command_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.command",
                    "params": {"session_id": session_id, "prompt": "/ultraplan map the runtime"},
                }
            )
            execution = command_result["result"]["execution"]
            self.assertTrue(execution["require_read_only_subagents"])
            self.assertEqual(execution["metadata"]["command_kind"], "ultraplan")

            session.run_command = lambda execution, sink=None: (  # type: ignore[method-assign]
                sink and sink(RuntimeEvent(kind="advisor_review_started", message="checkpoint=ultraplan"))
            ) or f"ran:{execution.metadata['command_kind']}"

            run_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "session.run_command",
                    "params": {"session_id": session_id, "execution": execution},
                }
            )
            self.assertEqual(run_result["result"]["payload"]["output"], "ran:ultraplan")
            self.assertTrue(
                any(
                    event["kind"] == "advisor_review_started"
                    for event in dispatcher._sessions[session_id].get_events()["events"]
                )
            )
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_dispatcher_can_report_and_resolve_pending_approval(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_approval"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            record = dispatcher._sessions[session_id]
            result_holder: dict[str, object] = {}

            def require() -> None:
                result_holder["result"] = record.request_approval(
                    ApprovalRequest(
                        tool_name="bash",
                        reason="Need to write files",
                        risk_level="write",
                        approval_key="write",
                        details="preview",
                        command="Remove-Item demo.txt",
                        target_paths=("demo.txt",),
                        permission_rules=("ask:path:demo",),
                        decision_reason="Matched ask rules: ask:path:demo",
                        command_mode_name="review",
                        command_mode_source="repl:/review",
                        command_mode_allowed_prefixes=("git diff", "git show"),
                        command_mode_complex_features=("command_substitution",),
                    )
                )

            thread = threading.Thread(target=require)
            thread.start()
            try:
                status = dispatcher.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "session.approval_status",
                        "params": {"session_id": session_id},
                    }
                )
                self.assertTrue(status["result"]["pending"])
                self.assertEqual(status["result"]["approval"]["tool_name"], "bash")
                self.assertEqual(status["result"]["approval"]["command"], "Remove-Item demo.txt")
                self.assertEqual(status["result"]["approval"]["target_paths"], ["demo.txt"])
                self.assertEqual(status["result"]["approval"]["permission_rules"], ["ask:path:demo"])
                self.assertEqual(
                    status["result"]["approval"]["decision_reason"],
                    "Matched ask rules: ask:path:demo",
                )
                self.assertEqual(status["result"]["approval"]["display_lines"][0], "risk: write")
                self.assertEqual(status["result"]["approval"]["display_lines"][1], "tool: bash")
                self.assertIn("policy: Matched ask rules: ask:path:demo", status["result"]["approval"]["display_lines"])
                self.assertEqual(status["result"]["approval"]["command_mode_name"], "review")
                self.assertEqual(status["result"]["approval"]["command_mode_allowed_prefixes"], ["git diff", "git show"])
                self.assertEqual(status["result"]["approval"]["command_mode_complex_features"], ["command_substitution"])

                response = dispatcher.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "session.approval_respond",
                        "params": {
                            "session_id": session_id,
                            "approval_id": status["result"]["approval_id"],
                            "decision": "allow",
                            "scope": "session",
                        },
                    }
                )
                self.assertTrue(response["result"]["resolved"])
            finally:
                thread.join(timeout=2)
            self.assertEqual(getattr(result_holder["result"], "decision", None), "allow")
            self.assertEqual(record.pending_approval_status()["pending"], False)
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_dispatcher_formats_workspace_cleanup_approval_like_tui_panel(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_workspace_cleanup_approval"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            record = dispatcher._sessions[session_id]
            result_holder: dict[str, object] = {}

            def require() -> None:
                result_holder["result"] = record.request_approval(
                    ApprovalRequest(
                        tool_name="workspace_cleanup",
                        reason="Delete orphaned isolated workspaces from the local .pyclaude directory.",
                        risk_level="delete",
                        approval_key="workspace_cleanup_delete:orphan-agent",
                        details=(
                            "Delete orphaned isolated workspaces.\n"
                            "selector: orphan-agent\n"
                            "planned_deletions: 1\n"
                            "planned_targets:\n"
                            "- workspace=snapshot label=orphan-agent cwd=C:/tmp/orphan-agent"
                        ),
                        target_paths=(".pyclaude/workspaces/orphan-agent",),
                    )
                )

            thread = threading.Thread(target=require)
            thread.start()
            try:
                status = dispatcher.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "session.approval_status",
                        "params": {"session_id": session_id},
                    }
                )
                approval = status["result"]["approval"]
                self.assertEqual(approval["display_lines"][0], "risk: delete")
                self.assertEqual(approval["display_lines"][1], "tool: workspace_cleanup")
                self.assertIn("paths:", approval["display_lines"])
                self.assertIn("- .pyclaude/workspaces/orphan-agent", approval["display_lines"])
                self.assertIn("details:", approval["display_lines"])
                self.assertIn("- selector: orphan-agent", approval["display_lines"])
                self.assertTrue(approval["display_compact"].startswith("risk=delete | tool=workspace_cleanup"))

                dispatcher.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "session.approval_respond",
                        "params": {
                            "session_id": session_id,
                            "approval_id": status["result"]["approval_id"],
                            "decision": "allow",
                            "scope": "once",
                        },
                    }
                )
            finally:
                thread.join(timeout=2)
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_dispatcher_can_report_and_resolve_pending_questions(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_questions"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            record = dispatcher._sessions[session_id]
            result_holder: dict[str, object] = {}

            def require() -> None:
                result_holder["result"] = record.request_questions(
                    UserQuestionRequest(
                        questions=(
                            UserQuestion(
                                header="Backend",
                                question="Which backend should be used?",
                                options=(
                                    QuestionOption(label="stdio", description="Use stdio transport"),
                                    QuestionOption(label="tcp", description="Use TCP bridge"),
                                ),
                            ),
                        )
                    )
                )

            thread = threading.Thread(target=require)
            thread.start()
            try:
                status = dispatcher.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "session.question_status",
                        "params": {"session_id": session_id},
                    }
                )
                self.assertTrue(status["result"]["pending"])
                self.assertEqual(status["result"]["question_request"]["questions"][0]["header"], "Backend")

                response = dispatcher.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "session.question_respond",
                        "params": {
                            "session_id": session_id,
                            "question_id": status["result"]["question_id"],
                            "answers": {"Which backend should be used?": "tcp"},
                        },
                    }
                )
                self.assertTrue(response["result"]["resolved"])
            finally:
                thread.join(timeout=2)
            answers = getattr(result_holder["result"], "answers", {})
            self.assertEqual(answers.get("Which backend should be used?"), "tcp")
            self.assertEqual(record.pending_question_status()["pending"], False)
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_dispatcher_returns_symbol_actions(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_symbol"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.py").write_text(
            "def build():\n"
            "    return 1\n\n"
            "value = build()\n",
            encoding="utf-8",
        )

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            response = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "symbol.actions",
                    "params": {"session_id": session_id, "symbol": "build"},
                }
            )
            self.assertEqual(response["result"]["symbol"], "build")
            self.assertEqual(response["result"]["definitions"][0]["action"], "open_symbol")
            self.assertEqual(response["result"]["references"][0]["action"], "open_reference")
            self.assertEqual(response["result"]["surface_kind"], "symbol_actions")
            self.assertEqual(response["result"]["definition_count"], 1)
            self.assertEqual(response["result"]["reference_count"], 1)
            self.assertEqual(response["result"]["selected_symbol"], "build")
            self.assertEqual(response["result"]["selected_definition"]["action"], "open_symbol")
            self.assertEqual(response["result"]["selected_reference"]["action"], "open_reference")
            self.assertEqual(response["result"]["navigation_target"]["action"], "open_symbol")
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_dispatcher_returns_js_ts_symbol_actions(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_symbol_ts"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "ui.ts").write_text(
            "export const build = () => 1\n"
            "const value = build()\n",
            encoding="utf-8",
        )

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            response = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "symbol.actions",
                    "params": {"session_id": session_id, "symbol": "build"},
                }
            )
            self.assertEqual(response["result"]["definitions"][0]["path"], "ui.ts")
            self.assertEqual(response["result"]["references"][0]["path"], "ui.ts")
            self.assertEqual(response["result"]["surface_kind"], "symbol_actions")
            self.assertEqual(response["result"]["navigation_target"]["path"], "ui.ts")
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_dispatcher_returns_symbol_lookup_and_reference_surfaces(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_symbol_surfaces"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.py").write_text(
            "def build():\n"
            "    return 1\n\n"
            "value = build()\n",
            encoding="utf-8",
        )

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            lookup = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "symbol.locate",
                    "params": {"session_id": session_id, "symbol": "build"},
                }
            )
            references = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "symbol.references",
                    "params": {"session_id": session_id, "symbol": "build"},
                }
            )

            self.assertEqual(lookup["result"]["surface_kind"], "symbol_lookup")
            self.assertEqual(lookup["result"]["match_count"], 1)
            self.assertEqual(lookup["result"]["selected_navigation_target"]["action"], "open_symbol")
            self.assertEqual(references["result"]["surface_kind"], "symbol_references")
            self.assertEqual(references["result"]["reference_count"], 1)
            self.assertEqual(references["result"]["selected_navigation_target"]["action"], "open_reference")
            self.assertEqual(references["result"]["reference_targets"][0]["path"], "demo.py")
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_dispatcher_session_create_loads_mcp_tools_from_config(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_mcp"
        if cwd.exists():
            shutil.rmtree(cwd)
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

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {"mcp_config_path": str(config_path)},
                }
            )
            session_id = created["result"]["session_id"]
            self.assertIn(
                "fake.echo_text",
                dispatcher._sessions[session_id].session.describe_mcp_tools(),
            )
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_dispatcher_lists_and_resumes_saved_sessions(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_saved"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        save_transcript(
            SessionConfig(cwd=cwd, interactive=False),
            SessionState(
                session_id="saved-session",
                session_execution_mode="read-only-subagent",
                session_command_policy_name="read-only-subagent",
                session_command_policy_source="subagent",
                session_command_policy_allowed_tool_names=["bash", "read_file"],
                session_command_policy_allowed_bash_prefixes=["git status"],
                session_command_policy_require_read_only_subagents=True,
                original_cwd=str(cwd.resolve()),
                effective_cwd=str((cwd / ".pyclaude" / "workspaces" / "agent-demo").resolve()),
                workspace_mode="snapshot",
                workspace_label="agent-demo",
                workspace_created_at="2026-05-02T00:00:00+00:00",
                workspace_cleanup_status="pending",
                workspace_unavailable=True,
                workspace_unavailable_reason="Isolated workspace is unavailable: expected missing snapshot.",
                workspace_fallback_cwd=str(cwd.resolve()),
                messages=[{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
            ),
        )

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            listed = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.list_saved",
                    "params": {},
                }
            )
            self.assertEqual(listed["result"]["sessions"][0]["session_id"], "saved-session")
            self.assertEqual(listed["result"]["sessions"][0]["workspace_mode"], "snapshot")
            self.assertEqual(listed["result"]["sessions"][0]["workspace_health"], "unavailable")
            self.assertEqual(listed["result"]["sessions"][0]["workspace_label"], "agent-demo")
            self.assertEqual(listed["result"]["sessions"][0]["workspace_cleanup_status"], "pending")
            self.assertTrue(listed["result"]["sessions"][0]["workspace_unavailable"])
            self.assertEqual(listed["result"]["sessions"][0]["workspace_fallback_cwd"], str(cwd.resolve()))
            self.assertEqual(listed["result"]["sessions"][0]["session_execution_mode"], "read-only-subagent")
            self.assertEqual(
                listed["result"]["sessions"][0]["session_command_policy_name"],
                "read-only-subagent",
            )
            self.assertEqual(
                listed["result"]["sessions"][0]["session_execution_summary"],
                "execution=read-only-subagent  policy=read-only-subagent  read_only_subagents=yes",
            )
            self.assertEqual(
                listed["result"]["sessions"][0]["session_command_policy_allowed_bash_prefixes"],
                ["git status"],
            )
            self.assertTrue(
                listed["result"]["sessions"][0]["session_command_policy_require_read_only_subagents"]
            )
            self.assertEqual(
                listed["result"]["sessions"][0]["workspace_primary_action"],
                "workspace_repair saved-session",
            )
            self.assertEqual(
                listed["result"]["sessions"][0]["workspace_secondary_action"],
                "workspace_cleanup_preview",
            )
            self.assertEqual(
                listed["result"]["sessions"][0]["workspace_tertiary_action"],
                "/workspaces list",
            )
            self.assertEqual(listed["result"]["sessions"][0]["workspace_action_target"], "saved-session")
            self.assertEqual(
                listed["result"]["sessions"][0]["workspace_recommended_actions"],
                [
                    "/workspaces list",
                    "/workspaces repair saved-session",
                    "/workspaces cleanup",
                ],
            )
            self.assertEqual(listed["result"]["sessions"][0]["focused_file_context_source"], "working_set")
            self.assertIsNone(listed["result"]["sessions"][0]["focused_file_context_path"])
            self.assertIsNone(listed["result"]["sessions"][0]["focused_file_context_summary"])

            resumed = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.resume",
                    "params": {"resume_session_id": "saved-session"},
                }
            )
            self.assertEqual(resumed["result"]["session_id"], "saved-session")
            self.assertIsNotNone(resumed["result"]["restored_from"])
            self.assertEqual(resumed["result"]["workspace_mode"], "snapshot")
            self.assertEqual(resumed["result"]["workspace_health"], "unavailable")
            self.assertEqual(resumed["result"]["workspace_label"], "agent-demo")
            self.assertTrue(resumed["result"]["workspace_unavailable"])
            self.assertEqual(resumed["result"]["workspace_fallback_cwd"], str(cwd.resolve()))
            self.assertEqual(resumed["result"]["session_execution_mode"], "read-only-subagent")
            self.assertEqual(resumed["result"]["session_command_policy_name"], "read-only-subagent")
            self.assertEqual(
                resumed["result"]["session_command_policy_allowed_tool_names"],
                ["bash", "read_file"],
            )
            self.assertTrue(resumed["result"]["session_command_policy_require_read_only_subagents"])
            self.assertEqual(
                resumed["result"]["workspace_recommended_actions"],
                [
                    "/workspaces list",
                    "/workspaces repair saved-session",
                    "/workspaces cleanup",
                ],
            )
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_session_resume_restores_plan_and_task_continuity(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_resume_continuity"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        setup_dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = setup_dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            session = setup_dispatcher._sessions[session_id].session
            artifact = PlanningArtifact(
                kind="plan",
                goal="resume active work",
                summary="Continue session state",
            )
            session.state.active_planning_artifact_id = artifact.artifact_id
            session.state.planning_artifact_history = [artifact]
            session.state.recent_planning_artifacts = [artifact]
            checklist_task = session.create_checklist_task(
                subject="Review runtime",
                description="Check restored state",
                active_form="Reviewing runtime",
                status="pending",
                owner="assistant",
            )
            background_task = session.task_manager.create(
                "agent",
                "Investigate resume flow",
                task_role="background",
                provider="anthropic",
                model="demo",
            )
            session.task_manager.set_progress(background_task.id, "Still running in prior session")
            session.persist_state()
        finally:
            setup_dispatcher.close()

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            resumed = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.resume",
                    "params": {"resume_session_id": session_id},
                }
            )
            result = resumed["result"]
            self.assertEqual(result["session_source"], "restored_saved")
            self.assertEqual(result["continuation_mode"], "live_session")
            self.assertTrue(result["saved_resume_restores_state_only"])
            self.assertTrue(result["has_active_plan"])
            self.assertEqual(result["active_planning_artifact_id"], artifact.artifact_id)
            self.assertEqual(result["planning_artifact_count"], 1)
            self.assertEqual(result["task_surface_counts"]["checklist"], 1)
            self.assertEqual(result["task_surface_counts"]["background_execution"], 1)
            self.assertEqual(result["task_surface_total_count"], 2)

            resumed_session = dispatcher._sessions[session_id].session
            restored_task = resumed_session.task_manager.get(background_task.id)
            self.assertIsNotNone(restored_task)
            assert restored_task is not None
            self.assertEqual(restored_task.status, "stopped")
            self.assertTrue(restored_task.metadata["restored_from_saved_session"])
            self.assertIn("Live process execution was not resumed", restored_task.metadata["resume_note"])

            detail = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "session.view",
                    "params": {
                        "session_id": session_id,
                        "view": "task_detail",
                        "task_id": background_task.id,
                    },
                }
            )
            self.assertIn("resume_note", detail["result"]["text"])

            transitioned = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "session.action",
                    "params": {
                        "session_id": session_id,
                        "action": "checklist_mark_completed",
                        "args": checklist_task["id"],
                    },
                }
            )
            self.assertIn("to completed", transitioned["result"]["text"])
            updated_task = resumed_session.get_checklist_task(checklist_task["id"])
            assert updated_task is not None
            self.assertEqual(updated_task["status"], "completed")
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_dispatcher_tracks_open_sessions_and_event_cursor(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_events"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            session = dispatcher._sessions[session_id].session
            session.state.original_cwd = str(cwd.resolve())
            session.state.effective_cwd = str((cwd / ".pyclaude" / "worktrees" / "agent-demo").resolve())
            session.state.workspace_mode = "worktree"
            session.state.workspace_label = "agent-demo"
            session.state.workspace_created_at = "2026-05-02T00:00:00+00:00"
            session.state.workspace_cleanup_status = "pending"

            def fake_ask(prompt, sink=None):
                if sink is not None:
                    sink(RuntimeEvent(kind="assistant_text", message="chunk-1"))
                    sink(RuntimeEvent(kind="tool_started", message='{"path":"demo.py"}', tool_name="read_file"))
                return "done"

            dispatcher._sessions[session_id].session.ask = fake_ask  # type: ignore[method-assign]

            dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.ask",
                    "params": {"session_id": session_id, "prompt": "hello"},
                }
            )
            events = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "session.events",
                    "params": {"session_id": session_id, "after_seq": 0},
                }
            )
            self.assertEqual(events["result"]["last_seq"], 2)
            self.assertEqual(len(events["result"]["events"]), 2)
            self.assertEqual(events["result"]["events"][0]["kind"], "assistant_text")

            listed = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "session.list_open",
                    "params": {},
                }
            )
            self.assertEqual(listed["result"]["sessions"][0]["session_id"], session_id)
            self.assertEqual(listed["result"]["sessions"][0]["event_cursor"], 2)
            self.assertEqual(listed["result"]["sessions"][0]["subscriber_count"], 0)
            self.assertEqual(listed["result"]["sessions"][0]["workspace_mode"], "worktree")
            self.assertEqual(listed["result"]["sessions"][0]["workspace_label"], "agent-demo")
            self.assertEqual(listed["result"]["sessions"][0]["session_execution_summary"], "execution=main")
            self.assertEqual(listed["result"]["sessions"][0]["workspace_primary_action"], "none")
            self.assertEqual(listed["result"]["sessions"][0]["workspace_secondary_action"], "none")
            self.assertEqual(listed["result"]["sessions"][0]["workspace_tertiary_action"], "/workspaces list")
            self.assertEqual(listed["result"]["sessions"][0]["workspace_action_target"], session_id)
            self.assertEqual(listed["result"]["sessions"][0]["focused_file_context_source"], "working_set")
            self.assertIsNone(listed["result"]["sessions"][0]["focused_file_context_path"])
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_stdio_service_handles_parse_error(self) -> None:
        dispatcher = ServiceDispatcher(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        stdin = StringIO('not-json\n')
        stdout = StringIO()
        service = JsonRpcStdioService(dispatcher, stdin=stdin, stdout=stdout)

        service.serve_forever()

        payload = json.loads(stdout.getvalue().strip())
        self.assertEqual(payload["error"]["code"], -32700)
        self.assertEqual(payload["error"]["data"]["type"], "parse_error")
        self.assertEqual(payload["meta"]["schema_version"], 1)

    def test_dispatcher_returns_method_not_found(self) -> None:
        dispatcher = ServiceDispatcher(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        try:
            response = dispatcher.handle(
                {"jsonrpc": "2.0", "id": 1, "method": "nope", "params": {}}
            )
            self.assertEqual(response["error"]["code"], -32601)
            self.assertEqual(response["meta"]["schema_version"], 1)
        finally:
            dispatcher.close()

    def test_dispatcher_returns_structured_error_data_for_unknown_session(self) -> None:
        dispatcher = ServiceDispatcher(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        try:
            response = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.describe",
                    "params": {"session_id": "missing"},
                }
            )
            self.assertEqual(response["error"]["code"], -32004)
            self.assertEqual(response["error"]["data"]["type"], "session_not_found")
            self.assertEqual(response["error"]["data"]["session_id"], "missing")
            self.assertEqual(response["meta"]["schema_version"], 1)
        finally:
            dispatcher.close()

    def test_session_view_tasks_includes_checklist_tasks(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_checklist_tasks"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            session = dispatcher._sessions[session_id].session
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
            duplicate = session.create_checklist_task(
                subject="Inspect runtime",
                description="Inspect session.py",
                active_form="Inspecting runtime",
            )

            response = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.view",
                    "params": {"session_id": session_id, "view": "tasks"},
                }
            )
            described = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "session.describe",
                    "params": {"session_id": session_id},
                }
            )

            self.assertFalse(duplicate["created"])
            self.assertEqual(response["result"]["view"], "tasks")
            self.assertEqual(len(response["result"]["checklist_tasks"]), 2)
            self.assertEqual(response["result"]["checklist_tasks"][0]["subject"], "Inspect runtime")
            self.assertEqual(response["result"]["checklist_tasks"][1]["status"], "in_progress")
            self.assertEqual(
                response["result"]["task_surface_counts"],
                {
                    "checklist": 2,
                    "workspace_maintenance": 0,
                    "child_execution": 0,
                    "background_execution": 0,
                    "active_plan_execution": 0,
                    "other_task": 0,
                },
            )
            self.assertEqual(
                response["result"]["checklist_duplicate_matched_task_id"],
                duplicate["duplicate_guard"]["matched_task_id"],
            )
            self.assertIn("session_task_get", response["result"]["checklist_duplicate_recommended_action"])
            self.assertEqual(
                described["result"]["checklist_duplicate_matched_task_id"],
                duplicate["duplicate_guard"]["matched_task_id"],
            )
            self.assertIn("session_checklist:", response["result"]["text"])
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_session_view_task_detail_includes_checklist_metadata(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_checklist_task_detail"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            session = dispatcher._sessions[session_id].session
            checklist_task = session.create_checklist_task(
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

            response = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.view",
                    "params": {
                        "session_id": session_id,
                        "view": "task_detail",
                        "task_id": checklist_task["id"],
                    },
                }
            )

            result = response["result"]
            self.assertFalse(duplicate["created"])
            self.assertEqual(result["view"], "task_detail")
            self.assertEqual(result["checklist_task_id"], checklist_task["id"])
            self.assertEqual(result["checklist_subject"], "Inspect runtime")
            self.assertEqual(result["checklist_status"], "in_progress")
            self.assertEqual(result["checklist_owner"], "assistant")
            self.assertEqual(result["checklist_blocks"], ["task-b"])
            self.assertEqual(result["checklist_blocked_by"], ["task-a"])
            self.assertEqual(result["checklist_metadata"], {"area": "runtime"})
            self.assertEqual(result["checklist_primary_action"], f"checklist_mark_completed {checklist_task['id']}")
            self.assertEqual(result["checklist_secondary_action"], f"checklist_reopen {checklist_task['id']}")
            self.assertEqual(result["checklist_edit_subject_action"], f"checklist_set_subject {checklist_task['id']}")
            self.assertEqual(
                result["checklist_edit_description_action"],
                f"checklist_set_description {checklist_task['id']}",
            )
            self.assertEqual(result["checklist_edit_owner_action"], f"checklist_set_owner {checklist_task['id']}")
            self.assertEqual(
                result["checklist_edit_active_form_action"],
                f"checklist_set_active_form {checklist_task['id']}",
            )
            self.assertEqual(result["checklist_edit_blocks_action"], f"checklist_set_blocks {checklist_task['id']}")
            self.assertEqual(
                result["checklist_edit_blocked_by_action"],
                f"checklist_set_blocked_by {checklist_task['id']}",
            )
            self.assertEqual(
                result["checklist_edit_metadata_action"],
                f"checklist_set_metadata {checklist_task['id']}",
            )
            self.assertEqual(result["selected_checklist_primary_action"], f"checklist_mark_completed {checklist_task['id']}")
            self.assertEqual(
                result["checklist_recommended_actions"],
                [
                    f"session_task_get {checklist_task['id']}",
                    f"session_task_update {checklist_task['id']} status=completed",
                    "session_task_list",
                ],
            )
            self.assertEqual(result["checklist_duplicate_matched_task_id"], checklist_task["id"])
            self.assertIn("session_task_get", result["checklist_duplicate_recommended_action"])
            self.assertIn("kind: session_checklist", result["text"])
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_session_action_supports_checklist_status_transitions(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_checklist_actions"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            session = dispatcher._sessions[session_id].session
            checklist_task = session.create_checklist_task(
                subject="Inspect runtime",
                description="Inspect session.py",
                active_form="Inspecting runtime",
                status="pending",
                owner="assistant",
            )

            response = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.action",
                    "params": {
                        "session_id": session_id,
                        "action": "checklist_mark_in_progress",
                        "args": checklist_task["id"],
                    },
                }
            )

            self.assertEqual(response["result"]["action"], "checklist_mark_in_progress")
            self.assertIn("to in_progress", response["result"]["text"])
            updated = session.get_checklist_task(checklist_task["id"])
            assert updated is not None
            self.assertEqual(updated["status"], "in_progress")
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_session_action_supports_checklist_field_edits(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_checklist_edit_actions"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            session = dispatcher._sessions[session_id].session
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
            checklist_task = session.create_checklist_task(
                subject="Inspect runtime",
                description="Inspect session.py",
                active_form="Inspecting runtime",
                status="pending",
                owner="assistant",
                blocks=[existing_a["id"]],
                blocked_by=[existing_b["id"]],
            )

            subject_response = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.action",
                    "params": {
                        "session_id": session_id,
                        "action": "checklist_set_subject",
                        "args": checklist_task["id"],
                        "value": "Review runtime flow",
                    },
                }
            )
            description_response = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "session.action",
                    "params": {
                        "session_id": session_id,
                        "action": "checklist_set_description",
                        "args": checklist_task["id"],
                        "value": "",
                    },
                }
            )
            owner_response = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "session.action",
                    "params": {
                        "session_id": session_id,
                        "action": "checklist_set_owner",
                        "args": checklist_task["id"],
                        "value": "",
                    },
                }
            )
            active_form_response = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "session.action",
                    "params": {
                        "session_id": session_id,
                        "action": "checklist_set_active_form",
                        "args": checklist_task["id"],
                        "value": "Reviewing runtime",
                    },
                }
            )
            blocks_response = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "session.action",
                    "params": {
                        "session_id": session_id,
                        "action": "checklist_set_blocks",
                        "args": checklist_task["id"],
                        "value": f'{existing_a["id"]}, {existing_b["id"]}',
                    },
                }
            )
            blocked_by_response = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "session.action",
                    "params": {
                        "session_id": session_id,
                        "action": "checklist_set_blocked_by",
                        "args": checklist_task["id"],
                        "value": "",
                    },
                }
            )
            metadata_response = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 8,
                    "method": "session.action",
                    "params": {
                        "session_id": session_id,
                        "action": "checklist_set_metadata",
                        "args": checklist_task["id"],
                        "value": "area=runtime\npriority=high",
                    },
                }
            )

            self.assertIn("Updated checklist task", subject_response["result"]["text"])
            self.assertIn("Cleared checklist task", description_response["result"]["text"])
            self.assertIn("Cleared checklist task", owner_response["result"]["text"])
            self.assertIn("Updated checklist task", active_form_response["result"]["text"])
            self.assertIn("Updated checklist task", blocks_response["result"]["text"])
            self.assertIn("Cleared checklist task", blocked_by_response["result"]["text"])
            self.assertIn("Updated checklist task", metadata_response["result"]["text"])
            updated = session.get_checklist_task(checklist_task["id"])
            assert updated is not None
            self.assertEqual(updated["subject"], "Review runtime flow")
            self.assertEqual(updated["description"], "")
            self.assertIsNone(updated["owner"])
            self.assertEqual(updated["active_form"], "Reviewing runtime")
            self.assertEqual(updated["blocks"], [existing_a["id"], existing_b["id"]])
            self.assertEqual(updated["blocked_by"], [])
            self.assertEqual(updated["metadata"], {"area": "runtime", "priority": "high"})
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)
