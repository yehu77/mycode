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
        self.message_count = 0
        self.context_summary: str | None = None
        self.last_memory_operation: str | None = None

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
            if params.get("action") == "describe_rewind":
                selector = str(params.get("args") or "").strip()
                if selector:
                    return {
                        "text": "rewind boundary:\nkind: compact\nselector: show 1",
                        "rewind_mode": "show",
                        "selector": 1,
                        "boundary_id": "hb-compact-1",
                        "boundary_kind": "compact",
                        "boundary_kind_label": "compact boundary",
                        "trigger": "manual",
                        "created_at": "2026-05-19T00:00:00+00:00",
                        "summary": "Compacted older turns",
                        "rewindable": True,
                        "message_count_before": 4,
                        "message_count_after": 2,
                        "context_summary_chars_before": 15,
                        "context_summary_chars_after": 120,
                        "snapshot_available": True,
                        "snapshot_message_count": 4,
                        "snapshot_summary_chars": 15,
                        "lineage_summary": "pre-compact restore point",
                        "restore_message_delta_current": 2,
                        "restore_summary_chars_delta_current": 0,
                        "restore_message_count_current": 2,
                        "restore_summary_chars_current": 15,
                        "targets_pre_compact_state": True,
                        "targets_post_resume_state": False,
                        "restore_effect_summary": "Conversation messages and compacted context summary were restored from a selected boundary snapshot.",
                        "workflow_surface_policy": {
                            "task_plan_file_focus": "cleared",
                            "advisor_review_state": "preserved",
                            "symbol_surface": "preserved",
                            "advisor_configuration": "preserved",
                        },
                        "show_action": "/rewind show 1",
                        "apply_action": "/rewind apply 1",
                    }
                return {
                    "text": "rewind boundaries:\n1. compact | snapshot_messages=4",
                    "rewind_mode": "list",
                    "rewindable_boundary_count": 1,
                    "default_rewind_selector": "1",
                }
            if params.get("action") == "rewind_to_boundary":
                self.message_count = 4
                self.context_summary = "Earlier summary"
                self.last_memory_operation = "rewind"
                return {
                    "text": "conversation rewound:\ntarget boundary kind: compact\n- task/plan/file focus: cleared"
                }
            if params.get("action") == "clear_session_reset":
                self.message_count = 0
                self.context_summary = None
                self.last_memory_operation = "clear_session"
                return {
                    "text": "Started a fresh local session.\nold_session_id: session-123\nnew_session_id: session-456",
                    "old_session_id": "session-123",
                    "session_id": "session-456",
                    "transcript_path": "C:/tmp/session-456.json",
                }
            if params.get("action") == "clear_history":
                self.message_count = 0
                self.context_summary = None
                self.last_memory_operation = "clear_history"
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
                "message_count": self.message_count,
                "context_summary": self.context_summary,
                "context_summary_present": bool(self.context_summary),
                "context_summary_chars": len(self.context_summary or ""),
                "history_boundary_count": 2 if self.context_summary else 0,
                "rewindable_history_boundary_count": 1 if self.context_summary else 0,
                "compact_boundary_count": 1 if self.context_summary else 0,
                "last_history_boundary_kind": "rewind" if self.context_summary else None,
                "latest_rewindable_boundary_kind": "compact" if self.context_summary else None,
                "default_rewind_selector": "1" if self.context_summary else None,
                "rewind_show_action": "/rewind show 1" if self.context_summary else None,
                "rewind_apply_action": "/rewind apply 1" if self.context_summary else None,
                "compaction_state": "warning" if self.context_summary else "ok",
                "would_compact": bool(self.context_summary),
                "should_warn": bool(self.context_summary),
                "should_auto_compact": False,
                "compaction_reason": (
                    "context summary chars 15 >= warning threshold 12" if self.context_summary else None
                ),
                "message_limit": 200,
                "warning_message_threshold": 150,
                "context_summary_limit": 16,
                "warning_summary_threshold": 12,
                "auto_summary_threshold": 14,
                "compact_preview_action": "/compact preview" if self.context_summary else None,
                "compact_apply_action": "/compact" if self.context_summary else None,
                "memory_budget_state": "warning" if self.context_summary else "ok",
                "memory_budget_reason": (
                    "context summary chars 15 >= warning threshold 12"
                    if self.context_summary
                    else "within limits"
                ),
                "memory_context_tokens_estimated": 42,
                "memory_context_percentage": 1.0,
                "memory_context_token_source": "provider",
                "memory_last_turn_token_count": 42,
                "memory_last_turn_token_source": "provider",
                "memory_provider_usage_seen": True,
                "memory_budget_pressure": "warning" if self.context_summary else "ok",
                "memory_compact_lifecycle": "manual" if self.context_summary else "none",
                "memory_should_stop": False,
                "memory_last_operation": self.last_memory_operation,
                "memory_last_operation_task_plan_file_focus": (
                    "cleared" if self.last_memory_operation in {"rewind", "clear_session"} else None
                ),
                "background_session_id": "bg-123",
                "background_session_source": "live_background",
                "background_continuation_category": "live attachable",
                "background_live_attachable": True,
                "background_saved_resumable": False,
                "background_inactive_only": False,
                "background_primary_action": "pyclaude attach bg-123",
                "background_secondary_action": "pyclaude logs bg-123 summary",
                "background_attach_action": "pyclaude attach bg-123",
                "background_resume_action": "pyclaude --resume-session session-123 repl",
                "background_logs_action": "pyclaude logs bg-123 summary",
                "background_history_action": "/history messages",
                "background_sessions_action": "pyclaude sessions --limit 10",
                "background_current_workflow_summary": "attachable live background session",
                "background_task_surface_counts": {
                    "background_execution": 1,
                    "other_task": 1,
                },
                "background_task_surface_summary": "background_execution:1,other_task:1",
                "background_background_execution_count": 1,
                "background_active_plan_execution_count": 0,
                "background_primary_task": {
                    "task_id": "task-bg",
                    "status": "running",
                    "kind": "agent",
                    "description": "Finish background work",
                    "progress_summary": "Waiting for attach",
                    "surface_kind": "background_execution",
                    "background_session_id": "bg-123",
                    "background_reverse_hint": "pyclaude ps bg-123 | pyclaude logs bg-123 summary",
                },
                "background_primary_task_action": "/task show task-bg",
                "background_recent_change_count": 1,
                "background_recent_activity": "Waiting for attach",
                "background_recent_activity_kind": "task_progress",
                "background_last_tool": "agent",
                "background_last_tool_input": '{"prompt":"continue"}',
                "background_last_tool_summary": "ok (25ms)",
                "background_token_count": 42,
                "background_token_count_source": "provider",
                "background_tool_use_count": 1,
                "background_message_count": 3,
                "background_progress_summary": "Waiting for attach",
                "background_progress_updated_at": "2026-05-16T00:00:00+00:00",
                "background_completion_state": "running",
                "background_completion_summary": "Waiting for attach",
                "background_failure_reason": None,
                "background_result_pointer": "/task show task-bg",
                "background_transcript_pointer": "pyclaude --resume-session session-123 repl",
                "background_working_set_file_count": 1,
                "background_focused_file": "demo.py",
                "background_focused_file_source": "working_set",
                "background_has_active_plan": False,
                "background_active_plan_id": None,
                "background_active_plan_summary": None,
                "background_registry_count": 2,
                "background_registry_entries": [
                    {
                        "background_session_id": "bg-123",
                        "status": "running",
                        "background_continuation_category": "live attachable",
                        "background_primary_action": "pyclaude attach bg-123",
                    },
                    {
                        "background_session_id": "bg-456",
                        "status": "completed",
                        "background_continuation_category": "saved resumable",
                        "background_primary_action": "pyclaude --resume-session session-456 repl",
                    },
                ],
                "background_registry_selected_bg_id": "bg-123",
                "background_registry_selected_status": "running",
                "background_registry_selected_continuation_category": "live attachable",
                "background_registry_selected_workflow_summary": "attachable live background session",
                "background_registry_selected_primary_task": {
                    "task_id": "task-bg",
                    "status": "running",
                    "description": "Finish background work",
                },
                "background_registry_selected_active_plan_summary": None,
                "background_registry_selected_focused_file": "demo.py",
                "background_registry_selected_recent_activity": "Waiting for attach",
                "background_registry_selected_recent_activity_kind": "task_progress",
                "background_registry_selected_progress_summary": "Waiting for attach",
                "background_registry_selected_last_tool_input": '{"prompt":"continue"}',
                "background_registry_selected_last_tool_summary": "ok (25ms)",
                "background_registry_selected_token_count": 42,
                "background_registry_selected_token_count_source": "provider",
                "background_registry_selected_completion_state": "running",
                "background_registry_selected_completion_summary": "Waiting for attach",
                "background_registry_primary_action": "pyclaude attach bg-123",
                "background_registry_secondary_action": "pyclaude logs bg-123 summary",
                "background_registry_attach_action": "pyclaude attach bg-123",
                "background_registry_resume_action": "pyclaude --resume-session session-123 repl",
                "background_registry_logs_action": "pyclaude logs bg-123 summary",
                "background_registry_selection_strategy": "live_attachable_first",
                "background_handoff_count": 1,
                "background_handoff_entries": [
                    {
                        "background_session_id": "bg-456",
                        "background_completion_state": "completed",
                        "background_completion_summary": "Background session completed.",
                        "background_handoff_transcript_action": "pyclaude logs bg-456 summary",
                        "background_handoff_task_action": "/task show task-bg",
                        "background_handoff_changes_action": "pyclaude --resume-session session-456 repl | /changes working-set",
                        "background_handoff_resume_action": "pyclaude --resume-session session-456 repl",
                    }
                ],
                "background_handoff_selected_bg_id": "bg-456",
                "background_handoff_selected_completion_state": "completed",
                "background_handoff_selected_completion_summary": "Background session completed.",
                "background_handoff_selected_failure_reason": None,
                "background_handoff_selected_primary_task": {"task_id": "task-bg", "status": "completed"},
                "background_handoff_transcript_action": "pyclaude logs bg-456 summary",
                "background_handoff_task_action": "/task show task-bg",
                "background_handoff_changes_action": "pyclaude --resume-session session-456 repl | /changes working-set",
                "background_handoff_resume_action": "pyclaude --resume-session session-456 repl",
                "background_handoff_selection_strategy": "recent_completion_first",
                "status_session_id": str(params.get("session_id") or "session-123"),
                "status_provider": "openai-compatible",
                "status_model": "gpt-test",
                "status_advisor_model": "gpt-test",
                "status_advisor_mode": "off",
                "status_mode": "main",
                "status_context_usage": "42 / 4096 (1.0%)",
                "status_context_usage_tokens": 42,
                "status_context_usage_max_tokens": 4096,
                "status_context_usage_percentage": 1.0,
                "status_memory_summary": self.last_memory_operation or "none",
                "status_memory_compaction": "ok",
                "status_memory_last_operation": self.last_memory_operation or "none",
                "status_memory_boundary_count": 2 if self.context_summary else 0,
                "status_budget_state": "warning" if self.context_summary else "ok",
                "status_budget_reason": (
                    "context summary chars 15 >= warning threshold 12"
                    if self.context_summary
                    else "within limits"
                ),
                "status_context_token_source": "provider",
                "status_last_turn_token_count": 42,
                "status_last_turn_token_source": "provider",
                "status_provider_usage_seen": True,
                "status_budget_pressure": "warning" if self.context_summary else "ok",
                "status_compact_lifecycle": "manual" if self.context_summary else "none",
                "status_runtime_progress_summary": "read_file: waiting for approval (read)",
                "status_runtime_progress_kind": "tool_waiting_for_approval",
                "status_runtime_active_tool_name": "read_file",
                "status_runtime_active_tool_status": "waiting_for_approval",
                "status_runtime_active_tool_input": '{"path":"demo.py"}',
                "status_runtime_last_tool_name": "read_file",
                "status_runtime_last_tool_status": "waiting_for_approval",
                "status_runtime_last_tool_summary": "waiting for approval (read)",
                "status_runtime_parallel_batch_active": True,
                "status_runtime_parallel_batch_size": 2,
                "status_runtime_last_result_summary": "ok results=2",
                "status_runtime_compact_recovery_summary": "starting compact recovery after prompt-too-long",
                "status_background_summary": "Background session completed.",
                "status_background_notification_count": 1,
                "status_background_latest_handoff": "bg-456",
                "status_working_set_summary": "mix: diff_backed=1 context_only=0 explicit=0 task=0 plan=0 change=1",
                "status_working_set_file_count": 1,
                "status_focused_file_summary": "demo.py (change)",
                "status_focused_file_path": "demo.py",
                "status_focused_file_source": "change",
                "status_plan_summary": "none",
                "status_plan_goal": None,
                "status_task_summary": "background_execution=1, other_task=1",
                "status_active_task_count": 2,
                "status_task_surface_summary": "background_execution=1, other_task=1",
                "status_project_context_summary": "memory=none skills=0 plugins=0",
                "status_project_context_reload_health": "latest reload: none",
                "status_project_context_issue": "none",
                "status_skills_health": "loaded=0 enabled=0 manual_enabled=0 manual_disabled=0",
                "status_skill_registry_summary": "registered=0 enabled=0 inactive=0 diagnostics=0",
                "status_skill_prompt_summary": "auto_enabled=0 manual_enabled=0 inactive=0",
                "status_skill_reload_state": "latest reload: none",
                "status_skill_manual_overrides": "enabled=0 disabled=0",
                "status_skill_diagnostics": 0,
                "status_plugins_health": "registered=0 enabled=0 diagnostics=0",
                "status_plugin_registry_summary": "registered=0 enabled=0 disabled=0 diagnostics=0",
                "status_plugin_reload_state": "latest reload: none",
                "status_plugin_manual_overrides": "enabled=0 disabled=0",
                "status_mcp_health": "servers=0 connected=0 failed=0 retrying=0",
                "status_mcp_issue": "none",
                "status_permission_mode": "default",
                "status_permission_summary": "mode=default workspace_rules=0 session_rules=0",
                "status_workspace_anomaly": "none",
                "status_runtime_health_alert": "none",
                "status_runtime_health_source": "none",
                "status_workspace_summary": "mode=main health=healthy focused=demo.py",
                "status_workspace_mode": "main",
                "status_workspace_health": "healthy",
                "workspace_surface": {
                    "workspace_summary": "mode=main health=healthy label=none",
                    "workspace_mode": "main",
                    "workspace_label": None,
                    "workspace_health": "healthy",
                    "workspace_created_at": None,
                    "workspace_original_cwd": "C:/tmp/project",
                    "workspace_effective_cwd": "C:/tmp/project",
                    "workspace_effective_cwd_exists": True,
                    "workspace_cleanup_status": "none",
                    "workspace_cleanup_error": None,
                    "workspace_unavailable": False,
                    "workspace_unavailable_reason": None,
                    "workspace_fallback_cwd": None,
                    "workspace_anomaly_summary": "none",
                    "workspace_recovery_summary": "/workspaces list",
                    "workspace_recommended_actions": ["/workspaces list"],
                    "workspace_action_bundle": {
                        "primary_action": "none",
                        "secondary_action": "none",
                        "tertiary_action": "/workspaces list",
                        "target": "session-123",
                        "workspace_health": "healthy",
                    },
                    "workspace_action_groups": {
                        "inspect_current_workspace": ["/workspaces current"],
                        "inspect_workspace_inventory": ["/workspaces list"],
                        "workspace_recovery": ["/workspaces list"],
                    },
                },
                "status_action_groups": {
                    "go_to_focused_file": ["/files focused"],
                    "inspect_changes": ["/changes working-set"],
                },
                "status_explicit_context_entry_count": 0,
                "status_unresolved_explicit_context_entry_count": 0,
                "status_next_actions": [
                    "/files focused",
                    "/changes working-set",
                    "/tasks active",
                    "/plan",
                    "/history all",
                    "/rewind",
                    "pyclaude ps",
                    "/project-context",
                    "/workspaces current",
                ],
                "plugin_surface": {
                    "plugin_registry_summary": "registered=0 enabled=0 disabled=0 diagnostics=0",
                    "plugin_registry_count": 0,
                    "plugin_enabled_count": 0,
                    "plugin_disabled_count": 0,
                    "plugin_diagnostic_count": 0,
                    "plugin_manual_enabled_count": 0,
                    "plugin_manual_disabled_count": 0,
                    "plugin_builtin_count": 0,
                    "plugin_project_local_count": 0,
                    "plugin_enabled_names": [],
                    "plugin_disabled_names": [],
                    "plugin_manual_enabled_names": [],
                    "plugin_manual_disabled_names": [],
                    "plugin_entries": [],
                    "plugin_diagnostics": [],
                    "plugin_selected_name": None,
                    "plugin_selected_summary": "none",
                    "plugin_reload_state": {
                        "timestamp": "unknown",
                        "plugin_state_changed": False,
                        "plugin_registry_changed": False,
                        "enabled_plugin_set_changed": False,
                        "plugin_diagnostics_changed": False,
                        "plugin_contributions_changed": False,
                        "error": None,
                        "summary": "latest reload: none",
                    },
                    "plugin_action_groups": {
                        "inspect_plugin_registry": ["/plugins"],
                        "inspect_project_context_plugins": ["/project-context plugins"],
                        "inspect_plugin_reload_state": ["/project-context reload-status", "/context-refresh"],
                        "inspect_selected_plugin": [],
                        "toggle_selected_plugin": [],
                    },
                },
                "skills_surface": {
                    "skill_registry_summary": "registered=0 enabled=0 inactive=0 diagnostics=0",
                    "skill_registry_count": 0,
                    "skill_enabled_count": 0,
                    "skill_disabled_count": 0,
                    "skill_inactive_count": 0,
                    "skill_diagnostic_count": 0,
                    "skill_auto_enabled_count": 0,
                    "skill_manual_enabled_count": 0,
                    "skill_manual_disabled_count": 0,
                    "skill_project_local_count": 0,
                    "skill_plugin_contributed_count": 0,
                    "skill_builtin_count": 0,
                    "skill_entries": [],
                    "skill_diagnostics": [],
                    "skill_selected_name": None,
                    "skill_selected_summary": "none",
                    "skill_reload_state": {
                        "summary": "latest reload: none",
                        "skill_state_changed": False,
                        "enabled_skill_set_changed": False,
                        "skill_diagnostics_changed": False,
                        "skill_resolution_changed": False,
                        "skill_content_changed": False,
                        "error": None,
                    },
                    "skill_action_groups": {
                        "inspect_skill_registry": ["/skills"],
                        "inspect_project_context_skills": ["/project-context skills"],
                        "inspect_skill_reload_state": ["/project-context reload-status", "/skills-reload"],
                        "inspect_selected_skill": [],
                        "toggle_selected_skill": [],
                    },
                },
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
                "file_context_surface": {
                    "working_set": {
                        "file_context_scope": "session",
                        "file_context_file_count": 1,
                        "file_context_sources": ["recent-change"],
                        "file_context_files": [
                            {
                                "path": "demo.py",
                                "source": "recent-change",
                                "target_summary": "open_file demo.py:1",
                                "diff_target_count": 1,
                            }
                        ],
                        "file_context_primary_path": "demo.py",
                        "file_context_primary_target": {
                            "action": "open_file",
                            "path": "demo.py",
                            "line": 1,
                            "label": "working set file",
                        },
                        "file_context_primary_diff_targets": {
                            "hunks": [
                                {
                                    "action": "open_diff",
                                    "path": "demo.py",
                                    "line": 2,
                                    "label": "working set diff",
                                }
                            ]
                        },
                    },
                    "working_set_summary": "mix: diff_backed=1 context_only=0 explicit=0 task=0 plan=0 change=1",
                    "focused_file": {
                        "source": "working_set",
                        "scope": "session",
                        "index": 0,
                        "file_count": 1,
                        "path": "demo.py",
                        "scope_reasons": ["recent change"],
                        "context_origin": "automatic-only",
                        "has_related_change": True,
                        "has_diff_hunks": True,
                        "is_context_only": False,
                        "primary_target": {
                            "action": "open_file",
                            "path": "demo.py",
                            "line": 1,
                            "label": "working set file",
                        },
                        "secondary_target": {
                            "action": "open_diff",
                            "path": "demo.py",
                            "line": 2,
                            "label": "working set diff",
                        },
                        "summary": "source=working_set path=demo.py target=open_file demo.py:1 context_origin=automatic-only",
                    },
                    "explicit_context": {
                        "entry_count": 0,
                        "unresolved_entry_count": 0,
                        "explicit_file_count": 0,
                        "explicit_only_file_count": 0,
                        "automatic_file_count": 1,
                        "overlapping_file_count": 0,
                        "compare_summary_lines": [],
                    },
                    "file_action_groups": {
                        "inspect_focused_file": ["/files focused"],
                        "inspect_focused_diff": ["/diff focused"],
                    },
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

    def test_runtime_event_from_payload_parses_extended_fields(self) -> None:
        event = _runtime_event_from_payload(
            {
                "kind": "tool_waiting_for_approval",
                "message": '{"path":"demo.py"}',
                "tool_name": "read_file",
                "tool_call_id": "tool-1",
                "approval_risk_level": "read",
                "batch_size": 2,
                "batch_parallel": True,
                "result_count": 1,
                "budget_state": "warning",
                "budget_reason": "message count 6 >= warning threshold 6",
                "compaction_trigger": "recovery",
            }
        )

        assert event is not None
        self.assertEqual(event.kind, "tool_waiting_for_approval")
        self.assertEqual(event.approval_risk_level, "read")
        self.assertEqual(event.batch_size, 2)
        self.assertTrue(event.batch_parallel)
        self.assertEqual(event.result_count, 1)
        self.assertEqual(event.budget_state, "warning")
        self.assertEqual(event.budget_reason, "message count 6 >= warning threshold 6")
        self.assertEqual(event.compaction_trigger, "recovery")

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

    def test_sync_background_metadata_updates_remote_state(self) -> None:
        proxy = RemoteSessionProxy.__new__(RemoteSessionProxy)
        proxy._background_metadata = {}

        proxy._sync_background_metadata(  # type: ignore[attr-defined]
            _FakeBridgeClient().request("session.describe", {"session_id": "session-123"})
        )

        self.assertEqual(proxy.background_surface_payload()["background_session_id"], "bg-123")
        self.assertEqual(
            proxy.background_surface_payload()["background_continuation_category"],
            "live attachable",
        )
        self.assertEqual(
            proxy.background_surface_payload()["background_current_workflow_summary"],
            "attachable live background session",
        )
        self.assertEqual(
            proxy.background_surface_payload()["background_primary_task"]["task_id"],
            "task-bg",
        )
        self.assertEqual(
            proxy.background_surface_payload()["background_attach_action"],
            "pyclaude attach bg-123",
        )
        self.assertEqual(
            proxy.background_surface_payload()["background_progress_summary"],
            "Waiting for attach",
        )
        self.assertEqual(
            proxy.background_surface_payload()["background_token_count"],
            42,
        )
        self.assertEqual(
            proxy.background_surface_payload()["background_last_tool_input"],
            '{"prompt":"continue"}',
        )
        self.assertEqual(
            proxy.background_surface_payload()["background_completion_state"],
            "running",
        )

    def test_sync_background_registry_metadata_updates_remote_state(self) -> None:
        proxy = RemoteSessionProxy.__new__(RemoteSessionProxy)
        proxy._background_registry_metadata = {}

        proxy._sync_background_registry_metadata(  # type: ignore[attr-defined]
            _FakeBridgeClient().request("session.describe", {"session_id": "session-123"})
        )

        self.assertEqual(proxy.background_registry_payload()["background_registry_count"], 2)
        self.assertEqual(
            proxy.background_registry_payload()["background_registry_selected_bg_id"],
            "bg-123",
        )
        self.assertEqual(
            proxy.background_registry_payload()["background_registry_primary_action"],
            "pyclaude attach bg-123",
        )
        self.assertEqual(
            proxy.background_registry_payload()["background_registry_selected_progress_summary"],
            "Waiting for attach",
        )
        self.assertEqual(
            proxy.background_registry_payload()["background_registry_selected_token_count"],
            42,
        )
        self.assertEqual(
            proxy.background_registry_payload()["background_registry_selected_last_tool_summary"],
            "ok (25ms)",
        )
        self.assertEqual(
            proxy.background_registry_payload()["background_registry_entries"][0]["background_session_id"],
            "bg-123",
        )

    def test_sync_background_handoff_metadata_updates_remote_state(self) -> None:
        proxy = RemoteSessionProxy.__new__(RemoteSessionProxy)
        proxy._background_handoff_metadata = {}

        proxy._sync_background_handoff_metadata(  # type: ignore[attr-defined]
            _FakeBridgeClient().request("session.describe", {"session_id": "session-123"})
        )

        self.assertEqual(proxy.background_handoff_payload()["background_handoff_count"], 1)
        self.assertEqual(
            proxy.background_handoff_payload()["background_handoff_selected_bg_id"],
            "bg-456",
        )
        self.assertEqual(
            proxy.background_handoff_payload()["background_handoff_transcript_action"],
            "pyclaude logs bg-456 summary",
        )

    def test_sync_status_metadata_updates_remote_state(self) -> None:
        proxy = RemoteSessionProxy.__new__(RemoteSessionProxy)
        proxy._status_metadata = {}
        proxy._memory_metadata = {}
        client = _FakeBridgeClient()
        client.context_summary = "Compacted summary"

        described = client.request("session.describe", {"session_id": "session-123"})
        proxy._sync_status_metadata(described)  # type: ignore[attr-defined]
        proxy._sync_memory_metadata(described)  # type: ignore[attr-defined]

        self.assertEqual(proxy.status_surface_payload()["status_session_id"], "session-123")
        self.assertEqual(proxy.status_surface_payload()["status_provider"], "openai-compatible")
        self.assertEqual(proxy.status_surface_payload()["status_model"], "gpt-test")
        self.assertEqual(proxy.status_surface_payload()["status_context_usage_tokens"], 42)
        self.assertEqual(proxy.status_surface_payload()["status_budget_state"], "warning")
        self.assertEqual(
            proxy.status_surface_payload()["status_budget_reason"],
            "context summary chars 15 >= warning threshold 12",
        )
        self.assertEqual(proxy.status_surface_payload()["status_context_token_source"], "provider")
        self.assertEqual(proxy.status_surface_payload()["status_last_turn_token_count"], 42)
        self.assertEqual(proxy.status_surface_payload()["status_last_turn_token_source"], "provider")
        self.assertTrue(proxy.status_surface_payload()["status_provider_usage_seen"])
        self.assertEqual(proxy.status_surface_payload()["status_budget_pressure"], "warning")
        self.assertEqual(proxy.status_surface_payload()["status_compact_lifecycle"], "manual")
        self.assertEqual(
            proxy.status_surface_payload()["status_runtime_progress_summary"],
            "read_file: waiting for approval (read)",
        )
        self.assertEqual(
            proxy.status_surface_payload()["status_runtime_progress_kind"],
            "tool_waiting_for_approval",
        )
        self.assertEqual(proxy.status_surface_payload()["status_runtime_active_tool_name"], "read_file")
        self.assertEqual(
            proxy.status_surface_payload()["status_runtime_active_tool_status"],
            "waiting_for_approval",
        )
        self.assertEqual(
            proxy.status_surface_payload()["status_runtime_active_tool_input"],
            '{"path":"demo.py"}',
        )
        self.assertEqual(proxy.status_surface_payload()["status_runtime_last_tool_name"], "read_file")
        self.assertEqual(
            proxy.status_surface_payload()["status_runtime_last_tool_status"],
            "waiting_for_approval",
        )
        self.assertEqual(
            proxy.status_surface_payload()["status_runtime_last_tool_summary"],
            "waiting for approval (read)",
        )
        self.assertTrue(proxy.status_surface_payload()["status_runtime_parallel_batch_active"])
        self.assertEqual(proxy.status_surface_payload()["status_runtime_parallel_batch_size"], 2)
        self.assertEqual(
            proxy.status_surface_payload()["status_runtime_last_result_summary"],
            "ok results=2",
        )
        self.assertEqual(
            proxy.status_surface_payload()["status_runtime_compact_recovery_summary"],
            "starting compact recovery after prompt-too-long",
        )
        self.assertEqual(proxy.status_surface_payload()["status_focused_file_path"], "demo.py")
        self.assertEqual(proxy.status_surface_payload()["status_mcp_health"], "servers=0 connected=0 failed=0 retrying=0")
        self.assertEqual(proxy.status_surface_payload()["status_permission_summary"], "mode=default workspace_rules=0 session_rules=0")
        self.assertEqual(proxy.status_surface_payload()["status_skill_registry_summary"], "registered=0 enabled=0 inactive=0 diagnostics=0")
        self.assertEqual(proxy.status_surface_payload()["status_skill_prompt_summary"], "auto_enabled=0 manual_enabled=0 inactive=0")
        self.assertEqual(proxy.status_surface_payload()["status_plugin_registry_summary"], "registered=0 enabled=0 disabled=0 diagnostics=0")
        self.assertEqual(proxy.status_surface_payload()["status_action_groups"]["go_to_focused_file"], ["/files focused"])
        self.assertIn("/files focused", proxy.status_surface_payload()["status_next_actions"])
        self.assertEqual(proxy.memory_surface_payload()["memory_budget_state"], "warning")
        self.assertEqual(
            proxy.memory_surface_payload()["memory_budget_reason"],
            "context summary chars 15 >= warning threshold 12",
        )
        self.assertEqual(proxy.memory_surface_payload()["memory_context_token_source"], "provider")
        self.assertEqual(proxy.memory_surface_payload()["memory_last_turn_token_count"], 42)
        self.assertEqual(proxy.memory_surface_payload()["memory_last_turn_token_source"], "provider")
        self.assertTrue(proxy.memory_surface_payload()["memory_provider_usage_seen"])
        self.assertEqual(proxy.memory_surface_payload()["memory_budget_pressure"], "warning")
        self.assertEqual(proxy.memory_surface_payload()["memory_compact_lifecycle"], "manual")
        self.assertFalse(proxy.memory_surface_payload()["memory_should_stop"])
        self.assertEqual(proxy.skills_surface_payload()["skill_registry_summary"], "registered=0 enabled=0 inactive=0 diagnostics=0")
        self.assertEqual(proxy.skills_surface_payload()["skill_action_groups"]["inspect_skill_registry"], ["/skills"])
        self.assertEqual(proxy.plugin_surface_payload()["plugin_registry_summary"], "registered=0 enabled=0 disabled=0 diagnostics=0")
        self.assertEqual(proxy.plugin_surface_payload()["plugin_action_groups"]["inspect_plugin_registry"], ["/plugins"])

    def test_sync_workspace_and_file_context_surface_metadata_updates_remote_state(self) -> None:
        proxy = RemoteSessionProxy.__new__(RemoteSessionProxy)
        proxy._workspace_surface_metadata = {}
        proxy._file_context_surface_metadata = {}

        described = _FakeBridgeClient().request("session.describe", {"session_id": "session-123"})
        proxy._sync_workspace_surface_metadata(described)  # type: ignore[attr-defined]
        proxy._sync_file_context_surface_metadata(described)  # type: ignore[attr-defined]

        self.assertEqual(
            proxy.workspace_surface_payload()["workspace_action_bundle"]["primary_action"],
            "none",
        )
        self.assertEqual(
            proxy.workspace_surface_payload()["workspace_recovery_summary"],
            "/workspaces list",
        )
        self.assertIn(
            "/workspaces list",
            proxy.workspace_surface_payload()["workspace_action_groups"]["workspace_recovery"],
        )
        self.assertEqual(
            proxy.file_context_surface_payload()["working_set"]["file_context_primary_path"],
            "demo.py",
        )
        self.assertEqual(
            proxy.file_context_surface_payload()["focused_file"]["path"],
            "demo.py",
        )

    def test_background_followup_wrappers_use_session_action(self) -> None:
        proxy = RemoteSessionProxy.__new__(RemoteSessionProxy)
        proxy.client = _FakeBridgeClient()
        proxy.session_id = "session-123"
        proxy.state = SessionState(session_id="session-123")
        proxy._memory_metadata = {}
        proxy._background_metadata = {}
        proxy._background_registry_metadata = {}
        proxy._workspace_action_bundle = {}
        proxy._task_detail_cache = {}
        proxy._working_set_metadata = {}
        proxy._task_surface_counts = {}
        proxy._checklist_duplicate_guard = None
        proxy._symbol_surface = None

        proxy.send_background_followup("bg-123", "please continue")
        proxy.queue_background_message("bg-123", "queue this")
        proxy.cancel_pending_background_followup("bg-123")

        self.assertIn(
            (
                "session.action",
                {
                    "session_id": "session-123",
                    "action": "background_send_followup",
                    "bg_id": "bg-123",
                    "prompt": "please continue",
                },
            ),
            proxy.client.calls,
        )
        self.assertIn(
            (
                "session.action",
                {
                    "session_id": "session-123",
                    "action": "background_queue_message",
                    "bg_id": "bg-123",
                    "prompt": "queue this",
                },
            ),
            proxy.client.calls,
        )
        self.assertIn(
            (
                "session.action",
                {
                    "session_id": "session-123",
                    "action": "background_cancel_pending_followup",
                    "bg_id": "bg-123",
                },
            ),
            proxy.client.calls,
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
        self.assertEqual(proxy.memory_surface_payload()["memory_last_operation"], "clear_session")
        self.assertEqual(proxy._task_detail_cache, {})
        self.assertIn(
            ("session.action", {"session_id": "session-123", "action": "clear_session_reset"}),
            proxy.client.calls,
        )
        self.assertIn(
            ("session.describe", {"session_id": "session-456"}),
            proxy.client.calls,
        )

    def test_rewind_actions_sync_remote_proxy_history_state(self) -> None:
        proxy = RemoteSessionProxy.__new__(RemoteSessionProxy)
        proxy.client = _FakeBridgeClient()
        proxy.session_id = "session-123"
        proxy.state = SessionState(
            session_id="session-123",
            context_summary="Compacted summary",
            messages=[{"role": "assistant", "content": [{"type": "text", "text": "cached"}]}],
        )
        proxy._workspace_action_bundle = {}
        proxy._task_detail_cache = {"task-1": {"text": "cached"}}
        proxy._working_set_metadata = {}
        proxy._task_surface_counts = {}
        proxy._checklist_duplicate_guard = None
        proxy._symbol_surface = None

        rewind_list = proxy.describe_rewind()
        rewind_show = proxy.describe_rewind("show 1")
        rewind_preview = proxy.rewind_boundary_preview_payload("1")
        rewound = proxy.rewind_to_boundary("1")

        self.assertIn("rewind boundaries:", rewind_list)
        self.assertIn("rewind boundary:", rewind_show)
        self.assertIsNotNone(rewind_preview)
        assert rewind_preview is not None
        self.assertEqual(rewind_preview["boundary_id"], "hb-compact-1")
        self.assertEqual(rewind_preview["boundary_kind_label"], "compact boundary")
        self.assertEqual(rewind_preview["snapshot_message_count"], 4)
        self.assertEqual(rewind_preview["lineage_summary"], "pre-compact restore point")
        self.assertTrue(rewind_preview["targets_pre_compact_state"])
        self.assertEqual(rewind_preview["restore_message_delta_current"], 2)
        self.assertEqual(rewind_preview["apply_action"], "/rewind apply 1")
        self.assertEqual(
            rewind_preview["workflow_surface_policy"]["task_plan_file_focus"],
            "cleared",
        )
        self.assertIn("conversation rewound:", rewound)
        self.assertEqual(len(proxy.state.messages), 4)
        self.assertEqual(proxy.state.context_summary, "Earlier summary")
        self.assertEqual(proxy.memory_surface_payload()["memory_last_operation"], "rewind")
        self.assertEqual(
            proxy.memory_surface_payload()["memory_last_operation_task_plan_file_focus"],
            "cleared",
        )
        self.assertEqual(proxy.memory_surface_payload()["default_rewind_selector"], "1")
        self.assertEqual(proxy.memory_surface_payload()["rewind_apply_action"], "/rewind apply 1")
        self.assertEqual(proxy._task_detail_cache, {})
        self.assertIn(
            ("session.action", {"session_id": "session-123", "action": "describe_rewind", "args": ""}),
            proxy.client.calls,
        )
        self.assertIn(
            ("session.action", {"session_id": "session-123", "action": "describe_rewind", "args": "show 1"}),
            proxy.client.calls,
        )
        self.assertIn(
            ("session.action", {"session_id": "session-123", "action": "rewind_to_boundary", "args": "1"}),
            proxy.client.calls,
        )
        self.assertIn(
            ("session.describe", {"session_id": "session-123"}),
            proxy.client.calls,
        )


if __name__ == "__main__":
    unittest.main()
