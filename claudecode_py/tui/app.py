from __future__ import annotations

from threading import Event

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, Input, Static
from textual import work

from ..cli import _handle_repl_command
from ..commands import CommandExecution
from ..permissions import ApprovalRequest, ApprovalResult
from ..runtime.events import RuntimeEvent
from ..session import Session
from .state import PendingApproval, TuiState


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
        ("alt+p", "history_prev", "Prev Input"),
        ("alt+n", "history_next", "Next Input"),
        ("ctrl+c", "quit", "Quit"),
    ]

    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session
        self.state = TuiState()
        self.chat_scroll = VerticalScroll(Static(), id="chat-scroll")
        self.tool_scroll = VerticalScroll(Static(), id="tool-scroll")
        self.tasks_scroll = VerticalScroll(Static(), id="tasks-scroll")
        self.plan_scroll = VerticalScroll(Static(), id="plan-scroll")
        self.advisor_scroll = VerticalScroll(Static(), id="advisor-scroll")
        self.status_scroll = VerticalScroll(Static(), id="status-scroll")
        self.changes_scroll = VerticalScroll(Static(), id="changes-scroll")
        self.approval_scroll = VerticalScroll(Static(), id="approval-scroll")
        self.events_scroll = VerticalScroll(Static(), id="events-scroll")
        self.input = Input(placeholder="Ask PyClaudeCode or type a slash command", id="prompt-input")
        self._approval_event: Event | None = None
        self._approval_result: ApprovalResult | None = None
        if hasattr(self.session.permission_manager, "approval_handler"):
            self.session.permission_manager.approval_handler = self._request_approval

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="body"):
            yield self.chat_scroll
            with Vertical(id="side-pane"):
                yield self.tool_scroll
                yield self.tasks_scroll
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
        self.sub_title = str(self.session.config.cwd)
        if hasattr(self.session, "set_live_event_sink"):
            self.session.set_live_event_sink(self._handle_runtime_event)
        if hasattr(self.session, "set_approval_handlers"):
            self.session.set_approval_handlers(
                self._handle_remote_approval_requested,
                self._handle_remote_approval_resolved,
            )
        if self.session.state.messages:
            self.state.append_event(
                f"Restored session {self.session.state.session_id} with {len(self.session.state.messages)} messages."
            )
        if hasattr(self.session, "take_replay_events"):
            for event in self.session.take_replay_events():
                self.state.record_runtime_event(event)
        if getattr(self.session, "pending_approval", None) is not None:
            self._show_remote_pending_approval(self.session.pending_approval)
        self._append_chat("System", f"Connected to {self.session.config.cwd}")
        self._append_event('Type "/help" for commands.')
        self._render()

    def on_unmount(self) -> None:
        if hasattr(self.session, "set_live_event_sink"):
            self.session.set_live_event_sink(None)
        if hasattr(self.session, "set_approval_handlers"):
            self.session.set_approval_handlers(None, None)

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

    def on_input_submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        self.input.value = ""
        if not prompt or self.state.busy:
            return
        if prompt == "/exit":
            self.exit()
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
        self._render()

    def action_select_next_change_file(self) -> None:
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

    def _append_chat(self, role: str, content: str) -> None:
        self.state.append_message(role, content)
        self._render()

    def _append_event(self, content: str) -> None:
        self.state.append_event(content)
        self._render()

    def _finish_turn_output(self, content: str) -> None:
        self.state.finish_turn(content)
        self._render()

    def _fail_turn_output(self, error_text: str) -> None:
        self.state.fail_turn(error_text)
        self._render()

    def _finish_prompt(self) -> None:
        self.state.busy = False
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

    def _render(self) -> None:
        self.chat_scroll.children[0].update(self.state.render_chat())
        self.tool_scroll.children[0].update(self.state.render_tool_logs())
        self.tasks_scroll.children[0].update(self.state.render_task_panel(self.session.describe_tasks()))
        self.plan_scroll.children[0].update(self.state.render_plan_panel(self.session.describe_active_plan()))
        self.status_scroll.children[0].update(
            self.state.render_status_panel(
                provider_text=self.session.describe_provider(),
                config_text=self.session.describe_config(),
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
        self.plan_scroll.scroll_home(animate=False)
        self.advisor_scroll.scroll_home(animate=False)
        self.status_scroll.scroll_home(animate=False)
        self.changes_scroll.scroll_home(animate=False)
        self.approval_scroll.scroll_home(animate=False)
        self.events_scroll.scroll_end(animate=False)


def run_tui_app(session: Session) -> int:
    app = PyClaudeTui(session)
    app.run()
    return 0
