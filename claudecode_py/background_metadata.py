from __future__ import annotations

from pathlib import Path
from typing import Any

from .storage.background_sessions import (
    BackgroundSessionRecord,
    list_background_sessions,
    resolve_background_session,
)
from .storage.transcript import load_transcript_by_session_id
from .workflow_semantics import build_continuation_semantics


def _compact_text(value: str | None, *, limit: int = 120) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _tool_use_count_from_changes(changes: list[Any]) -> int:
    count = 0
    for change in changes:
        tool_name = str(getattr(change, "tool_name", "") or "").strip()
        if tool_name:
            count += 1
    return count


def _coerce_int(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _background_runtime_fields_from_task_metadata(task_metadata: dict[str, Any]) -> dict[str, Any]:
    token_count = _coerce_int(task_metadata.get("background_token_count"))
    token_source = str(task_metadata.get("background_token_count_source") or "").strip() or None
    return {
        "background_token_count": token_count,
        "background_token_count_source": token_source if token_source in {"provider", "estimated"} else ("none" if not token_count else "estimated"),
        "background_recent_activity": _compact_text(
            str(task_metadata.get("background_recent_activity") or ""), limit=160
        ),
        "background_recent_activity_kind": (
            str(task_metadata.get("background_recent_activity_kind") or "").strip() or None
        ),
        "background_last_tool": _compact_text(str(task_metadata.get("background_last_tool") or ""), limit=80),
        "background_last_tool_input": _compact_text(
            str(task_metadata.get("background_last_tool_input") or ""), limit=160
        ),
        "background_last_tool_summary": _compact_text(
            str(task_metadata.get("background_last_tool_summary") or ""), limit=160
        ),
        "background_runtime_active_tool_status": (
            str(task_metadata.get("background_runtime_active_tool_status") or "").strip() or None
        ),
        "background_runtime_parallel_batch_active": bool(
            task_metadata.get("background_runtime_parallel_batch_active")
        ),
        "background_runtime_parallel_batch_size": _coerce_int(
            task_metadata.get("background_runtime_parallel_batch_size")
        ),
        "background_runtime_last_result_summary": _compact_text(
            str(
                task_metadata.get("background_runtime_last_result_summary")
                or task_metadata.get("background_runtime_last_tool_result_summary")
                or ""
            ),
            limit=160,
        ),
        "background_runtime_budget_pressure_summary": _compact_text(
            str(
                task_metadata.get("background_runtime_budget_pressure_summary")
                or task_metadata.get("background_runtime_last_budget_pressure")
                or ""
            ),
            limit=160,
        ),
        "background_runtime_compact_recovery_summary": _compact_text(
            str(
                task_metadata.get("background_runtime_compact_recovery_summary")
                or task_metadata.get("background_runtime_last_compact_recovery")
                or ""
            ),
            limit=160,
        ),
        "background_runtime_tool_result_replacement_summary": _compact_text(
            str(task_metadata.get("background_runtime_tool_result_replacement_summary") or ""),
            limit=160,
        ),
        "background_runtime_tool_result_artifact_summary": _compact_text(
            str(task_metadata.get("background_runtime_tool_result_artifact_summary") or ""),
            limit=160,
        ),
        "background_runtime_tool_result_microcompact_summary": _compact_text(
            str(task_metadata.get("background_runtime_tool_result_microcompact_summary") or ""),
            limit=160,
        ),
        "background_tool_use_count": _coerce_int(task_metadata.get("background_tool_use_count")),
        "background_progress_summary": _compact_text(
            str(task_metadata.get("background_progress_summary") or ""), limit=160
        ),
        "background_progress_updated_at": (
            str(task_metadata.get("background_progress_updated_at") or "").strip() or None
        ),
    }


def _completion_state_from_status(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"running", "busy", "queued"}:
        return "running"
    if normalized == "failed":
        return "failed"
    if normalized == "stopped":
        return "cancelled"
    if normalized == "completed":
        return "completed"
    return normalized or "unknown"


def _completion_payload(
    *,
    status: str,
    primary_task: dict[str, Any] | None,
    latest_change_summary: str | None,
    logs_action: str,
    resume_action: str | None,
    transcript_exists: bool,
) -> dict[str, Any]:
    completion_state = _completion_state_from_status(status)
    primary_task_status = (
        str(primary_task.get("status") or "").strip()
        if isinstance(primary_task, dict)
        else ""
    )
    effective_state = completion_state
    if effective_state == "running" and primary_task_status in {"completed", "failed", "stopped"}:
        effective_state = _completion_state_from_status(primary_task_status)
    primary_task_progress = (
        _compact_text(str(primary_task.get("progress_summary") or ""))
        if isinstance(primary_task, dict)
        else None
    )
    primary_task_error = (
        _compact_text(str(primary_task.get("error") or ""))
        if isinstance(primary_task, dict)
        else None
    )
    primary_task_output = (
        _compact_text(str(primary_task.get("output") or ""))
        if isinstance(primary_task, dict)
        else None
    )
    if effective_state == "failed":
        completion_summary = primary_task_error or primary_task_progress or "Background session failed."
    elif effective_state == "completed":
        completion_summary = (
            primary_task_output
            or primary_task_progress
            or _compact_text(latest_change_summary)
            or "Background session completed."
        )
    elif effective_state == "cancelled":
        completion_summary = primary_task_progress or "Background session stopped."
    else:
        completion_summary = primary_task_progress or _compact_text(latest_change_summary)
    result_pointer = (
        f"/task show {primary_task['task_id']}"
        if isinstance(primary_task, dict) and str(primary_task.get("task_id") or "").strip()
        else logs_action
    )
    transcript_pointer = resume_action if transcript_exists and resume_action else logs_action
    return {
        "background_completion_state": effective_state,
        "background_completion_summary": completion_summary,
        "background_failure_reason": primary_task_error if effective_state == "failed" else None,
        "background_result_pointer": result_pointer,
        "background_transcript_pointer": transcript_pointer,
    }


def _progress_payload(
    *,
    primary_task: dict[str, Any] | None,
    latest_change_summary: str | None,
    latest_change_tool_name: str | None,
    tool_use_count: int,
    message_count: int | None,
) -> dict[str, Any]:
    runtime_token_count = (
        _coerce_int(primary_task.get("background_token_count"))
        if isinstance(primary_task, dict)
        else None
    )
    runtime_token_source = (
        str(primary_task.get("background_token_count_source") or "").strip()
        if isinstance(primary_task, dict)
        else ""
    )
    runtime_recent_activity = (
        _compact_text(str(primary_task.get("background_recent_activity") or ""), limit=160)
        if isinstance(primary_task, dict)
        else None
    )
    runtime_recent_activity_kind = (
        str(primary_task.get("background_recent_activity_kind") or "").strip()
        if isinstance(primary_task, dict)
        else ""
    )
    runtime_last_tool = (
        _compact_text(str(primary_task.get("background_last_tool") or ""), limit=80)
        if isinstance(primary_task, dict)
        else None
    )
    runtime_last_tool_input = (
        _compact_text(str(primary_task.get("background_last_tool_input") or ""), limit=160)
        if isinstance(primary_task, dict)
        else None
    )
    runtime_last_tool_summary = (
        _compact_text(str(primary_task.get("background_last_tool_summary") or ""), limit=160)
        if isinstance(primary_task, dict)
        else None
    )
    runtime_tool_use_count = (
        _coerce_int(primary_task.get("background_tool_use_count"))
        if isinstance(primary_task, dict)
        else None
    )
    runtime_progress_summary = (
        _compact_text(str(primary_task.get("background_progress_summary") or ""), limit=160)
        if isinstance(primary_task, dict)
        else None
    )
    runtime_progress_updated_at = (
        str(primary_task.get("background_progress_updated_at") or "").strip()
        if isinstance(primary_task, dict)
        else ""
    )
    runtime_active_tool_status = (
        str(primary_task.get("background_runtime_active_tool_status") or "").strip()
        if isinstance(primary_task, dict)
        else ""
    )
    runtime_parallel_batch_active = (
        bool(primary_task.get("background_runtime_parallel_batch_active"))
        if isinstance(primary_task, dict)
        else False
    )
    runtime_parallel_batch_size = (
        _coerce_int(primary_task.get("background_runtime_parallel_batch_size"))
        if isinstance(primary_task, dict)
        else None
    )
    runtime_last_result_summary = (
        _compact_text(
            str(
                primary_task.get("background_runtime_last_result_summary")
                or primary_task.get("background_runtime_last_tool_result_summary")
                or ""
            ),
            limit=160,
        )
        if isinstance(primary_task, dict)
        else None
    )
    runtime_budget_pressure_summary = (
        _compact_text(
            str(
                primary_task.get("background_runtime_budget_pressure_summary")
                or primary_task.get("background_runtime_last_budget_pressure")
                or ""
            ),
            limit=160,
        )
        if isinstance(primary_task, dict)
        else None
    )
    runtime_compact_recovery_summary = (
        _compact_text(
            str(
                primary_task.get("background_runtime_compact_recovery_summary")
                or primary_task.get("background_runtime_last_compact_recovery")
                or ""
            ),
            limit=160,
        )
        if isinstance(primary_task, dict)
        else None
    )
    primary_task_progress = (
        _compact_text(str(primary_task.get("progress_summary") or ""))
        if isinstance(primary_task, dict)
        else None
    )
    recent_activity = runtime_recent_activity or primary_task_progress
    if recent_activity is None and latest_change_summary:
        recent_activity = f"latest change: {_compact_text(latest_change_summary) or latest_change_summary}"
    if recent_activity is None and isinstance(message_count, int) and message_count > 0:
        recent_activity = f"{message_count} messages recorded"
    last_tool = (
        runtime_last_tool
        or _compact_text(latest_change_tool_name)
        or (
            _compact_text(str(primary_task.get("kind") or ""))
            if isinstance(primary_task, dict)
            else None
        )
    )
    progress_summary = (
        runtime_progress_summary
        or recent_activity
        or primary_task_progress
        or _compact_text(latest_change_summary)
    )
    return {
        "background_token_count": runtime_token_count,
        "background_token_count_source": (
            runtime_token_source
            if runtime_token_source in {"provider", "estimated"}
            else ("estimated" if runtime_token_count else "none")
        ),
        "background_recent_activity": recent_activity,
        "background_recent_activity_kind": (
            runtime_recent_activity_kind or ("fallback" if runtime_recent_activity is None else None)
        ),
        "background_last_tool": last_tool,
        "background_last_tool_input": runtime_last_tool_input,
        "background_last_tool_summary": runtime_last_tool_summary,
        "background_runtime_active_tool_status": runtime_active_tool_status or None,
        "background_runtime_parallel_batch_active": runtime_parallel_batch_active,
        "background_runtime_parallel_batch_size": runtime_parallel_batch_size or 0,
        "background_runtime_last_result_summary": runtime_last_result_summary,
        "background_runtime_budget_pressure_summary": runtime_budget_pressure_summary,
        "background_runtime_compact_recovery_summary": runtime_compact_recovery_summary,
        "background_runtime_tool_result_replacement_summary": _compact_text(
            str(primary_task.get("background_runtime_tool_result_replacement_summary") or "")
        )
        if isinstance(primary_task, dict)
        else None,
        "background_runtime_tool_result_artifact_summary": _compact_text(
            str(primary_task.get("background_runtime_tool_result_artifact_summary") or "")
        )
        if isinstance(primary_task, dict)
        else None,
        "background_runtime_tool_result_microcompact_summary": _compact_text(
            str(primary_task.get("background_runtime_tool_result_microcompact_summary") or "")
        )
        if isinstance(primary_task, dict)
        else None,
        "background_tool_use_count": int(runtime_tool_use_count if runtime_tool_use_count is not None else tool_use_count),
        "background_message_count": message_count,
        "background_progress_summary": progress_summary,
        "background_progress_updated_at": runtime_progress_updated_at or None,
    }


def _followup_payload(
    record: BackgroundSessionRecord,
    *,
    allow_send: bool,
) -> dict[str, Any]:
    pending_followups = [str(item).strip() for item in (record.pending_followups or []) if str(item).strip()]
    pending_count = len(pending_followups)
    return {
        "background_pending_followup_count": pending_count,
        "background_pending_followup_summary": (
            _compact_text(pending_followups[0]) if pending_followups else None
        ),
        "background_latest_followup_message": _compact_text(record.latest_followup_message),
        "background_latest_followup_mode": str(record.latest_followup_mode or "").strip() or None,
        "background_latest_followup_at": str(record.latest_followup_at or "").strip() or None,
        "background_send_followup_action": (
            f"session.action background_send_followup {record.bg_id}"
            if allow_send
            else None
        ),
        "background_queue_message_action": f"session.action background_queue_message {record.bg_id}",
        "background_cancel_pending_followup_action": (
            f"session.action background_cancel_pending_followup {record.bg_id}"
            if pending_count > 0
            else None
        ),
    }


def background_transcript_metadata(record: BackgroundSessionRecord) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "transcript_exists": False,
        "message_count": None,
        "context_summary_chars": None,
        "task_surface_counts": {},
        "has_active_plan": False,
        "active_planning_artifact_id": None,
        "planning_artifact_count": 0,
        "transcript_path": record.transcript_path,
    }
    if not record.session_id:
        return metadata
    state, transcript_path = load_transcript_by_session_id(Path(record.cwd), record.session_id)
    if state is None:
        return metadata
    planning_artifacts = (
        state.planning_artifact_history
        if state.planning_artifact_history
        else state.recent_planning_artifacts
    )
    metadata.update(
        {
            "transcript_exists": True,
            "message_count": len(state.messages),
            "context_summary_chars": len(state.context_summary or ""),
            "task_surface_counts": dict(state.saved_task_surface_counts),
            "has_active_plan": bool(state.active_planning_artifact_id),
            "active_planning_artifact_id": state.active_planning_artifact_id,
            "planning_artifact_count": len(planning_artifacts),
            "transcript_path": str(transcript_path) if transcript_path is not None else record.transcript_path,
        }
    )
    return metadata


def _background_transcript_state(record: BackgroundSessionRecord) -> tuple[Any | None, Path | None]:
    if not record.session_id:
        return None, None
    return load_transcript_by_session_id(Path(record.cwd), record.session_id)


def background_session_is_live_attachable(record: BackgroundSessionRecord) -> bool:
    return (
        record.status in {"busy", "running"}
        and bool(record.bridge_host)
        and bool(record.bridge_port)
        and bool(record.session_id)
    )


def background_session_is_saved_resumable(record: BackgroundSessionRecord) -> bool:
    if background_session_is_live_attachable(record) or not record.session_id:
        return False
    return bool(background_transcript_metadata(record)["transcript_exists"])


def background_session_metadata(record: BackgroundSessionRecord, *, detail: bool) -> dict[str, Any]:
    live_attachable = background_session_is_live_attachable(record)
    transcript_metadata = background_transcript_metadata(record)
    saved_resumable = bool(record.session_id) and not live_attachable and bool(transcript_metadata["transcript_exists"])
    inactive_only = not live_attachable and not saved_resumable
    semantics = build_continuation_semantics(
        is_live_attachable=live_attachable,
        is_saved_resumable=saved_resumable,
        live_attach_command=f"pyclaude attach {record.bg_id}",
        resume_session_id=record.session_id,
        stay_on_surface=(
            f"pyclaude ps | pyclaude logs {record.bg_id}"
            if detail
            else f"pyclaude ps {record.bg_id} | pyclaude logs {record.bg_id}"
        ),
    )
    if record.status in {"busy", "running", "queued"}:
        source = "live_background"
    elif saved_resumable:
        source = "saved_background"
    else:
        source = "inactive_background"
    if live_attachable:
        primary_action = semantics.go_to_live_attach
        secondary_action = f"pyclaude logs {record.bg_id}"
    elif saved_resumable:
        primary_action = semantics.go_to_saved_resume.split(" | ", 1)[0]
        secondary_action = f"pyclaude logs {record.bg_id}"
    else:
        primary_action = f"pyclaude logs {record.bg_id}"
        secondary_action = f"pyclaude ps {record.bg_id}"
    return {
        "background_session_source": source,
        "background_continuation_category": semantics.category,
        "background_live_attachable": live_attachable,
        "background_saved_resumable": saved_resumable,
        "background_inactive_only": inactive_only,
        "background_go_to_live_attach": semantics.go_to_live_attach,
        "background_go_to_saved_resume": semantics.go_to_saved_resume,
        "background_stay_on_surface": semantics.stay_on_surface,
        "background_primary_action": primary_action,
        "background_secondary_action": secondary_action,
        "background_last_known_message_count": transcript_metadata["message_count"],
        "background_last_known_context_summary_chars": transcript_metadata["context_summary_chars"],
        "background_task_surface_counts": transcript_metadata["task_surface_counts"],
        "background_has_active_plan": transcript_metadata["has_active_plan"],
        "background_active_plan_id": transcript_metadata["active_planning_artifact_id"],
        "background_planning_artifact_count": transcript_metadata["planning_artifact_count"],
        "background_workspace_health": getattr(record, "workspace_health", "healthy"),
        "background_transcript_exists": transcript_metadata["transcript_exists"],
        "background_transcript_path": transcript_metadata["transcript_path"],
    }


def background_grouped_actions(record: BackgroundSessionRecord, *, detail: bool) -> dict[str, str]:
    metadata = background_session_metadata(record, detail=detail)
    return {
        "category": str(metadata["background_continuation_category"]),
        "go_to_live_attach": str(metadata["background_go_to_live_attach"]),
        "go_to_saved_resume": str(metadata["background_go_to_saved_resume"]),
        "stay_on_surface": str(metadata["background_stay_on_surface"]),
    }


def background_continuation_hint(record: BackgroundSessionRecord) -> str:
    return str(background_session_metadata(record, detail=False)["background_primary_action"])


def background_continuation_category(record: BackgroundSessionRecord) -> str:
    return str(background_session_metadata(record, detail=False)["background_continuation_category"])


def background_live_session_payload(session: Any) -> dict[str, Any]:
    bg_id = str(getattr(session, "_background_session_id", "") or "").strip()
    if not bg_id:
        return {}
    anchor_cwd = Path(getattr(session.config, "transcript_cwd", None) or session.config.cwd)
    record = resolve_background_session(anchor_cwd, bg_id)
    task_surface_counts = dict(session.task_surface_counts_payload())
    working_set_payload = dict(session.working_set_payload(limit=5))
    focused_file = str(working_set_payload.get("file_context_primary_path") or "").strip() or None
    focused_file_source = "working_set" if focused_file else "none"
    planning_payload = dict(session.planning_surface_payload())
    active_plan_id = str(planning_payload.get("active_planning_artifact_id") or "").strip() or None
    active_plan_summary = active_plan_id if active_plan_id else None
    recent_change_count = len(session.state.recent_change_sets)
    latest_change_summary: str | None = None
    latest_change_tool_name: str | None = None
    latest_change_file_count = 0
    if session.state.recent_change_sets:
        latest_change = session.state.recent_change_sets[-1]
        latest_change_summary = latest_change.summary or latest_change.change_kind or None
        latest_change_tool_name = latest_change.tool_name or None
        latest_change_file_count = len(latest_change.files)
    tool_use_count = _tool_use_count_from_changes(session.state.recent_change_sets)
    primary_task: dict[str, Any] | None = None
    background_execution_count = 0
    active_plan_execution_count = 0
    for task in reversed(session.task_manager.list()):
        surface_kind = str(session._task_surface_kind(task) or "").strip() or "other_task"
        if surface_kind == "background_execution":
            background_execution_count += 1
        elif surface_kind == "active_plan_execution":
            active_plan_execution_count += 1
        if primary_task is None and surface_kind in {"background_execution", "active_plan_execution"}:
            metadata = dict(task.metadata or {})
            primary_task = {
                "task_id": task.id,
                "status": task.status,
                "kind": task.kind,
                "description": task.description,
                "progress_summary": task.progress_summary,
                "output": task.output,
                "error": task.error,
                "updated_at": task.updated_at,
                "ended_at": task.ended_at,
                "surface_kind": surface_kind,
                "parent_session_id": str(metadata.get("parent_session_id") or "").strip() or None,
                "background_session_id": str(metadata.get("background_session_id") or "").strip() or None,
                "background_reverse_hint": str(metadata.get("background_reverse_hint") or "").strip() or None,
            }
            primary_task.update(_background_runtime_fields_from_task_metadata(metadata))
    if primary_task is None:
        for task in reversed(session.task_manager.list()):
            primary_task = {
                "task_id": task.id,
                "status": task.status,
                "kind": task.kind,
                "description": task.description,
                "progress_summary": task.progress_summary,
                "output": task.output,
                "error": task.error,
                "updated_at": task.updated_at,
                "ended_at": task.ended_at,
                "surface_kind": str(session._task_surface_kind(task) or "").strip() or "other_task",
                "parent_session_id": None,
                "background_session_id": str(task.metadata.get("background_session_id") or "").strip() or None,
                "background_reverse_hint": str(task.metadata.get("background_reverse_hint") or "").strip() or None,
            }
            primary_task.update(_background_runtime_fields_from_task_metadata(dict(task.metadata or {})))
            break
    task_surface_summary = ",".join(
        f"{key}:{int(value)}"
        for key, value in task_surface_counts.items()
        if isinstance(value, int) and value > 0
    )
    go_to_task = (
        [f"/task show {primary_task['task_id']}", "/tasks active"]
        if primary_task is not None and primary_task.get("task_id")
        else ["/tasks active"]
    )
    payload = {
        "background_session_id": bg_id,
        "background_session_source": "live_background",
        "background_continuation_category": "live attachable",
        "background_live_attachable": True,
        "background_saved_resumable": False,
        "background_inactive_only": False,
        "background_go_to_live_attach": f"pyclaude attach {bg_id}",
        "background_go_to_saved_resume": (
            f"pyclaude --resume-session {session.state.session_id} repl"
            if getattr(session.state, "session_id", None)
            else "none"
        ),
        "background_stay_on_surface": f"pyclaude ps {bg_id} | pyclaude logs {bg_id} summary",
        "background_primary_action": f"pyclaude attach {bg_id}",
        "background_secondary_action": f"pyclaude logs {bg_id} summary",
        "background_attach_action": f"pyclaude attach {bg_id}",
        "background_resume_action": (
            f"pyclaude --resume-session {session.state.session_id} repl"
            if getattr(session.state, "session_id", None)
            else "none"
        ),
        "background_logs_action": f"pyclaude logs {bg_id} summary",
        "background_history_action": "/history messages",
        "background_sessions_action": "pyclaude sessions --limit 10",
        "background_pending_followup_count": 0,
        "background_pending_followup_summary": None,
        "background_latest_followup_message": None,
        "background_latest_followup_mode": None,
        "background_latest_followup_at": None,
        "background_send_followup_action": f"session.action background_send_followup {bg_id}",
        "background_queue_message_action": f"session.action background_queue_message {bg_id}",
        "background_cancel_pending_followup_action": None,
        "background_current_workflow_summary": "attachable live background session",
        "background_task_surface_counts": task_surface_counts,
        "background_task_surface_summary": task_surface_summary or "none",
        "background_background_execution_count": background_execution_count,
        "background_active_plan_execution_count": active_plan_execution_count,
        "background_primary_task": primary_task,
        "background_primary_task_action": (
            f"/task show {primary_task['task_id']}"
            if primary_task is not None and primary_task.get("task_id")
            else None
        ),
        "background_recent_change_count": recent_change_count,
        "background_latest_change_summary": latest_change_summary,
        "background_latest_change_tool_name": latest_change_tool_name,
        "background_latest_change_file_count": latest_change_file_count,
        "background_working_set_file_count": int(working_set_payload.get("file_context_file_count") or 0),
        "background_working_set_paths": [
            str(item.get("path") or "").strip()
            for item in working_set_payload.get("file_context_files", [])
            if isinstance(item, dict) and str(item.get("path") or "").strip()
        ],
        "background_focused_file": focused_file,
        "background_focused_file_source": focused_file_source,
        "background_explicit_context_count": len(session.state.explicit_context_entries),
        "background_has_active_plan": bool(planning_payload.get("has_active_plan")),
        "background_active_plan_id": active_plan_id,
        "background_active_plan_summary": active_plan_summary,
        "background_last_known_message_count": len(session.state.messages),
        "background_last_known_context_summary_chars": len(session.state.context_summary or ""),
        "background_planning_artifact_count": int(planning_payload.get("planning_artifact_count") or 0),
        "background_workspace_health": getattr(session.state, "workspace_health", "healthy"),
        "background_transcript_exists": True,
        "background_transcript_path": None,
        "background_action_groups": {
            "go_to_live_attach": [f"pyclaude attach {bg_id}"],
            "go_to_saved_resume": [
                f"pyclaude --resume-session {session.state.session_id} repl"
                if getattr(session.state, "session_id", None)
                else "none"
            ],
            "go_to_task": go_to_task,
            "go_to_logs": [f"pyclaude logs {bg_id} summary", f"pyclaude logs {bg_id}"],
            "go_to_sessions_show": ["pyclaude sessions --limit 10"],
            "go_to_history": ["/history messages"],
            "go_to_followup": [f"session.action background_send_followup {bg_id}"],
            "go_to_queue": [f"session.action background_queue_message {bg_id}"],
            "stay_on_surface": [f"pyclaude ps {bg_id} | pyclaude logs {bg_id} summary"],
        },
        "background_action_order": (
            "go_to_live_attach",
            "go_to_followup",
            "go_to_queue",
            "go_to_task",
            "go_to_logs",
            "go_to_history",
            "go_to_sessions_show",
            "go_to_saved_resume",
            "stay_on_surface",
        ),
    }
    payload.update(
        _progress_payload(
            primary_task=primary_task,
            latest_change_summary=latest_change_summary,
            latest_change_tool_name=latest_change_tool_name,
            tool_use_count=tool_use_count,
            message_count=len(session.state.messages),
        )
    )
    payload.update(
        _completion_payload(
            status="running",
            primary_task=primary_task,
            latest_change_summary=latest_change_summary,
            logs_action=str(payload["background_logs_action"]),
            resume_action=str(payload["background_resume_action"]),
            transcript_exists=True,
        )
    )
    if record is not None:
        payload.update(_followup_payload(record, allow_send=True))
    return payload


def background_registry_payload(anchor_cwd: Path, *, limit: int = 5) -> dict[str, Any]:
    records = list_background_sessions(anchor_cwd)
    selected: dict[str, Any] | None = None
    fallback_saved: dict[str, Any] | None = None
    entries: list[dict[str, Any]] = []
    for record in records[: max(limit, 1)]:
        metadata = background_session_metadata(record, detail=False)
        workflow = background_workflow_payload(record, detail=False)
        primary_task = workflow.get("background_primary_task")
        entry = {
            "background_session_id": record.bg_id,
            "session_id": record.session_id,
            "status": record.status,
            "updated_at": record.updated_at or record.created_at,
            "background_session_source": metadata["background_session_source"],
            "background_continuation_category": metadata["background_continuation_category"],
            "background_live_attachable": bool(metadata["background_live_attachable"]),
            "background_saved_resumable": bool(metadata["background_saved_resumable"]),
            "background_inactive_only": bool(metadata["background_inactive_only"]),
            "background_primary_action": metadata["background_primary_action"],
            "background_secondary_action": metadata["background_secondary_action"],
            "background_attach_action": metadata["background_go_to_live_attach"],
            "background_resume_action": metadata["background_go_to_saved_resume"],
            "background_logs_action": f"pyclaude logs {record.bg_id} summary",
            "background_current_workflow_summary": workflow["background_current_workflow_summary"],
            "background_recent_activity": workflow["background_recent_activity"],
            "background_recent_activity_kind": workflow["background_recent_activity_kind"],
            "background_last_tool": workflow["background_last_tool"],
            "background_last_tool_input": workflow["background_last_tool_input"],
            "background_last_tool_summary": workflow["background_last_tool_summary"],
            "background_token_count": workflow["background_token_count"],
            "background_token_count_source": workflow["background_token_count_source"],
            "background_tool_use_count": workflow["background_tool_use_count"],
            "background_message_count": workflow["background_message_count"],
            "background_progress_summary": workflow["background_progress_summary"],
            "background_progress_updated_at": workflow["background_progress_updated_at"],
            "background_completion_state": workflow["background_completion_state"],
            "background_completion_summary": workflow["background_completion_summary"],
            "background_failure_reason": workflow["background_failure_reason"],
            "background_result_pointer": workflow["background_result_pointer"],
            "background_transcript_pointer": workflow["background_transcript_pointer"],
            "background_pending_followup_count": workflow["background_pending_followup_count"],
            "background_pending_followup_summary": workflow["background_pending_followup_summary"],
            "background_latest_followup_message": workflow["background_latest_followup_message"],
            "background_latest_followup_mode": workflow["background_latest_followup_mode"],
            "background_latest_followup_at": workflow["background_latest_followup_at"],
            "background_send_followup_action": workflow["background_send_followup_action"],
            "background_queue_message_action": workflow["background_queue_message_action"],
            "background_cancel_pending_followup_action": workflow["background_cancel_pending_followup_action"],
            "background_task_surface_summary": workflow["background_task_surface_summary"],
            "background_primary_task": primary_task,
            "background_active_plan_summary": workflow["background_active_plan_summary"],
            "background_focused_file": workflow["background_focused_file"],
            "background_workspace_health": metadata["background_workspace_health"],
        }
        entries.append(entry)
        if selected is None and entry["background_live_attachable"]:
            selected = entry
        elif fallback_saved is None and entry["background_saved_resumable"]:
            fallback_saved = entry
    if selected is None:
        selected = fallback_saved or (entries[0] if entries else None)
    return {
        "background_registry_count": len(records),
        "background_registry_entries": entries,
        "background_registry_selected_bg_id": (
            selected["background_session_id"] if selected is not None else None
        ),
        "background_registry_selected_status": selected["status"] if selected is not None else None,
        "background_registry_selected_continuation_category": (
            selected["background_continuation_category"] if selected is not None else None
        ),
        "background_registry_selected_workflow_summary": (
            selected["background_current_workflow_summary"] if selected is not None else None
        ),
        "background_registry_selected_primary_task": (
            selected["background_primary_task"] if selected is not None else None
        ),
        "background_registry_selected_active_plan_summary": (
            selected["background_active_plan_summary"] if selected is not None else None
        ),
        "background_registry_selected_focused_file": (
            selected["background_focused_file"] if selected is not None else None
        ),
        "background_registry_selected_recent_activity": (
            selected["background_recent_activity"] if selected is not None else None
        ),
        "background_registry_selected_recent_activity_kind": (
            selected["background_recent_activity_kind"] if selected is not None else None
        ),
        "background_registry_selected_progress_summary": (
            selected["background_progress_summary"] if selected is not None else None
        ),
        "background_registry_selected_last_tool_input": (
            selected["background_last_tool_input"] if selected is not None else None
        ),
        "background_registry_selected_last_tool_summary": (
            selected["background_last_tool_summary"] if selected is not None else None
        ),
        "background_registry_selected_token_count": (
            selected["background_token_count"] if selected is not None else None
        ),
        "background_registry_selected_token_count_source": (
            selected["background_token_count_source"] if selected is not None else "none"
        ),
        "background_registry_selected_completion_state": (
            selected["background_completion_state"] if selected is not None else None
        ),
        "background_registry_selected_completion_summary": (
            selected["background_completion_summary"] if selected is not None else None
        ),
        "background_registry_selected_pending_followup_count": (
            selected["background_pending_followup_count"] if selected is not None else 0
        ),
        "background_registry_selected_pending_followup_summary": (
            selected["background_pending_followup_summary"] if selected is not None else None
        ),
        "background_registry_selected_latest_followup_message": (
            selected["background_latest_followup_message"] if selected is not None else None
        ),
        "background_registry_selected_latest_followup_mode": (
            selected["background_latest_followup_mode"] if selected is not None else None
        ),
        "background_registry_primary_action": (
            selected["background_primary_action"] if selected is not None else None
        ),
        "background_registry_secondary_action": (
            selected["background_secondary_action"] if selected is not None else None
        ),
        "background_registry_attach_action": (
            selected["background_attach_action"] if selected is not None else None
        ),
        "background_registry_resume_action": (
            selected["background_resume_action"] if selected is not None else None
        ),
        "background_registry_logs_action": (
            selected["background_logs_action"] if selected is not None else None
        ),
        "background_registry_send_followup_action": (
            selected["background_send_followup_action"] if selected is not None else None
        ),
        "background_registry_queue_message_action": (
            selected["background_queue_message_action"] if selected is not None else None
        ),
        "background_registry_cancel_pending_followup_action": (
            selected["background_cancel_pending_followup_action"] if selected is not None else None
        ),
        "background_registry_selection_strategy": "live_attachable_first",
    }


def background_handoff_payload(anchor_cwd: Path, *, limit: int = 3) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for record in list_background_sessions(anchor_cwd):
        workflow = background_workflow_payload(record, detail=False)
        completion_state = str(workflow.get("background_completion_state") or "").strip()
        if completion_state not in {"completed", "failed", "cancelled"}:
            continue
        primary_task = workflow.get("background_primary_task")
        transcript_action = str(
            workflow.get("background_transcript_pointer") or f"pyclaude logs {record.bg_id} summary"
        ).strip()
        task_action = (
            f"/task show {primary_task['task_id']}"
            if isinstance(primary_task, dict) and str(primary_task.get("task_id") or "").strip()
            else "/tasks active"
        )
        resume_action = (
            f"pyclaude --resume-session {record.session_id} repl"
            if record.session_id and background_session_metadata(record, detail=False)["background_transcript_exists"]
            else None
        )
        changes_action = (
            f"{resume_action} | /changes working-set"
            if resume_action
            else f"pyclaude logs {record.bg_id} summary"
        )
        entries.append(
            {
                "background_session_id": record.bg_id,
                "session_id": record.session_id,
                "status": record.status,
                "updated_at": record.updated_at or record.created_at,
                "background_completion_state": completion_state,
                "background_completion_summary": workflow.get("background_completion_summary"),
                "background_failure_reason": workflow.get("background_failure_reason"),
                "background_primary_task": primary_task,
                "background_result_pointer": workflow.get("background_result_pointer"),
                "background_transcript_pointer": workflow.get("background_transcript_pointer"),
                "background_handoff_transcript_action": transcript_action,
                "background_handoff_task_action": task_action,
                "background_handoff_changes_action": changes_action,
                "background_handoff_resume_action": resume_action,
            }
        )
        if len(entries) >= max(limit, 1):
            break
    selected = entries[0] if entries else None
    return {
        "background_handoff_count": len(entries),
        "background_handoff_entries": entries,
        "background_handoff_selected_bg_id": (
            selected["background_session_id"] if selected is not None else None
        ),
        "background_handoff_selected_completion_state": (
            selected["background_completion_state"] if selected is not None else None
        ),
        "background_handoff_selected_completion_summary": (
            selected["background_completion_summary"] if selected is not None else None
        ),
        "background_handoff_selected_failure_reason": (
            selected["background_failure_reason"] if selected is not None else None
        ),
        "background_handoff_selected_primary_task": (
            selected["background_primary_task"] if selected is not None else None
        ),
        "background_handoff_transcript_action": (
            selected["background_handoff_transcript_action"] if selected is not None else None
        ),
        "background_handoff_task_action": (
            selected["background_handoff_task_action"] if selected is not None else None
        ),
        "background_handoff_changes_action": (
            selected["background_handoff_changes_action"] if selected is not None else None
        ),
        "background_handoff_resume_action": (
            selected["background_handoff_resume_action"] if selected is not None else None
        ),
        "background_handoff_selection_strategy": "recent_completion_first",
    }


def background_workflow_payload(record: BackgroundSessionRecord, *, detail: bool) -> dict[str, Any]:
    metadata = background_session_metadata(record, detail=detail)
    state, _ = _background_transcript_state(record)
    recent_change_count = 0
    latest_change_summary: str | None = None
    latest_change_tool_name: str | None = None
    latest_change_file_count = 0
    tool_use_count = 0
    working_set_paths: list[str] = []
    focused_file: str | None = None
    focused_file_source = "none"
    explicit_context_count = 0
    active_plan_summary: str | None = None
    primary_task: dict[str, Any] | None = None
    background_execution_count = 0
    active_plan_execution_count = 0
    if state is not None:
        recent_change_count = len(state.recent_change_sets)
        if state.recent_change_sets:
            tool_use_count = _tool_use_count_from_changes(state.recent_change_sets)
            latest_change = state.recent_change_sets[-1]
            latest_change_summary = latest_change.summary or latest_change.change_kind or None
            latest_change_tool_name = latest_change.tool_name or None
            latest_change_file_count = len(latest_change.files)
            for file_change in latest_change.files:
                path = str(file_change.path or "").strip()
                if path and path not in working_set_paths:
                    working_set_paths.append(path)
        explicit_context_count = len(state.explicit_context_entries)
        for entry in state.explicit_context_entries:
            path = str(entry.raw_path or entry.resolved_path or "").strip()
            if path and path not in working_set_paths:
                working_set_paths.append(path)
        if working_set_paths:
            focused_file = working_set_paths[0]
            focused_file_source = "recent_change" if recent_change_count else "explicit_context"
        active_plan_id = str(metadata.get("background_active_plan_id") or "").strip()
        if active_plan_id:
            planning_artifacts = (
                state.planning_artifact_history
                if state.planning_artifact_history
                else state.recent_planning_artifacts
            )
            matching_artifact = next(
                (item for item in planning_artifacts if str(item.artifact_id) == active_plan_id),
                None,
            )
            if matching_artifact is not None:
                active_plan_summary = f"{active_plan_id} ({matching_artifact.kind}: {matching_artifact.goal})"
            else:
                active_plan_summary = active_plan_id
        saved_tasks = list(state.saved_task_records)
        for payload in reversed(saved_tasks):
            if not isinstance(payload, dict):
                continue
            metadata_payload = payload.get("metadata")
            task_metadata = dict(metadata_payload) if isinstance(metadata_payload, dict) else {}
            task_role = str(task_metadata.get("task_role") or "").strip()
            plan_execution_mode = str(task_metadata.get("plan_execution_mode") or "").strip()
            child_execution_mode = str(task_metadata.get("child_execution_mode") or "").strip()
            task_kind = str(payload.get("kind") or "").strip()
            surface_kind = "other_task"
            if task_role == "background" or plan_execution_mode == "background_agent" or child_execution_mode == "background-agent":
                surface_kind = "background_execution"
                background_execution_count += 1
            elif task_role == "execution":
                surface_kind = "active_plan_execution"
                active_plan_execution_count += 1
            elif task_kind in {"agent", "ultraplan_scout"} and task_role == "scout":
                surface_kind = "child_execution"
            if primary_task is None and surface_kind in {"background_execution", "active_plan_execution"}:
                task_id = str(payload.get("id") or "").strip()
                if task_id:
                    primary_task = {
                        "task_id": task_id,
                        "status": str(payload.get("status") or "").strip(),
                        "kind": task_kind,
                        "description": str(payload.get("description") or "").strip(),
                        "progress_summary": (
                            str(payload.get("progress_summary"))
                            if payload.get("progress_summary") is not None
                            else None
                        ),
                        "output": (
                            str(payload.get("output"))
                            if payload.get("output") is not None
                            else None
                        ),
                        "error": (
                            str(payload.get("error"))
                            if payload.get("error") is not None
                            else None
                        ),
                        "updated_at": (
                            str(payload.get("updated_at"))
                            if payload.get("updated_at") is not None
                            else None
                        ),
                        "ended_at": (
                            str(payload.get("ended_at"))
                            if payload.get("ended_at") is not None
                            else None
                        ),
                        "surface_kind": surface_kind,
                        "parent_session_id": str(task_metadata.get("parent_session_id") or "").strip() or None,
                        "background_session_id": str(task_metadata.get("background_session_id") or "").strip() or None,
                        "background_reverse_hint": (
                            str(task_metadata.get("background_reverse_hint") or "").strip() or None
                        ),
                    }
                    primary_task.update(_background_runtime_fields_from_task_metadata(task_metadata))
        if primary_task is None:
            for payload in reversed(saved_tasks):
                if not isinstance(payload, dict):
                    continue
                task_id = str(payload.get("id") or "").strip()
                if not task_id:
                    continue
                primary_task = {
                    "task_id": task_id,
                    "status": str(payload.get("status") or "").strip(),
                    "kind": str(payload.get("kind") or "").strip(),
                    "description": str(payload.get("description") or "").strip(),
                    "progress_summary": (
                        str(payload.get("progress_summary"))
                        if payload.get("progress_summary") is not None
                        else None
                    ),
                    "output": (
                        str(payload.get("output"))
                        if payload.get("output") is not None
                        else None
                    ),
                    "error": (
                        str(payload.get("error"))
                        if payload.get("error") is not None
                        else None
                    ),
                    "updated_at": (
                        str(payload.get("updated_at"))
                        if payload.get("updated_at") is not None
                        else None
                    ),
                    "ended_at": (
                        str(payload.get("ended_at"))
                        if payload.get("ended_at") is not None
                        else None
                    ),
                    "surface_kind": "other_task",
                    "parent_session_id": None,
                    "background_session_id": None,
                    "background_reverse_hint": None,
                }
                task_metadata = dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {}
                primary_task.update(_background_runtime_fields_from_task_metadata(task_metadata))
                break
    if metadata["background_live_attachable"]:
        workflow_state = "attachable live background session"
        action_order = (
            "go_to_live_attach",
            "go_to_followup",
            "go_to_queue",
            "go_to_task",
            "go_to_logs",
            "go_to_history",
            "go_to_sessions_show",
            "go_to_saved_resume",
            "stay_on_surface",
        )
        history_actions = [f"pyclaude attach {record.bg_id}"]
    elif metadata["background_saved_resumable"]:
        workflow_state = "saved background session with resumable transcript"
        action_order = (
            "go_to_saved_resume",
            "go_to_queue",
            "go_to_task",
            "go_to_logs",
            "go_to_history",
            "go_to_sessions_show",
            "go_to_live_attach",
            "stay_on_surface",
        )
        history_actions = [f"pyclaude --resume-session {record.session_id} repl"] if record.session_id else []
    else:
        workflow_state = "inactive background record for inspection"
        action_order = (
            "go_to_logs",
            "go_to_task",
            "go_to_sessions_show",
            "go_to_history",
            "go_to_live_attach",
            "go_to_saved_resume",
            "stay_on_surface",
        )
        history_actions = []
    action_groups = {
        "go_to_live_attach": [str(metadata["background_go_to_live_attach"])],
        "go_to_saved_resume": [str(metadata["background_go_to_saved_resume"])],
        "go_to_task": (
            [f"/task show {primary_task['task_id']}", "/tasks active"]
            if primary_task is not None and primary_task.get("task_id")
            else ["/tasks active"]
        ),
        "go_to_logs": [f"pyclaude logs {record.bg_id} summary", f"pyclaude logs {record.bg_id}"],
        "go_to_sessions_show": ["pyclaude sessions --limit 10"],
        "go_to_history": history_actions,
        "go_to_followup": (
            [f"session.action background_send_followup {record.bg_id}"]
            if metadata["background_live_attachable"]
            else []
        ),
        "go_to_queue": [f"session.action background_queue_message {record.bg_id}"],
        "stay_on_surface": [str(metadata["background_stay_on_surface"])],
    }
    task_surface_counts = metadata["background_task_surface_counts"]
    task_surface_summary = ",".join(
        f"{key}:{int(value)}"
        for key, value in task_surface_counts.items()
        if isinstance(value, int) and value > 0
    )
    if background_execution_count == 0:
        try:
            background_execution_count = int(task_surface_counts.get("background_execution", 0))
        except (TypeError, ValueError, AttributeError):
            background_execution_count = 0
    if active_plan_execution_count == 0:
        try:
            active_plan_execution_count = int(task_surface_counts.get("active_plan_execution", 0))
        except (TypeError, ValueError, AttributeError):
            active_plan_execution_count = 0
    payload = {
        "background_current_workflow_summary": workflow_state,
        "background_task_surface_summary": task_surface_summary or "none",
        "background_background_execution_count": background_execution_count,
        "background_active_plan_execution_count": active_plan_execution_count,
        "background_primary_task": primary_task,
        "background_recent_change_count": recent_change_count,
        "background_latest_change_summary": latest_change_summary,
        "background_latest_change_tool_name": latest_change_tool_name,
        "background_latest_change_file_count": latest_change_file_count,
        "background_working_set_file_count": len(working_set_paths),
        "background_working_set_paths": working_set_paths,
        "background_focused_file": focused_file,
        "background_focused_file_source": focused_file_source,
        "background_explicit_context_count": explicit_context_count,
        "background_active_plan_summary": active_plan_summary,
        "background_action_groups": action_groups,
        "background_action_order": action_order,
    }
    payload.update(
        _followup_payload(
            record,
            allow_send=bool(metadata["background_live_attachable"]),
        )
    )
    payload.update(
        _progress_payload(
            primary_task=primary_task,
            latest_change_summary=latest_change_summary,
            latest_change_tool_name=latest_change_tool_name,
            tool_use_count=tool_use_count,
            message_count=metadata["background_last_known_message_count"],
        )
    )
    payload.update(
        _completion_payload(
            status=str(record.status or ""),
            primary_task=primary_task,
            latest_change_summary=latest_change_summary,
            logs_action=f"pyclaude logs {record.bg_id} summary",
            resume_action=(
                f"pyclaude --resume-session {record.session_id} repl"
                if record.session_id
                else None
            ),
            transcript_exists=bool(metadata["background_transcript_exists"]),
        )
    )
    return payload
