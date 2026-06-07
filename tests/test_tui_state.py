from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.permissions import ApprovalRequest
from claudecode_py.runtime.events import RuntimeEvent
from claudecode_py.tui.state import PendingApproval, TuiState, TurnErrorDetails


class TuiStateTests(unittest.TestCase):
    def test_turn_rendering_keeps_turn_separator_and_assistant_chunks(self) -> None:
        state = TuiState()

        state.start_turn("Analyze session.py")
        state.record_assistant_text("First chunk")
        state.record_runtime_event(
            RuntimeEvent(
                kind="assistant_tool_call",
                message="calling 1 tool(s): read_file",
            )
        )
        state.record_runtime_event(
            RuntimeEvent(
                kind="assistant_tool_result_ready",
                message="received 1 tool result block(s); continuing assistant response",
            )
        )
        state.record_assistant_text("Second chunk")
        state.finish_turn("Second chunk")

        rendered = state.render_chat()

        self.assertIn("===== Turn 1 =====", rendered)
        self.assertIn("[You]\nAnalyze session.py", rendered)
        self.assertIn("[Assistant]\nSecond chunk", rendered)
        self.assertIn("[Activity]\n[assistant->tools] calling 1 tool(s): read_file", rendered)
        self.assertIn("[tools->assistant] received 1 tool result block(s); continuing assistant response", rendered)

    def test_streaming_text_chunks_render_as_single_contiguous_assistant_block(self) -> None:
        state = TuiState()

        state.start_turn("Outline project")
        state.record_assistant_text("Here is")
        state.record_assistant_text(" claudecode_py")
        state.record_assistant_text(" overview")

        rendered = state.render_chat()

        self.assertIn("[Assistant]\nHere is claudecode_py overview", rendered)

    def test_turn_rendering_includes_structured_error_block(self) -> None:
        state = TuiState()

        state.start_turn("Review pending diff")
        state.fail_turn_with_details(
            TurnErrorDetails(
                message='Bash command is not allowed in command mode "review".',
                decision_reason='Matched ask rules: ask:shell:git diff [segment 1: git diff]',
                permission_rules=("ask:shell:git diff [segment 1: git diff]",),
                command_mode_name="review",
                command_mode_allowed_prefixes=("git diff", "git show"),
                command_mode_violating_segment="Set-Content out.txt",
                command_mode_violating_segment_index=2,
                command_mode_complex_features=("command_substitution",),
            )
        )

        rendered = state.render_chat()

        self.assertIn("[Error]\nturn_error:", rendered)
        self.assertIn('- message: Bash command is not allowed in command mode "review".', rendered)
        self.assertIn("- policy: Matched ask rules: ask:shell:git diff [segment 1: git diff]", rendered)
        self.assertIn("- matched_rules:", rendered)
        self.assertIn("  - ask:shell:git diff [segment 1: git diff]", rendered)
        self.assertIn("  - mode: review", rendered)
        self.assertIn("  - allowed_prefixes: git diff, git show", rendered)
        self.assertIn("  - violating_segment: segment 2: Set-Content out.txt", rendered)
        self.assertIn("  - complex_features: command_substitution", rendered)

    def test_input_history_supports_prev_and_next_navigation(self) -> None:
        state = TuiState()
        state.record_input_history("first")
        state.record_input_history("second")

        self.assertEqual(state.history_previous(), "second")
        self.assertEqual(state.history_previous(), "first")
        self.assertEqual(state.history_next(), "second")
        self.assertEqual(state.history_next(), "")

    def test_tool_events_render_as_dedicated_tool_log(self) -> None:
        state = TuiState()

        state.record_runtime_event(
            RuntimeEvent(
                kind="tool_waiting_for_approval",
                message='{"path":"session.py"}',
                tool_name="read_file",
                tool_call_id="tool-1",
                approval_risk_level="read",
            )
        )
        state.record_runtime_event(
            RuntimeEvent(
                kind="tool_started",
                message='{"path":"session.py"}',
                tool_name="read_file",
                tool_call_id="tool-1",
            )
        )
        state.record_runtime_event(
            RuntimeEvent(
                kind="tool_finished",
                message="ok",
                tool_name="read_file",
                tool_call_id="tool-1",
                duration_ms=12,
            )
        )

        rendered = state.render_tool_logs()

        self.assertIn("Recent Tools", rendered)
        self.assertIn("[OK] read_file (12ms)", rendered)
        self.assertIn('input: {"path":"session.py"}', rendered)
        self.assertNotIn("[WAITING]", rendered)

    def test_failed_tool_event_updates_existing_entry(self) -> None:
        state = TuiState()

        state.record_runtime_event(
            RuntimeEvent(
                kind="tool_started",
                message='{"command":"rm"}',
                tool_name="bash",
                tool_call_id="tool-2",
            )
        )
        state.record_runtime_event(
            RuntimeEvent(
                kind="tool_failed",
                message="Permission denied",
                tool_name="bash",
                tool_call_id="tool-2",
                duration_ms=4,
                is_error=True,
            )
        )

        rendered = state.render_tool_logs()

        self.assertIn("[ERROR] bash (4ms)", rendered)
        self.assertIn("detail: Permission denied", rendered)

    def test_change_tool_events_update_change_status_and_focus(self) -> None:
        state = TuiState(selected_change_stack="redo", selected_change_index=2)

        state.record_runtime_event(
            RuntimeEvent(
                kind="tool_started",
                message='{"path":"demo.py"}',
                tool_name="edit_file",
                tool_call_id="tool-3",
            )
        )
        self.assertEqual(state.change_status, "Applying edit_file...")

        state.record_runtime_event(
            RuntimeEvent(
                kind="tool_finished",
                message="ok",
                tool_name="edit_file",
                tool_call_id="tool-3",
                duration_ms=8,
            )
        )

        self.assertEqual(state.change_status, "Applied changes are now in Undo stack.")
        self.assertEqual(state.selected_change_stack, "undo")
        self.assertEqual(state.selected_change_index, 0)

    def test_failed_change_tool_event_sets_failure_status(self) -> None:
        state = TuiState()
        state.start_turn("Apply patch to demo.py")
        state.last_change_preview = "Pending patch change set\nfiles: 1"
        state.last_change_preview_label = "Approved change set"

        state.record_runtime_event(
            RuntimeEvent(
                kind="tool_started",
                message='{"patch":"..."}',
                tool_name="apply_patch",
                tool_call_id="tool-4",
            )
        )
        state.record_runtime_event(
            RuntimeEvent(
                kind="tool_failed",
                message="Hunk did not match.",
                tool_name="apply_patch",
                tool_call_id="tool-4",
                duration_ms=15,
                is_error=True,
            )
        )

        self.assertEqual(state.change_status, "Failed to apply apply_patch: Hunk did not match.")
        self.assertEqual(state.last_change_preview_label, "Failed change set")
        self.assertIn("tool: apply_patch", state.failed_change_context)
        self.assertIn('input: {"patch":"..."}', state.failed_change_context)
        self.assertEqual(state.retry_prompt, "Apply patch to demo.py")

    def test_failed_change_tool_extracts_recovery_hint(self) -> None:
        state = TuiState()
        state.last_change_preview = "Pending file edit\npath: demo.py"

        state.record_runtime_event(
            RuntimeEvent(
                kind="tool_failed",
                message=(
                    "old_text was not found.\n"
                    "Next steps:\n"
                    "  1. Read the file again.\n"
                    "  2. Retry with exact old_text."
                ),
                tool_name="edit_file",
                tool_call_id="tool-5",
                duration_ms=3,
                is_error=True,
            )
        )

        self.assertIn("Next steps:", state.recovery_hint)
        self.assertIn("Read the file again.", state.recovery_hint)

    def test_non_tool_events_stay_in_event_panel(self) -> None:
        state = TuiState()

        state.record_runtime_event(
            RuntimeEvent(
                kind="provider_retry",
                message="retrying in 0.5s",
            )
        )
        state.record_runtime_event(
            RuntimeEvent(
                kind="context_compacted",
                message="compacted 10 messages",
            )
        )
        state.record_runtime_event(
            RuntimeEvent(
                kind="budget_pressure",
                message="message count 6 >= warning threshold 6",
                budget_state="warning",
                budget_reason="message count 6 >= warning threshold 6",
            )
        )
        state.record_runtime_event(
            RuntimeEvent(
                kind="compact_recovery_started",
                message="starting compact recovery after prompt-too-long",
            )
        )
        state.record_runtime_event(
            RuntimeEvent(
                kind="compact_recovery_finished",
                message="compact recovery restored budget headroom; retrying turn",
            )
        )
        state.record_runtime_event(
            RuntimeEvent(
                kind="tool_batch_started",
                message="starting 2 parallel read-only tool call(s)",
                batch_size=2,
                batch_parallel=True,
            )
        )
        state.record_runtime_event(
            RuntimeEvent(
                kind="tool_batch_finished",
                message="completed 2 parallel read-only tool call(s)",
                batch_size=2,
                batch_parallel=True,
                result_count=2,
            )
        )

        rendered = state.render_events()

        self.assertIn("[provider:retry] retrying in 0.5s", rendered)
        self.assertIn("[context] compacted 10 messages", rendered)
        self.assertIn("[budget] message count 6 >= warning threshold 6", rendered)
        self.assertIn("[recovery:start] starting compact recovery after prompt-too-long", rendered)
        self.assertIn("[recovery:done] compact recovery restored budget headroom; retrying turn", rendered)
        self.assertIn("[tool:batch:start] starting 2 parallel read-only tool call(s)", rendered)
        self.assertIn("[tool:batch:done] completed 2 parallel read-only tool call(s)", rendered)

    def test_tool_result_summary_is_recorded_as_turn_activity(self) -> None:
        state = TuiState()
        state.start_turn("Inspect runtime")

        state.record_runtime_event(
            RuntimeEvent(
                kind="tool_result_summarized",
                message="ok results=2",
                result_count=2,
            )
        )

        rendered = state.render_chat()

        self.assertIn("[Activity]\n[tool:summary] ok results=2", rendered)

    def test_task_detail_panel_renders_file_context_hint_lines(self) -> None:
        state = TuiState(selected_task_id="task-123")
        state.set_task_detail(
            task_id="task-123",
            text="task detail body",
            file_context_metadata={
                "file_context_scope": "task",
                "file_context_file_count": 2,
                "file_context_sources": ["checklist", "change"],
                "file_context_files": [
                    {
                        "path": "claudecode_py/session.py",
                        "source": "checklist",
                        "target": {
                            "action": "open_file",
                            "path": "claudecode_py/session.py",
                            "line": 12,
                            "label": "task file",
                        },
                        "diff_targets": {
                            "hunks": [
                                {
                                    "action": "open_diff",
                                    "path": "claudecode_py/session.py",
                                    "line": 20,
                                    "label": "task diff",
                                }
                            ]
                        },
                        "target_summary": "open_file claudecode_py/session.py:12",
                        "diff_target_count": 1,
                    }
                    ,
                    {
                        "path": "claudecode_py/cli.py",
                        "source": "change",
                        "change_id": "abc12345",
                        "target": {
                            "action": "open_file",
                            "path": "claudecode_py/cli.py",
                            "line": 44,
                            "label": "cli file",
                        },
                        "diff_targets": {
                            "hunks": [
                                {
                                    "action": "open_diff",
                                    "path": "claudecode_py/cli.py",
                                    "line": 47,
                                    "label": "cli diff",
                                }
                            ]
                        },
                        "target_summary": "open_file claudecode_py/cli.py:44",
                        "diff_target_count": 1,
                    },
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
                            "label": "task diff",
                        }
                    ]
                },
            },
        )
        state.task_detail_file_context_index = 1

        rendered = state.render_task_detail_panel("task detail body")

        self.assertIn("Task Detail [detail] [task-123] [file 2/2 claudecode_py/cli.py]", rendered)
        self.assertIn("Focused file", rendered)
        self.assertIn("file_focus: 2/2", rendered)
        self.assertIn("focused_file: claudecode_py/cli.py", rendered)
        self.assertIn("source: change", rendered)
        self.assertIn("related change: abc12345", rendered)
        self.assertIn("diff hunks: 1", rendered)
        self.assertIn("context-only: no", rendered)
        self.assertIn("primary target: open_file claudecode_py/cli.py:44 cli file", rendered)
        self.assertIn("secondary target: open_diff claudecode_py/cli.py:47 cli diff", rendered)
        self.assertIn("navigation: F9 primary target, F10 secondary target", rendered)
        self.assertIn("navigation_f9: open_file claudecode_py/cli.py:44 cli file", rendered)
        self.assertIn("navigation_f10: open_diff claudecode_py/cli.py:47 cli diff", rendered)
        self.assertIn("file_inventory:", rendered)
        self.assertIn("> 2. claudecode_py/cli.py [change] related_change=abc12345", rendered)

    def test_plan_panel_renders_file_focus_in_header(self) -> None:
        state = TuiState()
        state.plan_file_context_index = 1

        rendered = state.render_plan_panel(
            "plan detail body",
            file_context_metadata={
                "file_context_scope": "plan",
                "file_context_file_count": 2,
                "file_context_sources": ["recent_change", "symbol_surface"],
                "file_context_files": [
                    {
                        "path": "claudecode_py/runtime/query_loop.py",
                        "source": "recent_change",
                        "change_id": "def67890",
                        "target": {
                            "action": "open_file",
                            "path": "claudecode_py/runtime/query_loop.py",
                            "line": 33,
                            "label": "plan file",
                        },
                        "target_summary": "open_file claudecode_py/runtime/query_loop.py:33",
                    },
                    {
                        "path": "claudecode_py/runtime/context.py",
                        "source": "symbol_surface",
                        "target": {
                            "action": "open_file",
                            "path": "claudecode_py/runtime/context.py",
                            "line": 18,
                            "label": "context file",
                        },
                        "diff_targets": {
                            "hunks": [
                                {
                                    "action": "open_diff",
                                    "path": "claudecode_py/runtime/context.py",
                                    "line": 22,
                                    "label": "context diff",
                                }
                            ]
                        },
                        "target_summary": "open_file claudecode_py/runtime/context.py:18",
                        "diff_target_count": 1,
                    },
                ],
            },
        )

        self.assertIn(
            "Active Plan [summary] [file 2/2 claudecode_py/runtime/context.py]",
            rendered,
        )
        self.assertIn("Focused file", rendered)
        self.assertIn("file_focus: 2/2", rendered)
        self.assertIn("focused_file: claudecode_py/runtime/context.py", rendered)
        self.assertIn("source: symbol_surface", rendered)
        self.assertIn("diff hunks: 1", rendered)
        self.assertIn("context-only: no", rendered)
        self.assertIn("primary target: open_file claudecode_py/runtime/context.py:18 context file", rendered)
        self.assertIn("secondary target: open_diff claudecode_py/runtime/context.py:22 context diff", rendered)
        self.assertIn("navigation: F9 primary target, F10 secondary target", rendered)
        self.assertIn("navigation_f9: open_file claudecode_py/runtime/context.py:18 context file", rendered)
        self.assertIn("navigation_f10: open_diff claudecode_py/runtime/context.py:22 context diff", rendered)
        self.assertIn("file_inventory:", rendered)

    def test_changes_panel_renders_file_focus_in_header(self) -> None:
        state = TuiState(selected_change_stack="redo")

        rendered = state.render_changes_panel(
            selected_file_context_metadata={
                "file_context_scope": "change",
                "file_context_file_count": 2,
                "file_context_files": [
                    {
                        "path": "demo.py",
                        "source": "selected_change",
                        "change_id": "9876abcd",
                        "target": {
                            "action": "open_file",
                            "path": "demo.py",
                            "line": 4,
                            "label": "demo file",
                        },
                        "target_summary": "open_file demo.py:4",
                    },
                    {
                        "path": "alt.py",
                        "source": "selected_change",
                        "change_id": "9876abcd",
                        "target": {
                            "action": "open_file",
                            "path": "alt.py",
                            "line": 8,
                            "label": "alt file",
                        },
                        "diff_targets": {
                            "hunks": [
                                {
                                    "action": "open_diff",
                                    "path": "alt.py",
                                    "line": 11,
                                    "label": "alt diff",
                                }
                            ]
                        },
                        "target_summary": "open_file alt.py:8",
                        "diff_target_count": 1,
                    },
                ],
            },
            selected_file_index=1,
        )

        self.assertIn("Changes [redo file 2/2 alt.py]", rendered)
        self.assertIn("Focused file", rendered)
        self.assertIn("source: selected_change", rendered)
        self.assertIn("related change: 9876abcd", rendered)
        self.assertIn("file_focus: 2/2", rendered)
        self.assertIn("focused_file: alt.py", rendered)
        self.assertIn("diff hunks: 1", rendered)
        self.assertIn("context-only: no", rendered)
        self.assertIn("primary target: open_file alt.py:8 alt file", rendered)
        self.assertIn("secondary target: open_diff alt.py:11 alt diff", rendered)
        self.assertIn("navigation: F9 primary target, F10 secondary target", rendered)
        self.assertIn("navigation_f9: open_file alt.py:8 alt file", rendered)
        self.assertIn("navigation_f10: open_diff alt.py:11 alt diff", rendered)
        self.assertIn("file_inventory:", rendered)

    def test_status_panel_renders_planning_lifecycle_fields(self) -> None:
        state = TuiState()

        rendered = state.render_status_panel(
            provider_text="provider: fake\nmodel: demo",
            config_text=(
                "provider: openai-compatible\n"
                "model: gpt-test\n"
                "session_id: session-1\n"
                "execution_constraints: read-only\n"
                "advisor_blocks: 2\n"
                "plan_executions: 4\n"
                "plan_drifts: 1\n"
                "last_plan_drift_summary: pending_tools: write_file\n"
                "active_plan_kind: ultraplan\n"
                "active_plan_goal: map runtime\n"
                "enabled_skills: 3\n"
                "mcp_servers: 1\n"
                "mcp_failed_servers: 0\n"
                "recent_change_sets: 2\n"
                "redo_change_sets: 0\n"
            ),
            memory_metadata={
                "memory_boundary_count": 3,
                "memory_rewindable_boundary_count": 2,
                "memory_context_summary_chars": 128,
                "memory_compaction_state": "warning",
                "memory_compaction_reason": "message count 6 >= warning threshold 6",
                "memory_last_boundary_kind": "rewind",
                "memory_latest_rewindable_boundary_kind": "compact",
                "memory_default_rewind_selector": "1",
                "memory_rewind_show_action": "/rewind show 1",
                "memory_rewind_apply_action": "/rewind apply 1",
                "memory_last_operation": "rewind",
                "memory_last_operation_messages": "restored from selected boundary snapshot",
                "memory_last_operation_session_identity": "preserved",
                "memory_last_operation_task_plan_file_focus": "cleared",
            },
            rewind_preview_metadata={
                "selector_index": 2,
                "boundary_id": "hb-compact-2",
                "boundary_kind_label": "compact boundary",
                "trigger": "manual",
                "summary": "Compacted older turns",
                "snapshot_message_count": 4,
                "snapshot_summary_chars": 128,
                "restore_effect_summary": "Conversation messages and compacted context summary were restored from a selected boundary snapshot.",
                "show_action": "/rewind show 2",
                "apply_action": "/rewind apply 2",
            },
            selected_rewind_boundary_index=1,
            focused_file_context_metadata={
                "file_context_scope": "task",
                "file_context_file_count": 2,
                "file_context_files": [
                    {
                        "path": "claudecode_py/session.py",
                        "target": {
                            "action": "open_file",
                            "path": "claudecode_py/session.py",
                            "line": 12,
                            "label": "task file",
                        },
                    },
                    {
                        "path": "claudecode_py/cli.py",
                        "change_id": "abc12345",
                        "target": {
                            "action": "open_file",
                            "path": "claudecode_py/cli.py",
                            "line": 44,
                            "label": "cli file",
                        },
                        "diff_targets": {
                            "hunks": [
                                {
                                    "action": "open_diff",
                                    "path": "claudecode_py/cli.py",
                                    "line": 47,
                                    "label": "cli diff",
                                }
                            ]
                        },
                    },
                ],
            },
            working_set_metadata={
                "file_context_scope": "session",
                "file_context_file_count": 2,
                "file_context_files": [
                    {
                        "path": "claudecode_py/session.py",
                        "scope_reasons": ["active task"],
                        "is_context_only": True,
                        "target": {
                            "action": "open_file",
                            "path": "claudecode_py/session.py",
                            "line": 12,
                            "label": "task file",
                        },
                    },
                    {
                        "path": "claudecode_py/cli.py",
                        "change_id": "abc12345",
                        "scope_reasons": ["active task", "recent change"],
                        "target": {
                            "action": "open_file",
                            "path": "claudecode_py/cli.py",
                            "line": 44,
                            "label": "cli file",
                        },
                        "diff_targets": {
                            "hunks": [
                                {
                                    "action": "open_diff",
                                    "path": "claudecode_py/cli.py",
                                    "line": 47,
                                    "label": "cli diff",
                                }
                            ]
                        },
                        "diff_target_count": 1,
                        "has_related_change": True,
                        "has_diff_hunks": True,
                        "is_context_only": False,
                    },
                ],
            },
            focused_file_context_source="task",
            focused_file_context_index=1,
        )

        self.assertIn("constraints: read-only", rendered)
        self.assertIn("advisor_blocks: 2", rendered)
        self.assertIn("plan_executions: 4", rendered)
        self.assertIn("plan_drifts: 1", rendered)
        self.assertIn("last_plan_drift: pending_tools: write_file", rendered)
        self.assertIn("active_plan_kind: ultraplan", rendered)
        self.assertIn("active_plan_goal: map runtime", rendered)
        self.assertIn("Memory Lifecycle", rendered)
        self.assertIn("history_boundaries: 3", rendered)
        self.assertIn("rewindable_boundaries: 2", rendered)
        self.assertIn("memory compaction: warning", rendered)
        self.assertIn("compact reason: message count 6 >= warning threshold 6", rendered)
        self.assertIn("latest memory operation: rewind", rendered)
        self.assertIn("memory_messages: restored from selected boundary snapshot", rendered)
        self.assertIn("memory_session_identity: preserved", rendered)
        self.assertIn("memory_focus_policy: cleared", rendered)
        self.assertIn("default rewind selector: 1", rendered)
        self.assertIn("rewind show: /rewind show 1", rendered)
        self.assertIn("rewind apply: /rewind apply 1", rendered)
        self.assertIn("selected_rewind_boundary: 2/2", rendered)
        self.assertIn("selected_rewind_boundary_id: hb-compact-2", rendered)
        self.assertIn("selected_rewind_boundary_kind: compact boundary", rendered)
        self.assertIn("selected_rewind_boundary_trigger: manual", rendered)
        self.assertIn("selected_rewind_boundary_summary: Compacted older turns", rendered)
        self.assertIn("selected_rewind_restore_messages: 4", rendered)
        self.assertIn("selected_rewind_restore_context_summary_chars: 128", rendered)
        self.assertIn("selected_rewind_show: /rewind show 2", rendered)
        self.assertIn("selected_rewind_apply: /rewind apply 2", rendered)
        self.assertIn("Ctrl+Alt+Left/Right select rewind boundary", rendered)
        self.assertIn("Focused file context", rendered)
        self.assertIn("source: task", rendered)
        self.assertIn("shortcuts: Ctrl+Left/Right focus task files", rendered)
        self.assertIn("focused_file: 2/2", rendered)
        self.assertIn("focused_file_path: claudecode_py/cli.py", rendered)
        self.assertIn("related change: abc12345", rendered)
        self.assertIn("diff hunks: 1", rendered)
        self.assertIn("context-only: no", rendered)
        self.assertIn(
            "secondary target: open_diff claudecode_py/cli.py:47 cli diff",
            rendered,
        )
        self.assertIn(
            "navigation: F9 primary target, F10 secondary target",
            rendered,
        )
        self.assertIn(
            "navigation_f9: open_file claudecode_py/cli.py:44 cli file",
            rendered,
        )
        self.assertIn(
            "navigation_f10: open_diff claudecode_py/cli.py:47 cli diff",
            rendered,
        )
        self.assertIn("Working Set", rendered)
        self.assertIn("> 2. claudecode_py/cli.py", rendered)
        self.assertIn("in scope because: active task, recent change", rendered)
        self.assertIn("related change: abc12345", rendered)
        self.assertIn("context-only: yes", rendered)

    def test_status_panel_renders_workspace_section_for_unavailable_workspace(self) -> None:
        state = TuiState()

        rendered = state.render_status_panel(
            provider_text="provider: openai-compatible\nmodel: demo",
            config_text=(
                "provider: openai-compatible\n"
                "model: demo\n"
                "session_id: session-1\n"
                "workspace_mode: snapshot\n"
                "workspace_label: scout-agent\n"
                "effective_cwd: C:/tmp/.pyclaude/workspaces/scout-agent\n"
                "workspace_effective_cwd_exists: no\n"
                "workspace_health: unavailable\n"
                "workspace_cleanup_status: failed\n"
                "workspace_unavailable_reason: isolated workspace path is missing\n"
                "workspace_fallback_cwd: C:/repo\n"
                "workspace_recommended_actions: /workspaces list, /workspaces repair scout-agent, /workspaces cleanup\n"
                "selected_workspace_primary_action: workspace_repair session-1\n"
                "selected_workspace_secondary_action: workspace_cleanup_preview\n"
                "selected_workspace_tertiary_action: /workspaces list\n"
                "selected_workspace_target: session-1\n"
            ),
        )

        self.assertIn("Workspace State", rendered)
        self.assertIn("workspace_mode: snapshot", rendered)
        self.assertIn("workspace_health: unavailable", rendered)
        self.assertIn("workspace_label: scout-agent", rendered)
        self.assertIn("effective_cwd: C:/tmp/.pyclaude/workspaces/scout-agent", rendered)
        self.assertIn("workspace_effective_cwd_exists: no", rendered)
        self.assertIn("workspace_cleanup_status: failed", rendered)
        self.assertIn(
            "workspace_expected_effective_cwd: C:/tmp/.pyclaude/workspaces/scout-agent",
            rendered,
        )
        self.assertIn("workspace_fallback_cwd: C:/repo", rendered)
        self.assertIn("workspace_unavailable_reason: isolated workspace path is missing", rendered)
        self.assertIn(
            "workspace_recommended_actions: /workspaces list, /workspaces repair scout-agent, /workspaces cleanup",
            rendered,
        )
        self.assertIn("selected_workspace_primary_action: workspace_repair session-1", rendered)
        self.assertIn("selected_workspace_secondary_action: workspace_cleanup_preview", rendered)
        self.assertIn("selected_workspace_tertiary_action: /workspaces list", rendered)
        self.assertIn("selected_workspace_target: session-1", rendered)

    def test_status_panel_renders_background_section(self) -> None:
        state = TuiState()

        rendered = state.render_status_panel(
            provider_text="provider: fake\nmodel: demo",
            config_text="provider: openai-compatible\nmodel: gpt-test\nsession_id: session-1\n",
            background_metadata={
                "background_session_id": "bg-123",
                "background_continuation_category": "live attachable",
                "background_current_workflow_summary": "attachable live background session",
                "background_task_surface_summary": "background_execution:1",
                "background_primary_task": {
                    "task_id": "task-bg",
                    "surface_kind": "background_execution",
                    "status": "running",
                    "description": "Finish background work",
                },
                "background_focused_file": "demo.py",
                "background_pending_followup_count": 1,
                "background_pending_followup_summary": "please continue",
                "background_send_followup_action": "session.action background_send_followup bg-123",
                "background_attach_action": "pyclaude attach bg-123",
                "background_logs_action": "pyclaude logs bg-123 summary",
            },
        )

        self.assertIn("Background State", rendered)
        self.assertIn("background_session_id: bg-123", rendered)
        self.assertIn("background_continuation: live attachable", rendered)
        self.assertIn("background_workflow: attachable live background session", rendered)
        self.assertIn("background_task_surfaces: background_execution:1", rendered)
        self.assertIn(
            "background_primary_task: task-bg | background_execution | running | Finish background work",
            rendered,
        )
        self.assertIn("background_focused_file: demo.py", rendered)
        self.assertIn("background_pending_followups: 1", rendered)
        self.assertIn("background_pending_followup: please continue", rendered)
        self.assertIn(
            "background_send_followup: session.action background_send_followup bg-123",
            rendered,
        )
        self.assertIn("background_attach: pyclaude attach bg-123", rendered)
        self.assertIn("background_logs: pyclaude logs bg-123 summary", rendered)

    def test_status_panel_renders_background_registry_section(self) -> None:
        state = TuiState()

        rendered = state.render_status_panel(
            provider_text="provider: fake\nmodel: demo",
            config_text="provider: openai-compatible\nmodel: gpt-test\nsession_id: session-1\n",
            background_registry_metadata={
                "background_registry_count": 2,
                "background_registry_selected_bg_id": "bg-123",
                "background_registry_selected_status": "running",
                "background_registry_selected_continuation_category": "live attachable",
                "background_registry_selected_workflow_summary": "attachable live background session",
                "background_registry_selected_primary_task": {
                    "task_id": "task-bg",
                    "status": "running",
                    "description": "Finish background work",
                },
                "background_registry_selected_focused_file": "demo.py",
                "background_registry_selected_recent_activity": "Waiting for attach",
                "background_registry_selected_token_count": 42,
                "background_registry_selected_token_count_source": "provider",
                "background_registry_selected_last_tool_input": '{"prompt":"continue"}',
                "background_registry_selected_last_tool_summary": "ok (25ms)",
                "background_registry_selected_progress_summary": "Waiting for attach",
                "background_registry_selected_completion_state": "running",
                "background_registry_selected_completion_summary": "Waiting for attach",
                "background_registry_selected_pending_followup_count": 1,
                "background_registry_selected_pending_followup_summary": "please continue",
                "background_registry_primary_action": "pyclaude attach bg-123",
                "background_registry_secondary_action": "pyclaude logs bg-123 summary",
                "background_registry_logs_action": "pyclaude logs bg-123 summary",
                "background_registry_send_followup_action": "session.action background_send_followup bg-123",
                "background_registry_entries": [
                    {
                        "background_session_id": "bg-123",
                        "status": "running",
                        "background_continuation_category": "live attachable",
                        "background_primary_action": "pyclaude attach bg-123",
                        "background_send_followup_action": "session.action background_send_followup bg-123",
                        "background_pending_followup_count": 1,
                        "background_pending_followup_summary": "please continue",
                        "background_recent_activity": "Waiting for attach",
                        "background_token_count": 42,
                        "background_token_count_source": "provider",
                        "background_last_tool_input": '{"prompt":"continue"}',
                        "background_last_tool_summary": "ok (25ms)",
                        "background_progress_summary": "Waiting for attach",
                        "background_completion_state": "running",
                        "background_completion_summary": "Waiting for attach",
                    },
                    {
                        "background_session_id": "bg-456",
                        "status": "completed",
                        "background_continuation_category": "saved resumable",
                        "background_primary_action": "pyclaude --resume-session session-456 repl",
                    },
                ],
            },
        )

        self.assertIn("Background Sessions", rendered)
        self.assertIn("background_sessions: 2", rendered)
        self.assertIn("selected_background_session: bg-123", rendered)
        self.assertIn("selected_background_status: running", rendered)
        self.assertIn("selected_background_continuation: live attachable", rendered)
        self.assertIn("selected_background_workflow: attachable live background session", rendered)
        self.assertIn("selected_background_primary_task: task-bg | running | Finish background work", rendered)
        self.assertIn("selected_background_focused_file: demo.py", rendered)
        self.assertIn("selected_background_recent_activity: Waiting for attach", rendered)
        self.assertIn("selected_background_token_count: 42 (provider)", rendered)
        self.assertIn('selected_background_last_tool_input: {"prompt":"continue"}', rendered)
        self.assertIn("selected_background_last_tool_summary: ok (25ms)", rendered)
        self.assertIn("selected_background_progress: Waiting for attach", rendered)
        self.assertIn("selected_background_completion: running", rendered)
        self.assertIn("selected_background_pending_followups: 1", rendered)
        self.assertIn("selected_background_pending_followup: please continue", rendered)
        self.assertIn("background_registry_primary_action: pyclaude attach bg-123", rendered)
        self.assertIn(
            "background_registry_send_followup_action: session.action background_send_followup bg-123",
            rendered,
        )
        self.assertIn("> 1. bg-123 status=running continuation=live attachable action=pyclaude attach bg-123", rendered)

    def test_status_panel_renders_background_handoff_section(self) -> None:
        state = TuiState()

        rendered = state.render_status_panel(
            provider_text="provider: fake\nmodel: demo",
            config_text="provider: openai-compatible\nmodel: gpt-test\nsession_id: session-1\n",
            background_handoff_metadata={
                "background_handoff_count": 1,
                "background_handoff_selected_bg_id": "bg-456",
                "background_handoff_selected_completion_state": "completed",
                "background_handoff_selected_completion_summary": "Background session completed.",
                "background_handoff_transcript_action": "pyclaude logs bg-456 summary",
                "background_handoff_task_action": "/task show task-bg",
                "background_handoff_changes_action": "pyclaude --resume-session session-456 repl | /changes working-set",
                "background_handoff_resume_action": "pyclaude --resume-session session-456 repl",
            },
        )

        self.assertIn("Background Notifications", rendered)
        self.assertIn("background_notifications: 1", rendered)
        self.assertIn("latest_background_handoff: bg-456", rendered)
        self.assertIn("latest_background_state: completed", rendered)
        self.assertIn("latest_background_summary: Background session completed.", rendered)
        self.assertIn("background_handoff_transcript_action: pyclaude logs bg-456 summary", rendered)
        self.assertIn("background_handoff_task_action: /task show task-bg", rendered)

    def test_status_panel_prefers_structured_workspace_and_file_context_surfaces(self) -> None:
        state = TuiState()

        rendered = state.render_status_panel(
            provider_text="provider: openai-compatible\nmodel: demo\n",
            config_text="provider: openai-compatible\nmodel: demo\nsession_id: session-1\n",
            status_metadata={
                "status_session_id": "session-1",
                "status_provider": "openai-compatible",
                "status_model": "demo",
                "status_workspace_summary": "mode=snapshot health=unavailable focused=demo.py",
                "status_workspace_mode": "snapshot",
                "status_workspace_health": "unavailable",
                "status_workspace_anomaly": "unavailable, fallback active",
                "status_workspace_recovery": "/workspaces repair session-1",
                "status_working_set_summary": "mix: diff_backed=1 context_only=0 explicit=1 task=1 plan=0 change=1",
                "status_focused_file_summary": "demo.py (change)",
                "status_explicit_context_entry_count": 1,
                "status_unresolved_explicit_context_entry_count": 0,
                "status_action_groups": {"go_to_focused_file": ["/files focused"]},
            },
            workspace_surface_metadata={
                "workspace_label": "scout-agent",
                "workspace_effective_cwd": "C:/tmp/.pyclaude/workspaces/scout-agent",
                "workspace_effective_cwd_exists": False,
                "workspace_cleanup_status": "failed",
                "workspace_cleanup_error": "PermissionError: cleanup blocked",
                "workspace_fallback_cwd": "C:/repo",
                "workspace_recommended_actions": [
                    "/workspaces list",
                    "/workspaces repair session-1",
                    "/workspaces cleanup",
                ],
                "workspace_action_bundle": {
                    "primary_action": "workspace_repair session-1",
                    "secondary_action": "workspace_cleanup_preview",
                    "tertiary_action": "/workspaces list",
                },
                "workspace_action_groups": {
                    "inspect_current_workspace": ["/workspaces current"],
                    "inspect_workspace_inventory": ["/workspaces list"],
                    "workspace_recovery": ["/workspaces repair session-1", "/workspaces cleanup"],
                },
            },
            file_context_surface_metadata={
                "working_set": {
                    "file_context_scope": "session",
                    "file_context_file_count": 2,
                    "file_context_files": [
                        {
                            "path": "demo.py",
                            "scope_reasons": ["explicit context path", "recent change"],
                            "change_id": "chg-1",
                            "target": {
                                "action": "open_file",
                                "path": "demo.py",
                                "line": 12,
                                "label": "demo file",
                            },
                            "diff_targets": {
                                "hunks": [
                                    {
                                        "action": "open_diff",
                                        "path": "demo.py",
                                        "line": 14,
                                        "label": "demo diff",
                                    }
                                ]
                            },
                            "diff_target_count": 1,
                            "is_context_only": False,
                        },
                        {
                            "path": "notes.md",
                            "scope_reasons": ["active task"],
                            "target": {
                                "action": "open_file",
                                "path": "notes.md",
                                "line": 1,
                                "label": "notes",
                            },
                            "is_context_only": True,
                        },
                    ],
                },
                "focused_file": {
                    "source": "working-set",
                    "index": 0,
                },
                "explicit_context": {
                    "entry_count": 1,
                    "unresolved_entry_count": 0,
                    "explicit_only_file_count": 0,
                    "automatic_file_count": 1,
                    "overlapping_file_count": 1,
                    "compare_summary_lines": [
                        "explicit context compare:",
                        "- explicit-only files: 0",
                        "- automatic-only files: 1",
                        "- overlapping files: 1",
                    ],
                },
                "file_action_groups": {
                    "inspect_focused_file": ["/files focused"],
                    "inspect_focused_diff": ["/diff focused"],
                    "inspect_explicit_context": ["/files explicit", "/files context"],
                    "stay_on_surface": ["/status workflow"],
                },
            },
        )

        self.assertIn("Workspace Recovery", rendered)
        self.assertIn("workspace_recommended_actions: /workspaces list, /workspaces repair session-1, /workspaces cleanup", rendered)
        self.assertIn("- inspect current workspace: /workspaces current", rendered)
        self.assertIn("Focused file context", rendered)
        self.assertIn("context_origin: explicit+automatic", rendered)
        self.assertIn("Working Set", rendered)
        self.assertIn("> 1. demo.py", rendered)
        self.assertIn("Explicit Context", rendered)
        self.assertIn("overlapping_files: 1", rendered)
        self.assertIn("File Actions", rendered)
        self.assertIn("- inspect focused diff: /diff focused", rendered)

    def test_status_panel_renders_structured_symbol_section(self) -> None:
        state = TuiState()

        rendered = state.render_status_panel(
            provider_text="provider: fake\nmodel: demo",
            config_text="provider: openai-compatible\nmodel: gpt-test\nsession_id: session-1\n",
            symbol_surface_metadata={
                "surface_kind": "symbol_actions",
                "selected_symbol": "build",
                "match_count": 1,
                "definition_count": 1,
                "reference_count": 1,
                "selected_match_index": 0,
                "selected_definition_index": 0,
                "selected_reference_index": 0,
                "selected_definition": {
                    "action": "open_symbol",
                    "path": "demo.py",
                    "line": 1,
                    "label": "function build",
                },
                "selected_reference": {
                    "action": "open_reference",
                    "path": "demo.py",
                    "line": 4,
                    "label": "value = build()",
                },
                "navigation_target": {
                    "action": "open_symbol",
                    "path": "demo.py",
                    "line": 1,
                    "label": "function build",
                },
                "symbol_primary_action": "/symbol open primary",
                "symbol_secondary_action": "/symbol open secondary",
                "symbol_tertiary_action": "/symbol clear",
                "symbol_action_target": "build",
                "definitions": [
                    {
                        "action": "open_symbol",
                        "path": "demo.py",
                        "line": 1,
                        "label": "function build",
                    },
                    {
                        "action": "open_symbol",
                        "path": "demo.py",
                        "line": 10,
                        "label": "function build backup",
                    },
                ],
                "references": [
                    {
                        "action": "open_reference",
                        "path": "demo.py",
                        "line": 4,
                        "label": "value = build()",
                    }
                ],
            },
            symbol_focus_group="definitions",
            symbol_focus_index=0,
        )

        self.assertIn("Symbol", rendered)
        self.assertIn("symbol_surface_kind: symbol_actions", rendered)
        self.assertIn("symbol_selected_symbol: build", rendered)
        self.assertIn("symbol_selected_match_index: 1/1", rendered)
        self.assertIn("symbol_selected_definition_index: 1/1", rendered)
        self.assertIn("symbol_selected_reference_index: 1/1", rendered)
        self.assertIn("symbol_selected_definition: open_symbol demo.py:1 (function build)", rendered)
        self.assertIn("symbol_selected_reference: open_reference demo.py:4 (value = build())", rendered)
        self.assertIn("symbol_selected_navigation_target: open_symbol demo.py:1 (function build)", rendered)
        self.assertIn("symbol_definitions:", rendered)
        self.assertIn("> 1. open_symbol demo.py:1 (function build)", rendered)
        self.assertIn("- 2. open_symbol demo.py:10 (function build backup)", rendered)
        self.assertIn("symbol_references:", rendered)
        self.assertIn("Symbol focus", rendered)
        self.assertIn("symbol_focus_group: definitions", rendered)
        self.assertIn("symbol_focus_index: 1", rendered)
        self.assertIn("symbol_focus_target: open_symbol demo.py:1 (function build)", rendered)
        self.assertIn("symbol_primary_action: /symbol open primary", rendered)
        self.assertIn("symbol_secondary_action: /symbol open secondary", rendered)

    def test_task_panel_renders_existing_tasks(self) -> None:
        state = TuiState()

        rendered = state.render_task_panel(
            "abc123  status=running  kind=agent  description=Analyze repository"
        )

        self.assertIn("Tasks", rendered)
        self.assertIn("status=running", rendered)
        self.assertIn("Analyze repository", rendered)

    def test_task_panel_renders_workspace_maintenance_hints(self) -> None:
        state = TuiState()

        rendered = state.render_task_panel(
            "task-1  status=completed  kind=workspace  description=Repair isolated workspace  "
            "workspace_action=repair workspace_target=scout-agent health_before=unavailable health_after=cleanup_pending"
        )

        self.assertIn("Workspace hints", rendered)
        self.assertIn("task: task-1", rendered)
        self.assertIn("workspace_action: repair", rendered)
        self.assertIn("workspace_target: scout-agent", rendered)
        self.assertIn("health_before: unavailable", rendered)
        self.assertIn("health_after: cleanup_pending", rendered)

    def test_task_panel_renders_execution_focus_hints_for_child_task(self) -> None:
        state = TuiState(selected_task_id="task-123")

        rendered = state.render_task_panel(
            "task-123  status=running  kind=agent  description=Analyze repository",
            selected_task_id=state.selected_task_id,
            task_execution_metadata={
                "task_surface": "child_execution",
                "execution_mode": "read-only-subagent",
                "execution_policy": "review",
                "execution_policy_source": "session_execution_contract",
                "allowed_tools": ["read_file", "find_in_files"],
                "allowed_bash_prefixes": ["git diff", "git show"],
                "read_only_subagents": True,
                "workspace_mode": "snapshot",
                "workspace_health": "healthy",
            },
        )

        self.assertIn("Execution focus", rendered)
        self.assertIn("task: task-123", rendered)
        self.assertIn("task_surface: child_execution", rendered)
        self.assertIn("execution_mode: read-only-subagent", rendered)
        self.assertIn("execution_policy: review", rendered)
        self.assertIn("execution_policy_source: session_execution_contract", rendered)
        self.assertIn("allowed_tools: read_file, find_in_files", rendered)
        self.assertIn("allowed_bash_prefixes: git diff, git show", rendered)
        self.assertIn("read_only_subagents: yes", rendered)
        self.assertIn("workspace_mode: snapshot", rendered)
        self.assertIn("workspace_health: healthy", rendered)

    def test_task_panel_renders_checklist_hints(self) -> None:
        state = TuiState()

        rendered = state.render_task_panel(
            "session_checklist:\n"
            "session_checklist_tasks: 1\n"
            "session_checklist_in_progress: 1\n"
            "- check-123  status=in_progress  subject=Inspect runtime  "
            "selected_checklist_primary_action=checklist_mark_completed check-123  "
            "selected_checklist_secondary_action=checklist_reopen check-123  "
            "selected_checklist_target=check-123"
        )

        self.assertIn("Checklist hints", rendered)
        self.assertIn("task: check-123", rendered)
        self.assertIn("status: in_progress", rendered)
        self.assertIn("subject: Inspect runtime", rendered)
        self.assertIn("selected_checklist_primary_action: checklist_mark_completed check-123", rendered)
        self.assertIn("selected_checklist_secondary_action: checklist_reopen check-123", rendered)

    def test_task_panel_renders_checklist_duplicate_guard_block(self) -> None:
        state = TuiState()

        rendered = state.render_task_panel(
            "No background tasks.",
            checklist_duplicate_metadata={
                "checklist_duplicate_message": "Possible duplicate checklist task.",
                "checklist_duplicate_matched_task_id": "check-123",
                "checklist_duplicate_recommended_action": "Call session_task_get check-123, then session_task_update check-123.",
            },
        )

        self.assertIn("Checklist duplicate guard", rendered)
        self.assertIn("message: Possible duplicate checklist task.", rendered)
        self.assertIn("matched_task_id: check-123", rendered)
        self.assertIn("recommended_action: Call session_task_get check-123, then session_task_update check-123.", rendered)

    def test_task_panel_prefers_structured_checklist_section(self) -> None:
        state = TuiState(selected_checklist_task_id="check-456")

        rendered = state.render_task_panel(
            "session_checklist:\n"
            "session_checklist_tasks: 2\n"
            "session_checklist_in_progress: 1\n"
            "- check-123  status=pending  subject=Inspect runtime\n"
            "- check-456  status=in_progress  subject=Patch runtime\n\n"
            "No background tasks.",
            selected_checklist_task_id=state.selected_checklist_task_id,
            checklist_tasks=[
                {
                    "id": "check-123",
                    "subject": "Inspect runtime",
                    "status": "pending",
                    "owner": "",
                    "active_form": "Inspecting runtime",
                    "blocks": [],
                    "blocked_by": [],
                    "selected_checklist_primary_action": "checklist_mark_in_progress check-123",
                    "selected_checklist_secondary_action": "checklist_mark_completed check-123",
                    "selected_checklist_edit_subject_action": "checklist_set_subject check-123",
                    "selected_checklist_edit_description_action": "checklist_set_description check-123",
                    "selected_checklist_edit_owner_action": "checklist_set_owner check-123",
                    "selected_checklist_edit_active_form_action": "checklist_set_active_form check-123",
                    "selected_checklist_edit_blocks_action": "checklist_set_blocks check-123",
                    "selected_checklist_edit_blocked_by_action": "checklist_set_blocked_by check-123",
                    "selected_checklist_edit_metadata_action": "checklist_set_metadata check-123",
                    "selected_checklist_target": "check-123",
                },
                {
                    "id": "check-456",
                    "subject": "Patch runtime",
                    "status": "in_progress",
                    "owner": "assistant",
                    "active_form": "Patching runtime",
                    "blocks": ["check-123"],
                    "blocked_by": [],
                    "selected_checklist_primary_action": "checklist_mark_completed check-456",
                    "selected_checklist_secondary_action": "checklist_reopen check-456",
                    "selected_checklist_edit_subject_action": "checklist_set_subject check-456",
                    "selected_checklist_edit_description_action": "checklist_set_description check-456",
                    "selected_checklist_edit_owner_action": "checklist_set_owner check-456",
                    "selected_checklist_edit_active_form_action": "checklist_set_active_form check-456",
                    "selected_checklist_edit_blocks_action": "checklist_set_blocks check-456",
                    "selected_checklist_edit_blocked_by_action": "checklist_set_blocked_by check-456",
                    "selected_checklist_edit_metadata_action": "checklist_set_metadata check-456",
                    "selected_checklist_target": "check-456",
                },
            ],
        )

        self.assertIn("Session Checklist", rendered)
        self.assertIn("filter: all", rendered)
        self.assertIn("sort: recent_updated", rendered)
        self.assertIn("total: 2", rendered)
        self.assertIn("in_progress: 1", rendered)
        self.assertIn("in_progress (1):", rendered)
        self.assertIn("pending (1):", rendered)
        self.assertIn("  check-123 [pending] Inspect runtime", rendered)
        self.assertIn("> check-456 [in_progress] Patch runtime", rendered)
        self.assertIn("  owner: assistant", rendered)
        self.assertIn("  active_form: Patching runtime", rendered)
        self.assertIn("  dependencies: blocks=1 blocked_by=0", rendered)
        self.assertIn("Checklist focus", rendered)
        self.assertIn("> check-456  status=in_progress  subject=Patch runtime", rendered)
        self.assertIn("Checklist hints", rendered)
        self.assertIn("selected_checklist_edit_metadata_action: checklist_set_metadata check-456", rendered)
        self.assertNotIn("session_checklist:", rendered)

    def test_task_panel_renders_filtered_structured_checklist_section(self) -> None:
        state = TuiState(selected_checklist_task_id="check-456", checklist_filter="in_progress")

        rendered = state.render_task_panel(
            "No background tasks.",
            selected_checklist_task_id=state.selected_checklist_task_id,
            checklist_tasks=[
                {
                    "id": "check-123",
                    "subject": "Inspect runtime",
                    "status": "pending",
                    "owner": "",
                    "active_form": "Inspecting runtime",
                    "blocks": [],
                    "blocked_by": [],
                },
                {
                    "id": "check-456",
                    "subject": "Patch runtime",
                    "status": "in_progress",
                    "owner": "assistant",
                    "active_form": "Patching runtime",
                    "blocks": ["check-123"],
                    "blocked_by": [],
                    "selected_checklist_primary_action": "checklist_mark_completed check-456",
                    "selected_checklist_secondary_action": "checklist_reopen check-456",
                    "selected_checklist_target": "check-456",
                },
            ],
            checklist_filter=state.checklist_filter,
        )

        self.assertIn("filter: in_progress", rendered)
        self.assertIn("sort: recent_updated", rendered)
        self.assertIn("in_progress (1):", rendered)
        self.assertNotIn("pending (1):", rendered)
        self.assertNotIn("check-123 [pending]", rendered)
        self.assertIn("> check-456 [in_progress] Patch runtime", rendered)

    def test_task_panel_renders_owner_sorted_checklist_section(self) -> None:
        state = TuiState(selected_checklist_task_id="check-2", checklist_sort="owner")

        rendered = state.render_task_panel(
            "No background tasks.",
            selected_checklist_task_id=state.selected_checklist_task_id,
            checklist_filter=state.checklist_filter,
            checklist_sort=state.checklist_sort,
            checklist_tasks=[
                {
                    "id": "check-1",
                    "subject": "Beta",
                    "status": "pending",
                    "owner": "zoe",
                    "active_form": "Doing beta",
                    "blocks": [],
                    "blocked_by": [],
                },
                {
                    "id": "check-2",
                    "subject": "Alpha",
                    "status": "pending",
                    "owner": "alice",
                    "active_form": "Doing alpha",
                    "blocks": [],
                    "blocked_by": [],
                },
            ],
        )

        self.assertIn("sort: owner", rendered)
        alpha_index = rendered.index("> check-2 [pending] Alpha")
        beta_index = rendered.index("  check-1 [pending] Beta")
        self.assertLess(alpha_index, beta_index)

    def test_task_panel_renders_selected_checklist_focus(self) -> None:
        state = TuiState(selected_checklist_task_id="check-456")

        rendered = state.render_task_panel(
            "session_checklist:\n"
            "session_checklist_tasks: 2\n"
            "session_checklist_in_progress: 1\n"
            "- check-123  status=pending  subject=Inspect runtime\n"
            "- check-456  status=in_progress  subject=Patch runtime\n\n"
            "No background tasks.",
            selected_checklist_task_id=state.selected_checklist_task_id,
        )

        self.assertIn("Checklist focus", rendered)
        self.assertIn("  check-123  status=pending  subject=Inspect runtime", rendered)
        self.assertIn("> check-456  status=in_progress  subject=Patch runtime", rendered)

    def test_task_detail_panel_renders_selected_task(self) -> None:
        state = TuiState()
        state.set_task_detail(task_id="task-123", text="task_id: task-123\nstatus: running")

        rendered = state.render_task_detail_panel(state.task_detail_text)

        self.assertIn("Task Detail [detail] [task-123]", rendered)
        self.assertIn("status: running", rendered)

    def test_task_detail_panel_prefers_structured_workspace_metadata(self) -> None:
        state = TuiState()
        state.set_task_detail(
            task_id="task-123",
            text="task_id: task-123\nstatus: completed",
            workspace_metadata={
                "workspace_action": "cleanup",
                "workspace_target": "orphan-agent",
                "workspace_health_before": "orphaned",
                "workspace_health_after": "healthy",
                "workspace_recommended_actions": [
                    "/workspaces list",
                    "/workspaces cleanup",
                    "/workspaces cleanup apply orphan-agent",
                ],
                "workspace_planned_paths": ["C:/tmp/orphan-agent"],
                "workspace_applied_paths": ["C:/tmp/orphan-agent"],
                "workspace_failure_reason": "cleanup backend failed",
            },
        )

        rendered = state.render_task_detail_panel(state.task_detail_text)

        self.assertIn("Workspace", rendered)
        self.assertIn("workspace_action: cleanup", rendered)
        self.assertIn("workspace_target: orphan-agent", rendered)
        self.assertIn("workspace_health_before: orphaned", rendered)
        self.assertIn("workspace_health_after: healthy", rendered)
        self.assertIn("workspace_recommended_actions:", rendered)
        self.assertIn("- /workspaces list", rendered)
        self.assertIn("- /workspaces cleanup", rendered)
        self.assertIn("- /workspaces cleanup apply orphan-agent", rendered)
        self.assertIn("workspace_planned_paths:", rendered)
        self.assertIn("workspace_applied_paths:", rendered)
        self.assertIn("workspace_failure_reason: cleanup backend failed", rendered)

    def test_task_detail_panel_prefers_structured_execution_metadata(self) -> None:
        state = TuiState()
        state.set_task_detail(
            task_id="task-123",
            text="task_id: task-123\nstatus: completed",
            execution_metadata={
                "task_surface": "child_execution",
                "execution_mode": "read-only-subagent",
                "execution_policy": "review",
                "execution_policy_source": "session_execution_contract",
                "allowed_tools": ["read_file", "find_in_files"],
                "allowed_bash_prefixes": ["git diff", "git show"],
                "read_only_subagents": True,
                "workspace_mode": "snapshot",
                "workspace_health": "healthy",
            },
        )

        rendered = state.render_task_detail_panel(state.task_detail_text)

        self.assertIn("Execution", rendered)
        self.assertIn("task_surface: child_execution", rendered)
        self.assertIn("execution_mode: read-only-subagent", rendered)
        self.assertIn("execution_policy: review", rendered)
        self.assertIn("execution_policy_source: session_execution_contract", rendered)
        self.assertIn("allowed_tools: read_file, find_in_files", rendered)
        self.assertIn("allowed_bash_prefixes: git diff, git show", rendered)
        self.assertIn("read_only_subagents: yes", rendered)
        self.assertIn("workspace_mode: snapshot", rendered)
        self.assertIn("workspace_health: healthy", rendered)

    def test_task_detail_panel_prefers_structured_checklist_metadata(self) -> None:
        state = TuiState()
        state.set_task_detail(
            task_id="check-123",
            text="task_id: check-123\nstatus: in_progress",
            checklist_metadata={
                "checklist_task_id": "check-123",
                "checklist_task_list_id": "session-1",
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
                    "session_task_get check-123",
                    "session_task_update check-123 status=completed",
                    "session_task_list",
                ],
                "checklist_duplicate_message": "Possible duplicate checklist task.",
                "checklist_duplicate_reason": "Matched existing checklist task by subject, description, and active_form.",
                "checklist_duplicate_matched_task_id": "check-123",
                "checklist_duplicate_recommended_action": "Call session_task_get check-123, then session_task_update check-123.",
                "selected_checklist_primary_action": "checklist_mark_completed check-123",
                "selected_checklist_secondary_action": "checklist_reopen check-123",
                "selected_checklist_edit_subject_action": "checklist_set_subject check-123",
                "selected_checklist_edit_description_action": "checklist_set_description check-123",
                "selected_checklist_edit_owner_action": "checklist_set_owner check-123",
                "selected_checklist_edit_active_form_action": "checklist_set_active_form check-123",
                "selected_checklist_edit_blocks_action": "checklist_set_blocks check-123",
                "selected_checklist_edit_blocked_by_action": "checklist_set_blocked_by check-123",
                "selected_checklist_edit_metadata_action": "checklist_set_metadata check-123",
                "selected_checklist_tertiary_action": "session_task_list",
                "selected_checklist_target": "check-123",
            },
        )

        rendered = state.render_task_detail_panel(state.task_detail_text)

        self.assertIn("Checklist", rendered)
        self.assertIn("checklist_task_id: check-123", rendered)
        self.assertIn("checklist_subject: Inspect runtime", rendered)
        self.assertIn("checklist_description: Inspect session.py", rendered)
        self.assertIn("checklist_active_form: Inspecting runtime", rendered)
        self.assertIn("checklist_status: in_progress", rendered)
        self.assertIn("checklist_owner: assistant", rendered)
        self.assertIn("checklist_blocks:", rendered)
        self.assertIn("- task-b", rendered)
        self.assertIn("checklist_blocked_by:", rendered)
        self.assertIn("- task-a", rendered)
        self.assertIn("checklist_metadata:", rendered)
        self.assertIn("- area: runtime", rendered)
        self.assertIn("checklist_recommended_actions:", rendered)
        self.assertIn("- session_task_get check-123", rendered)
        self.assertIn("checklist_duplicate_guard:", rendered)
        self.assertIn("checklist_duplicate_matched_task_id: check-123", rendered)
        self.assertIn("checklist_duplicate_recommended_action: Call session_task_get check-123, then session_task_update check-123.", rendered)
        self.assertIn("selected_checklist_primary_action: checklist_mark_completed check-123", rendered)
        self.assertIn("selected_checklist_secondary_action: checklist_reopen check-123", rendered)
        self.assertIn("selected_checklist_edit_subject_action: checklist_set_subject check-123", rendered)
        self.assertIn(
            "selected_checklist_edit_description_action: checklist_set_description check-123",
            rendered,
        )
        self.assertIn("selected_checklist_edit_owner_action: checklist_set_owner check-123", rendered)
        self.assertIn("selected_checklist_edit_active_form_action: checklist_set_active_form check-123", rendered)
        self.assertIn("selected_checklist_edit_blocks_action: checklist_set_blocks check-123", rendered)
        self.assertIn(
            "selected_checklist_edit_blocked_by_action: checklist_set_blocked_by check-123",
            rendered,
        )
        self.assertIn(
            "selected_checklist_edit_metadata_action: checklist_set_metadata check-123",
            rendered,
        )

    def test_task_detail_panel_renders_workspace_hint_block(self) -> None:
        state = TuiState()
        state.set_task_detail(
            task_id="task-123",
            text=(
                "task_id: task-123\n"
                "status: completed\n"
                "workspace_action: cleanup\n"
                "workspace_target: orphan-agent\n"
                "workspace_health_before: orphaned\n"
                "workspace_health_after: healthy\n"
                "workspace_health: healthy\n"
                "workspace_recommended_actions: /workspaces list\n"
                "workspace_planned_paths:\n"
                "- C:/tmp/orphan-agent\n"
                "workspace_applied_paths:\n"
                "- C:/tmp/orphan-agent\n"
                "workspace_failure_reason: cleanup backend failed\n"
                "selected_workspace_primary_action: workspace_cleanup_preview\n"
                "selected_workspace_secondary_action: workspace_cleanup_apply orphan-agent\n"
                "selected_workspace_tertiary_action: /workspaces list\n"
                "selected_workspace_target: orphan-agent\n"
            ),
        )

        rendered = state.render_task_detail_panel(state.task_detail_text)

        self.assertIn("Task Detail [detail] [task-123]", rendered)
        self.assertIn("Workspace", rendered)
        self.assertIn("workspace_action: cleanup", rendered)
        self.assertIn("workspace_target: orphan-agent", rendered)
        self.assertIn("workspace_health_before: orphaned", rendered)
        self.assertIn("workspace_health_after: healthy", rendered)
        self.assertIn("workspace_recommended_actions: /workspaces list", rendered)
        self.assertIn("workspace_planned_paths:", rendered)
        self.assertIn("- C:/tmp/orphan-agent", rendered)
        self.assertIn("workspace_applied_paths:", rendered)
        self.assertIn("workspace_failure_reason: cleanup backend failed", rendered)
        self.assertIn("selected_workspace_primary_action: workspace_cleanup_preview", rendered)
        self.assertIn("selected_workspace_secondary_action: workspace_cleanup_apply orphan-agent", rendered)
        self.assertIn("selected_workspace_target: orphan-agent", rendered)

    def test_task_detail_panel_renders_execution_hint_block_from_text(self) -> None:
        state = TuiState()
        state.set_task_detail(
            task_id="task-123",
            text=(
                "task_id: task-123\n"
                "status: running\n"
                "execution_contract:\n"
                "- task_surface: background_execution\n"
                "- execution_mode: background\n"
                "- execution_policy: review\n"
                "- execution_policy_source: session_execution_contract\n"
                "- allowed_tools: read_file, find_in_files\n"
                "- allowed_bash_prefixes: git diff, git show\n"
                "- read_only_subagents: yes\n"
                "- workspace_mode: snapshot\n"
                "- workspace_health: healthy\n"
            ),
        )

        rendered = state.render_task_detail_panel(state.task_detail_text)

        self.assertIn("Execution", rendered)
        self.assertIn("task_surface: background_execution", rendered)
        self.assertIn("execution_mode: background", rendered)
        self.assertIn("allowed_tools: read_file, find_in_files", rendered)
        self.assertIn("allowed_bash_prefixes: git diff, git show", rendered)
        self.assertIn("read_only_subagents: yes", rendered)

    def test_task_detail_panel_can_switch_subviews(self) -> None:
        state = TuiState()
        state.set_task_detail(
            task_id="task-123",
            text="task detail",
            execution_metadata={"task_surface": "child_execution", "execution_mode": "scout"},
        )
        state.set_task_advisor_detail(task_id="task-123", text="advisor detail")
        rendered_advisor = state.render_task_detail_panel(state.task_advisor_text)
        state.set_task_drift_detail(task_id="task-123", text="drift detail")
        rendered_drift = state.render_task_detail_panel(state.task_drift_text)
        state.set_task_detail_view("detail")
        rendered_detail = state.render_task_detail_panel(state.task_detail_text)

        self.assertIn("Task Detail [advisor] [task-123]", rendered_advisor)
        self.assertIn("advisor detail", rendered_advisor)
        self.assertIn("Task Detail [drift] [task-123]", rendered_drift)
        self.assertIn("drift detail", rendered_drift)
        self.assertIn("Task Detail [detail] [task-123]", rendered_detail)
        self.assertNotIn("Execution", rendered_advisor)
        self.assertNotIn("Execution", rendered_drift)

    def test_plan_panel_renders_active_plan_detail(self) -> None:
        state = TuiState()

        rendered = state.render_plan_panel(
            "artifact_id: plan-1\nactive: yes\ngoal: map runtime\nadvisor_review:\n- status: block\nscout_outputs:\n- scout-1: status=completed"
        )

        self.assertIn("Active Plan", rendered)
        self.assertIn("artifact_id: plan-1", rendered)
        self.assertIn("advisor_review:", rendered)
        self.assertIn("scout_outputs:", rendered)

    def test_plan_panel_can_switch_subviews(self) -> None:
        state = TuiState()

        state.set_plan_panel_view("scouts")
        rendered_scouts = state.render_plan_panel("scout_outputs:\n- scout-1: status=completed")
        state.set_plan_panel_view("execution")
        rendered_execution = state.render_plan_panel("execution_tasks:\n- exec-1: status=running")
        state.set_plan_panel_view("lineage")
        rendered_lineage = state.render_plan_panel("lineage:\n- plan-1 (current, active)")
        state.set_plan_panel_view("audit")
        rendered_audit = state.render_plan_panel("lineage_audit_summary:\n- artifacts: 2")
        state.set_plan_panel_view("advisor")
        rendered_advisor = state.render_plan_panel("advisor_review:\n- status: block")
        state.set_plan_panel_view("timeline")
        rendered_timeline = state.render_plan_panel("timeline:\n- 2026-01-01T00:00:00Z [plan] created")

        self.assertIn("Active Plan [scouts]", rendered_scouts)
        self.assertIn("scout_outputs:", rendered_scouts)
        self.assertIn("Active Plan [execution]", rendered_execution)
        self.assertIn("execution_tasks:", rendered_execution)
        self.assertIn("Active Plan [lineage]", rendered_lineage)
        self.assertIn("lineage:", rendered_lineage)
        self.assertIn("Active Plan [audit]", rendered_audit)
        self.assertIn("lineage_audit_summary:", rendered_audit)
        self.assertIn("Active Plan [advisor]", rendered_advisor)
        self.assertIn("advisor_review:", rendered_advisor)
        self.assertIn("Active Plan [timeline]", rendered_timeline)
        self.assertIn("timeline:", rendered_timeline)

    def test_plan_panel_tracks_selected_scout_index(self) -> None:
        state = TuiState()

        state.move_plan_scout_selection(1)
        state.move_plan_scout_selection(1)
        state.move_plan_scout_selection(-1)
        state.move_plan_scout_selection(-5)

        self.assertEqual(state.selected_plan_scout_index, 0)

    def test_plan_panel_tracks_scout_detail_mode(self) -> None:
        state = TuiState()

        state.set_plan_scout_detail_mode("full")
        self.assertEqual(state.plan_scout_detail_mode, "full")
        state.set_plan_scout_detail_mode("compact")
        self.assertEqual(state.plan_scout_detail_mode, "compact")

    def test_plan_panel_tracks_selected_execution_index(self) -> None:
        state = TuiState()

        state.move_plan_execution_selection(1)
        state.move_plan_execution_selection(1)
        state.move_plan_execution_selection(-1)
        state.move_plan_execution_selection(-5)

        self.assertEqual(state.selected_plan_execution_index, 0)

    def test_plan_panel_tracks_selected_lineage_index(self) -> None:
        state = TuiState()

        state.move_plan_lineage_selection(1)
        state.move_plan_lineage_selection(1)
        state.move_plan_lineage_selection(-1)
        state.move_plan_lineage_selection(-5)

        self.assertEqual(state.selected_plan_lineage_index, 0)

    def test_plan_panel_tracks_selected_timeline_index(self) -> None:
        state = TuiState()

        state.move_plan_timeline_selection(1)
        state.move_plan_timeline_selection(1)
        state.move_plan_timeline_selection(-1)
        state.move_plan_timeline_selection(-5)

        self.assertEqual(state.selected_plan_timeline_index, 0)

    def test_plan_panel_cycles_timeline_filter(self) -> None:
        state = TuiState()

        state.cycle_plan_timeline_filter()
        self.assertEqual(state.plan_timeline_filter, "plan")
        state.cycle_plan_timeline_filter()
        self.assertEqual(state.plan_timeline_filter, "scout")

    def test_plan_panel_cycles_timeline_delta_and_focus_modes(self) -> None:
        state = TuiState()

        state.cycle_plan_timeline_delta_mode()
        self.assertEqual(state.plan_timeline_delta_mode, "before-drift")
        state.cycle_plan_timeline_delta_mode()
        self.assertEqual(state.plan_timeline_delta_mode, "after-drift")
        state.cycle_plan_timeline_focus_mode()
        self.assertEqual(state.plan_timeline_focus_mode, "scout")
        state.cycle_plan_timeline_compare_mode()
        self.assertEqual(state.plan_timeline_compare_mode, "after-drift-vs-all")

    def test_advisor_panel_renders_status_detail(self) -> None:
        state = TuiState()

        rendered = state.render_advisor_panel(
            "Advisor: advisor-model\n"
            "Mode: interactive-review\n"
            "Execution constraints: read-only\n"
            "Constraint source: plan_drift_block"
        )

        self.assertIn("Advisor Status", rendered)
        self.assertIn("Advisor: advisor-model", rendered)
        self.assertIn("Constraint source: plan_drift_block", rendered)

    def test_status_panel_summarizes_provider_and_config(self) -> None:
        state = TuiState(busy=True)

        rendered = state.render_status_panel(
            provider_text=(
                "provider: openai-compatible\n"
                "model: gpt-4.1-mini\n"
                "tool_calling: yes\n"
                "streaming: no\n"
            ),
            config_text=(
                "provider: openai-compatible\n"
                "model: gpt-4.1-mini\n"
                "mcp_servers: 1\n"
                "mcp_failed_servers: 1\n"
                "enabled_skills: 2\n"
                "session_id: demo-session\n"
            ),
            status_metadata={
                "status_session_id": "demo-session",
                "status_provider": "openai-compatible",
                "status_model": "gpt-4.1-mini",
                "status_advisor_model": "gpt-4.1-mini",
                "status_advisor_mode": "off",
                "status_mode": "main",
                "status_context_usage": "42 / 4096 (1.0%)",
                "status_memory_summary": "none",
                "status_memory_compaction": "ok",
                "status_memory_last_operation": "none",
                "status_budget_reason": "context summary chars 15 >= warning threshold 12",
                "status_budget_pressure": "warning",
                "status_background_summary": "Background session completed.",
                "status_background_notification_count": 1,
                "status_background_latest_handoff": "bg-456",
                "status_runtime_progress_summary": "read_file: waiting for approval (read)",
                "status_runtime_progress_kind": "tool_waiting_for_approval",
                "status_runtime_active_tool_name": "read_file",
                "status_runtime_active_tool_status": "waiting_for_approval",
                "status_runtime_active_tool_input": '{"path":"demo.py"}',
                "status_runtime_last_tool_name": "read_file",
                "status_runtime_last_tool_status": "ok",
                "status_runtime_last_tool_summary": "ok (25ms)",
                "status_runtime_parallel_batch_active": True,
                "status_runtime_parallel_batch_size": 2,
                "status_runtime_last_result_summary": "ok results=2",
                "status_runtime_compact_recovery_summary": "retry succeeded after recovery compact",
                "status_prompt_prefix_segment_count": 9,
                "status_prompt_prefix_stable_chars": 1400,
                "status_prompt_prefix_dynamic_tail_chars": 260,
                "status_prompt_prefix_reduction_tier": "artifact_indirection",
                "status_prompt_prefix_planner_mode": "provider_hinted",
                "status_prompt_prefix_planner_reason": "artifact_indirection_active",
                "status_prompt_prefix_planner_summary": "preserved_groups=1 downgraded_groups=0 eligible_segments=4",
                "status_prompt_prefix_costed_planner_mode": "selected",
                "status_prompt_prefix_costed_planner_reason": "artifact_indirection_active",
                "status_prompt_prefix_target_tokens_to_shed": 2200,
                "status_prompt_prefix_estimated_input_tokens": 7200,
                "status_prompt_prefix_estimated_stable_prefix_tokens": 4100,
                "status_prompt_prefix_estimated_dynamic_tail_tokens": 3100,
                "status_prompt_prefix_selected_candidate_count": 1,
                "status_prompt_prefix_selected_candidate_summary": "artifact_indirection shed_tokens=2200 damage=1",
                "status_prompt_prefix_remaining_estimated_overage": 0,
                "status_prompt_prefix_prefix_damage_score": 1,
                "status_prompt_prefix_orchestration_mode": "selected",
                "status_prompt_prefix_orchestration_reason": "artifact_indirection_active",
                "status_prompt_prefix_orchestration_selected_candidate_count": 1,
                "status_prompt_prefix_orchestration_selected_candidate_summary": "artifact_indirection tool_use_id=tool-1 shed_tokens=2200 damage=1",
                "status_prompt_prefix_orchestration_remaining_estimated_overage": 0,
                "status_prompt_prefix_orchestration_requires_full_compaction": False,
                "status_prompt_prefix_signature": "prefixsig0000001",
                "status_prompt_prefix_previous_signature": "prefixsig0000000",
                "status_prompt_prefix_changed": True,
                "status_prompt_prefix_change_reason": "provider_view_messages",
                "status_provider_view_assembly_summary": "replacement-aware=yes microcompact-aware=no",
                "status_working_set_summary": "mix: diff_backed=1 context_only=0 explicit=0 task=0 plan=0 change=1",
                "status_focused_file_summary": "demo.py (change)",
                "status_plan_summary": "map runtime",
                "status_active_task_count": 2,
                "status_task_surface_summary": "background_execution=1, other_task=1",
                "status_project_context_summary": "memory=none skills=2 plugins=8",
                "status_project_context_reload_health": "latest reload: none",
                "status_project_context_issue": "none",
                "status_skills_health": "loaded=2 enabled=2 manual_enabled=0 manual_disabled=0",
                "status_skill_registry_summary": "registered=2 enabled=2 inactive=0 diagnostics=0",
                "status_skill_prompt_summary": "auto_enabled=2 manual_enabled=0 inactive=0",
                "status_skill_reload_state": "latest reload: none",
                "status_skill_manual_overrides": "enabled=0 disabled=0",
                "status_skill_diagnostics": 0,
                "status_plugins_health": "loaded=8 enabled=8 diagnostics=0",
                "status_plugin_registry_summary": "registered=8 enabled=8 disabled=0 diagnostics=0",
                "status_plugin_reload_state": "latest reload: plugins unchanged",
                "status_plugin_manual_overrides": "enabled=1 disabled=0",
                "status_mcp_health": "servers=2 connected=1 failed=1 retrying=0",
                "status_mcp_issue": "mcp failed servers: 1",
                "status_permission_mode": "default",
                "status_permission_summary": "mode=default workspace_rules=0 session_rules=0",
                "status_workspace_anomaly": "none",
                "status_runtime_health_alert": "mcp failed servers: 1",
                "status_workspace_summary": "mode=main health=healthy focused=demo.py",
                "status_workspace_mode": "main",
                "status_workspace_health": "healthy",
                "status_workspace_recovery": "/workspaces list",
                "status_workspace_recommended_actions": ["/workspaces list", "/workspaces cleanup"],
                "status_workspace_primary_action": "workspace_cleanup_preview",
                "status_workspace_secondary_action": "/workspaces list",
                "status_workspace_tertiary_action": "none",
                "status_action_groups": {
                    "go_to_focused_file": ["/files focused"],
                    "inspect_changes": ["/changes working-set", "/files context"],
                    "inspect_task": ["/tasks active"],
                    "inspect_project_context_health": ["/project-context", "/plugins", "/skills"],
                },
                "status_explicit_context_entry_count": 0,
                "status_unresolved_explicit_context_entry_count": 0,
                "status_next_actions": ["/files focused", "/changes working-set", "/tasks active"],
            },
            plugin_surface_metadata={
                "plugin_registry_summary": "registered=8 enabled=8 disabled=0 diagnostics=1",
                "plugin_diagnostic_count": 1,
                "plugin_manual_enabled_count": 1,
                "plugin_manual_disabled_count": 0,
                "plugin_selected_summary": "review (commands)",
                "plugin_reload_state": {"summary": "latest reload: diagnostics changed"},
                "plugin_action_groups": {
                    "inspect_plugin_registry": ["/plugins"],
                    "inspect_project_context_plugins": ["/project-context plugins"],
                    "inspect_plugin_reload_state": ["/project-context reload-status", "/context-refresh"],
                    "inspect_selected_plugin": ["/plugin show review"],
                    "toggle_selected_plugin": ["/plugin disable review"],
                },
            },
            skills_surface_metadata={
                "skill_registry_summary": "registered=2 enabled=2 inactive=0 diagnostics=1",
                "skill_enabled_count": 2,
                "skill_disabled_count": 0,
                "skill_inactive_count": 0,
                "skill_diagnostic_count": 1,
                "skill_manual_enabled_count": 1,
                "skill_manual_disabled_count": 0,
                "skill_builtin_count": 1,
                "skill_project_local_count": 1,
                "skill_plugin_contributed_count": 0,
                "skill_prompt_composition_summary": "auto_enabled=1 manual_enabled=1 inactive=0",
                "skill_selected_summary": "review (project-local, manual_enabled)",
                "skill_reload_state": {"summary": "latest reload: skill content changed"},
                "skill_action_groups": {
                    "inspect_skill_registry": ["/skills"],
                    "inspect_project_context_skills": ["/project-context skills"],
                    "inspect_skill_reload_state": ["/project-context reload-status", "/skills-reload"],
                    "inspect_selected_skill": ["/project-context skills"],
                    "toggle_selected_skill": ["/skills-disable review"],
                },
            },
        )

        self.assertIn("busy: yes", rendered)
        self.assertIn("Session Identity", rendered)
        self.assertIn("Model and Provider", rendered)
        self.assertIn("Memory Lifecycle", rendered)
        self.assertIn("Background Notifications", rendered)
        self.assertIn("Workspace State", rendered)
        self.assertIn("Active Workflow", rendered)
        self.assertIn("Project-Context Health", rendered)
        self.assertIn("Skill Registry", rendered)
        self.assertIn("Next Actions", rendered)
        self.assertIn("provider: openai-compatible", rendered)
        self.assertIn("model: gpt-4.1-mini", rendered)
        self.assertIn("session_id: demo-session", rendered)
        self.assertIn("context_usage: 42 / 4096 (1.0%)", rendered)
        self.assertIn("project_context: memory=none skills=2 plugins=8", rendered)
        self.assertIn("skill_registry: registered=2 enabled=2 inactive=0 diagnostics=0", rendered)
        self.assertIn("skill_prompt_composition: auto_enabled=2 manual_enabled=0 inactive=0", rendered)
        self.assertIn("skill_reload_state: latest reload: none", rendered)
        self.assertIn("manual_skill_overrides: enabled=0 disabled=0", rendered)
        self.assertIn("skill_registry: registered=2 enabled=2 inactive=0 diagnostics=1", rendered)
        self.assertIn("skill_prompt_composition: auto_enabled=1 manual_enabled=1 inactive=0", rendered)
        self.assertIn("skill_sources: builtin=1 project_local=1 plugin_contributed=0", rendered)
        self.assertIn("skill_status: enabled=2 disabled=0 inactive=0", rendered)
        self.assertIn("skill_reload_state: latest reload: skill content changed", rendered)
        self.assertIn("skill_diagnostics: 1", rendered)
        self.assertIn("selected_skill: review (project-local, manual_enabled)", rendered)
        self.assertIn("- inspect skill registry: /skills", rendered)
        self.assertIn("- inspect skill reload state: /project-context reload-status | /skills-reload", rendered)
        self.assertIn("mcp_health: servers=2 connected=1 failed=1 retrying=0", rendered)
        self.assertIn("permission_summary: mode=default workspace_rules=0 session_rules=0", rendered)
        self.assertIn("runtime_health_alert: mcp failed servers: 1", rendered)
        self.assertIn("plugin_registry: registered=8 enabled=8 disabled=0 diagnostics=0", rendered)
        self.assertIn("plugin_reload_state: latest reload: plugins unchanged", rendered)
        self.assertIn("manual_plugin_overrides: enabled=1 disabled=0", rendered)
        self.assertIn("workspace_anomaly: none", rendered)
        self.assertIn("workspace_recovery: /workspaces list", rendered)
        self.assertIn("workspace_recommended_actions: /workspaces list, /workspaces cleanup", rendered)
        self.assertIn("status_workspace_primary_action: workspace_cleanup_preview", rendered)
        self.assertIn("runtime progress: read_file: waiting for approval (read)", rendered)
        self.assertIn("active tool: waiting_for_approval read_file", rendered)
        self.assertIn('active tool input: {"path":"demo.py"}', rendered)
        self.assertIn("last tool outcome: read_file | ok | ok (25ms)", rendered)
        self.assertIn("parallel batch: active size=2", rendered)
        self.assertIn("last tool-result summary: ok results=2", rendered)
        self.assertIn("prompt prefix: segments=9 stable_chars=1400 dynamic_tail_chars=260", rendered)
        self.assertIn("provider-view assembly: replacement-aware=yes microcompact-aware=no", rendered)
        self.assertIn("provider-view planner: provider_hinted", rendered)
        self.assertIn("prefix planner reason: artifact_indirection_active", rendered)
        self.assertIn("costed planner mode: selected", rendered)
        self.assertIn("costed planner reason: artifact_indirection_active", rendered)
        self.assertIn("target tokens to shed: 2200", rendered)
        self.assertIn(
            "selected candidates: artifact_indirection shed_tokens=2200 damage=1",
            rendered,
        )
        self.assertIn("remaining estimated overage: 0", rendered)
        self.assertIn("provider-view orchestration: selected", rendered)
        self.assertIn("orchestration reason: artifact_indirection_active", rendered)
        self.assertIn(
            "orchestration selected candidates: artifact_indirection tool_use_id=tool-1 shed_tokens=2200 damage=1",
            rendered,
        )
        self.assertIn("full compaction required: no", rendered)
        self.assertIn("prefix reduction tier: artifact_indirection", rendered)
        self.assertIn("prefix signature: prefixsig0000001", rendered)
        self.assertIn("prefix preserved: no", rendered)
        self.assertIn("prefix change reason: provider_view_messages", rendered)
        self.assertIn("budget pressure: context summary chars 15 >= warning threshold 12", rendered)
        self.assertIn("compact recovery: retry succeeded after recovery compact", rendered)
        self.assertIn("Plugin Registry", rendered)
        self.assertIn("plugin_diagnostics: 1", rendered)
        self.assertIn("selected_plugin: review (commands)", rendered)
        self.assertIn("- inspect plugin registry: /plugins", rendered)
        self.assertIn("- inspect selected plugin: /plugin show review", rendered)
        self.assertIn("- inspect focused file: /files focused", rendered)
        self.assertIn("- inspect project-context health: /project-context | /plugins | /skills", rendered)
        self.assertIn("Capabilities", rendered)
        self.assertIn("tool_calling: yes", rendered)

    def test_status_panel_includes_mcp_diagnosis(self) -> None:
        state = TuiState(mcp_diagnostic_text="server: fake\ntool: echo_text\nok: no\nsource: model")

        rendered = state.render_status_panel(
            provider_text="provider: openai-compatible\nmodel: demo\ntool_calling: yes\n",
            config_text="provider: openai-compatible\nmodel: demo\nsession_id: abc\n",
        )

        self.assertIn("MCP diagnosis", rendered)
        self.assertIn("server: fake", rendered)
        self.assertIn("source: model", rendered)

    def test_approval_panel_renders_pending_request(self) -> None:
        state = TuiState()
        state.pending_approval = PendingApproval(
            request=ApprovalRequest(
                tool_name="bash",
                reason="Run a shell command in the workspace.",
                risk_level="shell_dangerous",
                approval_key="shell_dangerous",
                details='command="Remove-Item demo.txt"',
                command="Remove-Item demo.txt",
                target_paths=("demo.txt",),
                permission_rules=("ask:path:demo",),
                decision_reason="Matched ask rules: ask:path:demo",
                command_mode_name="review",
                command_mode_source="repl:/review",
                command_mode_allowed_prefixes=("git diff", "git show"),
            )
        )

        rendered = state.render_approval_panel()

        self.assertIn("Approval", rendered)
        self.assertIn("risk: shell_dangerous", rendered)
        self.assertIn("policy: Matched ask rules: ask:path:demo", rendered)
        self.assertIn("command_mode:", rendered)
        self.assertIn("- mode: review", rendered)
        self.assertIn("- source: repl:/review", rendered)
        self.assertIn("- allowed_prefixes: git diff, git show", rendered)
        self.assertIn("matched_rules:", rendered)
        self.assertIn("- ask:path:demo", rendered)
        self.assertIn("paths:", rendered)
        self.assertIn("- demo.txt", rendered)
        self.assertIn("command: Remove-Item demo.txt", rendered)
        self.assertIn("Preview mirrored in Changes panel.", rendered)
        self.assertIn("Ctrl+O allow once", rendered)
        self.assertNotIn("Recent changes", rendered)

    def test_approval_panel_renders_workspace_cleanup_request_with_details(self) -> None:
        state = TuiState()
        state.pending_approval = PendingApproval(
            request=ApprovalRequest(
                tool_name="workspace_cleanup",
                reason="Delete orphaned isolated workspaces from the local .pyclaude directory.",
                risk_level="delete",
                approval_key="workspace_cleanup_delete:orphan-agent",
                details=(
                    "Delete orphaned isolated workspaces.\n"
                    "selector: orphan-agent\n"
                    "planned_deletions: 1\n"
                    "planned_targets:\n"
                        "- workspace=snapshot health=orphaned label=orphan-agent origin=C:/tmp cwd=C:/tmp/orphan-agent cleanup=none session_refs=0 background_refs=0"
                ),
                target_paths=(".pyclaude/workspaces/orphan-agent",),
            )
        )

        rendered = state.render_approval_panel()

        self.assertIn("Approval", rendered)
        self.assertIn("risk: delete", rendered)
        self.assertIn("tool: workspace_cleanup", rendered)
        self.assertIn("paths:", rendered)
        self.assertIn("- .pyclaude/workspaces/orphan-agent", rendered)
        self.assertIn("details:", rendered)
        self.assertIn("- selector: orphan-agent", rendered)
        self.assertIn("- planned_deletions: 1", rendered)
        self.assertNotIn("Preview mirrored in Changes panel.", rendered)

    def test_failed_bash_tool_event_renders_command_mode_context(self) -> None:
        state = TuiState()

        state.record_runtime_event(
            RuntimeEvent(
                kind="tool_started",
                message='{"command":"git diff | Set-Content out.txt"}',
                tool_name="bash",
                tool_call_id="tool-9",
            )
        )
        state.record_runtime_event(
            RuntimeEvent(
                kind="tool_failed",
                message='Bash command is not allowed in command mode "review".',
                tool_name="bash",
                tool_call_id="tool-9",
                duration_ms=8,
                is_error=True,
                command_mode_name="review",
                command_mode_allowed_prefixes=("git diff", "git show"),
                command_mode_violating_segment="Set-Content out.txt",
                command_mode_violating_segment_index=2,
            )
        )

        rendered = state.render_tool_logs()

        self.assertIn("[ERROR] bash (8ms)", rendered)
        self.assertIn("detail: Bash command is not allowed in command mode \"review\".", rendered)
        self.assertIn("command_mode:", rendered)
        self.assertIn("- mode: review", rendered)
        self.assertIn("- allowed_prefixes: git diff, git show", rendered)
        self.assertIn("- violating_segment: segment 2: Set-Content out.txt", rendered)

    def test_failed_permission_tool_event_is_reflected_in_chat_error_block(self) -> None:
        state = TuiState()
        state.start_turn("Try restricted command")

        state.record_runtime_event(
            RuntimeEvent(
                kind="tool_failed",
                message='Tool "bash" is denied by session permission rules: deny:path:secrets.',
                tool_name="bash",
                tool_call_id="tool-10",
                duration_ms=5,
                is_error=True,
                decision_reason="Matched deny rules: deny:path:secrets [path: secrets.txt]",
                permission_rules=("deny:path:secrets [path: secrets.txt]",),
            )
        )

        rendered = state.render_chat()

        self.assertIn("[Error]\nturn_error:", rendered)
        self.assertIn('- message: Tool "bash" is denied by session permission rules: deny:path:secrets.', rendered)
        self.assertIn("- policy: Matched deny rules: deny:path:secrets [path: secrets.txt]", rendered)
        self.assertIn("- matched_rules:", rendered)
        self.assertIn("  - deny:path:secrets [path: secrets.txt]", rendered)

    def test_changes_panel_renders_recent_changes_and_shortcuts(self) -> None:
        state = TuiState(selected_change_stack="redo", selected_redo_index=0)

        rendered = state.render_changes_panel(
            undo_entries=["12345678  [edit_file] Updated demo.py (1 file)"],
            redo_entries=["87654321  [write_file] Created notes.txt (1 file)"],
            selected_undo_detail="12345678  tool=edit_file\nsummary: Updated demo.py",
            selected_redo_detail="87654321  tool=write_file\nsummary: Created notes.txt",
            pending_change_preview="Pending patch change set\naction: update",
            change_status="Approved apply_patch (once)",
        )

        self.assertIn("Changes", rendered)
        self.assertIn("Status", rendered)
        self.assertIn("Approved apply_patch (once)", rendered)
        self.assertIn("Pending change set", rendered)
        self.assertIn("Pending patch change set", rendered)
        self.assertIn("Undo stack (1):", rendered)
        self.assertIn("Redo stack [focused] (1):", rendered)
        self.assertIn("* 1. 12345678  [edit_file] Updated demo.py (1 file)", rendered)
        self.assertIn("> 1. 87654321  [write_file] Created notes.txt (1 file)", rendered)
        self.assertNotIn("Focused undo", rendered)
        self.assertIn("Selected change [redo 1/1]", rendered)
        self.assertIn("Ctrl+Z undo", rendered)
        self.assertIn("Ctrl+Y redo", rendered)
        self.assertIn("Shift+Left/Right focus stack", rendered)
        self.assertIn("Ctrl+Left/Right file", rendered)
        self.assertIn("Ctrl+Shift+Z undo selected", rendered)

    def test_changes_panel_surfaces_latest_applied_change_after_success(self) -> None:
        state = TuiState(selected_change_stack="undo", selected_change_index=0)

        rendered = state.render_changes_panel(
            undo_entries=["12345678  [apply_patch] Update app.py (2 files)"],
            redo_entries=[],
            selected_undo_detail="change: 12345678\nsummary: Update app.py\nFile 1\nupdated app.py (+3 -1)",
            selected_redo_detail="",
            pending_change_preview="",
            change_status="Applied changes are now in Undo stack.",
        )

        self.assertIn("Latest applied", rendered)
        self.assertIn("-> 12345678  [apply_patch] Update app.py (2 files)", rendered)
        self.assertIn("change: 12345678", rendered)

    def test_changes_panel_can_render_dismissed_preview_and_recovery(self) -> None:
        state = TuiState()

        rendered = state.render_changes_panel(
            undo_entries=[],
            redo_entries=[],
            selected_undo_detail="",
            selected_redo_detail="",
            pending_change_preview="",
            change_status="Denied apply_patch",
            changes_text="",
        )
        self.assertIn("No recorded workspace changes.", rendered)

        state.last_change_preview = "Pending patch change set\nfiles: 2"
        state.last_change_preview_label = "Dismissed change set"
        state.recovery_hint = "Next steps:\n1. Review the diff."
        state.failed_change_context = 'tool: apply_patch\ninput: {"patch":"..."}'
        state.retry_prompt = "Retry the patch"
        rendered = state.render_changes_panel(
            undo_entries=[],
            redo_entries=[],
            selected_undo_detail="",
            selected_redo_detail="",
            pending_change_preview="",
            change_status="Denied apply_patch",
            changes_text="",
        )
        self.assertIn("Dismissed change set", rendered)
        self.assertIn("Recovery", rendered)
        self.assertIn("Failure context", rendered)
        self.assertIn("Ctrl+Shift+R retry last failed prompt", rendered)

    def test_change_selection_moves_within_bounds(self) -> None:
        state = TuiState(selected_change_index=0, selected_redo_index=0)

        state.move_change_selection(1, total=2)
        state.move_change_selection(1, total=2)
        self.assertEqual(state.selected_change_index, 1)

        state.move_change_selection(-5, total=2)
        self.assertEqual(state.selected_change_index, 0)

        state.move_change_selection(3, redo=True, total=1)
        self.assertEqual(state.selected_redo_index, 0)

        state.switch_change_stack("redo")
        state.move_change_selection(1, total=2)
        self.assertEqual(state.selected_redo_index, 1)

    def test_change_file_selection_moves_within_bounds(self) -> None:
        state = TuiState(selected_change_file_index=0, selected_redo_file_index=0)

        state.move_change_file_selection(1, total=2)
        state.move_change_file_selection(1, total=2)
        self.assertEqual(state.selected_change_file_index, 1)

        state.move_change_file_selection(-5, total=2)
        self.assertEqual(state.selected_change_file_index, 0)

        state.move_change_file_selection(3, redo=True, total=1)
        self.assertEqual(state.selected_redo_file_index, 0)

        state.switch_change_stack("redo")
        state.move_change_file_selection(1, total=2)
        self.assertEqual(state.selected_redo_file_index, 1)


if __name__ == "__main__":
    unittest.main()
