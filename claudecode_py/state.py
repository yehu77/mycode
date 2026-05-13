from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4
from datetime import datetime, UTC

from .models import Message


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _artifact_id() -> str:
    return uuid4().hex[:10]


@dataclass
class WorkspaceFileChange:
    path: str
    existed_before: bool
    before_content: str
    after_content: str | None
    action_kind: str = ""
    source_path: str | None = None
    replacement_count: int | None = None
    change_mode: str = ""


@dataclass
class WorkspaceChangeSet:
    change_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: str = field(default_factory=_utc_now_iso)
    tool_name: str = ""
    summary: str = ""
    change_kind: str = "workspace_change"
    undoable: bool = True
    files: list[WorkspaceFileChange] = field(default_factory=list)


@dataclass
class AdvisorReviewSummary:
    checkpoint: str
    status: str
    reason: str = ""
    suggested_changes: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    model: str = ""
    created_at: str = field(default_factory=_utc_now_iso)


@dataclass
class PlanningArtifact:
    kind: str
    goal: str
    summary: str
    artifact_id: str = field(default_factory=_artifact_id)
    supersedes_artifact_id: str | None = None
    superseded_by_artifact_id: str | None = None
    derived_from_drift: bool = False
    derivation_reason: str = ""
    used_read_only_subagents: bool = False
    scout_categories: list[str] = field(default_factory=list)
    task_ids: list[str] = field(default_factory=list)
    advisor_status: str | None = None
    advisor_reason: str = ""
    advisor_suggested_changes: list[str] = field(default_factory=list)
    advisor_risk_flags: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_utc_now_iso)


@dataclass
class ExplicitContextEntry:
    raw_path: str
    resolved_path: str
    kind: str
    added_at: str = field(default_factory=_utc_now_iso)
    resolved: bool = True


@dataclass
class SessionState:
    session_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str | None = None
    session_execution_mode: str = "main"
    session_command_policy_name: str | None = None
    session_command_policy_source: str | None = None
    session_command_policy_allowed_tool_names: list[str] = field(default_factory=list)
    session_command_policy_allowed_bash_prefixes: list[str] = field(default_factory=list)
    session_command_policy_require_read_only_subagents: bool = False
    original_cwd: str | None = None
    effective_cwd: str | None = None
    workspace_mode: str = "main"
    workspace_label: str | None = None
    workspace_created_at: str | None = None
    workspace_health: str = "healthy"
    workspace_cleanup_status: str = "none"
    workspace_cleanup_error: str | None = None
    workspace_unavailable: bool = False
    workspace_unavailable_reason: str | None = None
    workspace_fallback_cwd: str | None = None
    context_summary: str | None = None
    explicit_context_entries: list[ExplicitContextEntry] = field(default_factory=list)
    advisor_model: str | None = None
    advisor_mode: str = "off"
    advisor_last_result: AdvisorReviewSummary | None = None
    advisor_review_history: list[AdvisorReviewSummary] = field(default_factory=list)
    active_execution_constraint: str = "normal"
    constraint_source: str | None = None
    constraint_reason: str | None = None
    constraint_trigger_count: int = 0
    active_execution_plan_id: str | None = None
    plan_execution_count: int = 0
    plan_drift_count: int = 0
    last_plan_drift_status: str | None = None
    last_plan_drift_reason: str | None = None
    last_plan_drift_context: str | None = None
    enabled_plugin_names: list[str] = field(default_factory=list)
    disabled_plugin_names: list[str] = field(default_factory=list)
    enabled_skill_names: list[str] = field(default_factory=list)
    disabled_skill_names: list[str] = field(default_factory=list)
    session_permission_rules: list[dict[str, str]] = field(default_factory=list)
    activated_deferred_tool_names: list[str] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    recent_change_sets: list[WorkspaceChangeSet] = field(default_factory=list)
    undone_change_sets: list[WorkspaceChangeSet] = field(default_factory=list)
    saved_task_records: list[dict[str, object]] = field(default_factory=list)
    saved_task_surface_counts: dict[str, int] = field(default_factory=dict)
    active_planning_artifact_id: str | None = None
    planning_artifact_history: list[PlanningArtifact] = field(default_factory=list)
    recent_planning_artifacts: list[PlanningArtifact] = field(default_factory=list)
