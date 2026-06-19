from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, UTC
from hashlib import sha1
from pathlib import Path
import json

from ..config import SessionConfig
from ..state import (
    AdvisorReviewSummary,
    ExplicitContextEntry,
    HistoryBoundary,
    PlanningArtifact,
    SessionState,
    ToolResultArtifactRecord,
    ToolResultReplacementRecord,
    WorkspaceChangeSet,
    WorkspaceFileChange,
)
from ..workspace.isolation import derive_workspace_health


@dataclass(slots=True)
class TranscriptSummary:
    session_id: str
    path: Path
    created_at: str | None
    updated_at: str | None
    provider: str | None
    model: str | None
    cwd: str | None
    original_cwd: str | None
    effective_cwd: str | None
    workspace_mode: str | None
    workspace_label: str | None
    workspace_created_at: str | None
    workspace_health: str | None
    workspace_cleanup_status: str | None
    workspace_unavailable: bool
    workspace_unavailable_reason: str | None
    workspace_fallback_cwd: str | None
    session_runtime_mode: str | None
    pre_plan_mode: str | None
    has_exited_plan_mode: bool
    needs_plan_mode_exit_attachment: bool
    needs_plan_mode_reentry_attachment: bool
    plan_mode_attachment_count: int
    plan_mode_exit_approved_plan: str | None
    plan_mode_exit_restored_mode: str | None
    plan_slug: str | None
    session_execution_mode: str | None
    session_command_policy_name: str | None
    session_command_policy_source: str | None
    session_command_policy_allowed_tool_names: tuple[str, ...]
    session_command_policy_allowed_bash_prefixes: tuple[str, ...]
    session_command_policy_require_read_only_subagents: bool
    message_count: int
    context_summary_present: bool
    history_boundary_count: int
    compact_boundary_count: int
    last_history_boundary_kind: str | None
    last_history_boundary_at: str | None
    last_compact_boundary_trigger: str | None
    last_compact_boundary_reason: str | None
    last_compact_boundary_summary: str | None
    active_planning_artifact_id: str | None
    planning_artifact_count: int
    task_surface_counts: dict[str, int]


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def get_session_storage_dir(cwd: Path) -> Path:
    return cwd / ".pyclaude" / "sessions"


def get_session_path(cwd: Path, session_id: str) -> Path:
    return get_session_storage_dir(cwd) / f"{session_id}.json"


def save_transcript(config: SessionConfig, state: SessionState) -> Path:
    transcript_cwd = config.transcript_cwd or config.cwd
    storage_dir = get_session_storage_dir(transcript_cwd)
    storage_dir.mkdir(parents=True, exist_ok=True)
    state.updated_at = utc_now_iso()
    workspace_health = derive_workspace_health(
        workspace_mode=state.workspace_mode,
        workspace_cleanup_status=state.workspace_cleanup_status,
        workspace_unavailable=bool(state.workspace_unavailable),
    )
    planning_artifacts = (
        state.planning_artifact_history
        if state.planning_artifact_history
        else state.recent_planning_artifacts
    )
    payload = {
        "version": 1,
        "session_id": state.session_id,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
        "session_runtime_mode": state.session_runtime_mode,
        "pre_plan_mode": state.pre_plan_mode,
        "has_exited_plan_mode": state.has_exited_plan_mode,
        "needs_plan_mode_exit_attachment": state.needs_plan_mode_exit_attachment,
        "needs_plan_mode_reentry_attachment": state.needs_plan_mode_reentry_attachment,
        "plan_mode_attachment_count": state.plan_mode_attachment_count,
        "plan_mode_exit_approved_plan": state.plan_mode_exit_approved_plan,
        "plan_mode_exit_restored_mode": state.plan_mode_exit_restored_mode,
        "plan_slug": state.plan_slug,
        "session_execution_mode": state.session_execution_mode,
        "session_command_policy_name": state.session_command_policy_name,
        "session_command_policy_source": state.session_command_policy_source,
        "session_command_policy_allowed_tool_names": state.session_command_policy_allowed_tool_names,
        "session_command_policy_allowed_bash_prefixes": state.session_command_policy_allowed_bash_prefixes,
        "session_command_policy_require_read_only_subagents": state.session_command_policy_require_read_only_subagents,
        "original_cwd": state.original_cwd,
        "effective_cwd": state.effective_cwd,
        "workspace_mode": state.workspace_mode,
        "workspace_label": state.workspace_label,
        "workspace_created_at": state.workspace_created_at,
        "workspace_health": workspace_health,
        "workspace_cleanup_status": state.workspace_cleanup_status,
        "workspace_cleanup_error": state.workspace_cleanup_error,
        "workspace_unavailable": state.workspace_unavailable,
        "workspace_unavailable_reason": state.workspace_unavailable_reason,
        "workspace_fallback_cwd": state.workspace_fallback_cwd,
        "context_summary": state.context_summary,
        "history_boundaries": [asdict(item) for item in state.history_boundaries],
        "explicit_context_entries": [asdict(item) for item in state.explicit_context_entries],
        "advisor_model": state.advisor_model,
        "advisor_mode": state.advisor_mode,
        "advisor_last_result": asdict(state.advisor_last_result) if state.advisor_last_result is not None else None,
        "advisor_review_history": [asdict(item) for item in state.advisor_review_history],
        "active_execution_constraint": state.active_execution_constraint,
        "constraint_source": state.constraint_source,
        "constraint_reason": state.constraint_reason,
        "constraint_trigger_count": state.constraint_trigger_count,
        "active_execution_plan_id": state.active_execution_plan_id,
        "plan_execution_count": state.plan_execution_count,
        "plan_drift_count": state.plan_drift_count,
        "last_plan_drift_status": state.last_plan_drift_status,
        "last_plan_drift_reason": state.last_plan_drift_reason,
        "last_plan_drift_context": state.last_plan_drift_context,
        "enabled_plugin_names": state.enabled_plugin_names,
        "disabled_plugin_names": state.disabled_plugin_names,
        "enabled_skill_names": state.enabled_skill_names,
        "disabled_skill_names": state.disabled_skill_names,
        "session_permission_rules": list(state.session_permission_rules),
        "activated_deferred_tool_names": state.activated_deferred_tool_names,
        "cwd": str(config.cwd),
        "transcript_cwd": str(transcript_cwd),
        "provider": config.provider,
        "model": config.model,
        "message_count": len(state.messages),
        "messages": state.messages,
        "tool_result_replacement_records": [
            asdict(item) for item in state.tool_result_replacement_records
        ],
        "tool_result_artifact_records": [
            asdict(item) for item in state.tool_result_artifact_records
        ],
        "recent_change_sets": [asdict(item) for item in state.recent_change_sets],
        "undone_change_sets": [asdict(item) for item in state.undone_change_sets],
        "saved_task_records": list(state.saved_task_records),
        "saved_task_surface_counts": dict(state.saved_task_surface_counts),
        "active_planning_artifact_id": state.active_planning_artifact_id,
        "planning_artifact_history": [asdict(item) for item in planning_artifacts],
        "recent_planning_artifacts": [asdict(item) for item in planning_artifacts],
    }
    path = get_session_path(transcript_cwd, state.session_id)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return path


def load_transcript(path: Path) -> SessionState:
    payload = _read_transcript_payload(path)
    planning_artifacts = [
        item
        for item in (
            _load_planning_artifact(entry)
            for entry in payload.get(
                "planning_artifact_history",
                payload.get("recent_planning_artifacts", []),
            )
        )
        if item is not None
    ]
    workspace_mode = str(payload.get("workspace_mode", "main") or "main")
    workspace_cleanup_status = str(payload.get("workspace_cleanup_status", "none") or "none")
    workspace_unavailable = bool(payload.get("workspace_unavailable", False))
    return SessionState(
        session_id=payload["session_id"],
        created_at=payload.get("created_at", utc_now_iso()),
        updated_at=payload.get("updated_at"),
        session_runtime_mode=str(payload.get("session_runtime_mode", "default") or "default"),
        pre_plan_mode=(
            str(payload.get("pre_plan_mode"))
            if payload.get("pre_plan_mode") is not None
            else None
        ),
        has_exited_plan_mode=bool(payload.get("has_exited_plan_mode", False)),
        needs_plan_mode_exit_attachment=bool(payload.get("needs_plan_mode_exit_attachment", False)),
        needs_plan_mode_reentry_attachment=bool(
            payload.get("needs_plan_mode_reentry_attachment", False)
        ),
        plan_mode_attachment_count=max(
            int(payload.get("plan_mode_attachment_count", 0) or 0),
            0,
        ),
        plan_mode_exit_approved_plan=(
            str(payload.get("plan_mode_exit_approved_plan"))
            if payload.get("plan_mode_exit_approved_plan") is not None
            else None
        ),
        plan_mode_exit_restored_mode=(
            str(payload.get("plan_mode_exit_restored_mode"))
            if payload.get("plan_mode_exit_restored_mode") is not None
            else None
        ),
        plan_slug=(
            str(payload.get("plan_slug"))
            if payload.get("plan_slug") is not None
            else None
        ),
        session_execution_mode=str(payload.get("session_execution_mode", "main") or "main"),
        session_command_policy_name=(
            str(payload.get("session_command_policy_name"))
            if payload.get("session_command_policy_name") is not None
            else None
        ),
        session_command_policy_source=(
            str(payload.get("session_command_policy_source"))
            if payload.get("session_command_policy_source") is not None
            else None
        ),
        session_command_policy_allowed_tool_names=[
            str(item) for item in payload.get("session_command_policy_allowed_tool_names", []) if item is not None
        ],
        session_command_policy_allowed_bash_prefixes=[
            str(item)
            for item in payload.get("session_command_policy_allowed_bash_prefixes", [])
            if item is not None
        ],
        session_command_policy_require_read_only_subagents=bool(
            payload.get("session_command_policy_require_read_only_subagents", False)
        ),
        original_cwd=payload.get("original_cwd"),
        effective_cwd=payload.get("effective_cwd"),
        workspace_mode=workspace_mode,
        workspace_label=(
            str(payload.get("workspace_label"))
            if payload.get("workspace_label") is not None
            else None
        ),
        workspace_created_at=(
            str(payload.get("workspace_created_at"))
            if payload.get("workspace_created_at") is not None
            else None
        ),
        workspace_health=str(
            payload.get(
                "workspace_health",
                derive_workspace_health(
                    workspace_mode=workspace_mode,
                    workspace_cleanup_status=workspace_cleanup_status,
                    workspace_unavailable=workspace_unavailable,
                ),
            )
            or "healthy"
        ),
        workspace_cleanup_status=workspace_cleanup_status,
        workspace_cleanup_error=(
            str(payload.get("workspace_cleanup_error"))
            if payload.get("workspace_cleanup_error") is not None
            else None
        ),
        workspace_unavailable=workspace_unavailable,
        workspace_unavailable_reason=(
            str(payload.get("workspace_unavailable_reason"))
            if payload.get("workspace_unavailable_reason") is not None
            else None
        ),
        workspace_fallback_cwd=(
            str(payload.get("workspace_fallback_cwd"))
            if payload.get("workspace_fallback_cwd") is not None
            else None
        ),
        context_summary=payload.get("context_summary"),
        history_boundaries=[
            HistoryBoundary(
                boundary_id=str(entry.get("boundary_id") or ""),
                kind=str(entry.get("kind") or ""),
                created_at=str(entry.get("created_at") or utc_now_iso()),
                trigger=str(entry.get("trigger") or ""),
                trigger_reason=(
                    str(entry.get("trigger_reason"))
                    if entry.get("trigger_reason") is not None
                    else None
                ),
                summary=str(entry.get("summary") or ""),
                compaction_mode=str(entry.get("compaction_mode") or ""),
                message_count_before=int(entry.get("message_count_before", 0) or 0),
                message_count_after=int(entry.get("message_count_after", 0) or 0),
                compacted_count=int(entry.get("compacted_count", 0) or 0),
                kept_count=int(entry.get("kept_count", 0) or 0),
                context_summary_chars_before=int(entry.get("context_summary_chars_before", 0) or 0),
                context_summary_chars_after=int(entry.get("context_summary_chars_after", 0) or 0),
                instructions=(
                    str(entry.get("instructions"))
                    if entry.get("instructions") is not None
                    else None
                ),
                old_session_id=(
                    str(entry.get("old_session_id"))
                    if entry.get("old_session_id") is not None
                    else None
                ),
                new_session_id=(
                    str(entry.get("new_session_id"))
                    if entry.get("new_session_id") is not None
                    else None
                ),
                target_boundary_id=(
                    str(entry.get("target_boundary_id"))
                    if entry.get("target_boundary_id") is not None
                    else None
                ),
                snapshot_messages=(
                    list(entry.get("snapshot_messages", []))
                    if entry.get("snapshot_messages") is not None
                    else None
                ),
                snapshot_context_summary=(
                    str(entry.get("snapshot_context_summary"))
                    if entry.get("snapshot_context_summary") is not None
                    else None
                ),
            )
            for entry in payload.get("history_boundaries", [])
            if isinstance(entry, dict)
        ],
        explicit_context_entries=[
            ExplicitContextEntry(
                raw_path=str(entry.get("raw_path") or ""),
                resolved_path=str(entry.get("resolved_path") or ""),
                kind=str(entry.get("kind") or "file"),
                added_at=str(entry.get("added_at") or utc_now_iso()),
                resolved=bool(entry.get("resolved", True)),
            )
            for entry in payload.get("explicit_context_entries", [])
            if isinstance(entry, dict)
        ],
        advisor_model=payload.get("advisor_model"),
        advisor_mode=_resolve_advisor_mode(payload),
        advisor_last_result=_load_advisor_summary(payload.get("advisor_last_result")),
        advisor_review_history=[
            item
            for item in (_load_advisor_summary(entry) for entry in payload.get("advisor_review_history", []))
            if item is not None
        ],
        active_execution_constraint=str(payload.get("active_execution_constraint", "normal") or "normal"),
        constraint_source=(
            str(payload.get("constraint_source"))
            if payload.get("constraint_source") is not None
            else None
        ),
        constraint_reason=(
            str(payload.get("constraint_reason"))
            if payload.get("constraint_reason") is not None
            else None
        ),
        constraint_trigger_count=int(payload.get("constraint_trigger_count", 0) or 0),
        active_execution_plan_id=(
            str(payload.get("active_execution_plan_id"))
            if payload.get("active_execution_plan_id") is not None
            else None
        ),
        plan_execution_count=int(payload.get("plan_execution_count", 0) or 0),
        plan_drift_count=int(payload.get("plan_drift_count", 0) or 0),
        last_plan_drift_status=(
            str(payload.get("last_plan_drift_status"))
            if payload.get("last_plan_drift_status") is not None
            else None
        ),
        last_plan_drift_reason=(
            str(payload.get("last_plan_drift_reason"))
            if payload.get("last_plan_drift_reason") is not None
            else None
        ),
        last_plan_drift_context=(
            str(payload.get("last_plan_drift_context"))
            if payload.get("last_plan_drift_context") is not None
            else None
        ),
        enabled_plugin_names=list(payload.get("enabled_plugin_names", [])),
        disabled_plugin_names=list(payload.get("disabled_plugin_names", [])),
        enabled_skill_names=list(payload.get("enabled_skill_names", [])),
        disabled_skill_names=list(payload.get("disabled_skill_names", [])),
        session_permission_rules=[
            {
                "decision": str(item.get("decision", "")),
                "scope": str(item.get("scope", "")),
                "value": str(item.get("value", "")),
            }
            for item in payload.get("session_permission_rules", [])
            if isinstance(item, dict)
        ],
        activated_deferred_tool_names=list(payload.get("activated_deferred_tool_names", [])),
        messages=list(payload.get("messages", [])),
        tool_result_replacement_records=[
            ToolResultReplacementRecord(
                tool_use_id=str(item.get("tool_use_id") or ""),
                replacement=str(item.get("replacement") or ""),
                original_size_chars=int(item.get("original_size_chars", 0) or 0),
                replacement_size_chars=int(item.get("replacement_size_chars", 0) or 0),
                created_at=str(item.get("created_at") or utc_now_iso()),
                reason=str(item.get("reason") or "message_budget"),
            )
            for item in payload.get("tool_result_replacement_records", [])
            if isinstance(item, dict)
        ],
        tool_result_artifact_records=[
            ToolResultArtifactRecord(
                tool_use_id=str(item.get("tool_use_id") or ""),
                artifact_path=str(item.get("artifact_path") or ""),
                content_sha256=str(item.get("content_sha256") or ""),
                original_size_chars=int(item.get("original_size_chars", 0) or 0),
                preview_size_chars=int(item.get("preview_size_chars", 0) or 0),
                summary=str(item.get("summary") or ""),
                created_at=str(item.get("created_at") or utc_now_iso()),
                reason=str(item.get("reason") or "message_budget"),
            )
            for item in payload.get("tool_result_artifact_records", [])
            if isinstance(item, dict)
        ],
        recent_change_sets=[
            WorkspaceChangeSet(
                change_id=item.get("change_id", utc_now_iso()),
                created_at=item.get("created_at", utc_now_iso()),
                tool_name=item.get("tool_name", ""),
                summary=item.get("summary", ""),
                change_kind=str(item.get("change_kind", "workspace_change") or "workspace_change"),
                undoable=bool(item.get("undoable", True)),
                files=[
                    WorkspaceFileChange(
                        path=file_item["path"],
                        existed_before=bool(file_item.get("existed_before", False)),
                        before_content=file_item.get("before_content", ""),
                        after_content=file_item.get("after_content"),
                        action_kind=str(file_item.get("action_kind", "") or ""),
                        source_path=(
                            str(file_item.get("source_path"))
                            if file_item.get("source_path") is not None
                            else None
                        ),
                        replacement_count=(
                            int(file_item["replacement_count"])
                            if file_item.get("replacement_count") is not None
                            else None
                        ),
                        change_mode=str(file_item.get("change_mode", "") or ""),
                    )
                    for file_item in item.get("files", [])
                ],
            )
            for item in payload.get("recent_change_sets", [])
        ],
        undone_change_sets=[
            WorkspaceChangeSet(
                change_id=item.get("change_id", utc_now_iso()),
                created_at=item.get("created_at", utc_now_iso()),
                tool_name=item.get("tool_name", ""),
                summary=item.get("summary", ""),
                change_kind=str(item.get("change_kind", "workspace_change") or "workspace_change"),
                undoable=bool(item.get("undoable", True)),
                files=[
                    WorkspaceFileChange(
                        path=file_item["path"],
                        existed_before=bool(file_item.get("existed_before", False)),
                        before_content=file_item.get("before_content", ""),
                        after_content=file_item.get("after_content"),
                        action_kind=str(file_item.get("action_kind", "") or ""),
                        source_path=(
                            str(file_item.get("source_path"))
                            if file_item.get("source_path") is not None
                            else None
                        ),
                        replacement_count=(
                            int(file_item["replacement_count"])
                            if file_item.get("replacement_count") is not None
                            else None
                        ),
                        change_mode=str(file_item.get("change_mode", "") or ""),
                    )
                    for file_item in item.get("files", [])
                ],
            )
            for item in payload.get("undone_change_sets", [])
        ],
        saved_task_records=[
            dict(item) for item in payload.get("saved_task_records", []) if isinstance(item, dict)
        ],
        saved_task_surface_counts=_normalize_task_surface_counts(payload.get("saved_task_surface_counts")),
        active_planning_artifact_id=(
            str(payload.get("active_planning_artifact_id"))
            if payload.get("active_planning_artifact_id") is not None
            else None
        ),
        planning_artifact_history=list(planning_artifacts),
        recent_planning_artifacts=list(planning_artifacts),
    )


def load_transcript_by_session_id(cwd: Path, session_id: str) -> tuple[SessionState | None, Path | None]:
    path = get_session_path(cwd, session_id)
    if not path.exists():
        for storage_dir in _iter_session_storage_dirs(cwd):
            candidate = storage_dir / f"{session_id}.json"
            if candidate.exists():
                path = candidate
                break
        else:
            return None, None
    return load_transcript(path), path


def list_transcripts(cwd: Path, *, limit: int | None = None) -> list[TranscriptSummary]:
    summaries_by_session_id: dict[str, TranscriptSummary] = {}
    for storage_dir in _iter_session_storage_dirs(cwd):
        if not storage_dir.exists():
            continue
        for path in storage_dir.glob("*.json"):
            if not path.is_file():
                continue
            try:
                payload = _read_transcript_payload(path)
            except Exception:  # noqa: BLE001
                continue
            summary = TranscriptSummary(
                session_id=payload.get("session_id", path.stem),
                path=path,
                created_at=payload.get("created_at"),
                updated_at=payload.get("updated_at"),
                provider=payload.get("provider"),
                model=payload.get("model"),
                cwd=payload.get("cwd"),
                original_cwd=payload.get("original_cwd"),
                effective_cwd=payload.get("effective_cwd"),
                workspace_mode=payload.get("workspace_mode"),
                workspace_label=payload.get("workspace_label"),
                workspace_created_at=payload.get("workspace_created_at"),
                workspace_health=payload.get(
                    "workspace_health",
                    derive_workspace_health(
                        workspace_mode=str(payload.get("workspace_mode", "main") or "main"),
                        workspace_cleanup_status=str(
                            payload.get("workspace_cleanup_status", "none") or "none"
                        ),
                        workspace_unavailable=bool(payload.get("workspace_unavailable", False)),
                    ),
                ),
                workspace_cleanup_status=payload.get("workspace_cleanup_status"),
                workspace_unavailable=bool(payload.get("workspace_unavailable", False)),
                workspace_unavailable_reason=payload.get("workspace_unavailable_reason"),
                workspace_fallback_cwd=payload.get("workspace_fallback_cwd"),
                session_runtime_mode=payload.get("session_runtime_mode"),
                pre_plan_mode=payload.get("pre_plan_mode"),
                has_exited_plan_mode=bool(payload.get("has_exited_plan_mode", False)),
                needs_plan_mode_exit_attachment=bool(
                    payload.get("needs_plan_mode_exit_attachment", False)
                ),
                needs_plan_mode_reentry_attachment=bool(
                    payload.get("needs_plan_mode_reentry_attachment", False)
                ),
                plan_mode_attachment_count=max(
                    int(payload.get("plan_mode_attachment_count", 0) or 0),
                    0,
                ),
                plan_mode_exit_approved_plan=payload.get("plan_mode_exit_approved_plan"),
                plan_mode_exit_restored_mode=payload.get("plan_mode_exit_restored_mode"),
                plan_slug=payload.get("plan_slug"),
                session_execution_mode=payload.get("session_execution_mode"),
                session_command_policy_name=payload.get("session_command_policy_name"),
                session_command_policy_source=payload.get("session_command_policy_source"),
                session_command_policy_allowed_tool_names=tuple(
                    str(item)
                    for item in payload.get("session_command_policy_allowed_tool_names", [])
                    if item is not None
                ),
                session_command_policy_allowed_bash_prefixes=tuple(
                    str(item)
                    for item in payload.get("session_command_policy_allowed_bash_prefixes", [])
                    if item is not None
                ),
                session_command_policy_require_read_only_subagents=bool(
                    payload.get("session_command_policy_require_read_only_subagents", False)
                ),
                message_count=int(payload.get("message_count", len(payload.get("messages", [])))),
                context_summary_present=bool(payload.get("context_summary")),
                history_boundary_count=len(payload.get("history_boundaries", [])),
                compact_boundary_count=sum(
                    1
                    for item in payload.get("history_boundaries", [])
                    if isinstance(item, dict) and str(item.get("kind") or "") == "compact"
                ),
                last_history_boundary_kind=_last_history_boundary_field(payload, "kind"),
                last_history_boundary_at=_last_history_boundary_field(payload, "created_at"),
                last_compact_boundary_trigger=_last_matching_history_boundary_field(
                    payload,
                    kind="compact",
                    field="trigger",
                ),
                last_compact_boundary_reason=_last_matching_history_boundary_field(
                    payload,
                    kind="compact",
                    field="trigger_reason",
                ),
                last_compact_boundary_summary=_last_matching_history_boundary_field(
                    payload,
                    kind="compact",
                    field="summary",
                ),
                active_planning_artifact_id=(
                    str(payload.get("active_planning_artifact_id"))
                    if payload.get("active_planning_artifact_id") is not None
                    else None
                ),
                planning_artifact_count=len(
                    payload.get(
                        "planning_artifact_history",
                        payload.get("recent_planning_artifacts", []),
                    )
                ),
                task_surface_counts=_summary_task_surface_counts(payload),
            )
            current = summaries_by_session_id.get(summary.session_id)
            if current is None or _summary_sort_key(summary) > _summary_sort_key(current):
                summaries_by_session_id[summary.session_id] = summary
    summaries = list(summaries_by_session_id.values())
    summaries.sort(
        key=lambda item: (
            item.updated_at or "",
            item.created_at or "",
            item.session_id,
        ),
        reverse=True,
    )
    if limit is not None:
        return summaries[:limit]
    return summaries


def load_latest_transcript(cwd: Path) -> tuple[SessionState | None, Path | None]:
    summaries = list_transcripts(cwd, limit=1)
    if not summaries:
        return None, None
    latest = summaries[0].path
    return load_transcript(latest), latest


def _last_history_boundary_field(payload: dict, field: str) -> str | None:
    boundaries = payload.get("history_boundaries", [])
    if not isinstance(boundaries, list) or not boundaries:
        return None
    for item in reversed(boundaries):
        if isinstance(item, dict) and item.get(field) is not None:
            return str(item.get(field))
    return None


def _last_matching_history_boundary_field(payload: dict, *, kind: str, field: str) -> str | None:
    boundaries = payload.get("history_boundaries", [])
    if not isinstance(boundaries, list) or not boundaries:
        return None
    for item in reversed(boundaries):
        if not isinstance(item, dict):
            continue
        if str(item.get("kind") or "") != kind:
            continue
        if item.get(field) is not None:
            return str(item.get(field))
    return None


def update_transcript_workspace_metadata(
    path: Path,
    *,
    original_cwd: str,
    effective_cwd: str,
    workspace_mode: str,
    workspace_label: str | None,
    workspace_created_at: str | None,
    workspace_health: str,
    workspace_cleanup_status: str,
    workspace_cleanup_error: str | None,
    workspace_unavailable: bool,
    workspace_unavailable_reason: str | None,
    workspace_fallback_cwd: str | None,
) -> None:
    payload = _read_transcript_payload(path)
    payload["original_cwd"] = original_cwd
    payload["effective_cwd"] = effective_cwd
    payload["workspace_mode"] = workspace_mode
    payload["workspace_label"] = workspace_label
    payload["workspace_created_at"] = workspace_created_at
    payload["workspace_health"] = workspace_health
    payload["workspace_cleanup_status"] = workspace_cleanup_status
    payload["workspace_cleanup_error"] = workspace_cleanup_error
    payload["workspace_unavailable"] = workspace_unavailable
    payload["workspace_unavailable_reason"] = workspace_unavailable_reason
    payload["workspace_fallback_cwd"] = workspace_fallback_cwd
    payload["updated_at"] = utc_now_iso()
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _read_transcript_payload(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_session_storage_dirs(cwd: Path) -> list[Path]:
    root = cwd.resolve()
    discovered: list[Path] = []
    seen: set[Path] = set()
    for directory in _candidate_session_storage_dirs(root):
        resolved = directory.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        discovered.append(resolved)
    return discovered


def _candidate_session_storage_dirs(root: Path) -> list[Path]:
    directories = [get_session_storage_dir(root)]
    pyclaude_dir = root / ".pyclaude"
    for group_name in ("workspaces", "worktrees"):
        parent = pyclaude_dir / group_name
        if not parent.exists():
            continue
        for child in parent.iterdir():
            if not child.is_dir():
                continue
            directories.append(get_session_storage_dir(child))
    return directories


def _summary_sort_key(summary: TranscriptSummary) -> tuple[str, str, str]:
    return (
        summary.updated_at or "",
        summary.created_at or "",
        summary.session_id,
    )


def _resolve_advisor_mode(payload: dict) -> str:
    advisor_mode = payload.get("advisor_mode")
    if isinstance(advisor_mode, str) and advisor_mode:
        return advisor_mode
    return "final-review" if payload.get("advisor_model") else "off"


def _load_advisor_summary(payload: object) -> AdvisorReviewSummary | None:
    if not isinstance(payload, dict):
        return None
    checkpoint = payload.get("checkpoint")
    status = payload.get("status")
    if not isinstance(checkpoint, str) or not checkpoint or not isinstance(status, str) or not status:
        return None
    return AdvisorReviewSummary(
        checkpoint=checkpoint,
        status=status,
        reason=str(payload.get("reason", "")),
        suggested_changes=[str(item) for item in payload.get("suggested_changes", [])],
        risk_flags=[str(item) for item in payload.get("risk_flags", [])],
        model=str(payload.get("model", "")),
        created_at=str(payload.get("created_at", utc_now_iso())),
    )


def _load_planning_artifact(payload: object) -> PlanningArtifact | None:
    if not isinstance(payload, dict):
        return None
    kind = payload.get("kind")
    goal = payload.get("goal")
    summary = payload.get("summary")
    if not isinstance(kind, str) or not kind or not isinstance(goal, str) or not isinstance(summary, str):
        return None
    advisor_status = payload.get("advisor_status")
    return PlanningArtifact(
        kind=kind,
        goal=goal,
        summary=summary,
        artifact_id=str(payload.get("artifact_id", _load_legacy_artifact_id(payload))),
        supersedes_artifact_id=(
            str(payload.get("supersedes_artifact_id"))
            if payload.get("supersedes_artifact_id") is not None
            else None
        ),
        superseded_by_artifact_id=(
            str(payload.get("superseded_by_artifact_id"))
            if payload.get("superseded_by_artifact_id") is not None
            else None
        ),
        derived_from_drift=bool(payload.get("derived_from_drift", False)),
        derivation_reason=str(payload.get("derivation_reason", "") or ""),
        used_read_only_subagents=bool(payload.get("used_read_only_subagents", False)),
        scout_categories=[str(item) for item in payload.get("scout_categories", [])],
        task_ids=[str(item) for item in payload.get("task_ids", [])],
        advisor_status=str(advisor_status) if isinstance(advisor_status, str) and advisor_status else None,
        advisor_reason=str(payload.get("advisor_reason", "") or ""),
        advisor_suggested_changes=[str(item) for item in payload.get("advisor_suggested_changes", [])],
        advisor_risk_flags=[str(item) for item in payload.get("advisor_risk_flags", [])],
        created_at=str(payload.get("created_at", utc_now_iso())),
    )


def _load_legacy_artifact_id(payload: dict) -> str:
    goal = str(payload.get("goal", ""))
    created_at = str(payload.get("created_at", ""))
    digest = sha1(f"{goal}\n{created_at}".encode("utf-8")).hexdigest()
    return f"plan-{digest[:10]}"


def _normalize_task_surface_counts(payload: object) -> dict[str, int]:
    if not isinstance(payload, dict):
        return {}
    counts: dict[str, int] = {}
    for key, value in payload.items():
        try:
            counts[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return counts


def _summary_task_surface_counts(payload: dict) -> dict[str, int]:
    counts = _normalize_task_surface_counts(payload.get("saved_task_surface_counts"))
    if counts:
        return counts
    fallback = {
        "checklist": 0,
        "workspace_maintenance": 0,
        "child_execution": 0,
        "background_execution": 0,
        "active_plan_execution": 0,
        "other_task": 0,
    }
    for item in payload.get("saved_task_records", []):
        if not isinstance(item, dict):
            continue
        task_kind = str(item.get("kind") or "").strip()
        metadata = item.get("metadata")
        task_metadata = dict(metadata) if isinstance(metadata, dict) else {}
        surface_kind = "other_task"
        task_role = str(task_metadata.get("task_role") or "").strip()
        if task_kind == "workspace":
            surface_kind = "workspace_maintenance"
        elif task_role == "execution":
            surface_kind = "active_plan_execution"
        elif task_kind in {"agent", "ultraplan_scout"}:
            surface_kind = "child_execution" if task_role == "scout" else "background_execution"
        fallback[surface_kind] = fallback.get(surface_kind, 0) + 1
    return fallback
