from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, UTC
from hashlib import sha1
from pathlib import Path
import json

from ..config import SessionConfig
from ..state import (
    AdvisorReviewSummary,
    PlanningArtifact,
    SessionState,
    WorkspaceChangeSet,
    WorkspaceFileChange,
)


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
    message_count: int
    context_summary_present: bool


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
        "original_cwd": state.original_cwd,
        "effective_cwd": state.effective_cwd,
        "workspace_mode": state.workspace_mode,
        "context_summary": state.context_summary,
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
        "cwd": str(config.cwd),
        "transcript_cwd": str(transcript_cwd),
        "provider": config.provider,
        "model": config.model,
        "message_count": len(state.messages),
        "messages": state.messages,
        "recent_change_sets": [asdict(item) for item in state.recent_change_sets],
        "undone_change_sets": [asdict(item) for item in state.undone_change_sets],
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
    return SessionState(
        session_id=payload["session_id"],
        created_at=payload.get("created_at", utc_now_iso()),
        updated_at=payload.get("updated_at"),
        original_cwd=payload.get("original_cwd"),
        effective_cwd=payload.get("effective_cwd"),
        workspace_mode=str(payload.get("workspace_mode", "main") or "main"),
        context_summary=payload.get("context_summary"),
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
        messages=list(payload.get("messages", [])),
        recent_change_sets=[
            WorkspaceChangeSet(
                change_id=item.get("change_id", utc_now_iso()),
                created_at=item.get("created_at", utc_now_iso()),
                tool_name=item.get("tool_name", ""),
                summary=item.get("summary", ""),
                files=[
                    WorkspaceFileChange(
                        path=file_item["path"],
                        existed_before=bool(file_item.get("existed_before", False)),
                        before_content=file_item.get("before_content", ""),
                        after_content=file_item.get("after_content"),
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
                files=[
                    WorkspaceFileChange(
                        path=file_item["path"],
                        existed_before=bool(file_item.get("existed_before", False)),
                        before_content=file_item.get("before_content", ""),
                        after_content=file_item.get("after_content"),
                    )
                    for file_item in item.get("files", [])
                ],
            )
            for item in payload.get("undone_change_sets", [])
        ],
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
        return None, None
    return load_transcript(path), path


def list_transcripts(cwd: Path, *, limit: int | None = None) -> list[TranscriptSummary]:
    storage_dir = get_session_storage_dir(cwd)
    if not storage_dir.exists():
        return []
    summaries: list[TranscriptSummary] = []
    for path in storage_dir.glob("*.json"):
        if not path.is_file():
            continue
        try:
            payload = _read_transcript_payload(path)
        except Exception:  # noqa: BLE001
            continue
        summaries.append(
            TranscriptSummary(
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
                message_count=int(payload.get("message_count", len(payload.get("messages", [])))),
                context_summary_present=bool(payload.get("context_summary")),
            )
        )
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


def _read_transcript_payload(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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
