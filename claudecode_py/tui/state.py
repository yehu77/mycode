from __future__ import annotations

from dataclasses import dataclass, field

from ..interactions import UserQuestionRequest
from ..permission_display import (
    PermissionDisplayContext,
    has_permission_display_context,
    render_approval_request_lines,
    render_permission_display_lines,
)
from ..permissions import ApprovalRequest
from ..runtime.events import RuntimeEvent


@dataclass(slots=True)
class ChatTurn:
    number: int
    user_prompt: str
    assistant_text: str = ""
    activity_lines: list[str] = field(default_factory=list)
    error_text: str | None = None


@dataclass(slots=True)
class TurnErrorDetails:
    message: str
    decision_reason: str = ""
    permission_rules: tuple[str, ...] = ()
    command_mode_name: str = ""
    command_mode_allowed_prefixes: tuple[str, ...] = ()
    command_mode_violating_segment: str = ""
    command_mode_violating_segment_index: int | None = None
    command_mode_complex_features: tuple[str, ...] = ()


@dataclass(slots=True)
class ToolLogEntry:
    tool_call_id: str
    tool_name: str
    status: str
    input_summary: str = ""
    duration_ms: int | None = None
    detail: str = ""


@dataclass(slots=True)
class PendingApproval:
    request: ApprovalRequest


@dataclass(slots=True)
class PendingQuestion:
    request: UserQuestionRequest


@dataclass(slots=True)
class PendingChecklistEdit:
    task_id: str
    field: str
    action: str
    prompt: str
    multiline: bool = False
    lines: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TuiState:
    messages: list[str] = field(default_factory=list)
    turns: list[ChatTurn] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    tool_logs: list[ToolLogEntry] = field(default_factory=list)
    pending_approval: PendingApproval | None = None
    pending_question: PendingQuestion | None = None
    pending_checklist_edit: PendingChecklistEdit | None = None
    input_history: list[str] = field(default_factory=list)
    input_history_index: int | None = None
    selected_change_index: int = 0
    selected_redo_index: int = 0
    selected_change_file_index: int = 0
    selected_redo_file_index: int = 0
    selected_change_stack: str = "undo"
    change_status: str = ""
    last_change_preview: str = ""
    last_change_preview_label: str = ""
    recovery_hint: str = ""
    failed_change_context: str = ""
    retry_prompt: str = ""
    mcp_diagnostic_text: str = ""
    plan_panel_view: str = "summary"
    selected_task_id: str | None = None
    selected_checklist_task_id: str | None = None
    task_detail_view: str = "detail"
    task_detail_text: str = ""
    task_detail_execution_metadata: dict[str, object] | None = None
    task_detail_workspace_metadata: dict[str, object] | None = None
    task_detail_checklist_metadata: dict[str, object] | None = None
    task_detail_file_context_metadata: dict[str, object] | None = None
    task_detail_file_context_index: int = 0
    plan_file_context_metadata: dict[str, object] | None = None
    plan_file_context_index: int = 0
    checklist_filter: str = "all"
    checklist_sort: str = "recent_updated"
    symbol_focus_group: str | None = None
    symbol_focus_index: int | None = None
    task_advisor_text: str = ""
    task_drift_text: str = ""
    selected_plan_scout_index: int = 0
    selected_plan_execution_index: int = 0
    selected_plan_lineage_index: int = 0
    selected_plan_timeline_index: int = 0
    selected_plan_timeline_compare_index: int = 0
    selected_plan_replay_index: int = 0
    selected_phase_local_task_index: int = 0
    plan_timeline_filter: str = "all"
    plan_timeline_delta_mode: str = "none"
    plan_timeline_focus_mode: str = "none"
    plan_timeline_compare_mode: str = "none"
    plan_replay_source_mode: str = "timeline-entry"
    plan_replay_phase_filter: str = "none"
    plan_replay_artifact_id: str | None = None
    plan_scout_detail_mode: str = "compact"
    busy: bool = False

    def append_message(self, role: str, content: str) -> None:
        self.messages.append(f"[{role}]\n{content}")

    def append_event(self, content: str) -> None:
        self.events.append(content)

    def clear_chat(self) -> None:
        self.messages.clear()
        self.turns.clear()

    def start_turn(self, prompt: str) -> None:
        turn = ChatTurn(number=len(self.turns) + 1, user_prompt=prompt)
        self.turns.append(turn)

    def record_assistant_text(self, text: str) -> None:
        if not self.turns:
            self.append_message("Assistant", text)
            return
        turn = self.turns[-1]
        if not text:
            return
        if turn.assistant_text.endswith(text):
            return
        turn.assistant_text += text

    def finish_turn(self, final_output: str) -> None:
        if not self.turns:
            if final_output:
                self.append_message("Assistant", final_output)
            return
        turn = self.turns[-1]
        if final_output:
            turn.assistant_text = final_output

    def record_turn_activity(self, text: str) -> None:
        if not self.turns:
            self.append_event(text)
            return
        turn = self.turns[-1]
        if turn.activity_lines and turn.activity_lines[-1] == text:
            return
        turn.activity_lines.append(text)

    def fail_turn(self, error_text: str) -> None:
        if not self.turns:
            self.append_message("Error", error_text)
            return
        self.turns[-1].error_text = error_text

    def fail_turn_with_details(self, details: TurnErrorDetails) -> None:
        self.fail_turn(self._render_turn_error(details))

    def _approval_display_context(self, request: ApprovalRequest) -> PermissionDisplayContext:
        return PermissionDisplayContext(
            decision_reason=request.decision_reason,
            permission_rules=request.permission_rules,
            command_mode_name=request.command_mode_name,
            command_mode_source=request.command_mode_source,
            command_mode_allowed_prefixes=request.command_mode_allowed_prefixes,
            command_mode_violating_segment=request.command_mode_violating_segment,
            command_mode_violating_segment_index=request.command_mode_violating_segment_index,
            command_mode_complex_features=request.command_mode_complex_features,
        )

    def _event_display_context(self, event: RuntimeEvent) -> PermissionDisplayContext:
        return PermissionDisplayContext(
            decision_reason=event.decision_reason or "",
            permission_rules=event.permission_rules,
            command_mode_name=event.command_mode_name or "",
            command_mode_allowed_prefixes=event.command_mode_allowed_prefixes,
            command_mode_violating_segment=event.command_mode_violating_segment or "",
            command_mode_violating_segment_index=event.command_mode_violating_segment_index,
            command_mode_complex_features=event.command_mode_complex_features,
        )

    def _turn_error_display_context(self, details: TurnErrorDetails) -> PermissionDisplayContext:
        return PermissionDisplayContext(
            decision_reason=details.decision_reason,
            permission_rules=details.permission_rules,
            command_mode_name=details.command_mode_name,
            command_mode_allowed_prefixes=details.command_mode_allowed_prefixes,
            command_mode_violating_segment=details.command_mode_violating_segment,
            command_mode_violating_segment_index=details.command_mode_violating_segment_index,
            command_mode_complex_features=details.command_mode_complex_features,
        )

    def record_input_history(self, prompt: str) -> None:
        if not prompt:
            return
        if not self.input_history or self.input_history[-1] != prompt:
            self.input_history.append(prompt)
        self.input_history_index = len(self.input_history)

    def history_previous(self, current_value: str = "") -> str:
        if not self.input_history:
            return current_value
        if self.input_history_index is None:
            self.input_history_index = len(self.input_history)
        if self.input_history_index > 0:
            self.input_history_index -= 1
        return self.input_history[self.input_history_index]

    def history_next(self) -> str:
        if not self.input_history:
            return ""
        if self.input_history_index is None:
            self.input_history_index = len(self.input_history)
        if self.input_history_index < len(self.input_history) - 1:
            self.input_history_index += 1
            return self.input_history[self.input_history_index]
        self.input_history_index = len(self.input_history)
        return ""

    def record_runtime_event(self, event: RuntimeEvent) -> None:
        if event.kind == "assistant_text":
            self.record_assistant_text(event.message)
            return
        if event.kind == "assistant_tool_call":
            self.record_turn_activity(f"[assistant->tools] {event.message}")
            return
        if event.kind == "assistant_tool_result_ready":
            self.record_turn_activity(f"[tools->assistant] {event.message}")
            return
        if event.kind == "plan_execution":
            self.record_turn_activity(f"[plan] {event.message}")
            return
        if event.kind == "task_progress":
            self.append_event(f"[task] {event.message}")
            return
        if event.kind in {
            "advisor",
            "advisor_review_started",
            "advisor_review_result",
            "advisor_revision_requested",
            "advisor_error",
        }:
            self.record_turn_activity(f"[advisor] {event.message}")
            return
        if event.kind == "context_compacted":
            self.append_event(f"[context] {event.message}")
            return
        if event.kind == "provider_retry":
            self.append_event(f"[provider:retry] {event.message}")
            return
        if event.kind == "tool_started":
            self._record_tool_started(event)
            return
        if event.kind == "tool_finished":
            self._record_tool_finished(event)
            return
        if event.kind == "tool_failed":
            self._record_tool_failed(event)

    def sync_after_change_applied(self) -> None:
        self.change_status = "Applied changes are now in Undo stack."
        self.selected_change_stack = "undo"
        self.selected_change_index = 0
        self.selected_change_file_index = 0
        self.last_change_preview = ""
        self.last_change_preview_label = ""
        self.recovery_hint = ""
        self.failed_change_context = ""
        self.retry_prompt = ""

    def render_chat(self, limit: int = 20) -> str:
        rendered: list[str] = []
        if self.messages:
            rendered.extend(self.messages)
        for turn in self.turns[-limit:]:
            rendered.append(f"===== Turn {turn.number} =====")
            rendered.append(f"[You]\n{turn.user_prompt}")
            if turn.assistant_text:
                rendered.append(f"[Assistant]\n{turn.assistant_text}")
            if turn.activity_lines:
                rendered.append(f"[Activity]\n" + "\n".join(turn.activity_lines))
            if turn.error_text:
                rendered.append(f"[Error]\n{turn.error_text}")
        return "\n\n".join(rendered) or "No messages yet."

    def render_events(self, limit: int = 20) -> str:
        return "\n".join(self.events[-limit:]) or "No system events yet."

    def render_tool_logs(self, limit: int = 12) -> str:
        if not self.tool_logs:
            return "No tool activity yet."
        lines = ["Recent Tools"]
        for entry in self.tool_logs[-limit:]:
            suffix = f" ({entry.duration_ms}ms)" if entry.duration_ms is not None else ""
            lines.append(f"[{entry.status}] {entry.tool_name}{suffix}")
            if entry.input_summary:
                lines.append(f"input: {entry.input_summary}")
            if entry.detail:
                lines.append(f"detail: {entry.detail}")
        return "\n".join(lines)

    def render_task_panel(
        self,
        tasks_text: str,
        *,
        selected_task_id: str | None = None,
        task_execution_metadata: dict[str, object] | None = None,
        selected_checklist_task_id: str | None = None,
        checklist_tasks: list[dict[str, object]] | None = None,
        checklist_filter: str | None = None,
        checklist_sort: str | None = None,
        checklist_duplicate_metadata: dict[str, object] | None = None,
    ) -> str:
        checklist_payload = checklist_tasks or []
        if (not tasks_text or tasks_text.strip() == "No tasks.") and not checklist_payload:
            return "Tasks\nNo tasks."
        body_text = self._strip_session_checklist_section(tasks_text) if checklist_payload else tasks_text
        if not body_text.strip() and checklist_payload:
            body_text = "No background tasks."
        lines = ["Tasks", body_text]
        active_filter = checklist_filter or self.checklist_filter
        active_sort = checklist_sort or self.checklist_sort
        checklist_section_lines = self._structured_checklist_section_lines(
            checklist_payload,
            selected_checklist_task_id=selected_checklist_task_id,
            checklist_filter=active_filter,
            checklist_sort=active_sort,
        )
        if checklist_section_lines:
            lines.extend(["", *checklist_section_lines])
        checklist_focus_lines = self._selected_checklist_lines(
            tasks_text,
            selected_checklist_task_id=selected_checklist_task_id,
            checklist_tasks=checklist_payload,
            checklist_filter=active_filter,
            checklist_sort=active_sort,
        )
        if checklist_focus_lines:
            lines.extend(["", *checklist_focus_lines])
        checklist_duplicate_lines = self._checklist_duplicate_hint_lines(
            checklist_duplicate_metadata
        )
        if checklist_duplicate_lines:
            lines.extend(["", *checklist_duplicate_lines])
        checklist_hint_lines = self._checklist_task_hint_lines(
            tasks_text,
            checklist_tasks=checklist_payload,
            checklist_filter=active_filter,
            checklist_sort=active_sort,
        )
        if checklist_hint_lines:
            lines.extend(["", *checklist_hint_lines])
        execution_hint_lines = self._execution_task_hint_lines(
            selected_task_id=selected_task_id,
            execution_metadata=task_execution_metadata,
        )
        if execution_hint_lines:
            lines.extend(["", *execution_hint_lines])
        workspace_hint_lines = self._workspace_task_hint_lines(tasks_text)
        if workspace_hint_lines:
            lines.extend(["", *workspace_hint_lines])
        return "\n".join(lines)

    def render_task_detail_panel(self, detail_text: str) -> str:
        header = f"Task Detail [{self.task_detail_view}]"
        if self.selected_task_id:
            header += f" [{self.selected_task_id}]"
        file_focus_header = self._file_context_focus_header(
            self.task_detail_file_context_metadata,
            selected_index=self.task_detail_file_context_index,
        )
        if file_focus_header:
            header += f" [{file_focus_header}]"
        if not detail_text:
            return header + "\nNo task detail selected."
        lines = [header]
        file_context_hint_lines = self._focused_file_detail_lines(
            self.task_detail_file_context_metadata,
            selected_index=self.task_detail_file_context_index,
        )
        if file_context_hint_lines:
            lines.extend(file_context_hint_lines)
            lines.append("")
        execution_hint_lines = self._execution_detail_hint_lines(
            detail_text,
            self.task_detail_execution_metadata if self.task_detail_view == "detail" else None,
        )
        if execution_hint_lines:
            lines.extend(execution_hint_lines)
            lines.append("")
        checklist_hint_lines = self._checklist_detail_hint_lines(
            detail_text,
            self.task_detail_checklist_metadata if self.task_detail_view == "detail" else None,
        )
        if checklist_hint_lines:
            lines.extend(checklist_hint_lines)
            lines.append("")
        workspace_hint_lines = self._workspace_detail_hint_lines(
            detail_text,
            self.task_detail_workspace_metadata if self.task_detail_view == "detail" else None,
        )
        if workspace_hint_lines:
            lines.extend(workspace_hint_lines)
            lines.append("")
        lines.append(detail_text)
        return "\n".join(lines)

    def set_task_detail(
        self,
        *,
        task_id: str | None,
        text: str,
        execution_metadata: dict[str, object] | None = None,
        workspace_metadata: dict[str, object] | None = None,
        checklist_metadata: dict[str, object] | None = None,
        file_context_metadata: dict[str, object] | None = None,
    ) -> None:
        self.selected_task_id = task_id
        self.task_detail_text = text
        self.task_detail_execution_metadata = dict(execution_metadata) if execution_metadata else None
        self.task_detail_workspace_metadata = dict(workspace_metadata) if workspace_metadata else None
        self.task_detail_checklist_metadata = dict(checklist_metadata) if checklist_metadata else None
        self.task_detail_file_context_metadata = (
            dict(file_context_metadata) if file_context_metadata else None
        )
        self.task_detail_file_context_index = 0
        if checklist_metadata and task_id:
            self.selected_checklist_task_id = task_id
        self.task_detail_view = "detail"

    def set_task_detail_view(self, mode: str) -> None:
        if mode in {"detail", "advisor", "drift"}:
            self.task_detail_view = mode

    def set_task_advisor_detail(self, *, task_id: str | None, text: str) -> None:
        self.selected_task_id = task_id
        self.task_advisor_text = text
        self.task_detail_execution_metadata = None
        self.task_detail_workspace_metadata = None
        self.task_detail_checklist_metadata = None
        self.task_detail_view = "advisor"

    def set_task_drift_detail(self, *, task_id: str | None, text: str) -> None:
        self.selected_task_id = task_id
        self.task_drift_text = text
        self.task_detail_execution_metadata = None
        self.task_detail_workspace_metadata = None
        self.task_detail_checklist_metadata = None
        self.task_detail_view = "drift"

    def render_plan_panel(
        self,
        plan_text: str,
        *,
        file_context_metadata: dict[str, object] | None = None,
    ) -> str:
        header = f"Active Plan [{self.plan_panel_view}]"
        file_focus_header = self._file_context_focus_header(
            file_context_metadata,
            selected_index=self.plan_file_context_index,
        )
        if file_focus_header:
            header += f" [{file_focus_header}]"
        if not plan_text or plan_text.strip().startswith("No active planning artifact"):
            return header + "\nNo active planning artifact."
        lines = [header]
        file_context_hint_lines = self._focused_file_detail_lines(
            file_context_metadata,
            selected_index=self.plan_file_context_index,
        )
        if file_context_hint_lines:
            lines.extend(file_context_hint_lines)
            lines.append("")
        lines.append(plan_text)
        return "\n".join(lines)

    def render_advisor_panel(self, advisor_text: str) -> str:
        if not advisor_text or advisor_text.strip().startswith("Advisor: not set"):
            return "Advisor Status\nAdvisor not configured."
        return "Advisor Status\n" + advisor_text

    def set_plan_panel_view(self, mode: str) -> None:
        if mode in {"summary", "scouts", "execution", "lineage", "advisor", "timeline", "replay", "audit"}:
            self.plan_panel_view = mode

    def move_plan_scout_selection(self, delta: int) -> None:
        self.selected_plan_scout_index = max(0, self.selected_plan_scout_index + delta)

    def move_plan_execution_selection(self, delta: int) -> None:
        self.selected_plan_execution_index = max(0, self.selected_plan_execution_index + delta)

    def set_plan_scout_detail_mode(self, mode: str) -> None:
        if mode in {"compact", "full"}:
            self.plan_scout_detail_mode = mode

    def move_plan_lineage_selection(self, delta: int) -> None:
        self.selected_plan_lineage_index = max(0, self.selected_plan_lineage_index + delta)

    def move_plan_timeline_selection(self, delta: int) -> None:
        self.selected_plan_timeline_index = max(0, self.selected_plan_timeline_index + delta)

    def move_plan_timeline_compare_selection(self, delta: int) -> None:
        self.selected_plan_timeline_compare_index = max(0, self.selected_plan_timeline_compare_index + delta)

    def move_phase_local_task_selection(self, delta: int) -> None:
        self.selected_phase_local_task_index = max(0, self.selected_phase_local_task_index + delta)

    def move_plan_replay_selection(self, delta: int) -> None:
        self.selected_plan_replay_index = max(0, self.selected_plan_replay_index + delta)

    def cycle_plan_timeline_filter(self) -> None:
        filters = ["all", "plan", "scout", "execution", "advisor", "drift"]
        try:
            current_index = filters.index(self.plan_timeline_filter)
        except ValueError:
            current_index = 0
        self.plan_timeline_filter = filters[(current_index + 1) % len(filters)]

    def cycle_plan_timeline_delta_mode(self) -> None:
        modes = ["none", "before-drift", "after-drift", "since-derived"]
        try:
            current_index = modes.index(self.plan_timeline_delta_mode)
        except ValueError:
            current_index = 0
        self.plan_timeline_delta_mode = modes[(current_index + 1) % len(modes)]

    def set_plan_replay_slice_context(
        self,
        *,
        phase_filter: str = "none",
        artifact_id: str | None = None,
    ) -> None:
        self.plan_replay_phase_filter = phase_filter or "none"
        self.plan_replay_artifact_id = artifact_id or None

    def cycle_plan_timeline_focus_mode(self) -> None:
        modes = ["none", "scout", "execution"]
        try:
            current_index = modes.index(self.plan_timeline_focus_mode)
        except ValueError:
            current_index = 0
        self.plan_timeline_focus_mode = modes[(current_index + 1) % len(modes)]

    def set_plan_timeline_focus_task(self, task_id: str | None) -> None:
        if task_id:
            self.plan_timeline_focus_mode = f"task:{task_id}"
        else:
            self.plan_timeline_focus_mode = "none"

    def cycle_plan_timeline_compare_mode(self) -> None:
        modes = ["none", "after-drift-vs-all", "execution-vs-scout", "active-vs-previous"]
        try:
            current_index = modes.index(self.plan_timeline_compare_mode)
        except ValueError:
            current_index = 0
        self.plan_timeline_compare_mode = modes[(current_index + 1) % len(modes)]

    def render_status_panel(
        self,
        *,
        provider_text: str,
        config_text: str,
        working_set_metadata: dict[str, object] | None = None,
        symbol_surface_metadata: dict[str, object] | None = None,
        symbol_focus_group: str | None = None,
        symbol_focus_index: int | None = None,
        focused_file_context_metadata: dict[str, object] | None = None,
        focused_file_context_source: str | None = None,
        focused_file_context_index: int = 0,
        focused_file_context_shortcut_label: str | None = None,
    ) -> str:
        config_items = self._parse_config_items(config_text)

        status_lines = [
            "Status",
            f"busy: {'yes' if self.busy else 'no'}",
            f"provider: {config_items.get('provider', '(unknown)')}",
            f"model: {config_items.get('model', '(unknown)')}",
            f"session_id: {config_items.get('session_id', '(unknown)')}",
            f"constraints: {config_items.get('execution_constraints', 'normal')}",
            f"advisor_blocks: {config_items.get('advisor_blocks', '0')}",
            f"plan_executions: {config_items.get('plan_executions', '0')}",
            f"plan_drifts: {config_items.get('plan_drifts', '0')}",
            f"last_plan_drift: {config_items.get('last_plan_drift_summary', 'none')}",
            f"active_plan_kind: {config_items.get('active_plan_kind', 'none')}",
            f"active_plan_goal: {config_items.get('active_plan_goal', 'none')}",
            f"skills: {config_items.get('enabled_skills', '0')}",
            f"mcp_servers: {config_items.get('mcp_servers', '0')}",
            f"mcp_failed: {config_items.get('mcp_failed_servers', '0')}",
            f"undo: {config_items.get('recent_change_sets', '0')}",
            f"redo: {config_items.get('redo_change_sets', '0')}",
        ]
        workspace_lines = self._workspace_status_lines(config_items)
        if workspace_lines:
            status_lines.append("")
            status_lines.extend(workspace_lines)
        symbol_lines = self._symbol_status_lines(
            config_items,
            symbol_surface_metadata=symbol_surface_metadata,
            symbol_focus_group=symbol_focus_group or self.symbol_focus_group,
            symbol_focus_index=symbol_focus_index if symbol_focus_index is not None else self.symbol_focus_index,
        )
        if symbol_lines:
            status_lines.append("")
            status_lines.extend(symbol_lines)
        file_context_lines = self._focused_file_context_status_lines(
            focused_file_context_metadata,
            source=focused_file_context_source,
            selected_index=focused_file_context_index,
            shortcut_label=focused_file_context_shortcut_label,
        )
        if file_context_lines:
            status_lines.append("")
            status_lines.extend(file_context_lines)
        working_set_lines = self._working_set_status_lines(
            working_set_metadata,
            focused_file_context_metadata=focused_file_context_metadata,
            focused_file_context_index=focused_file_context_index,
        )
        if working_set_lines:
            status_lines.append("")
            status_lines.extend(working_set_lines)

        provider_lines = [line.strip() for line in provider_text.splitlines() if line.strip()]
        if provider_lines:
            status_lines.append("")
            status_lines.append("Capabilities")
            status_lines.extend(provider_lines[:4])
        if self.mcp_diagnostic_text:
            status_lines.append("")
            status_lines.append("MCP diagnosis")
            status_lines.extend(self._compact_block(self.mcp_diagnostic_text, max_lines=10))
        return "\n".join(status_lines)

    def render_changes_panel(
        self,
        *,
        undo_entries: list[str] | None = None,
        redo_entries: list[str] | None = None,
        selected_undo_detail: str = "",
        selected_redo_detail: str = "",
        selected_file_context_metadata: dict[str, object] | None = None,
        selected_file_index: int = 0,
        pending_change_preview: str = "",
        change_status: str = "",
        changes_text: str = "",
    ) -> str:
        header = "Changes"
        stack = self.selected_change_stack
        file_focus_header = self._file_context_focus_header(
            selected_file_context_metadata,
            selected_index=selected_file_index,
        )
        if file_focus_header:
            header += f" [{stack} {file_focus_header}]"
        elif stack:
            header += f" [{stack}]"
        lines = [header]
        if change_status:
            lines.append("Status")
            lines.extend(self._compact_block(change_status, max_lines=3))
            lines.append("")
        if pending_change_preview:
            lines.append("Pending change set")
            lines.extend(self._compact_block(pending_change_preview, max_lines=8))
            lines.append("")
        elif self.last_change_preview:
            lines.append(self.last_change_preview_label or "Recent preview")
            lines.extend(self._compact_block(self.last_change_preview, max_lines=8))
            lines.append("")
        if self.recovery_hint:
            lines.append("Recovery")
            lines.extend(self._compact_block(self.recovery_hint, max_lines=5))
            lines.append("")
        if self.failed_change_context:
            lines.append("Failure context")
            lines.extend(self._compact_block(self.failed_change_context, max_lines=6))
            lines.append("")
        if self.retry_prompt:
            lines.append("Retry")
            lines.append("Ctrl+Shift+R retry last failed prompt")
            lines.append("")
        undo_entries = undo_entries or []
        redo_entries = redo_entries or []
        if change_status.startswith("Applied changes") and undo_entries:
            lines.append("Latest applied")
            lines.append(f"-> {undo_entries[0]}")
            if selected_undo_detail:
                lines.extend(self._compact_block(selected_undo_detail, max_lines=6))
            lines.append("")
        if undo_entries or redo_entries:
            undo_header = (
                f"Undo stack [focused] ({len(undo_entries)}):"
                if self.selected_change_stack == "undo"
                else f"Undo stack ({len(undo_entries)}):"
            )
            lines.append(undo_header)
            if undo_entries:
                for index, item in enumerate(undo_entries):
                    marker = (
                        ">"
                        if self.selected_change_stack == "undo" and index == self.selected_change_index
                        else "*"
                        if index == self.selected_change_index
                        else " "
                    )
                    lines.append(f"{marker} {index + 1}. {item}")
            else:
                lines.append("  (empty)")
            if selected_undo_detail and self.selected_change_stack == "undo":
                lines.append("")
                lines.append(f"Selected change [undo {self.selected_change_index + 1}/{len(undo_entries)}]")
                lines.extend(self._compact_block(selected_undo_detail, max_lines=10))
            lines.append("")
            redo_header = (
                f"Redo stack [focused] ({len(redo_entries)}):"
                if self.selected_change_stack == "redo"
                else f"Redo stack ({len(redo_entries)}):"
            )
            lines.append(redo_header)
            if redo_entries:
                for index, item in enumerate(redo_entries):
                    marker = (
                        ">"
                        if self.selected_change_stack == "redo" and index == self.selected_redo_index
                        else "*"
                        if index == self.selected_redo_index
                        else " "
                    )
                    lines.append(f"{marker} {index + 1}. {item}")
            else:
                lines.append("  (empty)")
            if selected_redo_detail and self.selected_change_stack == "redo":
                lines.append("")
                lines.append(f"Selected change [redo {self.selected_redo_index + 1}/{len(redo_entries)}]")
                lines.extend(self._compact_block(selected_redo_detail, max_lines=10))
        elif not changes_text or changes_text.strip() == "No recorded workspace changes.":
            lines.append("No recorded workspace changes.")
        else:
            lines.append(changes_text)
        file_context_hint_lines = self._focused_file_detail_lines(
            selected_file_context_metadata,
            selected_index=selected_file_index,
        )
        if file_context_hint_lines:
            lines.append("")
            lines.extend(file_context_hint_lines)
        lines.append("")
        lines.append("Ctrl+Z undo")
        lines.append("Ctrl+Y redo")
        lines.append("Shift+Left/Right focus stack")
        lines.append("Shift+Up/Down select")
        lines.append("Ctrl+Left/Right file")
        lines.append("Ctrl+Shift+Z undo selected")
        lines.append("Ctrl+Shift+Y redo selected")
        lines.append("F9 open focused primary target")
        lines.append("F10 open focused diff target")
        return "\n".join(lines)

    def _compact_block(self, text: str, *, max_lines: int) -> list[str]:
        lines = text.splitlines()
        if len(lines) <= max_lines:
            return lines
        return [*lines[:max_lines], "..."]

    def _parse_config_items(self, config_text: str) -> dict[str, str]:
        config_items: dict[str, str] = {}
        for line in config_text.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", maxsplit=1)
            config_items[key.strip()] = value.strip()
        return config_items

    def _workspace_status_lines(self, config_items: dict[str, str]) -> list[str]:
        workspace_health = config_items.get("workspace_health", "").strip()
        workspace_mode = config_items.get("workspace_mode", "").strip()
        effective_cwd = config_items.get("effective_cwd", "").strip()
        if not any((workspace_health, workspace_mode, effective_cwd)):
            return []
        lines = [
            "Workspace",
            f"workspace_mode: {workspace_mode or 'main'}",
            f"workspace_health: {workspace_health or 'healthy'}",
            f"workspace_label: {config_items.get('workspace_label', 'none')}",
            f"effective_cwd: {effective_cwd or '(unknown)'}",
            "workspace_effective_cwd_exists: "
            + config_items.get("workspace_effective_cwd_exists", "unknown"),
            "workspace_cleanup_status: "
            + config_items.get("workspace_cleanup_status", "none"),
        ]
        if workspace_health == "unavailable":
            if effective_cwd:
                lines.append(f"workspace_expected_effective_cwd: {effective_cwd}")
            fallback_cwd = config_items.get("workspace_fallback_cwd", "").strip()
            if fallback_cwd and fallback_cwd != "none":
                lines.append(f"workspace_fallback_cwd: {fallback_cwd}")
            unavailable_reason = config_items.get("workspace_unavailable_reason", "").strip()
            if unavailable_reason and unavailable_reason != "none":
                lines.append(f"workspace_unavailable_reason: {unavailable_reason}")
        recommended_actions = config_items.get("workspace_recommended_actions", "").strip()
        if recommended_actions:
            lines.append(f"workspace_recommended_actions: {recommended_actions}")
        for key in (
            "selected_workspace_primary_action",
            "selected_workspace_secondary_action",
            "selected_workspace_tertiary_action",
            "selected_workspace_target",
        ):
            value = config_items.get(key, "").strip()
            if value:
                lines.append(f"{key}: {value}")
        return lines

    def _symbol_status_lines(
        self,
        config_items: dict[str, str],
        *,
        symbol_surface_metadata: dict[str, object] | None = None,
        symbol_focus_group: str | None = None,
        symbol_focus_index: int | None = None,
    ) -> list[str]:
        def _symbol_target_summary(target: dict[str, object] | None) -> str:
            if not isinstance(target, dict):
                return "none"
            path = str(target.get("path") or "").strip()
            action = str(target.get("action") or "open_file").strip()
            line = int(target.get("line") or 1)
            label = str(target.get("label") or "").strip()
            summary = f"{action} {path}:{line}" if path else action
            if label:
                summary = f"{summary} ({label})"
            return summary

        def _candidate_lines(
            *,
            title: str,
            items: list[dict[str, object]],
            selected_index: object,
            focus_group_name: str,
        ) -> list[str]:
            if not items:
                return []
            try:
                selected = int(selected_index)
            except (TypeError, ValueError):
                selected = -1
            rendered = [title + ":"]
            for index, item in enumerate(items):
                marker = "-"
                if focus_group_name == (symbol_focus_group or "") and index == symbol_focus_index:
                    marker = ">"
                elif index == selected:
                    marker = "*"
                rendered.append(f"  {marker} {index + 1}. {_symbol_target_summary(item)}")
            return rendered

        if symbol_surface_metadata:
            surface_kind = str(symbol_surface_metadata.get("surface_kind") or "").strip()
            if not surface_kind or surface_kind == "none":
                return []
            lines = [
                "Symbol",
                f"symbol_surface_kind: {surface_kind}",
                "symbol_selected_symbol: "
                + str(symbol_surface_metadata.get("selected_symbol") or "none"),
                "symbol_match_count: "
                + str(int(symbol_surface_metadata.get("match_count") or 0)),
                "symbol_definition_count: "
                + str(int(symbol_surface_metadata.get("definition_count") or 0)),
                "symbol_reference_count: "
                + str(int(symbol_surface_metadata.get("reference_count") or 0)),
            ]
            match_count = int(symbol_surface_metadata.get("match_count") or 0)
            selected_match_index = symbol_surface_metadata.get("selected_match_index")
            if selected_match_index is not None and match_count > 0:
                lines.append(f"symbol_selected_match_index: {int(selected_match_index) + 1}/{match_count}")
            definition_count = int(symbol_surface_metadata.get("definition_count") or 0)
            selected_definition_index = symbol_surface_metadata.get("selected_definition_index")
            if selected_definition_index is not None and definition_count > 0:
                lines.append(
                    f"symbol_selected_definition_index: {int(selected_definition_index) + 1}/{definition_count}"
                )
            reference_count = int(symbol_surface_metadata.get("reference_count") or 0)
            selected_reference_index = symbol_surface_metadata.get("selected_reference_index")
            if selected_reference_index is not None and reference_count > 0:
                lines.append(
                    f"symbol_selected_reference_index: {int(selected_reference_index) + 1}/{reference_count}"
                )
            for key in (
                "selected_definition",
                "selected_reference",
                "selected_navigation_target",
            ):
                target = symbol_surface_metadata.get(key)
                if isinstance(target, dict):
                    lines.append(f"symbol_{key}: {_symbol_target_summary(target)}")
            if not isinstance(symbol_surface_metadata.get("selected_navigation_target"), dict) and isinstance(
                symbol_surface_metadata.get("navigation_target"), dict
            ):
                target = symbol_surface_metadata.get("navigation_target")
                lines.append(f"symbol_selected_navigation_target: {_symbol_target_summary(target)}")
            lines.extend(
                _candidate_lines(
                    title="symbol_matches",
                    items=[item for item in symbol_surface_metadata.get("matches", []) if isinstance(item, dict)],
                    selected_index=symbol_surface_metadata.get("selected_match_index"),
                    focus_group_name="matches",
                )
            )
            lines.extend(
                _candidate_lines(
                    title="symbol_definitions",
                    items=[
                        item for item in symbol_surface_metadata.get("definitions", []) if isinstance(item, dict)
                    ],
                    selected_index=symbol_surface_metadata.get("selected_definition_index"),
                    focus_group_name="definitions",
                )
            )
            lines.extend(
                _candidate_lines(
                    title="symbol_references",
                    items=[item for item in symbol_surface_metadata.get("references", []) if isinstance(item, dict)],
                    selected_index=symbol_surface_metadata.get("selected_reference_index"),
                    focus_group_name="references",
                )
            )
            if symbol_focus_group:
                focus_items = {
                    "matches": [
                        item for item in symbol_surface_metadata.get("matches", []) if isinstance(item, dict)
                    ],
                    "definitions": [
                        item for item in symbol_surface_metadata.get("definitions", []) if isinstance(item, dict)
                    ],
                    "references": [
                        item for item in symbol_surface_metadata.get("references", []) if isinstance(item, dict)
                    ],
                }.get(symbol_focus_group, [])
                focus_summary = "none"
                if (
                    focus_items
                    and symbol_focus_index is not None
                    and 0 <= symbol_focus_index < len(focus_items)
                ):
                    focus_summary = _symbol_target_summary(focus_items[symbol_focus_index])
                lines.extend(
                    [
                        "Symbol focus",
                        f"symbol_focus_group: {symbol_focus_group}",
                        f"symbol_focus_index: {(symbol_focus_index + 1) if symbol_focus_index is not None else 'none'}",
                        f"symbol_focus_target: {focus_summary}",
                    ]
                )
            for key in (
                "symbol_primary_action",
                "symbol_secondary_action",
                "symbol_tertiary_action",
                "symbol_action_target",
            ):
                if key in symbol_surface_metadata:
                    lines.append(f"{key}: {symbol_surface_metadata.get(key) or 'none'}")
            return lines
        surface_kind = config_items.get("symbol_surface_kind", "").strip()
        if not surface_kind or surface_kind == "none":
            return []
        lines = [
            "Symbol",
            f"symbol_surface_kind: {surface_kind}",
            f"symbol_selected_symbol: {config_items.get('symbol_selected_symbol', 'none')}",
            f"symbol_match_count: {config_items.get('symbol_match_count', '0')}",
            f"symbol_definition_count: {config_items.get('symbol_definition_count', '0')}",
            f"symbol_reference_count: {config_items.get('symbol_reference_count', '0')}",
            f"symbol_selected_match_index: {config_items.get('symbol_selected_match_index', 'none')}",
            f"symbol_selected_definition_index: {config_items.get('symbol_selected_definition_index', 'none')}",
            f"symbol_selected_reference_index: {config_items.get('symbol_selected_reference_index', 'none')}",
            f"symbol_selected_definition: {config_items.get('symbol_selected_definition', 'none')}",
            f"symbol_selected_reference: {config_items.get('symbol_selected_reference', 'none')}",
            f"symbol_navigation_target: {config_items.get('symbol_navigation_target', 'none')}",
            f"selected_symbol_primary_action: {config_items.get('selected_symbol_primary_action', 'none')}",
            f"selected_symbol_secondary_action: {config_items.get('selected_symbol_secondary_action', 'none')}",
            f"selected_symbol_tertiary_action: {config_items.get('selected_symbol_tertiary_action', '/symbol clear')}",
            f"selected_symbol_target: {config_items.get('selected_symbol_target', 'none')}",
        ]
        return lines

    def _workspace_task_hint_lines(self, tasks_text: str) -> list[str]:
        hints: list[tuple[str, dict[str, str]]] = []
        for raw_line in tasks_text.splitlines():
            line = raw_line.strip()
            if not line or "workspace_action=" not in line:
                continue
            tokens = line.split()
            metadata: dict[str, str] = {}
            for token in tokens:
                if "=" not in token:
                    continue
                key, value = token.split("=", maxsplit=1)
                if key in {
                    "workspace_action",
                    "workspace_target",
                    "health_before",
                    "health_after",
                }:
                    metadata[key] = value
            if metadata:
                hints.append((tokens[0], metadata))
        if not hints:
            return []
        lines = ["Workspace hints"]
        for task_id, metadata in hints:
            lines.append(f"task: {task_id}")
            for key in (
                "workspace_action",
                "workspace_target",
                "health_before",
                "health_after",
            ):
                value = metadata.get(key)
                if value:
                    lines.append(f"{key}: {value}")
            lines.append("")
        if lines[-1] == "":
            lines.pop()
        return lines

    def _checklist_task_hint_lines(
        self,
        tasks_text: str,
        *,
        checklist_tasks: list[dict[str, object]] | None = None,
        checklist_filter: str | None = None,
        checklist_sort: str | None = None,
    ) -> list[str]:
        if checklist_tasks:
            return self._structured_checklist_hint_lines(
                checklist_tasks,
                checklist_filter=checklist_filter or self.checklist_filter,
                checklist_sort=checklist_sort or self.checklist_sort,
            )
        hints: list[tuple[str, dict[str, str]]] = []
        for raw_line in tasks_text.splitlines():
            line = raw_line.strip()
            if not line.startswith("- ") or "selected_checklist_primary_action=" not in line:
                continue
            body = line[2:].strip()
            task_id, _separator, remainder = body.partition("  ")
            metadata: dict[str, str] = {}
            for token in remainder.split("  "):
                if "=" not in token:
                    continue
                key, value = token.split("=", maxsplit=1)
                if key in {
                    "status",
                    "subject",
                    "selected_checklist_primary_action",
                    "selected_checklist_secondary_action",
                    "selected_checklist_edit_subject_action",
                    "selected_checklist_edit_description_action",
                    "selected_checklist_edit_owner_action",
                    "selected_checklist_edit_active_form_action",
                    "selected_checklist_edit_blocks_action",
                    "selected_checklist_edit_blocked_by_action",
                    "selected_checklist_edit_metadata_action",
                    "selected_checklist_target",
                }:
                    metadata[key] = value
            if metadata:
                hints.append((task_id.strip(), metadata))
        if not hints:
            return []
        lines = ["Checklist hints"]
        for task_id, metadata in hints:
            lines.append(f"task: {task_id}")
            for key in (
                "status",
                "subject",
                "selected_checklist_primary_action",
                "selected_checklist_secondary_action",
                "selected_checklist_edit_subject_action",
                "selected_checklist_edit_description_action",
                "selected_checklist_edit_owner_action",
                "selected_checklist_edit_active_form_action",
                "selected_checklist_edit_blocks_action",
                "selected_checklist_edit_blocked_by_action",
                "selected_checklist_edit_metadata_action",
                "selected_checklist_target",
            ):
                value = metadata.get(key)
                if value:
                    lines.append(f"{key}: {value}")
            lines.append("")
        if lines[-1] == "":
            lines.pop()
        return lines

    def _selected_checklist_lines(
        self,
        tasks_text: str,
        *,
        selected_checklist_task_id: str | None,
        checklist_tasks: list[dict[str, object]] | None = None,
        checklist_filter: str | None = None,
        checklist_sort: str | None = None,
    ) -> list[str]:
        if not selected_checklist_task_id:
            return []
        if checklist_tasks:
            return self._structured_selected_checklist_lines(
                checklist_tasks,
                selected_checklist_task_id=selected_checklist_task_id,
                checklist_filter=checklist_filter or self.checklist_filter,
                checklist_sort=checklist_sort or self.checklist_sort,
            )
        checklist_lines: list[str] = []
        in_section = False
        for raw_line in tasks_text.splitlines():
            stripped = raw_line.strip()
            if stripped == "session_checklist:":
                in_section = True
                continue
            if not in_section:
                continue
            if not stripped:
                break
            if not stripped.startswith("- "):
                continue
            body = stripped[2:].strip()
            task_id, _separator, _rest = body.partition("  ")
            prefix = ">" if task_id.strip() == selected_checklist_task_id else " "
            checklist_lines.append(f"{prefix} {body}")
        if not checklist_lines:
            return []
        return ["Checklist focus", *checklist_lines]

    def _strip_session_checklist_section(self, tasks_text: str) -> str:
        lines = tasks_text.splitlines()
        stripped_lines: list[str] = []
        in_checklist = False
        for raw_line in lines:
            stripped = raw_line.strip()
            if stripped == "session_checklist:":
                in_checklist = True
                continue
            if in_checklist:
                if not stripped:
                    in_checklist = False
                continue
            stripped_lines.append(raw_line)
        result = "\n".join(stripped_lines).strip()
        return result or "No background tasks."

    def _structured_checklist_section_lines(
        self,
        checklist_tasks: list[dict[str, object]],
        *,
        selected_checklist_task_id: str | None,
        checklist_filter: str,
        checklist_sort: str,
    ) -> list[str]:
        visible_tasks = self.ordered_visible_checklist_tasks_payload(
            checklist_tasks,
            checklist_filter=checklist_filter,
            checklist_sort=checklist_sort,
        )
        if not visible_tasks:
            return []
        total = len(visible_tasks)
        in_progress = 0
        lines = ["Session Checklist"]
        lines.append(f"filter: {checklist_filter}")
        lines.append(f"sort: {checklist_sort}")
        if not visible_tasks:
            lines.append("no checklist tasks match the current filter")
            return lines
        for item in visible_tasks:
            if str(item.get("status") or "").strip() == "in_progress":
                in_progress += 1
        lines.append(f"total: {total}")
        lines.append(f"in_progress: {in_progress}")
        grouped = self._group_checklist_tasks(visible_tasks)
        for status in ("in_progress", "pending", "completed"):
            items = grouped.get(status) or []
            if not items:
                continue
            lines.append(f"{status} ({len(items)}):")
            for item in items:
                task_id = str(item.get("id") or "").strip()
                subject = str(item.get("subject") or "").strip() or "(no subject)"
                owner = str(item.get("owner") or "").strip() or "none"
                active_form = str(item.get("active_form") or "").strip()
                blocks = item.get("blocks")
                blocked_by = item.get("blocked_by")
                blocks_count = len(blocks) if isinstance(blocks, (list, tuple)) else 0
                blocked_by_count = len(blocked_by) if isinstance(blocked_by, (list, tuple)) else 0
                marker = ">" if task_id == selected_checklist_task_id else " "
                lines.append(f"{marker} {task_id} [{status}] {subject}")
                lines.append(f"  owner: {owner}")
                if active_form:
                    lines.append(f"  active_form: {active_form}")
                lines.append(f"  dependencies: blocks={blocks_count} blocked_by={blocked_by_count}")
        return lines

    def _structured_selected_checklist_lines(
        self,
        checklist_tasks: list[dict[str, object]],
        *,
        selected_checklist_task_id: str,
        checklist_filter: str,
        checklist_sort: str,
    ) -> list[str]:
        visible_tasks = self.ordered_visible_checklist_tasks_payload(
            checklist_tasks,
            checklist_filter=checklist_filter,
            checklist_sort=checklist_sort,
        )
        if not visible_tasks:
            return []
        lines = ["Checklist focus"]
        has_selected = False
        for item in visible_tasks:
            task_id = str(item.get("id") or "").strip()
            subject = str(item.get("subject") or "").strip() or "(no subject)"
            status = str(item.get("status") or "").strip() or "pending"
            marker = ">" if task_id == selected_checklist_task_id else " "
            if marker == ">":
                has_selected = True
            lines.append(f"{marker} {task_id}  status={status}  subject={subject}")
        return lines if has_selected else []

    def _structured_checklist_hint_lines(
        self,
        checklist_tasks: list[dict[str, object]],
        *,
        checklist_filter: str,
        checklist_sort: str,
    ) -> list[str]:
        visible_tasks = self.ordered_visible_checklist_tasks_payload(
            checklist_tasks,
            checklist_filter=checklist_filter,
            checklist_sort=checklist_sort,
        )
        if not visible_tasks:
            return []
        lines = ["Checklist hints"]
        for item in visible_tasks:
            task_id = str(item.get("id") or "").strip()
            if not task_id:
                continue
            lines.append(f"task: {task_id}")
            for key in (
                "status",
                "subject",
                "selected_checklist_primary_action",
                "selected_checklist_secondary_action",
                "selected_checklist_edit_subject_action",
                "selected_checklist_edit_description_action",
                "selected_checklist_edit_owner_action",
                "selected_checklist_edit_active_form_action",
                "selected_checklist_edit_blocks_action",
                "selected_checklist_edit_blocked_by_action",
                "selected_checklist_edit_metadata_action",
                "selected_checklist_target",
            ):
                value = str(item.get(key) or "").strip()
                if value:
                    lines.append(f"{key}: {value}")
            lines.append("")
        if lines[-1] == "":
            lines.pop()
        return lines

    def _checklist_duplicate_hint_lines(
        self,
        checklist_duplicate_metadata: dict[str, object] | None,
    ) -> list[str]:
        if not checklist_duplicate_metadata:
            return []
        message = str(checklist_duplicate_metadata.get("checklist_duplicate_message") or "").strip()
        matched_task_id = str(
            checklist_duplicate_metadata.get("checklist_duplicate_matched_task_id") or ""
        ).strip()
        recommended_action = str(
            checklist_duplicate_metadata.get("checklist_duplicate_recommended_action") or ""
        ).strip()
        if not any((message, matched_task_id, recommended_action)):
            return []
        lines = ["Checklist duplicate guard"]
        if message:
            lines.append(f"message: {message}")
        if matched_task_id:
            lines.append(f"matched_task_id: {matched_task_id}")
        if recommended_action:
            lines.append(f"recommended_action: {recommended_action}")
        return lines

    def _execution_task_hint_lines(
        self,
        *,
        selected_task_id: str | None,
        execution_metadata: dict[str, object] | None,
    ) -> list[str]:
        if not selected_task_id or not execution_metadata:
            return []
        task_surface = str(execution_metadata.get("task_surface") or "").strip()
        if task_surface not in {"child_execution", "background_execution"}:
            return []
        lines = ["Execution focus", f"task: {selected_task_id}"]
        for key in ("task_surface", "execution_mode", "execution_policy", "execution_policy_source"):
            value = execution_metadata.get(key)
            if value not in (None, ""):
                lines.append(f"{key}: {value}")
        allowed_tools = execution_metadata.get("allowed_tools")
        if isinstance(allowed_tools, (list, tuple)) and allowed_tools:
            lines.append("allowed_tools: " + ", ".join(str(item) for item in allowed_tools if str(item).strip()))
        elif allowed_tools not in (None, "", []):
            lines.append(f"allowed_tools: {allowed_tools}")
        allowed_bash_prefixes = execution_metadata.get("allowed_bash_prefixes")
        if isinstance(allowed_bash_prefixes, (list, tuple)) and allowed_bash_prefixes:
            lines.append(
                "allowed_bash_prefixes: "
                + ", ".join(str(item) for item in allowed_bash_prefixes if str(item).strip())
            )
        elif allowed_bash_prefixes not in (None, "", []):
            lines.append(f"allowed_bash_prefixes: {allowed_bash_prefixes}")
        if "read_only_subagents" in execution_metadata:
            lines.append(
                "read_only_subagents: "
                + ("yes" if bool(execution_metadata.get("read_only_subagents")) else "no")
            )
        for key in ("workspace_mode", "workspace_health"):
            value = execution_metadata.get(key)
            if value not in (None, ""):
                lines.append(f"{key}: {value}")
        return lines

    def cycle_checklist_filter(self) -> None:
        filters = ["all", "in_progress", "pending", "completed"]
        try:
            current_index = filters.index(self.checklist_filter)
        except ValueError:
            current_index = 0
        self.checklist_filter = filters[(current_index + 1) % len(filters)]

    def cycle_checklist_sort(self) -> None:
        sorts = ["recent_updated", "blocked", "owner", "subject"]
        try:
            current_index = sorts.index(self.checklist_sort)
        except ValueError:
            current_index = 0
        self.checklist_sort = sorts[(current_index + 1) % len(sorts)]

    def _filter_checklist_tasks(
        self,
        checklist_tasks: list[dict[str, object]],
        *,
        checklist_filter: str,
    ) -> list[dict[str, object]]:
        if checklist_filter == "all":
            return list(checklist_tasks)
        return [
            item
            for item in checklist_tasks
            if str(item.get("status") or "").strip() == checklist_filter
        ]

    def _group_checklist_tasks(
        self,
        checklist_tasks: list[dict[str, object]],
    ) -> dict[str, list[dict[str, object]]]:
        grouped: dict[str, list[dict[str, object]]] = {
            "in_progress": [],
            "pending": [],
            "completed": [],
        }
        for item in checklist_tasks:
            status = str(item.get("status") or "").strip() or "pending"
            if status not in grouped:
                grouped[status] = []
            grouped[status].append(item)
        return grouped

    def ordered_visible_checklist_tasks_payload(
        self,
        checklist_tasks: list[dict[str, object]],
        *,
        checklist_filter: str | None = None,
        checklist_sort: str | None = None,
    ) -> list[dict[str, object]]:
        active_filter = checklist_filter or self.checklist_filter
        sorted_tasks = self.sort_checklist_tasks_payload(checklist_tasks, checklist_sort=checklist_sort)
        visible_tasks = self._filter_checklist_tasks(sorted_tasks, checklist_filter=active_filter)
        if active_filter != "all":
            return visible_tasks
        grouped = self._group_checklist_tasks(visible_tasks)
        ordered: list[dict[str, object]] = []
        for status in ("in_progress", "pending", "completed"):
            ordered.extend(grouped.get(status, ()))
        extra_statuses = [
            status
            for status in grouped
            if status not in {"in_progress", "pending", "completed"}
        ]
        for status in sorted(extra_statuses):
            ordered.extend(grouped.get(status, ()))
        return ordered

    def sort_checklist_tasks_payload(
        self,
        checklist_tasks: list[dict[str, object]],
        *,
        checklist_sort: str | None = None,
    ) -> list[dict[str, object]]:
        active_sort = checklist_sort or self.checklist_sort
        items = list(checklist_tasks)
        if active_sort == "blocked":
            return sorted(
                items,
                key=lambda item: (
                    0 if self._checklist_blocked_count(item) > 0 else 1,
                    -self._checklist_blocked_count(item),
                    self._checklist_owner_sort_key(item),
                    self._checklist_subject_sort_key(item),
                    self._checklist_updated_sort_key(item),
                ),
            )
        if active_sort == "owner":
            return sorted(
                items,
                key=lambda item: (
                    self._checklist_owner_sort_key(item),
                    self._checklist_subject_sort_key(item),
                    self._checklist_updated_sort_key(item),
                ),
            )
        if active_sort == "subject":
            return sorted(
                items,
                key=lambda item: (
                    self._checklist_subject_sort_key(item),
                    self._checklist_owner_sort_key(item),
                    self._checklist_updated_sort_key(item),
                ),
            )
        return sorted(
            items,
            key=lambda item: (
                str(item.get("updated_at") or item.get("created_at") or "").strip(),
                self._checklist_numeric_id_sort_key(item),
            ),
            reverse=True,
        )

    def _checklist_updated_sort_key(self, item: dict[str, object]) -> tuple[int, str]:
        updated = str(item.get("updated_at") or item.get("created_at") or "").strip()
        return (0, updated) if updated else (1, "")

    def _checklist_numeric_id_sort_key(self, item: dict[str, object]) -> int:
        raw = str(item.get("id") or "").strip()
        if raw.isdigit():
            return int(raw)
        return -1

    def _checklist_owner_sort_key(self, item: dict[str, object]) -> tuple[int, str]:
        owner = str(item.get("owner") or "").strip()
        if owner:
            return (0, owner.casefold())
        return (1, "")

    def _checklist_subject_sort_key(self, item: dict[str, object]) -> str:
        return str(item.get("subject") or "").strip().casefold()

    def _checklist_blocked_count(self, item: dict[str, object]) -> int:
        blocked_by = item.get("blocked_by")
        if isinstance(blocked_by, (list, tuple)):
            return len(blocked_by)
        return 0

    def _workspace_detail_hint_lines(
        self,
        detail_text: str,
        workspace_metadata: dict[str, object] | None = None,
    ) -> list[str]:
        if workspace_metadata:
            return self._render_workspace_detail_metadata_lines(workspace_metadata)
        return self._workspace_detail_hint_lines_from_text(detail_text)

    def _execution_detail_hint_lines(
        self,
        detail_text: str,
        execution_metadata: dict[str, object] | None = None,
    ) -> list[str]:
        if execution_metadata:
            return self._render_execution_detail_metadata_lines(execution_metadata)
        return self._execution_detail_hint_lines_from_text(detail_text)

    def _checklist_detail_hint_lines(
        self,
        detail_text: str,
        checklist_metadata: dict[str, object] | None = None,
    ) -> list[str]:
        if checklist_metadata:
            return self._render_checklist_detail_metadata_lines(checklist_metadata)
        return self._checklist_detail_hint_lines_from_text(detail_text)

    def _file_context_detail_hint_lines(
        self,
        file_context_metadata: dict[str, object] | None = None,
        *,
        selected_index: int = 0,
    ) -> list[str]:
        if file_context_metadata:
            return self._render_file_context_detail_metadata_lines(
                file_context_metadata,
                selected_index=selected_index,
            )
        return []

    def _file_context_items_and_index(
        self,
        file_context_metadata: dict[str, object] | None,
        *,
        selected_index: int = 0,
    ) -> tuple[list[dict[str, object]], int, dict[str, object] | None]:
        if not isinstance(file_context_metadata, dict):
            return [], 0, None
        files = [
            item
            for item in (file_context_metadata.get("file_context_files") or [])
            if isinstance(item, dict)
        ]
        if not files:
            return [], 0, None
        bounded_index = max(0, min(len(files) - 1, selected_index))
        return files, bounded_index, files[bounded_index]

    def _file_context_focus_header(
        self,
        file_context_metadata: dict[str, object] | None,
        *,
        selected_index: int = 0,
    ) -> str:
        files, bounded_index, focused_item = self._file_context_items_and_index(
            file_context_metadata,
            selected_index=selected_index,
        )
        if not files or focused_item is None:
            return ""
        path = str(focused_item.get("path") or "").strip()
        label = f"file {bounded_index + 1}/{len(files)}"
        if path:
            label += f" {path}"
        return label

    def _focused_file_context_status_lines(
        self,
        file_context_metadata: dict[str, object] | None,
        *,
        source: str | None,
        selected_index: int = 0,
        shortcut_label: str | None = None,
    ) -> list[str]:
        files, bounded_index, focused_item = self._file_context_items_and_index(
            file_context_metadata,
            selected_index=selected_index,
        )
        if not files or focused_item is None:
            return []
        lines = ["Focused file context"]
        if source:
            lines.append(f"source: {source}")
        if not shortcut_label and source:
            shortcut_label = {
                "task": "focus task files",
                "plan": "focus plan files",
                "changes": "focus change files",
                "working-set": "focus working set files",
            }.get(source, "focus files")
        if shortcut_label:
            lines.append(f"shortcuts: Ctrl+Left/Right {shortcut_label}")
        scope = str(file_context_metadata.get("file_context_scope") or "").strip() if isinstance(file_context_metadata, dict) else ""
        if scope:
            lines.append(f"scope: {scope}")
        lines.append(f"focused_file: {bounded_index + 1}/{len(files)}")
        path = str(focused_item.get("path") or "").strip()
        if path:
            lines.append(f"focused_file_path: {path}")
        scope_reasons = self._file_context_scope_reasons(focused_item)
        if scope_reasons:
            lines.append("in scope because: " + ", ".join(scope_reasons))
        related_change = str(focused_item.get("change_id") or "").strip()
        if related_change:
            lines.append(f"related change: {related_change}")
        lines.append(f"diff hunks: {self._file_context_diff_hunk_count(focused_item)}")
        lines.append(
            "context-only: "
            + ("yes" if self._file_context_is_context_only(focused_item) else "no")
        )
        primary_target = focused_item.get("target")
        if isinstance(primary_target, dict):
            lines.append(
                "primary target: "
                + self._file_context_target_summary(primary_target)
            )
        secondary_target = self._file_context_secondary_target(focused_item)
        if isinstance(secondary_target, dict):
            lines.append(
                "secondary target: "
                + self._file_context_target_summary(secondary_target)
            )
        lines.extend(
            self._file_context_navigation_legend_lines(
                primary_target if isinstance(primary_target, dict) else None,
                secondary_target if isinstance(secondary_target, dict) else None,
                prefix="",
            )
        )
        return lines

    def _working_set_status_lines(
        self,
        working_set_metadata: dict[str, object] | None,
        *,
        focused_file_context_metadata: dict[str, object] | None,
        focused_file_context_index: int,
    ) -> list[str]:
        if not isinstance(working_set_metadata, dict):
            return []
        files, _, _ = self._file_context_items_and_index(
            working_set_metadata,
            selected_index=0,
        )
        if not files:
            return []
        focused_path = self._focused_file_context_path(
            focused_file_context_metadata,
            selected_index=focused_file_context_index,
        )
        lines = ["Working Set"]
        for index, item in enumerate(files, start=1):
            path = str(item.get("path") or "").strip() or "(unknown)"
            marker = ">" if focused_path and path == focused_path else " "
            lines.append(f"{marker} {index}. {path}")
            reasons = self._file_context_scope_reasons(item)
            if reasons:
                lines.append("  in scope because: " + ", ".join(reasons))
            related_change = str(item.get("change_id") or "").strip()
            if related_change:
                lines.append(f"  related change: {related_change}")
            lines.append(f"  diff hunks: {self._file_context_diff_hunk_count(item)}")
            lines.append(
                "  context-only: "
                + ("yes" if self._file_context_is_context_only(item) else "no")
            )
            primary_target = item.get("target")
            if isinstance(primary_target, dict):
                lines.append(
                    "  primary target: "
                    + self._file_context_target_summary(primary_target)
                )
            secondary_target = self._file_context_secondary_target(item)
            if isinstance(secondary_target, dict):
                lines.append(
                    "  secondary target: "
                    + self._file_context_target_summary(secondary_target)
                )
        return lines

    def _focused_file_context_path(
        self,
        file_context_metadata: dict[str, object] | None,
        *,
        selected_index: int = 0,
    ) -> str:
        _, _, focused_item = self._file_context_items_and_index(
            file_context_metadata,
            selected_index=selected_index,
        )
        if focused_item is None:
            return ""
        return str(focused_item.get("path") or "").strip()

    def _file_context_scope_reasons(self, item: dict[str, object]) -> list[str]:
        return [
            str(reason).strip()
            for reason in (item.get("scope_reasons") or [])
            if str(reason).strip()
        ]

    def _file_context_is_context_only(self, item: dict[str, object]) -> bool:
        if "is_context_only" in item:
            return bool(item.get("is_context_only"))
        return not bool(item.get("change_id")) and self._file_context_diff_hunk_count(item) == 0

    def _execution_detail_hint_lines_from_text(self, detail_text: str) -> list[str]:
        metadata: dict[str, str] = {}
        in_block = False
        for raw_line in detail_text.splitlines():
            line = raw_line.strip()
            if not line:
                if in_block:
                    break
                continue
            if line == "execution_contract:":
                in_block = True
                continue
            if not in_block:
                continue
            if not line.startswith("- "):
                break
            payload = line[2:].strip()
            if ":" not in payload:
                continue
            key, value = payload.split(":", maxsplit=1)
            key = key.strip()
            value = value.strip()
            if key in {
                "task_surface",
                "execution_mode",
                "execution_policy",
                "execution_policy_source",
                "allowed_tools",
                "allowed_bash_prefixes",
                "read_only_subagents",
                "workspace_mode",
                "workspace_health",
            }:
                metadata[key] = value
        if not metadata:
            return []
        lines = ["Execution"]
        for key in (
            "task_surface",
            "execution_mode",
            "execution_policy",
            "execution_policy_source",
            "allowed_tools",
            "allowed_bash_prefixes",
            "read_only_subagents",
            "workspace_mode",
            "workspace_health",
        ):
            value = metadata.get(key)
            if value:
                lines.append(f"{key}: {value}")
        return lines

    def _checklist_detail_hint_lines_from_text(self, detail_text: str) -> list[str]:
        metadata: dict[str, str] = {}
        checklist_blocks: list[str] = []
        checklist_blocked_by: list[str] = []
        checklist_recommended_actions: list[str] = []
        current_list: list[str] | None = None
        for raw_line in detail_text.splitlines():
            line = raw_line.strip()
            if not line:
                current_list = None
                continue
            if line == "checklist_blocks:":
                current_list = checklist_blocks
                continue
            if line == "checklist_blocked_by:":
                current_list = checklist_blocked_by
                continue
            if line == "checklist_recommended_actions:":
                current_list = checklist_recommended_actions
                continue
            if current_list is not None and line.startswith("- "):
                current_list.append(line[2:].strip())
                continue
            current_list = None
            if ":" not in line:
                continue
            key, value = line.split(":", maxsplit=1)
            key = key.strip()
            value = value.strip()
            if key in {
                "checklist_task_id",
                "checklist_task_list_id",
                "checklist_subject",
                "checklist_description",
                "checklist_active_form",
                "checklist_status",
                "checklist_owner",
                "checklist_created_at",
                "checklist_updated_at",
                "checklist_total_tasks",
                "checklist_in_progress_tasks",
                "checklist_duplicate_message",
                "checklist_duplicate_reason",
                "checklist_duplicate_matched_task_id",
                "checklist_duplicate_recommended_action",
                "selected_checklist_primary_action",
                "selected_checklist_secondary_action",
                "selected_checklist_tertiary_action",
                "selected_checklist_edit_subject_action",
                "selected_checklist_edit_description_action",
                "selected_checklist_edit_owner_action",
                "selected_checklist_edit_active_form_action",
                "selected_checklist_edit_blocks_action",
                "selected_checklist_edit_blocked_by_action",
                "selected_checklist_edit_metadata_action",
                "selected_checklist_target",
            }:
                metadata[key] = value
        if not metadata and not checklist_blocks and not checklist_blocked_by and not checklist_recommended_actions:
            return []
        lines = ["Checklist"]
        for key in (
            "checklist_task_id",
            "checklist_task_list_id",
            "checklist_subject",
            "checklist_description",
            "checklist_active_form",
            "checklist_status",
            "checklist_owner",
            "checklist_created_at",
            "checklist_updated_at",
            "checklist_total_tasks",
            "checklist_in_progress_tasks",
            "checklist_duplicate_message",
            "checklist_duplicate_reason",
            "checklist_duplicate_matched_task_id",
            "checklist_duplicate_recommended_action",
            "selected_checklist_primary_action",
            "selected_checklist_secondary_action",
            "selected_checklist_tertiary_action",
            "selected_checklist_edit_subject_action",
            "selected_checklist_edit_description_action",
            "selected_checklist_edit_owner_action",
            "selected_checklist_edit_active_form_action",
            "selected_checklist_edit_blocks_action",
            "selected_checklist_edit_blocked_by_action",
            "selected_checklist_edit_metadata_action",
            "selected_checklist_target",
        ):
            value = metadata.get(key)
            if value:
                lines.append(f"{key}: {value}")
        if checklist_recommended_actions:
            lines.append("checklist_recommended_actions:")
            lines.extend(f"- {item}" for item in checklist_recommended_actions)
        if checklist_blocks:
            lines.append("checklist_blocks:")
            lines.extend(f"- {item}" for item in checklist_blocks)
        if checklist_blocked_by:
            lines.append("checklist_blocked_by:")
            lines.extend(f"- {item}" for item in checklist_blocked_by)
        return lines

    def _render_checklist_detail_metadata_lines(
        self,
        checklist_metadata: dict[str, object],
    ) -> list[str]:
        lines = ["Checklist"]
        for key in (
            "checklist_task_id",
            "checklist_task_list_id",
            "checklist_subject",
            "checklist_description",
            "checklist_active_form",
            "checklist_status",
            "checklist_owner",
            "checklist_created_at",
            "checklist_updated_at",
            "checklist_total_tasks",
            "checklist_in_progress_tasks",
        ):
            value = checklist_metadata.get(key)
            if value not in (None, ""):
                lines.append(f"{key}: {value}")
        checklist_blocks = checklist_metadata.get("checklist_blocks")
        if isinstance(checklist_blocks, (list, tuple)) and checklist_blocks:
            lines.append("checklist_blocks:")
            lines.extend(f"- {item}" for item in checklist_blocks)
        checklist_blocked_by = checklist_metadata.get("checklist_blocked_by")
        if isinstance(checklist_blocked_by, (list, tuple)) and checklist_blocked_by:
            lines.append("checklist_blocked_by:")
            lines.extend(f"- {item}" for item in checklist_blocked_by)
        checklist_metadata_map = checklist_metadata.get("checklist_metadata")
        if isinstance(checklist_metadata_map, dict) and checklist_metadata_map:
            lines.append("checklist_metadata:")
            for key in sorted(checklist_metadata_map):
                lines.append(f"- {key}: {checklist_metadata_map[key]}")
        recommended_actions = checklist_metadata.get("checklist_recommended_actions")
        if isinstance(recommended_actions, (list, tuple)) and recommended_actions:
            lines.append("checklist_recommended_actions:")
            lines.extend(f"- {item}" for item in recommended_actions)
        duplicate_message = checklist_metadata.get("checklist_duplicate_message")
        duplicate_reason = checklist_metadata.get("checklist_duplicate_reason")
        duplicate_matched_task_id = checklist_metadata.get("checklist_duplicate_matched_task_id")
        duplicate_recommended_action = checklist_metadata.get("checklist_duplicate_recommended_action")
        if any(
            value not in (None, "")
            for value in (
                duplicate_message,
                duplicate_reason,
                duplicate_matched_task_id,
                duplicate_recommended_action,
            )
        ):
            lines.append("checklist_duplicate_guard:")
            if duplicate_message not in (None, ""):
                lines.append(f"checklist_duplicate_message: {duplicate_message}")
            if duplicate_reason not in (None, ""):
                lines.append(f"checklist_duplicate_reason: {duplicate_reason}")
            if duplicate_matched_task_id not in (None, ""):
                lines.append(
                    f"checklist_duplicate_matched_task_id: {duplicate_matched_task_id}"
                )
            if duplicate_recommended_action not in (None, ""):
                lines.append(
                    "checklist_duplicate_recommended_action: "
                    f"{duplicate_recommended_action}"
                )
        for key in (
            "selected_checklist_primary_action",
            "selected_checklist_secondary_action",
            "selected_checklist_tertiary_action",
            "selected_checklist_edit_subject_action",
            "selected_checklist_edit_description_action",
            "selected_checklist_edit_owner_action",
            "selected_checklist_edit_active_form_action",
            "selected_checklist_edit_blocks_action",
            "selected_checklist_edit_blocked_by_action",
            "selected_checklist_edit_metadata_action",
            "selected_checklist_target",
        ):
            value = checklist_metadata.get(key)
            if value not in (None, ""):
                lines.append(f"{key}: {value}")
        return lines if len(lines) > 1 else []

    def _render_execution_detail_metadata_lines(
        self,
        execution_metadata: dict[str, object],
    ) -> list[str]:
        lines = ["Execution"]
        for key in (
            "task_surface",
            "execution_mode",
            "execution_policy",
            "execution_policy_source",
        ):
            value = execution_metadata.get(key)
            if value not in (None, ""):
                lines.append(f"{key}: {value}")
        allowed_tools = execution_metadata.get("allowed_tools")
        if isinstance(allowed_tools, (list, tuple)) and allowed_tools:
            lines.append("allowed_tools: " + ", ".join(str(item) for item in allowed_tools if str(item).strip()))
        elif allowed_tools not in (None, "", []):
            lines.append(f"allowed_tools: {allowed_tools}")
        allowed_bash_prefixes = execution_metadata.get("allowed_bash_prefixes")
        if isinstance(allowed_bash_prefixes, (list, tuple)) and allowed_bash_prefixes:
            lines.append(
                "allowed_bash_prefixes: "
                + ", ".join(str(item) for item in allowed_bash_prefixes if str(item).strip())
            )
        elif allowed_bash_prefixes not in (None, "", []):
            lines.append(f"allowed_bash_prefixes: {allowed_bash_prefixes}")
        if "read_only_subagents" in execution_metadata:
            lines.append(
                "read_only_subagents: "
                + ("yes" if bool(execution_metadata.get("read_only_subagents")) else "no")
            )
        for key in ("workspace_mode", "workspace_health"):
            value = execution_metadata.get(key)
            if value not in (None, ""):
                lines.append(f"{key}: {value}")
        return lines if len(lines) > 1 else []

    def _file_context_secondary_target(
        self,
        target_source: dict[str, object],
    ) -> dict[str, object] | None:
        diff_targets = target_source.get("diff_targets")
        if diff_targets in (None, ""):
            diff_targets = target_source.get("file_context_primary_diff_targets")
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

    def _file_context_target_summary(self, target: dict[str, object] | None) -> str:
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

    def _render_file_context_detail_metadata_lines(
        self,
        file_context_metadata: dict[str, object],
        *,
        selected_index: int = 0,
    ) -> list[str]:
        file_count = int(file_context_metadata.get("file_context_file_count") or 0)
        files = file_context_metadata.get("file_context_files")
        if file_count <= 0 and not (isinstance(files, list) and files):
            return []
        file_items = [item for item in (files or []) if isinstance(item, dict)]
        focused_item = None
        if file_items:
            bounded_index = max(0, min(len(file_items) - 1, selected_index))
            focused_item = file_items[bounded_index]
        else:
            bounded_index = 0
        lines = ["File context"]
        scope = str(file_context_metadata.get("file_context_scope") or "").strip()
        if scope:
            lines.append(f"scope: {scope}")
        lines.append(f"file_count: {file_count}")
        sources = file_context_metadata.get("file_context_sources")
        if isinstance(sources, (list, tuple)):
            rendered_sources = [str(item) for item in sources if str(item).strip()]
            if rendered_sources:
                lines.append("sources: " + ", ".join(rendered_sources))
        primary_path = str(file_context_metadata.get("file_context_primary_path") or "").strip()
        primary_target = file_context_metadata.get("file_context_primary_target")
        secondary_target = self._file_context_secondary_target(file_context_metadata)
        if focused_item is not None:
            lines.append(f"focused_file_index: {bounded_index + 1}/{len(file_items)}")
            focused_path = str(focused_item.get("path") or "").strip()
            if focused_path:
                lines.append(f"focused_file: {focused_path}")
                lines.append(f"focused_path: {focused_path}")
            focused_source = str(focused_item.get("source") or "").strip()
            if focused_source:
                lines.append(f"focused_source: {focused_source}")
            scope_reasons = self._file_context_scope_reasons(focused_item)
            if scope_reasons:
                lines.append("in_scope_because: " + ", ".join(scope_reasons))
            related_change = str(focused_item.get("change_id") or "").strip()
            if related_change:
                lines.append(f"related_change: {related_change}")
            lines.append(f"diff_hunks: {self._file_context_diff_hunk_count(focused_item)}")
            lines.append(
                "context_only: "
                + ("yes" if self._file_context_is_context_only(focused_item) else "no")
            )
            focused_primary_target = focused_item.get("target")
            if isinstance(focused_primary_target, dict):
                lines.append(
                    "focused_primary_target: "
                    + self._file_context_target_summary(focused_primary_target)
                )
            focused_secondary_target = self._file_context_secondary_target(focused_item)
            if isinstance(focused_secondary_target, dict):
                lines.append(
                    "focused_secondary_target: "
                    + self._file_context_target_summary(focused_secondary_target)
                )
            lines.append(
                "navigation: "
                + self._file_context_navigation_summary(
                    has_primary=isinstance(focused_primary_target, dict),
                    has_secondary=isinstance(focused_secondary_target, dict),
                )
            )
        if primary_path:
            lines.append(f"primary_path: {primary_path}")
        if isinstance(primary_target, dict):
            lines.append("primary_target: " + self._file_context_target_summary(primary_target))
        secondary_target = self._file_context_secondary_target(file_context_metadata)
        if isinstance(secondary_target, dict):
            lines.append(
                "secondary_target: " + self._file_context_target_summary(secondary_target)
            )
        if file_items:
            lines.append("files:")
            for index, item in enumerate(file_items, start=1):
                path = str(item.get("path") or "").strip()
                source = str(item.get("source") or "").strip()
                summary = str(item.get("target_summary") or "").strip()
                marker = ">" if index - 1 == bounded_index else " "
                parts = [f"{marker} {index}. {path or '(unknown)'}"]
                if source:
                    parts.append(f"[{source}]")
                change_id = str(item.get("change_id") or "").strip()
                if change_id:
                    parts.append(f"related_change={change_id}")
                reasons = self._file_context_scope_reasons(item)
                if reasons:
                    parts.append("in_scope_because=" + ", ".join(reasons))
                if summary:
                    parts.append(f"target={summary}")
                diff_count = self._file_context_diff_hunk_count(item)
                if diff_count > 0:
                    parts.append(f"diff_hunks={diff_count}")
                elif self._file_context_is_context_only(item):
                    parts.append("context_only=yes")
                lines.append(" ".join(parts))
        return lines if len(lines) > 1 else []

    def _focused_file_detail_lines(
        self,
        file_context_metadata: dict[str, object] | None,
        *,
        selected_index: int = 0,
    ) -> list[str]:
        if not isinstance(file_context_metadata, dict):
            return []
        files, bounded_index, focused_item = self._file_context_items_and_index(
            file_context_metadata,
            selected_index=selected_index,
        )
        if not files or focused_item is None:
            return []
        lines = ["Focused file"]
        scope = str(file_context_metadata.get("file_context_scope") or "").strip()
        if scope:
            lines.append(f"scope: {scope}")
        lines.append(f"file_focus: {bounded_index + 1}/{len(files)}")
        focused_path = str(focused_item.get("path") or "").strip()
        if focused_path:
            lines.append(f"focused_file: {focused_path}")
        focused_source = str(focused_item.get("source") or "").strip()
        if focused_source:
            lines.append(f"source: {focused_source}")
        scope_reasons = self._file_context_scope_reasons(focused_item)
        if scope_reasons:
            lines.append("in scope because: " + ", ".join(scope_reasons))
        related_change = str(focused_item.get("change_id") or "").strip()
        if related_change:
            lines.append(f"related change: {related_change}")
        diff_hunks = self._file_context_diff_hunk_count(focused_item)
        lines.append(f"diff hunks: {diff_hunks}")
        lines.append(
            "context-only: "
            + ("yes" if self._file_context_is_context_only(focused_item) else "no")
        )
        focused_primary_target = focused_item.get("target")
        if isinstance(focused_primary_target, dict):
            lines.append(
                "primary target: "
                + self._file_context_target_summary(focused_primary_target)
            )
        focused_secondary_target = self._file_context_secondary_target(focused_item)
        if isinstance(focused_secondary_target, dict):
            lines.append(
                "secondary target: "
                + self._file_context_target_summary(focused_secondary_target)
            )
        lines.extend(
            self._file_context_navigation_legend_lines(
                focused_primary_target if isinstance(focused_primary_target, dict) else None,
                focused_secondary_target if isinstance(focused_secondary_target, dict) else None,
            )
        )
        lines.append("file_inventory:")
        for index, item in enumerate(files, start=1):
            path = str(item.get("path") or "").strip()
            source = str(item.get("source") or "").strip()
            summary = str(item.get("target_summary") or "").strip()
            marker = ">" if index - 1 == bounded_index else " "
            parts = [f"{marker} {index}. {path or '(unknown)'}"]
            if source:
                parts.append(f"[{source}]")
            change_id = str(item.get("change_id") or "").strip()
            if change_id:
                parts.append(f"related_change={change_id}")
            reasons = self._file_context_scope_reasons(item)
            if reasons:
                parts.append("in_scope_because=" + ", ".join(reasons))
            if summary:
                parts.append(f"target={summary}")
            diff_count = self._file_context_diff_hunk_count(item)
            if diff_count > 0:
                parts.append(f"diff_hunks={diff_count}")
            elif self._file_context_is_context_only(item):
                parts.append("context_only=yes")
            lines.append(" ".join(parts))
        return lines

    def _file_context_navigation_summary(self, *, has_primary: bool, has_secondary: bool) -> str:
        if has_primary and has_secondary:
            return "F9 primary target, F10 secondary target"
        if has_primary:
            return "F9 primary target, F10 primary target fallback"
        if has_secondary:
            return "F9 secondary target, F10 secondary target"
        return "No navigation target"

    def _file_context_navigation_legend_lines(
        self,
        primary_target: dict[str, object] | None,
        secondary_target: dict[str, object] | None,
        *,
        prefix: str = "",
    ) -> list[str]:
        has_primary = isinstance(primary_target, dict)
        has_secondary = isinstance(secondary_target, dict)
        lines = [
            f"{prefix}navigation: "
            + self._file_context_navigation_summary(
                has_primary=has_primary,
                has_secondary=has_secondary,
            )
        ]
        if has_primary:
            primary_summary = self._file_context_target_summary(primary_target)
        else:
            primary_summary = "none"
        if has_secondary:
            secondary_summary = self._file_context_target_summary(secondary_target)
        elif has_primary:
            secondary_summary = primary_summary + " (fallback)"
        else:
            secondary_summary = "none"
        lines.append(f"{prefix}navigation_f9: {primary_summary if has_primary else secondary_summary}")
        lines.append(f"{prefix}navigation_f10: {secondary_summary}")
        return lines

    def _file_context_diff_hunk_count(self, target_source: dict[str, object]) -> int:
        explicit = target_source.get("diff_target_count")
        if explicit not in (None, ""):
            try:
                return max(0, int(explicit))
            except (TypeError, ValueError):
                return 0
        diff_targets = target_source.get("diff_targets")
        if isinstance(diff_targets, dict):
            hunks = diff_targets.get("hunks")
            if isinstance(hunks, list):
                return len([item for item in hunks if isinstance(item, dict)])
        if isinstance(diff_targets, list):
            return len([item for item in diff_targets if isinstance(item, dict)])
        return 0

    def move_task_detail_file_context_selection(self, delta: int, *, total: int) -> None:
        if total <= 0:
            self.task_detail_file_context_index = 0
            return
        self.task_detail_file_context_index = max(
            0,
            min(total - 1, self.task_detail_file_context_index + delta),
        )

    def move_plan_file_context_selection(self, delta: int, *, total: int) -> None:
        if total <= 0:
            self.plan_file_context_index = 0
            return
        self.plan_file_context_index = max(
            0,
            min(total - 1, self.plan_file_context_index + delta),
        )

    def _workspace_detail_hint_lines_from_text(self, detail_text: str) -> list[str]:
        metadata: dict[str, str] = {}
        planned_paths: list[str] = []
        applied_paths: list[str] = []
        current_list: list[str] | None = None
        for raw_line in detail_text.splitlines():
            line = raw_line.strip()
            if not line:
                current_list = None
                continue
            if line == "workspace_planned_paths:":
                current_list = planned_paths
                continue
            if line == "workspace_applied_paths:":
                current_list = applied_paths
                continue
            if current_list is not None and line.startswith("- "):
                current_list.append(line[2:].strip())
                continue
            current_list = None
            if ":" not in line:
                continue
            key, value = line.split(":", maxsplit=1)
            key = key.strip()
            value = value.strip()
            if key in {
                "workspace_action",
                "workspace_target",
                "workspace_health_before",
                "workspace_health_after",
                "workspace_health",
                "workspace_recommended_actions",
                "workspace_failure_reason",
            }:
                metadata[key] = value
        if not metadata and not planned_paths and not applied_paths:
            return []
        lines = ["Workspace"]
        for key in (
            "workspace_action",
            "workspace_target",
            "workspace_health_before",
            "workspace_health_after",
            "workspace_health",
            "workspace_recommended_actions",
            "workspace_failure_reason",
        ):
            value = metadata.get(key)
            if value:
                lines.append(f"{key}: {value}")
        if planned_paths:
            lines.append("workspace_planned_paths:")
            lines.extend(f"- {path}" for path in planned_paths)
        if applied_paths:
            lines.append("workspace_applied_paths:")
            lines.extend(f"- {path}" for path in applied_paths)
        return lines

    def _render_workspace_detail_metadata_lines(
        self,
        workspace_metadata: dict[str, object],
    ) -> list[str]:
        lines = ["Workspace"]
        for key in (
            "workspace_action",
            "workspace_target",
            "workspace_health_before",
            "workspace_health_after",
            "workspace_health",
            "workspace_failure_reason",
        ):
            value = workspace_metadata.get(key)
            if value:
                lines.append(f"{key}: {value}")
        recommended_actions = workspace_metadata.get("workspace_recommended_actions")
        if isinstance(recommended_actions, (list, tuple)) and recommended_actions:
            lines.append("workspace_recommended_actions:")
            lines.extend(f"- {action}" for action in recommended_actions)
        elif recommended_actions:
            lines.append(f"workspace_recommended_actions: {recommended_actions}")
        planned_paths = workspace_metadata.get("workspace_planned_paths")
        if isinstance(planned_paths, (list, tuple)) and planned_paths:
            lines.append("workspace_planned_paths:")
            lines.extend(f"- {path}" for path in planned_paths)
        applied_paths = workspace_metadata.get("workspace_applied_paths")
        if isinstance(applied_paths, (list, tuple)) and applied_paths:
            lines.append("workspace_applied_paths:")
            lines.extend(f"- {path}" for path in applied_paths)
        return lines if len(lines) > 1 else []

    def move_change_selection(self, delta: int, *, redo: bool | None = None, total: int) -> None:
        if redo is None:
            redo = self.selected_change_stack == "redo"
        if total <= 0:
            if redo:
                self.selected_redo_index = 0
                self.selected_redo_file_index = 0
            else:
                self.selected_change_index = 0
                self.selected_change_file_index = 0
            return
        if redo:
            next_index = max(0, min(total - 1, self.selected_redo_index + delta))
            if next_index != self.selected_redo_index:
                self.selected_redo_file_index = 0
            self.selected_redo_index = next_index
        else:
            next_index = max(0, min(total - 1, self.selected_change_index + delta))
            if next_index != self.selected_change_index:
                self.selected_change_file_index = 0
            self.selected_change_index = next_index

    def move_change_file_selection(self, delta: int, *, redo: bool | None = None, total: int) -> None:
        if redo is None:
            redo = self.selected_change_stack == "redo"
        if total <= 0:
            if redo:
                self.selected_redo_file_index = 0
            else:
                self.selected_change_file_index = 0
            return
        if redo:
            self.selected_redo_file_index = max(0, min(total - 1, self.selected_redo_file_index + delta))
        else:
            self.selected_change_file_index = max(0, min(total - 1, self.selected_change_file_index + delta))

    def switch_change_stack(self, stack: str) -> None:
        if stack in {"undo", "redo"}:
            self.selected_change_stack = stack

    def render_approval_panel(self) -> str:
        if self.pending_question is not None:
            lines = ["Questions"]
            for index, question in enumerate(self.pending_question.request.questions, start=1):
                lines.append(f"{index}. {question.header or 'Question'}")
                lines.append(question.question)
                for option_index, option in enumerate(question.options, start=1):
                    lines.append(f"  {option_index}) {option.label} - {option.description}")
                if question.multi_select:
                    lines.append("answer: comma-separated indexes or labels")
                else:
                    lines.append("answer: single index or label")
                lines.append("")
            lines.append("Enter answer text in the prompt box.")
            lines.append("Ctrl+N cancel questions")
            return "\n".join(lines)
        if self.pending_approval is None:
            lines = ["Approval", "No pending approval."]
        else:
            request = self.pending_approval.request
            lines = render_approval_request_lines(
                request,
                include_title=True,
                footer_lines=(
                    "Ctrl+O allow once",
                    "Ctrl+S allow session",
                    "Ctrl+N deny",
                ),
            )
        return "\n".join(lines)

    def _record_tool_started(self, event: RuntimeEvent) -> None:
        self.tool_logs.append(
            ToolLogEntry(
                tool_call_id=event.tool_call_id or "",
                tool_name=event.tool_name or "unknown",
                status="RUNNING",
                input_summary=event.message,
            )
        )
        if self._is_change_tool(event.tool_name):
            self.change_status = f"Applying {event.tool_name}..."
            self.last_change_preview_label = "Approved change set"
            self.recovery_hint = ""
            self.failed_change_context = ""
            self.retry_prompt = ""

    def _record_tool_finished(self, event: RuntimeEvent) -> None:
        entry = self._find_tool_entry(event.tool_call_id)
        if entry is None:
            self.tool_logs.append(
                ToolLogEntry(
                    tool_call_id=event.tool_call_id or "",
                    tool_name=event.tool_name or "unknown",
                    status="OK",
                    duration_ms=event.duration_ms,
                )
            )
            return
        entry.status = "OK"
        entry.duration_ms = event.duration_ms
        if event.message != "ok":
            entry.detail = event.message
        if self._is_change_tool(event.tool_name):
            self.sync_after_change_applied()

    def _record_tool_failed(self, event: RuntimeEvent) -> None:
        entry = self._find_tool_entry(event.tool_call_id)
        if entry is None:
            self.tool_logs.append(
                ToolLogEntry(
                    tool_call_id=event.tool_call_id or "",
                    tool_name=event.tool_name or "unknown",
                    status="ERROR",
                    duration_ms=event.duration_ms,
                    detail=self._format_tool_failure_detail(event),
                )
            )
        else:
            entry.status = "ERROR"
            entry.duration_ms = event.duration_ms
            entry.detail = self._format_tool_failure_detail(event)
        if (
            event.command_mode_name
            or event.decision_reason
            or event.permission_rules
            or event.command_mode_complex_features
        ):
            self.fail_turn_with_details(
                TurnErrorDetails(
                    message=event.message,
                    decision_reason=event.decision_reason or "",
                    permission_rules=event.permission_rules,
                    command_mode_name=event.command_mode_name or "",
                    command_mode_allowed_prefixes=event.command_mode_allowed_prefixes,
                    command_mode_violating_segment=event.command_mode_violating_segment or "",
                    command_mode_violating_segment_index=event.command_mode_violating_segment_index,
                    command_mode_complex_features=event.command_mode_complex_features,
                )
            )
        if self._is_change_tool(event.tool_name):
            tool_name = event.tool_name or "change tool"
            self.change_status = f"Failed to apply {tool_name}: {event.message}"
            self.last_change_preview_label = "Failed change set"
            self.recovery_hint = self._extract_recovery_hint(event.message)
            self.failed_change_context = (
                f"tool: {tool_name}\n"
                f"input: {entry.input_summary if entry is not None else '(unknown)'}"
            )
            if self.turns:
                self.retry_prompt = self.turns[-1].user_prompt

    def _find_tool_entry(self, tool_call_id: str | None) -> ToolLogEntry | None:
        if tool_call_id is None:
            return None
        for entry in reversed(self.tool_logs):
            if entry.tool_call_id == tool_call_id:
                return entry
        return None

    def _is_change_tool(self, tool_name: str | None) -> bool:
        return tool_name in {"write_file", "edit_file", "apply_patch"}

    def _extract_recovery_hint(self, message: str) -> str:
        marker = "Next steps:"
        if marker not in message:
            return ""
        _, tail = message.split(marker, maxsplit=1)
        return "Next steps:\n" + tail.strip()

    def _format_tool_failure_detail(self, event: RuntimeEvent) -> str:
        context = self._event_display_context(event)
        if not has_permission_display_context(context):
            return event.message
        lines = [event.message]
        lines.extend(
            render_permission_display_lines(
                context,
                command_mode_header="command_mode:",
                bullet_prefix="- ",
                nested_bullet_prefix="- ",
            )
        )
        return "\n".join(lines)

    def _render_turn_error(self, details: TurnErrorDetails) -> str:
        lines = ["turn_error:", f"- message: {details.message}"]
        lines.extend(
            render_permission_display_lines(
                self._turn_error_display_context(details),
                policy_label="- policy",
                matched_rules_header="- matched_rules:",
                command_mode_header="- command_mode:",
                bullet_prefix="  - ",
                nested_bullet_prefix="  - ",
            )
        )
        return "\n".join(lines)
