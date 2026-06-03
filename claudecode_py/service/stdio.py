from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event, Lock
from typing import Any, TextIO
import json

from ..commands import CommandExecution
from ..config import SessionConfig
from ..env_loader import load_dotenv
from ..interactions import QuestionOption, UserQuestion, UserQuestionRequest, UserQuestionResponse
from ..permissions import ApprovalRequest, ApprovalResult
from ..permission_display import (
    PermissionDisplayContext,
    permission_display_context_to_dict,
    render_approval_request_compact,
    render_approval_request_lines,
)
from ..runtime.events import RuntimeEvent
from ..runtime.headless import HeadlessRunResult
from ..session import Session, workspace_action_bundle, workspace_recommended_actions
from ..session_factory import SessionFactory
from ..storage.transcript import get_session_path, list_transcripts
from ..tools.base import resolve_workspace_path
from ..workspace.isolation import derive_workspace_health

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
    "session.question_status",
    "session.question_respond",
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
        self._question_lock = Lock()
        self._pending_question_id = 0
        self._pending_question_request: UserQuestionRequest | None = None
        self._pending_question_event: Event | None = None
        self._pending_question_result: UserQuestionResponse | None = None

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

    def request_questions(self, request: UserQuestionRequest) -> UserQuestionResponse:
        pending_event = Event()
        with self._question_lock:
            self._pending_question_id += 1
            question_id = self._pending_question_id
            self._pending_question_request = request
            self._pending_question_event = pending_event
            self._pending_question_result = None
        self.append_system_event(
            kind="question_required",
            message="User input required for structured questions.",
            extra={
                "question_id": question_id,
                "question_request": _question_request_to_dict(request),
            },
        )
        pending_event.wait()
        with self._question_lock:
            result = self._pending_question_result or UserQuestionResponse(canceled=True)
            self._pending_question_request = None
            self._pending_question_event = None
            self._pending_question_result = None
        self.append_system_event(
            kind="question_resolved",
            message="Structured questions answered." if not result.canceled else "Structured questions canceled.",
            extra={
                "question_id": question_id,
                "question_response": _question_response_to_dict(result),
            },
        )
        return result

    def resolve_questions(self, *, question_id: int, result: UserQuestionResponse) -> bool:
        with self._question_lock:
            if (
                self._pending_question_request is None
                or self._pending_question_event is None
                or question_id != self._pending_question_id
            ):
                return False
            self._pending_question_result = result
            self._pending_question_event.set()
            return True

    def pending_question_status(self) -> dict[str, Any]:
        with self._question_lock:
            if self._pending_question_request is None:
                return {"pending": False}
            return {
                "pending": True,
                "question_id": self._pending_question_id,
                "question_request": _question_request_to_dict(self._pending_question_request),
            }

    def cancel_pending_questions(self) -> None:
        with self._question_lock:
            if self._pending_question_event is None:
                return
            self._pending_question_result = UserQuestionResponse(canceled=True)
            self._pending_question_event.set()


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
            record.cancel_pending_questions()
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
        if method == "session.question_status":
            return self._session_question_status(params)
        if method == "session.question_respond":
            return self._session_question_respond(params)
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
        session.set_question_handler(record.request_questions)
        session.set_live_event_sink(record.append_event)
        self._sessions[session.state.session_id] = record
        return {
            "session_id": session.state.session_id,
            "cwd": str(session.config.cwd),
            "provider": session.config.provider,
            "model": session.config.model,
            "restored_from": str(restored_from) if restored_from is not None else None,
            "event_cursor": 0,
            **self._session_source_metadata_for_record(record),
            **self._workspace_metadata_for_session(session),
            **self._execution_contract_metadata_for_session(session),
            **self._planning_surface_metadata_for_session(session),
            **self._task_surface_metadata_for_session(session),
            **self._memory_metadata_for_session(session),
            **self._background_metadata_for_session(session),
            **self._background_registry_metadata_for_session(session),
            **self._background_handoff_metadata_for_session(session),
            **self._skills_metadata_for_session(session),
            **self._plugin_metadata_for_session(session),
            **self._status_metadata_for_session(session),
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
        record.cancel_pending_questions()
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
            **self._session_source_metadata_for_record(record),
            **self._workspace_metadata_for_session(session),
            **self._execution_contract_metadata_for_session(session),
            **self._planning_surface_metadata_for_session(session),
            **self._task_surface_metadata_for_session(session),
            **self._memory_metadata_for_session(session),
            **self._background_metadata_for_session(session),
            **self._background_registry_metadata_for_session(session),
            **self._background_handoff_metadata_for_session(session),
            **self._skills_metadata_for_session(session),
            **self._plugin_metadata_for_session(session),
            **self._status_metadata_for_session(session),
            **self._checklist_duplicate_metadata_for_session(session),
            **self._symbol_surface_metadata_for_session(session),
            **self._file_context_metadata_for_session(session),
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
                    **self._session_source_metadata_for_record(record),
                    **self._workspace_metadata_for_session(session),
                    **self._execution_contract_metadata_for_session(session),
                    **self._planning_surface_metadata_for_session(session),
                    **self._task_surface_metadata_for_session(session),
                    **self._memory_metadata_for_session(session),
                    **self._background_metadata_for_session(session),
                    **self._background_registry_metadata_for_session(session),
                    **self._background_handoff_metadata_for_session(session),
                    **self._skills_metadata_for_session(session),
                    **self._plugin_metadata_for_session(session),
                    **self._status_metadata_for_session(session),
                    **self._symbol_surface_metadata_for_session(session),
                    **self._file_context_metadata_for_session(session),
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
                    **self._session_source_metadata_for_summary(),
                    **self._workspace_metadata_for_summary(item),
                    **self._execution_contract_metadata_for_summary(item),
                    **self._planning_surface_metadata_for_summary(item),
                    **self._task_surface_metadata_for_summary(item),
                    **self._empty_focused_file_context_metadata(source="working_set"),
                }
                for item in summaries
            ]
        }

    def _workspace_metadata_for_session(self, session: Session) -> dict[str, Any]:
        effective_cwd = session.state.effective_cwd or str(session.config.cwd)
        action_bundle = session.current_workspace_action_bundle()
        return {
            "original_cwd": session.state.original_cwd,
            "effective_cwd": effective_cwd,
            "workspace_mode": session.state.workspace_mode,
            "workspace_label": session.state.workspace_label,
            "workspace_created_at": session.state.workspace_created_at,
            "workspace_health": session.state.workspace_health,
            "workspace_cleanup_status": session.state.workspace_cleanup_status,
            "workspace_cleanup_error": session.state.workspace_cleanup_error,
            "workspace_unavailable": session.state.workspace_unavailable,
            "workspace_unavailable_reason": session.state.workspace_unavailable_reason,
            "workspace_fallback_cwd": session.state.workspace_fallback_cwd,
            "workspace_recommended_actions": list(
                workspace_recommended_actions(
                    workspace_health=session.state.workspace_health,
                    workspace_label=session.state.workspace_label,
                    session_id=session.state.session_id,
                )
            ),
            "workspace_primary_action": action_bundle["primary_action"],
            "workspace_secondary_action": action_bundle["secondary_action"],
            "workspace_tertiary_action": action_bundle["tertiary_action"],
            "workspace_action_target": action_bundle["target"],
            "workspace_effective_cwd_exists": Path(effective_cwd).exists() if effective_cwd else None,
            "workspace_surface": dict(session.workspace_surface_payload()),
        }

    def _symbol_surface_metadata_for_session(self, session: Session) -> dict[str, Any]:
        payload = session.current_symbol_surface_payload()
        if not isinstance(payload, dict) or not payload:
            return {
                "symbol_surface_kind": None,
                "symbol_selected_symbol": None,
                "symbol_match_count": 0,
                "symbol_definition_count": 0,
                "symbol_reference_count": 0,
                "symbol_selected_match_index": None,
                "symbol_selected_definition_index": None,
                "symbol_selected_reference_index": None,
                "symbol_matches": [],
                "symbol_definitions": [],
                "symbol_references": [],
                "symbol_selected_definition": None,
                "symbol_selected_reference": None,
                "symbol_navigation_target": None,
                "symbol_primary_action": "none",
                "symbol_secondary_action": "none",
                "symbol_tertiary_action": "/symbol clear",
                "symbol_action_target": None,
            }
        action_bundle = session.current_symbol_surface_action_bundle() or {}
        return {
            "symbol_surface_kind": payload.get("surface_kind"),
            "symbol_selected_symbol": payload.get("selected_symbol") or payload.get("symbol"),
            "symbol_match_count": int(payload.get("match_count") or 0),
            "symbol_definition_count": int(payload.get("definition_count") or 0),
            "symbol_reference_count": int(payload.get("reference_count") or 0),
            "symbol_selected_match_index": payload.get("selected_match_index"),
            "symbol_selected_definition_index": payload.get("selected_definition_index"),
            "symbol_selected_reference_index": payload.get("selected_reference_index"),
            "symbol_matches": [dict(item) for item in payload.get("matches", []) if isinstance(item, dict)],
            "symbol_definitions": [
                dict(item) for item in payload.get("definitions", []) if isinstance(item, dict)
            ],
            "symbol_references": [
                dict(item) for item in payload.get("references", []) if isinstance(item, dict)
            ],
            "symbol_selected_definition": payload.get("selected_definition"),
            "symbol_selected_reference": payload.get("selected_reference"),
            "symbol_navigation_target": payload.get("selected_navigation_target")
            or payload.get("navigation_target"),
            "symbol_primary_action": action_bundle.get("primary_action", "none"),
            "symbol_secondary_action": action_bundle.get("secondary_action", "none"),
            "symbol_tertiary_action": action_bundle.get("tertiary_action", "/symbol clear"),
            "symbol_action_target": action_bundle.get("target"),
        }

    def _checklist_duplicate_metadata_for_session(self, session: Session) -> dict[str, Any]:
        payload = session.checklist_duplicate_guard_payload() or {}
        return {
            "checklist_duplicate_guard": dict(payload) if payload else None,
            "checklist_duplicate_message": str(payload.get("message") or "") or None,
            "checklist_duplicate_matched_task_id": str(payload.get("matched_task_id") or "") or None,
            "checklist_duplicate_recommended_action": str(payload.get("recommended_action") or "") or None,
        }

    def _execution_contract_metadata_for_session(self, session: Session) -> dict[str, Any]:
        payload = dict(session.execution_contract_payload())
        payload["session_execution_summary"] = self._execution_summary(
            session_execution_mode=str(payload.get("session_execution_mode") or "main"),
            session_command_policy_name=payload.get("session_command_policy_name"),
            session_command_policy_require_read_only_subagents=bool(
                payload.get("session_command_policy_require_read_only_subagents", False)
            ),
        )
        return payload

    def _planning_surface_metadata_for_session(self, session: Session) -> dict[str, Any]:
        payload = dict(session.planning_surface_payload())
        return {
            "active_planning_artifact_id": payload.get("active_planning_artifact_id"),
            "planning_artifact_count": int(payload.get("planning_artifact_count") or 0),
            "has_active_plan": bool(payload.get("has_active_plan", False)),
        }

    def _task_surface_metadata_for_session(self, session: Session) -> dict[str, Any]:
        counts = session.task_surface_counts_payload()
        return {
            "task_surface_counts": counts,
            "task_surface_total_count": sum(int(value) for value in counts.values()),
            "has_task_surface": any(int(value) > 0 for value in counts.values()),
        }

    def _memory_metadata_for_session(self, session: Session) -> dict[str, Any]:
        payload = dict(session.memory_surface_payload())
        payload["context_summary"] = session.state.context_summary
        return payload

    def _background_metadata_for_session(self, session: Session) -> dict[str, Any]:
        return dict(session.background_surface_payload())

    def _background_registry_metadata_for_session(self, session: Session) -> dict[str, Any]:
        return dict(session.background_registry_payload())

    def _background_handoff_metadata_for_session(self, session: Session) -> dict[str, Any]:
        return dict(session.background_handoff_payload())

    def _plugin_metadata_for_session(self, session: Session) -> dict[str, Any]:
        return {"plugin_surface": dict(session.plugin_surface_payload())}

    def _skills_metadata_for_session(self, session: Session) -> dict[str, Any]:
        return {"skills_surface": dict(session.skills_surface_payload())}

    def _status_metadata_for_session(self, session: Session) -> dict[str, Any]:
        return dict(session.status_surface_payload())

    def _file_context_metadata_for_session(self, session: Session) -> dict[str, Any]:
        payload = session.working_set_payload()
        return {
            "working_set_scope": payload.get("file_context_scope"),
            "working_set_file_count": int(payload.get("file_context_file_count") or 0),
            "working_set_sources": list(payload.get("file_context_sources") or []),
            "working_set_files": [
                dict(item) for item in payload.get("file_context_files", []) if isinstance(item, dict)
            ],
            "working_set_primary_path": payload.get("file_context_primary_path"),
            "working_set_primary_target": payload.get("file_context_primary_target"),
            "working_set_primary_diff_targets": payload.get("file_context_primary_diff_targets"),
            "file_context_surface": dict(session.file_context_surface_payload()),
            **self._focused_file_context_metadata_for_payload(payload, source="working_set"),
        }

    def _focused_file_context_metadata_for_payload(
        self,
        payload: dict[str, Any] | None,
        *,
        source: str | None,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return self._empty_focused_file_context_metadata()
        files = [item for item in payload.get("file_context_files", []) if isinstance(item, dict)]
        if not files:
            return self._empty_focused_file_context_metadata(source=source)
        primary = files[0]
        secondary_target = self._focused_file_context_secondary_target(primary)
        summary = self._focused_file_context_summary(
            source=source,
            path=str(primary.get("path") or "").strip() or None,
            primary_target=primary.get("target") or payload.get("file_context_primary_target"),
            secondary_target=secondary_target or payload.get("file_context_primary_diff_targets"),
        )
        return {
            "focused_file_context_source": source,
            "focused_file_context_scope": payload.get("file_context_scope"),
            "focused_file_context_index": 0,
            "focused_file_context_file_count": len(files),
            "focused_file_context_path": str(primary.get("path") or "").strip() or None,
            "focused_file_context_scope_reasons": [
                str(reason)
                for reason in (primary.get("scope_reasons") or [])
                if str(reason).strip()
            ],
            "focused_file_context_has_related_change": bool(primary.get("change_id")),
            "focused_file_context_has_diff_hunks": int(primary.get("diff_target_count") or 0) > 0,
            "focused_file_context_is_context_only": bool(primary.get("is_context_only")),
            "focused_file_context_primary_target": primary.get("target")
            or payload.get("file_context_primary_target"),
            "focused_file_context_secondary_target": secondary_target
            or payload.get("file_context_primary_diff_targets"),
            "focused_file_context_summary": summary,
        }

    def _empty_focused_file_context_metadata(
        self,
        *,
        source: str | None = None,
    ) -> dict[str, Any]:
        return {
            "focused_file_context_source": source,
            "focused_file_context_scope": None,
            "focused_file_context_index": 0,
            "focused_file_context_file_count": 0,
            "focused_file_context_path": None,
            "focused_file_context_scope_reasons": [],
            "focused_file_context_has_related_change": False,
            "focused_file_context_has_diff_hunks": False,
            "focused_file_context_is_context_only": False,
            "focused_file_context_primary_target": None,
            "focused_file_context_secondary_target": None,
            "focused_file_context_summary": None,
        }

    def _focused_file_context_secondary_target(self, item: dict[str, Any]) -> dict[str, Any] | None:
        diff_targets = item.get("diff_targets")
        if isinstance(diff_targets, dict):
            hunks = diff_targets.get("hunks")
            if isinstance(hunks, list):
                for hunk in hunks:
                    if isinstance(hunk, dict):
                        return dict(hunk)
            return dict(diff_targets)
        if isinstance(diff_targets, list):
            for hunk in diff_targets:
                if isinstance(hunk, dict):
                    return dict(hunk)
        return None

    def _focused_file_context_summary(
        self,
        *,
        source: str | None,
        path: str | None,
        primary_target: Any,
        secondary_target: Any,
    ) -> str | None:
        bits: list[str] = []
        if source:
            bits.append(f"source={source}")
        if path:
            bits.append(f"path={path}")
        primary_summary = self._format_target_summary(primary_target)
        if primary_summary:
            bits.append(f"primary={primary_summary}")
        secondary_summary = self._format_target_summary(secondary_target)
        if secondary_summary:
            bits.append(f"secondary={secondary_summary}")
        return "  ".join(bits) if bits else None

    def _format_target_summary(self, target: Any) -> str | None:
        if not isinstance(target, dict):
            return None
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
        return " ".join(parts) if parts else None

    def _workspace_metadata_for_summary(self, item) -> dict[str, Any]:
        effective_cwd = item.effective_cwd or item.cwd
        workspace_health = getattr(item, "workspace_health", None)
        cleanup_status = getattr(item, "workspace_cleanup_status", None) or "none"
        workspace_unavailable = bool(getattr(item, "workspace_unavailable", False))
        if not workspace_health:
            workspace_health = derive_workspace_health(
                workspace_mode=str(getattr(item, "workspace_mode", "main") or "main"),
                workspace_cleanup_status=str(cleanup_status or "none"),
                workspace_unavailable=workspace_unavailable,
            )
        recommended_actions = list(
            workspace_recommended_actions(
                workspace_health=str(workspace_health or "healthy"),
                workspace_label=getattr(item, "workspace_label", None),
                session_id=getattr(item, "session_id", None),
            )
        )
        action_bundle = workspace_action_bundle(
            workspace_health=str(workspace_health or "healthy"),
            workspace_label=getattr(item, "workspace_label", None),
            session_id=getattr(item, "session_id", None),
        )
        return {
            "original_cwd": item.original_cwd,
            "effective_cwd": effective_cwd,
            "workspace_mode": item.workspace_mode,
            "workspace_label": item.workspace_label,
            "workspace_created_at": item.workspace_created_at,
            "workspace_health": workspace_health,
            "workspace_cleanup_status": cleanup_status,
            "workspace_unavailable": workspace_unavailable,
            "workspace_unavailable_reason": getattr(item, "workspace_unavailable_reason", None),
            "workspace_fallback_cwd": getattr(item, "workspace_fallback_cwd", None),
            "workspace_recommended_actions": recommended_actions,
            "workspace_primary_action": action_bundle["primary_action"],
            "workspace_secondary_action": action_bundle["secondary_action"],
            "workspace_tertiary_action": action_bundle["tertiary_action"],
            "workspace_action_target": action_bundle["target"],
            "workspace_effective_cwd_exists": Path(effective_cwd).exists() if effective_cwd else None,
        }

    def _execution_contract_metadata_for_summary(self, item) -> dict[str, Any]:
        execution_mode = getattr(item, "session_execution_mode", None) or "main"
        policy_name = getattr(item, "session_command_policy_name", None)
        require_read_only = bool(
            getattr(item, "session_command_policy_require_read_only_subagents", False)
        )
        return {
            "session_execution_mode": execution_mode,
            "session_command_policy_name": policy_name,
            "session_command_policy_source": getattr(item, "session_command_policy_source", None),
            "session_command_policy_allowed_tool_names": list(
                getattr(item, "session_command_policy_allowed_tool_names", ()) or ()
            ),
            "session_command_policy_allowed_bash_prefixes": list(
                getattr(item, "session_command_policy_allowed_bash_prefixes", ()) or ()
            ),
            "session_command_policy_require_read_only_subagents": require_read_only,
            "session_execution_summary": self._execution_summary(
                session_execution_mode=str(execution_mode or "main"),
                session_command_policy_name=policy_name,
                session_command_policy_require_read_only_subagents=require_read_only,
            ),
        }

    def _planning_surface_metadata_for_summary(self, item) -> dict[str, Any]:
        active_id = getattr(item, "active_planning_artifact_id", None)
        count = int(getattr(item, "planning_artifact_count", 0) or 0)
        return {
            "active_planning_artifact_id": active_id,
            "planning_artifact_count": count,
            "has_active_plan": bool(active_id),
        }

    def _task_surface_metadata_for_summary(self, item) -> dict[str, Any]:
        raw_counts = getattr(item, "task_surface_counts", {}) or {}
        counts: dict[str, int] = {}
        if isinstance(raw_counts, dict):
            for key, value in raw_counts.items():
                try:
                    counts[str(key)] = int(value)
                except (TypeError, ValueError):
                    continue
        return {
            "task_surface_counts": counts,
            "task_surface_total_count": sum(counts.values()),
            "has_task_surface": any(value > 0 for value in counts.values()),
        }

    def _session_source_metadata_for_record(self, record: SessionRecord) -> dict[str, Any]:
        restored = record.restored_from is not None
        return {
            "session_source": "restored_saved" if restored else "new",
            "continuation_mode": "live_session",
            "live_session": True,
            "saved_resume_restores_state_only": restored,
        }

    def _session_source_metadata_for_summary(self) -> dict[str, Any]:
        return {
            "session_source": "saved",
            "continuation_mode": "saved_resume",
            "live_session": False,
            "saved_resume_restores_state_only": True,
        }

    def _execution_summary(
        self,
        *,
        session_execution_mode: str,
        session_command_policy_name: Any,
        session_command_policy_require_read_only_subagents: bool,
    ) -> str:
        bits = [f"execution={session_execution_mode or 'main'}"]
        policy_name = str(session_command_policy_name or "").strip()
        if policy_name:
            bits.append(f"policy={policy_name}")
        if session_command_policy_require_read_only_subagents:
            bits.append("read_only_subagents=yes")
        return "  ".join(bits)

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
        elif view == "active_plan_scouts":
            text = session.describe_active_plan_scouts_at(
                int(params.get("selected_index", 0)),
                full_detail=bool(params.get("full_detail", False)),
            )
        elif view == "active_plan_execution":
            text = session.describe_active_plan_execution_at(
                int(params.get("selected_index", 0)),
                full_detail=bool(params.get("full_detail", False)),
            )
        elif view == "active_plan_timeline":
            text = session.describe_active_plan_timeline_at(
                int(params.get("selected_index", 0)),
                selected_compare_index=int(params.get("selected_compare_index", 0)),
                selected_phase_local_task_index=int(params.get("selected_phase_local_task_index", 0)),
                kind_filter=str(params.get("kind_filter", "all")),
                delta_mode=str(params.get("delta_mode", "none")),
                phase_filter=str(params.get("phase_filter", "none")),
                focus_mode=str(params.get("focus_mode", "none")),
                compare_mode=str(params.get("compare_mode", "none")),
                artifact_id=(
                    str(params.get("artifact_id"))
                    if params.get("artifact_id") is not None
                    else None
                ),
            )
        elif view == "active_plan_replay":
            text = session.describe_active_plan_replay_at(
                int(params.get("selected_index", 0)),
                selected_compare_index=int(params.get("selected_compare_index", 0)),
                selected_phase_local_task_index=int(params.get("selected_phase_local_task_index", 0)),
                kind_filter=str(params.get("kind_filter", "all")),
                delta_mode=str(params.get("delta_mode", "none")),
                phase_filter=str(params.get("phase_filter", "none")),
                focus_mode=str(params.get("focus_mode", "none")),
                compare_mode=str(params.get("compare_mode", "none")),
                latest=bool(params.get("latest", False)),
                source_mode=str(params.get("source_mode", "auto")),
                artifact_id=(
                    str(params.get("artifact_id"))
                    if params.get("artifact_id") is not None
                    else None
                ),
            )
        elif view == "active_plan_audit":
            text = session.describe_active_plan_audit_at(
                int(params.get("selected_index", 0)),
                artifact_id=(
                    str(params.get("artifact_id"))
                    if params.get("artifact_id") is not None
                    else None
                ),
            )
        elif view == "active_plan_lineage":
            text = session.describe_active_plan_lineage_at(int(params.get("selected_index", 0)))
        elif view == "active_plan_advisor":
            text = session.describe_active_plan_advisor()
        elif view == "advisor_status":
            text = session.describe_advisor()
        elif view == "task_detail":
            task_id = self._require_string(params, "task_id")
            text = session.describe_task_detail(task_id)
        elif view == "task_drift_detail":
            text = session.describe_task_drift_detail(self._require_string(params, "task_id"))
        elif view == "mcp_servers":
            text = session.describe_mcp_servers()
        elif view == "mcp_tools":
            text = session.describe_mcp_tools()
        elif view == "project_memory":
            text = session.describe_project_memory()
        elif view == "loaded_skills":
            text = session.describe_loaded_skills()
        elif view == "agents":
            text = session.describe_agents()
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
        response = {"view": view, "text": text}
        if view == "active_plan":
            file_context_payload = session.active_plan_file_context_payload()
            if file_context_payload is not None:
                response.update(file_context_payload)
        if view == "tasks":
            response["checklist_tasks"] = session.checklist_tasks_payload()
            response["task_surface_counts"] = session.task_surface_counts_payload()
            response.update(self._checklist_duplicate_metadata_for_session(session))
        if view == "task_detail":
            execution_detail_metadata = session.task_execution_detail_metadata(task_id)
            if execution_detail_metadata is not None:
                response.update(execution_detail_metadata)
            detail_metadata = session.task_workspace_detail_metadata(task_id)
            if detail_metadata is not None:
                response.update(detail_metadata)
            checklist_detail_metadata = session.checklist_task_detail_metadata(task_id)
            if checklist_detail_metadata is not None:
                response.update(checklist_detail_metadata)
            file_context_payload = session.task_file_context_payload(task_id)
            if file_context_payload is not None:
                response.update(file_context_payload)
        return response

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
            metadata = session.selected_change_detail_metadata(
                index=int(params.get("index", 0)),
                file_index=int(params.get("file_index", 0)),
                limit=limit,
                redo=redo,
            )
            return {
                "view": view,
                "redo": redo,
                "text": session.selected_change_detail(
                    index=int(params.get("index", 0)),
                    file_index=int(params.get("file_index", 0)),
                    limit=limit,
                    redo=redo,
                ),
                **metadata,
            }
        raise ServiceError(
            -32602,
            f'Unsupported change view "{view}".',
            data={"type": "invalid_params", "field": "view"},
        )

    def _session_action(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require_session_id(params)
        record = self._get_record(session_id)
        session = record.session
        action = params.get("action")
        if not isinstance(action, str) or not action:
            raise ServiceError(-32602, "action must be a non-empty string.")
        if action == "clear_history":
            text = session.clear_history()
        elif action == "background_send_followup":
            text = session.send_background_followup(
                str(params.get("bg_id", "")),
                str(params.get("prompt", "")),
            )
        elif action == "background_queue_message":
            text = session.queue_background_message(
                str(params.get("bg_id", "")),
                str(params.get("prompt", "")),
            )
        elif action == "background_cancel_pending_followup":
            text = session.cancel_pending_background_followup(str(params.get("bg_id", "")))
        elif action == "describe_rewind":
            args = str(params.get("args", ""))
            text = session.describe_rewind(args)
            response = {
                "action": action,
                "text": text,
                "rewind_mode": self._rewind_mode_from_args(args),
                "rewindable_boundary_count": int(
                    session.memory_surface_payload().get("rewindable_history_boundary_count") or 0
                ),
                "default_rewind_selector": session.memory_surface_payload().get("default_rewind_selector"),
            }
            preview = self._rewind_preview_metadata_for_args(session, args)
            if preview is not None:
                response.update(preview)
            return response
        elif action == "rewind_to_boundary":
            text = session.rewind_to_boundary(str(params.get("args", "")))
        elif action == "clear_session_reset":
            result = session.clear_session_reset()
            new_session_id = str(result.get("session_id") or session.state.session_id)
            if new_session_id != session_id:
                self._sessions.pop(session_id, None)
                self._sessions[new_session_id] = record
            return {
                "action": action,
                "text": str(result.get("text", "")),
                "old_session_id": str(result.get("old_session_id") or session_id),
                "session_id": new_session_id,
                "transcript_path": result.get("transcript_path"),
            }
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
        elif action == "workspace_cleanup_preview":
            text = session.workspace_cleanup_preview()
        elif action == "workspace_cleanup_apply":
            text = session.workspace_cleanup_apply(str(params.get("args", "")))
        elif action == "workspace_repair":
            text = session.workspace_repair(str(params.get("args", "")))
        elif action == "symbol_surface_open_primary":
            text = session.symbol_surface_primary_action()
        elif action == "symbol_surface_open_secondary":
            text = session.symbol_surface_secondary_action()
        elif action == "clear_symbol_surface":
            text = session.clear_symbol_surface()
        elif action == "symbol_surface_select_next_match":
            text = session.symbol_surface_select_next_match()
        elif action == "symbol_surface_select_prev_match":
            text = session.symbol_surface_select_prev_match()
        elif action == "symbol_surface_select_next_definition":
            text = session.symbol_surface_select_next_definition()
        elif action == "symbol_surface_select_prev_definition":
            text = session.symbol_surface_select_prev_definition()
        elif action == "symbol_surface_select_next_reference":
            text = session.symbol_surface_select_next_reference()
        elif action == "symbol_surface_select_prev_reference":
            text = session.symbol_surface_select_prev_reference()
        elif action == "checklist_mark_in_progress":
            text = session.checklist_mark_in_progress(str(params.get("args", "")))
        elif action == "checklist_mark_completed":
            text = session.checklist_mark_completed(str(params.get("args", "")))
        elif action == "checklist_reopen":
            text = session.checklist_reopen(str(params.get("args", "")))
        elif action == "checklist_set_owner":
            text = session.checklist_set_owner(
                str(params.get("args", "")),
                str(params.get("value", "")),
            )
        elif action == "checklist_set_subject":
            text = session.checklist_set_subject(
                str(params.get("args", "")),
                str(params.get("value", "")),
            )
        elif action == "checklist_set_description":
            text = session.checklist_set_description(
                str(params.get("args", "")),
                str(params.get("value", "")),
            )
        elif action == "checklist_set_metadata":
            text = session.checklist_set_metadata(
                str(params.get("args", "")),
                str(params.get("value", "")),
            )
        elif action == "checklist_set_active_form":
            text = session.checklist_set_active_form(
                str(params.get("args", "")),
                str(params.get("value", "")),
            )
        elif action == "checklist_set_blocks":
            text = session.checklist_set_blocks(
                str(params.get("args", "")),
                str(params.get("value", "")),
            )
        elif action == "checklist_set_blocked_by":
            text = session.checklist_set_blocked_by(
                str(params.get("args", "")),
                str(params.get("value", "")),
            )
        elif action == "open_active_plan_advisor":
            text = session.open_active_plan_advisor()
        elif action == "show_advisor_status":
            text = session.show_advisor_status()
        elif action == "open_task_detail":
            text = session.open_task_detail(str(params.get("args", "")))
        elif action == "open_task_detail_advisor":
            text = session.open_task_detail_advisor(str(params.get("args", "")))
        elif action == "open_task_drift_detail":
            text = session.open_task_drift_detail(str(params.get("args", "")))
        elif action == "open_phase_local_execution_task":
            text = session.open_phase_local_execution_task(str(params.get("args", "")))
        elif action == "open_phase_local_recent_drift_task":
            text = session.open_phase_local_recent_drift_task(str(params.get("args", "")))
        elif action == "focus_active_plan_timeline_task":
            text = session.focus_active_plan_timeline_task(str(params.get("args", "")))
        elif action == "clear_active_plan_timeline_focus":
            text = session.clear_active_plan_timeline_focus()
        else:
            raise ServiceError(
                -32602,
                f'Unsupported session action "{action}".',
                data={"type": "invalid_params", "field": "action"},
            )
        return {"action": action, "text": text}

    def _rewind_mode_from_args(self, args: str) -> str:
        raw = str(args or "").strip()
        if not raw or raw.lower() == "list":
            return "list"
        if raw.lower().startswith("show "):
            return "show"
        if raw.lower().startswith("apply "):
            return "apply"
        return "unknown"

    def _rewind_preview_metadata_for_args(
        self,
        session: Session,
        args: str,
    ) -> dict[str, Any] | None:
        raw = str(args or "").strip()
        selector = ""
        if raw.lower().startswith("show "):
            selector = raw.split(" ", 1)[1].strip()
        elif not raw or raw.lower() == "list":
            selector = str(session.memory_surface_payload().get("default_rewind_selector") or "").strip()
        if not selector:
            return None
        payload = session.rewind_boundary_preview_payload(selector)
        if not isinstance(payload, dict) or not payload:
            return None
        workflow_surface_policy = payload.get("workflow_surface_policy")
        return {
            "selector": payload.get("selector_index"),
            "boundary_id": payload.get("boundary_id"),
            "boundary_kind": payload.get("boundary_kind"),
            "boundary_kind_label": payload.get("boundary_kind_label"),
            "trigger": payload.get("trigger"),
            "trigger_reason": payload.get("trigger_reason"),
            "created_at": payload.get("created_at"),
            "summary": payload.get("summary"),
            "rewindable": bool(payload.get("rewindable")),
            "message_count_before": int(payload.get("message_count_before") or 0),
            "message_count_after": int(payload.get("message_count_after") or 0),
            "context_summary_chars_before": int(payload.get("context_summary_chars_before") or 0),
            "context_summary_chars_after": int(payload.get("context_summary_chars_after") or 0),
            "snapshot_available": bool(payload.get("snapshot_available")),
            "snapshot_message_count": int(payload.get("snapshot_message_count") or 0),
            "snapshot_summary_chars": int(payload.get("snapshot_summary_chars") or 0),
            "target_boundary_id": payload.get("target_boundary_id"),
            "target_boundary_kind": payload.get("target_boundary_kind"),
            "target_boundary_kind_label": payload.get("target_boundary_kind_label"),
            "old_session_id": payload.get("old_session_id"),
            "new_session_id": payload.get("new_session_id"),
            "lineage_summary": payload.get("lineage_summary"),
            "restore_message_delta_current": int(payload.get("restore_message_delta_current") or 0),
            "restore_summary_chars_delta_current": int(payload.get("restore_summary_chars_delta_current") or 0),
            "restore_message_count_current": int(payload.get("restore_message_count_current") or 0),
            "restore_summary_chars_current": int(payload.get("restore_summary_chars_current") or 0),
            "targets_pre_compact_state": bool(payload.get("targets_pre_compact_state")),
            "targets_post_resume_state": bool(payload.get("targets_post_resume_state")),
            "restore_effect_summary": payload.get("restore_effect_summary"),
            "workflow_surface_policy": dict(workflow_surface_policy) if isinstance(workflow_surface_policy, dict) else {},
            "apply_action": payload.get("apply_action"),
            "show_action": payload.get("show_action"),
        }

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

    def _session_question_status(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require_session_id(params)
        record = self._get_record(session_id)
        return {"session_id": session_id, **record.pending_question_status()}

    def _session_question_respond(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require_session_id(params)
        record = self._get_record(session_id)
        question_id = int(params.get("question_id", 0))
        answers = params.get("answers") or {}
        if not isinstance(answers, dict):
            raise ServiceError(
                -32602,
                "answers must be an object.",
                data={"type": "invalid_params", "field": "answers"},
            )
        resolved = record.resolve_questions(
            question_id=question_id,
            result=UserQuestionResponse(
                answers={str(key): str(value) for key, value in answers.items()},
                canceled=bool(params.get("canceled", False)),
            ),
        )
        if not resolved:
            raise ServiceError(
                -32004,
                "No matching pending question request.",
                data={"type": "question_not_found", "question_id": question_id},
            )
        return {
            "session_id": session_id,
            "question_id": question_id,
            "resolved": True,
        }

    def _symbol_locate(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._get_session(params)
        symbol = self._require_symbol(params)
        path = params.get("path", ".")
        max_results = int(params.get("max_results", 50))
        return session.locate_symbol_surface_payload(symbol, path=path, max_results=max_results)

    def _symbol_references(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._get_session(params)
        symbol = self._require_symbol(params)
        path = params.get("path", ".")
        scope = params.get("scope", "auto")
        max_results = int(params.get("max_results", 100))
        return session.collect_references_surface_payload(
            symbol,
            path=path,
            scope=scope,
            max_results=max_results,
        )

    def _symbol_actions(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._get_session(params)
        symbol = self._require_symbol(params)
        path = params.get("path", ".")
        scope = params.get("scope", "workspace")
        max_definition_results = int(params.get("max_definition_results", 50))
        max_reference_results = int(params.get("max_reference_results", 100))
        return session.build_symbol_action_surface_payload(
            symbol,
            path=path,
            scope=scope,
            max_definition_results=max_definition_results,
            max_reference_results=max_reference_results,
        )

    def _build_session_config(self, params: dict[str, Any]) -> SessionConfig:
        cwd = params.get("cwd")
        mcp_config_path = params.get("mcp_config_path")
        permission_config_path = params.get("permission_config_path")
        if cwd is None:
            resolved_cwd = self.base_config.cwd
        elif isinstance(cwd, str):
            resolved_cwd = Path(cwd).resolve()
        else:
            raise ServiceError(-32602, "cwd must be a string.")
        load_dotenv(resolved_cwd / ".env")
        resolved_mcp_config = self.base_config.mcp_config_path
        resolved_permission_config = self.base_config.permission_config_path
        if mcp_config_path is not None:
            if not isinstance(mcp_config_path, str):
                raise ServiceError(-32602, "mcp_config_path must be a string.")
            resolved_mcp_config = resolve_workspace_path(resolved_cwd, mcp_config_path)
        elif self.base_config.mcp_config_path and self.base_config.mcp_config_path.parent != resolved_cwd / ".pyclaude":
            resolved_mcp_config = self.base_config.mcp_config_path
        else:
            resolved_mcp_config = (resolved_cwd / ".pyclaude" / "mcp_servers.json").resolve()
        if permission_config_path is not None:
            if not isinstance(permission_config_path, str):
                raise ServiceError(-32602, "permission_config_path must be a string.")
            resolved_permission_config = resolve_workspace_path(resolved_cwd, permission_config_path)
        elif (
            self.base_config.permission_config_path
            and self.base_config.permission_config_path.parent != resolved_cwd / ".pyclaude"
        ):
            resolved_permission_config = self.base_config.permission_config_path
        else:
            resolved_permission_config = (resolved_cwd / ".pyclaude" / "permissions.json").resolve()
        return replace(
            self.base_config,
            cwd=resolved_cwd,
            interactive=False,
            mcp_config_path=resolved_mcp_config,
            permission_config_path=resolved_permission_config,
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
    context = PermissionDisplayContext(
        decision_reason=event.decision_reason or "",
        permission_rules=event.permission_rules,
        command_mode_name=event.command_mode_name or "",
        command_mode_allowed_prefixes=event.command_mode_allowed_prefixes,
        command_mode_violating_segment=event.command_mode_violating_segment or "",
        command_mode_violating_segment_index=event.command_mode_violating_segment_index,
        command_mode_complex_features=event.command_mode_complex_features,
    )
    payload = {
        "kind": event.kind,
        "message": event.message,
        "task_id": event.task_id,
        "tool_name": event.tool_name,
        "tool_call_id": event.tool_call_id,
        "duration_ms": event.duration_ms,
        "prompt_tokens": event.prompt_tokens,
        "completion_tokens": event.completion_tokens,
        "total_tokens": event.total_tokens,
        "usage_source": event.usage_source,
        "batch_size": event.batch_size,
        "batch_parallel": event.batch_parallel,
        "result_count": event.result_count,
        "budget_state": event.budget_state,
        "budget_reason": event.budget_reason,
        "compaction_trigger": event.compaction_trigger,
        "approval_risk_level": event.approval_risk_level,
        "replacement_count": event.replacement_count,
        "replaced_chars_total": event.replaced_chars_total,
        "replacement_reason": event.replacement_reason,
        "artifact_count": event.artifact_count,
        "artifact_chars_saved": event.artifact_chars_saved,
        "microcompact_count": event.microcompact_count,
        "microcompact_chars_saved": event.microcompact_chars_saved,
        "is_error": event.is_error,
    }
    payload.update(permission_display_context_to_dict(context))
    return payload


def _approval_request_to_dict(request: ApprovalRequest) -> dict[str, Any]:
    context = PermissionDisplayContext(
        decision_reason=request.decision_reason,
        permission_rules=request.permission_rules,
        command_mode_name=request.command_mode_name,
        command_mode_source=request.command_mode_source,
        command_mode_allowed_prefixes=request.command_mode_allowed_prefixes,
        command_mode_violating_segment=request.command_mode_violating_segment,
        command_mode_violating_segment_index=request.command_mode_violating_segment_index,
        command_mode_complex_features=request.command_mode_complex_features,
    )
    payload = {
        "tool_name": request.tool_name,
        "reason": request.reason,
        "risk_level": request.risk_level,
        "approval_key": request.approval_key,
        "details": request.details,
        "command": request.command,
        "target_paths": list(request.target_paths),
    }
    payload.update(permission_display_context_to_dict(context))
    payload["display_lines"] = render_approval_request_lines(request)
    payload["display_compact"] = render_approval_request_compact(request)
    return payload


def _approval_result_to_dict(result: ApprovalResult) -> dict[str, Any]:
    return {
        "decision": result.decision,
        "scope": result.scope,
    }


def _question_request_to_dict(request: UserQuestionRequest) -> dict[str, Any]:
    return {
        "questions": [
            {
                "header": question.header,
                "question": question.question,
                "multi_select": question.multi_select,
                "options": [
                    {"label": option.label, "description": option.description}
                    for option in question.options
                ],
            }
            for question in request.questions
        ]
    }


def _question_response_to_dict(result: UserQuestionResponse) -> dict[str, Any]:
    return {"answers": dict(result.answers), "canceled": result.canceled}


def _question_request_from_dict(payload: dict[str, Any]) -> UserQuestionRequest:
    return UserQuestionRequest(
        questions=tuple(
            UserQuestion(
                header=str(question.get("header", "")),
                question=str(question.get("question", "")),
                multi_select=bool(question.get("multi_select", False)),
                options=tuple(
                    QuestionOption(
                        label=str(option.get("label", "")),
                        description=str(option.get("description", "")),
                    )
                    for option in question.get("options", [])
                ),
            )
            for question in payload.get("questions", [])
        )
    )


def _question_response_from_dict(payload: dict[str, Any]) -> UserQuestionResponse:
    answers = payload.get("answers") or {}
    if not isinstance(answers, dict):
        answers = {}
    return UserQuestionResponse(
        answers={str(key): str(value) for key, value in answers.items()},
        canceled=bool(payload.get("canceled", False)),
    )
