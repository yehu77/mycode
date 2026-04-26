from __future__ import annotations

from dataclasses import dataclass, field

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
class TuiState:
    messages: list[str] = field(default_factory=list)
    turns: list[ChatTurn] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    tool_logs: list[ToolLogEntry] = field(default_factory=list)
    pending_approval: PendingApproval | None = None
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

    def render_task_panel(self, tasks_text: str) -> str:
        if not tasks_text or tasks_text.strip() == "No tasks.":
            return "Tasks\nNo tasks."
        return "Tasks\n" + tasks_text

    def render_plan_panel(self, plan_text: str) -> str:
        if not plan_text or plan_text.strip() == "No active planning artifact.":
            return "Active Plan\nNo active planning artifact."
        return "Active Plan\n" + plan_text

    def render_advisor_panel(self, advisor_text: str) -> str:
        if not advisor_text or advisor_text.strip().startswith("Advisor: not set"):
            return "Advisor Status\nAdvisor not configured."
        return "Advisor Status\n" + advisor_text

    def render_status_panel(self, *, provider_text: str, config_text: str) -> str:
        config_items: dict[str, str] = {}
        for line in config_text.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", maxsplit=1)
            config_items[key.strip()] = value.strip()

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
        pending_change_preview: str = "",
        change_status: str = "",
        changes_text: str = "",
    ) -> str:
        lines = ["Changes"]
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
                lines.append(f"Focused undo ({self.selected_change_index + 1}/{len(undo_entries)}):")
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
                lines.append(f"Focused redo ({self.selected_redo_index + 1}/{len(redo_entries)}):")
                lines.extend(self._compact_block(selected_redo_detail, max_lines=10))
        elif not changes_text or changes_text.strip() == "No recorded workspace changes.":
            lines.append("No recorded workspace changes.")
        else:
            lines.append(changes_text)
        lines.append("")
        lines.append("Ctrl+Z undo")
        lines.append("Ctrl+Y redo")
        lines.append("Shift+Left/Right focus stack")
        lines.append("Shift+Up/Down select")
        lines.append("Ctrl+Left/Right file")
        lines.append("Ctrl+Shift+Z undo selected")
        lines.append("Ctrl+Shift+Y redo selected")
        return "\n".join(lines)

    def _compact_block(self, text: str, *, max_lines: int) -> list[str]:
        lines = text.splitlines()
        if len(lines) <= max_lines:
            return lines
        return [*lines[:max_lines], "..."]

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
        if self.pending_approval is None:
            lines = ["Approval", "No pending approval."]
        else:
            request = self.pending_approval.request
            lines = [
                "Approval",
                f"risk: {request.risk_level}",
                f"tool: {request.tool_name}",
                f"reason: {request.reason}",
            ]
            if request.details:
                lines.append("")
                lines.append("Preview mirrored in Changes panel.")
            lines.append("")
            lines.append("Ctrl+O allow once")
            lines.append("Ctrl+S allow session")
            lines.append("Ctrl+N deny")
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
                    detail=event.message,
                )
            )
        else:
            entry.status = "ERROR"
            entry.duration_ms = event.duration_ms
            entry.detail = event.message
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
