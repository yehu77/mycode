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
from .permissions import ApprovalRequest, ApprovalResult
from .runtime.events import RuntimeEvent
from .state import SessionState


def _runtime_event_from_payload(event_payload: dict[str, Any]) -> RuntimeEvent | None:
    kind = event_payload.get("kind")
    if kind not in {
        "assistant_text",
        "assistant_tool_call",
        "assistant_tool_result_ready",
        "plan_execution",
        "advisor",
        "advisor_review_started",
        "advisor_review_result",
        "advisor_revision_requested",
        "advisor_error",
        "context_compacted",
        "provider_retry",
        "tool_started",
        "tool_finished",
        "tool_failed",
        "tool_result",
    }:
        return None
    return RuntimeEvent(
        kind=kind,  # type: ignore[arg-type]
        message=str(event_payload.get("message", "")),
        tool_name=event_payload.get("tool_name"),
        tool_call_id=event_payload.get("tool_call_id"),
        duration_ms=event_payload.get("duration_ms"),
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
        self.permission_manager = _RemotePermissionManager()
        self._default_live_sink = None
        self._transient_live_sink = None
        self._approval_requested_handler = None
        self._approval_resolved_handler = None
        self._sink_lock = Lock()
        self.pending_approval: ApprovalRequest | None = None
        self.pending_approval_id: int | None = None
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

    def set_live_event_sink(self, sink) -> None:
        self._default_live_sink = sink

    def set_approval_handlers(self, requested_handler=None, resolved_handler=None) -> None:
        self._approval_requested_handler = requested_handler
        self._approval_resolved_handler = resolved_handler

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

    def describe_tasks(self) -> str:
        return self._view_text("tasks")

    def describe_active_plan(self) -> str:
        return self._view_text("active_plan")

    def describe_advisor(self) -> str:
        return self._view_text("advisor_status")

    def describe_provider(self) -> str:
        return self._view_text("provider")

    def describe_config(self) -> str:
        return self._view_text("config")

    def clear_history(self) -> None:
        self.client.request("session.action", {"session_id": self.session_id, "action": "clear_history"})
        self.state.messages = []
        self.state.context_summary = None

    def reload_project_context(self) -> str:
        return self._action_text("reload_project_context")

    def undo_last_change(self, args: str = "") -> str:
        return self._action_text("undo_last_change", args=args)

    def redo_last_undo(self, args: str = "") -> str:
        return self._action_text("redo_last_undo", args=args)

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
        result = self.client.request(
            "session.action",
            {"session_id": self.session_id, "action": action, **params},
        )
        self._sync_from_describe()
        return str(result.get("text", ""))

    def _sync_metadata(self, result: dict[str, Any]) -> None:
        message_count = result.get("message_count")
        if isinstance(message_count, int):
            self.state.messages = [{} for _ in range(message_count)]
        context_summary = result.get("context_summary")
        self.state.context_summary = str(context_summary) if context_summary else None

    def _sync_from_describe(self) -> None:
        described = self.client.request("session.describe", {"session_id": self.session_id})
        self.state.messages = [{} for _ in range(int(described.get("message_count", 0)))]

    def _handle_notification(self, payload: dict[str, Any]) -> None:
        notification = str(payload.get("notification") or "")
        event_payload = payload.get("event")
        if not isinstance(event_payload, dict):
            return
        seq = payload.get("seq")
        if isinstance(seq, int):
            self._event_cursor = seq
        if notification in {"session.approval_required", "session.approval_resolved"}:
            self._ingest_control_event(event_payload)
            return
        event = _runtime_event_from_payload(event_payload)
        if event is None:
            return
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
        return None

    def _wait_for_pending_approval(self, *, timeout_sec: float) -> None:
        deadline = time() + timeout_sec
        while time() < deadline:
            self._sync_pending_approval()
            if self.pending_approval is not None and self.pending_approval_id is not None:
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
    )


def _approval_result_from_payload(payload: dict[str, Any]) -> ApprovalResult:
    return ApprovalResult(
        decision=str(payload.get("decision", "deny")),
        scope=str(payload.get("scope", "once")),
    )
