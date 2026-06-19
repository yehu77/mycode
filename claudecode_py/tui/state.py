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
class PendingBackgroundFollowup:
    bg_id: str
    mode: str
    prompt: str


@dataclass(slots=True)
class TuiState:
    messages: list[str] = field(default_factory=list)
    turns: list[ChatTurn] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    tool_logs: list[ToolLogEntry] = field(default_factory=list)
    pending_approval: PendingApproval | None = None
    pending_question: PendingQuestion | None = None
    pending_checklist_edit: PendingChecklistEdit | None = None
    pending_background_followup: PendingBackgroundFollowup | None = None
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
    selected_background_registry_index: int = 0
    selected_background_registry_bg_id: str | None = None
    selected_rewind_boundary_index: int = 0
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
        if event.kind == "assistant_usage":
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
        if event.kind == "tool_batch_started":
            self.append_event(f"[tool:batch:start] {event.message}")
            return
        if event.kind == "tool_batch_finished":
            self.append_event(f"[tool:batch:done] {event.message}")
            return
        if event.kind == "tool_waiting_for_approval":
            self._record_tool_waiting(event)
            return
        if event.kind == "tool_started":
            self._record_tool_started(event)
            return
        if event.kind == "tool_finished":
            self._record_tool_finished(event)
            return
        if event.kind == "tool_failed":
            self._record_tool_failed(event)
            return
        if event.kind == "tool_result_summarized":
            self.record_turn_activity(f"[tool:summary] {event.message}")
            return
        if event.kind == "tool_result_replacement_applied":
            self.append_event(f"[replacement] {event.message}")
            return
        if event.kind == "tool_result_replacement_reapplied":
            self.append_event(f"[replacement] {event.message}")
            return
        if event.kind == "prompt_cache_hints_applied":
            self.append_event(f"[cache] {event.message}")
            return
        if event.kind == "prompt_cache_hints_fallback":
            self.append_event(f"[cache:fallback] {event.message}")
            return
        if event.kind == "prompt_prefix_planner_applied":
            self.append_event(f"[planner] {event.message}")
            return
        if event.kind == "prompt_prefix_planner_downgraded":
            self.append_event(f"[planner:downgraded] {event.message}")
            return
        if event.kind == "budget_pressure":
            self.append_event(f"[budget] {event.message}")
            return
        if event.kind == "compact_recovery_started":
            self.append_event(f"[recovery:start] {event.message}")
            return
        if event.kind == "compact_recovery_finished":
            self.append_event(f"[recovery:done] {event.message}")

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

    def move_background_registry_selection(self, delta: int, *, total: int) -> None:
        if total <= 0:
            self.selected_background_registry_index = 0
            self.selected_background_registry_bg_id = None
            return
        self.selected_background_registry_index = (self.selected_background_registry_index + delta) % total

    def move_rewind_boundary_selection(self, delta: int, *, total: int) -> None:
        if total <= 0:
            self.selected_rewind_boundary_index = 0
            return
        self.selected_rewind_boundary_index = (self.selected_rewind_boundary_index + delta) % total

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
        status_metadata: dict[str, object] | None = None,
        memory_metadata: dict[str, object] | None = None,
        rewind_preview_metadata: dict[str, object] | None = None,
        background_metadata: dict[str, object] | None = None,
        background_registry_metadata: dict[str, object] | None = None,
        background_handoff_metadata: dict[str, object] | None = None,
        plugin_surface_metadata: dict[str, object] | None = None,
        skills_surface_metadata: dict[str, object] | None = None,
        selected_rewind_boundary_index: int = 0,
        selected_background_registry_index: int = 0,
        workspace_surface_metadata: dict[str, object] | None = None,
        file_context_surface_metadata: dict[str, object] | None = None,
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

        status_lines = ["Status"]
        structured_lines = self._structured_status_dashboard_lines(
            status_metadata,
            config_items=config_items,
            memory_metadata=memory_metadata,
            rewind_preview_metadata=rewind_preview_metadata,
            background_metadata=background_metadata,
            background_registry_metadata=background_registry_metadata,
            background_handoff_metadata=background_handoff_metadata,
            plugin_surface_metadata=plugin_surface_metadata,
            skills_surface_metadata=skills_surface_metadata,
            selected_rewind_boundary_index=selected_rewind_boundary_index,
            selected_background_registry_index=selected_background_registry_index,
            workspace_surface_metadata=workspace_surface_metadata,
            file_context_surface_metadata=file_context_surface_metadata,
            working_set_metadata=working_set_metadata,
            focused_file_context_metadata=focused_file_context_metadata,
            focused_file_context_source=focused_file_context_source,
            focused_file_context_index=focused_file_context_index,
            focused_file_context_shortcut_label=focused_file_context_shortcut_label,
        )
        if structured_lines:
            status_lines.append("")
            status_lines.extend(structured_lines)
        else:
            status_lines.extend(
                [
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
            )
            workspace_lines = self._workspace_status_lines(config_items)
            if workspace_lines:
                status_lines.append("")
                status_lines.extend(workspace_lines)
            memory_lines = self._memory_status_lines(
                memory_metadata,
                rewind_preview_metadata=rewind_preview_metadata,
                selected_rewind_boundary_index=selected_rewind_boundary_index,
            )
            if memory_lines:
                status_lines.append("")
                status_lines.extend(memory_lines)
            background_lines = self._background_status_lines(background_metadata)
            if background_lines:
                status_lines.append("")
                status_lines.extend(background_lines)
            background_registry_lines = self._background_registry_status_lines(
                background_registry_metadata,
                selected_index=selected_background_registry_index,
                selected_bg_id=self.selected_background_registry_bg_id,
            )
            if background_registry_lines:
                status_lines.append("")
                status_lines.extend(background_registry_lines)
            background_handoff_lines = self._background_handoff_status_lines(background_handoff_metadata)
            if background_handoff_lines:
                status_lines.append("")
                status_lines.extend(background_handoff_lines)
        symbol_lines = self._symbol_status_lines(
            config_items,
            symbol_surface_metadata=symbol_surface_metadata,
            symbol_focus_group=symbol_focus_group or self.symbol_focus_group,
            symbol_focus_index=symbol_focus_index if symbol_focus_index is not None else self.symbol_focus_index,
        )
        if symbol_lines:
            status_lines.append("")
            status_lines.extend(symbol_lines)
        if not structured_lines:
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

    def _structured_status_dashboard_lines(
        self,
        status_metadata: dict[str, object] | None,
        *,
        config_items: dict[str, str],
        memory_metadata: dict[str, object] | None,
        rewind_preview_metadata: dict[str, object] | None,
        background_metadata: dict[str, object] | None,
        background_registry_metadata: dict[str, object] | None,
        background_handoff_metadata: dict[str, object] | None,
        plugin_surface_metadata: dict[str, object] | None,
        skills_surface_metadata: dict[str, object] | None,
        selected_rewind_boundary_index: int,
        selected_background_registry_index: int,
        workspace_surface_metadata: dict[str, object] | None,
        file_context_surface_metadata: dict[str, object] | None,
        working_set_metadata: dict[str, object] | None,
        focused_file_context_metadata: dict[str, object] | None,
        focused_file_context_source: str | None,
        focused_file_context_index: int,
        focused_file_context_shortcut_label: str | None,
    ) -> list[str]:
        if not isinstance(status_metadata, dict) or not status_metadata:
            return []

        lines: list[str] = []

        self._append_dashboard_section(
            lines,
            "Session Identity",
            [
                f"busy: {'yes' if self.busy else 'no'}",
                f"session_id: {status_metadata.get('status_session_id') or config_items.get('session_id', '(unknown)')}",
                f"workspace: {status_metadata.get('status_workspace_summary') or 'none'}",
            ],
        )
        self._append_dashboard_section(
            lines,
            "Model and Provider",
            [
                f"provider: {status_metadata.get('status_provider') or config_items.get('provider', '(unknown)')}",
                f"model: {status_metadata.get('status_model') or config_items.get('model', '(unknown)')}",
                f"advisor_model: {status_metadata.get('status_advisor_model') or 'none'}",
                f"advisor_mode: {status_metadata.get('status_advisor_mode') or 'none'}",
                f"session_mode: {status_metadata.get('status_mode') or 'main'}",
                f"context_usage: {status_metadata.get('status_context_usage') or 'unknown'}",
            ],
        )
        memory_detail_lines = self._memory_status_lines(
            memory_metadata,
            rewind_preview_metadata=rewind_preview_metadata,
            selected_rewind_boundary_index=selected_rewind_boundary_index,
        )
        if memory_detail_lines:
            memory_lines = memory_detail_lines[1:]
        else:
            memory_lines = [
                f"memory compaction: {status_metadata.get('status_memory_compaction') or 'none'}",
                f"latest memory operation: {status_metadata.get('status_memory_last_operation') or 'none'}",
                f"latest memory summary: {status_metadata.get('status_memory_summary') or 'none'}",
            ]
        self._append_dashboard_section(lines, "Memory Lifecycle", memory_lines)

        notification_lines = [
            f"background_notifications: {status_metadata.get('status_background_notification_count') or 0}",
            f"latest_background_handoff: {status_metadata.get('status_background_latest_handoff') or 'none'}",
            f"background_summary: {status_metadata.get('status_background_summary') or 'none'}",
        ]
        handoff_detail_lines = self._background_handoff_status_lines(background_handoff_metadata)
        if handoff_detail_lines:
            notification_lines.extend(handoff_detail_lines[1:])
        self._append_dashboard_section(lines, "Background Notifications", notification_lines)

        resolved_workspace_surface = (
            workspace_surface_metadata if isinstance(workspace_surface_metadata, dict) else None
        )
        resolved_file_context_surface = (
            file_context_surface_metadata if isinstance(file_context_surface_metadata, dict) else None
        )
        resolved_working_set_metadata = working_set_metadata
        resolved_focused_file_context_metadata = focused_file_context_metadata
        resolved_focused_file_context_source = focused_file_context_source
        resolved_focused_file_context_index = focused_file_context_index
        resolved_focused_file_context_shortcut_label = focused_file_context_shortcut_label
        explicit_context_payload: dict[str, object] | None = None
        file_action_groups: dict[str, object] | None = None
        if resolved_file_context_surface is not None:
            working_set_payload = resolved_file_context_surface.get("working_set")
            if isinstance(working_set_payload, dict) and working_set_payload:
                resolved_working_set_metadata = working_set_payload
            focused_file_payload = resolved_file_context_surface.get("focused_file")
            if isinstance(focused_file_payload, dict):
                if isinstance(resolved_working_set_metadata, dict) and resolved_working_set_metadata:
                    resolved_focused_file_context_metadata = resolved_working_set_metadata
                source = str(focused_file_payload.get("source") or "").strip()
                if source:
                    resolved_focused_file_context_source = source
                try:
                    resolved_focused_file_context_index = max(
                        0, int(focused_file_payload.get("index") or 0)
                    )
                except (TypeError, ValueError):
                    resolved_focused_file_context_index = 0
            explicit_context = resolved_file_context_surface.get("explicit_context")
            if isinstance(explicit_context, dict):
                explicit_context_payload = explicit_context
            action_groups = resolved_file_context_surface.get("file_action_groups")
            if isinstance(action_groups, dict):
                file_action_groups = action_groups

        workspace_lines = [
            f"workspace_mode: {status_metadata.get('status_workspace_mode') or config_items.get('workspace_mode', 'main')}",
            f"workspace_health: {status_metadata.get('status_workspace_health') or config_items.get('workspace_health', 'healthy')}",
            f"workspace_anomaly: {status_metadata.get('status_workspace_anomaly') or 'none'}",
            f"workspace_recovery: {status_metadata.get('status_workspace_recovery') or 'none'}",
            f"working_set_summary: {status_metadata.get('status_working_set_summary') or 'none'}",
            f"focused_file: {status_metadata.get('status_focused_file_summary') or 'none'}",
            f"explicit_context_entries: {status_metadata.get('status_explicit_context_entry_count') or 0}",
            f"unresolved_explicit_context_entries: {status_metadata.get('status_unresolved_explicit_context_entry_count') or 0}",
        ]
        if resolved_workspace_surface is not None:
            workspace_lines.extend(
                [
                    f"workspace_label: {resolved_workspace_surface.get('workspace_label') or 'none'}",
                    f"effective_cwd: {resolved_workspace_surface.get('workspace_effective_cwd') or '(unknown)'}",
                    "workspace_effective_cwd_exists: "
                    + ("yes" if resolved_workspace_surface.get("workspace_effective_cwd_exists") else "no"),
                    f"workspace_cleanup_status: {resolved_workspace_surface.get('workspace_cleanup_status') or 'none'}",
                    f"workspace_cleanup_error: {resolved_workspace_surface.get('workspace_cleanup_error') or 'none'}",
                    f"workspace_fallback_cwd: {resolved_workspace_surface.get('workspace_fallback_cwd') or 'none'}",
                ]
            )
        recommended_actions = (
            resolved_workspace_surface.get("workspace_recommended_actions")
            if resolved_workspace_surface is not None
            else status_metadata.get("status_workspace_recommended_actions")
        )
        if isinstance(recommended_actions, (list, tuple)):
            recommended_action_text = ", ".join(
                str(item).strip() for item in recommended_actions if str(item).strip()
            )
            workspace_lines.extend(
                [
                    "Workspace Recovery",
                    f"workspace_recommended_actions: {recommended_action_text or 'none'}",
                ]
            )
        elif recommended_actions:
            workspace_lines.extend(
                [
                    "Workspace Recovery",
                    f"workspace_recommended_actions: {recommended_actions}",
                ]
            )
        if resolved_workspace_surface is not None:
            workspace_action_bundle = resolved_workspace_surface.get("workspace_action_bundle")
            action_value_map = {
                "status_workspace_primary_action": (
                    workspace_action_bundle.get("primary_action")
                    if isinstance(workspace_action_bundle, dict)
                    else None
                ),
                "status_workspace_secondary_action": (
                    workspace_action_bundle.get("secondary_action")
                    if isinstance(workspace_action_bundle, dict)
                    else None
                ),
                "status_workspace_tertiary_action": (
                    workspace_action_bundle.get("tertiary_action")
                    if isinstance(workspace_action_bundle, dict)
                    else None
                ),
            }
            workspace_group_lines = self._action_group_lines(
                resolved_workspace_surface.get("workspace_action_groups")
                if isinstance(resolved_workspace_surface.get("workspace_action_groups"), dict)
                else None,
                ordered_keys=[
                    "inspect_current_workspace",
                    "inspect_workspace_inventory",
                    "workspace_recovery",
                ],
                labels={
                    "inspect_current_workspace": "inspect current workspace",
                    "inspect_workspace_inventory": "inspect workspace inventory",
                    "workspace_recovery": "workspace recovery",
                },
            )
        else:
            action_value_map = {
                "status_workspace_primary_action": status_metadata.get("status_workspace_primary_action"),
                "status_workspace_secondary_action": status_metadata.get("status_workspace_secondary_action"),
                "status_workspace_tertiary_action": status_metadata.get("status_workspace_tertiary_action"),
            }
            workspace_group_lines = []
        for key in (
            "status_workspace_primary_action",
            "status_workspace_secondary_action",
            "status_workspace_tertiary_action",
        ):
            value = str(action_value_map.get(key) or "").strip()
            if value:
                workspace_lines.append(f"{key}: {value}")
        if workspace_group_lines:
            workspace_lines.extend(workspace_group_lines)
        workspace_detail_lines = self._workspace_status_lines(config_items)
        if workspace_detail_lines and resolved_workspace_surface is None:
            workspace_lines.extend(workspace_detail_lines[1:])
        file_context_lines = self._focused_file_context_status_lines(
            resolved_focused_file_context_metadata,
            source=resolved_focused_file_context_source,
            selected_index=resolved_focused_file_context_index,
            shortcut_label=resolved_focused_file_context_shortcut_label,
        )
        if file_context_lines:
            workspace_lines.extend(file_context_lines)
        working_set_lines = self._working_set_status_lines(
            resolved_working_set_metadata,
            focused_file_context_metadata=resolved_focused_file_context_metadata,
            focused_file_context_index=resolved_focused_file_context_index,
        )
        if working_set_lines:
            workspace_lines.extend(working_set_lines)
        if isinstance(explicit_context_payload, dict) and explicit_context_payload:
            workspace_lines.extend(
                [
                    "Explicit Context",
                    f"explicit_context_entries: {explicit_context_payload.get('entry_count') or 0}",
                    "unresolved_explicit_context_entries: "
                    + str(explicit_context_payload.get("unresolved_entry_count") or 0),
                    f"explicit_only_files: {explicit_context_payload.get('explicit_only_file_count') or 0}",
                    f"automatic_only_files: {explicit_context_payload.get('automatic_file_count') or 0}",
                    f"overlapping_files: {explicit_context_payload.get('overlapping_file_count') or 0}",
                ]
            )
            compare_lines = explicit_context_payload.get("compare_summary_lines")
            if isinstance(compare_lines, list):
                workspace_lines.extend(
                    str(item).strip()
                    for item in compare_lines
                    if str(item).strip() and str(item).strip() != "explicit context compare:"
                )
        file_action_lines = self._action_group_lines(
            file_action_groups,
            ordered_keys=[
                "inspect_focused_file",
                "inspect_focused_diff",
                "inspect_change",
                "inspect_task",
                "inspect_active_plan",
                "inspect_explicit_context",
                "stay_on_surface",
            ],
            labels={
                "inspect_focused_file": "inspect focused file",
                "inspect_focused_diff": "inspect focused diff",
                "inspect_change": "inspect change",
                "inspect_task": "inspect task",
                "inspect_active_plan": "inspect active plan",
                "inspect_explicit_context": "inspect explicit context",
                "stay_on_surface": "stay on surface",
            },
        )
        if file_action_lines:
            workspace_lines.extend(["File Actions", *file_action_lines])
        self._append_dashboard_section(lines, "Workspace State", workspace_lines)

        active_workflow_lines = [
            f"active plan: {status_metadata.get('status_plan_summary') or 'none'}",
            f"active task: {status_metadata.get('status_active_task_count') or 0}",
            f"task surfaces: {status_metadata.get('status_task_surface_summary') or 'none'}",
            f"runtime progress: {status_metadata.get('status_runtime_progress_summary') or 'none'}",
            "active tool: "
            + (
                (
                    f"{status_metadata.get('status_runtime_active_tool_status')} "
                    f"{status_metadata.get('status_runtime_active_tool_name') or ''}"
                ).strip()
                if str(status_metadata.get("status_runtime_active_tool_status") or "none") != "none"
                else "none"
            ),
            f"active tool input: {status_metadata.get('status_runtime_active_tool_input') or 'none'}",
            "last tool outcome: "
            + (
                " | ".join(
                    part
                    for part in (
                        str(status_metadata.get("status_runtime_last_tool_name") or "").strip(),
                        (
                            str(status_metadata.get("status_runtime_last_tool_status") or "").strip()
                            if str(status_metadata.get("status_runtime_last_tool_status") or "none") != "none"
                            else ""
                        ),
                        str(status_metadata.get("status_runtime_last_tool_summary") or "").strip(),
                    )
                    if part
                )
                or "none"
            ),
            "parallel batch: "
            + (
                f"active size={int(status_metadata.get('status_runtime_parallel_batch_size') or 0)}"
                if bool(status_metadata.get("status_runtime_parallel_batch_active"))
                else "none"
            ),
            f"last tool-result summary: {status_metadata.get('status_runtime_last_result_summary') or 'none'}",
            "tool-result replacement: "
            + str(
                status_metadata.get("status_runtime_tool_result_replacement_summary") or "none"
            ),
            "tool-result artifact: "
            + str(
                status_metadata.get("status_runtime_tool_result_artifact_summary") or "none"
            ),
            "tool-result microcompact: "
            + str(
                status_metadata.get("status_runtime_tool_result_microcompact_summary") or "none"
            ),
            "prompt prefix: "
            + (
                f"segments={int(status_metadata.get('status_prompt_prefix_segment_count') or 0)} "
                f"stable_chars={int(status_metadata.get('status_prompt_prefix_stable_chars') or 0)} "
                f"dynamic_tail_chars={int(status_metadata.get('status_prompt_prefix_dynamic_tail_chars') or 0)}"
            ),
            "plan attachments: "
            + str(status_metadata.get("status_prompt_prefix_attachment_summary") or "none"),
            "plan attachment mode: "
            + str(status_metadata.get("status_prompt_prefix_attachment_mode") or "none"),
            "plan workflow: "
            + (
                f"{status_metadata.get('status_plan_workflow_mode') or 'five_phase'} "
                f"agents={int(status_metadata.get('status_plan_workflow_agent_count') or 1)} "
                f"explore_agents={int(status_metadata.get('status_plan_workflow_explore_agent_count') or 3)}"
            ),
            "plan workflow branch: "
            + str(status_metadata.get("status_plan_workflow_branch_identity") or "none"),
            "plan attachment state: "
            + (
                f"{status_metadata.get('status_plan_instruction_state') or 'inactive'} "
                f"mode={status_metadata.get('status_plan_instruction_attachment_mode') or 'none'} "
                f"reentry={'yes' if bool(status_metadata.get('status_plan_instruction_reentry_active')) else 'no'} "
                f"exit={'yes' if bool(status_metadata.get('status_plan_instruction_exit_active')) else 'no'}"
            ),
            "provider-view assembly: "
            + str(status_metadata.get("status_provider_view_assembly_summary") or "none"),
            "prompt prefix cache mode: "
            + str(status_metadata.get("status_prompt_prefix_cache_mode") or "disabled"),
            "prompt prefix cache supported: "
            + (
                "yes"
                if bool(status_metadata.get("status_prompt_prefix_cache_supported"))
                else "no"
            ),
            "prompt prefix cache provider: "
            + str(status_metadata.get("status_prompt_prefix_cache_provider") or "none"),
            "prompt prefix cache summary: "
            + str(status_metadata.get("status_prompt_prefix_cache_summary") or "none"),
            "prompt prefix cache fallback reason: "
            + str(status_metadata.get("status_prompt_prefix_cache_fallback_reason") or "none"),
            "provider-view planner: "
            + str(status_metadata.get("status_prompt_prefix_planner_mode") or "disabled"),
            "prefix reduction tier: "
            + str(status_metadata.get("status_prompt_prefix_reduction_tier") or "none"),
            "prefix planner reason: "
            + str(status_metadata.get("status_prompt_prefix_planner_reason") or "none"),
            "planner summary: "
            + str(status_metadata.get("status_prompt_prefix_planner_summary") or "none"),
            "costed planner mode: "
            + str(status_metadata.get("status_prompt_prefix_costed_planner_mode") or "disabled"),
            "costed planner reason: "
            + str(status_metadata.get("status_prompt_prefix_costed_planner_reason") or "none"),
            "target tokens to shed: "
            + str(status_metadata.get("status_prompt_prefix_target_tokens_to_shed") or 0),
            "selected candidates: "
            + str(status_metadata.get("status_prompt_prefix_selected_candidate_summary") or "none"),
            "remaining estimated overage: "
            + str(status_metadata.get("status_prompt_prefix_remaining_estimated_overage") or 0),
            "provider-view orchestration: "
            + str(status_metadata.get("status_prompt_prefix_orchestration_mode") or "disabled"),
            "orchestration reason: "
            + str(status_metadata.get("status_prompt_prefix_orchestration_reason") or "none"),
            "orchestration selected candidates: "
            + str(
                status_metadata.get(
                    "status_prompt_prefix_orchestration_selected_candidate_summary"
                )
                or "none"
            ),
            "orchestration remaining overage: "
            + str(
                status_metadata.get(
                    "status_prompt_prefix_orchestration_remaining_estimated_overage"
                )
                or 0
            ),
            "full compaction required: "
            + (
                "yes"
                if bool(
                    status_metadata.get(
                        "status_prompt_prefix_orchestration_requires_full_compaction"
                    )
                )
                else "no"
            ),
            "preserved prefix signature: "
            + str(status_metadata.get("status_prompt_prefix_preserved_signature") or "none"),
            "preserved message groups: "
            + str(
                status_metadata.get("status_prompt_prefix_preserved_message_group_count") or 0
            ),
            "prefix signature: "
            + str(status_metadata.get("status_prompt_prefix_signature") or "none"),
            "prefix preserved: "
            + ("no" if bool(status_metadata.get("status_prompt_prefix_changed")) else "yes"),
            "prefix change reason: "
            + str(status_metadata.get("status_prompt_prefix_change_reason") or "none"),
            "plan attachment change reason: "
            + str(status_metadata.get("status_prompt_prefix_attachment_change_reason") or "none"),
            "budget pressure: "
            + (
                str(status_metadata.get("status_budget_reason") or "none")
                if str(status_metadata.get("status_budget_pressure") or "ok") != "ok"
                else "none"
            ),
            f"compact recovery: {status_metadata.get('status_runtime_compact_recovery_summary') or 'none'}",
            f"advisor blocks: {config_items.get('advisor_blocks', '0')}",
            f"plan executions: {config_items.get('plan_executions', '0')}",
            f"plan drifts: {config_items.get('plan_drifts', '0')}",
            f"last plan drift: {config_items.get('last_plan_drift_summary', 'none')}",
        ]
        background_state_lines = self._background_status_lines(background_metadata)
        if background_state_lines:
            active_workflow_lines.extend(["background_state:"])
            active_workflow_lines.extend(background_state_lines[1:])
        background_registry_lines = self._background_registry_status_lines(
            background_registry_metadata,
            selected_index=selected_background_registry_index,
            selected_bg_id=self.selected_background_registry_bg_id,
        )
        if background_registry_lines:
            active_workflow_lines.extend(["background_sessions:"])
            active_workflow_lines.extend(background_registry_lines[1:])
        self._append_dashboard_section(lines, "Active Workflow", active_workflow_lines)

        self._append_dashboard_section(
            lines,
            "Project-Context Health",
            [
                f"project_context: {status_metadata.get('status_project_context_summary') or 'none'}",
                f"project_context_reload_health: {status_metadata.get('status_project_context_reload_health') or 'latest reload: none'}",
                f"project_context_issue: {status_metadata.get('status_project_context_issue') or 'none'}",
                f"skill_registry: {status_metadata.get('status_skill_registry_summary') or 'none'}",
                f"skill_prompt_composition: {status_metadata.get('status_skill_prompt_summary') or 'none'}",
                f"skill_reload_state: {status_metadata.get('status_skill_reload_state') or 'latest reload: none'}",
                f"manual_skill_overrides: {status_metadata.get('status_skill_manual_overrides') or 'enabled=0 disabled=0'}",
                f"skill_diagnostics: {status_metadata.get('status_skill_diagnostics') or 0}",
                f"plugins_health: {status_metadata.get('status_plugins_health') or 'none'}",
                f"plugin_registry: {status_metadata.get('status_plugin_registry_summary') or 'none'}",
                f"plugin_reload_state: {status_metadata.get('status_plugin_reload_state') or 'latest reload: none'}",
                f"manual_plugin_overrides: {status_metadata.get('status_plugin_manual_overrides') or 'enabled=0 disabled=0'}",
                f"mcp_health: {status_metadata.get('status_mcp_health') or 'none'}",
                f"mcp_issue: {status_metadata.get('status_mcp_issue') or 'none'}",
                f"permission_mode: {status_metadata.get('status_permission_mode') or config_items.get('permission_mode', 'default')}",
                f"permission_summary: {status_metadata.get('status_permission_summary') or 'none'}",
                f"workspace_anomaly: {status_metadata.get('status_workspace_anomaly') or 'none'}",
                f"runtime_health_alert: {status_metadata.get('status_runtime_health_alert') or 'none'}",
            ],
        )
        if isinstance(skills_surface_metadata, dict) and skills_surface_metadata:
            skill_lines = [
                f"skill_registry: {skills_surface_metadata.get('skill_registry_summary') or 'none'}",
                f"skill_prompt_composition: {skills_surface_metadata.get('skill_prompt_composition_summary') or 'none'}",
                "manual_skill_overrides: "
                + (
                    f"enabled={skills_surface_metadata.get('skill_manual_enabled_count') or 0} "
                    f"disabled={skills_surface_metadata.get('skill_manual_disabled_count') or 0}"
                ),
                "skill_sources: "
                + (
                    f"builtin={skills_surface_metadata.get('skill_builtin_count') or 0} "
                    f"project_local={skills_surface_metadata.get('skill_project_local_count') or 0} "
                    f"plugin_contributed={skills_surface_metadata.get('skill_plugin_contributed_count') or 0}"
                ),
                "skill_status: "
                + (
                    f"enabled={skills_surface_metadata.get('skill_enabled_count') or 0} "
                    f"disabled={skills_surface_metadata.get('skill_disabled_count') or 0} "
                    f"inactive={skills_surface_metadata.get('skill_inactive_count') or 0}"
                ),
                "skill_reload_state: "
                + str(
                    (
                        skills_surface_metadata.get("skill_reload_state")
                        if isinstance(skills_surface_metadata.get("skill_reload_state"), dict)
                        else {}
                    ).get("summary")
                    or "latest reload: none"
                ),
                f"skill_diagnostics: {skills_surface_metadata.get('skill_diagnostic_count') or 0}",
                f"selected_skill: {skills_surface_metadata.get('skill_selected_summary') or 'none'}",
            ]
            skill_action_lines = self._action_group_lines(
                skills_surface_metadata.get("skill_action_groups")
                if isinstance(skills_surface_metadata.get("skill_action_groups"), dict)
                else None,
                ordered_keys=[
                    "inspect_skill_registry",
                    "inspect_project_context_skills",
                    "inspect_skill_reload_state",
                    "inspect_selected_skill",
                    "toggle_selected_skill",
                ],
                labels={
                    "inspect_skill_registry": "inspect skill registry",
                    "inspect_project_context_skills": "inspect project-context skills",
                    "inspect_skill_reload_state": "inspect skill reload state",
                    "inspect_selected_skill": "inspect selected skill",
                    "toggle_selected_skill": "toggle selected skill",
                },
            )
            if skill_action_lines:
                skill_lines.extend(["Skill Actions", *skill_action_lines])
            self._append_dashboard_section(lines, "Skill Registry", skill_lines)
        if isinstance(plugin_surface_metadata, dict) and plugin_surface_metadata:
            plugin_lines = [
                f"plugin_registry: {plugin_surface_metadata.get('plugin_registry_summary') or 'none'}",
                f"plugin_diagnostics: {plugin_surface_metadata.get('plugin_diagnostic_count') or 0}",
                "manual_plugin_overrides: "
                + (
                    f"enabled={plugin_surface_metadata.get('plugin_manual_enabled_count') or 0} "
                    f"disabled={plugin_surface_metadata.get('plugin_manual_disabled_count') or 0}"
                ),
                "plugin_reload_state: "
                + str(
                    (
                        plugin_surface_metadata.get("plugin_reload_state")
                        if isinstance(plugin_surface_metadata.get("plugin_reload_state"), dict)
                        else {}
                    ).get("summary")
                    or "latest reload: none"
                ),
                f"selected_plugin: {plugin_surface_metadata.get('plugin_selected_summary') or 'none'}",
            ]
            plugin_action_lines = self._action_group_lines(
                plugin_surface_metadata.get("plugin_action_groups")
                if isinstance(plugin_surface_metadata.get("plugin_action_groups"), dict)
                else None,
                ordered_keys=[
                    "inspect_plugin_registry",
                    "inspect_project_context_plugins",
                    "inspect_plugin_reload_state",
                    "inspect_selected_plugin",
                    "toggle_selected_plugin",
                ],
                labels={
                    "inspect_plugin_registry": "inspect plugin registry",
                    "inspect_project_context_plugins": "inspect project-context plugins",
                    "inspect_plugin_reload_state": "inspect plugin reload state",
                    "inspect_selected_plugin": "inspect selected plugin",
                    "toggle_selected_plugin": "toggle selected plugin",
                },
            )
            if plugin_action_lines:
                plugin_lines.extend(["Plugin Actions", *plugin_action_lines])
            self._append_dashboard_section(lines, "Plugin Registry", plugin_lines)
        next_action_lines = self._status_action_lines(
            status_metadata.get("status_action_groups"),
            resume=False,
        )
        if not next_action_lines:
            next_actions = status_metadata.get("status_next_actions")
            next_action_lines = [
                f"- {item}"
                for item in next_actions
                if isinstance(item, str) and item.strip()
            ]
        self._append_dashboard_section(lines, "Next Actions", next_action_lines)
        return lines

    def _status_action_label(self, key: str) -> str:
        labels = {
            "go_to_focused_file": "inspect focused file",
            "inspect_changes": "inspect changes",
            "inspect_task": "inspect tasks",
            "inspect_active_plan": "inspect active plan",
            "inspect_history_rewind": "inspect history/rewind",
            "inspect_background_handoff": "inspect background handoff",
            "inspect_project_context_health": "inspect project-context health",
            "inspect_runtime_health": "inspect runtime health",
            "inspect_sessions": "inspect sessions",
            "resume_repl": "resume repl",
            "resume_tui": "resume tui",
        }
        return labels.get(key, key)

    def _status_action_lines(
        self,
        action_groups: dict[str, object] | None,
        *,
        resume: bool = False,
    ) -> list[str]:
        if not isinstance(action_groups, dict) or not action_groups:
            return []
        ordered_keys = [
            "go_to_focused_file",
            "inspect_changes",
            "inspect_task",
            "inspect_active_plan",
            "inspect_history_rewind",
            "inspect_background_handoff",
            "inspect_project_context_health",
            "inspect_runtime_health",
        ]
        if resume:
            ordered_keys.extend(["inspect_sessions", "resume_repl", "resume_tui"])
        lines: list[str] = []
        for key in ordered_keys:
            raw = action_groups.get(key)
            if not isinstance(raw, list):
                continue
            commands = [str(item).strip() for item in raw if str(item).strip()]
            if not commands:
                continue
            lines.append(f"- {self._status_action_label(key)}: {' | '.join(commands)}")
        return lines

    def _action_group_lines(
        self,
        action_groups: dict[str, object] | None,
        *,
        ordered_keys: list[str],
        labels: dict[str, str],
    ) -> list[str]:
        if not isinstance(action_groups, dict) or not action_groups:
            return []
        lines: list[str] = []
        for key in ordered_keys:
            raw = action_groups.get(key)
            if not isinstance(raw, list):
                continue
            commands = [str(item).strip() for item in raw if str(item).strip()]
            if not commands:
                continue
            lines.append(f"- {labels.get(key, key)}: {' | '.join(commands)}")
        return lines

    def _append_dashboard_section(
        self,
        target: list[str],
        title: str,
        items: list[str],
    ) -> None:
        filtered = [item for item in items if str(item).strip()]
        if target:
            target.append("")
        target.append(title)
        target.extend(filtered or ["none"])

    def _memory_status_lines(
        self,
        memory_metadata: dict[str, object] | None,
        *,
        rewind_preview_metadata: dict[str, object] | None = None,
        selected_rewind_boundary_index: int = 0,
    ) -> list[str]:
        if not isinstance(memory_metadata, dict):
            return []
        history_boundary_count = int(
            memory_metadata.get("memory_boundary_count", memory_metadata.get("history_boundary_count") or 0)
            or 0
        )
        rewindable_count = int(
            memory_metadata.get(
                "memory_rewindable_boundary_count",
                memory_metadata.get("rewindable_history_boundary_count") or 0,
            )
            or 0
        )
        context_summary_chars = int(
            memory_metadata.get(
                "memory_context_summary_chars",
                memory_metadata.get("context_summary_chars") or 0,
            )
            or 0
        )
        last_boundary_kind = str(
            memory_metadata.get(
                "memory_last_boundary_kind",
                memory_metadata.get("last_history_boundary_kind") or "",
            )
            or ""
        ).strip()
        latest_rewindable_kind = str(
            memory_metadata.get(
                "memory_latest_rewindable_boundary_kind",
                memory_metadata.get("latest_rewindable_boundary_kind") or "",
            )
            or ""
        ).strip()
        default_rewind_selector = str(
            memory_metadata.get(
                "memory_default_rewind_selector",
                memory_metadata.get("default_rewind_selector") or "",
            )
            or ""
        ).strip()
        rewind_show_action = str(
            memory_metadata.get(
                "memory_rewind_show_action",
                memory_metadata.get("rewind_show_action") or "",
            )
            or ""
        ).strip()
        rewind_apply_action = str(
            memory_metadata.get(
                "memory_rewind_apply_action",
                memory_metadata.get("rewind_apply_action") or "",
            )
            or ""
        ).strip()
        compaction_state = str(
            memory_metadata.get(
                "memory_compaction_state",
                memory_metadata.get("compaction_state") or "",
            )
            or ""
        ).strip()
        compaction_reason = str(
            memory_metadata.get(
                "memory_compaction_reason",
                memory_metadata.get("compaction_reason") or "",
            )
            or ""
        ).strip()
        budget_state = str(memory_metadata.get("memory_budget_state") or "").strip()
        budget_reason = str(memory_metadata.get("memory_budget_reason") or "").strip()
        last_turn_token_count = memory_metadata.get("memory_last_turn_token_count")
        last_turn_token_source = str(memory_metadata.get("memory_last_turn_token_source") or "").strip()
        provider_usage_seen = bool(memory_metadata.get("memory_provider_usage_seen"))
        budget_pressure = str(memory_metadata.get("memory_budget_pressure") or "").strip()
        compact_lifecycle = str(memory_metadata.get("memory_compact_lifecycle") or "").strip()
        latest_compact_trigger = str(memory_metadata.get("memory_latest_compact_trigger") or "").strip()
        latest_compact_reason = str(memory_metadata.get("memory_latest_compact_reason") or "").strip()
        last_operation = str(memory_metadata.get("memory_last_operation") or "").strip()
        last_operation_messages = str(
            memory_metadata.get("memory_last_operation_messages") or ""
        ).strip()
        last_operation_session_identity = str(
            memory_metadata.get("memory_last_operation_session_identity") or ""
        ).strip()
        last_operation_focus = str(
            memory_metadata.get("memory_last_operation_task_plan_file_focus") or ""
        ).strip()
        if not any(
            (
                history_boundary_count,
                rewindable_count,
                context_summary_chars,
                last_boundary_kind,
                latest_rewindable_kind,
                default_rewind_selector,
                compaction_state,
                last_operation,
            )
        ):
            return []
        lines = ["Memory Lifecycle"]
        lines.append(f"history_boundaries: {history_boundary_count}")
        lines.append(f"rewindable_boundaries: {rewindable_count}")
        lines.append(f"context_summary_chars: {context_summary_chars}")
        if compaction_state:
            lines.append(f"memory compaction: {compaction_state}")
        if compaction_reason:
            lines.append(f"compact reason: {compaction_reason}")
        else:
            lines.append("compact reason: none")
        if budget_state:
            lines.append(f"runtime budget state: {budget_state}")
        if budget_reason:
            lines.append(f"runtime budget reason: {budget_reason}")
        else:
            lines.append("runtime budget reason: none")
        lines.append(
            "last turn token count: "
            + (str(last_turn_token_count) if last_turn_token_count is not None else "none")
        )
        lines.append(f"last turn token source: {last_turn_token_source or 'none'}")
        lines.append(f"provider usage seen: {'yes' if provider_usage_seen else 'no'}")
        lines.append(f"budget pressure: {budget_pressure or 'ok'}")
        lines.append(f"compact lifecycle: {compact_lifecycle or 'none'}")
        lines.append(f"latest compact trigger: {latest_compact_trigger or 'none'}")
        lines.append(f"latest compact reason: {latest_compact_reason or 'none'}")
        if last_operation:
            lines.append(f"latest memory operation: {last_operation}")
        if last_operation_messages:
            lines.append(f"memory_messages: {last_operation_messages}")
        if last_operation_session_identity:
            lines.append(f"memory_session_identity: {last_operation_session_identity}")
        if last_operation_focus:
            lines.append(f"memory_focus_policy: {last_operation_focus}")
        lines.append(f"last boundary: {last_boundary_kind or 'none'}")
        lines.append(f"latest rewindable: {latest_rewindable_kind or 'none'}")
        if default_rewind_selector:
            lines.append(f"default rewind selector: {default_rewind_selector}")
        if rewind_show_action:
            lines.append(f"rewind show: {rewind_show_action}")
        if rewind_apply_action:
            lines.append(f"rewind apply: {rewind_apply_action}")
        if isinstance(rewind_preview_metadata, dict) and rewindable_count > 0:
            selector_index = int(rewind_preview_metadata.get("selector_index") or 0)
            boundary_id = str(rewind_preview_metadata.get("boundary_id") or "").strip()
            boundary_kind = str(rewind_preview_metadata.get("boundary_kind_label") or "").strip()
            boundary_summary = str(rewind_preview_metadata.get("summary") or "").strip()
            restore_effect = str(rewind_preview_metadata.get("restore_effect_summary") or "").strip()
            restore_messages = int(rewind_preview_metadata.get("snapshot_message_count") or 0)
            restore_summary_chars = int(rewind_preview_metadata.get("snapshot_summary_chars") or 0)
            preview_show = str(rewind_preview_metadata.get("show_action") or "").strip()
            preview_apply = str(rewind_preview_metadata.get("apply_action") or "").strip()
            trigger = str(rewind_preview_metadata.get("trigger") or "").strip()
            lines.append(
                "selected_rewind_boundary: "
                f"{selector_index}/{max(rewindable_count, selected_rewind_boundary_index + 1)}"
            )
            if boundary_id:
                lines.append(f"selected_rewind_boundary_id: {boundary_id}")
            if boundary_kind:
                lines.append(f"selected_rewind_boundary_kind: {boundary_kind}")
            if trigger:
                lines.append(f"selected_rewind_boundary_trigger: {trigger}")
            if boundary_summary:
                lines.append(f"selected_rewind_boundary_summary: {boundary_summary}")
            lines.append(f"selected_rewind_restore_messages: {restore_messages}")
            lines.append(f"selected_rewind_restore_context_summary_chars: {restore_summary_chars}")
            if restore_effect:
                lines.append(f"selected_rewind_restore_effect: {restore_effect}")
            if preview_show:
                lines.append(f"selected_rewind_show: {preview_show}")
            if preview_apply:
                lines.append(f"selected_rewind_apply: {preview_apply}")
            lines.append(
                "shortcuts: Ctrl+Alt+Left/Right select rewind boundary, Ctrl+Alt+P preview, Ctrl+Alt+R apply"
            )
        return lines

    def _background_status_lines(self, background_metadata: dict[str, object] | None) -> list[str]:
        if not isinstance(background_metadata, dict):
            return []
        background_session_id = str(background_metadata.get("background_session_id") or "").strip()
        if not background_session_id:
            return []
        lines = ["Background State"]
        lines.append(f"background_session_id: {background_session_id}")
        continuation = str(background_metadata.get("background_continuation_category") or "").strip()
        if continuation:
            lines.append(f"background_continuation: {continuation}")
        workflow_summary = str(background_metadata.get("background_current_workflow_summary") or "").strip()
        if workflow_summary:
            lines.append(f"background_workflow: {workflow_summary}")
        task_surface_summary = str(background_metadata.get("background_task_surface_summary") or "").strip()
        if task_surface_summary:
            lines.append(f"background_task_surfaces: {task_surface_summary}")
        primary_task = background_metadata.get("background_primary_task")
        if isinstance(primary_task, dict):
            task_id = str(primary_task.get("task_id") or "").strip()
            task_surface = str(primary_task.get("surface_kind") or "").strip()
            task_status = str(primary_task.get("status") or "").strip()
            task_description = str(primary_task.get("description") or "").strip()
            summary_bits = [bit for bit in (task_id, task_surface, task_status) if bit]
            if task_description:
                summary_bits.append(task_description)
            if summary_bits:
                lines.append(f"background_primary_task: {' | '.join(summary_bits)}")
        active_plan = str(background_metadata.get("background_active_plan_summary") or "").strip()
        if active_plan:
            lines.append(f"background_active_plan: {active_plan}")
        focused_file = str(background_metadata.get("background_focused_file") or "").strip()
        if focused_file:
            lines.append(f"background_focused_file: {focused_file}")
        recent_activity = str(background_metadata.get("background_recent_activity") or "").strip()
        if recent_activity:
            lines.append(f"background_recent_activity: {recent_activity}")
        token_count = background_metadata.get("background_token_count")
        token_source = str(background_metadata.get("background_token_count_source") or "").strip()
        if token_count not in {None, ""}:
            suffix = f" ({token_source})" if token_source else ""
            lines.append(f"background_token_count: {token_count}{suffix}")
        last_tool_input = str(background_metadata.get("background_last_tool_input") or "").strip()
        if last_tool_input:
            lines.append(f"background_last_tool_input: {last_tool_input}")
        last_tool_summary = str(background_metadata.get("background_last_tool_summary") or "").strip()
        if last_tool_summary:
            lines.append(f"background_last_tool_summary: {last_tool_summary}")
        progress_summary = str(background_metadata.get("background_progress_summary") or "").strip()
        if progress_summary:
            lines.append(f"background_progress: {progress_summary}")
        completion_state = str(background_metadata.get("background_completion_state") or "").strip()
        if completion_state:
            lines.append(f"background_completion: {completion_state}")
        completion_summary = str(background_metadata.get("background_completion_summary") or "").strip()
        if completion_summary:
            lines.append(f"background_completion_summary: {completion_summary}")
        pending_followup_count = int(background_metadata.get("background_pending_followup_count", 0) or 0)
        if pending_followup_count > 0:
            lines.append(f"background_pending_followups: {pending_followup_count}")
        pending_followup_summary = str(
            background_metadata.get("background_pending_followup_summary") or ""
        ).strip()
        if pending_followup_summary:
            lines.append(f"background_pending_followup: {pending_followup_summary}")
        send_followup = str(background_metadata.get("background_send_followup_action") or "").strip()
        if send_followup:
            lines.append(f"background_send_followup: {send_followup}")
        attach_action = str(background_metadata.get("background_attach_action") or "").strip()
        if attach_action:
            lines.append(f"background_attach: {attach_action}")
        logs_action = str(background_metadata.get("background_logs_action") or "").strip()
        if logs_action:
            lines.append(f"background_logs: {logs_action}")
        return lines

    def _background_handoff_status_lines(
        self,
        background_handoff_metadata: dict[str, object] | None,
    ) -> list[str]:
        if not isinstance(background_handoff_metadata, dict):
            return []
        count = int(background_handoff_metadata.get("background_handoff_count", 0) or 0)
        if count <= 0:
            return []
        lines = ["Background Notifications"]
        lines.append(f"background_notifications: {count}")
        selected_bg_id = str(background_handoff_metadata.get("background_handoff_selected_bg_id") or "").strip()
        if selected_bg_id:
            lines.append(f"latest_background_handoff: {selected_bg_id}")
        selected_state = str(
            background_handoff_metadata.get("background_handoff_selected_completion_state") or ""
        ).strip()
        if selected_state:
            lines.append(f"latest_background_state: {selected_state}")
        selected_summary = str(
            background_handoff_metadata.get("background_handoff_selected_completion_summary") or ""
        ).strip()
        if selected_summary:
            lines.append(f"latest_background_summary: {selected_summary}")
        failure_reason = str(
            background_handoff_metadata.get("background_handoff_selected_failure_reason") or ""
        ).strip()
        if failure_reason:
            lines.append(f"latest_background_failure: {failure_reason}")
        transcript_action = str(
            background_handoff_metadata.get("background_handoff_transcript_action") or ""
        ).strip()
        if transcript_action:
            lines.append(f"background_handoff_transcript_action: {transcript_action}")
        task_action = str(background_handoff_metadata.get("background_handoff_task_action") or "").strip()
        if task_action:
            lines.append(f"background_handoff_task_action: {task_action}")
        changes_action = str(
            background_handoff_metadata.get("background_handoff_changes_action") or ""
        ).strip()
        if changes_action:
            lines.append(f"background_handoff_changes_action: {changes_action}")
        resume_action = str(
            background_handoff_metadata.get("background_handoff_resume_action") or ""
        ).strip()
        if resume_action:
            lines.append(f"background_handoff_resume_action: {resume_action}")
        return lines

    def _selected_background_registry_entry(
        self,
        background_registry_metadata: dict[str, object],
        *,
        selected_index: int,
        selected_bg_id: str | None,
    ) -> tuple[int, dict[str, object] | None]:
        entries = background_registry_metadata.get("background_registry_entries")
        if not isinstance(entries, list) or not entries:
            return 0, None
        for index, item in enumerate(entries):
            if not isinstance(item, dict):
                continue
            if selected_bg_id and str(item.get("background_session_id") or "").strip() == selected_bg_id:
                return index, item
        if 0 <= selected_index < len(entries):
            item = entries[selected_index]
            return selected_index, item if isinstance(item, dict) else None
        preferred_bg_id = str(
            background_registry_metadata.get("background_registry_selected_bg_id") or ""
        ).strip()
        for index, item in enumerate(entries):
            if not isinstance(item, dict):
                continue
            if preferred_bg_id and str(item.get("background_session_id") or "").strip() == preferred_bg_id:
                return index, item
        first = entries[0]
        return 0, first if isinstance(first, dict) else None

    def _background_registry_status_lines(
        self,
        background_registry_metadata: dict[str, object] | None,
        *,
        selected_index: int = 0,
        selected_bg_id: str | None = None,
    ) -> list[str]:
        if not isinstance(background_registry_metadata, dict):
            return []
        registry_count = int(background_registry_metadata.get("background_registry_count", 0) or 0)
        entries = background_registry_metadata.get("background_registry_entries")
        if registry_count <= 0 and not isinstance(entries, list):
            return []
        resolved_index, selected_entry = self._selected_background_registry_entry(
            background_registry_metadata,
            selected_index=selected_index,
            selected_bg_id=selected_bg_id,
        )
        lines = ["Background Sessions"]
        lines.append(f"background_sessions: {registry_count}")
        selected_bg_id_text = (
            str(selected_entry.get("background_session_id") or "").strip()
            if isinstance(selected_entry, dict)
            else str(background_registry_metadata.get("background_registry_selected_bg_id") or "").strip()
        )
        if selected_bg_id_text:
            lines.append(f"selected_background_session: {selected_bg_id_text}")
        selected_status = (
            str(selected_entry.get("status") or "").strip()
            if isinstance(selected_entry, dict)
            else str(background_registry_metadata.get("background_registry_selected_status") or "").strip()
        )
        if selected_status:
            lines.append(f"selected_background_status: {selected_status}")
        selected_continuation = (
            str(selected_entry.get("background_continuation_category") or "").strip()
            if isinstance(selected_entry, dict)
            else str(
                background_registry_metadata.get("background_registry_selected_continuation_category") or ""
            ).strip()
        )
        if selected_continuation:
            lines.append(f"selected_background_continuation: {selected_continuation}")
        selected_workflow = (
            str(selected_entry.get("background_current_workflow_summary") or "").strip()
            if isinstance(selected_entry, dict)
            else ""
        )
        if not selected_workflow:
            selected_workflow = str(
                background_registry_metadata.get("background_registry_selected_workflow_summary") or ""
            ).strip()
        if selected_workflow:
            lines.append(f"selected_background_workflow: {selected_workflow}")
        selected_task = (
            selected_entry.get("background_primary_task")
            if isinstance(selected_entry, dict)
            else background_registry_metadata.get("background_registry_selected_primary_task")
        )
        if not isinstance(selected_task, dict):
            fallback_task = background_registry_metadata.get("background_registry_selected_primary_task")
            selected_task = fallback_task if isinstance(fallback_task, dict) else selected_task
        if isinstance(selected_task, dict):
            task_id = str(selected_task.get("task_id") or "").strip()
            task_status = str(selected_task.get("status") or "").strip()
            task_description = str(selected_task.get("description") or "").strip()
            task_bits = [bit for bit in (task_id, task_status, task_description) if bit]
            if task_bits:
                lines.append(f"selected_background_primary_task: {' | '.join(task_bits)}")
        selected_plan = (
            str(selected_entry.get("background_active_plan_summary") or "").strip()
            if isinstance(selected_entry, dict)
            else ""
        )
        if not selected_plan:
            selected_plan = str(
                background_registry_metadata.get("background_registry_selected_active_plan_summary") or ""
            ).strip()
        if selected_plan:
            lines.append(f"selected_background_active_plan: {selected_plan}")
        selected_file = (
            str(selected_entry.get("background_focused_file") or "").strip()
            if isinstance(selected_entry, dict)
            else ""
        )
        if not selected_file:
            selected_file = str(
                background_registry_metadata.get("background_registry_selected_focused_file") or ""
            ).strip()
        if selected_file:
            lines.append(f"selected_background_focused_file: {selected_file}")
        selected_recent_activity = (
            str(selected_entry.get("background_recent_activity") or "").strip()
            if isinstance(selected_entry, dict)
            else ""
        )
        if not selected_recent_activity:
            selected_recent_activity = str(
                background_registry_metadata.get("background_registry_selected_recent_activity") or ""
            ).strip()
        if selected_recent_activity:
            lines.append(f"selected_background_recent_activity: {selected_recent_activity}")
        selected_token_count = (
            selected_entry.get("background_token_count")
            if isinstance(selected_entry, dict)
            else background_registry_metadata.get("background_registry_selected_token_count")
        )
        selected_token_source = (
            str(selected_entry.get("background_token_count_source") or "").strip()
            if isinstance(selected_entry, dict)
            else str(
                background_registry_metadata.get("background_registry_selected_token_count_source") or ""
            ).strip()
        )
        if selected_token_count not in {None, ""}:
            suffix = f" ({selected_token_source})" if selected_token_source else ""
            lines.append(f"selected_background_token_count: {selected_token_count}{suffix}")
        selected_last_tool_input = (
            str(selected_entry.get("background_last_tool_input") or "").strip()
            if isinstance(selected_entry, dict)
            else ""
        )
        if not selected_last_tool_input:
            selected_last_tool_input = str(
                background_registry_metadata.get("background_registry_selected_last_tool_input") or ""
            ).strip()
        if selected_last_tool_input:
            lines.append(f"selected_background_last_tool_input: {selected_last_tool_input}")
        selected_last_tool_summary = (
            str(selected_entry.get("background_last_tool_summary") or "").strip()
            if isinstance(selected_entry, dict)
            else ""
        )
        if not selected_last_tool_summary:
            selected_last_tool_summary = str(
                background_registry_metadata.get("background_registry_selected_last_tool_summary") or ""
            ).strip()
        if selected_last_tool_summary:
            lines.append(f"selected_background_last_tool_summary: {selected_last_tool_summary}")
        selected_progress = (
            str(selected_entry.get("background_progress_summary") or "").strip()
            if isinstance(selected_entry, dict)
            else ""
        )
        if not selected_progress:
            selected_progress = str(
                background_registry_metadata.get("background_registry_selected_progress_summary") or ""
            ).strip()
        if selected_progress:
            lines.append(f"selected_background_progress: {selected_progress}")
        selected_completion_state = (
            str(selected_entry.get("background_completion_state") or "").strip()
            if isinstance(selected_entry, dict)
            else ""
        )
        if not selected_completion_state:
            selected_completion_state = str(
                background_registry_metadata.get("background_registry_selected_completion_state") or ""
            ).strip()
        if selected_completion_state:
            lines.append(f"selected_background_completion: {selected_completion_state}")
        selected_completion_summary = (
            str(selected_entry.get("background_completion_summary") or "").strip()
            if isinstance(selected_entry, dict)
            else ""
        )
        if not selected_completion_summary:
            selected_completion_summary = str(
                background_registry_metadata.get("background_registry_selected_completion_summary") or ""
            ).strip()
        if selected_completion_summary:
            lines.append(f"selected_background_completion_summary: {selected_completion_summary}")
        pending_followup_count = (
            int(selected_entry.get("background_pending_followup_count") or 0)
            if isinstance(selected_entry, dict)
            else int(
                background_registry_metadata.get("background_registry_selected_pending_followup_count", 0)
                or 0
            )
        )
        if pending_followup_count > 0:
            lines.append(f"selected_background_pending_followups: {pending_followup_count}")
        pending_followup_summary = (
            str(selected_entry.get("background_pending_followup_summary") or "").strip()
            if isinstance(selected_entry, dict)
            else ""
        )
        if not pending_followup_summary:
            pending_followup_summary = str(
                background_registry_metadata.get("background_registry_selected_pending_followup_summary") or ""
            ).strip()
        if pending_followup_summary:
            lines.append(f"selected_background_pending_followup: {pending_followup_summary}")
        primary_action = (
            str(selected_entry.get("background_primary_action") or "").strip()
            if isinstance(selected_entry, dict)
            else str(background_registry_metadata.get("background_registry_primary_action") or "").strip()
        )
        if primary_action:
            lines.append(f"background_registry_primary_action: {primary_action}")
        secondary_action = (
            str(selected_entry.get("background_secondary_action") or "").strip()
            if isinstance(selected_entry, dict)
            else str(background_registry_metadata.get("background_registry_secondary_action") or "").strip()
        )
        if secondary_action:
            lines.append(f"background_registry_secondary_action: {secondary_action}")
        logs_action = (
            str(selected_entry.get("background_logs_action") or "").strip()
            if isinstance(selected_entry, dict)
            else str(background_registry_metadata.get("background_registry_logs_action") or "").strip()
        )
        if logs_action:
            lines.append(f"background_registry_logs_action: {logs_action}")
        send_followup_action = (
            str(selected_entry.get("background_send_followup_action") or "").strip()
            if isinstance(selected_entry, dict)
            else str(background_registry_metadata.get("background_registry_send_followup_action") or "").strip()
        )
        if send_followup_action:
            lines.append(f"background_registry_send_followup_action: {send_followup_action}")
        queue_action = (
            str(selected_entry.get("background_queue_message_action") or "").strip()
            if isinstance(selected_entry, dict)
            else str(background_registry_metadata.get("background_registry_queue_message_action") or "").strip()
        )
        if queue_action:
            lines.append(f"background_registry_queue_message_action: {queue_action}")
        cancel_action = (
            str(selected_entry.get("background_cancel_pending_followup_action") or "").strip()
            if isinstance(selected_entry, dict)
            else str(
                background_registry_metadata.get("background_registry_cancel_pending_followup_action") or ""
            ).strip()
        )
        if cancel_action:
            lines.append(f"background_registry_cancel_pending_followup_action: {cancel_action}")
        if isinstance(entries, list) and entries:
            lines.append("background_registry_entries:")
            for index, item in enumerate(entries[:3], start=1):
                if not isinstance(item, dict):
                    continue
                bg_id = str(item.get("background_session_id") or "").strip() or f"item-{index}"
                status = str(item.get("status") or "").strip() or "unknown"
                continuation = str(item.get("background_continuation_category") or "").strip() or "none"
                primary_action = str(item.get("background_primary_action") or "").strip() or "none"
                marker = ">" if index - 1 == resolved_index else "-"
                lines.append(
                    f"{marker} {index}. {bg_id} status={status} continuation={continuation} action={primary_action}"
                )
        return lines

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
            "Workspace State",
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
        lines.append(f"context_origin: {self._file_context_origin_label(focused_item)}")
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
            lines.append("  context_origin: " + self._file_context_origin_label(item))
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

    def _file_context_origin_label(self, item: dict[str, object]) -> str:
        explicit = False
        automatic = False
        for reason in self._file_context_scope_reasons(item):
            if reason == "explicit context path":
                explicit = True
            else:
                automatic = True
        if explicit and automatic:
            return "explicit+automatic"
        if explicit:
            return "explicit-only"
        return "automatic-only"

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
        lines = ["Workspace State"]
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

    def _record_tool_waiting(self, event: RuntimeEvent) -> None:
        self.tool_logs.append(
            ToolLogEntry(
                tool_call_id=event.tool_call_id or "",
                tool_name=event.tool_name or "unknown",
                status="WAITING",
                input_summary=event.message,
                detail=(
                    f"approval: {event.approval_risk_level}"
                    if event.approval_risk_level
                    else ""
                ),
            )
        )

    def _record_tool_started(self, event: RuntimeEvent) -> None:
        entry = self._find_tool_entry(event.tool_call_id)
        if entry is None:
            self.tool_logs.append(
                ToolLogEntry(
                    tool_call_id=event.tool_call_id or "",
                    tool_name=event.tool_name or "unknown",
                    status="RUNNING",
                    input_summary=event.message,
                )
            )
        else:
            entry.status = "RUNNING"
            if event.message:
                entry.input_summary = event.message
            entry.detail = ""
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
