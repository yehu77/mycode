from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.permissions import ApprovalRequest
from claudecode_py.runtime.events import RuntimeEvent
from claudecode_py.tui.state import PendingApproval, TuiState


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

        rendered = state.render_events()

        self.assertIn("[provider:retry] retrying in 0.5s", rendered)
        self.assertIn("[context] compacted 10 messages", rendered)

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
        )

        self.assertIn("constraints: read-only", rendered)
        self.assertIn("advisor_blocks: 2", rendered)
        self.assertIn("plan_executions: 4", rendered)
        self.assertIn("plan_drifts: 1", rendered)
        self.assertIn("last_plan_drift: pending_tools: write_file", rendered)
        self.assertIn("active_plan_kind: ultraplan", rendered)
        self.assertIn("active_plan_goal: map runtime", rendered)

    def test_task_panel_renders_existing_tasks(self) -> None:
        state = TuiState()

        rendered = state.render_task_panel(
            "abc123  status=running  kind=agent  description=Analyze repository"
        )

        self.assertIn("Tasks", rendered)
        self.assertIn("status=running", rendered)
        self.assertIn("Analyze repository", rendered)

    def test_plan_panel_renders_active_plan_detail(self) -> None:
        state = TuiState()

        rendered = state.render_plan_panel(
            "artifact_id: plan-1\nactive: yes\ngoal: map runtime\nadvisor_review:\n- status: block\nscout_outputs:\n- scout-1: status=completed"
        )

        self.assertIn("Active Plan", rendered)
        self.assertIn("artifact_id: plan-1", rendered)
        self.assertIn("advisor_review:", rendered)
        self.assertIn("scout_outputs:", rendered)

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
        )

        self.assertIn("busy: yes", rendered)
        self.assertIn("provider: openai-compatible", rendered)
        self.assertIn("model: gpt-4.1-mini", rendered)
        self.assertIn("session_id: demo-session", rendered)
        self.assertIn("mcp_failed: 1", rendered)
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
                risk_level="dangerous_shell",
                approval_key="dangerous_shell",
                details='command="Remove-Item demo.txt"',
            )
        )

        rendered = state.render_approval_panel()

        self.assertIn("Approval", rendered)
        self.assertIn("risk: dangerous_shell", rendered)
        self.assertIn("Preview mirrored in Changes panel.", rendered)
        self.assertNotIn('command="Remove-Item demo.txt"', rendered)
        self.assertIn("Ctrl+O allow once", rendered)
        self.assertNotIn("Recent changes", rendered)

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
        self.assertIn("Focused redo (1/1):", rendered)
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
