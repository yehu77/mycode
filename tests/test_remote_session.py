from pathlib import Path
from threading import Lock
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.remote_session import RemoteSessionProxy, _runtime_event_from_payload
from claudecode_py.state import SessionState


class _FakeBridgeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def request(self, method: str, params: dict[str, object]) -> dict[str, object]:
        self.calls.append((method, dict(params)))
        if method == "symbol.locate":
            return {
                "surface_kind": "symbol_lookup",
                "symbol": "build",
                "matches": [{"symbol": "build", "kind": "function", "path": "demo.py", "line": 1, "owner": None}],
                "match_count": 1,
                "selected_symbol": "build",
                "selected_match": {"symbol": "build", "kind": "function", "path": "demo.py", "line": 1, "owner": None},
                "selected_match_index": 0,
                "selected_navigation_target": {
                    "action": "open_symbol",
                    "path": "demo.py",
                    "line": 1,
                    "column": 1,
                    "end_line": None,
                    "end_column": None,
                    "label": "function build",
                },
            }
        if method == "symbol.references":
            return {
                "surface_kind": "symbol_references",
                "symbol": "build",
                "references": [{"symbol": "build", "path": "demo.py", "line": 3, "text": "value = build()"}],
                "reference_count": 1,
                "selected_symbol": "build",
                "selected_reference": {"symbol": "build", "path": "demo.py", "line": 3, "text": "value = build()"},
                "selected_reference_index": 0,
                "selected_navigation_target": {
                    "action": "open_reference",
                    "path": "demo.py",
                    "line": 3,
                    "column": 1,
                    "end_line": None,
                    "end_column": None,
                    "label": "value = build()",
                },
                "reference_targets": [
                    {
                        "action": "open_reference",
                        "path": "demo.py",
                        "line": 3,
                        "column": 1,
                        "end_line": None,
                        "end_column": None,
                        "label": "value = build()",
                    }
                ],
            }
        if method == "symbol.actions":
            return {
                "surface_kind": "symbol_actions",
                "symbol": "build",
                "definitions": [
                    {
                        "action": "open_symbol",
                        "path": "demo.py",
                        "line": 1,
                        "column": 1,
                        "end_line": None,
                        "end_column": None,
                        "label": "function build",
                    }
                ],
                "references": [
                    {
                        "action": "open_reference",
                        "path": "demo.py",
                        "line": 3,
                        "column": 1,
                        "end_line": None,
                        "end_column": None,
                        "label": "value = build()",
                    }
                ],
                "definition_count": 1,
                "reference_count": 1,
                "selected_symbol": "build",
                "selected_definition": {
                    "action": "open_symbol",
                    "path": "demo.py",
                    "line": 1,
                    "column": 1,
                    "end_line": None,
                    "end_column": None,
                    "label": "function build",
                },
                "selected_definition_index": 0,
                "selected_reference": {
                    "action": "open_reference",
                    "path": "demo.py",
                    "line": 3,
                    "column": 1,
                    "end_line": None,
                    "end_column": None,
                    "label": "value = build()",
                },
                "selected_reference_index": 0,
                "navigation_target": {
                    "action": "open_symbol",
                    "path": "demo.py",
                    "line": 1,
                    "column": 1,
                    "end_line": None,
                    "end_column": None,
                    "label": "function build",
                },
            }
        if method == "session.view" and params.get("view") == "task_detail":
            return {
                "view": "task_detail",
                "text": "task detail for task-123",
                "file_context_scope": "task",
                "file_context_file_count": 1,
                "file_context_sources": ["checklist"],
                "file_context_files": [
                    {
                        "path": "claudecode_py/session.py",
                        "source": "checklist",
                        "target_summary": "open_file claudecode_py/session.py:12",
                        "diff_target_count": 1,
                    }
                ],
                "file_context_primary_path": "claudecode_py/session.py",
                "file_context_primary_target": {
                    "action": "open_file",
                    "path": "claudecode_py/session.py",
                    "line": 12,
                    "label": "task file",
                },
                "file_context_primary_diff_targets": {
                    "hunks": [
                        {
                            "action": "open_diff",
                            "path": "claudecode_py/session.py",
                            "line": 20,
                            "label": "diff hunk",
                        }
                    ]
                },
                "checklist_task_id": "task-123",
                "checklist_task_list_id": "session-123",
                "checklist_subject": "Inspect runtime",
                "checklist_description": "Inspect session.py",
                "checklist_active_form": "Inspecting runtime",
                "checklist_status": "in_progress",
                "checklist_owner": "assistant",
                "checklist_blocks": ["task-b"],
                "checklist_blocked_by": ["task-a"],
                "checklist_metadata": {"area": "runtime"},
                "checklist_created_at": "2026-05-01T00:00:00+00:00",
                "checklist_updated_at": "2026-05-02T00:00:00+00:00",
                "checklist_total_tasks": 2,
                "checklist_in_progress_tasks": 1,
                "checklist_recommended_actions": [
                    "session_task_get task-123",
                    "session_task_update task-123 status=completed",
                    "session_task_list",
                ],
                "checklist_duplicate_guard": {
                    "message": "Possible duplicate checklist task. Use existing task \"task-123\" instead of creating a new one.",
                    "matched_task_id": "task-123",
                    "recommended_action": "Call session_task_get for task task-123, then use session_task_update to continue or revise it.",
                },
                "checklist_duplicate_message": "Possible duplicate checklist task. Use existing task \"task-123\" instead of creating a new one.",
                "checklist_duplicate_reason": "Matched existing checklist task by subject, description, and active_form.",
                "checklist_duplicate_matched_task_id": "task-123",
                "checklist_duplicate_recommended_action": "Call session_task_get for task task-123, then use session_task_update to continue or revise it.",
                "checklist_primary_action": "checklist_mark_completed task-123",
                "checklist_secondary_action": "checklist_reopen task-123",
                "checklist_tertiary_action": "session_task_list",
                "checklist_edit_subject_action": "checklist_set_subject task-123",
                "checklist_edit_description_action": "checklist_set_description task-123",
                "checklist_edit_owner_action": "checklist_set_owner task-123",
                "checklist_edit_active_form_action": "checklist_set_active_form task-123",
                "checklist_edit_blocks_action": "checklist_set_blocks task-123",
                "checklist_edit_blocked_by_action": "checklist_set_blocked_by task-123",
                "checklist_edit_metadata_action": "checklist_set_metadata task-123",
                "checklist_action_target": "task-123",
                "selected_checklist_primary_action": "checklist_mark_completed task-123",
                "selected_checklist_secondary_action": "checklist_reopen task-123",
                "selected_checklist_tertiary_action": "session_task_list",
                "selected_checklist_edit_subject_action": "checklist_set_subject task-123",
                "selected_checklist_edit_description_action": "checklist_set_description task-123",
                "selected_checklist_edit_owner_action": "checklist_set_owner task-123",
                "selected_checklist_edit_active_form_action": "checklist_set_active_form task-123",
                "selected_checklist_edit_blocks_action": "checklist_set_blocks task-123",
                "selected_checklist_edit_blocked_by_action": "checklist_set_blocked_by task-123",
                "selected_checklist_edit_metadata_action": "checklist_set_metadata task-123",
                "selected_checklist_target": "task-123",
                "workspace_action": "cleanup",
                "workspace_target": "orphan-agent",
                "workspace_health_before": "orphaned",
                "workspace_health_after": "healthy",
                "workspace_planned_paths": ["C:/tmp/orphan-agent"],
                "workspace_applied_paths": ["C:/tmp/orphan-agent"],
                "workspace_failure_reason": None,
                "workspace_recommended_actions": [
                    "/workspaces list",
                    "/workspaces cleanup",
                    "/workspaces cleanup apply orphan-agent",
                ],
                "workspace_primary_action": "workspace_cleanup_preview",
                "workspace_secondary_action": "workspace_cleanup_apply orphan-agent",
                "workspace_tertiary_action": "/workspaces list",
                "workspace_action_target": "orphan-agent",
                "workspace_health": "healthy",
                "task_surface": "child_execution",
                "execution_mode": "read-only-subagent",
                "execution_policy": "read-only-subagent",
                "execution_policy_source": "subagent",
                "allowed_tools": ["read_file", "bash"],
                "allowed_bash_prefixes": ["git status"],
                "read_only_subagents": True,
            }
        if method == "session.action":
            if params.get("action") == "clear_session_reset":
                return {
                    "text": "Started a fresh local session.\nold_session_id: session-123\nnew_session_id: session-456",
                    "old_session_id": "session-123",
                    "session_id": "session-456",
                    "transcript_path": "C:/tmp/session-456.json",
                }
            return {"text": str(params.get("action") or "ok")}
        if method == "session.change_view" and params.get("view") == "detail":
            return {
                "text": "change detail",
                "file_context_scope": "change",
                "file_context_file_count": 1,
                "file_context_sources": ["change"],
                "file_context_files": [
                    {
                        "path": "demo.py",
                        "source": "change",
                        "target_summary": "open_file demo.py:7",
                        "diff_target_count": 1,
                    }
                ],
                "file_context_primary_path": "demo.py",
                "file_context_primary_target": {
                    "action": "open_file",
                    "path": "demo.py",
                    "line": 7,
                    "label": "changed file",
                },
                "file_context_primary_diff_targets": {
                    "hunks": [
                        {
                            "action": "open_diff",
                            "path": "demo.py",
                            "line": 9,
                            "label": "changed hunk",
                        }
                    ]
                },
            }
        if method == "session.view" and params.get("view") == "active_plan":
            return {
                "text": "active plan summary",
                "file_context_scope": "plan",
                "file_context_file_count": 1,
                "file_context_sources": ["plan"],
                "file_context_files": [
                    {
                        "path": "claudecode_py/runtime/query_loop.py",
                        "source": "plan",
                        "target_summary": "open_file claudecode_py/runtime/query_loop.py:33",
                        "diff_target_count": 0,
                    }
                ],
                "file_context_primary_path": "claudecode_py/runtime/query_loop.py",
                "file_context_primary_target": {
                    "action": "open_file",
                    "path": "claudecode_py/runtime/query_loop.py",
                    "line": 33,
                    "label": "plan file",
                },
                "file_context_primary_diff_targets": None,
            }
        if method == "session.describe":
            return {
                "session_id": str(params.get("session_id") or "session-123"),
                "session_execution_mode": "main",
                "message_count": 0,
                "working_set_scope": "session",
                "working_set_file_count": 1,
                "working_set_sources": ["recent-change"],
                "working_set_files": [
                    {
                        "path": "demo.py",
                        "source": "recent-change",
                        "target_summary": "open_file demo.py:1",
                        "diff_target_count": 1,
                    }
                ],
                "working_set_primary_path": "demo.py",
                "working_set_primary_target": {
                    "action": "open_file",
                    "path": "demo.py",
                    "line": 1,
                    "label": "working set file",
                },
                "working_set_primary_diff_targets": {
                    "hunks": [
                        {
                            "action": "open_diff",
                            "path": "demo.py",
                            "line": 2,
                            "label": "working set diff",
                        }
                    ]
                },
                "symbol_surface_kind": "symbol_actions",
                "symbol_selected_symbol": "build",
                "symbol_match_count": 0,
                "symbol_definition_count": 1,
                "symbol_reference_count": 1,
                "symbol_selected_match_index": None,
                "symbol_selected_definition_index": 0,
                "symbol_selected_reference_index": 0,
                "symbol_matches": [],
                "symbol_definitions": [
                    {"action": "open_symbol", "path": "demo.py", "line": 1, "label": "function build"}
                ],
                "symbol_references": [
                    {"action": "open_reference", "path": "demo.py", "line": 3, "label": "value = build()"}
                ],
                "symbol_selected_definition": {
                    "action": "open_symbol",
                    "path": "demo.py",
                    "line": 1,
                },
                "symbol_selected_reference": {
                    "action": "open_reference",
                    "path": "demo.py",
                    "line": 3,
                },
                "symbol_navigation_target": {
                    "action": "open_symbol",
                    "path": "demo.py",
                    "line": 1,
                },
                "symbol_primary_action": "/symbol open primary",
                "symbol_secondary_action": "/symbol open secondary",
                "symbol_tertiary_action": "/symbol clear",
                "symbol_action_target": "build",
            }
        raise AssertionError(f"unexpected request: {method} {params}")


class RemoteSessionProxyTests(unittest.TestCase):
    def test_task_detail_view_is_cached_between_text_and_metadata_reads(self) -> None:
        proxy = RemoteSessionProxy.__new__(RemoteSessionProxy)
        proxy.client = _FakeBridgeClient()
        proxy.session_id = "session-123"
        proxy.state = SessionState(session_id="session-123")
        proxy._workspace_action_bundle = {}
        proxy._task_detail_cache = {}
        proxy._working_set_metadata = {}

        text = proxy.describe_task_detail("task-123")
        execution_metadata = proxy.task_execution_detail_metadata("task-123")
        metadata = proxy.task_workspace_detail_metadata("task-123")
        checklist_metadata = proxy.checklist_task_detail_metadata("task-123")
        checklist_actions = proxy.checklist_task_action_bundle("task-123")
        file_context = proxy.task_file_context_payload("task-123")

        self.assertEqual(text, "task detail for task-123")
        self.assertIsNotNone(execution_metadata)
        self.assertIsNotNone(metadata)
        self.assertIsNotNone(checklist_metadata)
        self.assertIsNotNone(checklist_actions)
        self.assertIsNotNone(file_context)
        self.assertEqual(execution_metadata["task_surface"], "child_execution")
        self.assertEqual(execution_metadata["execution_mode"], "read-only-subagent")
        self.assertEqual(execution_metadata["execution_policy"], "read-only-subagent")
        self.assertEqual(execution_metadata["execution_policy_source"], "subagent")
        self.assertEqual(execution_metadata["allowed_tools"], ["read_file", "bash"])
        self.assertEqual(execution_metadata["allowed_bash_prefixes"], ["git status"])
        self.assertTrue(execution_metadata["read_only_subagents"])
        self.assertEqual(metadata["workspace_target"], "orphan-agent")
        self.assertEqual(metadata["workspace_planned_paths"], ["C:/tmp/orphan-agent"])
        self.assertEqual(file_context["file_context_primary_path"], "claudecode_py/session.py")
        self.assertEqual(file_context["file_context_primary_target"]["action"], "open_file")
        self.assertEqual(checklist_metadata["checklist_subject"], "Inspect runtime")
        self.assertEqual(checklist_metadata["checklist_status"], "in_progress")
        self.assertEqual(checklist_metadata["checklist_blocks"], ["task-b"])
        self.assertEqual(checklist_metadata["checklist_metadata"], {"area": "runtime"})
        self.assertEqual(checklist_metadata["checklist_duplicate_matched_task_id"], "task-123")
        self.assertIn("session_task_get", checklist_metadata["checklist_duplicate_recommended_action"])
        self.assertEqual(
            checklist_metadata["checklist_recommended_actions"],
            [
                "session_task_get task-123",
                "session_task_update task-123 status=completed",
                "session_task_list",
            ],
        )
        self.assertEqual(checklist_metadata["checklist_primary_action"], "checklist_mark_completed task-123")
        self.assertEqual(checklist_metadata["checklist_edit_subject_action"], "checklist_set_subject task-123")
        self.assertEqual(checklist_metadata["checklist_edit_owner_action"], "checklist_set_owner task-123")
        self.assertEqual(checklist_actions["primary_action"], "checklist_mark_completed task-123")
        self.assertEqual(checklist_actions["secondary_action"], "checklist_reopen task-123")
        self.assertEqual(checklist_actions["edit_subject_action"], "checklist_set_subject task-123")
        self.assertEqual(checklist_actions["edit_owner_action"], "checklist_set_owner task-123")
        self.assertEqual(checklist_actions["edit_active_form_action"], "checklist_set_active_form task-123")
        self.assertEqual(checklist_actions["edit_blocks_action"], "checklist_set_blocks task-123")
        self.assertEqual(checklist_actions["edit_metadata_action"], "checklist_set_metadata task-123")
        self.assertEqual(
            metadata["workspace_recommended_actions"],
            [
                "/workspaces list",
                "/workspaces cleanup",
                "/workspaces cleanup apply orphan-agent",
            ],
        )
        self.assertEqual(
            proxy.client.calls,
            [
                (
                    "session.view",
                    {
                        "session_id": "session-123",
                        "view": "task_detail",
                        "task_id": "task-123",
                    },
                )
            ],
        )

    def test_task_progress_notification_invalidates_task_detail_cache(self) -> None:
        proxy = RemoteSessionProxy.__new__(RemoteSessionProxy)
        proxy.client = _FakeBridgeClient()
        proxy.session_id = "session-123"
        proxy.state = SessionState(session_id="session-123")
        proxy._workspace_action_bundle = {}
        proxy._task_detail_cache = {"task-123": {"text": "cached"}}
        proxy._working_set_metadata = {}
        proxy._sink_lock = Lock()
        proxy._default_live_sink = None
        proxy._transient_live_sink = None
        proxy._ingest_control_event = lambda payload: None  # type: ignore[method-assign]

        proxy._handle_notification(  # type: ignore[arg-type]
            {
                "notification": "session.event",
                "event": {
                    "kind": "task_progress",
                    "message": "cleanup progress 1/2 | target=orphan-agent",
                    "task_id": "task-123",
                },
            }
        )

        self.assertNotIn("task-123", proxy._task_detail_cache)

    def test_runtime_event_from_payload_preserves_task_id(self) -> None:
        event = _runtime_event_from_payload(
            {
                "kind": "task_progress",
                "message": "repair planned | target=missing-agent",
                "task_id": "task-123",
            }
        )

        assert event is not None
        self.assertEqual(event.kind, "task_progress")
        self.assertEqual(event.task_id, "task-123")

    def test_sync_execution_contract_metadata_updates_remote_state(self) -> None:
        proxy = RemoteSessionProxy.__new__(RemoteSessionProxy)
        proxy.state = SessionState(session_id="session-123")
        proxy._working_set_metadata = {}

        proxy._sync_execution_contract_metadata(  # type: ignore[attr-defined]
            {
                "session_execution_mode": "read-only-subagent",
                "session_command_policy_name": "read-only-subagent",
                "session_command_policy_source": "subagent",
                "session_command_policy_allowed_tool_names": ["bash", "read_file"],
                "session_command_policy_allowed_bash_prefixes": ["git status"],
                "session_command_policy_require_read_only_subagents": True,
            }
        )

        self.assertEqual(proxy.state.session_execution_mode, "read-only-subagent")
        self.assertEqual(proxy.state.session_command_policy_name, "read-only-subagent")
        self.assertEqual(proxy.state.session_command_policy_source, "subagent")
        self.assertEqual(proxy.state.session_command_policy_allowed_tool_names, ["bash", "read_file"])
        self.assertEqual(proxy.state.session_command_policy_allowed_bash_prefixes, ["git status"])
        self.assertTrue(proxy.state.session_command_policy_require_read_only_subagents)
        self.assertEqual(
            proxy.execution_contract_payload()["session_command_policy_name"],
            "read-only-subagent",
        )

    def test_sync_task_surface_counts_updates_remote_state(self) -> None:
        proxy = RemoteSessionProxy.__new__(RemoteSessionProxy)
        proxy._task_surface_counts = {}

        proxy._sync_task_surface_counts(  # type: ignore[attr-defined]
            {
                "task_surface_counts": {
                    "checklist": 2,
                    "workspace_maintenance": 1,
                    "child_execution": 3,
                    "background_execution": 1,
                    "active_plan_execution": 0,
                    "other_task": 4,
                }
            }
        )

        self.assertEqual(
            proxy.task_surface_counts_payload(),
            {
                "checklist": 2,
                "workspace_maintenance": 1,
                "child_execution": 3,
                "background_execution": 1,
                "active_plan_execution": 0,
                "other_task": 4,
            },
        )
        self.assertEqual(
            proxy.task_surface_summary_lines(),
            [
                "task_surfaces:",
                "checklist: 2",
                "workspace_maintenance: 1",
                "child_execution: 3",
                "background_execution: 1",
                "active_plan_execution: 0",
                "other_task: 4",
            ],
        )

    def test_remote_symbol_surface_wrappers_return_structured_payloads(self) -> None:
        proxy = RemoteSessionProxy.__new__(RemoteSessionProxy)
        proxy.client = _FakeBridgeClient()
        proxy.session_id = "session-123"

        lookup = proxy.locate_symbol_payload("build")
        references = proxy.collect_references_payload("build")
        actions = proxy.symbol_action_surface_payload("build")

        self.assertEqual(lookup["surface_kind"], "symbol_lookup")
        self.assertEqual(lookup["selected_navigation_target"]["action"], "open_symbol")
        self.assertEqual(references["surface_kind"], "symbol_references")
        self.assertEqual(references["selected_navigation_target"]["action"], "open_reference")
        self.assertEqual(actions["surface_kind"], "symbol_actions")
        self.assertEqual(actions["selected_definition"]["action"], "open_symbol")
        self.assertEqual(actions["selected_reference"]["action"], "open_reference")
        self.assertEqual(actions["navigation_target"]["action"], "open_symbol")

    def test_remote_file_context_wrappers_return_structured_payloads(self) -> None:
        proxy = RemoteSessionProxy.__new__(RemoteSessionProxy)
        proxy.client = _FakeBridgeClient()
        proxy.session_id = "session-123"
        proxy.state = SessionState(session_id="session-123")
        proxy._working_set_metadata = {}
        proxy._task_detail_cache = {}

        proxy._sync_working_set_metadata(  # type: ignore[attr-defined]
            proxy.client.request("session.describe", {"session_id": "session-123"})
        )
        working_set = proxy.working_set_payload()
        task_context = proxy.task_file_context_payload("task-123")
        change_context = proxy.selected_change_detail_metadata(index=0, file_index=0, redo=False)
        plan_context = proxy.active_plan_file_context_payload()

        self.assertEqual(working_set["file_context_primary_path"], "demo.py")
        self.assertEqual(working_set["file_context_primary_target"]["action"], "open_file")
        self.assertIsNotNone(task_context)
        self.assertEqual(task_context["file_context_primary_path"], "claudecode_py/session.py")
        self.assertEqual(change_context["file_context_primary_target"]["action"], "open_file")
        self.assertIsNotNone(plan_context)
        self.assertEqual(
            plan_context["file_context_primary_path"],
            "claudecode_py/runtime/query_loop.py",
        )

    def test_current_symbol_surface_action_bundle_uses_structured_symbol_payload(self) -> None:
        proxy = RemoteSessionProxy.__new__(RemoteSessionProxy)
        proxy._symbol_surface = {
            "surface_kind": "symbol_actions",
            "selected_symbol": "build",
            "selected_definition": {"action": "open_symbol", "path": "demo.py", "line": 1},
            "selected_reference": {"action": "open_reference", "path": "demo.py", "line": 3},
        }

        bundle = proxy.current_symbol_surface_action_bundle()

        self.assertIsNotNone(bundle)
        self.assertEqual(bundle["primary_action"], "/symbol open primary")
        self.assertEqual(bundle["secondary_action"], "/symbol open secondary")
        self.assertEqual(bundle["tertiary_action"], "/symbol clear")
        self.assertEqual(bundle["target"], "build")

    def test_symbol_selection_wrappers_use_session_action(self) -> None:
        proxy = RemoteSessionProxy.__new__(RemoteSessionProxy)
        proxy.client = _FakeBridgeClient()
        proxy.session_id = "session-123"
        proxy.state = SessionState(session_id="session-123")
        proxy._task_detail_cache = {}

        self.assertEqual(proxy.symbol_surface_select_next_definition(), "symbol_surface_select_next_definition")
        self.assertEqual(proxy.symbol_surface_select_prev_reference(), "symbol_surface_select_prev_reference")
        payload = proxy.current_symbol_surface_payload()
        self.assertIsNotNone(payload)
        self.assertEqual(len(payload["definitions"]), 1)
        self.assertEqual(len(payload["references"]), 1)

        self.assertIn(
            ("session.action", {"session_id": "session-123", "action": "symbol_surface_select_next_definition"}),
            proxy.client.calls,
        )
        self.assertIn(
            ("session.action", {"session_id": "session-123", "action": "symbol_surface_select_prev_reference"}),
            proxy.client.calls,
        )

    def test_clear_session_reset_updates_remote_proxy_session_identity(self) -> None:
        proxy = RemoteSessionProxy.__new__(RemoteSessionProxy)
        proxy.client = _FakeBridgeClient()
        proxy.session_id = "session-123"
        proxy.state = SessionState(
            session_id="session-123",
            context_summary="Earlier summary",
            messages=[{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
        )
        proxy._workspace_action_bundle = {}
        proxy._task_detail_cache = {"task-1": {"text": "cached"}}
        proxy._working_set_metadata = {"working_set_file_count": 1}
        proxy._task_surface_counts = {}
        proxy._checklist_duplicate_guard = None
        proxy._symbol_surface = {"surface_kind": "symbol_actions"}

        rendered = proxy.clear_session_reset()

        self.assertIn("Started a fresh local session.", rendered)
        self.assertEqual(proxy.session_id, "session-456")
        self.assertEqual(proxy.state.session_id, "session-456")
        self.assertEqual(proxy.state.messages, [])
        self.assertIsNone(proxy.state.context_summary)
        self.assertEqual(proxy._task_detail_cache, {})
        self.assertIn(
            ("session.action", {"session_id": "session-123", "action": "clear_session_reset"}),
            proxy.client.calls,
        )
        self.assertIn(
            ("session.describe", {"session_id": "session-456"}),
            proxy.client.calls,
        )


if __name__ == "__main__":
    unittest.main()
