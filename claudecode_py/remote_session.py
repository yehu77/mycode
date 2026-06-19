from __future__ import annotations

from pathlib import Path
from queue import Queue
from socket import create_connection
from threading import Lock, Thread
from time import sleep, time
from typing import Any, Callable
import json

from .commands import CommandExecution, build_default_command_registry, render_repl_command_help
from .config import SessionConfig
from .interactions import QuestionOption, UserQuestion, UserQuestionRequest, UserQuestionResponse
from .permissions import ApprovalRequest, ApprovalResult
from .runtime.events import RuntimeEvent
from .state import SessionState


def _runtime_event_from_payload(event_payload: dict[str, Any]) -> RuntimeEvent | None:
    kind = event_payload.get("kind")
    if kind not in {
        "assistant_text",
        "assistant_usage",
        "assistant_tool_call",
        "assistant_tool_result_ready",
        "plan_execution",
        "task_progress",
        "advisor",
        "advisor_review_started",
        "advisor_review_result",
        "advisor_revision_requested",
        "advisor_error",
        "context_compacted",
        "provider_retry",
        "tool_batch_started",
        "tool_batch_finished",
        "tool_waiting_for_approval",
        "tool_started",
        "tool_finished",
        "tool_failed",
        "tool_result",
        "tool_result_summarized",
        "skill_tool_inline_messages_applied",
        "skill_tool_fork_messages_applied",
        "skill_tool_context_applied",
        "tool_result_replacement_applied",
        "tool_result_replacement_reapplied",
        "tool_result_artifact_created",
        "tool_result_artifact_reused",
        "tool_result_microcompacted",
        "prompt_cache_hints_applied",
        "prompt_cache_hints_fallback",
        "prompt_prefix_planner_applied",
        "prompt_prefix_planner_downgraded",
        "budget_pressure",
        "compact_recovery_started",
        "compact_recovery_finished",
    }:
        return None
    return RuntimeEvent(
        kind=kind,  # type: ignore[arg-type]
        message=str(event_payload.get("message", "")),
        task_id=(str(event_payload.get("task_id")) if event_payload.get("task_id") is not None else None),
        tool_name=event_payload.get("tool_name"),
        tool_call_id=event_payload.get("tool_call_id"),
        duration_ms=event_payload.get("duration_ms"),
        prompt_tokens=event_payload.get("prompt_tokens"),
        completion_tokens=event_payload.get("completion_tokens"),
        total_tokens=event_payload.get("total_tokens"),
        usage_source=(
            str(event_payload.get("usage_source"))
            if event_payload.get("usage_source") is not None
            else None
        ),
        batch_size=event_payload.get("batch_size"),
        batch_parallel=event_payload.get("batch_parallel"),
        result_count=event_payload.get("result_count"),
        budget_state=(
            str(event_payload.get("budget_state"))
            if event_payload.get("budget_state") is not None
            else None
        ),
        budget_reason=(
            str(event_payload.get("budget_reason"))
            if event_payload.get("budget_reason") is not None
            else None
        ),
        compaction_trigger=(
            str(event_payload.get("compaction_trigger"))
            if event_payload.get("compaction_trigger") is not None
            else None
        ),
        approval_risk_level=(
            str(event_payload.get("approval_risk_level"))
            if event_payload.get("approval_risk_level") is not None
            else None
        ),
        replacement_count=event_payload.get("replacement_count"),
        replaced_chars_total=event_payload.get("replaced_chars_total"),
        replacement_reason=(
            str(event_payload.get("replacement_reason"))
            if event_payload.get("replacement_reason") is not None
            else None
        ),
        artifact_count=event_payload.get("artifact_count"),
        artifact_chars_saved=event_payload.get("artifact_chars_saved"),
        microcompact_count=event_payload.get("microcompact_count"),
        microcompact_chars_saved=event_payload.get("microcompact_chars_saved"),
        is_error=bool(event_payload.get("is_error")),
    )


class _RemotePermissionManager:
    def __init__(self) -> None:
        self.approval_handler = None


REMOTE_REPL_COMMAND_HELP = (
    render_repl_command_help(build_default_command_registry())
    + "\n/approve         Approve the pending tool request once"
    + "\n/approve-session Approve the pending tool request for the session"
    + "\n/deny            Deny the pending tool request"
    + "\n/cancel-question Cancel the pending structured question"
)


class BridgeClient:
    def __init__(self, host: str, port: int) -> None:
        self.socket = create_connection((host, port), timeout=5)
        self.reader = self.socket.makefile("r", encoding="utf-8")
        self.writer = self.socket.makefile("w", encoding="utf-8")
        self._notification_handlers: list[Callable[[dict[str, Any]], None]] = []
        self._responses: dict[object, Queue] = {}
        self._request_lock = Lock()
        self._request_id = 0
        self._reader_thread = Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def add_notification_handler(self, handler: Callable[[dict[str, Any]], None]) -> None:
        self._notification_handlers.append(handler)

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        with self._request_lock:
            self._request_id += 1
            request_id = self._request_id
            response_queue: Queue = Queue()
            self._responses[request_id] = response_queue
            self.writer.write(
                json.dumps({"id": request_id, "method": method, "params": params}, ensure_ascii=False) + "\n"
            )
            self.writer.flush()
        response = response_queue.get(timeout=30)
        error = response.get("error")
        if error is not None:
            raise RuntimeError(error.get("message", "bridge request failed"))
        return response["result"]

    def close(self) -> None:
        try:
            self.writer.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.reader.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.socket.close()
        except Exception:  # noqa: BLE001
            pass

    def _read_loop(self) -> None:
        try:
            for line in self.reader:
                payload = json.loads(line)
                if payload.get("type") == "notification":
                    for handler in list(self._notification_handlers):
                        try:
                            handler(payload)
                        except Exception:  # noqa: BLE001
                            continue
                    continue
                request_id = payload.get("id")
                queue = self._responses.pop(request_id, None)
                if queue is not None:
                    queue.put(payload)
        except Exception:  # noqa: BLE001
            return


class RemoteSessionProxy:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        session_id: str,
        replay_limit: int = 1000,
    ) -> None:
        self.host = host
        self.port = port
        self.client = BridgeClient(host, port)
        self.session_id = session_id
        self.client.add_notification_handler(self._handle_notification)
        self.client.request("bridge.hello", {})
        described = self.client.request("session.describe", {"session_id": self.session_id})
        self.config = SessionConfig(
            cwd=Path(described["cwd"]),
            provider=described["provider"],
            model=described["model"],
            interactive=True,
        )
        self.state = SessionState(
            session_id=self.session_id,
            messages=[{} for _ in range(int(described.get("message_count", 0)))],
        )
        self._workspace_action_bundle: dict[str, str] = {}
        self._symbol_surface: dict[str, Any] | None = None
        self._checklist_duplicate_guard: dict[str, Any] | None = None
        self._task_surface_counts: dict[str, int] = {}
        self._working_set_metadata: dict[str, Any] = {}
        self._file_context_surface_metadata: dict[str, Any] = {}
        self._memory_metadata: dict[str, Any] = {}
        self._rewind_preview_metadata: dict[str, Any] = {}
        self._rewind_preview_selector: str | None = None
        self._background_metadata: dict[str, Any] = {}
        self._background_registry_metadata: dict[str, Any] = {}
        self._background_handoff_metadata: dict[str, Any] = {}
        self._skills_surface_metadata: dict[str, Any] = {}
        self._plugin_surface_metadata: dict[str, Any] = {}
        self._status_metadata: dict[str, Any] = {}
        self._workspace_surface_metadata: dict[str, Any] = {}
        self._task_detail_cache: dict[str, dict[str, Any]] = {}
        self._sync_workspace_metadata(described)
        self._sync_execution_contract_metadata(described)
        self._sync_task_surface_counts(described)
        self._sync_symbol_surface_metadata(described)
        self._sync_working_set_metadata(described)
        self._sync_file_context_surface_metadata(described)
        self._sync_memory_metadata(described)
        self._sync_background_metadata(described)
        self._sync_background_registry_metadata(described)
        self._sync_background_handoff_metadata(described)
        self._sync_skills_surface_metadata(described)
        self._sync_plugin_surface_metadata(described)
        self._sync_status_metadata(described)
        self._sync_workspace_surface_metadata(described)
        self.permission_manager = _RemotePermissionManager()
        self._default_live_sink = None
        self._transient_live_sink = None
        self._approval_requested_handler = None
        self._approval_resolved_handler = None
        self._question_requested_handler = None
        self._question_resolved_handler = None
        self._sink_lock = Lock()
        self.pending_approval: ApprovalRequest | None = None
        self.pending_approval_id: int | None = None
        self.pending_question: UserQuestionRequest | None = None
        self.pending_question_id: int | None = None
        subscribed = self.client.request(
            "bridge.subscribe",
            {"session_id": self.session_id, "after_seq": 0, "limit": replay_limit},
        )
        self._event_cursor = int(subscribed.get("last_seq", described.get("event_cursor", 0)))
        self._replay_events: list[RuntimeEvent] = []
        for item in subscribed.get("replay", []):
            if not isinstance(item, dict):
                continue
            event_payload = item.get("event")
            if not isinstance(event_payload, dict):
                continue
            self._ingest_control_event(event_payload)
            event = _runtime_event_from_payload(event_payload)
            if event is not None:
                self._replay_events.append(event)
        self._sync_pending_approval()
        self._sync_pending_question()

    def set_live_event_sink(self, sink) -> None:
        self._default_live_sink = sink

    def set_approval_handlers(self, requested_handler=None, resolved_handler=None) -> None:
        self._approval_requested_handler = requested_handler
        self._approval_resolved_handler = resolved_handler

    def set_question_handlers(self, requested_handler=None, resolved_handler=None) -> None:
        self._question_requested_handler = requested_handler
        self._question_resolved_handler = resolved_handler

    def take_replay_events(self) -> list[RuntimeEvent]:
        events = list(self._replay_events)
        self._replay_events.clear()
        return events

    def ask(
        self,
        prompt: str,
        sink=None,
        *,
        allowed_tool_names: tuple[str, ...] | None = None,
        allowed_bash_command_prefixes: tuple[str, ...] | None = None,
    ) -> str:
        with self._temporary_sink(sink):
            request_client = BridgeClient(self.host, self.port)
            try:
                result = request_client.request(
                    "session.ask",
                    {
                        "session_id": self.session_id,
                        "prompt": prompt,
                        "allowed_tool_names": (
                            list(allowed_tool_names) if allowed_tool_names is not None else None
                        ),
                        "allowed_bash_command_prefixes": (
                            list(allowed_bash_command_prefixes)
                            if allowed_bash_command_prefixes is not None
                            else None
                        ),
                    },
                )
            finally:
                request_client.close()
        self._sync_metadata(result)
        self._sync_from_describe()
        return str(result.get("output") or "")

    def run_command(self, execution: CommandExecution, sink=None) -> str:
        with self._temporary_sink(sink):
            request_client = BridgeClient(self.host, self.port)
            try:
                result = request_client.request(
                    "session.run_command",
                    {
                        "session_id": self.session_id,
                        "execution": {
                            "prompt": execution.prompt,
                            "allowed_tool_names": (
                                list(execution.allowed_tool_names)
                                if execution.allowed_tool_names is not None
                                else None
                            ),
                            "allowed_bash_command_prefixes": (
                                list(execution.allowed_bash_command_prefixes)
                                if execution.allowed_bash_command_prefixes is not None
                                else None
                            ),
                            "require_read_only_subagents": execution.require_read_only_subagents,
                            "progress_message": execution.progress_message,
                            "metadata": execution.metadata,
                        },
                    },
                )
            finally:
                request_client.close()
        self._sync_metadata(result)
        self._sync_from_describe()
        return str(result.get("output") or "")

    def handle_repl_command(self, prompt: str) -> tuple[bool, str | CommandExecution | None]:
        local = self._handle_local_repl_command(prompt)
        if local is not None:
            return local
        result = self.client.request(
            "session.command",
            {"session_id": self.session_id, "prompt": prompt},
        )
        handled = bool(result.get("handled"))
        output_kind = result.get("output_kind")
        if output_kind == "command_execution":
            execution = result.get("execution") or {}
            return (
                handled,
                CommandExecution(
                    prompt=str(execution.get("prompt", "")),
                    allowed_tool_names=tuple(execution.get("allowed_tool_names") or ()),
                    allowed_bash_command_prefixes=tuple(
                        execution.get("allowed_bash_command_prefixes") or ()
                    ),
                    require_read_only_subagents=bool(
                        execution.get("require_read_only_subagents", False)
                    ),
                    progress_message=str(execution.get("progress_message", "Running command")),
                    metadata=execution.get("metadata") if isinstance(execution.get("metadata"), dict) else None,
                ),
            )
        if handled:
            self._sync_from_describe()
        return handled, result.get("output")

    def resolve_pending_approval(self, result: ApprovalResult) -> str:
        if self.pending_approval is None or self.pending_approval_id is None:
            self._wait_for_pending_approval(timeout_sec=2.0)
        if self.pending_approval is None or self.pending_approval_id is None:
            return "No pending approval request."
        tool_name = self.pending_approval.tool_name
        approval_id = self.pending_approval_id
        self.client.request(
            "session.approval_respond",
            {
                "session_id": self.session_id,
                "approval_id": approval_id,
                "decision": result.decision,
                "scope": result.scope,
            },
        )
        return (
            f'Approved {tool_name} ({result.scope}).'
            if result.decision == "allow"
            else f"Denied {tool_name}."
        )

    def resolve_pending_question(self, result: UserQuestionResponse) -> str:
        if self.pending_question is None or self.pending_question_id is None:
            self._wait_for_pending_question(timeout_sec=2.0)
        if self.pending_question is None or self.pending_question_id is None:
            return "No pending question request."
        question_id = self.pending_question_id
        self.client.request(
            "session.question_respond",
            {
                "session_id": self.session_id,
                "question_id": question_id,
                "answers": dict(result.answers),
                "canceled": result.canceled,
            },
        )
        if result.canceled:
            return "Canceled pending structured questions."
        return "Submitted structured question answers."

    def describe_tasks(self) -> str:
        return self._view_text("tasks")

    def checklist_tasks_payload(self) -> list[dict[str, Any]]:
        result = self.client.request(
            "session.view",
            {"session_id": self.session_id, "view": "tasks"},
        )
        self._sync_checklist_duplicate_guard_metadata(result)
        self._sync_task_surface_counts(result)
        payload = result.get("checklist_tasks")
        if not isinstance(payload, list):
            return []
        return [dict(item) for item in payload if isinstance(item, dict)]

    def task_surface_counts_payload(self) -> dict[str, int]:
        return dict(self._task_surface_counts)

    def task_surface_summary_lines(self) -> list[str]:
        counts = self.task_surface_counts_payload()
        lines = ["task_surfaces:"]
        for kind in (
            "checklist",
            "workspace_maintenance",
            "child_execution",
            "background_execution",
            "active_plan_execution",
            "other_task",
        ):
            lines.append(f"{kind}: {int(counts.get(kind, 0))}")
        return lines

    def checklist_duplicate_guard_payload(self) -> dict[str, Any] | None:
        if not isinstance(self._checklist_duplicate_guard, dict) or not self._checklist_duplicate_guard:
            return None
        return dict(self._checklist_duplicate_guard)

    def working_set_payload(self, limit: int = 5) -> dict[str, Any]:
        del limit
        return self._extract_file_context_payload(self._working_set_metadata)

    def file_context_surface_payload(self) -> dict[str, Any]:
        return dict(self._file_context_surface_metadata)

    def workspace_surface_payload(self) -> dict[str, Any]:
        return dict(self._workspace_surface_metadata)

    def execution_contract_payload(self) -> dict[str, Any]:
        return {
            "session_execution_mode": self.state.session_execution_mode,
            "session_command_policy_name": self.state.session_command_policy_name,
            "session_command_policy_source": self.state.session_command_policy_source,
            "session_command_policy_allowed_tool_names": list(
                self.state.session_command_policy_allowed_tool_names
            ),
            "session_command_policy_allowed_bash_prefixes": list(
                self.state.session_command_policy_allowed_bash_prefixes
            ),
            "session_command_policy_require_read_only_subagents": (
                self.state.session_command_policy_require_read_only_subagents
            ),
            "session_command_policy_enforce_read_only_bash": (
                str(self.state.session_command_policy_name or "") in {
                    "review",
                    "security-review",
                    "ultraplan",
                    "read-only-subagent",
                    "read-only-turn",
                }
            ),
        }

    def task_execution_detail_metadata(self, task_id: str) -> dict[str, Any] | None:
        result = self._task_detail_view_result(task_id)
        execution_mode = str(result.get("execution_mode") or "").strip()
        execution_policy = str(result.get("execution_policy") or "").strip()
        execution_policy_source = str(result.get("execution_policy_source") or "").strip()
        allowed_tools = [str(item) for item in (result.get("allowed_tools") or []) if str(item).strip()]
        allowed_bash_prefixes = [
            str(item) for item in (result.get("allowed_bash_prefixes") or []) if str(item).strip()
        ]
        read_only_subagents = bool(result.get("read_only_subagents", False))
        task_surface = str(result.get("task_surface") or "").strip()
        workspace_mode = str(result.get("workspace_mode") or "").strip()
        workspace_health = str(result.get("workspace_health") or "").strip()
        if not any(
            (
                execution_mode,
                execution_policy,
                execution_policy_source,
                allowed_tools,
                allowed_bash_prefixes,
                read_only_subagents,
                task_surface,
                workspace_mode,
                workspace_health,
            )
        ):
            return None
        return {
            "task_surface": task_surface or "other_task",
            "execution_mode": execution_mode or "main",
            "execution_policy": execution_policy or None,
            "execution_policy_source": execution_policy_source or None,
            "allowed_tools": allowed_tools,
            "allowed_bash_prefixes": allowed_bash_prefixes,
            "read_only_subagents": read_only_subagents,
            "workspace_mode": workspace_mode or None,
            "workspace_health": workspace_health or None,
        }

    def task_file_context_payload(self, task_id: str, limit: int = 5) -> dict[str, Any] | None:
        del limit
        payload = self._extract_file_context_payload(self._task_detail_view_result(task_id))
        return payload if int(payload.get("file_context_file_count") or 0) > 0 else None

    def active_plan_file_context_payload(
        self,
        identifier: str | None = None,
        limit: int = 5,
    ) -> dict[str, Any] | None:
        del identifier, limit
        result = self.client.request(
            "session.view",
            {
                "session_id": self.session_id,
                "view": "active_plan",
            },
        )
        payload = self._extract_file_context_payload(result)
        return payload if int(payload.get("file_context_file_count") or 0) > 0 else None

    def describe_active_plan(self) -> str:
        return self._view_text("active_plan")

    def describe_active_plan_scouts(
        self,
        *,
        selected_index: int = 0,
        full_detail: bool = False,
    ) -> str:
        result = self.client.request(
            "session.view",
            {
                "session_id": self.session_id,
                "view": "active_plan_scouts",
                "selected_index": selected_index,
                "full_detail": full_detail,
            },
        )
        return str(result.get("text", ""))

    def describe_active_plan_execution(
        self,
        *,
        selected_index: int = 0,
        full_detail: bool = False,
    ) -> str:
        result = self.client.request(
            "session.view",
            {
                "session_id": self.session_id,
                "view": "active_plan_execution",
                "selected_index": selected_index,
                "full_detail": full_detail,
            },
        )
        return str(result.get("text", ""))

    def describe_active_plan_timeline(
        self,
        *,
        selected_index: int = 0,
        selected_compare_index: int = 0,
        selected_phase_local_task_index: int = 0,
        kind_filter: str = "all",
        delta_mode: str = "none",
        phase_filter: str = "none",
        focus_mode: str = "none",
        compare_mode: str = "none",
        artifact_id: str | None = None,
    ) -> str:
        result = self.client.request(
            "session.view",
            {
                "session_id": self.session_id,
                "view": "active_plan_timeline",
                "selected_index": selected_index,
                "selected_compare_index": selected_compare_index,
                "selected_phase_local_task_index": selected_phase_local_task_index,
                "kind_filter": kind_filter,
                "delta_mode": delta_mode,
                "phase_filter": phase_filter,
                "focus_mode": focus_mode,
                "compare_mode": compare_mode,
                "artifact_id": artifact_id,
            },
        )
        self._sync_metadata(result)
        return str(result.get("text") or "")

    def describe_active_plan_lineage(self, *, selected_index: int = 0) -> str:
        result = self.client.request(
            "session.view",
            {
                "session_id": self.session_id,
                "view": "active_plan_lineage",
                "selected_index": selected_index,
            },
        )
        return str(result.get("text", ""))

    def describe_active_plan_audit(
        self,
        *,
        selected_index: int = 0,
        artifact_id: str | None = None,
    ) -> str:
        result = self.client.request(
            "session.view",
            {
                "session_id": self.session_id,
                "view": "active_plan_audit",
                "selected_index": selected_index,
                "artifact_id": artifact_id,
            },
        )
        return str(result.get("text", ""))

    def describe_active_plan_replay(
        self,
        *,
        selected_index: int = 0,
        selected_compare_index: int = 0,
        selected_phase_local_task_index: int = 0,
        kind_filter: str = "all",
        delta_mode: str = "none",
        phase_filter: str = "none",
        focus_mode: str = "none",
        compare_mode: str = "none",
        latest: bool = False,
        source_mode: str = "auto",
        artifact_id: str | None = None,
    ) -> str:
        result = self.client.request(
            "session.view",
            {
                "session_id": self.session_id,
                "view": "active_plan_replay",
                "selected_index": selected_index,
                "selected_compare_index": selected_compare_index,
                "selected_phase_local_task_index": selected_phase_local_task_index,
                "kind_filter": kind_filter,
                "delta_mode": delta_mode,
                "phase_filter": phase_filter,
                "focus_mode": focus_mode,
                "compare_mode": compare_mode,
                "latest": latest,
                "source_mode": source_mode,
                "artifact_id": artifact_id,
            },
        )
        return str(result.get("text", ""))

    def active_plan_lineage_index(self) -> int:
        text = self.describe_active_plan()
        for line in text.splitlines():
            if not line.startswith("lineage_position:"):
                continue
            raw = line.split(":", 1)[1].strip()
            head, sep, _tail = raw.partition("/")
            if not sep:
                break
            try:
                return max(0, int(head) - 1)
            except ValueError:
                break
        return 0

    def describe_active_plan_advisor(self) -> str:
        return self._view_text("active_plan_advisor")

    def describe_advisor(self) -> str:
        return self._view_text("advisor_status")

    def describe_task_detail(self, task_id: str) -> str:
        result = self._task_detail_view_result(task_id)
        return str(result.get("text", ""))

    def selected_change_detail_metadata(
        self,
        *,
        index: int = 0,
        file_index: int = 0,
        limit: int = 5,
        redo: bool = False,
    ) -> dict[str, Any]:
        result = self.client.request(
            "session.change_view",
            {
                "session_id": self.session_id,
                "view": "detail",
                "index": index,
                "file_index": file_index,
                "limit": limit,
                "redo": redo,
            },
        )
        return self._extract_file_context_payload(result)

    def locate_symbol_payload(
        self,
        symbol: str,
        *,
        path: str = ".",
        max_results: int = 50,
    ) -> dict[str, Any]:
        result = self.client.request(
            "symbol.locate",
            {
                "session_id": self.session_id,
                "symbol": symbol,
                "path": path,
                "max_results": max_results,
            },
        )
        payload = dict(result)
        self._symbol_surface = dict(payload)
        return payload

    def collect_references_payload(
        self,
        symbol: str,
        *,
        path: str = ".",
        scope: str = "auto",
        max_results: int = 100,
    ) -> dict[str, Any]:
        result = self.client.request(
            "symbol.references",
            {
                "session_id": self.session_id,
                "symbol": symbol,
                "path": path,
                "scope": scope,
                "max_results": max_results,
            },
        )
        payload = dict(result)
        self._symbol_surface = dict(payload)
        return payload

    def symbol_action_surface_payload(
        self,
        symbol: str,
        *,
        path: str = ".",
        scope: str = "workspace",
        max_definition_results: int = 50,
        max_reference_results: int = 100,
    ) -> dict[str, Any]:
        result = self.client.request(
            "symbol.actions",
            {
                "session_id": self.session_id,
                "symbol": symbol,
                "path": path,
                "scope": scope,
                "max_definition_results": max_definition_results,
                "max_reference_results": max_reference_results,
            },
        )
        payload = dict(result)
        self._symbol_surface = dict(payload)
        return payload

    def current_workspace_action_bundle(self) -> dict[str, str]:
        return dict(self._workspace_action_bundle)

    def current_symbol_surface_payload(self) -> dict[str, Any] | None:
        if not isinstance(self._symbol_surface, dict) or not self._symbol_surface:
            return None
        return {
            key: (
                dict(value)
                if isinstance(value, dict)
                else [dict(item) if isinstance(item, dict) else item for item in value]
                if isinstance(value, list)
                else value
            )
            for key, value in self._symbol_surface.items()
        }

    def current_symbol_surface_action_bundle(self) -> dict[str, str] | None:
        payload = self.current_symbol_surface_payload()
        if payload is None:
            return None
        primary_action = str(payload.get("symbol_primary_action") or "").strip()
        secondary_action = str(payload.get("symbol_secondary_action") or "").strip()
        surface_kind = str(payload.get("surface_kind") or payload.get("symbol_surface_kind") or "none")
        if not primary_action:
            if surface_kind == "symbol_actions" and isinstance(payload.get("selected_definition"), dict):
                primary_action = "/symbol open primary"
            elif isinstance(payload.get("selected_navigation_target"), dict):
                primary_action = "/symbol open primary"
            else:
                primary_action = "none"
        if not secondary_action:
            if surface_kind == "symbol_actions" and isinstance(payload.get("selected_reference"), dict):
                secondary_action = "/symbol open secondary"
            else:
                secondary_action = "none"
        return {
            "primary_action": primary_action,
            "secondary_action": secondary_action,
            "tertiary_action": str(payload.get("symbol_tertiary_action") or "/symbol clear"),
            "target": str(payload.get("symbol_action_target") or payload.get("selected_symbol") or "none"),
            "surface_kind": surface_kind,
        }

    def task_workspace_action_bundle(self, task_id: str) -> dict[str, str] | None:
        detail = self.task_workspace_detail_metadata(task_id)
        if detail is None:
            return None
        return {
            "primary_action": str(detail.get("workspace_primary_action") or ""),
            "secondary_action": str(detail.get("workspace_secondary_action") or ""),
            "tertiary_action": str(detail.get("workspace_tertiary_action") or ""),
            "target": str(detail.get("workspace_action_target") or ""),
            "workspace_health": str(detail.get("workspace_health") or self.state.workspace_health),
        }

    def task_workspace_detail_metadata(self, task_id: str) -> dict[str, Any] | None:
        result = self._task_detail_view_result(task_id)
        action = str(result.get("workspace_action") or "").strip()
        primary_action = str(result.get("workspace_primary_action") or "").strip()
        secondary_action = str(result.get("workspace_secondary_action") or "").strip()
        tertiary_action = str(result.get("workspace_tertiary_action") or "").strip()
        target = str(result.get("workspace_action_target") or "").strip()
        workspace_health = str(result.get("workspace_health") or "").strip()
        if not any((action, primary_action, secondary_action, tertiary_action, target, workspace_health)):
            return None
        return {
            "workspace_action": action,
            "workspace_target": str(result.get("workspace_target") or target),
            "workspace_health_before": result.get("workspace_health_before"),
            "workspace_health_after": result.get("workspace_health_after"),
            "workspace_planned_paths": list(result.get("workspace_planned_paths") or []),
            "workspace_applied_paths": list(result.get("workspace_applied_paths") or []),
            "workspace_failure_reason": result.get("workspace_failure_reason"),
            "workspace_recommended_actions": list(result.get("workspace_recommended_actions") or []),
            "workspace_primary_action": primary_action,
            "workspace_secondary_action": secondary_action,
            "workspace_tertiary_action": tertiary_action,
            "workspace_action_target": target,
            "workspace_health": workspace_health or self.state.workspace_health,
        }

    def checklist_task_detail_metadata(self, task_id: str) -> dict[str, Any] | None:
        result = self._task_detail_view_result(task_id)
        checklist_task_id = str(result.get("checklist_task_id") or "").strip()
        if not checklist_task_id:
            return None
        detail = {
            "checklist_task_id": checklist_task_id,
            "checklist_task_list_id": str(result.get("checklist_task_list_id") or ""),
            "checklist_subject": str(result.get("checklist_subject") or ""),
            "checklist_description": str(result.get("checklist_description") or ""),
            "checklist_active_form": str(result.get("checklist_active_form") or ""),
            "checklist_status": str(result.get("checklist_status") or ""),
            "checklist_owner": str(result.get("checklist_owner") or ""),
            "checklist_blocks": list(result.get("checklist_blocks") or []),
            "checklist_blocked_by": list(result.get("checklist_blocked_by") or []),
            "checklist_metadata": dict(result.get("checklist_metadata") or {}),
            "checklist_created_at": str(result.get("checklist_created_at") or ""),
            "checklist_updated_at": str(result.get("checklist_updated_at") or ""),
            "checklist_total_tasks": int(result.get("checklist_total_tasks") or 0),
            "checklist_in_progress_tasks": int(result.get("checklist_in_progress_tasks") or 0),
            "checklist_recommended_actions": list(result.get("checklist_recommended_actions") or []),
            "checklist_primary_action": str(result.get("checklist_primary_action") or ""),
            "checklist_secondary_action": str(result.get("checklist_secondary_action") or ""),
            "checklist_tertiary_action": str(result.get("checklist_tertiary_action") or ""),
            "checklist_edit_subject_action": str(result.get("checklist_edit_subject_action") or ""),
            "checklist_edit_description_action": str(result.get("checklist_edit_description_action") or ""),
            "checklist_edit_owner_action": str(result.get("checklist_edit_owner_action") or ""),
            "checklist_edit_active_form_action": str(result.get("checklist_edit_active_form_action") or ""),
            "checklist_edit_blocks_action": str(result.get("checklist_edit_blocks_action") or ""),
            "checklist_edit_blocked_by_action": str(result.get("checklist_edit_blocked_by_action") or ""),
            "checklist_edit_metadata_action": str(result.get("checklist_edit_metadata_action") or ""),
            "checklist_action_target": str(result.get("checklist_action_target") or ""),
            "selected_checklist_primary_action": str(result.get("selected_checklist_primary_action") or ""),
            "selected_checklist_secondary_action": str(result.get("selected_checklist_secondary_action") or ""),
            "selected_checklist_tertiary_action": str(result.get("selected_checklist_tertiary_action") or ""),
            "selected_checklist_edit_subject_action": str(result.get("selected_checklist_edit_subject_action") or ""),
            "selected_checklist_edit_description_action": str(result.get("selected_checklist_edit_description_action") or ""),
            "selected_checklist_edit_owner_action": str(result.get("selected_checklist_edit_owner_action") or ""),
            "selected_checklist_edit_active_form_action": str(result.get("selected_checklist_edit_active_form_action") or ""),
            "selected_checklist_edit_blocks_action": str(result.get("selected_checklist_edit_blocks_action") or ""),
            "selected_checklist_edit_blocked_by_action": str(result.get("selected_checklist_edit_blocked_by_action") or ""),
            "selected_checklist_edit_metadata_action": str(result.get("selected_checklist_edit_metadata_action") or ""),
            "selected_checklist_target": str(result.get("selected_checklist_target") or ""),
        }
        if result.get("checklist_duplicate_guard") is not None:
            detail["checklist_duplicate_guard"] = dict(result.get("checklist_duplicate_guard") or {})
        if result.get("checklist_duplicate_message") is not None:
            detail["checklist_duplicate_message"] = str(result.get("checklist_duplicate_message") or "")
        if result.get("checklist_duplicate_reason") is not None:
            detail["checklist_duplicate_reason"] = str(result.get("checklist_duplicate_reason") or "")
        if result.get("checklist_duplicate_matched_task_id") is not None:
            detail["checklist_duplicate_matched_task_id"] = str(
                result.get("checklist_duplicate_matched_task_id") or ""
            )
        if result.get("checklist_duplicate_recommended_action") is not None:
            detail["checklist_duplicate_recommended_action"] = str(
                result.get("checklist_duplicate_recommended_action") or ""
            )
        return detail

    def checklist_task_action_bundle(self, task_id: str) -> dict[str, str] | None:
        detail = self.checklist_task_detail_metadata(task_id)
        if detail is None:
            return None
        return {
            "primary_action": str(detail.get("checklist_primary_action") or detail.get("selected_checklist_primary_action") or ""),
            "secondary_action": str(detail.get("checklist_secondary_action") or detail.get("selected_checklist_secondary_action") or ""),
            "tertiary_action": str(detail.get("checklist_tertiary_action") or detail.get("selected_checklist_tertiary_action") or ""),
            "edit_subject_action": str(detail.get("checklist_edit_subject_action") or detail.get("selected_checklist_edit_subject_action") or ""),
            "edit_description_action": str(detail.get("checklist_edit_description_action") or detail.get("selected_checklist_edit_description_action") or ""),
            "edit_owner_action": str(detail.get("checklist_edit_owner_action") or detail.get("selected_checklist_edit_owner_action") or ""),
            "edit_active_form_action": str(detail.get("checklist_edit_active_form_action") or detail.get("selected_checklist_edit_active_form_action") or ""),
            "edit_blocks_action": str(detail.get("checklist_edit_blocks_action") or detail.get("selected_checklist_edit_blocks_action") or ""),
            "edit_blocked_by_action": str(detail.get("checklist_edit_blocked_by_action") or detail.get("selected_checklist_edit_blocked_by_action") or ""),
            "edit_metadata_action": str(detail.get("checklist_edit_metadata_action") or detail.get("selected_checklist_edit_metadata_action") or ""),
            "target": str(detail.get("checklist_action_target") or detail.get("selected_checklist_target") or ""),
            "checklist_status": str(detail.get("checklist_status") or ""),
        }

    def describe_task_drift_detail(self, task_id: str) -> str:
        result = self.client.request(
            "session.view",
            {"session_id": self.session_id, "view": "task_drift_detail", "task_id": task_id},
        )
        return str(result.get("text", ""))

    def open_active_plan_advisor(self) -> str:
        return self._action_text("open_active_plan_advisor")

    def show_advisor_status(self) -> str:
        return self._action_text("show_advisor_status")

    def open_task_detail(self, task_id: str) -> str:
        return self._action_text("open_task_detail", args=task_id)

    def open_task_detail_advisor(self, task_id: str) -> str:
        return self._action_text("open_task_detail_advisor", args=task_id)

    def open_task_drift_detail(self, task_id: str) -> str:
        return self._action_text("open_task_drift_detail", args=task_id)

    def open_phase_local_execution_task(self, task_id: str = "") -> str:
        return self._action_text("open_phase_local_execution_task", args=task_id)

    def open_phase_local_recent_drift_task(self, task_id: str = "") -> str:
        return self._action_text("open_phase_local_recent_drift_task", args=task_id)

    def focus_active_plan_timeline_task(self, task_id: str) -> str:
        return self._action_text("focus_active_plan_timeline_task", args=task_id)

    def clear_active_plan_timeline_focus(self) -> str:
        return self._action_text("clear_active_plan_timeline_focus")

    def describe_provider(self) -> str:
        return self._view_text("provider")

    def describe_config(self) -> str:
        return self._view_text("config")

    def describe_agents(self) -> str:
        return self._view_text("agents")

    def memory_surface_payload(self) -> dict[str, Any]:
        return dict(self._memory_metadata)

    def background_surface_payload(self) -> dict[str, Any]:
        return dict(getattr(self, "_background_metadata", {}))

    def background_registry_payload(self) -> dict[str, Any]:
        return dict(getattr(self, "_background_registry_metadata", {}))

    def background_handoff_payload(self) -> dict[str, Any]:
        return dict(getattr(self, "_background_handoff_metadata", {}))

    def skills_surface_payload(self) -> dict[str, Any]:
        return dict(getattr(self, "_skills_surface_metadata", {}))

    def plugin_surface_payload(self) -> dict[str, Any]:
        return dict(getattr(self, "_plugin_surface_metadata", {}))

    def status_surface_payload(self) -> dict[str, Any]:
        return dict(getattr(self, "_status_metadata", {}))

    def send_background_followup(self, bg_id: str, prompt: str = "") -> str:
        return self._action_text(
            "background_send_followup",
            bg_id=bg_id,
            prompt=prompt,
        )

    def queue_background_message(self, bg_id: str, prompt: str) -> str:
        return self._action_text(
            "background_queue_message",
            bg_id=bg_id,
            prompt=prompt,
        )

    def cancel_pending_background_followup(self, bg_id: str) -> str:
        return self._action_text(
            "background_cancel_pending_followup",
            bg_id=bg_id,
        )

    def clear_history(self) -> str:
        return self._action_text("clear_history")

    def describe_rewind(self, selector: str = "") -> str:
        result = self._action_result("describe_rewind", args=selector)
        return str(result.get("text", ""))

    def describe_rewind_payload(self, selector: str = "") -> dict[str, Any]:
        return dict(self._action_result("describe_rewind", args=selector))

    def rewind_boundary_preview_payload(self, selector: str = "1") -> dict[str, Any] | None:
        normalized = str(selector or "").strip() or "1"
        if self._rewind_preview_selector == normalized and self._rewind_preview_metadata:
            return dict(self._rewind_preview_metadata)
        result = self.describe_rewind_payload(f"show {normalized}")
        preview = {
            "selector_index": result.get("selector"),
            "boundary_id": result.get("boundary_id"),
            "boundary_kind": result.get("boundary_kind"),
            "boundary_kind_label": result.get("boundary_kind_label"),
            "created_at": result.get("created_at"),
            "trigger": result.get("trigger"),
            "trigger_reason": result.get("trigger_reason"),
            "summary": result.get("summary"),
            "rewindable": result.get("rewindable"),
            "message_count_before": result.get("message_count_before"),
            "message_count_after": result.get("message_count_after"),
            "context_summary_chars_before": result.get("context_summary_chars_before"),
            "context_summary_chars_after": result.get("context_summary_chars_after"),
            "snapshot_available": result.get("snapshot_available"),
            "snapshot_message_count": result.get("snapshot_message_count"),
            "snapshot_summary_chars": result.get("snapshot_summary_chars"),
            "target_boundary_id": result.get("target_boundary_id"),
            "target_boundary_kind": result.get("target_boundary_kind"),
            "target_boundary_kind_label": result.get("target_boundary_kind_label"),
            "old_session_id": result.get("old_session_id"),
            "new_session_id": result.get("new_session_id"),
            "lineage_summary": result.get("lineage_summary"),
            "restore_message_delta_current": result.get("restore_message_delta_current"),
            "restore_summary_chars_delta_current": result.get("restore_summary_chars_delta_current"),
            "restore_message_count_current": result.get("restore_message_count_current"),
            "restore_summary_chars_current": result.get("restore_summary_chars_current"),
            "targets_pre_compact_state": result.get("targets_pre_compact_state"),
            "targets_post_resume_state": result.get("targets_post_resume_state"),
            "restore_effect_summary": result.get("restore_effect_summary"),
            "workflow_surface_policy": result.get("workflow_surface_policy"),
            "show_action": result.get("show_action"),
            "apply_action": result.get("apply_action"),
        }
        if not preview.get("boundary_id"):
            return None
        self._rewind_preview_selector = normalized
        self._rewind_preview_metadata = dict(preview)
        return dict(preview)

    def rewind_to_boundary(self, selector: str) -> str:
        return self._action_text("rewind_to_boundary", args=selector)

    def clear_session_reset(self) -> str:
        result = self.client.request(
            "session.action",
            {"session_id": self.session_id, "action": "clear_session_reset"},
        )
        old_session_id = self.session_id
        new_session_id = str(result.get("session_id") or old_session_id)
        self.session_id = new_session_id
        self.state.session_id = new_session_id
        self.state.messages = []
        self.state.context_summary = None
        self._symbol_surface = None
        self._working_set_metadata = {}
        self._task_detail_cache.clear()
        self._sync_from_describe()
        text = str(result.get("text", ""))
        if old_session_id != new_session_id and text:
            return text
        return text

    def reload_project_context(self) -> str:
        return self._action_text("reload_project_context")

    def undo_last_change(self, args: str = "") -> str:
        return self._action_text("undo_last_change", args=args)

    def redo_last_undo(self, args: str = "") -> str:
        return self._action_text("redo_last_undo", args=args)

    def workspace_cleanup_preview(self) -> str:
        return self._action_text("workspace_cleanup_preview")

    def symbol_surface_primary_action(self) -> str:
        return self._action_text("symbol_surface_open_primary")

    def symbol_surface_secondary_action(self) -> str:
        return self._action_text("symbol_surface_open_secondary")

    def clear_symbol_surface(self) -> str:
        return self._action_text("clear_symbol_surface")

    def symbol_surface_select_next_match(self) -> str:
        return self._action_text("symbol_surface_select_next_match")

    def symbol_surface_select_prev_match(self) -> str:
        return self._action_text("symbol_surface_select_prev_match")

    def symbol_surface_select_next_definition(self) -> str:
        return self._action_text("symbol_surface_select_next_definition")

    def symbol_surface_select_prev_definition(self) -> str:
        return self._action_text("symbol_surface_select_prev_definition")

    def symbol_surface_select_next_reference(self) -> str:
        return self._action_text("symbol_surface_select_next_reference")

    def symbol_surface_select_prev_reference(self) -> str:
        return self._action_text("symbol_surface_select_prev_reference")

    def workspace_cleanup_apply(self, args: str) -> str:
        return self._action_text("workspace_cleanup_apply", args=args)

    def workspace_repair(self, args: str) -> str:
        return self._action_text("workspace_repair", args=args)

    def checklist_mark_in_progress(self, args: str) -> str:
        return self._action_text("checklist_mark_in_progress", args=args)

    def checklist_mark_completed(self, args: str) -> str:
        return self._action_text("checklist_mark_completed", args=args)

    def checklist_reopen(self, args: str) -> str:
        return self._action_text("checklist_reopen", args=args)

    def checklist_set_owner(self, args: str, value: str) -> str:
        return self._action_text("checklist_set_owner", args=args, value=value)

    def checklist_set_subject(self, args: str, value: str) -> str:
        return self._action_text("checklist_set_subject", args=args, value=value)

    def checklist_set_description(self, args: str, value: str) -> str:
        return self._action_text("checklist_set_description", args=args, value=value)

    def checklist_set_metadata(self, args: str, value: str) -> str:
        return self._action_text("checklist_set_metadata", args=args, value=value)

    def checklist_set_active_form(self, args: str, value: str) -> str:
        return self._action_text("checklist_set_active_form", args=args, value=value)

    def checklist_set_blocks(self, args: str, value: str) -> str:
        return self._action_text("checklist_set_blocks", args=args, value=value)

    def checklist_set_blocked_by(self, args: str, value: str) -> str:
        return self._action_text("checklist_set_blocked_by", args=args, value=value)

    def recent_change_entries(self, limit: int = 5) -> list[str]:
        result = self.client.request(
            "session.change_view",
            {
                "session_id": self.session_id,
                "view": "entries",
                "limit": limit,
                "redo": False,
            },
        )
        return [str(item) for item in result.get("entries", [])]

    def recent_redo_entries(self, limit: int = 5) -> list[str]:
        result = self.client.request(
            "session.change_view",
            {
                "session_id": self.session_id,
                "view": "entries",
                "limit": limit,
                "redo": True,
            },
        )
        return [str(item) for item in result.get("entries", [])]

    def selected_change_file_count(self, *, index: int = 0, limit: int = 5, redo: bool = False) -> int:
        result = self.client.request(
            "session.change_view",
            {
                "session_id": self.session_id,
                "view": "file_count",
                "index": index,
                "limit": limit,
                "redo": redo,
            },
        )
        return int(result.get("count", 0))

    def selected_change_detail(
        self,
        *,
        index: int = 0,
        file_index: int = 0,
        limit: int = 5,
        redo: bool = False,
    ) -> str:
        result = self.client.request(
            "session.change_view",
            {
                "session_id": self.session_id,
                "view": "detail",
                "index": index,
                "file_index": file_index,
                "limit": limit,
                "redo": redo,
            },
        )
        return str(result.get("text", ""))

    def close(self) -> None:
        try:
            self.client.request("bridge.unsubscribe", {"session_id": self.session_id})
        except Exception:  # noqa: BLE001
            pass
        self.client.close()

    def _view_text(self, view: str) -> str:
        result = self.client.request(
            "session.view",
            {"session_id": self.session_id, "view": view},
        )
        return str(result.get("text", ""))

    def _action_text(self, action: str, **params) -> str:
        result = self._action_result(action, **params)
        return str(result.get("text", ""))

    def _action_result(self, action: str, **params) -> dict[str, Any]:
        result = self.client.request(
            "session.action",
            {"session_id": self.session_id, "action": action, **params},
        )
        if action == "describe_rewind":
            args = str(params.get("args") or "").strip()
            if str(result.get("rewind_mode") or "").strip() == "show" and result.get("boundary_id"):
                selector = ""
                if args.lower().startswith("show "):
                    selector = args.split(" ", 1)[1].strip()
                self._rewind_preview_selector = selector or str(result.get("selector") or "").strip() or None
                self._rewind_preview_metadata = {
                    "selector_index": result.get("selector"),
                    "boundary_id": result.get("boundary_id"),
                    "boundary_kind": result.get("boundary_kind"),
                    "boundary_kind_label": result.get("boundary_kind_label"),
                    "created_at": result.get("created_at"),
                    "trigger": result.get("trigger"),
                    "trigger_reason": result.get("trigger_reason"),
                    "summary": result.get("summary"),
                    "rewindable": result.get("rewindable"),
                    "message_count_before": result.get("message_count_before"),
                    "message_count_after": result.get("message_count_after"),
                    "context_summary_chars_before": result.get("context_summary_chars_before"),
                    "context_summary_chars_after": result.get("context_summary_chars_after"),
                    "snapshot_available": result.get("snapshot_available"),
                    "snapshot_message_count": result.get("snapshot_message_count"),
                    "snapshot_summary_chars": result.get("snapshot_summary_chars"),
                    "target_boundary_id": result.get("target_boundary_id"),
                    "target_boundary_kind": result.get("target_boundary_kind"),
                    "target_boundary_kind_label": result.get("target_boundary_kind_label"),
                    "old_session_id": result.get("old_session_id"),
                    "new_session_id": result.get("new_session_id"),
                    "lineage_summary": result.get("lineage_summary"),
                    "restore_message_delta_current": result.get("restore_message_delta_current"),
                    "restore_summary_chars_delta_current": result.get("restore_summary_chars_delta_current"),
                    "restore_message_count_current": result.get("restore_message_count_current"),
                    "restore_summary_chars_current": result.get("restore_summary_chars_current"),
                    "targets_pre_compact_state": result.get("targets_pre_compact_state"),
                    "targets_post_resume_state": result.get("targets_post_resume_state"),
                    "restore_effect_summary": result.get("restore_effect_summary"),
                    "workflow_surface_policy": result.get("workflow_surface_policy"),
                    "show_action": result.get("show_action"),
                    "apply_action": result.get("apply_action"),
                }
        self._sync_from_describe()
        self._task_detail_cache.clear()
        if action == "rewind_to_boundary":
            self._rewind_preview_selector = None
            self._rewind_preview_metadata = {}
        return dict(result)

    def _task_detail_view_result(self, task_id: str, *, force: bool = False) -> dict[str, Any]:
        if not force:
            cached = self._task_detail_cache.get(task_id)
            if cached is not None:
                return dict(cached)
        result = self.client.request(
            "session.view",
            {"session_id": self.session_id, "view": "task_detail", "task_id": task_id},
        )
        self._task_detail_cache[task_id] = dict(result)
        return dict(result)

    def _sync_metadata(self, result: dict[str, Any]) -> None:
        message_count = result.get("message_count")
        if isinstance(message_count, int):
            self.state.messages = [{} for _ in range(message_count)]
        context_summary = result.get("context_summary")
        self.state.context_summary = str(context_summary) if context_summary else None
        self._sync_working_set_metadata(result)
        self._sync_memory_metadata(result)
        self._sync_skills_surface_metadata(result)
        self._sync_plugin_surface_metadata(result)

    def _sync_from_describe(self) -> None:
        described = self.client.request("session.describe", {"session_id": self.session_id})
        if isinstance(described.get("session_id"), str) and described["session_id"]:
            self.session_id = str(described["session_id"])
            self.state.session_id = self.session_id
        self.state.messages = [{} for _ in range(int(described.get("message_count", 0)))]
        context_summary = described.get("context_summary")
        self.state.context_summary = str(context_summary) if context_summary else None
        self._sync_workspace_metadata(described)
        self._sync_execution_contract_metadata(described)
        self._sync_task_surface_counts(described)
        self._sync_checklist_duplicate_guard_metadata(described)
        self._sync_symbol_surface_metadata(described)
        self._sync_working_set_metadata(described)
        self._sync_file_context_surface_metadata(described)
        self._sync_memory_metadata(described)
        self._sync_background_metadata(described)
        self._sync_background_registry_metadata(described)
        self._sync_background_handoff_metadata(described)
        self._sync_skills_surface_metadata(described)
        self._sync_plugin_surface_metadata(described)
        self._sync_status_metadata(described)
        self._sync_workspace_surface_metadata(described)

    def _sync_memory_metadata(self, payload: dict[str, Any]) -> None:
        self._memory_metadata = {
            "context_summary_present": bool(
                payload.get("memory_context_summary_present", payload.get("context_summary_present", False))
            ),
            "context_summary_chars": int(
                payload.get("memory_context_summary_chars", payload.get("context_summary_chars", 0))
                or 0
            ),
            "history_boundary_count": int(
                payload.get("memory_boundary_count", payload.get("history_boundary_count", 0)) or 0
            ),
            "rewindable_history_boundary_count": int(
                payload.get(
                    "memory_rewindable_boundary_count",
                    payload.get("rewindable_history_boundary_count", 0),
                )
                or 0
            ),
            "compact_boundary_count": int(
                payload.get("memory_compact_boundary_count", payload.get("compact_boundary_count", 0))
                or 0
            ),
            "last_history_boundary_kind": payload.get(
                "memory_last_boundary_kind",
                payload.get("last_history_boundary_kind"),
            ),
            "last_history_boundary_created_at": payload.get(
                "memory_last_boundary_created_at",
                payload.get("last_history_boundary_created_at"),
            ),
            "last_history_boundary_summary": payload.get(
                "memory_last_boundary_summary",
                payload.get("last_history_boundary_summary"),
            ),
            "latest_rewindable_boundary_id": payload.get(
                "memory_latest_rewindable_boundary_id",
                payload.get("latest_rewindable_boundary_id"),
            ),
            "latest_rewindable_boundary_kind": payload.get(
                "memory_latest_rewindable_boundary_kind",
                payload.get("latest_rewindable_boundary_kind"),
            ),
            "latest_rewindable_boundary_created_at": payload.get(
                "memory_latest_rewindable_boundary_created_at",
                payload.get("latest_rewindable_boundary_created_at"),
            ),
            "latest_rewindable_boundary_summary": payload.get(
                "memory_latest_rewindable_boundary_summary",
                payload.get("latest_rewindable_boundary_summary"),
            ),
            "default_rewind_selector": payload.get(
                "memory_default_rewind_selector",
                payload.get("default_rewind_selector"),
            ),
            "rewind_show_action": payload.get(
                "memory_rewind_show_action",
                payload.get("rewind_show_action"),
            ),
            "rewind_apply_action": payload.get(
                "memory_rewind_apply_action",
                payload.get("rewind_apply_action"),
            ),
            "compaction_state": payload.get(
                "memory_compaction_state",
                payload.get("compaction_state"),
            ),
            "would_compact": bool(
                payload.get("memory_would_compact", payload.get("would_compact", False))
            ),
            "should_warn": bool(
                payload.get("memory_should_warn", payload.get("should_warn", False))
            ),
            "should_auto_compact": bool(
                payload.get("memory_should_auto_compact", payload.get("should_auto_compact", False))
            ),
            "compaction_reason": payload.get(
                "memory_compaction_reason",
                payload.get("compaction_reason"),
            ),
            "message_count": int(
                payload.get("memory_message_count", payload.get("message_count", 0)) or 0
            ),
            "message_limit": int(
                payload.get("memory_message_limit", payload.get("message_limit", 0)) or 0
            ),
            "warning_message_threshold": int(
                payload.get(
                    "memory_warning_message_threshold",
                    payload.get("warning_message_threshold", 0),
                )
                or 0
            ),
            "context_summary_limit": int(
                payload.get(
                    "memory_context_summary_limit",
                    payload.get("context_summary_limit", 0),
                )
                or 0
            ),
            "warning_summary_threshold": int(
                payload.get(
                    "memory_warning_summary_threshold",
                    payload.get("warning_summary_threshold", 0),
                )
                or 0
            ),
            "auto_summary_threshold": int(
                payload.get(
                    "memory_auto_summary_threshold",
                    payload.get("auto_summary_threshold", 0),
                )
                or 0
            ),
            "compact_preview_action": payload.get(
                "memory_compact_preview_action",
                payload.get("compact_preview_action"),
            ),
            "compact_apply_action": payload.get(
                "memory_compact_apply_action",
                payload.get("compact_apply_action"),
            ),
            "memory_budget_state": payload.get("memory_budget_state"),
            "memory_budget_reason": payload.get("memory_budget_reason"),
            "memory_context_tokens_estimated": payload.get("memory_context_tokens_estimated"),
            "memory_context_percentage": payload.get("memory_context_percentage"),
            "memory_context_token_source": payload.get("memory_context_token_source"),
            "memory_last_turn_token_count": payload.get("memory_last_turn_token_count"),
            "memory_last_turn_token_source": payload.get("memory_last_turn_token_source"),
            "memory_provider_usage_seen": payload.get("memory_provider_usage_seen"),
            "memory_budget_pressure": payload.get("memory_budget_pressure"),
            "memory_compact_lifecycle": payload.get("memory_compact_lifecycle"),
            "memory_latest_compact_trigger": payload.get("memory_latest_compact_trigger"),
            "memory_latest_compact_reason": payload.get("memory_latest_compact_reason"),
            "memory_latest_compact_summary": payload.get("memory_latest_compact_summary"),
            "memory_tool_result_replacements": payload.get("memory_tool_result_replacements"),
            "memory_tool_result_artifacts": payload.get("memory_tool_result_artifacts"),
            "memory_replacement_aware_compaction": payload.get("memory_replacement_aware_compaction"),
            "memory_should_stop": payload.get("memory_should_stop"),
            "memory_last_operation": payload.get("memory_last_operation"),
            "memory_last_operation_summary": payload.get("memory_last_operation_summary"),
            "memory_last_operation_messages": payload.get("memory_last_operation_messages"),
            "memory_last_operation_context_summary": payload.get(
                "memory_last_operation_context_summary"
            ),
            "memory_last_operation_session_identity": payload.get(
                "memory_last_operation_session_identity"
            ),
            "memory_last_operation_history_boundaries": payload.get(
                "memory_last_operation_history_boundaries"
            ),
            "memory_last_operation_task_plan_file_focus": payload.get(
                "memory_last_operation_task_plan_file_focus"
            ),
            "memory_last_operation_advisor_review_state": payload.get(
                "memory_last_operation_advisor_review_state"
            ),
            "memory_last_operation_symbol_surface": payload.get(
                "memory_last_operation_symbol_surface"
            ),
            "memory_last_operation_advisor_configuration": payload.get(
                "memory_last_operation_advisor_configuration"
            ),
            "memory_last_operation_boundary_kind": payload.get(
                "memory_last_operation_boundary_kind"
            ),
            "memory_last_operation_boundary_id": payload.get(
                "memory_last_operation_boundary_id"
            ),
            "memory_last_operation_trigger": payload.get("memory_last_operation_trigger"),
            "memory_last_operation_trigger_reason": payload.get(
                "memory_last_operation_trigger_reason"
            ),
            "memory_context_summary_present": bool(
                payload.get("memory_context_summary_present", payload.get("context_summary_present", False))
            ),
            "memory_context_summary_chars": int(
                payload.get("memory_context_summary_chars", payload.get("context_summary_chars", 0))
                or 0
            ),
            "memory_boundary_count": int(
                payload.get("memory_boundary_count", payload.get("history_boundary_count", 0)) or 0
            ),
            "memory_rewindable_boundary_count": int(
                payload.get(
                    "memory_rewindable_boundary_count",
                    payload.get("rewindable_history_boundary_count", 0),
                )
                or 0
            ),
            "memory_compaction_state": payload.get(
                "memory_compaction_state",
                payload.get("compaction_state"),
            ),
            "memory_compaction_reason": payload.get(
                "memory_compaction_reason",
                payload.get("compaction_reason"),
            ),
            "memory_default_rewind_selector": payload.get(
                "memory_default_rewind_selector",
                payload.get("default_rewind_selector"),
            ),
            "memory_rewind_show_action": payload.get(
                "memory_rewind_show_action",
                payload.get("rewind_show_action"),
            ),
            "memory_rewind_apply_action": payload.get(
                "memory_rewind_apply_action",
                payload.get("rewind_apply_action"),
            ),
        }

    def _sync_background_metadata(self, payload: dict[str, Any]) -> None:
        background_session_id = str(payload.get("background_session_id") or "").strip()
        if not background_session_id:
            self._background_metadata = {}
            return
        self._background_metadata = {
            "background_session_id": background_session_id,
            "background_session_source": payload.get("background_session_source"),
            "background_continuation_category": payload.get("background_continuation_category"),
            "background_live_attachable": bool(payload.get("background_live_attachable", False)),
            "background_saved_resumable": bool(payload.get("background_saved_resumable", False)),
            "background_inactive_only": bool(payload.get("background_inactive_only", False)),
            "background_primary_action": payload.get("background_primary_action"),
            "background_secondary_action": payload.get("background_secondary_action"),
            "background_attach_action": payload.get(
                "background_attach_action",
                payload.get("background_go_to_live_attach"),
            ),
            "background_resume_action": payload.get(
                "background_resume_action",
                payload.get("background_go_to_saved_resume"),
            ),
            "background_logs_action": payload.get("background_logs_action"),
            "background_history_action": payload.get("background_history_action"),
            "background_sessions_action": payload.get("background_sessions_action"),
            "background_pending_followup_count": int(
                payload.get("background_pending_followup_count", 0) or 0
            ),
            "background_pending_followup_summary": payload.get("background_pending_followup_summary"),
            "background_latest_followup_message": payload.get("background_latest_followup_message"),
            "background_latest_followup_mode": payload.get("background_latest_followup_mode"),
            "background_latest_followup_at": payload.get("background_latest_followup_at"),
            "background_send_followup_action": payload.get("background_send_followup_action"),
            "background_queue_message_action": payload.get("background_queue_message_action"),
            "background_cancel_pending_followup_action": payload.get(
                "background_cancel_pending_followup_action"
            ),
            "background_current_workflow_summary": payload.get("background_current_workflow_summary"),
            "background_task_surface_counts": dict(payload.get("background_task_surface_counts") or {}),
            "background_task_surface_summary": payload.get("background_task_surface_summary"),
            "background_background_execution_count": int(
                payload.get("background_background_execution_count", 0) or 0
            ),
            "background_active_plan_execution_count": int(
                payload.get("background_active_plan_execution_count", 0) or 0
            ),
            "background_primary_task": payload.get("background_primary_task"),
            "background_primary_task_action": payload.get("background_primary_task_action"),
            "background_recent_change_count": int(payload.get("background_recent_change_count", 0) or 0),
            "background_recent_activity": payload.get("background_recent_activity"),
            "background_recent_activity_kind": payload.get("background_recent_activity_kind"),
            "background_last_tool": payload.get("background_last_tool"),
            "background_last_tool_input": payload.get("background_last_tool_input"),
            "background_last_tool_summary": payload.get("background_last_tool_summary"),
            "background_token_count": payload.get("background_token_count"),
            "background_token_count_source": payload.get("background_token_count_source"),
            "background_tool_use_count": int(payload.get("background_tool_use_count", 0) or 0),
            "background_message_count": payload.get("background_message_count"),
            "background_progress_summary": payload.get("background_progress_summary"),
            "background_progress_updated_at": payload.get("background_progress_updated_at"),
            "background_completion_state": payload.get("background_completion_state"),
            "background_completion_summary": payload.get("background_completion_summary"),
            "background_failure_reason": payload.get("background_failure_reason"),
            "background_result_pointer": payload.get("background_result_pointer"),
            "background_transcript_pointer": payload.get("background_transcript_pointer"),
            "background_working_set_file_count": int(
                payload.get("background_working_set_file_count", 0) or 0
            ),
            "background_focused_file": payload.get("background_focused_file"),
            "background_focused_file_source": payload.get("background_focused_file_source"),
            "background_has_active_plan": bool(payload.get("background_has_active_plan", False)),
            "background_active_plan_id": payload.get("background_active_plan_id"),
            "background_active_plan_summary": payload.get("background_active_plan_summary"),
            "background_action_groups": payload.get("background_action_groups"),
            "background_action_order": payload.get("background_action_order"),
        }

    def _sync_background_registry_metadata(self, payload: dict[str, Any]) -> None:
        count = int(payload.get("background_registry_count", 0) or 0)
        entries_payload = payload.get("background_registry_entries")
        entries = (
            [dict(item) for item in entries_payload if isinstance(item, dict)]
            if isinstance(entries_payload, list)
            else []
        )
        if count <= 0 and not entries:
            self._background_registry_metadata = {}
            return
        self._background_registry_metadata = {
            "background_registry_count": count,
            "background_registry_entries": entries,
            "background_registry_selected_bg_id": payload.get("background_registry_selected_bg_id"),
            "background_registry_selected_status": payload.get("background_registry_selected_status"),
            "background_registry_selected_continuation_category": payload.get(
                "background_registry_selected_continuation_category"
            ),
            "background_registry_selected_workflow_summary": payload.get(
                "background_registry_selected_workflow_summary"
            ),
            "background_registry_selected_primary_task": payload.get(
                "background_registry_selected_primary_task"
            ),
            "background_registry_selected_active_plan_summary": payload.get(
                "background_registry_selected_active_plan_summary"
            ),
            "background_registry_selected_focused_file": payload.get(
                "background_registry_selected_focused_file"
            ),
            "background_registry_selected_recent_activity": payload.get(
                "background_registry_selected_recent_activity"
            ),
            "background_registry_selected_recent_activity_kind": payload.get(
                "background_registry_selected_recent_activity_kind"
            ),
            "background_registry_selected_progress_summary": payload.get(
                "background_registry_selected_progress_summary"
            ),
            "background_registry_selected_last_tool_input": payload.get(
                "background_registry_selected_last_tool_input"
            ),
            "background_registry_selected_last_tool_summary": payload.get(
                "background_registry_selected_last_tool_summary"
            ),
            "background_registry_selected_token_count": payload.get(
                "background_registry_selected_token_count"
            ),
            "background_registry_selected_token_count_source": payload.get(
                "background_registry_selected_token_count_source"
            ),
            "background_registry_selected_completion_state": payload.get(
                "background_registry_selected_completion_state"
            ),
            "background_registry_selected_completion_summary": payload.get(
                "background_registry_selected_completion_summary"
            ),
            "background_registry_selected_pending_followup_count": int(
                payload.get("background_registry_selected_pending_followup_count", 0) or 0
            ),
            "background_registry_selected_pending_followup_summary": payload.get(
                "background_registry_selected_pending_followup_summary"
            ),
            "background_registry_selected_latest_followup_message": payload.get(
                "background_registry_selected_latest_followup_message"
            ),
            "background_registry_selected_latest_followup_mode": payload.get(
                "background_registry_selected_latest_followup_mode"
            ),
            "background_registry_primary_action": payload.get("background_registry_primary_action"),
            "background_registry_secondary_action": payload.get(
                "background_registry_secondary_action"
            ),
            "background_registry_attach_action": payload.get("background_registry_attach_action"),
            "background_registry_resume_action": payload.get("background_registry_resume_action"),
            "background_registry_logs_action": payload.get("background_registry_logs_action"),
            "background_registry_send_followup_action": payload.get(
                "background_registry_send_followup_action"
            ),
            "background_registry_queue_message_action": payload.get(
                "background_registry_queue_message_action"
            ),
            "background_registry_cancel_pending_followup_action": payload.get(
                "background_registry_cancel_pending_followup_action"
            ),
            "background_registry_selection_strategy": payload.get(
                "background_registry_selection_strategy"
            ),
        }

    def _sync_background_handoff_metadata(self, payload: dict[str, Any]) -> None:
        count = int(payload.get("background_handoff_count", 0) or 0)
        entries_payload = payload.get("background_handoff_entries")
        entries = (
            [dict(item) for item in entries_payload if isinstance(item, dict)]
            if isinstance(entries_payload, list)
            else []
        )
        if count <= 0 and not entries:
            self._background_handoff_metadata = {}
            return
        self._background_handoff_metadata = {
            "background_handoff_count": count,
            "background_handoff_entries": entries,
            "background_handoff_selected_bg_id": payload.get("background_handoff_selected_bg_id"),
            "background_handoff_selected_completion_state": payload.get(
                "background_handoff_selected_completion_state"
            ),
            "background_handoff_selected_completion_summary": payload.get(
                "background_handoff_selected_completion_summary"
            ),
            "background_handoff_selected_failure_reason": payload.get(
                "background_handoff_selected_failure_reason"
            ),
            "background_handoff_selected_primary_task": payload.get(
                "background_handoff_selected_primary_task"
            ),
            "background_handoff_transcript_action": payload.get(
                "background_handoff_transcript_action"
            ),
            "background_handoff_task_action": payload.get("background_handoff_task_action"),
            "background_handoff_changes_action": payload.get(
                "background_handoff_changes_action"
            ),
            "background_handoff_resume_action": payload.get("background_handoff_resume_action"),
            "background_handoff_selection_strategy": payload.get(
                "background_handoff_selection_strategy"
            ),
        }

    def _sync_plugin_surface_metadata(self, payload: dict[str, Any]) -> None:
        surface = payload.get("plugin_surface")
        if isinstance(surface, dict) and surface:
            self._plugin_surface_metadata = dict(surface)
            return
        self._plugin_surface_metadata = {}

    def _sync_skills_surface_metadata(self, payload: dict[str, Any]) -> None:
        surface = payload.get("skills_surface")
        if isinstance(surface, dict) and surface:
            self._skills_surface_metadata = dict(surface)
            return
        self._skills_surface_metadata = {}

    def _sync_status_metadata(self, payload: dict[str, Any]) -> None:
        self._sync_skills_surface_metadata(payload)
        self._sync_plugin_surface_metadata(payload)
        self._status_metadata = {
            "status_session_id": payload.get("status_session_id"),
            "status_provider": payload.get("status_provider"),
            "status_model": payload.get("status_model"),
            "status_advisor_model": payload.get("status_advisor_model"),
            "status_advisor_mode": payload.get("status_advisor_mode"),
            "status_mode": payload.get("status_mode"),
            "status_context_usage": payload.get("status_context_usage"),
            "status_context_usage_tokens": payload.get("status_context_usage_tokens"),
            "status_context_usage_max_tokens": payload.get("status_context_usage_max_tokens"),
            "status_context_usage_percentage": payload.get("status_context_usage_percentage"),
            "status_memory_summary": payload.get("status_memory_summary"),
            "status_memory_compaction": payload.get("status_memory_compaction"),
            "status_memory_last_operation": payload.get("status_memory_last_operation"),
            "status_memory_boundary_count": payload.get("status_memory_boundary_count"),
            "status_budget_state": payload.get("status_budget_state"),
            "status_budget_reason": payload.get("status_budget_reason"),
            "status_context_token_source": payload.get("status_context_token_source"),
            "status_last_turn_token_count": payload.get("status_last_turn_token_count"),
            "status_last_turn_token_source": payload.get("status_last_turn_token_source"),
            "status_provider_usage_seen": payload.get("status_provider_usage_seen"),
            "status_budget_pressure": payload.get("status_budget_pressure"),
            "status_compact_lifecycle": payload.get("status_compact_lifecycle"),
            "status_runtime_progress_summary": payload.get("status_runtime_progress_summary"),
            "status_runtime_progress_kind": payload.get("status_runtime_progress_kind"),
            "status_runtime_active_tool_name": payload.get("status_runtime_active_tool_name"),
            "status_runtime_active_tool_status": payload.get("status_runtime_active_tool_status"),
            "status_runtime_active_tool_input": payload.get("status_runtime_active_tool_input"),
            "status_runtime_last_tool_name": payload.get("status_runtime_last_tool_name"),
            "status_runtime_last_tool_status": payload.get("status_runtime_last_tool_status"),
            "status_runtime_last_tool_summary": payload.get("status_runtime_last_tool_summary"),
            "status_runtime_parallel_batch_active": payload.get("status_runtime_parallel_batch_active"),
            "status_runtime_parallel_batch_size": payload.get("status_runtime_parallel_batch_size"),
            "status_runtime_last_result_summary": payload.get("status_runtime_last_result_summary"),
            "status_runtime_compact_recovery_summary": payload.get(
                "status_runtime_compact_recovery_summary"
            ),
            "status_runtime_tool_result_replacement_summary": payload.get(
                "status_runtime_tool_result_replacement_summary"
            ),
            "status_runtime_tool_result_artifact_summary": payload.get(
                "status_runtime_tool_result_artifact_summary"
            ),
            "status_runtime_tool_result_microcompact_summary": payload.get(
                "status_runtime_tool_result_microcompact_summary"
            ),
            "status_runtime_skill_tool_fork_summary": payload.get(
                "status_runtime_skill_tool_fork_summary"
            ),
            "status_runtime_skill_tool_inline_summary": payload.get(
                "status_runtime_skill_tool_inline_summary"
            ),
            "status_runtime_skill_tool_context_summary": payload.get(
                "status_runtime_skill_tool_context_summary"
            ),
            "status_prompt_prefix_segment_count": payload.get(
                "status_prompt_prefix_segment_count"
            ),
            "status_prompt_prefix_stable_chars": payload.get(
                "status_prompt_prefix_stable_chars"
            ),
            "status_prompt_prefix_dynamic_tail_chars": payload.get(
                "status_prompt_prefix_dynamic_tail_chars"
            ),
            "status_prompt_prefix_attachment_count": payload.get(
                "status_prompt_prefix_attachment_count"
            ),
            "status_prompt_prefix_attachment_kinds": payload.get(
                "status_prompt_prefix_attachment_kinds"
            ),
            "status_prompt_prefix_attachment_summaries": payload.get(
                "status_prompt_prefix_attachment_summaries"
            ),
            "status_prompt_prefix_attachment_summary": payload.get(
                "status_prompt_prefix_attachment_summary"
            ),
            "status_prompt_prefix_attachment_mode": payload.get(
                "status_prompt_prefix_attachment_mode"
            ),
            "status_prompt_prefix_attachment_change_reason": payload.get(
                "status_prompt_prefix_attachment_change_reason"
            ),
            "status_plan_workflow_mode": payload.get("status_plan_workflow_mode"),
            "status_plan_workflow_phase_family": payload.get(
                "status_plan_workflow_phase_family"
            ),
            "status_plan_workflow_branch_identity": payload.get(
                "status_plan_workflow_branch_identity"
            ),
            "status_plan_workflow_branch_summary": payload.get(
                "status_plan_workflow_branch_summary"
            ),
            "status_plan_workflow_agent_count": payload.get(
                "status_plan_workflow_agent_count"
            ),
            "status_plan_workflow_explore_agent_count": payload.get(
                "status_plan_workflow_explore_agent_count"
            ),
            "status_plan_workflow_allowed_agent_names": payload.get(
                "status_plan_workflow_allowed_agent_names"
            ),
            "status_plan_workflow_invocation_boundary_summary": payload.get(
                "status_plan_workflow_invocation_boundary_summary"
            ),
            "status_plan_workflow_invocation_delegation_default": payload.get(
                "status_plan_workflow_invocation_delegation_default"
            ),
            "status_plan_instruction_state": payload.get("status_plan_instruction_state"),
            "status_plan_instruction_attachment_mode": payload.get(
                "status_plan_instruction_attachment_mode"
            ),
            "status_plan_instruction_attachment_summary": payload.get(
                "status_plan_instruction_attachment_summary"
            ),
            "status_plan_instruction_reentry_active": payload.get(
                "status_plan_instruction_reentry_active"
            ),
            "status_plan_instruction_exit_active": payload.get(
                "status_plan_instruction_exit_active"
            ),
            "status_prompt_prefix_cache_mode": payload.get(
                "status_prompt_prefix_cache_mode"
            ),
            "status_prompt_prefix_cache_supported": payload.get(
                "status_prompt_prefix_cache_supported"
            ),
            "status_prompt_prefix_cache_provider": payload.get(
                "status_prompt_prefix_cache_provider"
            ),
            "status_prompt_prefix_cache_summary": payload.get(
                "status_prompt_prefix_cache_summary"
            ),
            "status_prompt_prefix_cache_fallback_reason": payload.get(
                "status_prompt_prefix_cache_fallback_reason"
            ),
            "status_prompt_prefix_reduction_tier": payload.get(
                "status_prompt_prefix_reduction_tier"
            ),
            "status_prompt_prefix_planner_mode": payload.get(
                "status_prompt_prefix_planner_mode"
            ),
            "status_prompt_prefix_planner_reason": payload.get(
                "status_prompt_prefix_planner_reason"
            ),
            "status_prompt_prefix_planner_summary": payload.get(
                "status_prompt_prefix_planner_summary"
            ),
            "status_prompt_prefix_costed_planner_mode": payload.get(
                "status_prompt_prefix_costed_planner_mode"
            ),
            "status_prompt_prefix_costed_planner_reason": payload.get(
                "status_prompt_prefix_costed_planner_reason"
            ),
            "status_prompt_prefix_target_tokens_to_shed": payload.get(
                "status_prompt_prefix_target_tokens_to_shed"
            ),
            "status_prompt_prefix_estimated_input_tokens": payload.get(
                "status_prompt_prefix_estimated_input_tokens"
            ),
            "status_prompt_prefix_estimated_stable_prefix_tokens": payload.get(
                "status_prompt_prefix_estimated_stable_prefix_tokens"
            ),
            "status_prompt_prefix_estimated_dynamic_tail_tokens": payload.get(
                "status_prompt_prefix_estimated_dynamic_tail_tokens"
            ),
            "status_prompt_prefix_selected_candidate_count": payload.get(
                "status_prompt_prefix_selected_candidate_count"
            ),
            "status_prompt_prefix_selected_candidate_summary": payload.get(
                "status_prompt_prefix_selected_candidate_summary"
            ),
            "status_prompt_prefix_remaining_estimated_overage": payload.get(
                "status_prompt_prefix_remaining_estimated_overage"
            ),
            "status_prompt_prefix_prefix_damage_score": payload.get(
                "status_prompt_prefix_prefix_damage_score"
            ),
            "status_prompt_prefix_orchestration_mode": payload.get(
                "status_prompt_prefix_orchestration_mode"
            ),
            "status_prompt_prefix_orchestration_reason": payload.get(
                "status_prompt_prefix_orchestration_reason"
            ),
            "status_prompt_prefix_orchestration_selected_candidate_count": payload.get(
                "status_prompt_prefix_orchestration_selected_candidate_count"
            ),
            "status_prompt_prefix_orchestration_selected_candidate_summary": payload.get(
                "status_prompt_prefix_orchestration_selected_candidate_summary"
            ),
            "status_prompt_prefix_orchestration_remaining_estimated_overage": payload.get(
                "status_prompt_prefix_orchestration_remaining_estimated_overage"
            ),
            "status_prompt_prefix_orchestration_requires_full_compaction": payload.get(
                "status_prompt_prefix_orchestration_requires_full_compaction"
            ),
            "status_prompt_prefix_preserved_signature": payload.get(
                "status_prompt_prefix_preserved_signature"
            ),
            "status_prompt_prefix_preserved_segment_count": payload.get(
                "status_prompt_prefix_preserved_segment_count"
            ),
            "status_prompt_prefix_preserved_message_group_count": payload.get(
                "status_prompt_prefix_preserved_message_group_count"
            ),
            "status_prompt_prefix_downgraded_message_group_count": payload.get(
                "status_prompt_prefix_downgraded_message_group_count"
            ),
            "status_prompt_prefix_preserved_chars": payload.get(
                "status_prompt_prefix_preserved_chars"
            ),
            "status_prompt_prefix_cache_eligible_segment_count": payload.get(
                "status_prompt_prefix_cache_eligible_segment_count"
            ),
            "status_prompt_prefix_signature": payload.get(
                "status_prompt_prefix_signature"
            ),
            "status_prompt_prefix_previous_signature": payload.get(
                "status_prompt_prefix_previous_signature"
            ),
            "status_prompt_prefix_changed": payload.get("status_prompt_prefix_changed"),
            "status_prompt_prefix_change_reason": payload.get(
                "status_prompt_prefix_change_reason"
            ),
            "status_provider_view_assembly_summary": payload.get(
                "status_provider_view_assembly_summary"
            ),
            "status_background_summary": payload.get("status_background_summary"),
            "status_background_notification_count": payload.get("status_background_notification_count"),
            "status_background_latest_handoff": payload.get("status_background_latest_handoff"),
            "status_working_set_summary": payload.get("status_working_set_summary"),
            "status_working_set_file_count": payload.get("status_working_set_file_count"),
            "status_focused_file_summary": payload.get("status_focused_file_summary"),
            "status_focused_file_path": payload.get("status_focused_file_path"),
            "status_focused_file_source": payload.get("status_focused_file_source"),
            "status_plan_summary": payload.get("status_plan_summary"),
            "status_plan_goal": payload.get("status_plan_goal"),
            "status_task_summary": payload.get("status_task_summary"),
            "status_active_task_count": payload.get("status_active_task_count"),
            "status_task_surface_summary": payload.get("status_task_surface_summary"),
            "status_project_context_summary": payload.get("status_project_context_summary"),
            "status_project_context_reload_health": payload.get("status_project_context_reload_health"),
            "status_project_context_issue": payload.get("status_project_context_issue"),
            "status_skills_health": payload.get("status_skills_health"),
            "status_skill_registry_summary": payload.get("status_skill_registry_summary"),
            "status_skill_prompt_summary": payload.get("status_skill_prompt_summary"),
            "status_skill_reload_state": payload.get("status_skill_reload_state"),
            "status_skill_manual_overrides": payload.get("status_skill_manual_overrides"),
            "status_skill_diagnostics": payload.get("status_skill_diagnostics"),
            "status_plugins_health": payload.get("status_plugins_health"),
            "status_plugin_registry_summary": payload.get("status_plugin_registry_summary"),
            "status_plugin_reload_state": payload.get("status_plugin_reload_state"),
            "status_plugin_manual_overrides": payload.get("status_plugin_manual_overrides"),
            "status_mcp_health": payload.get("status_mcp_health"),
            "status_mcp_issue": payload.get("status_mcp_issue"),
            "status_permission_mode": payload.get("status_permission_mode"),
            "status_permission_summary": payload.get("status_permission_summary"),
            "status_permission_issue": payload.get("status_permission_issue"),
            "status_workspace_summary": payload.get("status_workspace_summary"),
            "status_workspace_mode": payload.get("status_workspace_mode"),
            "status_workspace_health": payload.get("status_workspace_health"),
            "status_workspace_anomaly": payload.get("status_workspace_anomaly"),
            "status_workspace_cleanup_status": payload.get("status_workspace_cleanup_status"),
            "status_workspace_unavailable": payload.get("status_workspace_unavailable"),
            "status_runtime_health_alert": payload.get("status_runtime_health_alert"),
            "status_runtime_health_source": payload.get("status_runtime_health_source"),
            "status_action_groups": dict(payload.get("status_action_groups") or {}),
            "status_explicit_context_entry_count": payload.get("status_explicit_context_entry_count"),
            "status_unresolved_explicit_context_entry_count": payload.get(
                "status_unresolved_explicit_context_entry_count"
            ),
            "status_tool_result_replacement_summary": payload.get(
                "status_tool_result_replacement_summary"
            ),
            "status_tool_result_artifact_summary": payload.get(
                "status_tool_result_artifact_summary"
            ),
            "status_next_actions": list(payload.get("status_next_actions") or []),
        }

    def _sync_workspace_metadata(self, payload: dict[str, Any]) -> None:
        self.state.original_cwd = (
            str(payload.get("original_cwd"))
            if payload.get("original_cwd") is not None
            else self.state.original_cwd
        )
        self.state.effective_cwd = (
            str(payload.get("effective_cwd"))
            if payload.get("effective_cwd") is not None
            else self.state.effective_cwd
        )
        if payload.get("workspace_mode") is not None:
            self.state.workspace_mode = str(payload.get("workspace_mode") or "main")
        self.state.workspace_label = (
            str(payload.get("workspace_label"))
            if payload.get("workspace_label") is not None
            else None
        )
        self.state.workspace_created_at = (
            str(payload.get("workspace_created_at"))
            if payload.get("workspace_created_at") is not None
            else None
        )
        self.state.workspace_health = str(payload.get("workspace_health", "healthy") or "healthy")
        self.state.workspace_cleanup_status = str(payload.get("workspace_cleanup_status", "none") or "none")
        self.state.workspace_cleanup_error = (
            str(payload.get("workspace_cleanup_error"))
            if payload.get("workspace_cleanup_error") is not None
            else None
        )
        self.state.workspace_unavailable = bool(payload.get("workspace_unavailable", False))
        self.state.workspace_unavailable_reason = (
            str(payload.get("workspace_unavailable_reason"))
            if payload.get("workspace_unavailable_reason") is not None
            else None
        )
        self.state.workspace_fallback_cwd = (
            str(payload.get("workspace_fallback_cwd"))
            if payload.get("workspace_fallback_cwd") is not None
            else None
        )
        self._workspace_action_bundle = {
            "primary_action": str(payload.get("workspace_primary_action") or "none"),
            "secondary_action": str(payload.get("workspace_secondary_action") or "none"),
            "tertiary_action": str(payload.get("workspace_tertiary_action") or "/workspaces list"),
            "target": str(payload.get("workspace_action_target") or self.session_id),
            "workspace_health": self.state.workspace_health,
        }

    def _sync_workspace_surface_metadata(self, payload: dict[str, Any]) -> None:
        surface = payload.get("workspace_surface")
        if isinstance(surface, dict) and surface:
            self._workspace_surface_metadata = dict(surface)
            return
        recommended_actions = [
            str(item).strip()
            for item in (payload.get("workspace_recommended_actions") or [])
            if str(item).strip()
        ]
        if not recommended_actions:
            for key in ("workspace_primary_action", "workspace_secondary_action", "workspace_tertiary_action"):
                action = str(payload.get(key) or "").strip()
                if action and action != "none" and action not in recommended_actions:
                    recommended_actions.append(action)
        self._workspace_surface_metadata = {
            "workspace_summary": payload.get("workspace_summary"),
            "workspace_mode": payload.get("workspace_mode"),
            "workspace_label": payload.get("workspace_label"),
            "workspace_health": payload.get("workspace_health"),
            "workspace_created_at": payload.get("workspace_created_at"),
            "workspace_original_cwd": payload.get("original_cwd"),
            "workspace_effective_cwd": payload.get("effective_cwd"),
            "workspace_effective_cwd_exists": payload.get("workspace_effective_cwd_exists"),
            "workspace_cleanup_status": payload.get("workspace_cleanup_status"),
            "workspace_cleanup_error": payload.get("workspace_cleanup_error"),
            "workspace_unavailable": payload.get("workspace_unavailable"),
            "workspace_unavailable_reason": payload.get("workspace_unavailable_reason"),
            "workspace_fallback_cwd": payload.get("workspace_fallback_cwd"),
            "workspace_anomaly_summary": payload.get("status_workspace_anomaly"),
            "workspace_recovery_summary": recommended_actions[0] if recommended_actions else None,
            "workspace_recommended_actions": recommended_actions,
            "workspace_action_bundle": {
                "primary_action": payload.get("workspace_primary_action"),
                "secondary_action": payload.get("workspace_secondary_action"),
                "tertiary_action": payload.get("workspace_tertiary_action"),
                "target": payload.get("workspace_action_target"),
                "workspace_health": payload.get("workspace_health"),
            },
            "workspace_action_groups": {
                "inspect_current_workspace": ["/workspaces current"],
                "inspect_workspace_inventory": ["/workspaces list"],
                "workspace_recovery": recommended_actions,
            },
        }

    def _sync_symbol_surface_metadata(self, payload: dict[str, Any]) -> None:
        surface_kind = payload.get("symbol_surface_kind")
        if surface_kind in {None, "", "none"}:
            self._symbol_surface = None
            return
        self._symbol_surface = {
            "surface_kind": str(surface_kind),
            "selected_symbol": str(payload.get("symbol_selected_symbol") or ""),
            "match_count": int(payload.get("symbol_match_count") or 0),
            "definition_count": int(payload.get("symbol_definition_count") or 0),
            "reference_count": int(payload.get("symbol_reference_count") or 0),
            "selected_match_index": payload.get("symbol_selected_match_index"),
            "selected_definition_index": payload.get("symbol_selected_definition_index"),
            "selected_reference_index": payload.get("symbol_selected_reference_index"),
            "matches": [dict(item) for item in payload.get("symbol_matches", []) if isinstance(item, dict)],
            "definitions": [
                dict(item) for item in payload.get("symbol_definitions", []) if isinstance(item, dict)
            ],
            "references": [
                dict(item) for item in payload.get("symbol_references", []) if isinstance(item, dict)
            ],
            "selected_definition": (
                dict(payload.get("symbol_selected_definition"))
                if isinstance(payload.get("symbol_selected_definition"), dict)
                else None
            ),
            "selected_reference": (
                dict(payload.get("symbol_selected_reference"))
                if isinstance(payload.get("symbol_selected_reference"), dict)
                else None
            ),
            "selected_navigation_target": (
                dict(payload.get("symbol_navigation_target"))
                if isinstance(payload.get("symbol_navigation_target"), dict)
                else None
            ),
            "symbol_primary_action": str(payload.get("symbol_primary_action") or "none"),
            "symbol_secondary_action": str(payload.get("symbol_secondary_action") or "none"),
            "symbol_tertiary_action": str(payload.get("symbol_tertiary_action") or "/symbol clear"),
            "symbol_action_target": str(payload.get("symbol_action_target") or ""),
        }

    def _sync_working_set_metadata(self, payload: dict[str, Any]) -> None:
        self._working_set_metadata = self._extract_file_context_payload(payload)

    def _sync_file_context_surface_metadata(self, payload: dict[str, Any]) -> None:
        surface = payload.get("file_context_surface")
        if isinstance(surface, dict) and surface:
            self._file_context_surface_metadata = dict(surface)
            return
        working_set = self._extract_file_context_payload(payload)
        self._file_context_surface_metadata = {
            "working_set": dict(working_set),
            "working_set_summary": None,
            "focused_file": {
                "source": payload.get("focused_file_context_source"),
                "scope": payload.get("focused_file_context_scope"),
                "index": payload.get("focused_file_context_index"),
                "file_count": payload.get("focused_file_context_file_count"),
                "path": payload.get("focused_file_context_path"),
                "scope_reasons": list(payload.get("focused_file_context_scope_reasons") or []),
                "context_origin": None,
                "has_related_change": bool(payload.get("focused_file_context_has_related_change", False)),
                "has_diff_hunks": bool(payload.get("focused_file_context_has_diff_hunks", False)),
                "is_context_only": bool(payload.get("focused_file_context_is_context_only", False)),
                "primary_target": payload.get("focused_file_context_primary_target"),
                "secondary_target": payload.get("focused_file_context_secondary_target"),
                "summary": payload.get("focused_file_context_summary"),
            },
            "explicit_context": {},
            "file_action_groups": {},
        }

    def _extract_file_context_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "file_context_scope": str(
                payload.get("file_context_scope")
                or payload.get("working_set_scope")
                or "session"
            ),
            "file_context_file_count": int(
                payload.get("file_context_file_count", payload.get("working_set_file_count", 0)) or 0
            ),
            "file_context_sources": [
                str(item)
                for item in (
                    payload.get("file_context_sources")
                    or payload.get("working_set_sources")
                    or []
                )
                if str(item).strip()
            ],
            "file_context_files": [
                dict(item)
                for item in (
                    payload.get("file_context_files")
                    or payload.get("working_set_files")
                    or []
                )
                if isinstance(item, dict)
            ],
            "file_context_primary_path": payload.get(
                "file_context_primary_path",
                payload.get("working_set_primary_path"),
            ),
            "file_context_primary_target": payload.get(
                "file_context_primary_target",
                payload.get("working_set_primary_target"),
            ),
            "file_context_primary_diff_targets": payload.get(
                "file_context_primary_diff_targets",
                payload.get("working_set_primary_diff_targets"),
            ),
        }

    def _sync_execution_contract_metadata(self, payload: dict[str, Any]) -> None:
        self.state.session_execution_mode = str(
            payload.get("session_execution_mode", self.state.session_execution_mode or "main") or "main"
        )
        self.state.session_command_policy_name = (
            str(payload.get("session_command_policy_name"))
            if payload.get("session_command_policy_name") is not None
            else None
        )
        self.state.session_command_policy_source = (
            str(payload.get("session_command_policy_source"))
            if payload.get("session_command_policy_source") is not None
            else None
        )
        self.state.session_command_policy_allowed_tool_names = [
            str(item)
            for item in payload.get("session_command_policy_allowed_tool_names", [])
            if item is not None
        ]
        self.state.session_command_policy_allowed_bash_prefixes = [
            str(item)
            for item in payload.get("session_command_policy_allowed_bash_prefixes", [])
            if item is not None
        ]
        self.state.session_command_policy_require_read_only_subagents = bool(
            payload.get("session_command_policy_require_read_only_subagents", False)
        )

    def _sync_checklist_duplicate_guard_metadata(self, payload: dict[str, Any]) -> None:
        guard = payload.get("checklist_duplicate_guard")
        if isinstance(guard, dict) and guard:
            self._checklist_duplicate_guard = dict(guard)
            return
        message = str(payload.get("checklist_duplicate_message") or "").strip()
        matched_task_id = str(payload.get("checklist_duplicate_matched_task_id") or "").strip()
        recommended_action = str(payload.get("checklist_duplicate_recommended_action") or "").strip()
        if not any((message, matched_task_id, recommended_action)):
            self._checklist_duplicate_guard = None
            return
        self._checklist_duplicate_guard = {
            "message": message,
            "matched_task_id": matched_task_id,
            "recommended_action": recommended_action,
        }

    def _sync_task_surface_counts(self, payload: dict[str, Any]) -> None:
        raw = payload.get("task_surface_counts")
        if not isinstance(raw, dict):
            return
        normalized: dict[str, int] = {}
        for key, value in raw.items():
            try:
                normalized[str(key)] = int(value)
            except (TypeError, ValueError):
                continue
        self._task_surface_counts = normalized

    def _handle_notification(self, payload: dict[str, Any]) -> None:
        notification = str(payload.get("notification") or "")
        event_payload = payload.get("event")
        if not isinstance(event_payload, dict):
            return
        seq = payload.get("seq")
        if isinstance(seq, int):
            self._event_cursor = seq
        if notification in {
            "session.approval_required",
            "session.approval_resolved",
            "session.question_required",
            "session.question_resolved",
        }:
            self._ingest_control_event(event_payload)
            return
        event = _runtime_event_from_payload(event_payload)
        if event is None:
            return
        if event.kind == "task_progress" and event.task_id:
            self._task_detail_cache.pop(event.task_id, None)
        sinks = []
        with self._sink_lock:
            if self._default_live_sink is not None:
                sinks.append(self._default_live_sink)
            if self._transient_live_sink is not None and self._transient_live_sink not in sinks:
                sinks.append(self._transient_live_sink)
        for sink in sinks:
            sink(event)

    def _temporary_sink(self, sink):
        proxy = self

        class _SinkContext:
            def __enter__(self_nonlocal):
                if sink is None:
                    return None
                with proxy._sink_lock:
                    proxy._transient_live_sink = sink
                return sink

            def __exit__(self_nonlocal, exc_type, exc, tb):
                with proxy._sink_lock:
                    proxy._transient_live_sink = None
                return False

        return _SinkContext()

    def _sync_pending_approval(self) -> None:
        result = self.client.request(
            "session.approval_status",
            {"session_id": self.session_id},
        )
        if not result.get("pending"):
            self.pending_approval = None
            self.pending_approval_id = None
            return
        approval = result.get("approval") or {}
        self.pending_approval = _approval_request_from_payload(approval)
        self.pending_approval_id = int(result.get("approval_id", 0))

    def _sync_pending_question(self) -> None:
        result = self.client.request(
            "session.question_status",
            {"session_id": self.session_id},
        )
        if not result.get("pending"):
            self.pending_question = None
            self.pending_question_id = None
            return
        payload = result.get("question_request") or {}
        self.pending_question = _question_request_from_payload(payload)
        self.pending_question_id = int(result.get("question_id", 0))

    def _ingest_control_event(self, event_payload: dict[str, Any]) -> None:
        kind = event_payload.get("kind")
        if kind == "approval_required":
            approval = event_payload.get("approval") or {}
            self.pending_approval = _approval_request_from_payload(approval)
            approval_id = event_payload.get("approval_id")
            self.pending_approval_id = int(approval_id) if approval_id is not None else None
            if self._approval_requested_handler is not None and self.pending_approval is not None:
                self._approval_requested_handler(self.pending_approval)
            return
        if kind == "approval_resolved":
            approval_result = event_payload.get("approval_result") or {}
            result = _approval_result_from_payload(approval_result)
            self.pending_approval = None
            self.pending_approval_id = None
            if self._approval_resolved_handler is not None:
                self._approval_resolved_handler(result)
            return
        if kind == "question_required":
            payload = event_payload.get("question_request") or {}
            self.pending_question = _question_request_from_payload(payload)
            question_id = event_payload.get("question_id")
            self.pending_question_id = int(question_id) if question_id is not None else None
            if self._question_requested_handler is not None and self.pending_question is not None:
                self._question_requested_handler(self.pending_question)
            return
        if kind == "question_resolved":
            payload = event_payload.get("question_response") or {}
            result = _question_response_from_payload(payload)
            self.pending_question = None
            self.pending_question_id = None
            if self._question_resolved_handler is not None:
                self._question_resolved_handler(result)

    def _handle_local_repl_command(self, prompt: str) -> tuple[bool, str | CommandExecution | None] | None:
        raw = prompt.strip()
        if raw == "/help":
            return True, REMOTE_REPL_COMMAND_HELP
        if raw == "/approve":
            return True, self.resolve_pending_approval(ApprovalResult(decision="allow", scope="once"))
        if raw == "/approve-session":
            return True, self.resolve_pending_approval(ApprovalResult(decision="allow", scope="session"))
        if raw == "/deny":
            return True, self.resolve_pending_approval(ApprovalResult(decision="deny", scope="once"))
        if raw == "/cancel-question":
            return True, self.resolve_pending_question(UserQuestionResponse(canceled=True))
        return None

    def _wait_for_pending_approval(self, *, timeout_sec: float) -> None:
        deadline = time() + timeout_sec
        while time() < deadline:
            self._sync_pending_approval()
            if self.pending_approval is not None and self.pending_approval_id is not None:
                return
            sleep(0.05)

    def _wait_for_pending_question(self, *, timeout_sec: float) -> None:
        deadline = time() + timeout_sec
        while time() < deadline:
            self._sync_pending_question()
            if self.pending_question is not None and self.pending_question_id is not None:
                return
            sleep(0.05)


def _approval_request_from_payload(payload: dict[str, Any]) -> ApprovalRequest:
    return ApprovalRequest(
        tool_name=str(payload.get("tool_name", "")),
        reason=str(payload.get("reason", "")),
        risk_level=str(payload.get("risk_level", "")),
        approval_key=(
            str(payload.get("approval_key"))
            if payload.get("approval_key") is not None
            else None
        ),
        details=str(payload.get("details", "")),
        command=str(payload.get("command", "")),
        target_paths=tuple(str(item) for item in payload.get("target_paths", []) if isinstance(item, str)),
        permission_rules=tuple(
            str(item) for item in payload.get("permission_rules", []) if isinstance(item, str)
        ),
        decision_reason=str(payload.get("decision_reason", "")),
    )


def _approval_result_from_payload(payload: dict[str, Any]) -> ApprovalResult:
    return ApprovalResult(
        decision=str(payload.get("decision", "deny")),
        scope=str(payload.get("scope", "once")),
    )


def _question_request_from_payload(payload: dict[str, Any]) -> UserQuestionRequest:
    questions: list[UserQuestion] = []
    for item in payload.get("questions", []):
        if not isinstance(item, dict):
            continue
        questions.append(
            UserQuestion(
                header=str(item.get("header", "")),
                question=str(item.get("question", "")),
                multi_select=bool(item.get("multi_select", False)),
                options=tuple(
                    QuestionOption(
                        label=str(option.get("label", "")),
                        description=str(option.get("description", "")),
                    )
                    for option in item.get("options", [])
                    if isinstance(option, dict)
                ),
            )
        )
    return UserQuestionRequest(questions=tuple(questions))


def _question_response_from_payload(payload: dict[str, Any]) -> UserQuestionResponse:
    answers = payload.get("answers") or {}
    if not isinstance(answers, dict):
        answers = {}
    return UserQuestionResponse(
        answers={str(key): str(value) for key, value in answers.items()},
        canceled=bool(payload.get("canceled", False)),
    )
