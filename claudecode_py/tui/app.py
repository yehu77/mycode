from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, Input, Static
from textual import work

from ..cli import _handle_repl_command
from ..cli import _focused_file_context_header_lines
from ..commands import CommandExecution
from ..interactions import UserQuestionRequest, UserQuestionResponse
from ..permissions import ApprovalRequest, ApprovalResult
from ..remote_session import RemoteSessionProxy
from ..runtime.events import RuntimeEvent
from ..session import Session
from ..session_factory import SessionFactory
from ..storage.background_sessions import BackgroundSessionRecord, resolve_background_session
from .state import (
    PendingApproval,
    PendingBackgroundFollowup,
    PendingChecklistEdit,
    PendingQuestion,
    TuiState,
)


class PyClaudeTui(App[None]):
    CSS = """
    Screen {
        layout: vertical;
    }

    #body {
        height: 1fr;
    }

    #chat-scroll, #tool-scroll, #tasks-scroll, #plan-scroll, #advisor-scroll, #status-scroll, #changes-scroll, #approval-scroll, #events-scroll {
        border: round $accent;
        padding: 1;
    }

    #chat-scroll {
        width: 2fr;
    }

    #side-pane {
        width: 1fr;
    }

    #tool-scroll {
        height: 2fr;
    }

    #tasks-scroll {
        height: 1fr;
    }

    #plan-scroll {
        height: 1fr;
    }

    #advisor-scroll {
        height: 1fr;
    }

    #status-scroll {
        height: 1fr;
    }

    #changes-scroll {
        height: 1fr;
    }

    #events-scroll {
        height: 1fr;
    }

    #approval-scroll {
        height: 1fr;
    }

    #prompt-input {
        dock: bottom;
    }
    """

    BINDINGS = [
        ("ctrl+l", "clear_chat", "Clear chat"),
        ("ctrl+r", "reload_context", "Reload Context"),
        ("ctrl+o", "approve_once", "Approve Once"),
        ("ctrl+s", "approve_session", "Approve Session"),
        ("ctrl+n", "deny_approval", "Deny"),
        ("ctrl+z", "undo_change", "Undo"),
        ("ctrl+y", "redo_change", "Redo"),
        ("shift+left", "focus_undo_stack", "Focus Undo"),
        ("shift+right", "focus_redo_stack", "Focus Redo"),
        ("shift+up", "select_prev_change", "Prev Change"),
        ("shift+down", "select_next_change", "Next Change"),
        ("ctrl+left", "select_prev_change_file", "Prev File"),
        ("ctrl+right", "select_next_change_file", "Next File"),
        ("ctrl+shift+r", "retry_failed_prompt", "Retry Failed"),
        ("ctrl+shift+z", "undo_selected_change", "Undo Selected"),
        ("ctrl+shift+y", "redo_selected_change", "Redo Selected"),
        ("ctrl+1", "plan_view_summary", "Plan Summary"),
        ("ctrl+2", "plan_view_scouts", "Plan Scouts"),
        ("ctrl+3", "plan_view_lineage", "Plan Lineage"),
        ("ctrl+4", "plan_view_advisor", "Plan Advisor"),
        ("ctrl+5", "plan_view_execution", "Plan Execution"),
        ("ctrl+shift+a", "plan_view_audit", "Plan Audit"),
        ("ctrl+9", "plan_view_timeline", "Plan Timeline"),
        ("ctrl+0", "cycle_plan_timeline_filter", "Timeline Filter"),
        ("ctrl+shift+0", "cycle_plan_timeline_delta_mode", "Timeline Delta"),
        ("ctrl+shift+9", "cycle_plan_timeline_focus_mode", "Timeline Focus"),
        ("ctrl+shift+t", "toggle_plan_timeline_focus_selected_task", "Focus Selected Task"),
        ("ctrl+shift+8", "cycle_plan_timeline_compare_mode", "Timeline Compare"),
        ("ctrl+shift+6", "select_prev_timeline_compare", "Prev Compare Item"),
        ("ctrl+shift+7", "select_next_timeline_compare", "Next Compare Item"),
        ("ctrl+shift+4", "select_prev_phase_local_task", "Prev Phase Task"),
        ("ctrl+shift+5", "select_next_phase_local_task", "Next Phase Task"),
        ("ctrl+6", "task_view_detail", "Task Detail"),
        ("ctrl+7", "task_view_advisor", "Task Advisor"),
        ("ctrl+8", "task_view_drift", "Task Drift"),
        ("ctrl+shift+up", "select_prev_plan_scout", "Prev Plan Scout"),
        ("ctrl+shift+down", "select_next_plan_scout", "Next Plan Scout"),
        ("ctrl+shift+1", "plan_scouts_compact", "Compact Scout Detail"),
        ("ctrl+shift+2", "plan_scouts_full", "Full Scout Detail"),
        ("f5", "plan_view_replay", "Replay"),
        ("f6", "open_selected_plan_task", "Open Plan Task"),
        ("f7", "select_prev_plan_lineage", "Prev Lineage"),
        ("f8", "select_next_plan_lineage", "Next Lineage"),
        ("f9", "execute_lineage_show", "Primary Nav"),
        ("f10", "execute_lineage_default_action", "Secondary Nav"),
        ("f11", "open_execution_plan_advisor", "Execution Advisor Detail"),
        ("f12", "show_execution_advisor_status", "Execution Advisor Status"),
        ("shift+f11", "execute_workspace_primary_action", "Workspace Primary"),
        ("shift+f12", "execute_workspace_secondary_action", "Workspace Secondary"),
        ("alt+f11", "execute_checklist_primary_action", "Checklist Primary"),
        ("alt+f12", "execute_checklist_secondary_action", "Checklist Secondary"),
        ("alt+shift+up", "select_prev_background_session", "Prev Background"),
        ("alt+shift+down", "select_next_background_session", "Next Background"),
        ("alt+shift+f8", "open_background_logs", "Background Logs"),
        ("alt+shift+f9", "execute_background_primary_action", "Background Primary"),
        ("alt+shift+f10", "execute_background_secondary_action", "Background Secondary"),
        ("alt+shift+f6", "send_background_followup", "Background Follow-up"),
        ("alt+shift+f7", "queue_background_followup", "Background Queue"),
        ("alt+shift+f12", "cancel_background_followup", "Cancel Bg Queue"),
        ("ctrl+alt+left", "select_prev_rewind_boundary", "Prev Rewind"),
        ("ctrl+alt+right", "select_next_rewind_boundary", "Next Rewind"),
        ("ctrl+alt+p", "preview_selected_rewind_boundary", "Preview Rewind"),
        ("ctrl+alt+r", "apply_selected_rewind_boundary", "Apply Rewind"),
        ("alt+f9", "execute_symbol_primary_action", "Symbol Primary"),
        ("alt+f10", "execute_symbol_secondary_action", "Symbol Secondary"),
        ("alt+1", "select_prev_symbol_primary_target", "Prev Symbol Primary"),
        ("alt+2", "select_next_symbol_primary_target", "Next Symbol Primary"),
        ("alt+3", "select_prev_symbol_reference", "Prev Symbol Reference"),
        ("alt+4", "select_next_symbol_reference", "Next Symbol Reference"),
        ("alt+5", "select_prev_symbol_focus_item", "Prev Symbol Focus"),
        ("alt+6", "select_next_symbol_focus_item", "Next Symbol Focus"),
        ("alt+7", "select_prev_symbol_focus_group", "Prev Symbol Group"),
        ("alt+8", "select_next_symbol_focus_group", "Next Symbol Group"),
        ("alt+0", "open_focused_symbol_candidate", "Open Symbol Focus"),
        ("alt+up", "select_prev_checklist_task", "Prev Checklist"),
        ("alt+down", "select_next_checklist_task", "Next Checklist"),
        ("alt+o", "open_selected_checklist_task", "Open Checklist"),
        ("alt+g", "cycle_checklist_filter", "Checklist Filter"),
        ("alt+j", "cycle_checklist_sort", "Checklist Sort"),
        ("alt+s", "edit_selected_checklist_subject", "Edit Checklist Subject"),
        ("alt+d", "edit_selected_checklist_description", "Edit Checklist Desc"),
        ("alt+e", "edit_selected_checklist_owner", "Edit Checklist Owner"),
        ("alt+r", "edit_selected_checklist_active_form", "Edit Checklist Active"),
        ("alt+b", "edit_selected_checklist_blocks", "Edit Checklist Blocks"),
        ("alt+l", "edit_selected_checklist_blocked_by", "Edit Checklist Blocked"),
        ("alt+m", "edit_selected_checklist_metadata", "Edit Checklist Meta"),
        ("alt+p", "history_prev", "Prev Input"),
        ("alt+n", "history_next", "Next Input"),
        ("ctrl+c", "quit", "Quit"),
    ]

    def __init__(
        self,
        session: Session,
        *,
        session_source: str = "new",
        restored_from: Path | None = None,
        live_background_id: str | None = None,
    ) -> None:
        super().__init__()
        self.session = session
        self.session_source = session_source
        self.restored_from = restored_from
        self.live_background_id = live_background_id
        self.state = TuiState()
        self._follow_active_lineage_after_turn = False
        self.chat_scroll = VerticalScroll(Static(), id="chat-scroll")
        self.tool_scroll = VerticalScroll(Static(), id="tool-scroll")
        self.tasks_scroll = VerticalScroll(Static(), id="tasks-scroll")
        self.task_detail_scroll = VerticalScroll(Static(), id="task-detail-scroll")
        self.plan_scroll = VerticalScroll(Static(), id="plan-scroll")
        self.advisor_scroll = VerticalScroll(Static(), id="advisor-scroll")
        self.status_scroll = VerticalScroll(Static(), id="status-scroll")
        self.changes_scroll = VerticalScroll(Static(), id="changes-scroll")
        self.approval_scroll = VerticalScroll(Static(), id="approval-scroll")
        self.events_scroll = VerticalScroll(Static(), id="events-scroll")
        self.input = Input(placeholder="Ask PyClaudeCode or type a slash command", id="prompt-input")
        self._approval_event: Event | None = None
        self._approval_result: ApprovalResult | None = None
        self._question_event: Event | None = None
        self._question_result: UserQuestionResponse | None = None
        if hasattr(self.session.permission_manager, "approval_handler"):
            self.session.permission_manager.approval_handler = self._request_approval
        if hasattr(self.session, "set_question_handler"):
            self.session.set_question_handler(self._request_questions)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="body"):
            yield self.chat_scroll
            with Vertical(id="side-pane"):
                yield self.tool_scroll
                yield self.tasks_scroll
                yield self.task_detail_scroll
                yield self.plan_scroll
                yield self.advisor_scroll
                yield self.status_scroll
                yield self.changes_scroll
                yield self.approval_scroll
                yield self.events_scroll
        yield self.input
        yield Footer()

    def on_mount(self) -> None:
        self.title = "PyClaudeCode TUI"
        self._bind_session_handlers()
        self._initialize_session_surface(reset_state=False)

    def on_unmount(self) -> None:
        self._unbind_session_handlers(self.session)
        if hasattr(self.session, "set_question_handler"):
            self.session.set_question_handler(None)

    def _bind_session_handlers(self) -> None:
        self.sub_title = str(self.session.config.cwd)
        if hasattr(self.session, "set_live_event_sink"):
            self.session.set_live_event_sink(self._handle_runtime_event)
        if hasattr(self.session, "set_approval_handlers"):
            self.session.set_approval_handlers(
                self._handle_remote_approval_requested,
                self._handle_remote_approval_resolved,
            )
        if hasattr(self.session, "set_question_handlers"):
            self.session.set_question_handlers(
                self._handle_remote_question_requested,
                self._handle_remote_question_resolved,
            )
        if hasattr(self.session.permission_manager, "approval_handler"):
            self.session.permission_manager.approval_handler = self._request_approval
        if hasattr(self.session, "set_question_handler"):
            self.session.set_question_handler(self._request_questions)

    def _unbind_session_handlers(self, session: object) -> None:
        if hasattr(session, "set_live_event_sink"):
            session.set_live_event_sink(None)
        if hasattr(session, "set_approval_handlers"):
            session.set_approval_handlers(None, None)
        if hasattr(session, "set_question_handlers"):
            session.set_question_handlers(None, None)

    def _initialize_session_surface(self, *, reset_state: bool) -> None:
        if reset_state:
            self.state = TuiState()
        if self.session.state.messages and self.restored_from is not None:
            self.state.append_event(
                f"Restored saved session {self.session.state.session_id} with {len(self.session.state.messages)} messages."
            )
        elif self.session.state.messages and self.live_background_id:
            self.state.append_event(
                f"Attached to live session {self.session.state.session_id} with {len(self.session.state.messages)} messages."
            )
        self.state.append_event(f"session_source: {self.session_source}")
        if self.restored_from is not None:
            self.state.append_event(f"restored_from: {self.restored_from}")
            self.state.append_event(
                "resume_semantics: saved session resume restores state only; live work requires attach."
            )
        elif self.live_background_id:
            self.state.append_event(f"background_session: {self.live_background_id}")
            self.state.append_event(
                "resume_semantics: attached to live background work; closing the TUI detaches."
            )
        else:
            self.state.append_event("resume_semantics: new live session.")
        if getattr(self.session.state, "active_planning_artifact_id", None):
            self.state.append_event(
                f"active_plan: {self.session.state.active_planning_artifact_id}"
            )
        for line in _focused_file_context_header_lines(self.session):
            self.state.append_event(line)
        if hasattr(self.session, "take_replay_events"):
            for event in self.session.take_replay_events():
                self.state.record_runtime_event(event)
        if getattr(self.session, "pending_approval", None) is not None:
            self._show_remote_pending_approval(self.session.pending_approval)
        if getattr(self.session, "pending_question", None) is not None:
            self._show_remote_pending_question(self.session.pending_question)
        self._append_chat("System", f"Connected to {self.session.config.cwd}")
        execution_summary = self._execution_summary_line()
        if execution_summary:
            self._append_event(execution_summary)
        self._append_event('Type "/help" for commands.')
        self._render()

    def _request_approval(self, request: ApprovalRequest) -> ApprovalResult:
        approval_event = Event()
        self.call_from_thread(self._show_approval_request, request, approval_event)
        approval_event.wait()
        result = self._approval_result or ApprovalResult(decision="deny", scope="once")
        self._approval_result = None
        return result

    def _show_approval_request(self, request: ApprovalRequest, approval_event: Event) -> None:
        self._approval_event = approval_event
        self._approval_result = None
        self.state.pending_question = None
        self.state.pending_approval = PendingApproval(request=request)
        self.state.last_change_preview = request.details or ""
        self.state.last_change_preview_label = "Pending change set"
        self._append_event(f"Approval required for {request.tool_name} ({request.risk_level})")
        self._render()

    def _handle_remote_approval_requested(self, request: ApprovalRequest) -> None:
        self.call_from_thread(self._show_remote_pending_approval, request)

    def _show_remote_pending_approval(self, request: ApprovalRequest) -> None:
        self._approval_event = None
        self._approval_result = None
        self.state.pending_question = None
        self.state.pending_approval = PendingApproval(request=request)
        self.state.last_change_preview = request.details or ""
        self.state.last_change_preview_label = "Pending change set"
        self._append_event(f"Approval required for {request.tool_name} ({request.risk_level})")
        self._render()

    def _handle_remote_approval_resolved(self, result: ApprovalResult) -> None:
        self.call_from_thread(self._apply_resolved_approval_result, result)

    def _apply_resolved_approval_result(self, result: ApprovalResult) -> None:
        if self.state.pending_approval is None:
            return
        request = self.state.pending_approval.request
        if result.decision == "allow":
            scope_text = "session" if result.scope == "session" else "once"
            message = f"Approved {request.tool_name} ({scope_text})"
            self.state.last_change_preview_label = "Approved change set"
        else:
            message = f"Denied {request.tool_name}"
            self.state.last_change_preview_label = "Dismissed change set"
            self.state.recovery_hint = ""
        self.state.change_status = message
        self.state.last_change_preview = request.details or ""
        self.state.pending_approval = None
        self._append_event(message)
        self._render()

    def _request_questions(self, request: UserQuestionRequest) -> UserQuestionResponse:
        question_event = Event()
        self.call_from_thread(self._show_question_request, request, question_event)
        question_event.wait()
        result = self._question_result or UserQuestionResponse(canceled=True)
        self._question_result = None
        return result

    def _show_question_request(self, request: UserQuestionRequest, question_event: Event) -> None:
        self._question_event = question_event
        self._question_result = None
        self.state.pending_approval = None
        self.state.pending_question = PendingQuestion(request=request)
        self._append_event("Structured questions require input.")
        self._render()

    def _handle_remote_question_requested(self, request: UserQuestionRequest) -> None:
        self.call_from_thread(self._show_remote_pending_question, request)

    def _show_remote_pending_question(self, request: UserQuestionRequest) -> None:
        self._question_event = None
        self._question_result = None
        self.state.pending_approval = None
        self.state.pending_question = PendingQuestion(request=request)
        self._append_event("Structured questions require input.")
        self._render()

    def _handle_remote_question_resolved(self, result: UserQuestionResponse) -> None:
        self.call_from_thread(self._apply_resolved_question_result, result)

    def _apply_resolved_question_result(self, result: UserQuestionResponse) -> None:
        if self.state.pending_question is None:
            return
        self.state.pending_question = None
        if result.canceled:
            self._append_event("Structured questions canceled.")
        else:
            self._append_event("Structured question answers submitted.")
        self._render()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        raw_prompt = event.value
        prompt = raw_prompt.strip()
        self.input.value = ""
        if self.state.busy:
            return
        if prompt == "/exit":
            self.exit()
            return
        if self.state.pending_checklist_edit is not None:
            self._submit_pending_checklist_edit(raw_prompt)
            return
        if not prompt:
            return
        if self.state.pending_background_followup is not None:
            self._submit_pending_background_followup(raw_prompt)
            return
        if self.state.pending_question is not None:
            self._resolve_question_from_prompt(prompt)
            return
        self.state.record_input_history(prompt)

        handled, output = _handle_repl_command(self.session, prompt)
        if handled:
            self._append_chat("You", prompt)
            if isinstance(output, CommandExecution):
                self.state.start_turn(prompt)
                self.state.busy = True
                self._append_event(output.progress_message)
                self._render()
                self._run_prompt(output.prompt, execution=output)
                return
            if output:
                self._append_chat("System", output)
            if prompt.startswith("/mcp-call") or prompt.startswith("/mcp-verify"):
                self.state.mcp_diagnostic_text = output or ""
            self._render()
            return

        self.state.start_turn(prompt)
        self.state.busy = True
        self._append_event("Running...")
        self._render()
        self._run_prompt(prompt)

    @work(thread=True)
    def _run_prompt(self, prompt: str, execution: CommandExecution | None = None) -> None:
        try:
            if execution is None:
                output = self.session.ask(prompt, sink=self._handle_runtime_event)
            else:
                output = self.session.run_command(execution, sink=self._handle_runtime_event)
            self.call_from_thread(self._finish_turn_output, output or "(no output)")
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self._fail_turn_output, f"{type(exc).__name__}: {exc}")
        finally:
            self.call_from_thread(self._finish_prompt)

    def _handle_runtime_event(self, event: RuntimeEvent) -> None:
        self.call_from_thread(self._record_runtime_event, event)

    def _record_runtime_event(self, event: RuntimeEvent) -> None:
        self.state.record_runtime_event(event)
        if event.kind == "task_progress" and event.task_id and event.task_id == self.state.selected_task_id:
            self._refresh_selected_task_detail()
        self._render()

    def action_clear_chat(self) -> None:
        self.session.clear_history()
        self.state.clear_chat()
        self.state.append_event("Cleared in-memory conversation history.")
        self._render()

    def action_reload_context(self) -> None:
        message = self.session.reload_project_context()
        self._append_event(message)
        self._render()

    def action_history_prev(self) -> None:
        self.input.value = self.state.history_previous(self.input.value)
        self.input.cursor_position = len(self.input.value)

    def action_history_next(self) -> None:
        self.input.value = self.state.history_next()
        self.input.cursor_position = len(self.input.value)

    def action_approve_once(self) -> None:
        self._resolve_approval(ApprovalResult(decision="allow", scope="once"))

    def action_approve_session(self) -> None:
        self._resolve_approval(ApprovalResult(decision="allow", scope="session"))

    def action_deny_approval(self) -> None:
        if self.state.pending_question is not None:
            self._cancel_pending_question()
            return
        self._resolve_approval(ApprovalResult(decision="deny", scope="once"))

    def action_undo_change(self) -> None:
        message = self.session.undo_last_change()
        self.state.change_status = message
        self._append_event(message)
        self._render()

    def action_redo_change(self) -> None:
        message = self.session.redo_last_undo()
        self.state.change_status = message
        self._append_event(message)
        self._render()

    def action_focus_undo_stack(self) -> None:
        self.state.switch_change_stack("undo")
        self._render()

    def action_focus_redo_stack(self) -> None:
        self.state.switch_change_stack("redo")
        self._render()

    def action_select_prev_change(self) -> None:
        if self.state.selected_change_stack == "redo":
            total = len(self.session.recent_redo_entries(limit=5))
            self.state.move_change_selection(-1, redo=True, total=total)
        else:
            total = len(self.session.recent_change_entries(limit=5))
            self.state.move_change_selection(-1, redo=False, total=total)
        self._render()

    def action_select_next_change(self) -> None:
        if self.state.selected_change_stack == "redo":
            total = len(self.session.recent_redo_entries(limit=5))
            self.state.move_change_selection(1, redo=True, total=total)
        else:
            total = len(self.session.recent_change_entries(limit=5))
            self.state.move_change_selection(1, redo=False, total=total)
        self._render()

    def action_select_prev_change_file(self) -> None:
        if self._move_file_context_focus(-1):
            self._render()
            return
        if self.state.selected_change_stack == "redo":
            total = self.session.selected_change_file_count(
                index=self.state.selected_redo_index,
                limit=5,
                redo=True,
            )
            self.state.move_change_file_selection(-1, redo=True, total=total)
        else:
            total = self.session.selected_change_file_count(
                index=self.state.selected_change_index,
                limit=5,
                redo=False,
            )
            self.state.move_change_file_selection(-1, redo=False, total=total)
        self._append_change_file_focus_event(total=total)
        self._render()

    def action_select_next_change_file(self) -> None:
        if self._move_file_context_focus(1):
            self._render()
            return
        if self.state.selected_change_stack == "redo":
            total = self.session.selected_change_file_count(
                index=self.state.selected_redo_index,
                limit=5,
                redo=True,
            )
            self.state.move_change_file_selection(1, redo=True, total=total)
        else:
            total = self.session.selected_change_file_count(
                index=self.state.selected_change_index,
                limit=5,
                redo=False,
            )
            self.state.move_change_file_selection(1, redo=False, total=total)
        self._append_change_file_focus_event(total=total)
        self._render()

    def action_undo_selected_change(self) -> None:
        entries = self.session.recent_change_entries(limit=5)
        if not entries:
            self._append_event("No undoable change is selected.")
            self._render()
            return
        selected = entries[min(self.state.selected_change_index, len(entries) - 1)]
        change_id = selected.split()[0]
        message = self.session.undo_last_change(change_id)
        self.state.change_status = message
        self._append_event(message)
        self.state.move_change_selection(0, total=len(self.session.recent_change_entries(limit=5)))
        self._render()

    def action_redo_selected_change(self) -> None:
        entries = self.session.recent_redo_entries(limit=5)
        if not entries:
            self._append_event("No redoable change is selected.")
            self._render()
            return
        selected = entries[min(self.state.selected_redo_index, len(entries) - 1)]
        change_id = selected.split()[0]
        message = self.session.redo_last_undo(change_id)
        self.state.change_status = message
        self._append_event(message)
        self.state.move_change_selection(
            0,
            redo=True,
            total=len(self.session.recent_redo_entries(limit=5)),
        )
        self._render()

    def action_retry_failed_prompt(self) -> None:
        prompt = self.state.retry_prompt.strip()
        if not prompt:
            self._append_event("No failed prompt available to retry.")
            self._render()
            return
        if self.state.busy:
            return
        self.state.start_turn(prompt)
        self.state.busy = True
        self._append_event("Retrying last failed prompt...")
        self._render()
        self._run_prompt(prompt)

    def action_plan_view_summary(self) -> None:
        self.state.set_plan_panel_view("summary")
        self._render()

    def action_plan_view_scouts(self) -> None:
        self.state.set_plan_panel_view("scouts")
        self._render()

    def action_plan_view_lineage(self) -> None:
        self.state.set_plan_panel_view("lineage")
        self._render()

    def action_plan_view_advisor(self) -> None:
        self.state.set_plan_panel_view("advisor")
        self._render()

    def action_plan_view_execution(self) -> None:
        self.state.set_plan_panel_view("execution")
        self._render()

    def action_plan_view_audit(self) -> None:
        self.state.set_plan_panel_view("audit")
        self._render()

    def action_plan_view_timeline(self) -> None:
        self.state.set_plan_panel_view("timeline")
        self._render()

    def action_plan_view_replay(self) -> None:
        if self.state.plan_panel_view == "timeline":
            self.state.plan_replay_source_mode = self._timeline_replay_source_mode()
            self.state.selected_plan_replay_index = self.state.selected_plan_timeline_index
            replay_context = self._timeline_replay_slice_context()
            self.state.set_plan_replay_slice_context(
                phase_filter=replay_context["phase_filter"],
                artifact_id=replay_context["artifact_id"] or None,
            )
        else:
            self.state.plan_replay_source_mode = "timeline-entry"
            self.state.selected_plan_replay_index = 0
            self.state.set_plan_replay_slice_context()
        self.state.set_plan_panel_view("replay")
        self._render()

    def action_cycle_plan_timeline_filter(self) -> None:
        if self.state.plan_panel_view != "timeline":
            return
        self.state.cycle_plan_timeline_filter()
        self._render()

    def action_cycle_plan_timeline_delta_mode(self) -> None:
        if self.state.plan_panel_view != "timeline":
            return
        self.state.cycle_plan_timeline_delta_mode()
        self._render()

    def action_cycle_plan_timeline_focus_mode(self) -> None:
        if self.state.plan_panel_view != "timeline":
            return
        self.state.cycle_plan_timeline_focus_mode()
        self._render()

    def action_toggle_plan_timeline_focus_selected_task(self) -> None:
        task_id = self._current_audit_focus_task_id()
        if not task_id:
            if (
                self.state.plan_panel_view == "timeline"
                and self.state.plan_timeline_focus_mode.startswith("task:")
            ):
                self.state.set_plan_timeline_focus_task(None)
                self._render()
            return
        self.state.set_plan_panel_view("timeline")
        current_focus = self.state.plan_timeline_focus_mode
        if current_focus == f"task:{task_id}":
            self.state.set_plan_timeline_focus_task(None)
        else:
            self.state.set_plan_timeline_focus_task(task_id)
        self._render()

    def action_cycle_plan_timeline_compare_mode(self) -> None:
        if self.state.plan_panel_view != "timeline":
            return
        self.state.cycle_plan_timeline_compare_mode()
        self._render()

    def action_select_prev_timeline_compare(self) -> None:
        if self.state.plan_panel_view != "timeline":
            return
        self.state.move_plan_timeline_compare_selection(-1)
        self._render()

    def action_select_next_timeline_compare(self) -> None:
        if self.state.plan_panel_view != "timeline":
            return
        self.state.move_plan_timeline_compare_selection(1)
        self._render()

    def action_select_prev_phase_local_task(self) -> None:
        if self.state.plan_panel_view != "timeline":
            return
        self.state.move_phase_local_task_selection(-1)
        self._render()

    def action_select_next_phase_local_task(self) -> None:
        if self.state.plan_panel_view != "timeline":
            return
        self.state.move_phase_local_task_selection(1)
        self._render()

    def action_select_prev_plan_scout(self) -> None:
        if self.state.plan_panel_view == "execution":
            self.state.move_plan_execution_selection(-1)
            self._render()
            return
        self.state.move_plan_scout_selection(-1)
        self._render()

    def action_select_next_plan_scout(self) -> None:
        if self.state.plan_panel_view == "execution":
            self.state.move_plan_execution_selection(1)
            self._render()
            return
        self.state.move_plan_scout_selection(1)
        self._render()

    def action_plan_scouts_compact(self) -> None:
        self.state.set_plan_scout_detail_mode("compact")
        self._render()

    def action_plan_scouts_full(self) -> None:
        self.state.set_plan_scout_detail_mode("full")
        self._render()

    def action_open_selected_plan_task(self) -> None:
        context = self._selected_plan_task_context()
        if context is None or self.state.busy:
            return
        if hasattr(self.session, "describe_task_detail"):
            output = self.session.describe_task_detail(context["task_id"])
            self.state.set_task_detail(
                task_id=context["task_id"],
                text=output,
                execution_metadata=self._task_execution_detail_metadata(context["task_id"]),
                workspace_metadata=self._task_workspace_detail_metadata(context["task_id"]),
                checklist_metadata=self._task_checklist_detail_metadata(context["task_id"]),
                file_context_metadata=self._resolved_task_file_context(
                    context["task_id"],
                    fallback_index=self.state.plan_file_context_index,
                )[0],
            )
            self._set_task_detail_file_context_state(
                context["task_id"],
                fallback_index=self.state.plan_file_context_index,
            )
            self._render()
            return
        if hasattr(self.session, "open_task_detail"):
            output = self.session.open_task_detail(context["task_id"])
            self.state.set_task_detail(
                task_id=context["task_id"],
                text=output,
                execution_metadata=self._task_execution_detail_metadata(context["task_id"]),
                workspace_metadata=self._task_workspace_detail_metadata(context["task_id"]),
                checklist_metadata=self._task_checklist_detail_metadata(context["task_id"]),
                file_context_metadata=self._resolved_task_file_context(
                    context["task_id"],
                    fallback_index=self.state.plan_file_context_index,
                )[0],
            )
            self._set_task_detail_file_context_state(
                context["task_id"],
                fallback_index=self.state.plan_file_context_index,
            )
            self._render()
            return
        self._execute_navigation_command(context["command"])

    def action_select_prev_plan_lineage(self) -> None:
        if self.state.plan_panel_view == "replay":
            self.state.move_plan_replay_selection(-1)
            self._render()
            return
        if self.state.plan_panel_view == "timeline":
            self.state.move_plan_timeline_selection(-1)
            self._render()
            return
        self.state.move_plan_lineage_selection(-1)
        self._render()

    def action_select_next_plan_lineage(self) -> None:
        if self.state.plan_panel_view == "replay":
            self.state.move_plan_replay_selection(1)
            self._render()
            return
        if self.state.plan_panel_view == "timeline":
            self.state.move_plan_timeline_selection(1)
            self._render()
            return
        self.state.move_plan_lineage_selection(1)
        self._render()

    def action_execute_lineage_show(self) -> None:
        if self._execute_task_detail_navigation(primary=True):
            return
        context = self._selected_replay_context()
        if context is not None and not self.state.busy:
            self._execute_lineage_command(context["primary_action"])
            return
        context = self._selected_timeline_context()
        if context is not None and not self.state.busy:
            self._execute_lineage_command(context["primary_action"])
            return
        context = self._selected_audit_context()
        if context is not None and not self.state.busy:
            self._execute_lineage_command(context["primary_action"])
            return
        context = self._selected_lineage_context()
        if context is None or self.state.busy:
            self._execute_file_context_navigation(primary=True)
            return
        self._execute_lineage_command(f"/plan show {context['artifact_id']}")

    def action_execute_lineage_default_action(self) -> None:
        if self._execute_task_detail_navigation(primary=False):
            return
        context = self._selected_replay_context()
        if context is not None and not self.state.busy:
            self._execute_lineage_command(context["secondary_action"])
            return
        context = self._selected_timeline_context()
        if context is not None and not self.state.busy:
            self._execute_lineage_command(context["secondary_action"])
            return
        context = self._selected_audit_context()
        if context is not None and not self.state.busy:
            self._execute_lineage_command(context["secondary_action"])
            return
        context = self._selected_lineage_context()
        if context is None or self.state.busy:
            self._execute_file_context_navigation(primary=False)
            return
        self._execute_lineage_command(context["default_action"], follow_active_lineage=True)

    def action_open_execution_plan_advisor(self) -> None:
        if self.state.plan_panel_view == "timeline" and not self.state.busy:
            context = self._selected_timeline_compare_context()
            if context is not None and context["primary_action"]:
                self._execute_lineage_command(context["primary_action"])
                return
        task_id = self._selected_execution_task_id()
        if task_id and hasattr(self.session, "open_task_detail_advisor"):
            output = self.session.open_task_detail_advisor(task_id)
            self.state.set_task_advisor_detail(task_id=task_id, text=output)
            self._set_task_detail_file_context_state(
                task_id,
                fallback_index=self.state.plan_file_context_index,
            )
            self.state.set_plan_panel_view("advisor")
            self._render()
            return
        if self.state.plan_panel_view != "execution":
            return
        if hasattr(self.session, "open_active_plan_advisor"):
            output = self.session.open_active_plan_advisor()
            self._append_chat("System", output)
        self.state.set_plan_panel_view("advisor")
        self._render()

    def action_show_execution_advisor_status(self) -> None:
        if self.state.plan_panel_view == "timeline" and not self.state.busy:
            context = self._selected_timeline_compare_context()
            if context is not None and context["secondary_action"]:
                self._execute_lineage_command(context["secondary_action"])
                return
            phase_local = self._selected_phase_local_summary_context()
            if phase_local is not None and phase_local["drift_action"]:
                drift_task_id = phase_local["drift_task_id"]
                if drift_task_id and hasattr(self.session, "open_task_drift_detail"):
                    output = self.session.open_task_drift_detail(drift_task_id)
                    self.state.set_task_drift_detail(task_id=drift_task_id, text=output)
                    self._set_task_detail_file_context_state(
                        drift_task_id,
                        fallback_index=self.state.plan_file_context_index,
                    )
                    self._render()
                    return
                if hasattr(self.session, "open_phase_local_recent_drift_task"):
                    output = self.session.open_phase_local_recent_drift_task()
                    self._append_chat("System", output)
                    self._render()
                    return
        if self.state.busy:
            return
        task_id = self._selected_execution_task_id()
        if task_id and hasattr(self.session, "open_task_drift_detail"):
            output = self.session.open_task_drift_detail(task_id)
            self.state.set_task_drift_detail(task_id=task_id, text=output)
            self._set_task_detail_file_context_state(
                task_id,
                fallback_index=self.state.plan_file_context_index,
            )
            self._render()
            return
        if self.state.plan_panel_view != "execution":
            return
        if hasattr(self.session, "show_advisor_status"):
            output = self.session.show_advisor_status()
            self._append_chat("System", output)
            return
        self._execute_navigation_command("/advisor status")

    def action_task_view_detail(self) -> None:
        if self.state.selected_task_id:
            self.state.set_task_detail_view("detail")
            self._render()

    def action_task_view_advisor(self) -> None:
        task_id = self._selected_execution_task_id() or self.state.selected_task_id
        if not task_id:
            return
        if hasattr(self.session, "open_task_detail_advisor"):
            output = self.session.open_task_detail_advisor(task_id)
            self.state.set_task_advisor_detail(task_id=task_id, text=output)
            self._set_task_detail_file_context_state(
                task_id,
                fallback_index=self.state.task_detail_file_context_index,
            )
            self._render()

    def action_task_view_drift(self) -> None:
        task_id = self._selected_execution_task_id() or self.state.selected_task_id
        if not task_id:
            return
        if hasattr(self.session, "open_task_drift_detail"):
            output = self.session.open_task_drift_detail(task_id)
            self.state.set_task_drift_detail(task_id=task_id, text=output)
            self._set_task_detail_file_context_state(
                task_id,
                fallback_index=self.state.task_detail_file_context_index,
            )
            self._render()

    def action_execute_workspace_primary_action(self) -> None:
        self._execute_workspace_action(primary=True)

    def action_execute_workspace_secondary_action(self) -> None:
        self._execute_workspace_action(primary=False)

    def action_execute_checklist_primary_action(self) -> None:
        self._execute_checklist_action(primary=True)

    def action_execute_checklist_secondary_action(self) -> None:
        self._execute_checklist_action(primary=False)

    def action_select_prev_background_session(self) -> None:
        self._move_background_registry_selection(-1)

    def action_select_next_background_session(self) -> None:
        self._move_background_registry_selection(1)

    def action_execute_background_primary_action(self) -> None:
        self._execute_background_registry_action(primary=True)

    def action_execute_background_secondary_action(self) -> None:
        self._execute_background_registry_action(primary=False)

    def action_open_background_logs(self) -> None:
        self._execute_background_registry_action(logs_only=True)

    def action_send_background_followup(self) -> None:
        self._request_background_followup(mode="send")

    def action_queue_background_followup(self) -> None:
        self._request_background_followup(mode="queue")

    def action_cancel_background_followup(self) -> None:
        if self.state.pending_background_followup is not None:
            self.state.pending_background_followup = None
            self._append_event("Canceled background follow-up input.")
            self._render()
            return
        selected = self._resolved_background_registry_context()
        if not isinstance(selected, dict):
            self._append_event("No background session is selected.")
            self._render()
            return
        bg_id = str(selected.get("background_session_id") or "").strip()
        if not bg_id:
            self._append_event("Selected background session has no id.")
            self._render()
            return
        output = self.session.cancel_pending_background_followup(bg_id)
        if output:
            self.state.append_message("System", output)
        self._render()

    def action_select_prev_rewind_boundary(self) -> None:
        self._move_rewind_boundary_selection(-1)

    def action_select_next_rewind_boundary(self) -> None:
        self._move_rewind_boundary_selection(1)

    def action_preview_selected_rewind_boundary(self) -> None:
        payload = self._selected_rewind_boundary_payload()
        if not isinstance(payload, dict):
            self._append_event("No rewindable boundaries are available in the current session.")
            return
        command = str(payload.get("show_action") or "").strip()
        if not command:
            self._append_event("No rewind preview action is available for the selected boundary.")
            return
        self._execute_navigation_command(command)

    def action_apply_selected_rewind_boundary(self) -> None:
        payload = self._selected_rewind_boundary_payload()
        if not isinstance(payload, dict):
            self._append_event("No rewindable boundaries are available in the current session.")
            return
        command = str(payload.get("apply_action") or "").strip()
        if not command:
            self._append_event("No rewind apply action is available for the selected boundary.")
            return
        self._execute_navigation_command(command)

    def action_execute_symbol_primary_action(self) -> None:
        self._execute_symbol_action(primary=True)

    def action_execute_symbol_secondary_action(self) -> None:
        self._execute_symbol_action(primary=False)

    def action_select_prev_symbol_primary_target(self) -> None:
        self._cycle_symbol_selection(primary_group=True, delta=-1)

    def action_select_next_symbol_primary_target(self) -> None:
        self._cycle_symbol_selection(primary_group=True, delta=1)

    def action_select_prev_symbol_reference(self) -> None:
        self._cycle_symbol_selection(primary_group=False, delta=-1)

    def action_select_next_symbol_reference(self) -> None:
        self._cycle_symbol_selection(primary_group=False, delta=1)

    def action_select_prev_symbol_focus_item(self) -> None:
        self._move_symbol_focus_item(-1)

    def action_select_next_symbol_focus_item(self) -> None:
        self._move_symbol_focus_item(1)

    def action_select_prev_symbol_focus_group(self) -> None:
        self._move_symbol_focus_group(-1)

    def action_select_next_symbol_focus_group(self) -> None:
        self._move_symbol_focus_group(1)

    def action_open_focused_symbol_candidate(self) -> None:
        self._open_focused_symbol_candidate()

    def action_select_prev_checklist_task(self) -> None:
        self._move_selected_checklist_task(-1)

    def action_select_next_checklist_task(self) -> None:
        self._move_selected_checklist_task(1)

    def action_open_selected_checklist_task(self) -> None:
        task_id = self._selected_checklist_task_id()
        if not task_id:
            self._append_event("No checklist task is available in the current context.")
            return
        try:
            output = self.session.describe_task_detail(task_id)
        except Exception as exc:  # noqa: BLE001
            self._append_event(f"Failed to open checklist task detail: {exc}")
            return
        self.state.set_task_detail(
            task_id=task_id,
            text=output,
            execution_metadata=self._task_execution_detail_metadata(task_id),
            workspace_metadata=self._task_workspace_detail_metadata(task_id),
            checklist_metadata=self._task_checklist_detail_metadata(task_id),
            file_context_metadata=self._resolved_task_file_context(task_id)[0],
        )
        self._set_task_detail_file_context_state(task_id)
        self._render()

    def action_cycle_checklist_filter(self) -> None:
        self.state.cycle_checklist_filter()
        visible = self._visible_checklist_tasks_payload()
        if visible:
            selected = (self.state.selected_checklist_task_id or "").strip()
            visible_ids = {str(item.get("id") or "").strip() for item in visible}
            if selected not in visible_ids:
                self.state.selected_checklist_task_id = str(visible[0].get("id") or "").strip() or None
        else:
            self.state.selected_checklist_task_id = None
        self._append_event(f"Checklist filter: {self.state.checklist_filter}")
        self._render()

    def action_cycle_checklist_sort(self) -> None:
        self.state.cycle_checklist_sort()
        visible = self._visible_checklist_tasks_payload()
        if visible:
            selected = (self.state.selected_checklist_task_id or "").strip()
            visible_ids = {str(item.get("id") or "").strip() for item in visible}
            if selected not in visible_ids:
                self.state.selected_checklist_task_id = str(visible[0].get("id") or "").strip() or None
        else:
            self.state.selected_checklist_task_id = None
        self._append_event(f"Checklist sort: {self.state.checklist_sort}")
        self._render()

    def action_edit_selected_checklist_owner(self) -> None:
        self._begin_checklist_edit(field="owner")

    def action_edit_selected_checklist_subject(self) -> None:
        self._begin_checklist_edit(field="subject")

    def action_edit_selected_checklist_description(self) -> None:
        self._begin_checklist_edit(field="description")

    def action_edit_selected_checklist_active_form(self) -> None:
        self._begin_checklist_edit(field="active_form")

    def action_edit_selected_checklist_blocks(self) -> None:
        self._begin_checklist_edit(field="blocks")

    def action_edit_selected_checklist_blocked_by(self) -> None:
        self._begin_checklist_edit(field="blocked_by")

    def action_edit_selected_checklist_metadata(self) -> None:
        self._begin_checklist_edit(field="metadata")

    def _plan_panel_text(self) -> str:
        if self.state.plan_panel_view == "scouts":
            if hasattr(self.session, "describe_active_plan_scouts_at"):
                return self.session.describe_active_plan_scouts_at(
                    self.state.selected_plan_scout_index,
                    full_detail=self.state.plan_scout_detail_mode == "full",
                    file_index=self.state.plan_file_context_index,
                    preserve_current_focus=False,
                )
            if hasattr(self.session, "describe_active_plan_scouts"):
                return self.session.describe_active_plan_scouts(
                    selected_index=self.state.selected_plan_scout_index,
                    full_detail=self.state.plan_scout_detail_mode == "full",
                )
            return self.session.describe_active_plan()
        if self.state.plan_panel_view == "lineage":
            if hasattr(self.session, "describe_active_plan_lineage"):
                return self.session.describe_active_plan_lineage(
                    selected_index=self.state.selected_plan_lineage_index
                )
            return self.session.describe_active_plan()
        if self.state.plan_panel_view == "audit":
            if hasattr(self.session, "describe_active_plan_audit"):
                return self.session.describe_active_plan_audit(
                    selected_index=self.state.selected_plan_lineage_index
                )
            return self.session.describe_active_plan()
        if self.state.plan_panel_view == "execution":
            if hasattr(self.session, "describe_active_plan_execution_at"):
                return self.session.describe_active_plan_execution_at(
                    self.state.selected_plan_execution_index,
                    full_detail=self.state.plan_scout_detail_mode == "full",
                    file_index=self.state.plan_file_context_index,
                    preserve_current_focus=False,
                )
            if hasattr(self.session, "describe_active_plan_execution"):
                return self.session.describe_active_plan_execution(
                    selected_index=self.state.selected_plan_execution_index,
                    full_detail=self.state.plan_scout_detail_mode == "full",
                )
            return self.session.describe_active_plan()
        if self.state.plan_panel_view == "advisor":
            if hasattr(self.session, "describe_active_plan_advisor"):
                return self.session.describe_active_plan_advisor(
                    file_index=self.state.plan_file_context_index,
                    preserve_current_focus=False,
                )
            return self.session.describe_active_plan(
                file_index=self.state.plan_file_context_index,
                preserve_current_focus=False,
            )
        if self.state.plan_panel_view == "timeline":
            if hasattr(self.session, "describe_active_plan_timeline_at"):
                return self.session.describe_active_plan_timeline_at(
                    self.state.selected_plan_timeline_index,
                    selected_compare_index=self.state.selected_plan_timeline_compare_index,
                    selected_phase_local_task_index=self.state.selected_phase_local_task_index,
                    kind_filter=self.state.plan_timeline_filter,
                    delta_mode=self.state.plan_timeline_delta_mode,
                    focus_mode=self.state.plan_timeline_focus_mode,
                    compare_mode=self.state.plan_timeline_compare_mode,
                )
            if hasattr(self.session, "describe_active_plan_timeline"):
                return self.session.describe_active_plan_timeline(
                    selected_index=self.state.selected_plan_timeline_index,
                    selected_compare_index=self.state.selected_plan_timeline_compare_index,
                    selected_phase_local_task_index=self.state.selected_phase_local_task_index,
                    kind_filter=self.state.plan_timeline_filter,
                    delta_mode=self.state.plan_timeline_delta_mode,
                    focus_mode=self.state.plan_timeline_focus_mode,
                    compare_mode=self.state.plan_timeline_compare_mode,
                )
            return self.session.describe_active_plan()
        if self.state.plan_panel_view == "replay":
            if hasattr(self.session, "describe_active_plan_replay"):
                return self.session.describe_active_plan_replay(
                    selected_index=self.state.selected_plan_replay_index,
                    selected_compare_index=self.state.selected_plan_timeline_compare_index,
                    selected_phase_local_task_index=self.state.selected_phase_local_task_index,
                    kind_filter=self.state.plan_timeline_filter,
                    delta_mode=self.state.plan_timeline_delta_mode,
                    phase_filter=self.state.plan_replay_phase_filter,
                    focus_mode=self.state.plan_timeline_focus_mode,
                    compare_mode=self.state.plan_timeline_compare_mode,
                    source_mode=self.state.plan_replay_source_mode,
                    artifact_id=self.state.plan_replay_artifact_id,
                )
            return self.session.describe_active_plan()
        return self.session.describe_active_plan(
            file_index=self.state.plan_file_context_index,
            preserve_current_focus=False,
        )

    def _selected_lineage_context(self) -> dict[str, str] | None:
        if self.state.plan_panel_view != "lineage":
            return None
        text = self._plan_panel_text()
        artifact_id = None
        default_action = None
        for line in text.splitlines():
            if line.startswith("selected_lineage_artifact_id:"):
                artifact_id = line.split(":", 1)[1].strip()
            elif line.startswith("selected_lineage_default_action:"):
                default_action = line.split(":", 1)[1].strip()
        if not artifact_id or not default_action:
            return None
        return {"artifact_id": artifact_id, "default_action": default_action}

    def _selected_audit_context(self) -> dict[str, str] | None:
        if self.state.plan_panel_view != "audit":
            return None
        text = self._plan_panel_text()
        artifact_id = None
        primary_action = None
        secondary_action = None
        for line in text.splitlines():
            if line.startswith("selected_audit_artifact_id:"):
                artifact_id = line.split(":", 1)[1].strip()
            elif line.startswith("selected_audit_primary_action:"):
                primary_action = line.split(":", 1)[1].strip()
            elif line.startswith("selected_audit_secondary_action:"):
                secondary_action = line.split(":", 1)[1].strip()
        if not artifact_id or not primary_action or not secondary_action:
            return None
        return {
            "artifact_id": artifact_id,
            "primary_action": primary_action,
            "secondary_action": secondary_action,
        }

    def _selected_timeline_context(self) -> dict[str, str] | None:
        if self.state.plan_panel_view != "timeline":
            return None
        text = self._plan_panel_text()
        primary_action = None
        secondary_action = None
        for line in text.splitlines():
            if line.startswith("selected_timeline_primary_action:"):
                primary_action = line.split(":", 1)[1].strip()
            elif line.startswith("selected_timeline_secondary_action:"):
                secondary_action = line.split(":", 1)[1].strip()
        if not primary_action or not secondary_action:
            return None
        return {"primary_action": primary_action, "secondary_action": secondary_action}

    def _selected_replay_context(self) -> dict[str, str] | None:
        if self.state.plan_panel_view != "replay":
            return None
        text = self._plan_panel_text()
        primary_action = ""
        secondary_action = ""
        for line in text.splitlines():
            if line.startswith("selected_replay_primary_action:"):
                primary_action = line.split(":", 1)[1].strip()
            elif line.startswith("selected_replay_secondary_action:"):
                secondary_action = line.split(":", 1)[1].strip()
        if not primary_action and not secondary_action:
            return None
        return {
            "primary_action": primary_action,
            "secondary_action": secondary_action,
        }

    def _selected_timeline_compare_context(self) -> dict[str, str] | None:
        if self.state.plan_panel_view != "timeline":
            return None
        text = self._plan_panel_text()
        label = None
        primary_action = None
        secondary_action = None
        for line in text.splitlines():
            if line.startswith("selected_timeline_compare_label:"):
                label = line.split(":", 1)[1].strip()
            elif line.startswith("selected_timeline_compare_primary_action:"):
                primary_action = line.split(":", 1)[1].strip()
            elif line.startswith("selected_timeline_compare_secondary_action:"):
                secondary_action = line.split(":", 1)[1].strip()
        if not primary_action and not secondary_action:
            return None
        return {
            "label": label or "",
            "primary_action": primary_action or "",
            "secondary_action": secondary_action or "",
        }

    def _selected_plan_task_context(self) -> dict[str, str] | None:
        if self.state.plan_panel_view == "timeline":
            phase_local = self._selected_phase_local_summary_context()
            if phase_local is None or not phase_local["task_id"] or not phase_local["task_action"]:
                return None
            return {"task_id": phase_local["task_id"], "command": phase_local["task_action"]}
        if self.state.plan_panel_view not in {"scouts", "execution"}:
            return None
        text = self._plan_panel_text()
        task_id = None
        command = None
        for line in text.splitlines():
            if self.state.plan_panel_view == "scouts" and line.startswith("selected_scout_task_id:"):
                task_id = line.split(":", 1)[1].strip()
            elif self.state.plan_panel_view == "scouts" and line.startswith("selected_scout_task_action:"):
                command = line.split(":", 1)[1].strip()
            elif self.state.plan_panel_view == "execution" and line.startswith("selected_execution_task_id:"):
                task_id = line.split(":", 1)[1].strip()
            elif self.state.plan_panel_view == "execution" and line.startswith("selected_execution_task_action:"):
                command = line.split(":", 1)[1].strip()
        if not task_id or not command:
            return None
        return {"task_id": task_id, "command": command}

    def _selected_timeline_task_id(self) -> str | None:
        if self.state.plan_panel_view != "timeline":
            return None
        text = self._plan_panel_text()
        for line in text.splitlines():
            if line.startswith("selected_timeline_task_id:"):
                task_id = line.split(":", 1)[1].strip()
                return task_id or None
        return None

    def _current_audit_focus_task_id(self) -> str | None:
        context = self._selected_plan_task_context()
        if context is not None and context["task_id"]:
            return context["task_id"]
        timeline_task_id = self._selected_timeline_task_id()
        if timeline_task_id:
            return timeline_task_id
        if self.state.selected_task_id:
            return self.state.selected_task_id
        return None

    def _selected_phase_local_summary_context(self) -> dict[str, str] | None:
        if self.state.plan_panel_view != "timeline":
            return None
        text = self._plan_panel_text()
        task_id = ""
        task_action = ""
        drift_task_id = ""
        drift_action = ""
        for line in text.splitlines():
            if line.startswith("selected_phase_local_task_id:"):
                task_id = line.split(":", 1)[1].strip()
            elif line.startswith("selected_phase_local_task_action:"):
                task_action = line.split(":", 1)[1].strip()
            elif line.startswith("recent_drift_linked_task:"):
                drift_task_id = line.split(":", 1)[1].strip()
            elif line.startswith("recent_drift_linked_task_action:"):
                drift_action = line.split(":", 1)[1].strip()
        if (
            task_id in {"", "none"}
            and task_action in {"", "none"}
            and drift_task_id in {"", "none"}
            and drift_action in {"", "none"}
        ):
            return None
        return {
            "task_id": "" if task_id == "none" else task_id,
            "task_action": "" if task_action == "none" else task_action,
            "drift_task_id": "" if drift_task_id == "none" else drift_task_id,
            "drift_action": "" if drift_action == "none" else drift_action,
        }

    def _timeline_replay_source_mode(self) -> str:
        if self.state.plan_timeline_compare_mode != "none":
            compare = self._selected_timeline_compare_context()
            if compare is not None:
                return "compare-item"
        phase_local = self._selected_phase_local_summary_context()
        if phase_local is not None and phase_local["task_id"]:
            return "phase-local-summary"
        return "timeline-entry"

    def _timeline_replay_slice_context(self) -> dict[str, str]:
        phase_filter = "none"
        artifact_id = ""
        compare = self._selected_timeline_compare_context()
        if compare is not None:
            command_context = self._parse_plan_timeline_command_context(compare["primary_action"])
            if command_context is None and compare["secondary_action"]:
                command_context = self._parse_plan_timeline_command_context(compare["secondary_action"])
            if command_context is not None:
                phase_filter = command_context["phase_filter"] or "none"
                artifact_id = command_context["artifact_id"]
        if phase_filter == "none":
            phase_local = self._selected_phase_local_summary_context()
            if phase_local is not None and phase_local["task_id"]:
                phase_filter = "execution-loop"
        return {"phase_filter": phase_filter, "artifact_id": artifact_id}

    def _parse_plan_timeline_command_context(self, command: str) -> dict[str, str] | None:
        raw = command.strip()
        if not raw.startswith("/plan timeline "):
            return None
        phase_filter = "none"
        artifact_id = ""
        for token in raw.split()[2:]:
            lowered = token.lower()
            if lowered.startswith("phase="):
                phase_filter = token.split("=", 1)[1].strip() or "none"
            elif lowered.startswith("artifact="):
                artifact_id = token.split("=", 1)[1].strip()
        return {"phase_filter": phase_filter, "artifact_id": artifact_id}

    def _selected_execution_task_id(self) -> str | None:
        if self.state.selected_task_id and "task_role: execution" in self.state.task_detail_text:
            return self.state.selected_task_id
        context = self._selected_plan_task_context()
        if context is not None and self.state.plan_panel_view == "execution":
            return context["task_id"]
        return None

    def _selected_workspace_context(self) -> dict[str, str] | None:
        if self.state.selected_task_id and hasattr(self.session, "task_workspace_action_bundle"):
            context = self.session.task_workspace_action_bundle(self.state.selected_task_id)
            if context is not None:
                return context
        if hasattr(self.session, "current_workspace_action_bundle"):
            context = self.session.current_workspace_action_bundle()
            if context is not None:
                return context
        return None

    def _selected_checklist_context(self) -> dict[str, str] | None:
        selected_id = self.state.selected_task_id or self.state.selected_checklist_task_id
        if selected_id and hasattr(self.session, "checklist_task_action_bundle"):
            context = self.session.checklist_task_action_bundle(selected_id)
            if context is not None:
                return context
        return None

    def _selected_symbol_context(self) -> dict[str, str] | None:
        if hasattr(self.session, "current_symbol_surface_action_bundle"):
            context = self.session.current_symbol_surface_action_bundle()
            if context is not None:
                return context
        return None

    def _selected_file_context_context(self) -> dict[str, object] | None:
        if self.state.selected_task_id and self.state.task_detail_view in {"detail", "advisor", "drift"}:
            metadata = self.state.task_detail_file_context_metadata
            if isinstance(metadata, dict) and metadata:
                return {
                    "source": "task",
                    "task_id": self.state.selected_task_id,
                    "payload": metadata,
                    "selected_index": self.state.task_detail_file_context_index,
                }
        plan_context = self._selected_plan_panel_file_context()
        if isinstance(plan_context, dict):
            return plan_context
        metadata = self._selected_change_file_context_metadata()
        if isinstance(metadata, dict) and metadata:
            return {
                "source": "changes",
                "payload": metadata,
                "selected_index": (
                    self.state.selected_redo_file_index
                    if self.state.selected_change_stack == "redo"
                    else self.state.selected_change_file_index
                ),
            }
        if hasattr(self.session, "working_set_payload"):
            try:
                metadata = self.session.working_set_payload()
            except Exception:  # noqa: BLE001
                metadata = None
            if isinstance(metadata, dict) and metadata:
                return {
                    "source": "working-set",
                    "payload": metadata,
                }
        return None

    def _file_context_binding_label(self) -> str:
        context = self._selected_file_context_context()
        source = str(context.get("source") or "").strip() if isinstance(context, dict) else ""
        mapping = {
            "task": "focus task files",
            "plan": "focus plan files",
            "changes": "focus change files",
            "working-set": "focus working set files",
        }
        return mapping.get(source, "focus files")

    def _update_file_context_footer_hints(self) -> None:
        label = self._file_context_binding_label()
        primary_summary, secondary_summary = self._current_file_context_navigation_summaries()
        descriptions = {
            "select_prev_change_file": f"Prev ({label})",
            "select_next_change_file": f"Next ({label})",
            "execute_lineage_show": f"Primary ({primary_summary})",
            "execute_lineage_default_action": f"Secondary ({secondary_summary})",
        }
        changed = False
        for key, bindings in list(self._bindings.key_to_bindings.items()):
            updated_bindings = list(bindings)
            for index, binding in enumerate(bindings):
                description = descriptions.get(binding.action)
                if description and binding.description != description:
                    updated_bindings[index] = replace(binding, description=description)
                    changed = True
            if changed:
                self._bindings.key_to_bindings[key] = updated_bindings
        if not changed:
            return
        try:
            self.refresh_bindings()
        except Exception:  # noqa: BLE001
            pass
        try:
            footer = self.query_one(Footer)
        except Exception:  # noqa: BLE001
            return
        try:
            footer.refresh_bindings()
        except Exception:  # noqa: BLE001
            try:
                footer.refresh()
            except Exception:  # noqa: BLE001
                pass

    def _current_file_context_navigation_summaries(self) -> tuple[str, str]:
        context = self._selected_file_context_context()
        if not isinstance(context, dict):
            return ("none", "none")
        payload = context.get("payload")
        if not isinstance(payload, dict):
            return ("none", "none")
        selected_index = int(context.get("selected_index") or 0)
        primary_target = self._selected_file_context_primary_target(
            payload,
            selected_index=selected_index,
        )
        secondary_target = self._file_context_secondary_target(
            payload,
            selected_index=selected_index,
        )
        primary_summary = self._format_file_context_target_summary(primary_target)
        if isinstance(secondary_target, dict):
            secondary_summary = self._format_file_context_target_summary(secondary_target)
        elif isinstance(primary_target, dict):
            secondary_summary = primary_summary + " (fallback)"
        else:
            secondary_summary = "none"
        return (primary_summary, secondary_summary)

    def _file_context_secondary_target(
        self,
        payload: dict[str, object],
        *,
        selected_index: int = 0,
    ) -> dict[str, object] | None:
        file_items = payload.get("file_context_files")
        if isinstance(file_items, list) and file_items:
            bounded_index = max(0, min(len(file_items) - 1, selected_index))
            focused_item = file_items[bounded_index]
            if isinstance(focused_item, dict):
                diff_targets = focused_item.get("diff_targets")
                if diff_targets not in (None, ""):
                    payload = focused_item
        diff_targets = payload.get("diff_targets", payload.get("file_context_primary_diff_targets"))
        if isinstance(diff_targets, list):
            for item in diff_targets:
                if isinstance(item, dict):
                    return item
            return None
        if isinstance(diff_targets, dict):
            hunks = diff_targets.get("hunks")
            if isinstance(hunks, list):
                for item in hunks:
                    if isinstance(item, dict):
                        return item
            return diff_targets
        return None

    def _format_file_context_target_summary(
        self,
        target: dict[str, object] | None,
    ) -> str:
        if not isinstance(target, dict):
            return "none"
        action = str(target.get("action") or "").strip()
        path = str(target.get("path") or "").strip()
        line = target.get("line")
        label = str(target.get("label") or "").strip()
        parts: list[str] = []
        if action:
            parts.append(action)
        if path:
            location = path
            if line not in (None, ""):
                location += f":{line}"
            parts.append(location)
        if label:
            parts.append(label)
        return " ".join(parts) if parts else "none"

    def _selected_file_context_primary_target(
        self,
        payload: dict[str, object],
        *,
        selected_index: int = 0,
    ) -> dict[str, object] | None:
        file_items = payload.get("file_context_files")
        if isinstance(file_items, list) and file_items:
            bounded_index = max(0, min(len(file_items) - 1, selected_index))
            focused_item = file_items[bounded_index]
            if isinstance(focused_item, dict) and isinstance(focused_item.get("target"), dict):
                return focused_item.get("target")
        target = payload.get("file_context_primary_target")
        return target if isinstance(target, dict) else None

    def _execute_file_context_navigation(self, *, primary: bool) -> bool:
        if self.state.busy:
            return False
        context = self._selected_file_context_context()
        if not isinstance(context, dict):
            return False
        payload = context.get("payload")
        if not isinstance(payload, dict):
            return False
        selected_index = int(context.get("selected_index") or 0)
        target = (
            self._selected_file_context_primary_target(payload, selected_index=selected_index)
            if primary
            else self._file_context_secondary_target(payload, selected_index=selected_index)
            or self._selected_file_context_primary_target(payload, selected_index=selected_index)
        )
        if not isinstance(target, dict):
            return False
        summary = self._format_file_context_target_summary(target)
        source = str(context.get("source") or "file-context")
        self._append_event(f"Selected {source} file target: {summary}")
        return True

    def _move_file_context_focus(self, delta: int) -> bool:
        if self.state.task_detail_view in {"detail", "advisor", "drift"} and isinstance(
            self.state.task_detail_file_context_metadata,
            dict,
        ):
            files = self.state.task_detail_file_context_metadata.get("file_context_files")
            total = len(files) if isinstance(files, list) else 0
            if total > 0:
                self.state.move_task_detail_file_context_selection(delta, total=total)
                if self.state.selected_task_id and hasattr(self.session, "remember_task_context_focus"):
                    try:
                        self.session.remember_task_context_focus(
                            self.state.selected_task_id,
                            file_index=self.state.task_detail_file_context_index,
                            preserve_current_focus=False,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                primary_summary, secondary_summary = self._current_file_context_navigation_summaries()
                self._append_event(
                    f"Task file focus: {self.state.task_detail_file_context_index + 1}/{total} "
                    f"(F9: {primary_summary}; F10: {secondary_summary})"
                )
                return True
        plan_context = self._selected_plan_panel_file_context()
        if isinstance(plan_context, dict):
            metadata = plan_context.get("payload")
            files = metadata.get("file_context_files") if isinstance(metadata, dict) else None
            total = len(files) if isinstance(files, list) else 0
            if total > 0:
                self.state.move_plan_file_context_selection(delta, total=total)
                self.state.plan_file_context_metadata = metadata if isinstance(metadata, dict) else None
                source = str(plan_context.get("source") or "").strip()
                task_id = str(plan_context.get("task_id") or "").strip()
                if source == "task" and task_id and hasattr(self.session, "remember_task_context_focus"):
                    try:
                        self.session.remember_task_context_focus(
                            task_id,
                            file_index=self.state.plan_file_context_index,
                            preserve_current_focus=False,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                elif isinstance(metadata, dict) and hasattr(self.session, "remember_plan_context_focus_payload"):
                    try:
                        self.session.remember_plan_context_focus_payload(
                            metadata,
                            file_index=self.state.plan_file_context_index,
                            preserve_current_focus=False,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                primary_summary, secondary_summary = self._current_file_context_navigation_summaries()
                self._append_event(
                    f"Plan file focus: {self.state.plan_file_context_index + 1}/{total} "
                    f"(F9: {primary_summary}; F10: {secondary_summary})"
                )
                return True
        return False

    def _append_change_file_focus_event(self, *, total: int) -> None:
        if total <= 0:
            return
        selected_index = (
            self.state.selected_redo_file_index
            if self.state.selected_change_stack == "redo"
            else self.state.selected_change_file_index
        )
        primary_summary, secondary_summary = self._current_file_context_navigation_summaries()
        self._append_event(
            f"Change file focus: {selected_index + 1}/{total} "
            f"(F9: {primary_summary}; F10: {secondary_summary})"
        )

    def _available_symbol_focus_groups(self, payload: dict[str, object] | None = None) -> list[str]:
        if payload is None:
            payload = self._current_symbol_surface_metadata()
        if not isinstance(payload, dict):
            return []
        groups: list[str] = []
        if isinstance(payload.get("matches"), list) and payload.get("matches"):
            groups.append("matches")
        if isinstance(payload.get("definitions"), list) and payload.get("definitions"):
            groups.append("definitions")
        if isinstance(payload.get("references"), list) and payload.get("references"):
            groups.append("references")
        return groups

    def _selected_index_for_symbol_group(self, payload: dict[str, object], group: str) -> int | None:
        key_map = {
            "matches": "selected_match_index",
            "definitions": "selected_definition_index",
            "references": "selected_reference_index",
        }
        try:
            value = payload.get(key_map[group])
            return int(value) if value is not None else None
        except (KeyError, TypeError, ValueError):
            return None

    def _sync_symbol_focus_state(self, payload: dict[str, object] | None = None) -> None:
        if payload is None:
            payload = self._current_symbol_surface_metadata()
        groups = self._available_symbol_focus_groups(payload)
        if not groups:
            self.state.symbol_focus_group = None
            self.state.symbol_focus_index = None
            return
        current_group = self.state.symbol_focus_group
        if current_group not in groups:
            current_group = groups[0]
            self.state.symbol_focus_group = current_group
        if not isinstance(payload, dict):
            self.state.symbol_focus_index = None
            return
        items = payload.get(current_group)
        if not isinstance(items, list) or not items:
            self.state.symbol_focus_index = None
            return
        selected_index = self._selected_index_for_symbol_group(payload, current_group)
        current_index = self.state.symbol_focus_index
        if current_index is None or current_index < 0 or current_index >= len(items):
            self.state.symbol_focus_index = selected_index if selected_index is not None else 0

    def _move_symbol_focus_group(self, delta: int) -> None:
        payload = self._current_symbol_surface_metadata()
        groups = self._available_symbol_focus_groups(payload)
        if not groups:
            self._append_event("No symbol candidates are available in the current context.")
            return
        self._sync_symbol_focus_state(payload)
        current_group = self.state.symbol_focus_group or groups[0]
        try:
            current_index = groups.index(current_group)
        except ValueError:
            current_index = 0
        next_group = groups[(current_index + delta) % len(groups)]
        self.state.symbol_focus_group = next_group
        if isinstance(payload, dict):
            selected_index = self._selected_index_for_symbol_group(payload, next_group)
            items = payload.get(next_group)
            if isinstance(items, list) and items:
                self.state.symbol_focus_index = selected_index if selected_index is not None else 0
            else:
                self.state.symbol_focus_index = None
        self._append_event(f"Symbol focus group: {self.state.symbol_focus_group}")
        self._render()

    def _move_symbol_focus_item(self, delta: int) -> None:
        payload = self._current_symbol_surface_metadata()
        if not isinstance(payload, dict):
            self._append_event("No symbol candidates are available in the current context.")
            return
        self._sync_symbol_focus_state(payload)
        group = self.state.symbol_focus_group
        if not group:
            self._append_event("No symbol candidates are available in the current context.")
            return
        if group == "matches":
            self._cycle_symbol_selection(primary_group=True, delta=delta)
        elif group == "definitions":
            self._cycle_symbol_selection(primary_group=True, delta=delta)
        elif group == "references":
            self._cycle_symbol_selection(primary_group=False, delta=delta)

    def _open_focused_symbol_candidate(self) -> None:
        payload = self._current_symbol_surface_metadata()
        if not isinstance(payload, dict):
            self._append_event("No focused symbol candidate is available in the current context.")
            return
        self._sync_symbol_focus_state(payload)
        group = self.state.symbol_focus_group
        if not group:
            self._append_event("No focused symbol candidate is available in the current context.")
            return
        surface_kind = str(payload.get("surface_kind") or "").strip()
        if group in {"matches", "definitions"}:
            self._execute_symbol_action(primary=True)
            return
        if group == "references":
            if surface_kind == "symbol_references":
                self._execute_symbol_action(primary=True)
            else:
                self._execute_symbol_action(primary=False)

    def _begin_checklist_edit(self, *, field: str) -> None:
        task_id = self._selected_checklist_task_id()
        if not task_id:
            self._append_event("No checklist task is available in the current context.")
            return
        context = self._selected_checklist_context()
        if context is None:
            self._append_event("No checklist task is available in the current context.")
            return
        multiline = False
        if field == "subject":
            action = str(context.get("edit_subject_action") or "").strip()
            prompt = f'Enter new subject for checklist task "{task_id}" in the input box and press Enter.'
        elif field == "description":
            action = str(context.get("edit_description_action") or "").strip()
            prompt = (
                f'Enter new description lines for checklist task "{task_id}" and press Enter after each line. '
                'Use ".done" to apply or ".cancel" to abort. Submit ".done" immediately to clear the description.'
            )
            multiline = True
        elif field == "owner":
            action = str(context.get("edit_owner_action") or "").strip()
            prompt = (
                f'Enter new owner for checklist task "{task_id}" in the input box and press Enter. '
                "Submit an empty value to clear the owner."
            )
        elif field == "active_form":
            action = str(context.get("edit_active_form_action") or "").strip()
            prompt = (
                f'Enter new active_form for checklist task "{task_id}" in the input box and press Enter.'
            )
        elif field == "blocks":
            action = str(context.get("edit_blocks_action") or "").strip()
            prompt = (
                f'Enter blocks for checklist task "{task_id}" one per line (comma-separated lines also work). '
                'Use ".done" to apply or ".cancel" to abort. Submit ".done" immediately to clear all blocks.'
            )
            multiline = True
        elif field == "blocked_by":
            action = str(context.get("edit_blocked_by_action") or "").strip()
            prompt = (
                f'Enter blocked_by values for checklist task "{task_id}" one per line (comma-separated lines also work). '
                'Use ".done" to apply or ".cancel" to abort. Submit ".done" immediately to clear all blocked_by entries.'
            )
            multiline = True
        else:
            action = str(context.get("edit_metadata_action") or "").strip()
            prompt = (
                f'Enter metadata lines for checklist task "{task_id}" as key=value. '
                'Use ".done" to apply or ".cancel" to abort. Submit ".done" immediately to clear all metadata.'
            )
            multiline = True
        if not action or action == "none":
            self._append_event("No checklist edit action is available in the current context.")
            return
        self.state.pending_checklist_edit = PendingChecklistEdit(
            task_id=task_id,
            field=field,
            action=action,
            prompt=prompt,
            multiline=multiline,
        )
        self._append_event(prompt)
        self._render()

    def _submit_pending_checklist_edit(self, raw_value: str) -> None:
        pending = self.state.pending_checklist_edit
        if pending is None:
            return
        if pending.multiline:
            command = raw_value.strip()
            if command == ".cancel":
                self.state.pending_checklist_edit = None
                self._append_event("Canceled checklist edit.")
                self._render()
                return
            if command != ".done":
                pending.lines.append(raw_value)
                self._append_event(
                    f'Captured checklist {pending.field} line {len(pending.lines)}. Enter ".done" to apply.'
                )
                self._render()
                return
            value = "\n".join(pending.lines)
        else:
            value = raw_value
        if pending.field == "active_form" and not value.strip():
            self._append_event("Checklist active_form cannot be empty.")
            self._render()
            return
        if pending.field == "subject" and not value.strip():
            self._append_event("Checklist subject cannot be empty.")
            self._render()
            return
        self.state.pending_checklist_edit = None
        self._append_chat("You", f"{pending.action} {value}".rstrip())
        if pending.field == "subject":
            output = self.session.checklist_set_subject(pending.task_id, value)
        elif pending.field == "description":
            output = self.session.checklist_set_description(pending.task_id, value)
        elif pending.field == "owner":
            output = self.session.checklist_set_owner(pending.task_id, value)
        elif pending.field == "active_form":
            output = self.session.checklist_set_active_form(pending.task_id, value)
        elif pending.field == "blocks":
            output = self.session.checklist_set_blocks(pending.task_id, value)
        elif pending.field == "blocked_by":
            output = self.session.checklist_set_blocked_by(pending.task_id, value)
        else:
            output = self.session.checklist_set_metadata(pending.task_id, value)
        if pending.field == "metadata" and output.startswith("Checklist metadata "):
            self.state.pending_checklist_edit = pending
            self._append_event(output)
            self._render()
            return
        self._refresh_selected_task_detail()
        if output:
            self.state.append_message("System", output)
        self._render()

    def _request_background_followup(self, *, mode: str) -> None:
        selected = self._resolved_background_registry_context()
        if not isinstance(selected, dict):
            self._append_event("No background session is selected.")
            self._render()
            return
        bg_id = str(selected.get("background_session_id") or "").strip()
        if not bg_id:
            self._append_event("Selected background session has no id.")
            self._render()
            return
        if mode == "send" and not bool(selected.get("background_live_attachable")):
            self._append_event("Selected background session is not live-attachable.")
            self._render()
            return
        if mode not in {"send", "queue"}:
            self._append_event("Unsupported background follow-up mode.")
            self._render()
            return
        label = "send" if mode == "send" else "queue"
        prompt = (
            f'Enter a background follow-up message to {label} for "{bg_id}". '
            'Use ".cancel" to abort.'
        )
        self.state.pending_background_followup = PendingBackgroundFollowup(
            bg_id=bg_id,
            mode=mode,
            prompt=prompt,
        )
        self._append_event(prompt)
        self._render()

    def _submit_pending_background_followup(self, raw_value: str) -> None:
        pending = self.state.pending_background_followup
        if pending is None:
            return
        command = raw_value.strip()
        if command == ".cancel":
            self.state.pending_background_followup = None
            self._append_event("Canceled background follow-up.")
            self._render()
            return
        if not command:
            self._append_event("Background follow-up cannot be empty.")
            self._render()
            return
        self.state.pending_background_followup = None
        if pending.mode == "send":
            output = self.session.send_background_followup(pending.bg_id, raw_value)
            action_label = "send_background_followup"
        else:
            output = self.session.queue_background_message(pending.bg_id, raw_value)
            action_label = "queue_background_message"
        self._append_chat("You", f"{action_label} {pending.bg_id} {command}".rstrip())
        if output:
            self.state.append_message("System", output)
        self._render()

    def _checklist_tasks_payload(self) -> list[dict[str, object]]:
        if not hasattr(self.session, "checklist_tasks_payload"):
            return []
        try:
            payload = self.session.checklist_tasks_payload()
        except Exception:  # noqa: BLE001
            return []
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def _checklist_duplicate_guard_metadata(self) -> dict[str, object] | None:
        if not hasattr(self.session, "checklist_duplicate_guard_payload"):
            return None
        try:
            payload = self.session.checklist_duplicate_guard_payload()
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(payload, dict) or not payload:
            return None
        return {
            "checklist_duplicate_guard": dict(payload),
            "checklist_duplicate_message": str(payload.get("message") or ""),
            "checklist_duplicate_matched_task_id": str(payload.get("matched_task_id") or ""),
            "checklist_duplicate_recommended_action": str(
                payload.get("recommended_action") or ""
            ),
        }

    def _visible_checklist_tasks_payload(self) -> list[dict[str, object]]:
        return self.state.ordered_visible_checklist_tasks_payload(
            self._checklist_tasks_payload(),
            checklist_filter=self.state.checklist_filter,
            checklist_sort=self.state.checklist_sort,
        )

    def _selected_checklist_task_id(self) -> str | None:
        tasks = self._visible_checklist_tasks_payload()
        if not tasks:
            return None
        selected_task_id = (self.state.selected_checklist_task_id or "").strip()
        if selected_task_id:
            for item in tasks:
                task_id = str(item.get("id") or "").strip()
                if task_id == selected_task_id:
                    return task_id
        task_id = str(tasks[0].get("id") or "").strip()
        return task_id or None

    def _move_selected_checklist_task(self, delta: int) -> None:
        tasks = self._visible_checklist_tasks_payload()
        if not tasks:
            self._append_event("No checklist task is available in the current context.")
            return
        selected_task_id = (self.state.selected_checklist_task_id or "").strip()
        if not selected_task_id:
            next_index = 0 if delta >= 0 else len(tasks) - 1
            next_task_id = str(tasks[next_index].get("id") or "").strip()
            self.state.selected_checklist_task_id = next_task_id or None
            self._render()
            return
        index = 0
        if selected_task_id:
            for position, item in enumerate(tasks):
                if str(item.get("id") or "").strip() == selected_task_id:
                    index = position
                    break
        next_index = max(0, min(len(tasks) - 1, index + delta))
        next_task_id = str(tasks[next_index].get("id") or "").strip()
        self.state.selected_checklist_task_id = next_task_id or None
        self._render()

    def _refresh_selected_task_detail(self) -> None:
        task_id = self.state.selected_task_id
        if not task_id:
            return
        try:
            if hasattr(self.session, "describe_task_detail"):
                output = self.session.describe_task_detail(task_id)
            elif hasattr(self.session, "open_task_detail"):
                output = self.session.open_task_detail(task_id)
            else:
                return
        except Exception:  # noqa: BLE001
            return
        self.state.set_task_detail(
            task_id=task_id,
            text=output,
            execution_metadata=self._task_execution_detail_metadata(task_id),
            workspace_metadata=self._task_workspace_detail_metadata(task_id),
            checklist_metadata=self._task_checklist_detail_metadata(task_id),
            file_context_metadata=self._resolved_task_file_context(task_id)[0],
        )
        self._set_task_detail_file_context_state(task_id)

    def _task_execution_detail_metadata(self, task_id: str) -> dict[str, object] | None:
        if hasattr(self.session, "task_execution_detail_metadata"):
            try:
                metadata = self.session.task_execution_detail_metadata(task_id)
            except Exception:  # noqa: BLE001
                return None
            if isinstance(metadata, dict):
                return metadata
        return None

    def _task_workspace_detail_metadata(self, task_id: str) -> dict[str, object] | None:
        if hasattr(self.session, "task_workspace_detail_metadata"):
            try:
                metadata = self.session.task_workspace_detail_metadata(task_id)
            except Exception:  # noqa: BLE001
                return None
            if isinstance(metadata, dict):
                return metadata
        return None

    def _task_checklist_detail_metadata(self, task_id: str) -> dict[str, object] | None:
        if hasattr(self.session, "checklist_task_detail_metadata"):
            try:
                metadata = self.session.checklist_task_detail_metadata(task_id)
            except Exception:  # noqa: BLE001
                return None
            if isinstance(metadata, dict):
                return metadata
        return None

    def _task_file_context_metadata(self, task_id: str) -> dict[str, object] | None:
        if hasattr(self.session, "task_file_context_payload"):
            try:
                metadata = self.session.task_file_context_payload(task_id)
            except Exception:  # noqa: BLE001
                return None
            if isinstance(metadata, dict) and metadata:
                return metadata
        return None

    def _resolved_task_file_context(
        self,
        task_id: str,
        *,
        fallback_index: int = 0,
        preserve_current_focus: bool = True,
    ) -> tuple[dict[str, object] | None, int]:
        if hasattr(self.session, "resolve_task_file_context"):
            try:
                resolved = self.session.resolve_task_file_context(
                    task_id,
                    file_index=fallback_index,
                    preserve_current_focus=preserve_current_focus,
                )
            except Exception:  # noqa: BLE001
                resolved = None
            if isinstance(resolved, dict):
                metadata = resolved.get("payload")
                selected_index = int(resolved.get("selected_index") or 0)
                if isinstance(metadata, dict) and metadata:
                    return metadata, selected_index
                return None, selected_index
        metadata = self._task_file_context_metadata(task_id)
        selected_index = fallback_index
        if hasattr(self.session, "preferred_task_file_index"):
            try:
                selected_index = self.session.preferred_task_file_index(
                    task_id,
                    fallback=fallback_index,
                )
            except Exception:  # noqa: BLE001
                selected_index = fallback_index
        return metadata, selected_index

    def _set_task_detail_file_context_state(
        self,
        task_id: str,
        *,
        fallback_index: int = 0,
        preserve_current_focus: bool = True,
    ) -> None:
        metadata, selected_index = self._resolved_task_file_context(
            task_id,
            fallback_index=fallback_index,
            preserve_current_focus=preserve_current_focus,
        )
        self.state.task_detail_file_context_metadata = metadata
        self.state.task_detail_file_context_index = selected_index

    def _artifact_file_context_metadata(self, artifact_id: str) -> dict[str, object] | None:
        if hasattr(self.session, "active_plan_file_context_payload"):
            try:
                metadata = self.session.active_plan_file_context_payload(artifact_id)
            except Exception:  # noqa: BLE001
                return None
            if isinstance(metadata, dict) and metadata:
                return metadata
        return None

    def _timeline_artifact_id(self) -> str | None:
        text = self._plan_panel_text()
        for line in text.splitlines():
            if line.startswith("timeline_artifact:"):
                artifact_id = line.split(":", 1)[1].strip()
                return artifact_id or None
        return None

    def _selected_replay_task_id(self) -> str | None:
        if self.state.plan_panel_view != "replay":
            return None
        text = self._plan_panel_text()
        for line in text.splitlines():
            if line.startswith("- task_id:"):
                task_id = line.split(":", 1)[1].strip()
                return task_id or None
        return None

    def _replay_artifact_id(self) -> str | None:
        if self.state.plan_panel_view != "replay":
            return None
        text = self._plan_panel_text()
        for line in text.splitlines():
            if line.startswith("replay_artifact:"):
                artifact_id = line.split(":", 1)[1].strip()
                return artifact_id or None
        return None

    def _selected_plan_panel_file_context(self) -> dict[str, object] | None:
        if self.state.plan_panel_view in {"summary", "advisor"}:
            metadata = self._artifact_file_context_metadata("active") or self._active_plan_file_context_metadata()
            if isinstance(metadata, dict) and metadata:
                return {"source": "plan", "payload": metadata, "selected_index": self.state.plan_file_context_index}
            return None
        if self.state.plan_panel_view == "scouts":
            metadata = self._active_plan_file_context_metadata()
            if isinstance(metadata, dict) and metadata:
                return {"source": "plan", "payload": metadata, "selected_index": self.state.plan_file_context_index}
            return None
        if self.state.plan_panel_view == "execution":
            metadata = self._active_plan_file_context_metadata()
            if isinstance(metadata, dict) and metadata:
                return {"source": "plan", "payload": metadata, "selected_index": self.state.plan_file_context_index}
            return None
        if self.state.plan_panel_view == "timeline":
            phase_local = self._selected_phase_local_summary_context()
            task_id = phase_local["task_id"] if phase_local is not None and phase_local["task_id"] else self._selected_timeline_task_id()
            if task_id:
                metadata = self._task_file_context_metadata(task_id)
                if isinstance(metadata, dict) and metadata:
                    return {
                        "source": "task",
                        "task_id": task_id,
                        "payload": metadata,
                        "selected_index": self.state.plan_file_context_index,
                    }
            artifact_id = self._timeline_artifact_id()
            if artifact_id:
                metadata = self._artifact_file_context_metadata(artifact_id)
                if isinstance(metadata, dict) and metadata:
                    return {"source": "plan", "payload": metadata, "selected_index": self.state.plan_file_context_index}
            return None
        if self.state.plan_panel_view == "replay":
            task_id = self._selected_replay_task_id()
            if task_id:
                metadata = self._task_file_context_metadata(task_id)
                if isinstance(metadata, dict) and metadata:
                    return {
                        "source": "task",
                        "task_id": task_id,
                        "payload": metadata,
                        "selected_index": self.state.plan_file_context_index,
                    }
            artifact_id = self._replay_artifact_id()
            if artifact_id:
                metadata = self._artifact_file_context_metadata(artifact_id)
                if isinstance(metadata, dict) and metadata:
                    return {"source": "plan", "payload": metadata, "selected_index": self.state.plan_file_context_index}
            return None
        if self.state.plan_panel_view == "audit":
            context = self._selected_audit_context()
            artifact_id = context["artifact_id"] if context is not None else ""
            if artifact_id:
                metadata = self._artifact_file_context_metadata(artifact_id)
                if isinstance(metadata, dict) and metadata:
                    return {"source": "plan", "payload": metadata, "selected_index": self.state.plan_file_context_index}
            return None
        return None

    def _active_plan_file_context_metadata(self) -> dict[str, object] | None:
        try:
            if self.state.plan_panel_view == "summary" and hasattr(self.session, "active_plan_file_context_payload"):
                metadata = self.session.active_plan_file_context_payload()
            elif self.state.plan_panel_view == "advisor" and hasattr(self.session, "active_plan_file_context_payload"):
                metadata = self.session.active_plan_file_context_payload()
            elif self.state.plan_panel_view == "scouts" and hasattr(self.session, "active_plan_scout_file_context_payload"):
                metadata = self.session.active_plan_scout_file_context_payload(
                    selected_index=self.state.selected_plan_scout_index
                )
            elif self.state.plan_panel_view == "execution" and hasattr(
                self.session,
                "active_plan_execution_file_context_payload",
            ):
                metadata = self.session.active_plan_execution_file_context_payload(
                    selected_index=self.state.selected_plan_execution_index
                )
            else:
                metadata = None
        except Exception:  # noqa: BLE001
            return None
        if isinstance(metadata, dict) and metadata:
            return metadata
        return None

    def _selected_change_file_context_metadata(self) -> dict[str, object] | None:
        if not hasattr(self.session, "selected_change_detail_metadata"):
            return None
        redo = self.state.selected_change_stack == "redo"
        index = self.state.selected_redo_index if redo else self.state.selected_change_index
        file_index = self.state.selected_redo_file_index if redo else self.state.selected_change_file_index
        try:
            metadata = self.session.selected_change_detail_metadata(
                index=index,
                file_index=file_index,
                limit=5,
                redo=redo,
            )
        except Exception:  # noqa: BLE001
            return None
        if isinstance(metadata, dict) and metadata:
            return metadata
        return None

    def _execute_workspace_action(self, *, primary: bool) -> None:
        if self.state.busy:
            return
        context = self._selected_workspace_context()
        if context is None:
            self._append_event("No workspace action is available in the current context.")
            return
        action_text = context["primary_action" if primary else "secondary_action"].strip()
        if not action_text or action_text == "none":
            self._append_event("No workspace action is available in the current context.")
            return
        if action_text.startswith("/"):
            self._execute_navigation_command(action_text)
            return
        self._append_chat("You", action_text)
        output = ""
        if action_text == "workspace_cleanup_preview":
            output = self.session.workspace_cleanup_preview()
        elif action_text.startswith("workspace_cleanup_apply "):
            output = self.session.workspace_cleanup_apply(action_text.split(" ", 1)[1].strip())
        elif action_text.startswith("workspace_repair "):
            output = self.session.workspace_repair(action_text.split(" ", 1)[1].strip())
        else:
            self._append_event(f"Unsupported workspace action: {action_text}")
            return
        self._refresh_selected_task_detail()
        if output:
            self.state.append_message("System", output)
        self._render()

    def _background_registry_payload(self) -> dict[str, object] | None:
        if not hasattr(self.session, "background_registry_payload"):
            return None
        try:
            payload = self.session.background_registry_payload()
        except Exception:  # noqa: BLE001
            return None
        return payload if isinstance(payload, dict) and payload else None

    def _resolved_background_registry_context(self) -> dict[str, object] | None:
        payload = self._background_registry_payload()
        if not isinstance(payload, dict):
            return None
        entries = payload.get("background_registry_entries")
        if not isinstance(entries, list) or not entries:
            return None
        selected_bg_id = self.state.selected_background_registry_bg_id
        selected_index = self.state.selected_background_registry_index
        resolved_index = 0
        selected_entry: dict[str, object] | None = None
        for index, item in enumerate(entries):
            if not isinstance(item, dict):
                continue
            bg_id = str(item.get("background_session_id") or "").strip()
            if selected_bg_id and bg_id == selected_bg_id:
                resolved_index = index
                selected_entry = item
                break
        if selected_entry is None and 0 <= selected_index < len(entries):
            item = entries[selected_index]
            if isinstance(item, dict):
                resolved_index = selected_index
                selected_entry = item
        if selected_entry is None:
            preferred_bg_id = str(payload.get("background_registry_selected_bg_id") or "").strip()
            for index, item in enumerate(entries):
                if not isinstance(item, dict):
                    continue
                if preferred_bg_id and str(item.get("background_session_id") or "").strip() == preferred_bg_id:
                    resolved_index = index
                    selected_entry = item
                    break
        if selected_entry is None:
            first = entries[0]
            if not isinstance(first, dict):
                return None
            selected_entry = first
            resolved_index = 0
        self.state.selected_background_registry_index = resolved_index
        self.state.selected_background_registry_bg_id = (
            str(selected_entry.get("background_session_id") or "").strip() or None
        )
        return selected_entry

    def _selected_rewind_boundary_payload(self) -> dict[str, object] | None:
        if not hasattr(self.session, "memory_surface_payload"):
            return None
        try:
            memory_payload = self.session.memory_surface_payload()
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(memory_payload, dict):
            return None
        rewindable_count = int(
            memory_payload.get(
                "memory_rewindable_boundary_count",
                memory_payload.get("rewindable_history_boundary_count") or 0,
            )
            or 0
        )
        if rewindable_count <= 0 or not hasattr(self.session, "rewind_boundary_preview_payload"):
            self.state.selected_rewind_boundary_index = 0
            return None
        resolved_index = max(0, min(self.state.selected_rewind_boundary_index, rewindable_count - 1))
        self.state.selected_rewind_boundary_index = resolved_index
        try:
            payload = self.session.rewind_boundary_preview_payload(str(resolved_index + 1))
        except Exception:  # noqa: BLE001
            return None
        return payload if isinstance(payload, dict) and payload else None

    def _move_rewind_boundary_selection(self, delta: int) -> None:
        if not hasattr(self.session, "memory_surface_payload"):
            self._append_event("Memory metadata is not available in the current session.")
            return
        try:
            memory_payload = self.session.memory_surface_payload()
        except Exception:  # noqa: BLE001
            self._append_event("Failed to load rewind boundary metadata.")
            return
        rewindable_count = int(
            memory_payload.get(
                "memory_rewindable_boundary_count",
                memory_payload.get("rewindable_history_boundary_count") or 0,
            )
            if isinstance(memory_payload, dict)
            else 0
        )
        if rewindable_count <= 0:
            self.state.selected_rewind_boundary_index = 0
            self._append_event("No rewindable boundaries are available in the current session.")
            return
        self.state.move_rewind_boundary_selection(delta, total=rewindable_count)
        payload = self._selected_rewind_boundary_payload()
        if isinstance(payload, dict):
            self._append_event(
                "rewind boundary selection: "
                + str(payload.get("boundary_id") or f"selector {payload.get('selector_index') or 1}")
            )
        self._render()

    def _move_background_registry_selection(self, delta: int) -> None:
        payload = self._background_registry_payload()
        entries = payload.get("background_registry_entries") if isinstance(payload, dict) else None
        total = len(entries) if isinstance(entries, list) else 0
        if total <= 0:
            self._append_event("No background sessions are available in the current workspace.")
            return
        self._resolved_background_registry_context()
        self.state.move_background_registry_selection(delta, total=total)
        self.state.selected_background_registry_bg_id = None
        selected = self._resolved_background_registry_context()
        if selected is not None:
            self._append_event(
                "background selection: "
                + (str(selected.get("background_session_id") or "").strip() or "(unknown)")
            )
        self._render()

    def _selected_background_record(self) -> BackgroundSessionRecord | None:
        context = self._resolved_background_registry_context()
        if context is None:
            return None
        bg_id = str(context.get("background_session_id") or "").strip()
        if not bg_id:
            return None
        try:
            cwd = Path(self.session.config.cwd)
        except Exception:  # noqa: BLE001
            return None
        return resolve_background_session(cwd, bg_id)

    def _show_background_logs(self, record: BackgroundSessionRecord) -> None:
        self._append_chat("You", f"pyclaude logs {record.bg_id} summary")
        lines = [f"background logs: {record.bg_id}"]
        log_path = Path(record.log_path) if record.log_path else None
        if log_path is None or not log_path.exists():
            lines.append("No background log file is available.")
        else:
            content = log_path.read_text(encoding="utf-8")
            tail_lines = [line for line in content.splitlines() if line.strip()][-20:]
            if tail_lines:
                lines.append("tail:")
                lines.extend(tail_lines)
            else:
                lines.append("(log is empty)")
        self.state.append_message("System", "\n".join(lines))
        self._render()

    def _swap_active_session(
        self,
        new_session: object,
        *,
        session_source: str,
        restored_from: Path | None = None,
        live_background_id: str | None = None,
    ) -> None:
        old_session = self.session
        self._unbind_session_handlers(old_session)
        self.session = new_session  # type: ignore[assignment]
        self.session_source = session_source
        self.restored_from = restored_from
        self.live_background_id = live_background_id
        self._bind_session_handlers()
        if hasattr(old_session, "close"):
            try:
                old_session.close()
            except Exception:  # noqa: BLE001
                pass
        self._initialize_session_surface(reset_state=True)

    def _attach_background_record(self, record: BackgroundSessionRecord) -> None:
        if not record.bridge_host or not record.bridge_port or not record.session_id:
            self._append_event("Selected background session is not live attachable.")
            self._render()
            return
        try:
            proxy = RemoteSessionProxy(
                host=str(record.bridge_host),
                port=int(record.bridge_port),
                session_id=str(record.session_id),
            )
        except Exception as exc:  # noqa: BLE001
            self._append_event(f"Background attach failed: {type(exc).__name__}: {exc}")
            self._render()
            return
        self._swap_active_session(
            proxy,
            session_source="live_background",
            live_background_id=record.bg_id,
        )

    def _resume_background_session(self, session_id: str) -> None:
        config = replace(self.session.config, interactive=True)
        session_factory = getattr(self.session, "_session_factory", None)
        if not isinstance(session_factory, SessionFactory):
            session_factory = SessionFactory(load_mcp_from_config=True)
        try:
            new_session, restored_from = session_factory.create_or_restore_session(
                config,
                resume_session_id=session_id,
            )
        except Exception as exc:  # noqa: BLE001
            self._append_event(f"Background resume failed: {type(exc).__name__}: {exc}")
            self._render()
            return
        self._swap_active_session(
            new_session,
            session_source="saved_resume",
            restored_from=restored_from,
            live_background_id=None,
        )

    def _execute_background_registry_action(
        self,
        *,
        primary: bool = False,
        logs_only: bool = False,
    ) -> None:
        if self.state.busy:
            return
        context = self._resolved_background_registry_context()
        if context is None:
            self._append_event("No background session is available in the current workspace.")
            return
        record = self._selected_background_record()
        action_text = ""
        if logs_only:
            action_text = str(context.get("background_logs_action") or "").strip()
        else:
            action_text = str(
                context.get("background_primary_action" if primary else "background_secondary_action") or ""
            ).strip()
        if not action_text or action_text == "none":
            self._append_event("No background action is available for the selected session.")
            return
        if action_text.startswith("/"):
            self._execute_navigation_command(action_text)
            return
        if action_text.startswith("pyclaude logs ") and record is not None:
            self._show_background_logs(record)
            return
        if action_text.startswith("pyclaude attach ") and record is not None:
            self._attach_background_record(record)
            return
        if action_text.startswith("pyclaude --resume-session "):
            parts = action_text.split()
            if len(parts) >= 3:
                self._resume_background_session(parts[2])
                return
        self._append_event(f"Unsupported background action: {action_text}")
        self._render()

    def _execute_checklist_action(self, *, primary: bool) -> None:
        if self.state.busy:
            return
        context = self._selected_checklist_context()
        if context is None:
            self._append_event("No checklist action is available in the current context.")
            return
        action_text = context["primary_action" if primary else "secondary_action"].strip()
        if not action_text or action_text == "none":
            self._append_event("No checklist action is available in the current context.")
            return
        if action_text.startswith("/"):
            self._execute_navigation_command(action_text)
            return
        self._append_chat("You", action_text)
        focused_task_id = self._selected_checklist_task_id()
        if focused_task_id and not self.state.selected_task_id:
            self.state.selected_task_id = focused_task_id
        output = ""
        if action_text == "session_task_list":
            output = self.session.describe_tasks()
        elif action_text.startswith("checklist_mark_in_progress "):
            output = self.session.checklist_mark_in_progress(action_text.split(" ", 1)[1].strip())
        elif action_text.startswith("checklist_mark_completed "):
            output = self.session.checklist_mark_completed(action_text.split(" ", 1)[1].strip())
        elif action_text.startswith("checklist_reopen "):
            output = self.session.checklist_reopen(action_text.split(" ", 1)[1].strip())
        else:
            self._append_event(f"Unsupported checklist action: {action_text}")
            return
        self._refresh_selected_task_detail()
        if output:
            self.state.append_message("System", output)
        self._render()

    def _execute_symbol_action(self, *, primary: bool) -> None:
        if self.state.busy:
            return
        context = self._selected_symbol_context()
        if context is None:
            self._append_event("No symbol navigation action is available in the current context.")
            return
        action_text = context["primary_action" if primary else "secondary_action"].strip()
        if not action_text or action_text == "none":
            self._append_event("No symbol navigation action is available in the current context.")
            return
        if action_text.startswith("/"):
            self._execute_navigation_command(action_text)
            return
        self._append_chat("You", action_text)
        if action_text == "symbol_surface_open_primary":
            output = self.session.symbol_surface_primary_action()
        elif action_text == "symbol_surface_open_secondary":
            output = self.session.symbol_surface_secondary_action()
        else:
            self._append_event(f"Unsupported symbol action: {action_text}")
            return
        if output:
            self.state.append_message("System", output)
        self._render()

    def _cycle_symbol_selection(self, *, primary_group: bool, delta: int) -> None:
        if self.state.busy:
            return
        payload = None
        if hasattr(self.session, "current_symbol_surface_payload"):
            try:
                payload = self.session.current_symbol_surface_payload()
            except Exception:  # noqa: BLE001
                payload = None
        if not isinstance(payload, dict) or not payload:
            self._append_event("No symbol surface is available in the current context.")
            return
        surface_kind = str(payload.get("surface_kind") or "").strip()
        if primary_group:
            if surface_kind == "symbol_lookup":
                handler_name = (
                    "symbol_surface_select_next_match" if delta > 0 else "symbol_surface_select_prev_match"
                )
                slash_command = "/symbol next match" if delta > 0 else "/symbol prev match"
            elif surface_kind == "symbol_actions":
                handler_name = (
                    "symbol_surface_select_next_definition"
                    if delta > 0
                    else "symbol_surface_select_prev_definition"
                )
                slash_command = "/symbol next definition" if delta > 0 else "/symbol prev definition"
            else:
                self._append_event("No symbol primary selection is available in the current context.")
                return
        else:
            if surface_kind in {"symbol_references", "symbol_actions"}:
                handler_name = (
                    "symbol_surface_select_next_reference"
                    if delta > 0
                    else "symbol_surface_select_prev_reference"
                )
                slash_command = "/symbol next reference" if delta > 0 else "/symbol prev reference"
            else:
                self._append_event("No symbol reference selection is available in the current context.")
                return
        if hasattr(self.session, handler_name):
            self._append_chat("You", slash_command)
            output = getattr(self.session, handler_name)()
            if output:
                self.state.append_message("System", output)
            payload = self._current_symbol_surface_metadata()
            self._sync_symbol_focus_state(payload)
            self._render()
            return
        self._execute_navigation_command(slash_command)

    def _execute_task_detail_navigation(self, *, primary: bool) -> bool:
        if not self.state.selected_task_id:
            return False
        if self.state.task_detail_view == "advisor":
            self.state.set_plan_panel_view("advisor" if primary else "execution")
            self._render()
            return True
        if self.state.task_detail_view == "drift":
            self.state.set_plan_panel_view("execution" if primary else "advisor")
            self._render()
            return True
        return False

    def _task_detail_panel_text(self) -> str:
        if self.state.task_detail_view == "advisor":
            return self.state.task_advisor_text
        if self.state.task_detail_view == "drift":
            return self.state.task_drift_text
        return self.state.task_detail_text

    def _execute_navigation_command(self, command: str) -> None:
        handled, output = _handle_repl_command(self.session, command)
        if not handled:
            return
        self._append_chat("You", command)
        if isinstance(output, CommandExecution):
            self.state.start_turn(command)
            self.state.busy = True
            self._append_event(output.progress_message)
            self._render()
            self._run_prompt(output.prompt, execution=output)
            return
        if output:
            self._append_chat("System", output)
        self._render()

    def _sync_lineage_focus_to_active_plan(self) -> None:
        if not hasattr(self.session, "active_plan_lineage_index"):
            return
        try:
            index = int(self.session.active_plan_lineage_index())
        except Exception:  # noqa: BLE001
            return
        self.state.selected_plan_lineage_index = max(0, index)

    def _execute_lineage_command(self, command: str, *, follow_active_lineage: bool = False) -> None:
        handled, output = _handle_repl_command(self.session, command)
        if not handled:
            return
        self._append_chat("You", command)
        if isinstance(output, CommandExecution):
            self._follow_active_lineage_after_turn = follow_active_lineage
            self.state.start_turn(command)
            self.state.busy = True
            self._append_event(output.progress_message)
            self._render()
            self._run_prompt(output.prompt, execution=output)
            return
        if output:
            self._append_chat("System", output)
        if follow_active_lineage:
            self._sync_lineage_focus_to_active_plan()
        self._render()

    def _append_chat(self, role: str, content: str) -> None:
        self.state.append_message(role, content)
        self._render()

    def _append_event(self, content: str) -> None:
        self.state.append_event(content)
        self._render()

    def _finish_turn_output(self, content: str) -> None:
        self.state.finish_turn(content)
        if self._follow_active_lineage_after_turn:
            self._sync_lineage_focus_to_active_plan()
        self._render()

    def _fail_turn_output(self, error_text: str) -> None:
        self.state.fail_turn(error_text)
        self._render()

    def _finish_prompt(self) -> None:
        self.state.busy = False
        self._follow_active_lineage_after_turn = False
        self._render()

    def _resolve_approval(self, result: ApprovalResult) -> None:
        if self.state.pending_approval is None:
            return
        if hasattr(self.session, "resolve_pending_approval") and self._approval_event is None:
            message = self.session.resolve_pending_approval(result)
            if message:
                self.state.change_status = message
                self._append_event(message)
                self._render()
            return
        if self._approval_event is None:
            return
        request = self.state.pending_approval.request
        if result.decision == "allow":
            scope_text = "session" if result.scope == "session" else "once"
            message = f"Approved {request.tool_name} ({scope_text})"
            self.state.change_status = message
            self.state.last_change_preview = request.details or ""
            self.state.last_change_preview_label = "Approved change set"
            self._append_event(message)
        else:
            message = f"Denied {request.tool_name}"
            self.state.change_status = message
            self.state.last_change_preview = request.details or ""
            self.state.last_change_preview_label = "Dismissed change set"
            self.state.recovery_hint = ""
            self._append_event(message)
        self._approval_result = result
        self.state.pending_approval = None
        approval_event = self._approval_event
        self._approval_event = None
        approval_event.set()
        self._render()

    def _cancel_pending_question(self) -> None:
        if self.state.pending_question is None:
            return
        self._submit_question_response(UserQuestionResponse(canceled=True))

    def _resolve_question_from_prompt(self, prompt: str) -> None:
        request = self.state.pending_question.request if self.state.pending_question is not None else None
        if request is None:
            return
        answers: dict[str, str] = {}
        if len(request.questions) == 1:
            answer = self._normalize_question_answer(prompt, request.questions[0].multi_select)
            if answer is None:
                self._append_event("Invalid answer format for pending question.")
                self._render()
                return
            answers[request.questions[0].question] = answer
        else:
            lines = [line.strip() for line in prompt.splitlines() if line.strip()]
            if len(lines) != len(request.questions):
                self._append_event(
                    f"Expected {len(request.questions)} answers, one per line."
                )
                self._render()
                return
            for index, question in enumerate(request.questions):
                answer = self._normalize_question_answer(lines[index], question.multi_select)
                if answer is None:
                    self._append_event(f"Invalid answer format for {question.header or f'question {index + 1}'}.")
                    self._render()
                    return
                answers[question.question] = answer
        self._submit_question_response(UserQuestionResponse(answers=answers))

    def _submit_question_response(self, result: UserQuestionResponse) -> None:
        if self.state.pending_question is None:
            return
        if hasattr(self.session, "resolve_pending_question") and self._question_event is None:
            message = self.session.resolve_pending_question(result)
            if message:
                self._append_event(message)
            self.state.pending_question = None
            self._render()
            return
        if self._question_event is None:
            return
        self._question_result = result
        self.state.pending_question = None
        question_event = self._question_event
        self._question_event = None
        question_event.set()
        self._render()

    def _normalize_question_answer(self, raw: str, multi_select: bool) -> str | None:
        value = raw.strip()
        if not value:
            return None
        if not multi_select:
            return value
        parts = [item.strip() for item in value.split(",") if item.strip()]
        if not parts:
            return None
        return ",".join(parts)

    def _render(self) -> None:
        plan_context = self._selected_plan_panel_file_context()
        self.state.plan_file_context_metadata = (
            plan_context.get("payload")
            if isinstance(plan_context, dict) and isinstance(plan_context.get("payload"), dict)
            else None
        )
        if (
            self.state.selected_task_id
            and isinstance(self.state.task_detail_file_context_metadata, dict)
            and hasattr(self.session, "remember_task_context_focus")
        ):
            try:
                self.session.remember_task_context_focus(
                    self.state.selected_task_id,
                    file_index=self.state.task_detail_file_context_index,
                    preserve_current_focus=False,
                )
            except Exception:  # noqa: BLE001
                pass
        if isinstance(plan_context, dict):
            source = str(plan_context.get("source") or "").strip()
            task_id = str(plan_context.get("task_id") or "").strip()
            if source == "task" and task_id and hasattr(self.session, "remember_task_context_focus"):
                try:
                    self.session.remember_task_context_focus(
                        task_id,
                        file_index=self.state.plan_file_context_index,
                        preserve_current_focus=False,
                    )
                except Exception:  # noqa: BLE001
                    pass
            elif (
                isinstance(self.state.plan_file_context_metadata, dict)
                and hasattr(self.session, "remember_plan_context_focus_payload")
            ):
                try:
                    self.session.remember_plan_context_focus_payload(
                        self.state.plan_file_context_metadata,
                        file_index=self.state.plan_file_context_index,
                        preserve_current_focus=False,
                    )
                except Exception:  # noqa: BLE001
                    pass
        selected_change_file_context_metadata = self._selected_change_file_context_metadata()
        focused_file_context = self._selected_file_context_context()
        focused_file_context_shortcut_label = self._file_context_binding_label()
        self._update_file_context_footer_hints()
        self.chat_scroll.children[0].update(self.state.render_chat())
        self.tool_scroll.children[0].update(self.state.render_tool_logs())
        self.tasks_scroll.children[0].update(
            self.state.render_task_panel(
                self.session.describe_tasks(),
                selected_task_id=self.state.selected_task_id,
                task_execution_metadata=self.state.task_detail_execution_metadata,
                selected_checklist_task_id=self.state.selected_checklist_task_id,
                checklist_tasks=self._checklist_tasks_payload(),
                checklist_filter=self.state.checklist_filter,
                checklist_sort=self.state.checklist_sort,
                checklist_duplicate_metadata=self._checklist_duplicate_guard_metadata(),
            )
        )
        self.task_detail_scroll.children[0].update(
            self.state.render_task_detail_panel(self._task_detail_panel_text())
        )
        self.plan_scroll.children[0].update(
            self.state.render_plan_panel(
                self._plan_panel_text(),
                file_context_metadata=self.state.plan_file_context_metadata,
            )
        )
        self.status_scroll.children[0].update(
            self.state.render_status_panel(
                provider_text=self.session.describe_provider(),
                config_text=self.session.describe_config(),
                status_metadata=(
                    self.session.status_surface_payload()
                    if hasattr(self.session, "status_surface_payload")
                    else None
                ),
                memory_metadata=(
                    self.session.memory_surface_payload()
                    if hasattr(self.session, "memory_surface_payload")
                    else None
                ),
                rewind_preview_metadata=self._selected_rewind_boundary_payload(),
                background_metadata=(
                    self.session.background_surface_payload()
                    if hasattr(self.session, "background_surface_payload")
                    else None
                ),
                background_registry_metadata=(
                    self.session.background_registry_payload()
                    if hasattr(self.session, "background_registry_payload")
                    else None
                ),
                background_handoff_metadata=(
                    self.session.background_handoff_payload()
                    if hasattr(self.session, "background_handoff_payload")
                    else None
                ),
                skills_surface_metadata=(
                    self.session.skills_surface_payload()
                    if hasattr(self.session, "skills_surface_payload")
                    else None
                ),
                plugin_surface_metadata=(
                    self.session.plugin_surface_payload()
                    if hasattr(self.session, "plugin_surface_payload")
                    else None
                ),
                selected_rewind_boundary_index=self.state.selected_rewind_boundary_index,
                selected_background_registry_index=self.state.selected_background_registry_index,
                workspace_surface_metadata=(
                    self.session.workspace_surface_payload()
                    if hasattr(self.session, "workspace_surface_payload")
                    else None
                ),
                file_context_surface_metadata=(
                    self.session.file_context_surface_payload()
                    if hasattr(self.session, "file_context_surface_payload")
                    else None
                ),
                working_set_metadata=(
                    self.session.working_set_payload()
                    if hasattr(self.session, "working_set_payload")
                    else None
                ),
                symbol_surface_metadata=self._current_symbol_surface_metadata(),
                symbol_focus_group=self.state.symbol_focus_group,
                symbol_focus_index=self.state.symbol_focus_index,
                focused_file_context_metadata=(
                    focused_file_context.get("payload")
                    if isinstance(focused_file_context, dict)
                    and isinstance(focused_file_context.get("payload"), dict)
                    else None
                ),
                focused_file_context_source=(
                    str(focused_file_context.get("source") or "")
                    if isinstance(focused_file_context, dict)
                    else None
                ),
                focused_file_context_index=(
                    int(focused_file_context.get("selected_index") or 0)
                    if isinstance(focused_file_context, dict)
                    else 0
                ),
                focused_file_context_shortcut_label=focused_file_context_shortcut_label,
            )
        )
        self.advisor_scroll.children[0].update(
            self.state.render_advisor_panel(self.session.describe_advisor())
        )
        self.changes_scroll.children[0].update(
            self.state.render_changes_panel(
                undo_entries=self.session.recent_change_entries(limit=5),
                redo_entries=self.session.recent_redo_entries(limit=5),
                selected_undo_detail=self.session.selected_change_detail(
                    index=self.state.selected_change_index,
                    file_index=self.state.selected_change_file_index,
                    limit=5,
                    redo=False,
                ),
                selected_redo_detail=self.session.selected_change_detail(
                    index=self.state.selected_redo_index,
                    file_index=self.state.selected_redo_file_index,
                    limit=5,
                    redo=True,
                ),
                selected_file_context_metadata=selected_change_file_context_metadata,
                selected_file_index=(
                    self.state.selected_redo_file_index
                    if self.state.selected_change_stack == "redo"
                    else self.state.selected_change_file_index
                ),
                pending_change_preview=(
                    self.state.pending_approval.request.details
                    if self.state.pending_approval is not None
                    else ""
                ),
                change_status=self.state.change_status,
            )
        )
        self.approval_scroll.children[0].update(
            self.state.render_approval_panel()
        )
        self.events_scroll.children[0].update(self.state.render_events())
        self.chat_scroll.scroll_end(animate=False)
        self.tool_scroll.scroll_end(animate=False)
        self.tasks_scroll.scroll_home(animate=False)
        self.task_detail_scroll.scroll_home(animate=False)

    def _current_symbol_surface_metadata(self) -> dict[str, object] | None:
        if not hasattr(self.session, "current_symbol_surface_payload"):
            return None
        try:
            payload = self.session.current_symbol_surface_payload()
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(payload, dict) or not payload:
            return None
        action_bundle = None
        if hasattr(self.session, "current_symbol_surface_action_bundle"):
            try:
                action_bundle = self.session.current_symbol_surface_action_bundle()
            except Exception:  # noqa: BLE001
                action_bundle = None
        metadata = dict(payload)
        if "selected_navigation_target" not in metadata and isinstance(
            metadata.get("navigation_target"), dict
        ):
            metadata["selected_navigation_target"] = dict(metadata["navigation_target"])
        if isinstance(action_bundle, dict):
            metadata["symbol_primary_action"] = str(action_bundle.get("primary_action") or "none")
            metadata["symbol_secondary_action"] = str(action_bundle.get("secondary_action") or "none")
            metadata["symbol_tertiary_action"] = str(action_bundle.get("tertiary_action") or "/symbol clear")
            metadata["symbol_action_target"] = str(action_bundle.get("target") or "none")
        self._sync_symbol_focus_state(metadata)
        return metadata
        self.plan_scroll.scroll_home(animate=False)
        self.advisor_scroll.scroll_home(animate=False)
        self.status_scroll.scroll_home(animate=False)
        self.changes_scroll.scroll_home(animate=False)
        self.approval_scroll.scroll_home(animate=False)
        self.events_scroll.scroll_end(animate=False)

    def _execution_summary_line(self) -> str:
        if not hasattr(self.session, "execution_contract_payload"):
            return ""
        try:
            payload = self.session.execution_contract_payload()
        except Exception:  # noqa: BLE001
            return ""
        if not isinstance(payload, dict):
            return ""
        execution_mode = str(payload.get("session_execution_mode") or "main").strip() or "main"
        policy = str(payload.get("session_command_policy_name") or "").strip()
        read_only_subagents = bool(payload.get("session_command_policy_require_read_only_subagents", False))
        if execution_mode == "main" and not policy and not read_only_subagents:
            return ""
        bits = [f"execution={execution_mode}"]
        if policy:
            bits.append(f"policy={policy}")
        if read_only_subagents:
            bits.append("read_only_subagents=yes")
        return "execution: " + "  ".join(bits)


def run_tui_app(
    session: Session,
    *,
    session_source: str = "new",
    restored_from: Path | None = None,
    live_background_id: str | None = None,
) -> int:
    app = PyClaudeTui(
        session,
        session_source=session_source,
        restored_from=restored_from,
        live_background_id=live_background_id,
    )
    app.run()
    return 0
