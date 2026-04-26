from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event, Lock
from typing import Any, TextIO
import json

from ..commands import CommandExecution
from ..config import SessionConfig
from ..env_loader import load_dotenv
from ..permissions import ApprovalRequest, ApprovalResult
from ..runtime.events import RuntimeEvent
from ..runtime.headless import HeadlessRunResult
from ..session import Session
from ..session_factory import SessionFactory
from ..storage.transcript import get_session_path, list_transcripts
from ..tools.base import resolve_workspace_path

SERVICE_PROTOCOL = "pyclaude-stdio-service"
SERVICE_VERSION = "0.1"
SERVICE_SCHEMA_VERSION = 1
SERVICE_METHODS = (
    "ping",
    "service.hello",
    "session.create",
    "session.resume",
    "session.close",
    "session.list_open",
    "session.list_saved",
    "session.describe",
    "session.events",
    "session.ask",
    "session.command",
    "session.run_command",
    "session.view",
    "session.change_view",
    "session.action",
    "session.approval_status",
    "session.approval_respond",
    "symbol.locate",
    "symbol.references",
    "symbol.actions",
)


class ServiceError(Exception):
    def __init__(self, code: int, message: str, *, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {}


class SessionRecord:
    def __init__(self, session: Session, *, restored_from: Path | None = None) -> None:
        self.session = session
        self.restored_from = restored_from
        self._event_cursor = 0
        self._events: list[dict[str, Any]] = []
        self._subscribers: list[Any] = []
        self._approval_lock = Lock()
        self._pending_approval_id = 0
        self._pending_approval_request: ApprovalRequest | None = None
        self._pending_approval_event: Event | None = None
        self._pending_approval_result: ApprovalResult | None = None

    def append_event(self, event: RuntimeEvent) -> None:
        self._event_cursor += 1
        payload = {"seq": self._event_cursor, **_service_event_to_dict(event)}
        self._events.append(payload)
        for subscriber in list(self._subscribers):
            try:
                subscriber(payload)
            except Exception:  # noqa: BLE001
                continue

    def append_system_event(
        self,
        *,
        kind: str,
        message: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self._event_cursor += 1
        payload = {
            "seq": self._event_cursor,
            "kind": kind,
            "message": message,
            "tool_name": None,
            "tool_call_id": None,
            "duration_ms": None,
            "is_error": False,
        }
        if extra:
            payload.update(extra)
        self._events.append(payload)
        for subscriber in list(self._subscribers):
            try:
                subscriber(payload)
            except Exception:  # noqa: BLE001
                continue

    def get_events(self, *, after_seq: int = 0, limit: int = 100) -> dict[str, Any]:
        items = [item for item in self._events if int(item["seq"]) > after_seq][:limit]
        return {
            "events": items,
            "next_seq": items[-1]["seq"] if items else after_seq,
            "last_seq": self._event_cursor,
        }

    def add_subscriber(self, callback) -> None:
        self._subscribers.append(callback)

    def remove_subscriber(self, callback) -> None:
        self._subscribers = [item for item in self._subscribers if item is not callback]

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def request_approval(self, request: ApprovalRequest) -> ApprovalResult:
        pending_event = Event()
        with self._approval_lock:
            self._pending_approval_id += 1
            approval_id = self._pending_approval_id
            self._pending_approval_request = request
            self._pending_approval_event = pending_event
            self._pending_approval_result = None
        self.append_system_event(
            kind="approval_required",
            message=f'Approval required for {request.tool_name} ({request.risk_level})',
            extra={
                "approval_id": approval_id,
                "approval": _approval_request_to_dict(request),
            },
        )
        pending_event.wait()
        with self._approval_lock:
            result = self._pending_approval_result or ApprovalResult(decision="deny", scope="once")
            self._pending_approval_request = None
            self._pending_approval_event = None
            self._pending_approval_result = None
        self.append_system_event(
            kind="approval_resolved",
            message=f'Approval {result.decision} ({result.scope}) for {request.tool_name}',
            extra={
                "approval_id": approval_id,
                "approval_result": _approval_result_to_dict(result),
            },
        )
        return result

    def resolve_approval(self, *, approval_id: int, result: ApprovalResult) -> bool:
        with self._approval_lock:
            if (
                self._pending_approval_request is None
                or self._pending_approval_event is None
                or approval_id != self._pending_approval_id
            ):
                return False
            self._pending_approval_result = result
            self._pending_approval_event.set()
            return True

    def pending_approval_status(self) -> dict[str, Any]:
        with self._approval_lock:
            if self._pending_approval_request is None:
                return {"pending": False}
            return {
                "pending": True,
                "approval_id": self._pending_approval_id,
                "approval": _approval_request_to_dict(self._pending_approval_request),
            }

    def cancel_pending_approval(self) -> None:
        with self._approval_lock:
            if self._pending_approval_event is None:
                return
            self._pending_approval_result = ApprovalResult(decision="deny", scope="once")
            self._pending_approval_event.set()


class ServiceDispatcher:
    def __init__(self, config: SessionConfig) -> None:
        self.base_config = config
        self.session_factory = SessionFactory(load_mcp_from_config=True)
        self._sessions: dict[str, SessionRecord] = {}

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("id")
        try:
            method = request.get("method")
            if not isinstance(method, str) or not method:
                raise ServiceError(-32600, "Invalid request: missing method.")
            params = request.get("params") or {}
            if not isinstance(params, dict):
                raise ServiceError(-32602, "Invalid params: expected object.")
            result = self._dispatch(method, params)
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result,
                "meta": {
                    "protocol": SERVICE_PROTOCOL,
                    "version": SERVICE_VERSION,
                    "schema_version": SERVICE_SCHEMA_VERSION,
                },
            }
        except ServiceError as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": exc.code, "message": exc.message, "data": exc.data},
                "meta": {
                    "protocol": SERVICE_PROTOCOL,
                    "version": SERVICE_VERSION,
                    "schema_version": SERVICE_SCHEMA_VERSION,
                },
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32000,
                    "message": f"{type(exc).__name__}: {exc}",
                    "data": {"type": type(exc).__name__},
                },
                "meta": {
                    "protocol": SERVICE_PROTOCOL,
                    "version": SERVICE_VERSION,
                    "schema_version": SERVICE_SCHEMA_VERSION,
                },
            }

    def close(self) -> None:
        for record in self._sessions.values():
            record.cancel_pending_approval()
            record.session.close()
        self._sessions.clear()

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "service.hello":
            return {
                "protocol": SERVICE_PROTOCOL,
                "version": SERVICE_VERSION,
                "schema_version": SERVICE_SCHEMA_VERSION,
                "methods": list(SERVICE_METHODS),
                "capabilities": {
                    "sessions": True,
                    "events_polling": True,
                    "symbol_actions": True,
                },
            }
        if method == "ping":
            return {
                "ok": True,
                "cwd": str(self.base_config.cwd),
                "provider": self.base_config.provider,
                "protocol": SERVICE_PROTOCOL,
                "version": SERVICE_VERSION,
                "schema_version": SERVICE_SCHEMA_VERSION,
            }
        if method == "session.create":
            return self._session_create(params)
        if method == "session.resume":
            return self._session_resume(params)
        if method == "session.close":
            return self._session_close(params)
        if method == "session.list_open":
            return self._session_list_open(params)
        if method == "session.list_saved":
            return self._session_list_saved(params)
        if method == "session.describe":
            return self._session_describe(params)
        if method == "session.events":
            return self._session_events(params)
        if method == "session.ask":
            return self._session_ask(params)
        if method == "session.command":
            return self._session_command(params)
        if method == "session.run_command":
            return self._session_run_command(params)
        if method == "session.view":
            return self._session_view(params)
        if method == "session.change_view":
            return self._session_change_view(params)
        if method == "session.action":
            return self._session_action(params)
        if method == "session.approval_status":
            return self._session_approval_status(params)
        if method == "session.approval_respond":
            return self._session_approval_respond(params)
        if method == "symbol.locate":
            return self._symbol_locate(params)
        if method == "symbol.references":
            return self._symbol_references(params)
        if method == "symbol.actions":
            return self._symbol_actions(params)
        raise ServiceError(-32601, f'Method not found: "{method}".')

    def _session_create(self, params: dict[str, Any]) -> dict[str, Any]:
        config = self._build_session_config(params)
        restore_latest = bool(params.get("restore_latest", False))
        resume_session_id = params.get("resume_session_id")
        if resume_session_id is not None and not isinstance(resume_session_id, str):
            raise ServiceError(-32602, "resume_session_id must be a string.")
        try:
            session, restored_from = self.session_factory.create_or_restore_session(
                config,
                restore_latest=restore_latest,
                resume_session_id=resume_session_id,
            )
        except FileNotFoundError as exc:
            raise ServiceError(
                -32004,
                str(exc),
                data={"type": "session_not_found", "session_id": resume_session_id},
            ) from exc
        existing = self._sessions.pop(session.state.session_id, None)
        if existing is not None:
            existing.cancel_pending_approval()
            existing.session.close()
        record = SessionRecord(session, restored_from=restored_from)
        session.permission_manager.approval_handler = record.request_approval
        self._sessions[session.state.session_id] = record
        return {
            "session_id": session.state.session_id,
            "cwd": str(session.config.cwd),
            "provider": session.config.provider,
            "model": session.config.model,
            "restored_from": str(restored_from) if restored_from is not None else None,
            "event_cursor": 0,
        }

    def _session_resume(self, params: dict[str, Any]) -> dict[str, Any]:
        resume_session_id = params.get("resume_session_id")
        restore_latest = bool(params.get("restore_latest", False))
        if not restore_latest and not isinstance(resume_session_id, str):
            raise ServiceError(
                -32602,
                "resume requires resume_session_id or restore_latest=true.",
            )
        return self._session_create(
            {
                **params,
                "restore_latest": restore_latest,
                "resume_session_id": resume_session_id,
            }
        )

    def _session_close(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require_session_id(params)
        record = self._sessions.pop(session_id, None)
        if record is None:
            raise ServiceError(
                -32004,
                f'Unknown session "{session_id}".',
                data={"type": "session_not_found", "session_id": session_id},
            )
        record.append_system_event(kind="session_closed", message="Session closed.")
        record.cancel_pending_approval()
        record.session.close()
        return {"session_id": session_id, "closed": True}

    def _session_describe(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require_session_id(params)
        record = self._get_record(session_id)
        session = record.session
        return {
            "session_id": session.state.session_id,
            "cwd": str(session.config.cwd),
            "provider": session.config.provider,
            "model": session.config.model,
            "message_count": len(session.state.messages),
            "restored_from": str(record.restored_from) if record.restored_from is not None else None,
            "event_cursor": record._event_cursor,
            "subscriber_count": record.subscriber_count,
            "config": session.describe_config(),
        }

    def _session_list_open(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        sessions = []
        for session_id, record in self._sessions.items():
            session = record.session
            sessions.append(
                {
                    "session_id": session_id,
                    "cwd": str(session.config.cwd),
                    "provider": session.config.provider,
                    "model": session.config.model,
                    "message_count": len(session.state.messages),
                    "restored_from": str(record.restored_from) if record.restored_from is not None else None,
                    "event_cursor": record._event_cursor,
                    "subscriber_count": record.subscriber_count,
                }
            )
        return {"sessions": sessions}

    def _session_list_saved(self, params: dict[str, Any]) -> dict[str, Any]:
        cwd = params.get("cwd")
        if cwd is None:
            target_cwd = self.base_config.cwd
        elif isinstance(cwd, str):
            target_cwd = Path(cwd).resolve()
        else:
            raise ServiceError(-32602, "cwd must be a string.")
        limit = int(params.get("limit", 20))
        summaries = list_transcripts(target_cwd, limit=limit)
        return {
            "sessions": [
                {
                    "session_id": item.session_id,
                    "path": str(item.path),
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                    "provider": item.provider,
                    "model": item.model,
                    "cwd": item.cwd,
                    "message_count": item.message_count,
                    "context_summary_present": item.context_summary_present,
                }
                for item in summaries
            ]
        }

    def _session_events(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require_session_id(params)
        record = self._get_record(session_id)
        after_seq = int(params.get("after_seq", 0))
        limit = int(params.get("limit", 100))
        return {
            "session_id": session_id,
            **record.get_events(after_seq=after_seq, limit=limit),
        }

    def _session_ask(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require_session_id(params)
        record = self._get_record(session_id)
        session = record.session
        prompt = params.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ServiceError(-32602, "prompt must be a non-empty string.")
        allowed_tool_names = self._optional_string_list(params, "allowed_tool_names")
        allowed_bash_prefixes = self._optional_string_list(
            params,
            "allowed_bash_command_prefixes",
        )
        events: list[RuntimeEvent] = []
        ask_kwargs: dict[str, Any] = {
            "sink": lambda event: self._capture_event(record, events, event),
        }
        if allowed_tool_names is not None:
            ask_kwargs["allowed_tool_names"] = allowed_tool_names
        if allowed_bash_prefixes is not None:
            ask_kwargs["allowed_bash_command_prefixes"] = allowed_bash_prefixes
        output = session.ask(prompt, **ask_kwargs)
        result = HeadlessRunResult(
            output=output,
            events=events,
            session_id=session.state.session_id,
            cwd=str(session.config.cwd),
            message_count=len(session.state.messages),
            context_summary=session.state.context_summary,
            transcript_path=(
                get_session_path(session.config.cwd, session.state.session_id)
                if session.persist_transcript
                else None
            ),
            restored_from=record.restored_from,
        )
        return result.to_dict()

    def _session_command(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._get_session(params)
        prompt = params.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ServiceError(-32602, "prompt must be a non-empty string.")
        handled, output = session.handle_repl_command(prompt.strip())
        if isinstance(output, CommandExecution):
            return {
                "handled": handled,
                "output_kind": "command_execution",
                "execution": {
                    "prompt": output.prompt,
                    "allowed_tool_names": list(output.allowed_tool_names or ()),
                    "allowed_bash_command_prefixes": list(
                        output.allowed_bash_command_prefixes or ()
                    ),
                    "require_read_only_subagents": output.require_read_only_subagents,
                    "progress_message": output.progress_message,
                    "metadata": output.metadata,
                },
            }
        return {
            "handled": handled,
            "output_kind": "text" if output is not None else "none",
            "output": output,
        }

    def _session_run_command(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require_session_id(params)
        record = self._get_record(session_id)
        session = record.session
        execution_payload = params.get("execution")
        if not isinstance(execution_payload, dict):
            raise ServiceError(-32602, "execution must be an object.")
        prompt = execution_payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ServiceError(-32602, "execution.prompt must be a non-empty string.")
        events: list[RuntimeEvent] = []
        output = session.run_command(
            CommandExecution(
                prompt=prompt,
                allowed_tool_names=tuple(execution_payload.get("allowed_tool_names") or ()),
                allowed_bash_command_prefixes=tuple(
                    execution_payload.get("allowed_bash_command_prefixes") or ()
                ),
                require_read_only_subagents=bool(
                    execution_payload.get("require_read_only_subagents", False)
                ),
                progress_message=str(execution_payload.get("progress_message", "Running command")),
                metadata=(
                    execution_payload.get("metadata")
                    if isinstance(execution_payload.get("metadata"), dict)
                    else None
                ),
            ),
            sink=lambda event: self._capture_event(record, events, event),
        )
        result = HeadlessRunResult(
            output=output,
            events=events,
            session_id=session.state.session_id,
            cwd=str(session.config.cwd),
            message_count=len(session.state.messages),
            context_summary=session.state.context_summary,
            transcript_path=(
                get_session_path(session.config.cwd, session.state.session_id)
                if session.persist_transcript
                else None
            ),
            restored_from=record.restored_from,
        )
        return result.to_dict()

    def _session_view(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._get_session(params)
        view = params.get("view")
        if not isinstance(view, str) or not view:
            raise ServiceError(-32602, "view must be a non-empty string.")
        if view == "tools":
            text = session.describe_tools()
        elif view == "provider":
            text = session.describe_provider()
        elif view == "config":
            text = session.describe_config()
        elif view == "history":
            text = session.describe_history(limit=int(params.get("limit", 12)))
        elif view == "recent_changes":
            text = session.describe_recent_changes(limit=int(params.get("limit", 5)))
        elif view == "saved_sessions":
            text = session.describe_saved_sessions(limit=int(params.get("limit", 10)))
        elif view == "tasks":
            text = session.describe_tasks()
        elif view == "active_plan":
            text = session.describe_active_plan()
        elif view == "advisor_status":
            text = session.describe_advisor()
        elif view == "mcp_servers":
            text = session.describe_mcp_servers()
        elif view == "mcp_tools":
            text = session.describe_mcp_tools()
        elif view == "project_memory":
            text = session.describe_project_memory()
        elif view == "loaded_skills":
            text = session.describe_loaded_skills()
        elif view == "mcp_tool_diagnostic":
            text = session.describe_mcp_tool_diagnostic(
                self._require_string(params, "server"),
                self._require_string(params, "tool"),
                arguments=self._optional_json_object(params, "arguments"),
            )
        elif view == "mcp_verification":
            text = session.describe_mcp_verification(
                self._require_string(params, "server"),
                self._require_string(params, "tool"),
                arguments=self._optional_json_object(params, "arguments"),
            )
        else:
            raise ServiceError(
                -32602,
                f'Unsupported session view "{view}".',
                data={"type": "invalid_params", "field": "view"},
            )
        return {"view": view, "text": text}

    def _session_change_view(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._get_session(params)
        view = params.get("view")
        if not isinstance(view, str) or not view:
            raise ServiceError(-32602, "view must be a non-empty string.")
        redo = bool(params.get("redo", False))
        limit = int(params.get("limit", 5))
        if view == "entries":
            entries = (
                session.recent_redo_entries(limit=limit)
                if redo
                else session.recent_change_entries(limit=limit)
            )
            return {"view": view, "redo": redo, "entries": entries}
        if view == "file_count":
            return {
                "view": view,
                "redo": redo,
                "count": session.selected_change_file_count(
                    index=int(params.get("index", 0)),
                    limit=limit,
                    redo=redo,
                ),
            }
        if view == "detail":
            return {
                "view": view,
                "redo": redo,
                "text": session.selected_change_detail(
                    index=int(params.get("index", 0)),
                    file_index=int(params.get("file_index", 0)),
                    limit=limit,
                    redo=redo,
                ),
            }
        raise ServiceError(
            -32602,
            f'Unsupported change view "{view}".',
            data={"type": "invalid_params", "field": "view"},
        )

    def _session_action(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._get_session(params)
        action = params.get("action")
        if not isinstance(action, str) or not action:
            raise ServiceError(-32602, "action must be a non-empty string.")
        if action == "clear_history":
            session.clear_history()
            text = "Cleared in-memory conversation history for this session."
        elif action == "reload_project_context":
            text = session.reload_project_context()
        elif action == "reload_mcp_from_config":
            text = session.reload_mcp_from_config()
        elif action == "reconnect_mcp_server":
            text = session.reconnect_mcp_server(str(params.get("args", "")))
        elif action == "enable_skill":
            text = session.enable_skill(str(params.get("args", "")))
        elif action == "disable_skill":
            text = session.disable_skill(str(params.get("args", "")))
        elif action == "undo_last_change":
            text = session.undo_last_change(str(params.get("args", "")))
        elif action == "redo_last_undo":
            text = session.redo_last_undo(str(params.get("args", "")))
        else:
            raise ServiceError(
                -32602,
                f'Unsupported session action "{action}".',
                data={"type": "invalid_params", "field": "action"},
            )
        return {"action": action, "text": text}

    def _session_approval_status(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require_session_id(params)
        record = self._get_record(session_id)
        return {"session_id": session_id, **record.pending_approval_status()}

    def _session_approval_respond(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require_session_id(params)
        record = self._get_record(session_id)
        approval_id = int(params.get("approval_id", 0))
        decision = self._require_string(params, "decision")
        scope = str(params.get("scope", "once"))
        if decision not in {"allow", "deny"}:
            raise ServiceError(
                -32602,
                'decision must be "allow" or "deny".',
                data={"type": "invalid_params", "field": "decision"},
            )
        if scope not in {"once", "session"}:
            raise ServiceError(
                -32602,
                'scope must be "once" or "session".',
                data={"type": "invalid_params", "field": "scope"},
            )
        resolved = record.resolve_approval(
            approval_id=approval_id,
            result=ApprovalResult(decision=decision, scope=scope),
        )
        if not resolved:
            raise ServiceError(
                -32004,
                "No matching pending approval.",
                data={"type": "approval_not_found", "approval_id": approval_id},
            )
        return {
            "session_id": session_id,
            "approval_id": approval_id,
            "resolved": True,
        }

    def _symbol_locate(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._get_session(params)
        symbol = self._require_symbol(params)
        path = params.get("path", ".")
        max_results = int(params.get("max_results", 50))
        return session.locate_symbol(symbol, path=path, max_results=max_results).to_dict()

    def _symbol_references(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._get_session(params)
        symbol = self._require_symbol(params)
        path = params.get("path", ".")
        scope = params.get("scope", "auto")
        max_results = int(params.get("max_results", 100))
        return session.collect_references(
            symbol,
            path=path,
            scope=scope,
            max_results=max_results,
        ).to_dict()

    def _symbol_actions(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._get_session(params)
        symbol = self._require_symbol(params)
        path = params.get("path", ".")
        scope = params.get("scope", "workspace")
        max_definition_results = int(params.get("max_definition_results", 50))
        max_reference_results = int(params.get("max_reference_results", 100))
        return session.build_symbol_action_bundle(
            symbol,
            path=path,
            scope=scope,
            max_definition_results=max_definition_results,
            max_reference_results=max_reference_results,
        ).to_dict()

    def _build_session_config(self, params: dict[str, Any]) -> SessionConfig:
        cwd = params.get("cwd")
        mcp_config_path = params.get("mcp_config_path")
        if cwd is None:
            resolved_cwd = self.base_config.cwd
        elif isinstance(cwd, str):
            resolved_cwd = Path(cwd).resolve()
        else:
            raise ServiceError(-32602, "cwd must be a string.")
        load_dotenv(resolved_cwd / ".env")
        resolved_mcp_config = self.base_config.mcp_config_path
        if mcp_config_path is not None:
            if not isinstance(mcp_config_path, str):
                raise ServiceError(-32602, "mcp_config_path must be a string.")
            resolved_mcp_config = resolve_workspace_path(resolved_cwd, mcp_config_path)
        elif self.base_config.mcp_config_path and self.base_config.mcp_config_path.parent != resolved_cwd / ".pyclaude":
            resolved_mcp_config = self.base_config.mcp_config_path
        else:
            resolved_mcp_config = (resolved_cwd / ".pyclaude" / "mcp_servers.json").resolve()
        return replace(
            self.base_config,
            cwd=resolved_cwd,
            interactive=False,
            mcp_config_path=resolved_mcp_config,
        )

    def _get_session(self, params: dict[str, Any]) -> Session:
        session_id = self._require_session_id(params)
        record = self._sessions.get(session_id)
        if record is None:
            raise ServiceError(
                -32004,
                f'Unknown session "{session_id}".',
                data={"type": "session_not_found", "session_id": session_id},
            )
        return record.session

    def _get_record(self, session_id: str) -> SessionRecord:
        record = self._sessions.get(session_id)
        if record is None:
            raise ServiceError(
                -32004,
                f'Unknown session "{session_id}".',
                data={"type": "session_not_found", "session_id": session_id},
            )
        return record

    def _require_session_id(self, params: dict[str, Any]) -> str:
        session_id = params.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise ServiceError(
                -32602,
                "session_id must be a non-empty string.",
                data={"type": "invalid_params", "field": "session_id"},
            )
        return session_id

    def _require_symbol(self, params: dict[str, Any]) -> str:
        symbol = params.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise ServiceError(
                -32602,
                "symbol must be a non-empty string.",
                data={"type": "invalid_params", "field": "symbol"},
            )
        return symbol

    def _require_string(self, params: dict[str, Any], field: str) -> str:
        value = params.get(field)
        if not isinstance(value, str) or not value:
            raise ServiceError(
                -32602,
                f"{field} must be a non-empty string.",
                data={"type": "invalid_params", "field": field},
            )
        return value

    def _optional_json_object(self, params: dict[str, Any], field: str) -> dict[str, Any] | None:
        value = params.get(field)
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ServiceError(
                -32602,
                f"{field} must be an object.",
                data={"type": "invalid_params", "field": field},
            )
        return value

    def _optional_string_list(self, params: dict[str, Any], field: str) -> tuple[str, ...] | None:
        value = params.get(field)
        if value is None:
            return None
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ServiceError(
                -32602,
                f"{field} must be an array of strings.",
                data={"type": "invalid_params", "field": field},
            )
        return tuple(value)

    def _capture_event(
        self,
        record: SessionRecord,
        events: list[RuntimeEvent],
        event: RuntimeEvent,
    ) -> None:
        events.append(event)
        record.append_event(event)


class JsonRpcStdioService:
    def __init__(self, dispatcher: ServiceDispatcher, *, stdin: TextIO, stdout: TextIO) -> None:
        self.dispatcher = dispatcher
        self.stdin = stdin
        self.stdout = stdout

    def serve_forever(self) -> int:
        try:
            for raw_line in self.stdin:
                line = raw_line.strip()
                if not line:
                    continue
                response = self._handle_line(line)
                self.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                self.stdout.flush()
        finally:
            self.dispatcher.close()
        return 0

    def _handle_line(self, line: str) -> dict[str, Any]:
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": f"Parse error: {exc.msg}",
                    "data": {"type": "parse_error"},
                },
                "meta": {
                    "protocol": SERVICE_PROTOCOL,
                    "version": SERVICE_VERSION,
                    "schema_version": SERVICE_SCHEMA_VERSION,
                },
            }
        if not isinstance(request, dict):
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32600,
                    "message": "Invalid request: expected object.",
                    "data": {"type": "invalid_request"},
                },
                "meta": {
                    "protocol": SERVICE_PROTOCOL,
                    "version": SERVICE_VERSION,
                    "schema_version": SERVICE_SCHEMA_VERSION,
                },
            }
        return self.dispatcher.handle(request)


def _service_event_to_dict(event: RuntimeEvent) -> dict[str, Any]:
    return {
        "kind": event.kind,
        "message": event.message,
        "tool_name": event.tool_name,
        "tool_call_id": event.tool_call_id,
        "duration_ms": event.duration_ms,
        "is_error": event.is_error,
    }


def _approval_request_to_dict(request: ApprovalRequest) -> dict[str, Any]:
    return {
        "tool_name": request.tool_name,
        "reason": request.reason,
        "risk_level": request.risk_level,
        "approval_key": request.approval_key,
        "details": request.details,
    }


def _approval_result_to_dict(result: ApprovalResult) -> dict[str, Any]:
    return {
        "decision": result.decision,
        "scope": result.scope,
    }
