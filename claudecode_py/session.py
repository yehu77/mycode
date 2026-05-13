from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Thread
from typing import Any
import re

from .commands import CommandExecution, render_repl_command_help
from .config import SessionConfig
from .context_usage import collect_context_usage, render_context_usage
from .history_compaction import (
    HistoryCompactionRequest,
    build_history_compaction_result,
    merge_context_summary,
)
from .indexing import (
    JsTsProjectIndex,
    PythonProjectIndex,
)
from .interactions import UserQuestionRequest, UserQuestionResponse
from .integrations import (
    DiffTargetResult,
    EditorTarget,
    ReferenceLookupResult,
    ReferenceTargetResult,
    SymbolActionBundle,
    SymbolLookupResult,
    build_diff_targets,
    build_open_file_target,
    build_reference_targets,
    build_symbol_action_bundle,
    build_symbol_target,
    parse_reference_line,
)
from .mcp import McpRegistry
from .mcp.client import McpClientError
from .mcp.models import McpDiagnosticResult, McpVerificationResult
from .permission_config import (
    default_permission_config_path,
    load_permission_rules,
    permission_rule_from_dict,
    permission_rule_to_dict,
    save_permission_rules,
)
from .permission_display import (
    PermissionDisplayContext,
    has_permission_display_context,
    render_permission_display_compact,
    render_permission_display_lines,
)
from .permissions import (
    ApprovalRequest,
    PermissionDecision,
    PermissionDeniedError,
    PermissionManager,
    PermissionRule,
    PermissionRuleScope,
)
from .providers import build_provider, format_capabilities
from .providers.errors import ProviderCapabilityError
from .runtime.events import RuntimeEvent
from .runtime.context import SessionRuntimeContext
from .runtime.query_loop import _create_provider_message_with_retries, _request_advisor_review, run_query_loop
from .session_components import (
    AdvisorSessionComponent,
    PlanSessionComponent,
    SymbolSurfaceSessionComponent,
    TaskDetailSessionComponent,
    WorkspaceSessionComponent,
    file_context_item_matches_path as workflow_file_context_item_matches_path,
    find_matching_file_context_index as workflow_find_matching_file_context_index,
    focused_path_from_payload as workflow_focused_path_from_payload,
    preferred_active_plan_execution_file_index as workflow_preferred_active_plan_execution_file_index,
    preferred_active_plan_file_index as workflow_preferred_active_plan_file_index,
    preferred_active_plan_scout_file_index as workflow_preferred_active_plan_scout_file_index,
    preferred_file_context_index as workflow_preferred_file_context_index,
    preferred_selected_change_file_index as workflow_preferred_selected_change_file_index,
    preferred_task_file_index as workflow_preferred_task_file_index,
    resolve_active_plan_execution_file_context as workflow_resolve_active_plan_execution_file_context,
    resolve_active_plan_file_context as workflow_resolve_active_plan_file_context,
    resolve_active_plan_scout_file_context as workflow_resolve_active_plan_scout_file_context,
    resolve_file_context_selection as workflow_resolve_file_context_selection,
    resolve_selected_change_file_context as workflow_resolve_selected_change_file_context,
    resolve_task_file_context as workflow_resolve_task_file_context,
    build_file_context_item_action_groups as workflow_build_file_context_item_action_groups,
    dedupe_action_commands as workflow_dedupe_action_commands,
    render_action_group_lines as workflow_render_action_group_lines,
    render_action_group_summary as workflow_render_action_group_summary,
    render_file_context_action_group_lines as workflow_render_file_context_action_group_lines,
    render_file_context_action_group_summary as workflow_render_file_context_action_group_summary,
    render_focused_file_context_lines as workflow_render_focused_file_context_lines,
    render_navigation_section as workflow_render_navigation_section,
    render_primary_secondary_action_section as workflow_render_primary_secondary_action_section,
    render_resolved_file_context_sections as workflow_render_resolved_file_context_sections,
    render_selected_surface_summary as workflow_render_selected_surface_summary,
    render_summary_field_lines as workflow_render_summary_field_lines,
    render_surface_metadata_section as workflow_render_surface_metadata_section,
    render_workflow_action_sections as workflow_render_workflow_action_sections,
)
from .session_factory import SessionFactory, _UNSET
from .storage.background_sessions import (
    BackgroundSessionRecord,
    list_background_sessions,
    update_background_session,
)
from .storage.session_checklist import ChecklistTask, SessionChecklistStore
from .storage.transcript import (
    TranscriptSummary,
    get_session_path,
    list_transcripts,
    load_latest_transcript,
    load_transcript_by_session_id,
    save_transcript,
    update_transcript_workspace_metadata,
)
from .state import (
    AdvisorReviewSummary,
    ExplicitContextEntry,
    PlanningArtifact,
    SessionState,
    WorkspaceChangeSet,
    WorkspaceFileChange,
)
from .tasks import TaskManager
from .text_utils import compact_multiline_text, summarize_text_diff
from .tools import FindReferencesTool, ToolContext
from .tools.bash import BashTool, ShellCommandAnalysis, ShellCommandSegment
from .tools.base import (
    count_workspace_change_actions,
    describe_workspace_change,
    is_visible_workspace_change,
    render_change_detail,
    render_change_summary,
    resolve_workspace_path,
    workspace_change_metadata_lines,
)
from .tools.mcp import make_mcp_tool_name
from .workflow_semantics import build_continuation_semantics
from .workspace.isolation import (
    OrphanedWorkspaceDiagnostic,
    IsolatedWorkspace,
    cleanup_isolated_workspace,
    cleanup_orphaned_workspace,
    derive_workspace_health,
    diagnose_orphaned_workspaces,
    repair_isolated_workspace,
)


READ_ONLY_SUBAGENT_TOOL_NAMES = (
    "bash",
    "list_dir",
    "read_file",
    "outline_file",
    "outline_project",
    "glob",
    "grep",
    "find_symbol",
    "find_references",
    "find_symbol_graph",
    "find_callers",
    "find_callees",
    "task_list",
    "task_get",
    "task_wait",
    "session_task_list",
    "session_task_get",
)

READ_ONLY_SUBAGENT_BASH_PREFIXES = (
    "pwd",
    "git status",
    "git diff",
    "git log",
    "git show",
    "git branch",
    "git ls-files",
    "git grep",
    "rg ",
    "grep ",
    "ls",
    "dir",
)

ADVISOR_MODES = ("off", "final-review", "interactive-review")
WRITE_TOOL_NAMES = frozenset({"write_file", "edit_file", "apply_patch"})
MAX_ADVISOR_HISTORY = 20
MAX_PLANNING_ARTIFACTS = 5


@dataclass(slots=True, frozen=True)
class _UltraplanScoutDefinition:
    category: str
    description: str
    prompt: str


@dataclass(slots=True, frozen=True)
class TurnCommandPolicy:
    name: str
    source: str
    allowed_tool_names: frozenset[str] | None = None
    allowed_bash_command_prefixes: tuple[str, ...] | None = None
    require_read_only_subagents: bool = False
    enforce_read_only_bash: bool = False


@dataclass(slots=True, frozen=True)
class BashCommandPolicyResult:
    allowed: bool
    policy_name: str = ""
    policy_source: str = ""
    allowed_prefixes: tuple[str, ...] = ()
    violating_segment_index: int | None = None
    violating_segment: str = ""
    violating_features: tuple[str, ...] = ()
    violation_kind: str = ""
    reason: str = ""


READ_ONLY_COMMAND_POLICY_NAMES = frozenset(
    {
        "review",
        "security-review",
        "ultraplan",
        "read-only-subagent",
        "read-only-turn",
    }
)


@dataclass(slots=True)
class _WorkspaceInventoryReference:
    source: str
    label: str
    mode: str
    original_cwd: str
    effective_cwd: str
    workspace_created_at: str | None
    workspace_cleanup_status: str
    workspace_cleanup_error: str | None
    workspace_unavailable: bool
    workspace_unavailable_reason: str | None
    workspace_fallback_cwd: str | None
    workspace_health: str
    session_id: str | None = None
    transcript_path: Path | None = None
    background_record: BackgroundSessionRecord | None = None


def workspace_recommended_actions(
    *,
    workspace_health: str,
    workspace_label: str | None = None,
    session_id: str | None = None,
) -> tuple[str, ...]:
    if workspace_health == "unavailable":
        selector = session_id or workspace_label or "all"
        return (
            "/workspaces list",
            f"/workspaces repair {selector}",
            "/workspaces cleanup",
        )
    if workspace_health == "orphaned":
        return (
            "/workspaces list",
            "/workspaces cleanup",
            f"/workspaces cleanup apply {workspace_label or 'all'}",
        )
    if workspace_health in {"cleanup_failed", "cleanup_pending"}:
        selector = session_id or workspace_label or "all"
        return (
            "/workspaces list",
            f"/workspaces repair {selector}",
        )
    return ()


def workspace_action_bundle(
    *,
    workspace_health: str,
    workspace_label: str | None = None,
    session_id: str | None = None,
    workspace_action: str | None = None,
    workspace_target: str | None = None,
) -> dict[str, str]:
    selector = (workspace_target or session_id or workspace_label or "all").strip() or "all"
    if workspace_action == "repair":
        return {
            "primary_action": f"workspace_repair {selector}",
            "secondary_action": "workspace_cleanup_preview",
            "tertiary_action": "/workspaces list",
            "target": selector,
            "workspace_health": workspace_health,
        }
    if workspace_action == "cleanup":
        return {
            "primary_action": "workspace_cleanup_preview",
            "secondary_action": f"workspace_cleanup_apply {selector}",
            "tertiary_action": "/workspaces list",
            "target": selector,
            "workspace_health": workspace_health,
        }
    if workspace_health == "unavailable":
        return {
            "primary_action": f"workspace_repair {selector}",
            "secondary_action": "workspace_cleanup_preview",
            "tertiary_action": "/workspaces list",
            "target": selector,
            "workspace_health": workspace_health,
        }
    if workspace_health == "orphaned":
        cleanup_target = (workspace_label or workspace_target or "all").strip() or "all"
        return {
            "primary_action": "workspace_cleanup_preview",
            "secondary_action": f"workspace_cleanup_apply {cleanup_target}",
            "tertiary_action": "/workspaces list",
            "target": cleanup_target,
            "workspace_health": workspace_health,
        }
    return {
        "primary_action": "none",
        "secondary_action": "none",
        "tertiary_action": "/workspaces list",
        "target": selector,
        "workspace_health": workspace_health,
    }


def checklist_recommended_actions(
    *,
    task_id: str,
    checklist_status: str,
) -> tuple[str, ...]:
    normalized_task_id = task_id.strip()
    if checklist_status == "pending":
        return (
            f"session_task_get {normalized_task_id}",
            f"session_task_update {normalized_task_id} status=in_progress",
            "session_task_list",
        )
    if checklist_status == "in_progress":
        return (
            f"session_task_get {normalized_task_id}",
            f"session_task_update {normalized_task_id} status=completed",
            "session_task_list",
        )
    return (
        f"session_task_get {normalized_task_id}",
        f"session_task_update {normalized_task_id} status=in_progress",
        "session_task_list",
    )


def checklist_action_bundle(
    *,
    task_id: str,
    checklist_status: str,
) -> dict[str, str]:
    normalized_task_id = task_id.strip()
    if checklist_status == "pending":
        return {
            "primary_action": f"checklist_mark_in_progress {normalized_task_id}",
            "secondary_action": f"checklist_mark_completed {normalized_task_id}",
            "tertiary_action": "session_task_list",
            "edit_subject_action": f"checklist_set_subject {normalized_task_id}",
            "edit_description_action": f"checklist_set_description {normalized_task_id}",
            "edit_owner_action": f"checklist_set_owner {normalized_task_id}",
            "edit_active_form_action": f"checklist_set_active_form {normalized_task_id}",
            "edit_blocks_action": f"checklist_set_blocks {normalized_task_id}",
            "edit_blocked_by_action": f"checklist_set_blocked_by {normalized_task_id}",
            "edit_metadata_action": f"checklist_set_metadata {normalized_task_id}",
            "target": normalized_task_id,
            "checklist_status": checklist_status,
        }
    if checklist_status == "in_progress":
        return {
            "primary_action": f"checklist_mark_completed {normalized_task_id}",
            "secondary_action": f"checklist_reopen {normalized_task_id}",
            "tertiary_action": "session_task_list",
            "edit_subject_action": f"checklist_set_subject {normalized_task_id}",
            "edit_description_action": f"checklist_set_description {normalized_task_id}",
            "edit_owner_action": f"checklist_set_owner {normalized_task_id}",
            "edit_active_form_action": f"checklist_set_active_form {normalized_task_id}",
            "edit_blocks_action": f"checklist_set_blocks {normalized_task_id}",
            "edit_blocked_by_action": f"checklist_set_blocked_by {normalized_task_id}",
            "edit_metadata_action": f"checklist_set_metadata {normalized_task_id}",
            "target": normalized_task_id,
            "checklist_status": checklist_status,
        }
    return {
        "primary_action": f"checklist_reopen {normalized_task_id}",
        "secondary_action": f"checklist_mark_in_progress {normalized_task_id}",
        "tertiary_action": "session_task_list",
        "edit_subject_action": f"checklist_set_subject {normalized_task_id}",
        "edit_description_action": f"checklist_set_description {normalized_task_id}",
        "edit_owner_action": f"checklist_set_owner {normalized_task_id}",
        "edit_active_form_action": f"checklist_set_active_form {normalized_task_id}",
        "edit_blocks_action": f"checklist_set_blocks {normalized_task_id}",
        "edit_blocked_by_action": f"checklist_set_blocked_by {normalized_task_id}",
        "edit_metadata_action": f"checklist_set_metadata {normalized_task_id}",
        "target": normalized_task_id,
        "checklist_status": checklist_status,
    }


class Session:
    def __init__(
        self,
        config: SessionConfig,
        *,
        state: SessionState | None = None,
        task_manager: TaskManager | None = None,
        mcp_registry: McpRegistry | None | object = _UNSET,
        depth: int = 0,
        persist_transcript: bool | None = None,
        runtime_context: SessionRuntimeContext | None = None,
        session_factory: SessionFactory | None = None,
        permission_manager: PermissionManager | None = None,
    ) -> None:
        self._session_factory = session_factory or SessionFactory(load_mcp_from_config=False)
        self._runtime_context = runtime_context or self._session_factory.create_runtime_context(
            config,
            task_manager=task_manager,
            mcp_registry=mcp_registry,
        )
        self.state = state or SessionState()
        self._workspace_cleanup = None
        self._latest_checklist_duplicate_guard: dict[str, Any] | None = None
        self._current_symbol_surface: dict[str, Any] | None = None
        self._current_change_focus_payload: dict[str, Any] | None = None
        self._current_task_focus_payload: dict[str, Any] | None = None
        self._current_plan_focus_payload: dict[str, Any] | None = None
        self._last_project_context_reload: dict[str, Any] | None = None
        self._normalize_advisor_state()
        self._normalize_execution_contract_state()
        self._normalize_workspace_state()
        self.persist_transcript = depth == 0 if persist_transcript is None else persist_transcript
        self.permission_manager = permission_manager or PermissionManager(
            mode=config.permission_mode,
            interactive=config.interactive,
        )
        self._workspace_permission_rules: list[PermissionRule] = []
        self._restore_permission_rules()
        self.depth = depth
        self._active_tool_names: frozenset[str] | None = None
        self._active_bash_command_prefixes: tuple[str, ...] | None = None
        self._require_read_only_subagents = False
        self._active_command_policy: TurnCommandPolicy | None = None
        self._turn_read_only_constraints_active = False
        self._live_event_sink = None
        self._question_handler = None
        self._session_checklist = SessionChecklistStore(
            self.config.transcript_cwd or self.config.cwd,
            session_id=self.state.session_id,
        )
        self._reconcile_plugin_state(initial_load=True)
        self._reconcile_skill_state(initial_load=True)
        self._refresh_command_registry()

    @property
    def config(self) -> SessionConfig:
        return self._runtime_context.config

    @property
    def task_manager(self) -> TaskManager:
        return self._runtime_context.task_manager

    @property
    def command_registry(self):
        return self._runtime_context.command_registry

    @property
    def plugin_registry(self):
        return self._runtime_context.plugin_registry

    @property
    def mcp_registry(self) -> McpRegistry | None:
        return self._runtime_context.mcp_registry

    @property
    def provider(self):
        return self._runtime_context.provider

    @provider.setter
    def provider(self, value) -> None:
        self._runtime_context.provider = value

    @property
    def project_context(self):
        return self._runtime_context.project_context

    @property
    def base_system_prompt(self) -> str:
        return self._runtime_context.base_system_prompt

    @property
    def tools(self):
        return self._runtime_context.tools

    @property
    def orchestrator(self):
        return self._runtime_context.orchestrator

    @property
    def default_tools(self):
        return self._runtime_context.default_tools

    @property
    def deferred_tools(self):
        return self._runtime_context.deferred_tools

    def tool_specs(self) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []
        for tool in self._available_tools():
            spec = tool.to_model_tool()
            policy = self._active_command_policy
            if tool.name == "bash" and policy and policy.allowed_bash_command_prefixes:
                allowed = ", ".join(policy.allowed_bash_command_prefixes)
                mode = policy.name
                spec = {
                    **spec,
                    "description": (
                        f'{spec["description"]} Current command mode: {mode}. '
                        f"Allowed commands in this turn must start with: {allowed}."
                    ),
                }
            specs.append(spec)
        return specs

    def execute_tool_calls(self, tool_calls, ctx: ToolContext, *, sink=None) -> list[dict[str, Any]]:
        return self._build_active_orchestrator().execute_tool_calls(tool_calls, ctx, sink=sink)

    def tool_context(self) -> ToolContext:
        return ToolContext(
            cwd=self.runtime_cwd(),
            permission_manager=self.permission_manager,
            task_manager=self.task_manager,
            session=self,
        )

    def ask(
        self,
        prompt: str,
        sink=None,
        *,
        allowed_tool_names: tuple[str, ...] | None = None,
        allowed_bash_command_prefixes: tuple[str, ...] | None = None,
        require_read_only_subagents: bool = False,
        command_policy_name: str | None = None,
        command_policy_source: str | None = None,
    ) -> str:
        with self._command_execution_scope(
            allowed_tool_names=allowed_tool_names,
            allowed_bash_command_prefixes=allowed_bash_command_prefixes,
            require_read_only_subagents=require_read_only_subagents,
            command_policy_name=command_policy_name,
            command_policy_source=command_policy_source,
        ):
            result = run_query_loop(self, prompt, sink=sink)
        self.persist_state()
        return result

    def run_command(self, execution: CommandExecution, sink=None) -> str:
        metadata = execution.metadata or {}
        if metadata.get("command_kind") == "ultraplan":
            goal = str(metadata.get("goal") or execution.prompt).strip()
            scout_categories = tuple(str(item) for item in metadata.get("scout_categories", []))
            supersede_artifact_id = metadata.get("supersede_artifact_id")
            derived_from_drift = bool(metadata.get("derived_from_drift", False))
            derivation_reason = metadata.get("derivation_reason")
            return self.run_ultraplan(
                goal=goal,
                scout_categories=scout_categories,
                supersede_artifact_id=(
                    str(supersede_artifact_id)
                    if supersede_artifact_id is not None
                    else None
                ),
                derived_from_drift=derived_from_drift,
                derivation_reason=(
                    str(derivation_reason)
                    if derivation_reason is not None
                    else None
                ),
                sink=sink,
            )
        return self.ask(
            execution.prompt,
            sink=sink,
            allowed_tool_names=execution.allowed_tool_names,
            allowed_bash_command_prefixes=execution.allowed_bash_command_prefixes,
            require_read_only_subagents=execution.require_read_only_subagents,
            command_policy_name=(
                str(metadata.get("command_policy_name"))
                if metadata.get("command_policy_name") is not None
                else None
            ),
            command_policy_source=(
                str(metadata.get("command_policy_source"))
                if metadata.get("command_policy_source") is not None
                else None
            ),
        )

    def handle_repl_command(self, prompt: str) -> tuple[bool, str | CommandExecution | None]:
        if prompt == "/help":
            return True, render_repl_command_help(self.command_registry)
        if prompt == "/context-refresh":
            return True, self.reload_project_context()
        return self.command_registry.handle(self, prompt)

    def set_live_event_sink(self, sink) -> None:
        self._live_event_sink = sink

    def _emit_runtime_event(self, event: RuntimeEvent) -> None:
        sink = self._live_event_sink
        if sink is None:
            return
        sink(event)

    def _workspace_task_progress_message(
        self,
        *,
        summary: str,
        target: str,
        health_before: str | None = None,
        health_after: str | None = None,
        planned_paths: list[str] | None = None,
        applied_paths: list[str] | None = None,
        failure_reason: str | None = None,
    ) -> str:
        parts = [summary, f"target={target or 'all'}"]
        if health_before:
            parts.append(f"health_before={health_before}")
        if health_after:
            parts.append(f"health_after={health_after}")
        if planned_paths is not None:
            parts.append(f"planned={len(planned_paths)}")
        if applied_paths is not None:
            parts.append(f"applied={len(applied_paths)}")
        if failure_reason:
            parts.append(f"failure={failure_reason}")
        return " | ".join(parts)

    def _workspace_task_recommended_actions(
        self,
        *,
        workspace_target: str,
        workspace_health_before: str | None = None,
        workspace_health_after: str | None = None,
        workspace_health: str | None = None,
    ) -> list[str]:
        recommendation_health = (
            workspace_health_after
            if workspace_health_after and workspace_health_after != "healthy"
            else workspace_health_before or workspace_health or "healthy"
        )
        recommendation_target = (workspace_target or "all").strip() or "all"
        return list(
            self._workspace_recommended_actions(
                workspace_health=recommendation_health,
                workspace_label=recommendation_target,
            )
        )

    def _workspace_task_runtime_metadata(
        self,
        *,
        workspace_action: str,
        workspace_target: str,
        workspace_health_before: str | None = None,
        workspace_health_after: str | None = None,
        workspace_planned_paths: list[str] | None = None,
        workspace_applied_paths: list[str] | None = None,
        workspace_failure_reason: str | None = None,
    ) -> dict[str, Any]:
        normalized_target = (workspace_target or "all").strip() or "all"
        normalized_before = (workspace_health_before or "").strip() or None
        normalized_after = (workspace_health_after or "").strip() or None
        recommended_actions = self._workspace_task_recommended_actions(
            workspace_target=normalized_target,
            workspace_health_before=normalized_before,
            workspace_health_after=normalized_after,
        )
        return {
            "workspace_action": workspace_action,
            "workspace_target": normalized_target,
            "workspace_health_before": normalized_before,
            "workspace_health_after": normalized_after,
            "workspace_planned_paths": list(workspace_planned_paths or []),
            "workspace_applied_paths": list(workspace_applied_paths or []),
            "workspace_failure_reason": workspace_failure_reason,
            "workspace_recommended_actions": recommended_actions,
        }

    def _set_workspace_task_progress(
        self,
        task_id: str,
        summary: str,
        *,
        workspace_action: str,
        workspace_target: str,
        workspace_health_before: str | None = None,
        workspace_health_after: str | None = None,
        workspace_planned_paths: list[str] | None = None,
        workspace_applied_paths: list[str] | None = None,
        workspace_failure_reason: str | None = None,
    ) -> None:
        metadata = self._workspace_task_runtime_metadata(
            workspace_action=workspace_action,
            workspace_target=workspace_target,
            workspace_health_before=workspace_health_before,
            workspace_health_after=workspace_health_after,
            workspace_planned_paths=workspace_planned_paths,
            workspace_applied_paths=workspace_applied_paths,
            workspace_failure_reason=workspace_failure_reason,
        )
        self.task_manager.set_progress(task_id, summary, **metadata)
        self._emit_runtime_event(
            RuntimeEvent(
                kind="task_progress",
                message=self._workspace_task_progress_message(
                    summary=summary,
                    target=workspace_target,
                    health_before=workspace_health_before,
                    health_after=workspace_health_after,
                    planned_paths=workspace_planned_paths,
                    applied_paths=workspace_applied_paths,
                    failure_reason=workspace_failure_reason,
                ),
                task_id=task_id,
                tool_name=f"workspace_{workspace_action}",
            )
        )

    def set_question_handler(self, handler) -> None:
        self._question_handler = handler

    def ask_user_questions(self, request: UserQuestionRequest) -> UserQuestionResponse:
        if self._question_handler is not None:
            return self._question_handler(request)
        return self._prompt_user_questions(request)

    def persist_state(self) -> None:
        self._session_checklist.save()
        self.state.saved_task_records = self.task_manager.snapshot()
        self.state.saved_task_surface_counts = self._task_surface_counts_payload()
        if self.persist_transcript:
            save_transcript(self.config, self.state)

    def set_workspace_cleanup(self, callback) -> None:
        self._workspace_cleanup = callback
        if self.state.workspace_mode != "main":
            self.state.workspace_cleanup_status = "pending"
            self.state.workspace_cleanup_error = None
            self.state.workspace_health = self._derive_workspace_health(
                workspace_mode=self.state.workspace_mode,
                workspace_cleanup_status=self.state.workspace_cleanup_status,
                workspace_unavailable=bool(self.state.workspace_unavailable),
            )

    def _workspace_metadata_from_state(self, state: SessionState) -> dict[str, Any]:
        recommended_actions = self._workspace_recommended_actions(
            workspace_health=state.workspace_health,
            workspace_label=state.workspace_label,
            session_id=state.session_id,
        )
        metadata: dict[str, Any] = {
            "workspace_mode": state.workspace_mode,
            "workspace_health": state.workspace_health,
            "workspace_cleanup_status": state.workspace_cleanup_status,
            "workspace_unavailable": state.workspace_unavailable,
        }
        if state.workspace_label:
            metadata["workspace_label"] = state.workspace_label
        if state.workspace_created_at:
            metadata["workspace_created_at"] = state.workspace_created_at
        if state.original_cwd:
            metadata["original_cwd"] = state.original_cwd
        if state.effective_cwd:
            metadata["effective_cwd"] = state.effective_cwd
            metadata["child_cwd"] = state.effective_cwd
        if state.workspace_cleanup_error:
            metadata["workspace_cleanup_error"] = state.workspace_cleanup_error
        if state.workspace_unavailable_reason:
            metadata["workspace_unavailable_reason"] = state.workspace_unavailable_reason
        if state.workspace_fallback_cwd:
            metadata["workspace_fallback_cwd"] = state.workspace_fallback_cwd
        if recommended_actions:
            metadata["workspace_recommended_actions"] = list(recommended_actions)
        return metadata

    def _task_workspace_metadata(self, session: Any | None) -> dict[str, Any]:
        if session is None:
            return {}
        metadata = self._workspace_metadata_from_state(session.state)
        execution_contract = session.execution_contract_payload()
        metadata["child_execution_mode"] = str(execution_contract.get("session_execution_mode") or "main")
        if execution_contract.get("session_command_policy_name"):
            metadata["child_command_policy_name"] = str(execution_contract["session_command_policy_name"])
        if execution_contract.get("session_command_policy_source"):
            metadata["child_command_policy_source"] = str(execution_contract["session_command_policy_source"])
        allowed_tools = execution_contract.get("session_command_policy_allowed_tool_names") or []
        if allowed_tools:
            metadata["child_command_policy_allowed_tools"] = list(allowed_tools)
            metadata["child_command_policy_allowed_tool_names"] = list(allowed_tools)
        allowed_prefixes = execution_contract.get("session_command_policy_allowed_bash_prefixes") or []
        if allowed_prefixes:
            metadata["child_command_policy_allowed_bash_prefixes"] = list(allowed_prefixes)
        if execution_contract.get("session_command_policy_require_read_only_subagents"):
            metadata["child_command_policy_require_read_only_subagents"] = True
        return metadata

    def _task_surface_kind(self, task: Any) -> str:
        return self._task_detail_component._task_surface_kind(task)

    def _task_surface_header(self, surface_kind: str) -> str:
        return self._task_detail_component._task_surface_header(surface_kind)

    def _task_surface_counts_payload(self) -> dict[str, int]:
        return self._task_detail_component._task_surface_counts_payload()

    def task_surface_counts_payload(self) -> dict[str, int]:
        return dict(self._task_surface_counts_payload())

    def planning_surface_payload(self) -> dict[str, Any]:
        artifacts = self.state.planning_artifact_history or self.state.recent_planning_artifacts
        return {
            "active_planning_artifact_id": self.state.active_planning_artifact_id,
            "planning_artifact_count": len(artifacts),
            "has_active_plan": self.active_planning_artifact() is not None,
        }

    def task_surface_summary_lines(self) -> list[str]:
        counts = self._task_surface_counts_payload()
        lines = ["task_surfaces:"]
        ordered_kinds = (
            "checklist",
            "workspace_maintenance",
            "child_execution",
            "background_execution",
            "active_plan_execution",
            "other_task",
        )
        for kind in ordered_kinds:
            lines.append(f"{kind}: {counts.get(kind, 0)}")
        return lines

    def _task_execution_contract_metadata(self, task: Any) -> dict[str, Any] | None:
        return self._task_detail_component._task_execution_contract_metadata(task)

    def _workspace_summary_bits_from_metadata(self, metadata: dict[str, Any]) -> list[str]:
        return self._task_detail_component._workspace_summary_bits_from_metadata(metadata)

    def _render_task_workspace_context(self, metadata: dict[str, Any]) -> list[str]:
        return self._task_detail_component._render_task_workspace_context(metadata)

    def _derive_workspace_health(
        self,
        *,
        workspace_mode: str,
        workspace_cleanup_status: str,
        workspace_unavailable: bool,
        orphaned: bool = False,
    ) -> str:
        return derive_workspace_health(
            workspace_mode=workspace_mode,
            workspace_cleanup_status=workspace_cleanup_status,
            workspace_unavailable=workspace_unavailable,
            orphaned=orphaned,
        )

    def _workspace_recommended_actions(
        self,
        *,
        workspace_health: str,
        workspace_label: str | None = None,
        session_id: str | None = None,
    ) -> tuple[str, ...]:
        return workspace_recommended_actions(
            workspace_health=workspace_health,
            workspace_label=workspace_label,
            session_id=session_id,
        )

    def _workspace_session_action_fields(
        self,
        *,
        workspace_health: str,
        workspace_label: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, str]:
        bundle = workspace_action_bundle(
            workspace_health=workspace_health,
            workspace_label=workspace_label,
            session_id=session_id,
        )
        return {
            "selected_workspace_primary_action": bundle["primary_action"],
            "selected_workspace_secondary_action": bundle["secondary_action"],
            "selected_workspace_tertiary_action": bundle["tertiary_action"],
            "selected_workspace_target": bundle["target"],
        }

    def _workspace_task_action_fields(self, metadata: dict[str, Any]) -> dict[str, str]:
        return self._task_detail_component._workspace_task_action_fields(metadata)

    def _workspace_task_action_bundle(self, metadata: dict[str, Any]) -> dict[str, str] | None:
        return self._task_detail_component._workspace_task_action_bundle(metadata)

    def _workspace_task_detail_metadata(self, metadata: dict[str, Any]) -> dict[str, Any] | None:
        return self._task_detail_component._workspace_task_detail_metadata(metadata)

    def current_workspace_action_bundle(self) -> dict[str, str]:
        return workspace_action_bundle(
            workspace_health=self.state.workspace_health,
            workspace_label=self.state.workspace_label,
            session_id=self.state.session_id,
        )

    def task_workspace_action_bundle(self, identifier: str) -> dict[str, str] | None:
        task = self.resolve_task(identifier)
        if task is None:
            return None
        metadata = task.metadata or {}
        return self._workspace_task_action_bundle(metadata)

    def task_workspace_detail_metadata(self, identifier: str) -> dict[str, Any] | None:
        task = self.resolve_task(identifier)
        if task is None:
            return None
        metadata = task.metadata or {}
        return self._workspace_task_detail_metadata(metadata)

    def task_execution_detail_metadata(self, identifier: str) -> dict[str, Any] | None:
        task = self.resolve_task(identifier)
        if task is None:
            return None
        return self._task_execution_contract_metadata(task)

    def checklist_task_list_id(self) -> str:
        return self.state.session_id

    def checklist_tasks(self) -> list[ChecklistTask]:
        return self._session_checklist.list_tasks(task_list_id=self.checklist_task_list_id())

    def checklist_task(self, task_id: str) -> ChecklistTask | None:
        return self._session_checklist.get_task(
            task_id.strip(),
            task_list_id=self.checklist_task_list_id(),
        )

    def resolve_checklist_task(self, identifier: str) -> ChecklistTask | None:
        raw = identifier.strip()
        if not raw:
            return None
        matches = [
            task
            for task in self.checklist_tasks()
            if task.id == raw or task.id.startswith(raw)
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def checklist_stats(self) -> dict[str, int]:
        return self._session_checklist.stats(task_list_id=self.checklist_task_list_id())

    def checklist_tasks_payload(self) -> list[dict[str, Any]]:
        return [self._checklist_task_payload(task) for task in self.checklist_tasks()]

    def create_checklist_task(
        self,
        *,
        subject: str,
        description: str,
        active_form: str,
        status: str = "pending",
        owner: str | None = None,
        blocks: list[str] | None = None,
        blocked_by: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        task_list_id: str | None = None,
    ) -> dict[str, Any]:
        duplicate_matches = self._find_checklist_duplicate_matches(
            subject=subject,
            description=description,
            active_form=active_form,
            task_list_id=task_list_id,
        )
        if duplicate_matches:
            result = self._checklist_duplicate_create_result(
                duplicate_matches,
                subject=subject,
                description=description,
                active_form=active_form,
            )
            self._latest_checklist_duplicate_guard = dict(result.get("duplicate_guard") or {})
            return result
        task = self._session_checklist.create_task(
            subject=subject,
            description=description,
            active_form=active_form,
            status=status,
            owner=owner,
            blocks=blocks,
            blocked_by=blocked_by,
            metadata=metadata,
            task_list_id=task_list_id or self.checklist_task_list_id(),
        )
        self._session_checklist.save()
        self._latest_checklist_duplicate_guard = None
        created_payload = self._checklist_task_payload(task)
        return {
            **created_payload,
            "task": dict(created_payload),
            "created": True,
        }

    def list_checklist_tasks(self, *, task_list_id: str | None = None) -> list[dict[str, Any]]:
        return [
            self._checklist_task_payload(task)
            for task in self._session_checklist.list_tasks(
                task_list_id=task_list_id or self.checklist_task_list_id()
            )
        ]

    def get_checklist_task(self, task_id: str, *, task_list_id: str | None = None) -> dict[str, Any] | None:
        task = self._session_checklist.get_task(
            task_id.strip(),
            task_list_id=task_list_id or self.checklist_task_list_id(),
        )
        if task is None:
            return None
        return self._checklist_task_payload(task)

    def checklist_task_action_bundle(self, identifier: str) -> dict[str, str] | None:
        task = self.resolve_checklist_task(identifier)
        if task is None:
            return None
        return checklist_action_bundle(
            task_id=task.id,
            checklist_status=task.status,
        )

    def checklist_mark_in_progress(self, identifier: str) -> str:
        return self._set_checklist_task_status(identifier, "in_progress")

    def checklist_mark_completed(self, identifier: str) -> str:
        return self._set_checklist_task_status(identifier, "completed")

    def checklist_reopen(self, identifier: str) -> str:
        return self._set_checklist_task_status(identifier, "pending")

    def checklist_set_owner(self, identifier: str, owner: str) -> str:
        task = self.resolve_checklist_task(identifier)
        if task is None:
            return f'Unknown checklist task "{identifier.strip()}".'
        normalized_owner = owner.strip()
        current_owner = task.owner or ""
        if normalized_owner == current_owner:
            if normalized_owner:
                return f'Checklist task "{task.id}" already has owner "{normalized_owner}".'
            return f'Checklist task "{task.id}" already has no owner.'
        result = self.update_checklist_task(task.id, owner=normalized_owner)
        updated = result.get("task") if isinstance(result, dict) else None
        next_owner = ""
        if isinstance(updated, dict):
            next_owner = str(updated.get("owner") or "").strip()
        if next_owner:
            return f'Updated checklist task "{task.id}" owner to "{next_owner}".'
        return f'Cleared checklist task "{task.id}" owner.'

    def checklist_set_subject(self, identifier: str, subject: str) -> str:
        task = self.resolve_checklist_task(identifier)
        if task is None:
            return f'Unknown checklist task "{identifier.strip()}".'
        normalized_subject = subject.strip()
        if not normalized_subject:
            return "Checklist subject cannot be empty."
        if normalized_subject == task.subject:
            return f'Checklist task "{task.id}" already uses subject "{normalized_subject}".'
        result = self.update_checklist_task(task.id, subject=normalized_subject)
        updated = result.get("task") if isinstance(result, dict) else None
        next_subject = normalized_subject
        if isinstance(updated, dict):
            next_subject = str(updated.get("subject") or normalized_subject)
        return f'Updated checklist task "{task.id}" subject to "{next_subject}".'

    def checklist_set_description(self, identifier: str, description: str) -> str:
        task = self.resolve_checklist_task(identifier)
        if task is None:
            return f'Unknown checklist task "{identifier.strip()}".'
        normalized_description = description.strip()
        if normalized_description == task.description:
            if normalized_description:
                return f'Checklist task "{task.id}" already uses description "{normalized_description}".'
            return f'Checklist task "{task.id}" already has no description.'
        result = self.update_checklist_task(task.id, description=normalized_description)
        updated = result.get("task") if isinstance(result, dict) else None
        next_description = normalized_description
        if isinstance(updated, dict):
            next_description = str(updated.get("description") or "").strip()
        if next_description:
            return f'Updated checklist task "{task.id}" description to "{next_description}".'
        return f'Cleared checklist task "{task.id}" description.'

    def checklist_set_metadata(self, identifier: str, metadata_text: str) -> str:
        task = self.resolve_checklist_task(identifier)
        if task is None:
            return f'Unknown checklist task "{identifier.strip()}".'
        try:
            parsed_metadata = self._parse_checklist_metadata_input(metadata_text)
        except ValueError as exc:
            return str(exc)
        current_metadata = dict(task.metadata)
        if parsed_metadata == current_metadata:
            if parsed_metadata:
                return f'Checklist task "{task.id}" metadata already matches the requested values.'
            return f'Checklist task "{task.id}" already has no metadata.'
        metadata_patch: dict[str, Any] = {
            key: None
            for key in current_metadata
            if key not in parsed_metadata
        }
        for key, value in parsed_metadata.items():
            if current_metadata.get(key) != value:
                metadata_patch[key] = value
        result = self.update_checklist_task(task.id, metadata=metadata_patch)
        updated = result.get("task") if isinstance(result, dict) else None
        next_metadata = parsed_metadata
        if isinstance(updated, dict):
            payload_metadata = updated.get("metadata")
            if isinstance(payload_metadata, dict):
                next_metadata = {str(key): payload_metadata[key] for key in payload_metadata}
        if next_metadata:
            return f'Updated checklist task "{task.id}" metadata ({len(next_metadata)} entries).'
        return f'Cleared checklist task "{task.id}" metadata.'

    def checklist_set_active_form(self, identifier: str, active_form: str) -> str:
        task = self.resolve_checklist_task(identifier)
        if task is None:
            return f'Unknown checklist task "{identifier.strip()}".'
        normalized_active_form = active_form.strip()
        if not normalized_active_form:
            return "Checklist active_form cannot be empty."
        if normalized_active_form == task.active_form:
            return f'Checklist task "{task.id}" already uses active_form "{normalized_active_form}".'
        result = self.update_checklist_task(task.id, active_form=normalized_active_form)
        updated = result.get("task") if isinstance(result, dict) else None
        next_active_form = normalized_active_form
        if isinstance(updated, dict):
            next_active_form = str(updated.get("active_form") or normalized_active_form)
        return f'Updated checklist task "{task.id}" active_form to "{next_active_form}".'

    def checklist_set_blocks(self, identifier: str, blocks_text: str) -> str:
        task = self.resolve_checklist_task(identifier)
        if task is None:
            return f'Unknown checklist task "{identifier.strip()}".'
        desired_blocks = self._parse_checklist_relation_input(blocks_text)
        current_blocks = list(task.blocks)
        if desired_blocks == current_blocks:
            if desired_blocks:
                return f'Checklist task "{task.id}" already blocks: {", ".join(desired_blocks)}.'
            return f'Checklist task "{task.id}" already has no blocks.'
        result = self.update_checklist_task(
            task.id,
            add_blocks=[item for item in desired_blocks if item not in current_blocks],
            remove_blocks=[item for item in current_blocks if item not in desired_blocks],
        )
        updated = result.get("task") if isinstance(result, dict) else None
        next_blocks = desired_blocks
        if isinstance(updated, dict):
            payload_blocks = updated.get("blocks")
            if isinstance(payload_blocks, list):
                next_blocks = [str(item).strip() for item in payload_blocks if str(item).strip()]
        if next_blocks:
            return f'Updated checklist task "{task.id}" blocks to: {", ".join(next_blocks)}.'
        return f'Cleared checklist task "{task.id}" blocks.'

    def checklist_set_blocked_by(self, identifier: str, blocked_by_text: str) -> str:
        task = self.resolve_checklist_task(identifier)
        if task is None:
            return f'Unknown checklist task "{identifier.strip()}".'
        desired_blocked_by = self._parse_checklist_relation_input(blocked_by_text)
        current_blocked_by = list(task.blocked_by)
        if desired_blocked_by == current_blocked_by:
            if desired_blocked_by:
                return f'Checklist task "{task.id}" already blocked_by: {", ".join(desired_blocked_by)}.'
            return f'Checklist task "{task.id}" already has no blocked_by entries.'
        result = self.update_checklist_task(
            task.id,
            add_blocked_by=[item for item in desired_blocked_by if item not in current_blocked_by],
            remove_blocked_by=[item for item in current_blocked_by if item not in desired_blocked_by],
        )
        updated = result.get("task") if isinstance(result, dict) else None
        next_blocked_by = desired_blocked_by
        if isinstance(updated, dict):
            payload_blocked_by = updated.get("blocked_by")
            if isinstance(payload_blocked_by, list):
                next_blocked_by = [str(item).strip() for item in payload_blocked_by if str(item).strip()]
        if next_blocked_by:
            return f'Updated checklist task "{task.id}" blocked_by to: {", ".join(next_blocked_by)}.'
        return f'Cleared checklist task "{task.id}" blocked_by.'

    def _parse_checklist_relation_input(self, raw_value: str) -> list[str]:
        if not raw_value.strip():
            return []
        seen: set[str] = set()
        items: list[str] = []
        for token in re.split(r"[\r\n,]+", raw_value):
            normalized = token.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            items.append(normalized)
        return items

    def _parse_checklist_metadata_input(self, raw_value: str) -> dict[str, str]:
        if not raw_value.strip():
            return {}
        parsed: dict[str, str] = {}
        for raw_line in raw_value.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if "=" not in line:
                raise ValueError("Checklist metadata lines must use key=value format.")
            key, value = line.split("=", maxsplit=1)
            normalized_key = key.strip()
            if not normalized_key:
                raise ValueError("Checklist metadata keys cannot be empty.")
            parsed[normalized_key] = value.strip()
        return parsed

    def update_checklist_task(
        self,
        task_id: str,
        *,
        subject: str | None = None,
        description: str | None = None,
        active_form: str | None = None,
        status: str | None = None,
        owner: str | None = None,
        add_blocks: list[str] | None = None,
        add_blocked_by: list[str] | None = None,
        remove_blocks: list[str] | None = None,
        remove_blocked_by: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        task_list_id: str | None = None,
    ) -> dict[str, Any]:
        updated_task, updated_fields, status_change = self._session_checklist.update_task(
            task_id.strip(),
            subject=subject,
            description=description,
            active_form=active_form,
            status=status,
            owner=owner,
            add_blocks=add_blocks,
            add_blocked_by=add_blocked_by,
            remove_blocks=remove_blocks,
            remove_blocked_by=remove_blocked_by,
            metadata=metadata,
            task_list_id=task_list_id or self.checklist_task_list_id(),
        )
        self._session_checklist.save()
        if updated_task is not None or status_change is not None:
            self._latest_checklist_duplicate_guard = None
        payload: dict[str, Any] = {
            "task_id": task_id.strip(),
            "updated_fields": list(updated_fields),
        }
        if status_change is not None:
            payload["status_change"] = dict(status_change)
            payload["statusChange"] = dict(status_change)
        if updated_task is not None:
            payload["task"] = self._checklist_task_payload(updated_task)
        else:
            payload["task"] = None
        payload["updatedFields"] = list(updated_fields)
        return payload

    def _set_checklist_task_status(self, identifier: str, status: str) -> str:
        task = self.resolve_checklist_task(identifier)
        if task is None:
            return f'Unknown checklist task "{identifier.strip()}".'
        if task.status == status:
            return f'Checklist task "{task.id}" is already {status}.'
        result = self.update_checklist_task(task.id, status=status)
        updated = result.get("task") if isinstance(result, dict) else None
        if not isinstance(updated, dict):
            return f'Updated checklist task "{task.id}" to {status}.'
        subject = str(updated.get("subject") or task.subject)
        return f'Updated checklist task "{task.id}" to {status}: {subject}'

    def todo_write(
        self,
        todos: list[dict[str, Any]],
        *,
        task_list_id: str | None = None,
    ) -> dict[str, Any]:
        old_tasks, new_tasks = self._session_checklist.replace_with_todos(
            todos,
            task_list_id=task_list_id or self.checklist_task_list_id(),
        )
        self._session_checklist.save()
        self._latest_checklist_duplicate_guard = None
        old_todos = [self._todo_payload_from_checklist_task(task) for task in old_tasks]
        new_todos = [self._todo_payload_from_checklist_task(task) for task in new_tasks]
        payload = {
            "old_todos": old_todos,
            "new_todos": new_todos,
            "oldTodos": old_todos,
            "newTodos": new_todos,
            "checklist_tasks": [self._checklist_task_payload(task) for task in new_tasks],
        }
        return payload

    def _checklist_task_payload(self, task: ChecklistTask) -> dict[str, Any]:
        return {
            "id": task.id,
            "subject": task.subject,
            "description": task.description,
            "active_form": task.active_form,
            "status": task.status,
            "owner": task.owner,
            "blocks": list(task.blocks),
            "blocked_by": list(task.blocked_by),
            "metadata": dict(task.metadata),
            "created_at": task.created_at,
            "updated_at": task.updated_at,
        }

    def _find_checklist_duplicate_matches(
        self,
        *,
        subject: str,
        description: str,
        active_form: str,
        task_list_id: str | None = None,
    ) -> list[tuple[ChecklistTask, tuple[str, ...]]]:
        normalized_subject = self._normalize_checklist_duplicate_text(subject)
        normalized_description = self._normalize_checklist_duplicate_text(description)
        normalized_active_form = self._normalize_checklist_duplicate_text(active_form)
        if not normalized_subject:
            return []
        matches: list[tuple[ChecklistTask, tuple[str, ...]]] = []
        for task in self._session_checklist.list_tasks(
            task_list_id=task_list_id or self.checklist_task_list_id()
        ):
            if self._normalize_checklist_duplicate_text(task.subject) != normalized_subject:
                continue
            match_fields: list[str] = []
            if (
                normalized_description
                and self._normalize_checklist_duplicate_text(task.description) == normalized_description
            ):
                match_fields.append("description")
            if (
                normalized_active_form
                and self._normalize_checklist_duplicate_text(task.active_form) == normalized_active_form
            ):
                match_fields.append("active_form")
            if match_fields:
                matches.append((task, tuple(match_fields)))
        matches.sort(
            key=lambda item: (
                self._checklist_duplicate_status_rank(item[0].status),
                self._checklist_numeric_id_sort_key(item[0].id),
            )
        )
        return matches

    def _normalize_checklist_duplicate_text(self, value: str) -> str:
        return re.sub(r"\s+", " ", value.strip()).casefold()

    def _checklist_duplicate_status_rank(self, status: str) -> int:
        if status == "in_progress":
            return 0
        if status == "pending":
            return 1
        if status == "completed":
            return 2
        return 3

    def _checklist_numeric_id_sort_key(self, task_id: str) -> int:
        try:
            return int(task_id)
        except ValueError:
            return 10**9

    def _checklist_duplicate_reason(self, match_fields: tuple[str, ...]) -> str:
        labels = ["subject", *match_fields]
        if len(labels) == 2:
            return f"Matched existing checklist task by {labels[0]} and {labels[1]}."
        if len(labels) == 3:
            return f"Matched existing checklist task by {labels[0]}, {labels[1]}, and {labels[2]}."
        return "Matched existing checklist task."

    def _checklist_duplicate_create_result(
        self,
        matches: list[tuple[ChecklistTask, tuple[str, ...]]],
        *,
        subject: str,
        description: str,
        active_form: str,
    ) -> dict[str, Any]:
        matched_payloads = [self._checklist_task_payload(task) for task, _ in matches]
        primary_payload = dict(matched_payloads[0])
        primary_task_id = str(primary_payload["id"])
        primary_match_fields = matches[0][1]
        duplicate_guard = {
            "message": (
                f'Possible duplicate checklist task. Use existing task "{primary_task_id}" '
                "instead of creating a new one."
            ),
            "reason": self._checklist_duplicate_reason(primary_match_fields),
            "matched_task_id": primary_task_id,
            "matched_task_ids": [str(task_payload["id"]) for task_payload in matched_payloads],
            "matched_tasks": matched_payloads,
            "recommended_action": (
                f"Call session_task_get for task {primary_task_id}, then use session_task_update "
                "to continue or revise it."
            ),
            "candidate_subject": subject,
            "candidate_description": description,
            "candidate_active_form": active_form,
        }
        duplicate_guard["matchedTaskId"] = duplicate_guard["matched_task_id"]
        duplicate_guard["matchedTaskIds"] = list(duplicate_guard["matched_task_ids"])
        duplicate_guard["matchedTasks"] = list(duplicate_guard["matched_tasks"])
        duplicate_guard["recommendedAction"] = duplicate_guard["recommended_action"]
        return {
            **primary_payload,
            "task": dict(primary_payload),
            "created": False,
            "duplicate_guard": duplicate_guard,
            "duplicateGuard": dict(duplicate_guard),
            "message": duplicate_guard["message"],
        }

    def _normalized_checklist_duplicate_guard(
        self,
        *,
        task_id: str | None = None,
    ) -> dict[str, Any] | None:
        guard = self._latest_checklist_duplicate_guard
        if not isinstance(guard, dict) or not guard:
            return None
        matched_task_id = str(guard.get("matched_task_id") or guard.get("matchedTaskId") or "").strip()
        if task_id is not None and matched_task_id != task_id.strip():
            return None
        matched_task_ids_raw = guard.get("matched_task_ids") or guard.get("matchedTaskIds") or []
        matched_task_ids = [
            str(item).strip()
            for item in matched_task_ids_raw
            if str(item).strip()
        ]
        matched_tasks_raw = guard.get("matched_tasks") or guard.get("matchedTasks") or []
        matched_tasks = [dict(item) for item in matched_tasks_raw if isinstance(item, dict)]
        normalized = {
            "message": str(guard.get("message") or "").strip(),
            "reason": str(guard.get("reason") or "").strip(),
            "matched_task_id": matched_task_id,
            "matched_task_ids": matched_task_ids,
            "matched_tasks": matched_tasks,
            "recommended_action": str(
                guard.get("recommended_action") or guard.get("recommendedAction") or ""
            ).strip(),
            "candidate_subject": str(guard.get("candidate_subject") or "").strip(),
            "candidate_description": str(guard.get("candidate_description") or "").strip(),
            "candidate_active_form": str(guard.get("candidate_active_form") or "").strip(),
        }
        normalized["matchedTaskId"] = normalized["matched_task_id"]
        normalized["matchedTaskIds"] = list(normalized["matched_task_ids"])
        normalized["matchedTasks"] = list(normalized["matched_tasks"])
        normalized["recommendedAction"] = normalized["recommended_action"]
        return normalized

    def checklist_duplicate_guard_payload(self) -> dict[str, Any] | None:
        return self._task_detail_component.checklist_duplicate_guard_payload()

    def _checklist_task_detail_metadata(self, task: ChecklistTask) -> dict[str, Any]:
        return self._task_detail_component._checklist_task_detail_metadata(task)

    def checklist_task_detail_metadata(self, identifier: str) -> dict[str, Any] | None:
        return self._task_detail_component.checklist_task_detail_metadata(identifier)

    def _todo_payload_from_checklist_task(self, task: ChecklistTask) -> dict[str, Any]:
        return {
            "content": task.subject,
            "status": task.status,
            "active_form": task.active_form,
        }

    def _render_session_checklist_lines(self) -> list[str]:
        return self._task_detail_component._render_session_checklist_lines()

    def _checklist_prompt_context(self, *, max_tasks: int = 6) -> str | None:
        return self._task_detail_component._checklist_prompt_context(max_tasks=max_tasks)

    def _checklist_duplicate_prompt_lines(
        self,
        *,
        prefix: str,
        bullet_prefix: str = "",
    ) -> list[str]:
        return self._task_detail_component._checklist_duplicate_prompt_lines(
            prefix=prefix,
            bullet_prefix=bullet_prefix,
        )

    def _workspace_anchor_cwd(self) -> Path:
        return self._workspace_component._workspace_anchor_cwd()

    def _workspace_reference_paths(self) -> set[Path]:
        return self._workspace_component._workspace_reference_paths()

    def orphaned_workspace_diagnostics(self) -> list[OrphanedWorkspaceDiagnostic]:
        return self._workspace_component.orphaned_workspace_diagnostics()

    def _render_orphaned_workspace_lines(self) -> list[str]:
        return self._workspace_component._render_orphaned_workspace_lines()

    def _render_orphaned_workspace_entry(self, item: OrphanedWorkspaceDiagnostic) -> str:
        return self._workspace_component._render_orphaned_workspace_entry(item)

    def _workspace_inventory_references(self) -> list[_WorkspaceInventoryReference]:
        return self._workspace_component._workspace_inventory_references()

    def _workspace_inventory(self) -> list[dict[str, Any]]:
        return self._workspace_component._workspace_inventory()

    def _render_workspace_inventory_entry(self, item: dict[str, Any]) -> str:
        return self._workspace_component._render_workspace_inventory_entry(item)

    def describe_orphaned_workspaces(self) -> str:
        return self._workspace_component.describe_orphaned_workspaces()

    def describe_current_workspace(self) -> str:
        return self._workspace_component.describe_current_workspace()

    def describe_workspace_inventory_detail(self, selector: str) -> str:
        return self._workspace_component.describe_workspace_inventory_detail(selector)

    def preview_orphaned_workspace_cleanup(self) -> str:
        return self._workspace_component.preview_orphaned_workspace_cleanup()

    def workspace_cleanup_preview(self) -> str:
        return self._workspace_component.workspace_cleanup_preview()

    def repair_isolated_workspaces(self, target: str) -> str:
        return self._workspace_component.repair_isolated_workspaces(target)

    def workspace_repair(self, target: str) -> str:
        return self._workspace_component.workspace_repair(target)

    def apply_orphaned_workspace_cleanup(self, target: str) -> str:
        return self._workspace_component.apply_orphaned_workspace_cleanup(target)

    def workspace_cleanup_apply(self, target: str) -> str:
        return self._workspace_component.workspace_cleanup_apply(target)

    def _select_orphaned_workspaces(
        self,
        orphans: list[OrphanedWorkspaceDiagnostic],
        selector: str,
    ) -> tuple[list[OrphanedWorkspaceDiagnostic], str | None]:
        return self._workspace_component._select_orphaned_workspaces(orphans, selector)

    def _select_repair_targets(
        self,
        inventory: list[dict[str, Any]],
        selector: str,
    ) -> tuple[list[dict[str, Any]], str | None]:
        return self._workspace_component._select_repair_targets(inventory, selector)

    def _apply_repaired_workspace_metadata(self, item: dict[str, Any], repaired_workspace) -> None:
        self._workspace_component._apply_repaired_workspace_metadata(item, repaired_workspace)

    def _require_orphaned_workspace_cleanup_approval(
        self,
        targets: list[OrphanedWorkspaceDiagnostic],
        *,
        selector: str,
    ) -> None:
        self._workspace_component._require_orphaned_workspace_cleanup_approval(
            targets,
            selector=selector,
        )

    def runtime_cwd(self) -> Path:
        return self._workspace_component.runtime_cwd()

    def workspace_unavailable(self) -> bool:
        return self._workspace_component.workspace_unavailable()

    def build_system_prompt(self) -> str:
        base_prompt = self._runtime_context.build_system_prompt(self.state)
        checklist_guidance = (
            "Session checklist guidance:\n"
            "- If a session checklist exists, treat it as the canonical short task list for the current work.\n"
            "- Before creating new checklist tasks, call session_task_list to see what already exists.\n"
            "- Before updating a specific checklist task, call session_task_get unless you just created or listed it in the same turn.\n"
            "- Prefer updating existing checklist tasks instead of creating duplicates for the same outcome.\n"
            "- Keep checklist status aligned with real progress using session_task_list/get/update or todo_write."
        )
        checklist_context = self._checklist_prompt_context()
        parts = [base_prompt, checklist_guidance]
        if checklist_context:
            parts.append("Current session checklist:\n" + checklist_context)
        return "\n\n".join(part for part in parts if part)

    def latest_planning_artifact(self) -> PlanningArtifact | None:
        return self._plan_component.latest_planning_artifact()

    def planning_artifacts(self) -> list[PlanningArtifact]:
        return self._plan_component.planning_artifacts()

    def active_planning_artifact(self) -> PlanningArtifact | None:
        return self._plan_component.active_planning_artifact()

    def resolve_planning_artifact(self, identifier: str) -> PlanningArtifact | None:
        return self._plan_component.resolve_planning_artifact(identifier)

    def advisor_block_count(self) -> int:
        return sum(1 for item in self.state.advisor_review_history if item.status == "block")

    def turn_read_only_constraints_active(self) -> bool:
        return self._turn_read_only_constraints_active

    def set_turn_read_only_constraints_active(self, value: bool) -> None:
        self._turn_read_only_constraints_active = value
        if value:
            self.state.active_execution_constraint = "read-only"
        elif self.state.active_execution_constraint == "read-only":
            self.state.active_execution_constraint = "normal"

    def activate_execution_constraint(
        self,
        *,
        mode: str,
        source: str | None,
        reason: str | None,
        increment: bool = False,
    ) -> None:
        self.state.active_execution_constraint = mode
        self.state.constraint_source = source
        self.state.constraint_reason = reason
        self._turn_read_only_constraints_active = mode == "read-only"
        if increment:
            self.state.constraint_trigger_count += 1

    def clear_execution_constraint(self) -> None:
        self.activate_execution_constraint(mode="normal", source=None, reason=None)

    def begin_plan_execution(self, artifact: PlanningArtifact) -> None:
        self.state.active_execution_plan_id = artifact.artifact_id
        self.state.plan_execution_count += 1

    def clear_plan_execution(self) -> None:
        self.state.active_execution_plan_id = None

    def start_active_plan_execution_task(self, *, prompt: str, artifact: PlanningArtifact) -> str:
        task = self.task_manager.create(
            "plan_execution",
            prompt.strip() or artifact.goal,
            parent_session_id=self.state.session_id,
            provider=self.config.provider,
            model=self.config.model,
            cwd=str(self.config.cwd),
            task_role="execution",
            planner_kind=artifact.kind,
            active_plan_id=artifact.artifact_id,
            active_plan_goal=artifact.goal,
            plan_execution_mode="interactive_turn",
            plan_execution_phase="queued",
            plan_status="on-plan",
        )
        self.task_manager.set_progress(
            task.id,
            "Queued interactive turn under active plan",
            plan_execution_phase="queued",
            plan_status="on-plan",
        )
        return task.id

    def update_execution_task(
        self,
        task_id: str,
        summary: str,
        **metadata: Any,
    ) -> None:
        self.task_manager.set_progress(task_id, summary, **metadata)

    def complete_execution_task(
        self,
        task_id: str,
        output: str,
        **metadata: Any,
    ) -> None:
        self.task_manager.complete(task_id, output, **metadata)

    def fail_execution_task(
        self,
        task_id: str,
        error: str,
        **metadata: Any,
    ) -> None:
        self.task_manager.fail(task_id, error, **metadata)

    def _scout_tasks_for_artifact(self, artifact: PlanningArtifact) -> list[Any]:
        return self._plan_component._scout_tasks_for_artifact(artifact)

    def _execution_tasks_for_artifact(self, artifact: PlanningArtifact) -> list[Any]:
        return self._plan_component._execution_tasks_for_artifact(artifact)

    def describe_planning_lifecycle(self) -> list[str]:
        artifact = self.active_planning_artifact()
        lines = [
            f"advisor_blocks: {self.advisor_block_count()}",
            f"execution_constraints: {self.state.active_execution_constraint}",
            f"constraint_triggers: {self.state.constraint_trigger_count}",
            f"plan_executions: {self.state.plan_execution_count}",
            f"plan_drifts: {self.state.plan_drift_count}",
        ]
        if self.state.constraint_source:
            lines.append(f"constraint_source: {self.state.constraint_source}")
        if self.state.constraint_reason:
            lines.append(f"constraint_reason: {self.state.constraint_reason}")
        if self.state.active_execution_plan_id:
            lines.append(f"active_execution_plan_id: {self.state.active_execution_plan_id}")
        if self.state.last_plan_drift_status:
            lines.append(f"last_plan_drift_status: {self.state.last_plan_drift_status}")
        if self.state.last_plan_drift_reason:
            lines.append(f"last_plan_drift_reason: {self.state.last_plan_drift_reason}")
        if self.state.last_plan_drift_context:
            summary = self._recent_plan_drift_summary()
            if summary:
                lines.append(f"last_plan_drift_summary: {summary}")
        if artifact is None:
            lines.append("active_plan: none")
            return lines
        lines.append(f"active_plan_id: {artifact.artifact_id}")
        lines.append(f"active_plan_kind: {artifact.kind}")
        lines.append(f"active_plan_goal: {artifact.goal}")
        lines.append(
            "active_plan_read_only_subagents: "
            f"{'yes' if artifact.used_read_only_subagents else 'no'}"
        )
        lines.append(f"active_plan_scouts: {len(artifact.scout_categories)}")
        lines.append(f"active_plan_tasks: {len(artifact.task_ids)}")
        execution_tasks = self._execution_tasks_for_artifact(artifact)
        lines.append(f"active_plan_execution_tasks: {len(execution_tasks)}")
        lines.append(
            "active_plan_running_execution_tasks: "
            + str(sum(1 for task in execution_tasks if task.status == "running"))
        )
        if artifact.advisor_status:
            lines.append(f"active_plan_advisor_status: {artifact.advisor_status}")
        if artifact.advisor_risk_flags:
            lines.append("active_plan_risk_flags: " + ", ".join(artifact.advisor_risk_flags))
        if artifact.supersedes_artifact_id:
            lines.append(f"active_plan_supersedes: {artifact.supersedes_artifact_id}")
        if artifact.superseded_by_artifact_id:
            lines.append(f"active_plan_superseded_by: {artifact.superseded_by_artifact_id}")
        if artifact.derived_from_drift:
            lines.append("active_plan_derived_from_drift: yes")
        if artifact.derivation_reason:
            lines.append(f"active_plan_derivation_reason: {artifact.derivation_reason}")
        return lines

    def build_turn_prompt(self, prompt: str) -> str:
        context_sections: list[str] = []
        artifact = self.active_planning_artifact()
        if artifact is not None and artifact.kind == "ultraplan":
            summary = artifact.summary.strip()
            if len(summary) > 1200:
                summary = summary[:1197] + "..."
            lines = [
                "Use the recent ultraplan artifact below as explicit execution context unless the user request overrides it.",
                "",
                f"Plan goal: {artifact.goal}",
                f"Plan summary:\n{summary}",
            ]
            if artifact.advisor_status:
                lines.append(f"Plan advisor status: {artifact.advisor_status}")
            if artifact.advisor_risk_flags:
                lines.append("Plan advisor risk flags: " + ", ".join(artifact.advisor_risk_flags))
            context_sections.append("\n".join(lines))
        checklist_context = self._checklist_prompt_context(max_tasks=8)
        if checklist_context:
            context_sections.append(checklist_context)
        if not context_sections:
            return prompt
        lines = []
        for index, section in enumerate(context_sections):
            if index:
                lines.append("")
            lines.append(section)
        lines.extend(
            [
                "",
                "Current user request:",
                prompt,
            ]
        )
        return "\n".join(lines)

    def active_skills(self):
        return self._runtime_context.active_skills(self.state)

    def active_skills_by_source(self):
        return self._runtime_context.active_skills_by_source(self.state)

    def create_child_session(
        self,
        *,
        interactive: bool = False,
        isolated_workspace: bool = False,
    ) -> "Session":
        return self._session_factory.create_child_session(
            self,
            interactive=interactive,
            isolated_workspace=isolated_workspace,
        )

    def requires_read_only_subagents(self) -> bool:
        return self._require_read_only_subagents

    def active_command_policy(self) -> TurnCommandPolicy | None:
        return self._active_command_policy

    def session_command_policy(self) -> TurnCommandPolicy | None:
        has_constraints = (
            self.state.session_command_policy_name is not None
            or self.state.session_command_policy_source is not None
            or bool(self.state.session_command_policy_allowed_tool_names)
            or bool(self.state.session_command_policy_allowed_bash_prefixes)
            or self.state.session_command_policy_require_read_only_subagents
        )
        if not has_constraints:
            return None
        return self._compile_turn_command_policy(
            allowed_tool_names=(
                tuple(self.state.session_command_policy_allowed_tool_names)
                if self.state.session_command_policy_allowed_tool_names
                else None
            ),
            allowed_bash_command_prefixes=(
                tuple(self.state.session_command_policy_allowed_bash_prefixes)
                if self.state.session_command_policy_allowed_bash_prefixes
                else None
            ),
            require_read_only_subagents=self.state.session_command_policy_require_read_only_subagents,
            command_policy_name=self.state.session_command_policy_name,
            command_policy_source=self.state.session_command_policy_source,
        )

    def set_session_execution_contract(
        self,
        *,
        execution_mode: str,
        command_policy: TurnCommandPolicy | None = None,
        active_execution_constraint: str | None = None,
        constraint_source: str | None = None,
        constraint_reason: str | None = None,
    ) -> None:
        self.state.session_execution_mode = execution_mode.strip() or "main"
        if command_policy is None:
            self.state.session_command_policy_name = None
            self.state.session_command_policy_source = None
            self.state.session_command_policy_allowed_tool_names = []
            self.state.session_command_policy_allowed_bash_prefixes = []
            self.state.session_command_policy_require_read_only_subagents = False
        else:
            self.state.session_command_policy_name = command_policy.name
            self.state.session_command_policy_source = command_policy.source
            self.state.session_command_policy_allowed_tool_names = (
                sorted(command_policy.allowed_tool_names)
                if command_policy.allowed_tool_names is not None
                else []
            )
            self.state.session_command_policy_allowed_bash_prefixes = list(
                command_policy.allowed_bash_command_prefixes or ()
            )
            self.state.session_command_policy_require_read_only_subagents = (
                command_policy.require_read_only_subagents
            )
        if active_execution_constraint is not None:
            self.activate_execution_constraint(
                mode=active_execution_constraint,
                source=constraint_source,
                reason=constraint_reason,
            )

    def execution_contract_payload(self) -> dict[str, Any]:
        policy = self.session_command_policy()
        return {
            "session_execution_mode": self.state.session_execution_mode,
            "session_command_policy_name": policy.name if policy is not None else None,
            "session_command_policy_source": policy.source if policy is not None else None,
            "session_command_policy_allowed_tool_names": (
                sorted(policy.allowed_tool_names)
                if policy is not None and policy.allowed_tool_names is not None
                else []
            ),
            "session_command_policy_allowed_bash_prefixes": list(
                policy.allowed_bash_command_prefixes or ()
            )
            if policy is not None
            else [],
            "session_command_policy_require_read_only_subagents": (
                policy.require_read_only_subagents if policy is not None else False
            ),
            "session_command_policy_enforce_read_only_bash": (
                policy.enforce_read_only_bash if policy is not None else False
            ),
        }

    def analyze_bash_command(self, command: str) -> ShellCommandAnalysis:
        return BashTool().analyze_command(self.runtime_cwd(), command)

    def evaluate_bash_command_policy(
        self,
        command: str,
        *,
        analysis: ShellCommandAnalysis | None = None,
    ) -> BashCommandPolicyResult:
        policy = self._active_command_policy
        if policy is None or not policy.allowed_bash_command_prefixes:
            return BashCommandPolicyResult(allowed=True)
        command_analysis = analysis or self.analyze_bash_command(command)
        allowed_prefixes = tuple(policy.allowed_bash_command_prefixes)
        normalized_prefixes = tuple(prefix.casefold() for prefix in allowed_prefixes)
        for segment in command_analysis.segments:
            unsafe_features = tuple(
                feature for feature in segment.features if feature not in {"env_assignment"}
            )
            if unsafe_features:
                return BashCommandPolicyResult(
                    allowed=False,
                    policy_name=policy.name,
                    policy_source=policy.source,
                    allowed_prefixes=allowed_prefixes,
                    violating_segment_index=segment.index,
                    violating_segment=segment.raw_command,
                    violating_features=unsafe_features,
                    violation_kind="complex_feature",
                    reason=self._format_bash_command_policy_violation(
                        policy,
                        segment,
                        allowed_prefixes=allowed_prefixes,
                        reason_kind="complex_feature",
                    ),
                )
            normalized = (segment.policy_command or segment.raw_command).casefold()
            if not any(normalized.startswith(prefix) for prefix in normalized_prefixes):
                return BashCommandPolicyResult(
                    allowed=False,
                    policy_name=policy.name,
                    policy_source=policy.source,
                    allowed_prefixes=allowed_prefixes,
                    violating_segment_index=segment.index,
                    violating_segment=segment.raw_command,
                    violating_features=segment.features,
                    violation_kind="prefix",
                    reason=self._format_bash_command_policy_violation(
                        policy,
                        segment,
                        allowed_prefixes=allowed_prefixes,
                        reason_kind="prefix",
                    ),
                )
            if segment.uncertain:
                return BashCommandPolicyResult(
                    allowed=False,
                    policy_name=policy.name,
                    policy_source=policy.source,
                    allowed_prefixes=allowed_prefixes,
                    violating_segment_index=segment.index,
                    violating_segment=segment.raw_command,
                    violating_features=segment.features,
                    violation_kind="uncertain",
                    reason=self._format_bash_command_policy_violation(
                        policy,
                        segment,
                        allowed_prefixes=allowed_prefixes,
                        reason_kind="uncertain",
                    ),
                )
            if policy.enforce_read_only_bash and segment.risk_level != "shell_read":
                return BashCommandPolicyResult(
                    allowed=False,
                    policy_name=policy.name,
                    policy_source=policy.source,
                    allowed_prefixes=allowed_prefixes,
                    violating_segment_index=segment.index,
                    violating_segment=segment.raw_command,
                    violating_features=segment.features,
                    violation_kind="read_only_risk",
                    reason=self._format_bash_command_policy_violation(
                        policy,
                        segment,
                        allowed_prefixes=allowed_prefixes,
                        reason_kind="read_only_risk",
                    ),
                )
        return BashCommandPolicyResult(
            allowed=True,
            policy_name=policy.name,
            policy_source=policy.source,
            allowed_prefixes=allowed_prefixes,
        )

    def validate_tool_call_policy(self, tool_name: str, tool_input: dict[str, Any]) -> None:
        self._validate_workspace_runtime(tool_name, tool_input)
        policy = self._active_command_policy
        if policy is None:
            return
        if policy.allowed_tool_names is not None and tool_name not in policy.allowed_tool_names:
            allowed = ", ".join(sorted(policy.allowed_tool_names))
            raise PermissionDeniedError(
                f'Tool "{tool_name}" is not allowed in command mode "{policy.name}". '
                f"Allowed tools: {allowed}"
            )
        if tool_name != "bash":
            return
        command = str(tool_input.get("command") or "")
        result = self.evaluate_bash_command_policy(command)
        if not result.allowed:
            raise PermissionDeniedError(result.reason)

    def _validate_workspace_runtime(self, tool_name: str, tool_input: dict[str, Any]) -> None:
        if not self.workspace_unavailable():
            return
        if tool_name == "bash":
            command = str(tool_input.get("command") or "")
            analysis = self.analyze_bash_command(command)
            if analysis.risk_level == "shell_read":
                return
            raise PermissionDeniedError(self._workspace_unavailable_denial_message(tool_name))
        tool = next((item for item in self.tools if item.name == tool_name), None)
        if tool is not None and tool.read_only:
            return
        raise PermissionDeniedError(self._workspace_unavailable_denial_message(tool_name))

    def _workspace_unavailable_denial_message(self, tool_name: str) -> str:
        reason = self.state.workspace_unavailable_reason or "Isolated workspace is unavailable."
        fallback = self.state.workspace_fallback_cwd or self.state.original_cwd or str(self.config.cwd)
        repair_selector = self.state.session_id or self.state.workspace_label or "all"
        return (
            f'Workspace unavailable for tool "{tool_name}". {reason} '
            f"Fallback workspace: {fallback}. "
            "Read-only diagnostics remain available, but write tools and write-capable shell commands are blocked. "
            f"Recommended actions: /workspaces list, /workspaces repair {repair_selector}, /workspaces cleanup."
        )

    def bash_command_policy_event_metadata(
        self,
        command: str,
        *,
        analysis: ShellCommandAnalysis | None = None,
    ) -> dict[str, Any]:
        result = self.evaluate_bash_command_policy(command, analysis=analysis)
        if result.allowed or not result.policy_name:
            return {}
        return {
            "command_mode_name": result.policy_name,
            "command_mode_allowed_prefixes": result.allowed_prefixes,
            "command_mode_violating_segment": (
                result.violating_segment if result.violating_segment else None
            ),
            "command_mode_violating_segment_index": result.violating_segment_index,
            "command_mode_complex_features": result.violating_features,
        }

    def run_subagent(
        self,
        *,
        description: str,
        prompt: str,
        isolated_workspace: bool = False,
        read_only: bool = False,
    ) -> str:
        if self.depth >= self.config.max_agent_depth:
            raise RuntimeError("Max agent depth reached.")
        read_only_policy = (
            self._compile_turn_command_policy(
                allowed_tool_names=READ_ONLY_SUBAGENT_TOOL_NAMES,
                allowed_bash_command_prefixes=READ_ONLY_SUBAGENT_BASH_PREFIXES,
                require_read_only_subagents=True,
                command_policy_name="read-only-subagent",
                command_policy_source="subagent",
            )
            if read_only
            else None
        )
        child = self.create_child_session(
            interactive=False,
            isolated_workspace=isolated_workspace,
        )
        child.set_session_execution_contract(
            execution_mode="read-only-subagent" if read_only else "child-session",
            command_policy=read_only_policy,
            active_execution_constraint="read-only" if read_only else "normal",
            constraint_source="session_execution_contract" if read_only else None,
            constraint_reason="read-only subagent contract" if read_only else None,
        )
        child_prompt = f"{description}\n\n{prompt}"
        if read_only:
            child_prompt = _build_read_only_subagent_prompt(description=description, prompt=prompt)
        return child.ask(
            child_prompt,
            allowed_tool_names=READ_ONLY_SUBAGENT_TOOL_NAMES if read_only else None,
            allowed_bash_command_prefixes=READ_ONLY_SUBAGENT_BASH_PREFIXES if read_only else None,
            require_read_only_subagents=read_only,
            command_policy_name="read-only-subagent" if read_only else None,
            command_policy_source="subagent" if read_only else None,
        )

    def launch_background_agent(
        self,
        *,
        description: str,
        prompt: str,
        isolated_workspace: bool = False,
        read_only: bool = False,
    ) -> str:
        if self.depth >= self.config.max_agent_depth:
            raise RuntimeError("Max agent depth reached.")
        active_plan = self.active_planning_artifact()
        task = self.task_manager.create(
            "agent",
            description,
            parent_session_id=self.state.session_id,
            provider=self.config.provider,
            model=self.config.model,
            cwd=str(self.config.cwd),
            isolated_workspace=isolated_workspace,
            read_only=read_only,
            task_role="execution" if active_plan is not None else "background",
            active_plan_id=active_plan.artifact_id if active_plan is not None else None,
            active_plan_goal=active_plan.goal if active_plan is not None else None,
            plan_execution_mode="background_agent" if active_plan is not None else None,
            plan_execution_phase="queued" if active_plan is not None else None,
            plan_status="on-plan" if active_plan is not None else None,
        )
        self.task_manager.set_progress(task.id, "Queued background agent")

        def worker() -> None:
            child = None
            output = ""
            error: Exception | None = None
            try:
                read_only_policy = (
                    self._compile_turn_command_policy(
                        allowed_tool_names=READ_ONLY_SUBAGENT_TOOL_NAMES,
                        allowed_bash_command_prefixes=READ_ONLY_SUBAGENT_BASH_PREFIXES,
                        require_read_only_subagents=True,
                        command_policy_name="read-only-subagent",
                        command_policy_source="background-subagent",
                    )
                    if read_only
                    else None
                )
                child = self.create_child_session(
                    interactive=False,
                    isolated_workspace=isolated_workspace,
                )
                child.set_session_execution_contract(
                    execution_mode="read-only-subagent" if read_only else "background-agent",
                    command_policy=read_only_policy,
                    active_execution_constraint="read-only" if read_only else "normal",
                    constraint_source="session_execution_contract" if read_only else None,
                    constraint_reason="read-only background agent contract" if read_only else None,
                )
                self.task_manager.set_progress(
                    task.id,
                    "Running background agent",
                    **self._task_workspace_metadata(child),
                    plan_execution_phase="running" if active_plan is not None else None,
                )
                output = child.ask(
                    _build_read_only_subagent_prompt(description=description, prompt=prompt)
                    if read_only
                    else f"{description}\n\n{prompt}",
                    sink=self._build_background_task_sink(task.id),
                    allowed_tool_names=READ_ONLY_SUBAGENT_TOOL_NAMES if read_only else None,
                    allowed_bash_command_prefixes=READ_ONLY_SUBAGENT_BASH_PREFIXES if read_only else None,
                    require_read_only_subagents=read_only,
                    command_policy_name="read-only-subagent" if read_only else None,
                    command_policy_source="background-subagent" if read_only else None,
                )
            except Exception as exc:  # noqa: BLE001
                error = exc
            finally:
                if child is not None:
                    child.close()
            workspace_metadata = self._task_workspace_metadata(child)
            if error is None:
                self.task_manager.complete(
                    task.id,
                    output,
                    **workspace_metadata,
                    plan_execution_phase="completed" if active_plan is not None else None,
                    plan_status="on-plan" if active_plan is not None else None,
                )
            else:
                self.task_manager.fail(
                    task.id,
                    f"{type(error).__name__}: {error}",
                    **workspace_metadata,
                    plan_execution_phase="failed" if active_plan is not None else None,
                )

        Thread(target=worker, daemon=True).start()
        return task.id

    def describe_tools(self) -> str:
        active_deferred = set(self.state.activated_deferred_tool_names)
        lines = []
        for tool in self.tools:
            mode = "read-only" if tool.read_only else "write"
            concurrency = "parallel" if tool.concurrency_safe else "serial"
            risk = tool.declared_risk_level()
            availability = "default"
            if tool.is_deferred():
                availability = "active-deferred" if tool.name in active_deferred else "deferred"
            lines.append(
                f"{tool.name}: {tool.description} "
                f"[{mode}, {concurrency}, risk={risk}, availability={availability}]"
            )
        return "\n".join(lines)

    def describe_permissions(self) -> str:
        lines = [
            "permissions",
            f"mode: {self.permission_manager.mode}",
            f"config_path: {self._permission_config_path()}",
            f"workspace_rules: {len(self._workspace_permission_rules)}",
            f"session_rules: {len(self.permission_manager.session_rules)}",
        ]
        if self._workspace_permission_rules:
            lines.append("workspace:")
            for index, rule in enumerate(self._workspace_permission_rules, start=1):
                lines.append(f"- workspace:{index} {rule.describe()}")
        if self.permission_manager.session_rules:
            lines.append("session:")
            for index, rule in enumerate(self.permission_manager.session_rules, start=1):
                lines.append(f"- session:{index} {rule.describe()}")
        if not self._workspace_permission_rules and not self.permission_manager.session_rules:
            lines.append("No permission rules configured.")
        lines.append("")
        lines.append("Notes:")
        lines.append("- shell rules match the prefix of any analyzed bash command segment, not only the full command")
        lines.append("- path rules match workspace-relative targets extracted from tools and shell segments")
        lines.append("- shell rules are permission-layer approvals; command mode policy is a separate per-turn restriction layer")
        lines.append("")
        lines.append("Commands:")
        lines.append("- /permissions list")
        lines.append("- /permissions allow <tool|shell|path|risk> <value>")
        lines.append("- /permissions ask <tool|shell|path|risk> <value>")
        lines.append("- /permissions deny <tool|shell|path|risk> <value>")
        lines.append("- /permissions remove <session|workspace> <index>")
        lines.append("- /permissions clear [session|workspace|all]")
        lines.append("- /permissions save")
        lines.append("- /permissions export [path]")
        lines.append("- /permissions reload")
        return "\n".join(lines)

    def describe_tasks(self, *, mode: str = "list") -> str:
        return self._task_detail_component.describe_tasks(mode=mode)

    def resolve_task(self, identifier: str) -> Any | None:
        return self._task_detail_component.resolve_task(identifier)

    def describe_task_detail(
        self,
        identifier: str,
        *,
        file_index: int = 0,
        preserve_current_focus: bool = True,
    ) -> str:
        return self._task_detail_component.describe_task_detail(
            identifier,
            file_index=file_index,
            preserve_current_focus=preserve_current_focus,
        )

    def _describe_checklist_task_detail(self, task: ChecklistTask, *, file_index: int = 0) -> str:
        return self._task_detail_component._describe_checklist_task_detail(task, file_index=file_index)

    def _recent_task_activity_lines(self, limit: int = 6) -> list[str]:
        return self._task_detail_component._recent_task_activity_lines(limit=limit)

    def open_task_detail(self, identifier: str) -> str:
        return self._task_detail_component.open_task_detail(identifier)

    def describe_task_drift_detail(self, identifier: str) -> str:
        return self._task_detail_component.describe_task_drift_detail(identifier)

    def open_task_detail_advisor(self, identifier: str) -> str:
        return self._task_detail_component.open_task_detail_advisor(identifier)

    def open_task_drift_detail(self, identifier: str) -> str:
        return self._task_detail_component.open_task_drift_detail(identifier)

    def describe_mcp_servers(self) -> str:
        config_path = self.config.mcp_config_path
        header = f"config_path: {config_path}" if config_path is not None else "config_path: (none)"
        if self.mcp_registry is None or not self.mcp_registry.list_servers():
            return header + "\nNo MCP servers configured."
        summary = self._mcp_health_summary()
        lines = []
        for server_name in self.mcp_registry.list_servers():
            server = self.mcp_registry.get_server(server_name)
            version = (
                server.initialize_result.server_version
                if server.initialize_result is not None
                else "unknown"
            )
            line = (
                f"{server_name}: transport={server.client.config.transport} "
                f"status={server.status} version={version} "
                f"tools={len(server.tools)} resources={len(server.resources)}"
            )
            if server.client.config.auth_mode:
                line += f" auth={server.client.config.auth_mode}"
            if server.last_error:
                line += f" error={server.last_error}"
            if server.last_connected_at:
                line += f" connected_at={server.last_connected_at}"
            if server.last_failed_at:
                line += f" failed_at={server.last_failed_at}"
            if server.failure_count:
                line += f" failures={server.failure_count}"
            retry_in = self.mcp_registry.retry_wait_seconds(server_name)
            if retry_in:
                line += f" retry_in={retry_in}s"
            lines.append(line)
        return "\n".join([header, summary, *lines])

    def describe_mcp_tools(self) -> str:
        if self.mcp_registry is None:
            return "No MCP servers configured."
        refs = self.mcp_registry.list_tool_references()
        if not refs:
            counts = self._mcp_server_counts()
            return (
                "No MCP tools loaded. "
                f"servers={counts['servers']} resources={counts['resources']} "
                f"failed={counts['failed']} retrying={counts['retrying']}"
            )
        lines = []
        for ref in refs:
            mapped_name = make_mcp_tool_name(ref.server_name, ref.tool.name)
            lines.append(
                f"{ref.qualified_name} -> {mapped_name}: {ref.tool.description}"
            )
        return "\n".join(lines)

    def diagnose_mcp_tool(
        self,
        server_name: str,
        tool_name: str,
        *,
        arguments: dict[str, Any] | None = None,
    ) -> McpDiagnosticResult:
        if self.mcp_registry is None or server_name not in self.mcp_registry.list_servers():
            return McpDiagnosticResult(
                server_name=server_name,
                tool_name=tool_name,
                ok=False,
                source="config",
                error_text=f'MCP server "{server_name}" is not configured.',
            )

        registry = self.mcp_registry
        server = self.ensure_mcp_server_connected(server_name)
        assert server is not None
        transport = server.client.config.transport
        retry_in = registry.retry_wait_seconds(server_name)
        if server.status != "connected":
            return McpDiagnosticResult(
                server_name=server_name,
                tool_name=tool_name,
                ok=False,
                source="transport",
                transport=transport,
                server_status=server.status,
                retry_in=retry_in,
                failure_count=server.failure_count,
                error_text=server.last_error or f'MCP server "{server_name}" is unavailable.',
            )

        reference = registry.find_tool_reference(server_name, tool_name)
        if reference is None:
            return McpDiagnosticResult(
                server_name=server_name,
                tool_name=tool_name,
                ok=False,
                source="mcp_server",
                transport=transport,
                server_status=server.status,
                failure_count=server.failure_count,
                error_text=f'MCP tool "{tool_name}" is not available on server "{server_name}".',
            )

        try:
            result = server.client.call_tool(tool_name, arguments or {})
        except (McpClientError, OSError, TimeoutError) as exc:
            self.handle_mcp_server_failure(server_name, f"{type(exc).__name__}: {exc}")
            failed = registry.get_server(server_name)
            return McpDiagnosticResult(
                server_name=server_name,
                tool_name=tool_name,
                ok=False,
                source="transport",
                transport=transport,
                server_status=failed.status,
                retry_in=registry.retry_wait_seconds(server_name),
                failure_count=failed.failure_count,
                error_text=f"{type(exc).__name__}: {exc}",
            )

        rendered = _render_mcp_content(result.content)
        if result.is_error:
            return McpDiagnosticResult(
                server_name=server_name,
                tool_name=tool_name,
                ok=False,
                source="mcp_tool",
                transport=transport,
                server_status=server.status,
                retry_in=retry_in,
                failure_count=server.failure_count,
                result_text=rendered,
                error_text="MCP tool returned an error payload.",
            )
        return McpDiagnosticResult(
            server_name=server_name,
            tool_name=tool_name,
            ok=True,
            source="ok",
            transport=transport,
            server_status=server.status,
            retry_in=retry_in,
            failure_count=server.failure_count,
            result_text=rendered,
        )

    def describe_mcp_tool_diagnostic(
        self,
        server_name: str,
        tool_name: str,
        *,
        arguments: dict[str, Any] | None = None,
    ) -> str:
        result = self.diagnose_mcp_tool(server_name, tool_name, arguments=arguments)
        lines = [
            f"server: {result.server_name}",
            f"tool: {result.tool_name}",
            f"ok: {'yes' if result.ok else 'no'}",
            f"source: {result.source}",
        ]
        if result.transport:
            lines.append(f"transport: {result.transport}")
        if result.server_status:
            lines.append(f"server_status: {result.server_status}")
        if result.failure_count:
            lines.append(f"failure_count: {result.failure_count}")
        if result.retry_in:
            lines.append(f"retry_in: {result.retry_in}s")
        if result.error_text:
            lines.append(f"error: {result.error_text}")
        if result.result_text:
            lines.append("result:")
            lines.append(result.result_text)
        next_steps = self._mcp_next_steps(result.source, server_name=server_name, tool_name=tool_name)
        if next_steps:
            lines.append("next_steps:")
            lines.extend(f"- {step}" for step in next_steps)
        return "\n".join(lines)

    def verify_mcp_tool_via_model(
        self,
        server_name: str,
        tool_name: str,
        *,
        arguments: dict[str, Any] | None = None,
    ) -> McpVerificationResult:
        preflight = self.diagnose_mcp_tool(server_name, tool_name, arguments=arguments)
        mapped_tool_name = make_mcp_tool_name(server_name, tool_name)
        if not preflight.ok:
            return McpVerificationResult(
                server_name=server_name,
                tool_name=tool_name,
                mapped_tool_name=mapped_tool_name,
                ok=False,
                source=preflight.source,
                error_text=preflight.error_text or "MCP preflight failed.",
                preflight=preflight,
            )

        capabilities = getattr(self.provider, "capabilities", None)
        if capabilities is None or not capabilities.supports_tool_calling:
            return McpVerificationResult(
                server_name=server_name,
                tool_name=tool_name,
                mapped_tool_name=mapped_tool_name,
                ok=False,
                source="model",
                error_text="Current model/provider does not support tool calling.",
                preflight=preflight,
            )

        payload = arguments or {}
        prompt = (
            "You must call the MCP tool exactly once.\n"
            f"Tool name: {mapped_tool_name}\n"
            f"Arguments JSON: {payload}\n"
            "Do not guess or answer without using the tool. "
            "After receiving the tool result, briefly summarize it."
        )
        events: list[RuntimeEvent] = []
        try:
            output = self.ask(prompt, sink=events.append)
        except Exception as exc:  # noqa: BLE001
            return McpVerificationResult(
                server_name=server_name,
                tool_name=tool_name,
                mapped_tool_name=mapped_tool_name,
                ok=False,
                source="model",
                error_text=f"{type(exc).__name__}: {exc}",
                preflight=preflight,
            )

        tool_called = any(
            event.kind == "tool_started" and event.tool_name == mapped_tool_name
            for event in events
        )
        if not tool_called:
            return McpVerificationResult(
                server_name=server_name,
                tool_name=tool_name,
                mapped_tool_name=mapped_tool_name,
                ok=False,
                source="model",
                output_text=output,
                error_text="Model did not invoke the required MCP tool.",
                tool_called=False,
                preflight=preflight,
            )

        for event in events:
            if event.kind == "tool_failed" and event.tool_name == mapped_tool_name:
                source = "transport"
                if self.mcp_registry is not None and server_name in self.mcp_registry.list_servers():
                    server = self.mcp_registry.get_server(server_name)
                    if server.status == "connected":
                        source = "mcp_server"
                return McpVerificationResult(
                    server_name=server_name,
                    tool_name=tool_name,
                    mapped_tool_name=mapped_tool_name,
                    ok=False,
                    source=source,
                    output_text=output,
                    error_text=event.message,
                    tool_called=True,
                    preflight=preflight,
                )

        return McpVerificationResult(
            server_name=server_name,
            tool_name=tool_name,
            mapped_tool_name=mapped_tool_name,
            ok=True,
            source="ok",
            output_text=output,
            tool_called=True,
            preflight=preflight,
        )

    def describe_mcp_verification(
        self,
        server_name: str,
        tool_name: str,
        *,
        arguments: dict[str, Any] | None = None,
    ) -> str:
        result = self.verify_mcp_tool_via_model(server_name, tool_name, arguments=arguments)
        lines = [
            f"server: {result.server_name}",
            f"tool: {result.tool_name}",
            f"mapped_tool: {result.mapped_tool_name}",
            f"ok: {'yes' if result.ok else 'no'}",
            f"source: {result.source}",
            f"tool_called: {'yes' if result.tool_called else 'no'}",
        ]
        if result.preflight is not None:
            lines.extend(
                [
                    "preflight:",
                    f"  ok: {'yes' if result.preflight.ok else 'no'}",
                    f"  source: {result.preflight.source}",
                ]
            )
            if result.preflight.transport:
                lines.append(f"  transport: {result.preflight.transport}")
            if result.preflight.server_status:
                lines.append(f"  server_status: {result.preflight.server_status}")
            if result.preflight.failure_count:
                lines.append(f"  failure_count: {result.preflight.failure_count}")
            if result.preflight.retry_in:
                lines.append(f"  retry_in: {result.preflight.retry_in}s")
        if result.error_text:
            lines.append(f"error: {result.error_text}")
        if result.output_text:
            lines.append("output:")
            lines.append(result.output_text)
        next_steps = self._mcp_next_steps(
            result.source,
            server_name=server_name,
            tool_name=tool_name,
            model_verification=True,
        )
        if next_steps:
            lines.append("next_steps:")
            lines.extend(f"- {step}" for step in next_steps)
        return "\n".join(lines)

    def describe_history(self, limit: int = 12, *, section: str = "all") -> str:
        if section not in {"all", "messages", "tasks", "workspace", "changes"}:
            return "Usage: /history [all|messages|tasks|workspace|changes]"
        rendered_sections = self._history_section_lines(limit=limit)
        selected_sections = (
            ("messages", "workspace", "tasks", "changes")
            if section == "all"
            else (section,)
        )
        lines: list[str] = []
        for name in selected_sections:
            section_lines = rendered_sections.get(name, [])
            if not section_lines:
                continue
            if lines:
                lines.append("")
            lines.extend(section_lines)
        if lines:
            return "\n".join(lines)
        empty_messages = {
            "messages": "No recent messages.",
            "tasks": "No recent task activity.",
            "workspace": "No workspace audit history.",
            "changes": "No recorded workspace changes.",
            "all": "No messages yet.",
        }
        return empty_messages[section]

    def describe_compact(
        self,
        *,
        section: str = "status",
        instructions: str | None = None,
    ) -> str:
        if section not in {"status", "preview"}:
            return "Usage: /compact [status|preview [instructions...]|<instructions...>]"
        preview = self._history_compaction_preview_payload(instructions=instructions)
        if section == "preview":
            return self._describe_compact_preview(preview)
        return self._describe_compact_status(preview)

    def _history_state_payload(
        self,
        *,
        message_count: int,
        context_summary: str | None = None,
        context_summary_present: bool | None = None,
    ) -> dict[str, Any]:
        summary_text = str(context_summary or "").strip()
        summary_present = bool(summary_text) if context_summary_present is None else bool(context_summary_present)
        summary_preview = summary_text
        if len(summary_preview) > 180:
            summary_preview = summary_preview[:177] + "..."
        active_message_history = message_count > 0
        return {
            "active_message_history": active_message_history,
            "active_message_count": message_count,
            "compacted_context_summary": summary_present,
            "compacted_context_summary_chars": len(summary_text),
            "compacted_context_summary_preview": summary_preview,
            "history_cleared": not active_message_history and not summary_present,
        }

    def _history_state_lines(
        self,
        payload: dict[str, Any],
        *,
        include_summary_preview: bool = False,
    ) -> list[str]:
        lines = [
            "active message history: " + ("yes" if payload["active_message_history"] else "no"),
            f"active message count: {payload['active_message_count']}",
            "compacted context summary: " + ("yes" if payload["compacted_context_summary"] else "no"),
            "history cleared: " + ("yes" if payload["history_cleared"] else "no"),
        ]
        if payload["compacted_context_summary"]:
            lines.append(
                f"compacted context summary chars: {payload['compacted_context_summary_chars']}"
            )
            if include_summary_preview and payload["compacted_context_summary_preview"]:
                lines.append(
                    "compacted summary preview: " + str(payload["compacted_context_summary_preview"])
                )
        return lines

    def compact_history_into_context_summary(self, instructions: str | None = None) -> str:
        result = self.apply_history_compaction(persist=True, instructions=instructions)
        history_state = self._history_state_payload(
            message_count=len(self.state.messages),
            context_summary=self.state.context_summary,
        )
        if not bool(result.get("applied")):
            return "\n".join(
                [
                    "No history compaction needed.",
                    "compaction mode: local estimated summary",
                    *self._history_state_lines(history_state),
                    f"message count: {result['message_count']}",
                    f"keep last threshold: {result['keep_last']}",
                    *self._compact_instruction_lines(result),
                    "next actions:",
                    "- /compact status",
                    "- /history messages",
                    "- /status workflow",
                ]
            )
        return "\n".join(
            [
                "history compacted:",
                "compaction mode: local estimated summary",
                *self._history_state_lines(history_state),
                f"compacted messages: {result['compacted_count']}",
                f"kept messages: {result['kept_count']}",
                *self._compact_instruction_lines(result),
                "next actions:",
                "- /compact status",
                "- /history messages",
                "- /status workflow",
            ]
        )

    def _history_compaction_keep_last(self) -> int:
        return max(
            1,
            min(self.config.history_keep_last_messages, self.config.max_history_messages),
        )

    def _history_compaction_preview_payload(
        self,
        *,
        instructions: str | None = None,
    ) -> dict[str, Any]:
        result = build_history_compaction_result(
            HistoryCompactionRequest(
                messages=list(self.state.messages),
                existing_summary=self.state.context_summary or "",
                keep_last=self._history_compaction_keep_last(),
                max_summary_chars=self.config.max_context_summary_chars,
                instructions=instructions,
            ),
            summarize_message=self._summarize_message,
        )
        return result.to_payload()

    def _merge_context_summary(self, existing_summary: str, new_summary: str) -> str:
        return merge_context_summary(
            existing_summary,
            new_summary,
            max_summary_chars=self.config.max_context_summary_chars,
        )

    def apply_history_compaction(
        self,
        *,
        persist: bool,
        sink=None,
        instructions: str | None = None,
    ) -> dict[str, Any]:
        preview = self._history_compaction_preview_payload(instructions=instructions)
        result = dict(preview)
        result["applied"] = False
        if not bool(preview.get("would_compact")):
            return result
        self.state.messages = list(preview["kept_messages"])
        self.state.context_summary = str(preview["merged_summary"])
        event = RuntimeEvent(
            kind="context_compacted",
            message=(
                f"compacted {preview['compacted_count']} messages into context_summary; "
                f"kept last {preview['kept_count']} messages"
                + (
                    f"; instruction={preview['instructions']}"
                    if preview.get("instructions")
                    else ""
                )
            ),
        )
        if sink is not None:
            sink(event)
        else:
            self._emit_runtime_event(event)
        if persist:
            self.persist_state()
        result["applied"] = True
        return result

    def _describe_compact_status(self, preview: dict[str, Any]) -> str:
        history_state = self._history_state_payload(
            message_count=preview["message_count"],
            context_summary=self.state.context_summary,
        )
        lines = [
            "history compaction status:",
            "compaction mode: local estimated summary",
            *self._history_state_lines(history_state),
            f"message count: {preview['message_count']}",
            f"keep last threshold: {preview['keep_last']}",
            f"would compact: {'yes' if preview['would_compact'] else 'no'}",
            f"messages to compact: {preview['compacted_count']}",
            f"messages to keep: {preview['kept_count']}",
        ]
        if bool(preview["would_compact"]):
            lines.append(
                f"compacted context summary chars after compact: {preview['merged_summary_chars']}"
            )
        lines.extend(self._compact_instruction_lines(preview))
        lines.extend(
            [
                "next actions:",
                "- /compact",
                "- /compact preview",
                "- /history messages",
            ]
        )
        return "\n".join(lines)

    def _describe_compact_preview(self, preview: dict[str, Any]) -> str:
        history_state = self._history_state_payload(
            message_count=preview["message_count"],
            context_summary=self.state.context_summary,
        )
        lines = [
            "history compaction preview:",
            "compaction mode: local estimated summary",
            *self._history_state_lines(history_state, include_summary_preview=True),
            f"message count: {preview['message_count']}",
            f"keep last threshold: {preview['keep_last']}",
            f"would compact: {'yes' if preview['would_compact'] else 'no'}",
            f"messages to compact: {preview['compacted_count']}",
            f"messages to keep: {preview['kept_count']}",
            f"compacted context summary chars after compact: {preview['merged_summary_chars']}",
        ]
        lines.extend(self._compact_instruction_lines(preview))
        compacted_lines = list(preview["compacted_lines"])
        if compacted_lines:
            lines.append("compacted message preview:")
            lines.extend(compacted_lines[:8])
            if len(compacted_lines) > 8:
                lines.append(f"... {len(compacted_lines) - 8} more line(s)")
        lines.extend(
            [
                "next actions:",
                "- /compact",
                "- /compact status",
                "- /history messages",
            ]
        )
        return "\n".join(lines)

    def _compact_instruction_lines(self, payload: dict[str, Any]) -> list[str]:
        instructions = str(payload.get("instructions") or "").strip()
        if not instructions:
            return []
        return [f"compact instruction: {instructions}"]

    def _history_section_lines(self, *, limit: int = 12) -> dict[str, list[str]]:
        audit_lines = self._recent_workspace_audit_history_lines()
        task_history_lines = self._recent_task_activity_lines()
        visible_messages = self.state.messages[-limit:]
        history_state = self._history_state_payload(
            message_count=len(self.state.messages),
            context_summary=self.state.context_summary,
        )
        message_lines: list[str] = []
        message_lines.extend(self._history_state_lines(history_state, include_summary_preview=True))
        for index, message in enumerate(visible_messages, start=1):
            role = message.get("role", "unknown")
            summary = self._summarize_message(message)
            message_lines.append(f"{index}. {role}: {summary}")
        if not visible_messages:
            if history_state["history_cleared"]:
                message_lines.append("History has been cleared.")
            else:
                message_lines.append("No active messages in current history.")
        change_lines: list[str] = []
        undo_text = self.describe_change_stack(limit=min(limit, 5))
        if undo_text != "No recorded workspace changes.":
            change_lines.extend(["recent changes:", *undo_text.splitlines()])
        working_set_text = self.describe_working_set(limit=min(limit, 5))
        if working_set_text:
            if change_lines:
                change_lines.append("")
            change_lines.extend(working_set_text.splitlines())
        focused_lines = self._history_focus_summary_lines()
        if focused_lines:
            if change_lines:
                change_lines.append("")
            change_lines.extend(focused_lines)
        sections: dict[str, list[str]] = {}
        if message_lines:
            sections["messages"] = ["recent messages:", *message_lines]
        if audit_lines:
            sections["workspace"] = ["workspace audit:", *audit_lines]
        if task_history_lines:
            sections["tasks"] = ["recent task activity:", *task_history_lines]
        if change_lines:
            sections["changes"] = change_lines
        return sections

    def _history_focus_summary_lines(self) -> list[str]:
        payload = self._current_context_focus_payload()
        _files, _bounded_index, focused_item = self._file_context_items_and_index(payload)
        if focused_item is None:
            return []
        path = str(focused_item.get("path") or "").strip()
        if not path:
            return []
        source = str(focused_item.get("source") or "").strip()
        action_groups = self._file_context_item_action_groups(
            focused_item,
            stay_on_surface_actions=[
                "/history changes",
                "/files focused",
                "/diff focused",
                "/status workflow",
            ],
        )
        return self.render_surface_metadata_section(
            "focused file context:",
            summary_fields=[
                ("focused file", path),
                ("source", source or None),
                ("diff hunks", self._file_context_diff_hunk_count(focused_item)),
            ],
            action_groups=action_groups,
            action_order=("go_to_change", "go_to_task", "go_to_plan", "stay_on_surface"),
        )

    def describe_recent_changes(self, limit: int = 5) -> str:
        changes = self.state.recent_change_sets[-limit:]
        redos = self.state.undone_change_sets[-limit:]
        if not changes and not redos:
            return "No recorded workspace changes."
        lines = []
        if changes:
            lines.extend(
                self._render_change_stack_lines(
                    list(reversed(changes)),
                    title="Undo stack:",
                    redo=False,
                )
            )
        if redos:
            if lines:
                lines.append("")
            lines.extend(
                self._render_change_stack_lines(
                    list(reversed(redos)),
                    title="Redo stack:",
                    include_file_preview=False,
                    redo=True,
                )
            )
        working_set_lines = self._render_file_context_lines(
            self.working_set_payload(limit=limit),
            title="Working set",
        )
        if working_set_lines:
            if lines:
                lines.append("")
            lines.extend(working_set_lines)
        return "\n".join(lines)

    def _render_change_stack_lines(
        self,
        changes: list[WorkspaceChangeSet],
        *,
        title: str,
        include_file_preview: bool = True,
        redo: bool = False,
    ) -> list[str]:
        lines = [title]
        for index, change in enumerate(changes, start=1):
            visible_files = self._visible_change_files(change)
            lines.append(
                f"{index}. {change.change_id}  tool={change.tool_name}  files={len(visible_files)}  "
                f"created={change.created_at}  kind={change.change_kind}  "
                f"undoable={'yes' if change.undoable else 'no'}"
            )
            lines.append(f"   summary: {change.summary}")
            if include_file_preview:
                for file_change in visible_files[:3]:
                    lines.append("   - " + self._render_file_change_summary(file_change))
                if len(visible_files) > 3:
                    lines.append(f"   - ... {len(visible_files) - 3} more file(s)")
            lines.extend(
                "   " + line
                for line in self._render_action_group_lines(
                    self._change_stack_entry_action_groups(
                        selected_index=index - 1,
                        limit=len(changes),
                        redo=redo,
                    ),
                    line_prefix="- ",
                    ordered_keys=("go_to_change", "go_to_task", "go_to_plan", "stay_on_surface"),
                )
            )
        return lines

    def describe_change_stack(self, *, redo: bool = False, limit: int = 5) -> str:
        stack = self._visible_change_stack(redo=redo, limit=limit)
        if not stack:
            return (
                "No undone workspace changes."
                if redo
                else "No recorded workspace changes."
            )
        lines = self._render_change_stack_lines(
            stack,
            title="Redo stack:" if redo else "Undo stack:",
            include_file_preview=not redo,
            redo=redo,
        )
        return "\n".join(lines)

    def _visible_change_stack(
        self,
        *,
        redo: bool = False,
        limit: int | None = None,
    ) -> list[WorkspaceChangeSet]:
        stack = self.state.undone_change_sets if redo else self.state.recent_change_sets
        if limit is None:
            return list(reversed(stack))
        return list(reversed(stack[-limit:]))

    def resolve_change_stack_index(
        self,
        selector: str,
        *,
        redo: bool = False,
        limit: int | None = None,
    ) -> int | None:
        raw = selector.strip()
        if not raw:
            return None
        visible = self._visible_change_stack(redo=redo, limit=limit)
        if not visible:
            return None
        prefer_index = raw.isdigit() and len(raw) <= 2
        if prefer_index:
            selected_index = int(raw)
            if 1 <= selected_index <= len(visible):
                return selected_index - 1
            return None
        matches = [
            index
            for index, change in enumerate(visible)
            if change.change_id.startswith(raw)
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def describe_working_set(self, limit: int = 5) -> str:
        lines = self._render_file_context_lines(
            self.working_set_payload(limit=limit),
            title="Working set",
        )
        if not lines:
            return "\n".join(
                [
                    "Working set:",
                    "- file_count: 0",
                ]
            )
        return "\n".join(lines)

    def recent_change_entries(self, limit: int = 5) -> list[str]:
        changes = list(reversed(self.state.recent_change_sets[-limit:]))
        return [self._render_change_entry(change) for change in changes]

    def recent_redo_entries(self, limit: int = 5) -> list[str]:
        changes = list(reversed(self.state.undone_change_sets[-limit:]))
        return [self._render_change_entry(change) for change in changes]

    def selected_change_file_count(self, *, index: int = 0, limit: int = 5, redo: bool = False) -> int:
        visible = self._visible_change_stack(redo=redo, limit=limit)
        if not visible:
            return 0
        selected = visible[max(0, min(index, len(visible) - 1))]
        return len(self._visible_change_files(selected))

    def selected_change_detail(
        self,
        *,
        index: int = 0,
        file_index: int = 0,
        limit: int = 5,
        redo: bool = False,
        preserve_current_focus: bool = False,
    ) -> str:
        visible = self._visible_change_stack(redo=redo, limit=limit)
        if not visible:
            return "No selected change."
        selected = visible[max(0, min(index, len(visible) - 1))]
        counts = self._count_change_actions(selected)
        visible_files = self._visible_change_files(selected)
        resolved_file_index = file_index
        if visible_files and preserve_current_focus:
            resolved_file_index = self.preferred_selected_change_file_index(
                index=index,
                redo=redo,
                limit=limit,
                fallback=file_index,
            )
        clamped_file_index = max(0, min(resolved_file_index, len(visible_files) - 1)) if visible_files else 0
        lines = [
            f"change: {selected.change_id}",
            f"tool: {selected.tool_name}",
            f"kind: {selected.change_kind}",
            f"undoable: {'yes' if selected.undoable else 'no'}",
            f"files: {len(visible_files)}",
            (
                "actions: "
                f"create={counts['create']} "
                f"update={counts['update']} "
                f"delete={counts['delete']} "
                f"move={counts['move']}"
            ),
            f"summary: {selected.summary}",
        ]
        if visible_files:
            lines.append("")
            lines.append("")
            lines.append("Files")
            for current_index, file_change in enumerate(visible_files[:8], start=1):
                marker = ">" if current_index - 1 == clamped_file_index else " "
                lines.append(f"{marker} {current_index}. {self._describe_file_change(file_change)}")
            if len(visible_files) > 8:
                lines.append(f"  ... {len(visible_files) - 8} more file(s)")
            focused = visible_files[clamped_file_index]
            lines.append("")
            lines.append(f"Focused file ({clamped_file_index + 1}/{len(visible_files)})")
            lines.append(self._render_file_change_detail(focused))
            metadata = self.selected_change_detail_metadata(
                index=index,
                file_index=clamped_file_index,
                limit=limit,
                redo=redo,
                preserve_current_focus=False,
            )
            context_lines = self._render_file_context_lines(
                metadata,
                title="Focused file context",
            )
            if context_lines:
                lines.append("")
                lines.extend(context_lines)
            lines.append("")
            lines.extend(
                self._render_selected_change_next_action_lines(
                    selected,
                    focused_path=focused.path,
                    file_index=clamped_file_index,
                    redo=redo,
                )
            )
        return "\n".join(lines)

    def _coerce_target_dict(self, target: EditorTarget | dict[str, Any] | None) -> dict[str, Any] | None:
        if isinstance(target, EditorTarget):
            return target.to_dict()
        if isinstance(target, dict):
            return dict(target)
        return None

    def _format_target_summary(self, target: EditorTarget | dict[str, Any] | None) -> str:
        payload = self._coerce_target_dict(target)
        if not payload:
            return "none"
        action = str(payload.get("action") or "open_file").strip()
        path = str(payload.get("path") or "").strip()
        line = int(payload.get("line") or 1)
        label = str(payload.get("label") or "").strip()
        summary = f"{action} {path}:{line}" if path else action
        if label:
            summary = f"{summary} ({label})"
        return summary

    def _normalize_explicit_context_path(self, raw_path: str) -> Path:
        candidate = str(raw_path or "").strip()
        if not candidate:
            raise ValueError("path is required")
        resolved = resolve_workspace_path(self.config.cwd, candidate)
        return resolved.resolve()

    def _explicit_context_entry_is_resolved(self, entry: ExplicitContextEntry) -> bool:
        try:
            return Path(entry.resolved_path).exists()
        except OSError:
            return False

    def _explicit_context_entry_payload(
        self,
        entry: ExplicitContextEntry,
        *,
        known_items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        resolved_now = self._explicit_context_entry_is_resolved(entry)
        contributed_paths = self._explicit_context_entry_contributed_paths(
            entry,
            known_items=known_items,
        )
        return {
            "raw_path": entry.raw_path,
            "resolved_path": entry.resolved_path,
            "kind": entry.kind,
            "added_at": entry.added_at,
            "resolved": resolved_now,
            "contributed_paths": contributed_paths,
            "contributes_files": bool(contributed_paths),
        }

    def _known_working_set_source_items(self, *, limit: int = 50) -> list[dict[str, Any]]:
        items = self._active_task_file_context_items()
        artifact = self.active_planning_artifact()
        if artifact is not None:
            items.extend(self._active_plan_file_context_items(artifact))
        items.extend(self._recent_change_file_context_items(limit=limit))
        items.extend(self._symbol_surface_file_context_items())
        return items

    def _path_is_within_directory(self, *, path: str, directory: str) -> bool:
        try:
            Path(path).resolve().relative_to(Path(directory).resolve())
        except (OSError, ValueError):
            return False
        return True

    def _explicit_context_entry_contributed_paths(
        self,
        entry: ExplicitContextEntry,
        *,
        known_items: list[dict[str, Any]] | None = None,
    ) -> list[str]:
        if not self._explicit_context_entry_is_resolved(entry):
            return []
        if entry.kind == "file":
            item = self._build_file_context_item(
                path=entry.resolved_path,
                source="explicit_context",
                summary=f"explicit context path:{entry.raw_path}",
                action_kind="explicit_context",
                allow_external=True,
                scope_reasons=["explicit context path"],
            )
            return [str(item.get("path") or entry.resolved_path)] if item is not None else [entry.resolved_path]
        visible_paths: list[str] = []
        for item in known_items or []:
            resolved_path = str(item.get("resolved_path") or "").strip()
            path = str(item.get("path") or "").strip()
            if not resolved_path or not path:
                continue
            if self._path_is_within_directory(path=resolved_path, directory=entry.resolved_path):
                visible_paths.append(path)
        deduped: list[str] = []
        seen: set[str] = set()
        for path in visible_paths:
            if path in seen:
                continue
            seen.add(path)
            deduped.append(path)
        return deduped

    def _explicit_context_entries_payloads(self) -> list[dict[str, Any]]:
        known_items = self._dedupe_file_context_items(
            self._known_working_set_source_items(limit=50),
            limit=200,
        )
        return [
            self._explicit_context_entry_payload(entry, known_items=known_items)
            for entry in self.state.explicit_context_entries
        ]

    def _explicit_context_summary_counts(
        self,
        *,
        entries: list[dict[str, Any]] | None = None,
        files: list[dict[str, Any]] | None = None,
        total_file_count: int | None = None,
    ) -> dict[str, int]:
        entry_payloads = entries if entries is not None else self._explicit_context_entries_payloads()
        file_items = files if files is not None else []
        explicit_file_count = sum(
            1
            for item in file_items
            if isinstance(item, dict) and "explicit context path" in self._file_context_scope_reasons(item)
        )
        automatic_file_count = 0
        if total_file_count is not None:
            automatic_file_count = max(0, total_file_count - explicit_file_count)
        return {
            "entry_count": len(entry_payloads),
            "unresolved_entry_count": sum(
                1 for entry in entry_payloads if not bool(entry.get("resolved", False))
            ),
            "explicit_file_count": explicit_file_count,
            "automatic_file_count": automatic_file_count,
        }

    def _explicit_context_file_context_items(self, *, limit: int = 50) -> list[dict[str, Any]]:
        known_items = self._dedupe_file_context_items(
            self._known_working_set_source_items(limit=max(limit, 20)),
            limit=max(limit * 4, 50),
        )
        items: list[dict[str, Any]] = []
        for entry in self.state.explicit_context_entries:
            if not self._explicit_context_entry_is_resolved(entry):
                continue
            if entry.kind == "file":
                item = self._build_file_context_item(
                    path=entry.resolved_path,
                    source="explicit_context",
                    summary=f"explicit context path:{entry.raw_path}",
                    action_kind="explicit_context",
                    allow_external=True,
                    scope_reasons=["explicit context path"],
                )
                if item is not None:
                    items.append(item)
                continue
            for known_item in known_items:
                resolved_path = str(known_item.get("resolved_path") or "").strip()
                if not resolved_path or not self._path_is_within_directory(
                    path=resolved_path,
                    directory=entry.resolved_path,
                ):
                    continue
                item = self._build_file_context_item(
                    path=resolved_path,
                    source="explicit_context",
                    summary=f"explicit context path:{entry.raw_path}",
                    action_kind="explicit_context",
                    target=known_item.get("target"),
                    allow_external=True,
                    scope_reasons=["explicit context path"],
                )
                if item is not None:
                    items.append(item)
        return items

    def add_explicit_context_path(self, raw_path: str) -> str:
        candidate = str(raw_path or "").strip()
        if not candidate:
            return "Usage: /add-dir <path>|list|clear|remove <n>"
        resolved = self._normalize_explicit_context_path(candidate)
        if not resolved.exists():
            return f"Explicit context path does not exist: {candidate}"
        kind = "directory" if resolved.is_dir() else "file"
        normalized = str(resolved)
        existing = next(
            (entry for entry in self.state.explicit_context_entries if entry.resolved_path == normalized),
            None,
        )
        if existing is None:
            self.state.explicit_context_entries.append(
                ExplicitContextEntry(
                    raw_path=candidate,
                    resolved_path=normalized,
                    kind=kind,
                    resolved=True,
                )
            )
            self.persist_state()
            status = "added"
        else:
            existing.resolved = True
            status = "already present"
            self.persist_state()
        return "\n".join(
            [
                "explicit context path:",
                f"status: {status}",
                f"kind: {kind}",
                f"resolved path: {normalized}",
                "next actions:",
                "- /add-dir list",
                "- /files explicit",
                "- /files context",
            ]
        )

    def describe_explicit_context_paths(self) -> str:
        entries = self._explicit_context_entries_payloads()
        working_set = self.working_set_payload(limit=20)
        file_items = [item for item in working_set.get("file_context_files", []) if isinstance(item, dict)]
        counts = self._explicit_context_summary_counts(
            entries=entries,
            files=file_items,
            total_file_count=len(file_items),
        )
        lines = [
            "explicit context entries:",
            f"entry_count: {counts['entry_count']}",
            f"unresolved entry count: {counts['unresolved_entry_count']}",
            f"explicit-context-contributed files: {counts['explicit_file_count']}",
        ]
        if not entries:
            return "\n".join(lines + ["No explicit context entries."])
        for index, entry in enumerate(entries, start=1):
            contributes = list(entry["contributed_paths"])
            lines.append(
                f"{index}. {entry['resolved_path']}  kind={entry['kind']}  "
                f"resolved={'yes' if entry['resolved'] else 'no'}  "
                f"contributes_files={len(contributes)}"
            )
            lines.append(
                "   next_actions: "
                + "; ".join(
                    [
                        "go_to_context=/files explicit | /files context",
                        "stay_on_surface=/add-dir list | /files context",
                    ]
                )
            )
        return "\n".join(lines)

    def clear_explicit_context_paths(self) -> str:
        self.state.explicit_context_entries.clear()
        self.persist_state()
        return "Cleared explicit context entries for this session."

    def remove_explicit_context_path(self, index: int) -> str:
        if index < 0 or index >= len(self.state.explicit_context_entries):
            return "Usage: /add-dir <path>|list|clear|remove <n>"
        removed = self.state.explicit_context_entries.pop(index)
        self.persist_state()
        return f"Removed explicit context entry: {removed.resolved_path}"

    def _build_file_context_item(
        self,
        *,
        path: str,
        source: str,
        summary: str,
        action_kind: str = "",
        change_id: str | None = None,
        symbol: str | None = None,
        target: EditorTarget | dict[str, Any] | None = None,
        before_content: str | None = None,
        after_content: str | None = None,
        allow_external: bool = False,
        scope_reasons: list[str] | None = None,
    ) -> dict[str, Any] | None:
        raw_path = str(path or "").strip()
        if not raw_path:
            return None
        resolved = Path(raw_path)
        if not resolved.is_absolute():
            resolved = resolve_workspace_path(self.config.cwd, raw_path)
        resolved = resolved.resolve()
        try:
            relative_path = resolved.relative_to(self.config.cwd).as_posix()
            in_workspace = True
        except ValueError:
            if not allow_external:
                return None
            relative_path = resolved.as_posix()
            in_workspace = False
        navigation_target = self._coerce_target_dict(target)
        diff_payload: dict[str, Any] | None = None
        if in_workspace and before_content is not None and after_content is not None:
            diff_payload = self.build_diff_targets(
                relative_path,
                before=before_content,
                after=after_content,
            ).to_dict()
        if navigation_target is None and in_workspace:
            line = 1
            hunks = diff_payload.get("hunks", []) if isinstance(diff_payload, dict) else []
            if hunks:
                line = int(hunks[0].get("line") or 1)
            navigation_target = self.build_open_file_target(
                relative_path,
                line=line,
                label=summary,
            ).to_dict()
        diff_target_count = len(diff_payload.get("hunks", [])) if isinstance(diff_payload, dict) else 0
        normalized_scope_reasons = self._normalize_file_context_scope_reasons(scope_reasons)
        has_related_change = bool(change_id)
        has_diff_hunks = diff_target_count > 0
        return {
            "path": relative_path,
            "resolved_path": str(resolved),
            "source": source,
            "summary": summary,
            "action_kind": action_kind or "context",
            "change_id": change_id,
            "symbol": symbol,
            "target": navigation_target,
            "target_summary": self._format_target_summary(navigation_target),
            "diff_targets": diff_payload,
            "diff_target_count": diff_target_count,
            "scope_reasons": normalized_scope_reasons,
            "has_related_change": has_related_change,
            "has_diff_hunks": has_diff_hunks,
            "is_context_only": not has_related_change and not has_diff_hunks,
        }

    def _file_context_reason_priority(self, reason: str) -> int:
        priorities = {
            "active task": 0,
            "active plan": 1,
            "recent change": 2,
            "symbol navigation": 3,
            "explicit context path": 4,
            "fallback working set": 5,
        }
        return priorities.get(reason, len(priorities))

    def _normalize_file_context_scope_reasons(
        self,
        reasons: list[str] | tuple[str, ...] | None,
    ) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_reason in reasons or []:
            reason = str(raw_reason or "").strip()
            if not reason or reason in seen:
                continue
            seen.add(reason)
            normalized.append(reason)
        normalized.sort(key=lambda item: (self._file_context_reason_priority(item), item))
        return normalized

    def _merge_file_context_items(
        self,
        current: dict[str, Any],
        incoming: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(current)
        reasons = list(current.get("scope_reasons") or [])
        reasons.extend(item for item in (incoming.get("scope_reasons") or []) if isinstance(item, str))
        merged["scope_reasons"] = self._normalize_file_context_scope_reasons(reasons)
        if not merged.get("change_id") and incoming.get("change_id"):
            merged["change_id"] = incoming.get("change_id")
        if not merged.get("symbol") and incoming.get("symbol"):
            merged["symbol"] = incoming.get("symbol")
        if not merged.get("target") and incoming.get("target"):
            merged["target"] = incoming.get("target")
            merged["target_summary"] = incoming.get("target_summary")
        if not merged.get("diff_targets") and incoming.get("diff_targets"):
            merged["diff_targets"] = incoming.get("diff_targets")
        current_diff_count = int(merged.get("diff_target_count") or 0)
        incoming_diff_count = int(incoming.get("diff_target_count") or 0)
        if incoming_diff_count > current_diff_count:
            merged["diff_target_count"] = incoming_diff_count
        merged["has_related_change"] = bool(merged.get("change_id"))
        merged["has_diff_hunks"] = int(merged.get("diff_target_count") or 0) > 0
        merged["is_context_only"] = not merged["has_related_change"] and not merged["has_diff_hunks"]
        return merged

    def _recent_change_file_context_items(
        self,
        limit: int = 5,
        *,
        source: str = "recent_change",
        scope_reasons: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        reasons = scope_reasons or ["recent change"]
        for change in reversed(self.state.recent_change_sets[-limit:]):
            for file_change in self._visible_change_files(change):
                item = self._build_file_context_item(
                    path=file_change.path,
                    source=source,
                    summary=self._describe_file_change(file_change),
                    action_kind=file_change.action_kind,
                    change_id=change.change_id[:8],
                    before_content=file_change.before_content,
                    after_content=file_change.after_content or "",
                    scope_reasons=reasons,
                )
                if item is not None:
                    items.append(item)
        return items

    def _symbol_surface_file_context_items(
        self,
        *,
        source: str = "symbol_surface",
        scope_reasons: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        payload = self.current_symbol_surface_payload()
        if not isinstance(payload, dict) or not payload:
            return []
        symbol = str(payload.get("selected_symbol") or payload.get("symbol") or "").strip() or None
        items: list[dict[str, Any]] = []
        reasons = scope_reasons or ["symbol navigation"]
        candidates: list[tuple[str, dict[str, Any] | None]] = [
            (source, self._coerce_target_dict(payload.get("selected_navigation_target"))),
            (source, self._coerce_target_dict(payload.get("selected_definition"))),
            (source, self._coerce_target_dict(payload.get("selected_reference"))),
            (source, self._coerce_target_dict(payload.get("navigation_target"))),
        ]
        for source, target in candidates:
            if not isinstance(target, dict):
                continue
            path = str(target.get("path") or "").strip()
            if not path:
                continue
            label = str(target.get("label") or "").strip()
            summary = label or f"{source} target"
            item = self._build_file_context_item(
                path=path,
                source=source,
                summary=summary,
                action_kind=str(target.get("action") or "open_file"),
                symbol=symbol,
                target=target,
                scope_reasons=reasons,
            )
            if item is not None:
                items.append(item)
        return items

    def _workspace_metadata_file_context_items(
        self,
        metadata: dict[str, Any],
        *,
        source: str = "workspace_task",
        scope_reasons: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        reasons = scope_reasons or ["active task"]
        for key in ("workspace_planned_paths", "workspace_applied_paths"):
            values = metadata.get(key) or []
            if not isinstance(values, list):
                continue
            for raw_path in values:
                item = self._build_file_context_item(
                    path=str(raw_path),
                    source=source,
                    summary=f"{key}:{raw_path}",
                    action_kind="workspace_path",
                    allow_external=True,
                    scope_reasons=reasons,
                )
                if item is not None:
                    items.append(item)
        return items

    def _active_task_file_context_items(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for task in self.task_manager.list():
            if str(getattr(task, "status", "") or "") != "running":
                continue
            metadata = getattr(task, "metadata", None)
            if not isinstance(metadata, dict) or not metadata:
                continue
            items.extend(
                self._workspace_metadata_file_context_items(
                    metadata,
                    source="active_task",
                    scope_reasons=["active task"],
                )
            )
        return items

    def _active_plan_file_context_items(self, artifact: PlanningArtifact) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for task_id in artifact.task_ids:
            task = self.resolve_task(task_id)
            if task is None:
                continue
            metadata = getattr(task, "metadata", None)
            if not isinstance(metadata, dict) or not metadata:
                continue
            items.extend(
                self._workspace_metadata_file_context_items(
                    metadata,
                    source="active_plan",
                    scope_reasons=["active plan"],
                )
            )
        return items

    def _dedupe_file_context_items(
        self,
        items: list[dict[str, Any]],
        *,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: dict[str, int] = {}
        for item in items:
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            existing_index = seen.get(path)
            if existing_index is not None:
                deduped[existing_index] = self._merge_file_context_items(deduped[existing_index], item)
                continue
            if len(deduped) >= limit:
                continue
            seen[path] = len(deduped)
            deduped.append(item)
        return deduped

    def _build_file_context_payload(
        self,
        items: list[dict[str, Any]],
        *,
        limit: int = 5,
        scope: str,
    ) -> dict[str, Any]:
        files = self._dedupe_file_context_items(items, limit=limit)
        primary = files[0] if files else None
        return {
            "file_context_scope": scope,
            "file_context_file_count": len(files),
            "file_context_sources": [str(item.get("source") or "") for item in files],
            "file_context_files": files,
            "file_context_primary_path": primary.get("path") if primary else None,
            "file_context_primary_target": primary.get("target") if primary else None,
            "file_context_primary_diff_targets": primary.get("diff_targets") if primary else None,
        }

    def _file_context_payload_from_files(
        self,
        files: list[dict[str, Any]],
        *,
        scope: str,
    ) -> dict[str, Any]:
        primary = files[0] if files else None
        return {
            "file_context_scope": scope,
            "file_context_file_count": len(files),
            "file_context_sources": [str(item.get("source") or "") for item in files],
            "file_context_files": files,
            "file_context_primary_path": primary.get("path") if primary else None,
            "file_context_primary_target": primary.get("target") if primary else None,
            "file_context_primary_diff_targets": primary.get("diff_targets") if primary else None,
        }

    def remember_selected_change_context_focus(
        self,
        *,
        index: int = 0,
        file_index: int = 0,
        redo: bool = False,
        preserve_current_focus: bool = False,
    ) -> dict[str, Any] | None:
        resolved_index = file_index
        if preserve_current_focus:
            resolved_index = self.preferred_selected_change_file_index(
                index=index,
                redo=redo,
                limit=10,
                fallback=file_index,
            )
        payload = self.selected_change_detail_metadata(
            index=index,
            file_index=resolved_index,
            redo=redo,
            limit=10,
            preserve_current_focus=False,
        )
        self._current_change_focus_payload = self._reordered_file_context_payload(payload, selected_index=0)
        return self._current_change_focus_payload

    def remember_task_context_focus(
        self,
        identifier: str,
        *,
        file_index: int = 0,
        preserve_current_focus: bool = False,
    ) -> dict[str, Any] | None:
        resolved_index = (
            self.preferred_task_file_index(identifier, fallback=file_index)
            if preserve_current_focus
            else file_index
        )
        payload = self.task_file_context_payload(identifier, limit=20)
        self._current_task_focus_payload = self._reordered_file_context_payload(
            payload,
            selected_index=resolved_index,
        )
        return self._current_task_focus_payload

    def remember_plan_context_focus_payload(
        self,
        payload: dict[str, Any] | None,
        *,
        file_index: int = 0,
        preserve_current_focus: bool = False,
    ) -> dict[str, Any] | None:
        resolved_index = (
            self._preferred_file_context_index(payload, fallback=file_index)
            if preserve_current_focus
            else file_index
        )
        self._current_plan_focus_payload = self._reordered_file_context_payload(
            payload,
            selected_index=resolved_index,
        )
        return self._current_plan_focus_payload

    def _current_context_focus_payload(self) -> dict[str, Any] | None:
        for payload in (
            self._current_change_focus_payload,
            self._current_task_focus_payload,
            self._current_plan_focus_payload,
        ):
            files, _index, focused_item = self._file_context_items_and_index(payload)
            if files and focused_item is not None:
                return payload
        payload = self.working_set_payload(limit=20)
        files, _index, focused_item = self._file_context_items_and_index(payload)
        if files and focused_item is not None:
            return payload
        return None

    def _filtered_working_set_payload(self, *, reason: str) -> dict[str, Any]:
        payload = self.working_set_payload(limit=20)
        files = [item for item in payload.get("file_context_files", []) if isinstance(item, dict)]
        filtered = [item for item in files if reason in self._file_context_scope_reasons(item)]
        return self._file_context_payload_from_files(filtered, scope="session")

    def _render_file_context_lines(
        self,
        payload: dict[str, Any] | None,
        *,
        title: str,
    ) -> list[str]:
        if not isinstance(payload, dict):
            return []
        files = [item for item in payload.get("file_context_files", []) if isinstance(item, dict)]
        if not files:
            return []
        lines = [title + ":"]
        lines.append(f"- file_count: {len(files)}")
        sources = [str(item.get("source") or "").strip() for item in files if str(item.get("source") or "").strip()]
        if sources:
            lines.append("- sources: " + ", ".join(sources))
        primary_path = str(payload.get("file_context_primary_path") or "").strip()
        if primary_path:
            lines.append(f"- primary_path: {primary_path}")
        primary_target = payload.get("file_context_primary_target")
        if primary_target:
            lines.append("- primary_target: " + self._format_target_summary(primary_target))
        lines.append("- files:")
        for index, item in enumerate(files, start=1):
            parts = [f"{index}. {item['path']}"]
            source = str(item.get("source") or "").strip()
            if source:
                parts.append(f"[{source}]")
            reasons = [str(reason) for reason in item.get("scope_reasons", []) if str(reason).strip()]
            if reasons:
                parts.append("in_scope_because=" + ", ".join(reasons))
            if item.get("change_id"):
                parts.append(f"change={item['change_id']}")
            if item.get("symbol"):
                parts.append(f"symbol={item['symbol']}")
            parts.append(f"target={item.get('target_summary') or 'none'}")
            if int(item.get("diff_target_count") or 0) > 0:
                parts.append(f"diff_hunks={int(item['diff_target_count'])}")
            if bool(item.get("is_context_only")):
                parts.append("context_only=yes")
            summary = str(item.get("summary") or "").strip()
            if summary:
                parts.append(f"summary={summary}")
            lines.append("  " + "  ".join(parts))
        return lines

    def working_set_payload(self, limit: int = 5) -> dict[str, Any]:
        items = self._known_working_set_source_items(limit=max(limit, 20))
        items.extend(self._explicit_context_file_context_items(limit=max(limit, 20)))
        return self._build_file_context_payload(items, limit=limit, scope="session")

    def task_file_context_payload(self, identifier: str, limit: int = 5) -> dict[str, Any] | None:
        task = self.resolve_task(identifier)
        if task is None:
            checklist_task = self.resolve_checklist_task(identifier)
            if checklist_task is None:
                return None
            return self._build_file_context_payload(
                self._recent_change_file_context_items(limit=limit),
                limit=limit,
                scope="checklist_task",
            )
        items = self._workspace_metadata_file_context_items(task.metadata or {})
        items.extend(self._recent_change_file_context_items(limit=limit))
        items.extend(self._symbol_surface_file_context_items())
        return self._build_file_context_payload(items, limit=limit, scope="task")

    def active_plan_file_context_payload(
        self,
        identifier: str | None = None,
        limit: int = 5,
    ) -> dict[str, Any] | None:
        artifact = self.resolve_planning_artifact(identifier) if identifier else self.active_planning_artifact()
        if artifact is None:
            return None
        items = self._active_plan_file_context_items(artifact)
        items.extend(self._recent_change_file_context_items(limit=limit))
        items.extend(self._symbol_surface_file_context_items())
        payload = self._build_file_context_payload(items, limit=limit, scope="active_plan")
        payload["planning_artifact_id"] = artifact.artifact_id
        payload["planning_goal"] = artifact.goal
        payload["planning_task_count"] = len(artifact.task_ids)
        return payload

    def selected_change_detail_metadata(
        self,
        *,
        index: int = 0,
        file_index: int = 0,
        limit: int = 5,
        redo: bool = False,
        preserve_current_focus: bool = False,
    ) -> dict[str, Any]:
        resolved = self.resolve_selected_change_file_context(
            index=index,
            file_index=file_index,
            limit=limit,
            redo=redo,
            preserve_current_focus=preserve_current_focus,
        )
        payload = dict(resolved["reordered_payload"])
        visible = self._visible_change_stack(redo=redo, limit=limit)
        if not visible:
            return payload
        selected = visible[max(0, min(index, len(visible) - 1))]
        payload["selected_change_id"] = selected.change_id
        payload["selected_change_tool"] = selected.tool_name
        return payload

    def _selected_change_file_context_payload(
        self,
        *,
        index: int = 0,
        limit: int = 5,
        redo: bool = False,
    ) -> dict[str, Any]:
        visible = self._visible_change_stack(redo=redo, limit=limit)
        if not visible:
            return self._build_file_context_payload([], scope="change_detail")
        selected = visible[max(0, min(index, len(visible) - 1))]
        items: list[dict[str, Any]] = []
        for file_change in self._visible_change_files(selected):
            item = self._build_file_context_item(
                path=file_change.path,
                source="selected_change",
                summary=self._describe_file_change(file_change),
                action_kind=file_change.action_kind,
                change_id=selected.change_id[:8],
                before_content=file_change.before_content,
                after_content=file_change.after_content or "",
                scope_reasons=["recent change"],
            )
            if item is not None:
                items.append(item)
        payload = self._build_file_context_payload(items, limit=max(1, len(items)), scope="change_detail")
        payload["selected_change_id"] = selected.change_id
        payload["selected_change_tool"] = selected.tool_name
        return payload

    def _file_context_secondary_target(
        self,
        target_source: dict[str, Any],
    ) -> dict[str, Any] | None:
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

    def _file_context_scope_reasons(self, item: dict[str, Any]) -> list[str]:
        return [str(reason) for reason in item.get("scope_reasons", []) if str(reason).strip()]

    def _file_context_diff_hunk_count(self, target_source: dict[str, Any]) -> int:
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
            return 1
        if isinstance(diff_targets, list):
            return len([item for item in diff_targets if isinstance(item, dict)])
        return 0

    def _file_context_is_context_only(self, target_source: dict[str, Any]) -> bool:
        explicit = target_source.get("is_context_only")
        if explicit not in (None, ""):
            return bool(explicit)
        return not bool(target_source.get("change_id")) and self._file_context_diff_hunk_count(target_source) == 0

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
        primary_target: dict[str, Any] | None,
        secondary_target: dict[str, Any] | None,
        *,
        prefix: str = "- ",
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
        primary_summary = self._format_target_summary(primary_target) if has_primary else "none"
        if has_secondary:
            secondary_summary = self._format_target_summary(secondary_target)
        elif has_primary:
            secondary_summary = primary_summary + " (fallback)"
        else:
            secondary_summary = "none"
        lines.append(f"{prefix}navigation_f9: {primary_summary if has_primary else secondary_summary}")
        lines.append(f"{prefix}navigation_f10: {secondary_summary}")
        return lines

    def _file_context_items_and_index(
        self,
        payload: dict[str, Any] | None,
        *,
        selected_index: int = 0,
    ) -> tuple[list[dict[str, Any]], int, dict[str, Any] | None]:
        if not isinstance(payload, dict):
            return [], 0, None
        files = [item for item in payload.get("file_context_files", []) if isinstance(item, dict)]
        if not files:
            return [], 0, None
        bounded_index = max(0, min(len(files) - 1, selected_index))
        return files, bounded_index, files[bounded_index]

    def _reordered_file_context_payload(
        self,
        payload: dict[str, Any] | None,
        *,
        selected_index: int = 0,
    ) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        files, bounded_index, focused_item = self._file_context_items_and_index(
            payload,
            selected_index=selected_index,
        )
        if not files or focused_item is None:
            return payload
        reordered_files = [focused_item, *files[:bounded_index], *files[bounded_index + 1 :]]
        reordered_payload = dict(payload)
        reordered_payload["file_context_files"] = reordered_files
        reordered_payload["file_context_primary_path"] = focused_item.get("path")
        reordered_payload["file_context_primary_target"] = focused_item.get("target")
        reordered_payload["file_context_primary_diff_targets"] = focused_item.get("diff_targets")
        reordered_payload["file_context_sources"] = [
            str(item.get("source") or "")
            for item in reordered_files
        ]
        return reordered_payload

    def _reorder_payload_to_current_focus(
        self,
        payload: dict[str, Any] | None,
        *,
        required_reason: str | None = None,
    ) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return payload
        focused_payload = self._current_context_focus_payload()
        _focused_files, _focused_index, focused_item = self._file_context_items_and_index(focused_payload)
        if focused_item is None:
            return payload
        focused_path = str(focused_item.get("path") or "").strip()
        if not focused_path:
            return payload
        selected_index = self._find_matching_file_context_index(
            payload,
            path=focused_path,
            required_reason=required_reason,
        )
        if selected_index is None:
            selected_index = self._find_matching_file_context_index(payload, path=focused_path)
        if selected_index is None:
            return payload
        return self._reordered_file_context_payload(payload, selected_index=selected_index)

    def _file_context_classification_counts(self, files: list[dict[str, Any]]) -> dict[str, int]:
        counts = {
            "diff_backed": 0,
            "context_only": 0,
            "explicit": 0,
            "task": 0,
            "plan": 0,
            "change": 0,
        }
        for item in files:
            reasons = self._file_context_scope_reasons(item)
            if bool(item.get("has_diff_hunks")) or self._file_context_diff_hunk_count(item) > 0:
                counts["diff_backed"] += 1
            if self._file_context_is_context_only(item):
                counts["context_only"] += 1
            if "explicit context path" in reasons:
                counts["explicit"] += 1
            if "active task" in reasons:
                counts["task"] += 1
            if "active plan" in reasons:
                counts["plan"] += 1
            if "recent change" in reasons:
                counts["change"] += 1
        return counts

    def _render_file_context_mix_line(self, files: list[dict[str, Any]]) -> str:
        counts = self._file_context_classification_counts(files)
        return (
            "mix: "
            + f"diff_backed={counts['diff_backed']} "
            + f"context_only={counts['context_only']} "
            + f"explicit={counts['explicit']} "
            + f"task={counts['task']} "
            + f"plan={counts['plan']} "
            + f"change={counts['change']}"
        )

    def _resolve_change_navigation_for_file_context_item(
        self,
        item: dict[str, Any],
    ) -> dict[str, Any] | None:
        change_id = str(item.get("change_id") or "").strip()
        if not change_id:
            return None
        path = str(item.get("path") or "").strip()
        for redo in (False, True):
            stack = self._visible_change_stack(redo=redo, limit=None)
            for index, change in enumerate(stack, start=1):
                if not change.change_id.startswith(change_id):
                    continue
                command_prefix = "/changes show redo " if redo else "/changes show "
                result: dict[str, Any] = {
                    "redo": redo,
                    "change_id": change.change_id,
                    "change_selector": change.change_id[:8],
                    "change_command": f"{command_prefix}{change.change_id[:8]}",
                    "change_index": index,
                    "stack_label": "redo stack" if redo else "undo stack",
                }
                if path:
                    visible_files = self._visible_change_files(change)
                    for file_index, file_change in enumerate(visible_files, start=1):
                        if file_change.path == path:
                            result["file_index"] = file_index
                            result["change_file_command"] = (
                                f"{command_prefix}{change.change_id[:8]} file {file_index}"
                            )
                            break
                return result
        return {
            "redo": False,
            "change_id": change_id,
            "change_selector": change_id,
            "change_command": f"/changes show {change_id}",
            "stack_label": "change stack",
        }

    def render_focused_file_context_lines(
        self,
        payload: dict[str, Any] | None,
        *,
        selected_index: int = 0,
        title: str = "focused file",
        include_next_actions: bool = True,
    ) -> list[str]:
        return workflow_render_focused_file_context_lines(
            self,
            payload,
            selected_index=selected_index,
            title=title,
            include_next_actions=include_next_actions,
        )

    def _context_stay_on_surface_actions(self) -> list[str]:
        return [
            "/files focused",
            "/files changes",
            "/files tasks",
            "/files plan",
            "/files explicit",
            "/files auto",
            "/files context",
        ]

    def _files_stay_on_surface_actions(self) -> list[str]:
        return [
            "/files focused",
            "/files changes",
            "/files tasks",
            "/files plan",
            "/files explicit",
            "/files auto",
            "/files context",
        ]

    def _diff_stay_on_surface_actions(self) -> list[str]:
        return ["/diff focused", "/diff working-set", "/changes working-set"]

    def _file_context_item_action_groups(
        self,
        item: dict[str, Any],
        *,
        stay_on_surface_actions: list[str],
    ) -> dict[str, list[str]]:
        return workflow_build_file_context_item_action_groups(
            self,
            item,
            stay_on_surface_actions=stay_on_surface_actions,
        )

    def _file_context_context_actions(self, item: dict[str, Any]) -> list[str]:
        reasons = self._file_context_scope_reasons(item)
        actions = ["/files context"]
        if "explicit context path" in reasons:
            actions.append("/files explicit")
        else:
            actions.append("/files auto")
        return self._dedupe_action_commands(actions)

    def _context_item_action_groups(self, item: dict[str, Any]) -> dict[str, list[str]]:
        return self._file_context_item_action_groups(
            item,
            stay_on_surface_actions=self._context_stay_on_surface_actions(),
        )

    def _render_context_focused_lines(
        self,
        payload: dict[str, Any] | None,
        *,
        selected_index: int = 0,
        title: str = "focused file",
    ) -> list[str]:
        lines = self.render_focused_file_context_lines(
            payload,
            selected_index=selected_index,
            title=title,
            include_next_actions=False,
        )
        if not lines:
            return []
        _files, _bounded_index, focused_item = self._file_context_items_and_index(
            payload,
            selected_index=selected_index,
        )
        if focused_item is None:
            return lines
        lines.extend(
            self._render_file_context_action_group_lines(
                focused_item,
                stay_on_surface_actions=self._context_stay_on_surface_actions(),
                line_prefix="- ",
                ordered_keys=("go_to_change", "go_to_task", "go_to_plan", "stay_on_surface"),
            )
        )
        return lines

    def _render_context_inventory_text(
        self,
        payload: dict[str, Any] | None,
        *,
        filter_label: str,
    ) -> str:
        files = [item for item in (payload or {}).get("file_context_files", []) if isinstance(item, dict)]
        lines = [
            "working set files:",
            f"filter: {filter_label}",
            f"file_count: {len(files)}",
        ]
        if not files:
            return "\n".join(lines + ["No matching working-set files."])
        for index, item in enumerate(files, start=1):
            lines.append("")
            lines.append(f"{index}. {item['path']}")
            scope_reasons = self._file_context_scope_reasons(item)
            if scope_reasons:
                lines.append("- in scope because: " + ", ".join(scope_reasons))
            related_change = str(item.get("change_id") or "").strip()
            change_navigation = self._resolve_change_navigation_for_file_context_item(item)
            if related_change:
                if change_navigation is not None:
                    lines.append(f"- related change: {related_change} ({change_navigation['stack_label']})")
                else:
                    lines.append(f"- related change: {related_change}")
            else:
                lines.append("- related change: none")
            lines.append(f"- diff hunks: {self._file_context_diff_hunk_count(item)}")
            lines.append("- context-only: " + ("yes" if self._file_context_is_context_only(item) else "no"))
            primary_target = item.get("target")
            if isinstance(primary_target, dict):
                lines.append("- primary target: " + self._format_target_summary(primary_target))
            else:
                lines.append("- primary target: none")
            secondary_target = self._file_context_secondary_target(item)
            if isinstance(secondary_target, dict):
                lines.append("- secondary target: " + self._format_target_summary(secondary_target))
            else:
                lines.append("- secondary target: none")
            lines.extend(
                self._render_file_context_action_group_lines(
                    item,
                    stay_on_surface_actions=self._context_stay_on_surface_actions(),
                    line_prefix="- ",
                    ordered_keys=("go_to_change", "go_to_task", "go_to_plan", "stay_on_surface"),
                )
            )
        return "\n".join(lines)

    def _file_context_item_matches_path(
        self,
        item: dict[str, Any],
        *,
        path: str,
        required_reason: str | None = None,
    ) -> bool:
        return workflow_file_context_item_matches_path(
            self,
            item,
            path=path,
            required_reason=required_reason,
        )

    def _find_matching_file_context_index(
        self,
        payload: dict[str, Any] | None,
        *,
        path: str,
        required_reason: str | None = None,
    ) -> int | None:
        return workflow_find_matching_file_context_index(
            self,
            payload,
            path=path,
            required_reason=required_reason,
        )

    def _focused_path_from_payload(self, payload: dict[str, Any] | None) -> str | None:
        return workflow_focused_path_from_payload(self, payload)

    def _preferred_file_context_index(
        self,
        payload: dict[str, Any] | None,
        *,
        fallback: int = 0,
        preferred_payloads: tuple[dict[str, Any] | None, ...] | None = None,
        required_reason: str | None = None,
    ) -> int:
        return workflow_preferred_file_context_index(
            self,
            payload,
            fallback=fallback,
            preferred_payloads=preferred_payloads,
            required_reason=required_reason,
        )

    def resolve_file_context_selection(
        self,
        payload: dict[str, Any] | None,
        *,
        file_index: int = 0,
        preserve_current_focus: bool = False,
        preferred_payloads: tuple[dict[str, Any] | None, ...] | None = None,
        required_reason: str | None = None,
    ) -> dict[str, Any]:
        return workflow_resolve_file_context_selection(
            self,
            payload,
            file_index=file_index,
            preserve_current_focus=preserve_current_focus,
            preferred_payloads=preferred_payloads,
            required_reason=required_reason,
        )

    def resolve_task_file_context(
        self,
        identifier: str,
        *,
        file_index: int = 0,
        preserve_current_focus: bool = True,
        limit: int = 5,
    ) -> dict[str, Any]:
        return workflow_resolve_task_file_context(
            self,
            identifier,
            file_index=file_index,
            preserve_current_focus=preserve_current_focus,
            limit=limit,
        )

    def resolve_selected_change_file_context(
        self,
        *,
        index: int = 0,
        file_index: int = 0,
        limit: int = 10,
        redo: bool = False,
        preserve_current_focus: bool = False,
    ) -> dict[str, Any]:
        return workflow_resolve_selected_change_file_context(
            self,
            index=index,
            file_index=file_index,
            limit=limit,
            redo=redo,
            preserve_current_focus=preserve_current_focus,
        )

    def resolve_active_plan_file_context(
        self,
        *,
        identifier: str | None = None,
        file_index: int = 0,
        preserve_current_focus: bool = True,
    ) -> dict[str, Any]:
        return workflow_resolve_active_plan_file_context(
            self,
            identifier=identifier,
            file_index=file_index,
            preserve_current_focus=preserve_current_focus,
        )

    def resolve_active_plan_scout_file_context(
        self,
        *,
        selected_index: int = 0,
        file_index: int = 0,
        preserve_current_focus: bool = True,
    ) -> dict[str, Any]:
        return workflow_resolve_active_plan_scout_file_context(
            self,
            selected_index=selected_index,
            file_index=file_index,
            preserve_current_focus=preserve_current_focus,
        )

    def resolve_active_plan_execution_file_context(
        self,
        *,
        selected_index: int = 0,
        file_index: int = 0,
        preserve_current_focus: bool = True,
    ) -> dict[str, Any]:
        return workflow_resolve_active_plan_execution_file_context(
            self,
            selected_index=selected_index,
            file_index=file_index,
            preserve_current_focus=preserve_current_focus,
        )

    def preferred_task_file_index(self, identifier: str, *, fallback: int = 0) -> int:
        return workflow_preferred_task_file_index(
            self,
            identifier,
            fallback=fallback,
        )

    def preferred_selected_change_file_index(
        self,
        *,
        index: int = 0,
        redo: bool = False,
        limit: int = 10,
        fallback: int = 0,
    ) -> int:
        return workflow_preferred_selected_change_file_index(
            self,
            index=index,
            redo=redo,
            limit=limit,
            fallback=fallback,
        )

    def preferred_active_plan_file_index(
        self,
        *,
        identifier: str | None = None,
        fallback: int = 0,
    ) -> int:
        return workflow_preferred_active_plan_file_index(
            self,
            identifier=identifier,
            fallback=fallback,
        )

    def preferred_active_plan_scout_file_index(
        self,
        *,
        selected_index: int = 0,
        fallback: int = 0,
    ) -> int:
        return workflow_preferred_active_plan_scout_file_index(
            self,
            selected_index=selected_index,
            fallback=fallback,
        )

    def preferred_active_plan_execution_file_index(
        self,
        *,
        selected_index: int = 0,
        fallback: int = 0,
    ) -> int:
        return workflow_preferred_active_plan_execution_file_index(
            self,
            selected_index=selected_index,
            fallback=fallback,
        )

    def active_plan_scout_index_for_task(self, task_id: str) -> int | None:
        normalized = str(task_id or "").strip()
        if not normalized:
            return None
        artifact = self.active_planning_artifact()
        if artifact is None:
            return None
        for index, snapshot in enumerate(self._planning_artifact_scout_snapshots(artifact)):
            if str(snapshot.get("task_id") or "").strip() == normalized:
                return index
        return None

    def active_plan_execution_index_for_task(self, task_id: str) -> int | None:
        normalized = str(task_id or "").strip()
        if not normalized:
            return None
        artifact = self.active_planning_artifact()
        if artifact is None:
            return None
        for index, snapshot in enumerate(self._planning_artifact_execution_snapshots(artifact)):
            if str(snapshot.get("task_id") or "").strip() == normalized:
                return index
        return None

    def active_plan_scout_command_for_task(
        self,
        task_id: str,
        *,
        file_index: int | None = None,
    ) -> str | None:
        selected_index = self.active_plan_scout_index_for_task(task_id)
        if selected_index is None:
            return None
        command = f"/plan scouts {selected_index + 1}"
        payload = self.active_plan_scout_file_context_payload(selected_index=selected_index)
        file_count = int((payload or {}).get("file_context_file_count") or 0)
        if file_index is not None and file_count > 0:
            bounded = max(0, min(file_count - 1, file_index))
            command += f" file {bounded + 1}"
        return command

    def active_plan_execution_command_for_task(
        self,
        task_id: str,
        *,
        file_index: int | None = None,
    ) -> str | None:
        selected_index = self.active_plan_execution_index_for_task(task_id)
        if selected_index is None:
            return None
        command = f"/plan execution {selected_index + 1}"
        payload = self.active_plan_execution_file_context_payload(selected_index=selected_index)
        file_count = int((payload or {}).get("file_context_file_count") or 0)
        if file_index is not None and file_count > 0:
            bounded = max(0, min(file_count - 1, file_index))
            command += f" file {bounded + 1}"
        return command

    def _related_task_commands_for_change_path(self, path: str) -> list[str]:
        commands: list[str] = []
        for task in self.task_manager.list():
            payload = self.task_file_context_payload(task.id)
            match_index = self._find_matching_file_context_index(
                payload,
                path=path,
                required_reason="active task",
            )
            if match_index is None:
                continue
            commands.append(f"/task show {task.id} file {match_index + 1}")
            commands.append(f"/task show {task.id}")
        return self._dedupe_action_commands(commands)

    def _related_plan_commands_for_change_path(self, path: str) -> list[str]:
        artifact = self.active_planning_artifact()
        if artifact is None:
            return []
        commands: list[str] = []
        summary_payload = self.active_plan_file_context_payload()
        summary_index = self._find_matching_file_context_index(
            summary_payload,
            path=path,
            required_reason="active plan",
        )
        if summary_index is not None:
            commands.append(f"/plan file {summary_index + 1}")
        for child_index in range(self.active_plan_scout_count()):
            payload = self.active_plan_scout_file_context_payload(selected_index=child_index)
            file_index = self._find_matching_file_context_index(
                payload,
                path=path,
                required_reason="active task",
            )
            if file_index is not None:
                commands.append(f"/plan scouts {child_index + 1} file {file_index + 1}")
        for child_index in range(self.active_plan_execution_count()):
            payload = self.active_plan_execution_file_context_payload(selected_index=child_index)
            file_index = self._find_matching_file_context_index(
                payload,
                path=path,
                required_reason="active task",
            )
            if file_index is not None:
                commands.append(f"/plan execution {child_index + 1} file {file_index + 1}")
        return self._dedupe_action_commands(commands)

    def _dedupe_action_commands(self, commands: list[str]) -> list[str]:
        return workflow_dedupe_action_commands(commands)

    def focus_preserving_task_show_command(
        self,
        task_id: str,
        *,
        file_context_payload: dict[str, Any] | None = None,
        file_index: int | None = None,
        fallback_file_index: int = 0,
    ) -> str:
        normalized_task_id = str(task_id or "").strip()
        if not normalized_task_id:
            return "/task show"
        payload = file_context_payload
        if payload is None:
            payload = self.task_file_context_payload(normalized_task_id)
        file_count = int((payload or {}).get("file_context_file_count") or 0)
        if file_count <= 0:
            return f"/task show {normalized_task_id}"
        resolved_file_index = (
            self.preferred_task_file_index(normalized_task_id, fallback=fallback_file_index)
            if file_index is None
            else file_index
        )
        bounded_index = max(0, min(file_count - 1, resolved_file_index))
        return f"/task show {normalized_task_id} file {bounded_index + 1}"

    def _render_action_group_lines(
        self,
        action_groups: dict[str, list[str]],
        *,
        heading: str = "next_actions:",
        line_prefix: str = "- ",
        ordered_keys: tuple[str, ...] | None = None,
    ) -> list[str]:
        return workflow_render_action_group_lines(
            action_groups,
            heading=heading,
            line_prefix=line_prefix,
            ordered_keys=ordered_keys,
        )

    def _render_action_group_summary(
        self,
        action_groups: dict[str, list[str]],
        *,
        ordered_keys: tuple[str, ...] | None = None,
        separator: str = "; ",
        key_value_separator: str = "=",
    ) -> str:
        return workflow_render_action_group_summary(
            action_groups,
            ordered_keys=ordered_keys,
            separator=separator,
            key_value_separator=key_value_separator,
        )

    def render_workflow_action_sections(
        self,
        action_groups: dict[str, list[str]],
        *,
        heading: str = "next_actions:",
        ordered_keys: tuple[str, ...] | None = None,
        line_prefix: str = "- ",
    ) -> list[str]:
        return workflow_render_workflow_action_sections(
            action_groups,
            heading=heading,
            ordered_keys=ordered_keys,
            line_prefix=line_prefix,
        )

    def render_navigation_section(
        self,
        commands: dict[str, str | None],
        *,
        heading: str = "navigation:",
        line_prefix: str = "- ",
        ordered_keys: tuple[str, ...] | None = None,
        include_empty: bool = True,
    ) -> list[str]:
        return workflow_render_navigation_section(
            commands,
            heading=heading,
            line_prefix=line_prefix,
            ordered_keys=ordered_keys,
            include_empty=include_empty,
        )

    def render_primary_secondary_action_section(
        self,
        *,
        primary_action: str | None,
        secondary_action: str | None,
        primary_label: str,
        secondary_label: str,
        next_actions: dict[str, str | None] | None = None,
        line_prefix: str = "",
        next_actions_heading: str = "next_actions:",
        include_empty_next_actions: bool = True,
    ) -> list[str]:
        return workflow_render_primary_secondary_action_section(
            primary_action=primary_action,
            secondary_action=secondary_action,
            primary_label=primary_label,
            secondary_label=secondary_label,
            next_actions=next_actions,
            line_prefix=line_prefix,
            next_actions_heading=next_actions_heading,
            include_empty_next_actions=include_empty_next_actions,
        )

    def render_summary_field_lines(
        self,
        summary_fields: list[tuple[str, Any | None]],
        *,
        line_prefix: str = "",
        include_empty: bool = False,
        empty_value: str = "none",
    ) -> list[str]:
        return workflow_render_summary_field_lines(
            summary_fields,
            line_prefix=line_prefix,
            include_empty=include_empty,
            empty_value=empty_value,
        )

    def render_surface_metadata_section(
        self,
        title: str,
        *,
        summary_fields: list[tuple[str, Any | None]],
        action_groups: dict[str, list[str]] | None = None,
        action_order: tuple[str, ...] | None = None,
        line_prefix: str = "- ",
        include_empty_fields: bool = False,
    ) -> list[str]:
        return workflow_render_surface_metadata_section(
            title,
            summary_fields=summary_fields,
            action_groups=action_groups,
            action_order=action_order,
            line_prefix=line_prefix,
            include_empty_fields=include_empty_fields,
        )

    def render_selected_surface_summary(
        self,
        title: str,
        *,
        summary_fields: list[tuple[str, str | None]],
        metadata_line: str | None = None,
        focused_file_lines: list[str] | None = None,
        action_groups: dict[str, list[str]] | None = None,
        action_order: tuple[str, ...] | None = None,
        line_prefix: str = "- ",
    ) -> list[str]:
        return workflow_render_selected_surface_summary(
            title,
            summary_fields=summary_fields,
            metadata_line=metadata_line,
            focused_file_lines=focused_file_lines,
            action_groups=action_groups,
            action_order=action_order,
            line_prefix=line_prefix,
        )

    def _render_file_context_action_group_lines(
        self,
        item: dict[str, Any],
        *,
        stay_on_surface_actions: list[str],
        heading: str = "next_actions:",
        line_prefix: str = "- ",
        ordered_keys: tuple[str, ...] | None = None,
    ) -> list[str]:
        return workflow_render_file_context_action_group_lines(
            self,
            item,
            stay_on_surface_actions=stay_on_surface_actions,
            heading=heading,
            line_prefix=line_prefix,
            ordered_keys=ordered_keys,
        )

    def _render_file_context_action_group_summary(
        self,
        item: dict[str, Any],
        *,
        stay_on_surface_actions: list[str],
        extra_actions: dict[str, list[str]] | None = None,
        ordered_keys: tuple[str, ...] | None = None,
    ) -> str:
        return workflow_render_file_context_action_group_summary(
            self,
            item,
            stay_on_surface_actions=stay_on_surface_actions,
            extra_actions=extra_actions,
            ordered_keys=ordered_keys,
        )

    def render_resolved_file_context_sections(
        self,
        context_selection: dict[str, Any] | None,
        *,
        focused_title: str = "focused file",
        include_file_context: bool = True,
    ) -> list[str]:
        return workflow_render_resolved_file_context_sections(
            self,
            context_selection,
            focused_title=focused_title,
            include_file_context=include_file_context,
        )

    def _render_selected_change_next_action_lines(
        self,
        selected: WorkspaceChangeSet,
        *,
        focused_path: str,
        file_index: int,
        redo: bool,
    ) -> list[str]:
        selector = selected.change_id[:8]
        command_prefix = "/changes show redo " if redo else "/changes show "
        base_command = f"{command_prefix}{selector}"
        stay_actions = self._dedupe_action_commands(
            [base_command, f"{base_command} file {file_index + 1}", "/changes working-set"]
        )
        task_actions = self._related_task_commands_for_change_path(focused_path)
        plan_actions = self._related_plan_commands_for_change_path(focused_path)
        return [
            *self._render_action_group_lines(
                {
                    "go_to_task": task_actions,
                    "go_to_plan": plan_actions,
                    "stay_on_surface": stay_actions,
                },
                ordered_keys=("go_to_task", "go_to_plan", "stay_on_surface"),
            )
        ]

    def _change_stack_entry_action_groups(
        self,
        *,
        selected_index: int,
        limit: int,
        redo: bool,
    ) -> dict[str, list[str]]:
        visible = self._visible_change_stack(redo=redo, limit=limit)
        if not visible:
            return {
                "go_to_change": [],
                "go_to_task": [],
                "go_to_plan": [],
                "stay_on_surface": [],
            }
        bounded_index = max(0, min(selected_index, len(visible) - 1))
        selected = visible[bounded_index]
        selector = selected.change_id[:8]
        command_prefix = "/changes show redo " if redo else "/changes show "
        base_command = f"{command_prefix}{selector}"
        payload = self.selected_change_detail_metadata(
            index=bounded_index,
            limit=limit,
            redo=redo,
            preserve_current_focus=True,
        )
        _files, focused_index, focused_item = self._file_context_items_and_index(payload)
        stay_actions = [base_command, "/changes list", "/changes working-set"]
        if focused_item is None:
            return {
                "go_to_change": [base_command],
                "go_to_task": [],
                "go_to_plan": [],
                "stay_on_surface": self._dedupe_action_commands(stay_actions),
            }
        stay_actions.insert(1, f"{base_command} file {focused_index + 1}")
        return self._file_context_item_action_groups(
            focused_item,
            stay_on_surface_actions=stay_actions,
        )

    def describe_provider(self, *, section: str = "summary") -> str:
        if section not in {"summary", "capabilities", "advisor"}:
            return "Usage: /model [summary|capabilities|advisor]"
        capabilities = getattr(self.provider, "capabilities", None)
        if capabilities is None:
            capability_text = (
                f"provider: {self.config.provider}\n"
                f"model: {self.config.model}\n"
                "notes: provider does not declare capabilities"
            )
        else:
            capability_text = format_capabilities(capabilities)
        if section == "capabilities":
            return capability_text
        if section == "advisor":
            advisor_model = self.state.advisor_model or self.config.model
            relationship = (
                "shared-runtime-model"
                if not self.state.advisor_model or self.state.advisor_model == self.config.model
                else "separate-advisor-model"
            )
            return "\n".join(
                [
                    "current session:",
                    f"provider: {self.config.provider}",
                    f"runtime model: {self.config.model}",
                    f"advisor_model: {advisor_model}",
                    f"advisor_mode: {self.state.advisor_mode}",
                    f"advisor_relationship: {relationship}",
                    "active_plan: " + ("yes" if self.active_planning_artifact() is not None else "no"),
                ]
            )
        return capability_text

    def describe_project_context(self, *, section: str = "summary") -> str:
        if section not in {"summary", "memory", "skills", "plugins", "reload-status"}:
            return "Usage: /project-context [summary|memory|skills|plugins|reload-status]"
        if section == "memory":
            return self.describe_project_memory()
        if section == "skills":
            return self.describe_loaded_skills()
        if section == "plugins":
            return self.describe_plugins()
        if section == "reload-status":
            return self._describe_project_context_reload_status()
        return self._describe_project_context_summary()

    def _describe_project_context_summary(self) -> str:
        skill_groups = self._loaded_skill_groups()
        plugins = self.plugin_registry.list_plugins()
        diagnostics = self.plugin_registry.list_diagnostics()
        enabled_plugins = self.plugin_registry.enabled_plugins(self.state)
        lines = [
            "project context:",
            "project memory: " + ("loaded" if self.project_context.memory_content else "none"),
            "memory path: "
            + (str(self.project_context.memory_path) if self.project_context.memory_path is not None else "none"),
            f"loaded skills: {len(self.project_context.skills)}",
            f"active auto-enabled skills: {len(skill_groups['active_auto'])}",
            f"active manually enabled skills: {len(skill_groups['active_manual'])}",
            f"inactive loaded skills: {len(skill_groups['inactive'])}",
            f"plugins enabled: {len(enabled_plugins)}",
            f"plugins registered: {len(plugins)}",
            f"plugin diagnostics: {len(diagnostics)}",
        ]
        latest_reload = self._last_project_context_reload_summary_line()
        if latest_reload:
            lines.append(latest_reload)
        lines.extend(
            [
                "next_actions:",
                "- /project-context memory",
                "- /project-context skills",
                "- /project-context plugins",
                "- /memory",
                "- /context",
            ]
        )
        return "\n".join(lines)

    def describe_project_memory(self) -> str:
        memory_loaded = bool(self.project_context.memory_content)
        lines = [
            "project memory:",
            "source path: "
            + (str(self.project_context.memory_path) if self.project_context.memory_path is not None else "none"),
            "loaded: " + ("yes" if memory_loaded else "no"),
        ]
        if memory_loaded:
            lines.append("content:")
            lines.append(self.project_context.memory_content)
        else:
            lines.append("content: (none)")
        lines.extend(
            [
                "next_actions:",
                "- /project-context",
                "- /skills",
                "- /context",
                "- /context-refresh",
            ]
        )
        return "\n".join(lines)

    def _loaded_skill_status_parts(self, skill) -> list[str]:
        manual_enabled_names = set(self.state.enabled_skill_names)
        manual_disabled_names = set(self.state.disabled_skill_names)
        if skill.name in manual_disabled_names:
            return ["disabled"]
        if skill.name in manual_enabled_names:
            return ["enabled", "manual"]
        if skill.auto_enable:
            return ["enabled", "auto"]
        return ["inactive"]

    def _loaded_skill_groups(self) -> dict[str, list[Any]]:
        groups: dict[str, list[Any]] = {
            "active_auto": [],
            "active_manual": [],
            "inactive": [],
        }
        for skill in self.project_context.skills:
            status_parts = self._loaded_skill_status_parts(skill)
            if status_parts == ["enabled", "auto"]:
                groups["active_auto"].append(skill)
            elif status_parts == ["enabled", "manual"]:
                groups["active_manual"].append(skill)
            else:
                groups["inactive"].append(skill)
        return groups

    def _render_loaded_skill_lines(self, skill) -> list[str]:
        status = ",".join(self._loaded_skill_status_parts(skill))
        preview = compact_multiline_text(skill.content, max_lines=2, max_chars=140)
        description = f" description={skill.description}" if skill.description else ""
        tags = f" tags={','.join(skill.tags)}" if skill.tags else ""
        if "disabled" in status:
            toggle_command = f"/skills-enable {skill.name}"
        else:
            toggle_command = f"/skills-disable {skill.name}"
        return [
            f"- {skill.name}: status={status} path={skill.path}{description}{tags} content={preview}",
            "  next_actions: "
            + " | ".join([toggle_command, "/project-context skills", "/context-refresh"]),
        ]

    def describe_loaded_skills(self) -> str:
        groups = self._loaded_skill_groups()
        lines = ["loaded skills:"]
        if not self.project_context.skills:
            lines.extend(
                [
                    "No project skills loaded.",
                    "next_actions:",
                    "- /project-context",
                    "- /context-refresh",
                ]
            )
            return "\n".join(lines)
        sections = (
            ("active auto-enabled skills", groups["active_auto"]),
            ("active manually enabled skills", groups["active_manual"]),
            ("inactive loaded skills", groups["inactive"]),
        )
        for label, skills in sections:
            lines.append(f"{label}:")
            if not skills:
                lines.append("- none")
                continue
            for skill in skills:
                lines.extend(self._render_loaded_skill_lines(skill))
        return "\n".join(lines)

    def _plugin_contribution_labels(self, plugin: Any) -> list[str]:
        labels: list[str] = []
        if plugin.commands:
            labels.append("commands")
        if plugin.skills:
            labels.append("skills")
        if plugin.mcp_servers:
            labels.append("mcp_servers")
        if plugin.hooks or not labels:
            labels.append("hooks/config-only")
        return labels

    def _plugin_toggle_command(self, plugin_name: str) -> str:
        if self.plugin_registry.is_enabled(plugin_name, self.state):
            return f"/plugin disable {plugin_name}"
        return f"/plugin enable {plugin_name}"

    def describe_plugins(self) -> str:
        plugins = self.plugin_registry.list_plugins()
        diagnostics = self.plugin_registry.list_diagnostics()
        if not plugins and not diagnostics:
            return "plugin contributions:\nNo plugins registered."
        lines = ["plugin contributions:"]
        for plugin in plugins:
            status = "enabled" if self.plugin_registry.is_enabled(plugin.name, self.state) else "disabled"
            default = "default=enabled" if plugin.default_enabled else "default=disabled"
            path_text = f" path={plugin.path}" if plugin.path is not None else ""
            contributions = ",".join(self._plugin_contribution_labels(plugin))
            next_actions = " | ".join(
                [
                    f"/plugin show {plugin.name}",
                    "/project-context plugins",
                    self._plugin_toggle_command(plugin.name),
                ]
            )
            lines.append(
                f"{plugin.name}: status={status} source={plugin.source} "
                f"version={plugin.version} {default} contributions={contributions} "
                f"commands={len(plugin.commands)} skills={len(plugin.skills)} "
                f"mcp_servers={len(plugin.mcp_servers)} hooks={len(plugin.hooks)} "
                f"description={plugin.description}{path_text} next_actions={next_actions}"
            )
        for diagnostic in diagnostics:
            lines.append(
                f"{diagnostic.name}: status=invalid source={diagnostic.source} "
                f"path={diagnostic.path} error={diagnostic.error} "
                f"next_actions=/project-context plugins | /context-refresh"
            )
        return "\n".join(lines)

    def describe_plugin(self, name: str) -> str:
        plugin_name = name.strip()
        if not plugin_name:
            return "Usage: /plugin show <plugin-name>"
        plugin = self.plugin_registry.get_plugin(plugin_name)
        if plugin is None:
            diagnostic = self.plugin_registry.get_diagnostic(plugin_name)
            if diagnostic is not None:
                return "\n".join(
                    [
                        f"name: {diagnostic.name}",
                        "status: invalid",
                        f"source: {diagnostic.source}",
                        f"path: {diagnostic.path}",
                        f"error: {diagnostic.error}",
                        "next_actions:",
                        "- /project-context plugins",
                        "- /context-refresh",
                    ]
                )
            return f'Unknown plugin "{plugin_name}".'
        status = "enabled" if self.plugin_registry.is_enabled(plugin.name, self.state) else "disabled"
        default = "enabled" if plugin.default_enabled else "disabled"
        lines = [
            f"name: {plugin.name}",
            f"plugin_id: {plugin.plugin_id}",
            f"status: {status}",
            f"default_enabled: {default}",
            f"version: {plugin.version}",
            f"source: {plugin.source}",
            f"description: {plugin.description}",
            "path: " + (str(plugin.path) if plugin.path is not None else "none"),
            f"commands: {len(plugin.commands)}",
            f"skills: {len(plugin.skills)}",
            f"mcp_servers: {len(plugin.mcp_servers)}",
            f"hooks: {len(plugin.hooks)}",
            "contributes commands: " + ("yes" if plugin.commands else "no"),
            "contributes skills: " + ("yes" if plugin.skills else "no"),
            "contributes mcp servers: " + ("yes" if plugin.mcp_servers else "no"),
            "contributes hooks/config-only behavior: "
            + ("yes" if plugin.hooks or not (plugin.commands or plugin.skills or plugin.mcp_servers) else "no"),
        ]
        if plugin.commands:
            lines.append("command_names: " + ", ".join(command.name for command in plugin.commands))
        if plugin.skills:
            lines.append("skill_names: " + ", ".join(skill.name for skill in plugin.skills))
        if plugin.mcp_servers:
            lines.append("mcp_server_names: " + ", ".join(server.name for server in plugin.mcp_servers))
        if plugin.hooks:
            lines.append("hook_names: " + ", ".join(plugin.hooks))
        lines.extend(
            [
                "next_actions:",
                "- /skills",
                "- /project-context plugins",
                "- /project-context",
                f"- {self._plugin_toggle_command(plugin.name)}",
            ]
        )
        return "\n".join(lines)

    def _project_context_reload_snapshot(self) -> dict[str, Any]:
        return {
            "memory_path": str(self.project_context.memory_path) if self.project_context.memory_path is not None else "",
            "memory_content": self.project_context.memory_content,
            "skill_names": tuple(sorted(skill.name for skill in self.project_context.skills)),
            "enabled_skill_names": tuple(sorted(skill.name for skill in self.active_skills())),
            "plugin_names": tuple(sorted(self.plugin_registry.known_plugin_names())),
            "enabled_plugin_names": tuple(
                sorted(plugin.name for plugin in self.plugin_registry.enabled_plugins(self.state))
            ),
        }

    def _record_project_context_reload_result(
        self,
        *,
        before: dict[str, Any],
        error: str | None = None,
    ) -> None:
        after = self._project_context_reload_snapshot()
        self._last_project_context_reload = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "memory_changed": (
                before.get("memory_path") != after.get("memory_path")
                or before.get("memory_content") != after.get("memory_content")
            ),
            "skill_set_changed": before.get("skill_names") != after.get("skill_names"),
            "plugin_state_changed": (
                before.get("enabled_plugin_names") != after.get("enabled_plugin_names")
                or before.get("plugin_names") != after.get("plugin_names")
            ),
            "before": before,
            "after": after,
            "error": error,
        }

    def _last_project_context_reload_summary_line(self) -> str:
        status = self._last_project_context_reload
        if not isinstance(status, dict):
            return ""
        timestamp = str(status.get("timestamp") or "unknown")
        error = str(status.get("error") or "").strip()
        return (
            "latest reload: "
            f"{timestamp} memory_changed={self._yes_no(status.get('memory_changed'))} "
            f"skill_set_changed={self._yes_no(status.get('skill_set_changed'))} "
            f"plugin_state_changed={self._yes_no(status.get('plugin_state_changed'))} "
            f"errors={'none' if not error else error}"
        )

    def _describe_project_context_reload_status(self) -> str:
        status = self._last_project_context_reload
        if not isinstance(status, dict):
            return "\n".join(
                [
                    "reload status:",
                    "No project-context reload has run in this live session.",
                    "next_actions:",
                    "- /context-refresh",
                    "- /skills-reload",
                    "- /project-context",
                ]
            )
        before = status.get("before") if isinstance(status.get("before"), dict) else {}
        after = status.get("after") if isinstance(status.get("after"), dict) else {}
        error = str(status.get("error") or "").strip()
        lines = [
            "reload status:",
            f"timestamp: {status.get('timestamp') or 'unknown'}",
            f"memory changed: {self._yes_no(status.get('memory_changed'))}",
            f"skill set changed: {self._yes_no(status.get('skill_set_changed'))}",
            f"plugin state changed: {self._yes_no(status.get('plugin_state_changed'))}",
            "errors: " + ("none" if not error else error),
            "before memory path: " + (str(before.get("memory_path") or "none")),
            "after memory path: " + (str(after.get("memory_path") or "none")),
            "before skills: " + str(len(before.get("skill_names") or ())),
            "after skills: " + str(len(after.get("skill_names") or ())),
            "before enabled plugins: " + str(len(before.get("enabled_plugin_names") or ())),
            "after enabled plugins: " + str(len(after.get("enabled_plugin_names") or ())),
            "next_actions:",
            "- /project-context",
            "- /context-refresh",
            "- /skills-reload",
        ]
        return "\n".join(lines)

    def _yes_no(self, value: Any) -> str:
        return "yes" if bool(value) else "no"

    def describe_advisor(self) -> str:
        return self._advisor_component.describe_advisor()

    def describe_active_plan(
        self,
        *,
        file_index: int = 0,
        preserve_current_focus: bool = True,
    ) -> str:
        return self._plan_component.describe_active_plan(
            file_index=file_index,
            preserve_current_focus=preserve_current_focus,
        )

    def describe_active_plan_scouts(
        self,
        *,
        file_index: int = 0,
        preserve_current_focus: bool = True,
    ) -> str:
        return self._plan_component.describe_active_plan_scouts(
            file_index=file_index,
            preserve_current_focus=preserve_current_focus,
        )

    def describe_active_plan_scouts_at(
        self,
        selected_index: int = 0,
        *,
        full_detail: bool = False,
        file_index: int = 0,
        preserve_current_focus: bool = False,
    ) -> str:
        return self._plan_component.describe_active_plan_scouts_at(
            selected_index,
            full_detail=full_detail,
            file_index=file_index,
            preserve_current_focus=preserve_current_focus,
        )

    def describe_active_plan_execution(
        self,
        *,
        file_index: int = 0,
        preserve_current_focus: bool = True,
    ) -> str:
        return self._plan_component.describe_active_plan_execution(
            file_index=file_index,
            preserve_current_focus=preserve_current_focus,
        )

    def describe_active_plan_execution_at(
        self,
        selected_index: int = 0,
        *,
        full_detail: bool = False,
        file_index: int = 0,
        preserve_current_focus: bool = False,
    ) -> str:
        return self._plan_component.describe_active_plan_execution_at(
            selected_index,
            full_detail=full_detail,
            file_index=file_index,
            preserve_current_focus=preserve_current_focus,
        )

    def active_plan_scout_count(self) -> int:
        return self._plan_component.active_plan_scout_count()

    def active_plan_execution_count(self) -> int:
        return self._plan_component.active_plan_execution_count()

    def active_plan_scout_file_context_payload(
        self,
        *,
        selected_index: int = 0,
    ) -> dict[str, Any] | None:
        return self._plan_component.active_plan_scout_file_context_payload(
            selected_index=selected_index
        )

    def active_plan_execution_file_context_payload(
        self,
        *,
        selected_index: int = 0,
    ) -> dict[str, Any] | None:
        return self._plan_component.active_plan_execution_file_context_payload(
            selected_index=selected_index
        )

    def describe_active_plan_timeline(
        self,
        kind_filter: str = "all",
        *,
        delta_mode: str = "none",
        phase_filter: str = "none",
        focus_mode: str = "none",
        compare_mode: str = "none",
        selected_compare_index: int = 0,
        selected_phase_local_task_index: int = 0,
        artifact_id: str | None = None,
    ) -> str:
        return self._plan_component.describe_active_plan_timeline_at(
            0,
            kind_filter=kind_filter,
            delta_mode=delta_mode,
            phase_filter=phase_filter,
            focus_mode=focus_mode,
            compare_mode=compare_mode,
            selected_compare_index=selected_compare_index,
            selected_phase_local_task_index=selected_phase_local_task_index,
            artifact_id=artifact_id,
        )

    def describe_active_plan_replay(
        self,
        kind_filter: str = "all",
        *,
        delta_mode: str = "none",
        phase_filter: str = "none",
        focus_mode: str = "none",
        compare_mode: str = "none",
        selected_compare_index: int = 0,
        selected_phase_local_task_index: int = 0,
        selected_index: int = 0,
        latest: bool = False,
        source_mode: str = "auto",
        artifact_id: str | None = None,
    ) -> str:
        return self._plan_component.describe_active_plan_replay_at(
            selected_index,
            kind_filter=kind_filter,
            delta_mode=delta_mode,
            phase_filter=phase_filter,
            focus_mode=focus_mode,
            compare_mode=compare_mode,
            selected_compare_index=selected_compare_index,
            selected_phase_local_task_index=selected_phase_local_task_index,
            latest=latest,
            source_mode=source_mode,
            artifact_id=artifact_id,
        )

    def describe_active_plan_audit(
        self,
        *,
        artifact_id: str | None = None,
        selected_index: int | None = None,
    ) -> str:
        return self._plan_component.describe_active_plan_audit_at(
            selected_index,
            artifact_id=artifact_id,
        )

    def describe_active_plan_timeline_at(
        self,
        selected_index: int = 0,
        *,
        kind_filter: str = "all",
        delta_mode: str = "none",
        phase_filter: str = "none",
        focus_mode: str = "none",
        compare_mode: str = "none",
        selected_compare_index: int = 0,
        selected_phase_local_task_index: int = 0,
        artifact_id: str | None = None,
    ) -> str:
        return self._plan_component.describe_active_plan_timeline_at(
            selected_index,
            kind_filter=kind_filter,
            delta_mode=delta_mode,
            phase_filter=phase_filter,
            focus_mode=focus_mode,
            compare_mode=compare_mode,
            selected_compare_index=selected_compare_index,
            selected_phase_local_task_index=selected_phase_local_task_index,
            artifact_id=artifact_id,
        )

    def describe_active_plan_replay_at(
        self,
        selected_index: int = 0,
        *,
        kind_filter: str = "all",
        delta_mode: str = "none",
        phase_filter: str = "none",
        focus_mode: str = "none",
        compare_mode: str = "none",
        selected_compare_index: int = 0,
        selected_phase_local_task_index: int = 0,
        latest: bool = False,
        source_mode: str = "auto",
        artifact_id: str | None = None,
    ) -> str:
        return self._plan_component.describe_active_plan_replay_at(
            selected_index,
            kind_filter=kind_filter,
            delta_mode=delta_mode,
            phase_filter=phase_filter,
            focus_mode=focus_mode,
            compare_mode=compare_mode,
            selected_compare_index=selected_compare_index,
            selected_phase_local_task_index=selected_phase_local_task_index,
            latest=latest,
            source_mode=source_mode,
            artifact_id=artifact_id,
        )

    def describe_active_plan_audit_at(
        self,
        selected_index: int | None = None,
        *,
        artifact_id: str | None = None,
    ) -> str:
        return self._plan_component.describe_active_plan_audit_at(
            selected_index,
            artifact_id=artifact_id,
        )

    def describe_active_plan_lineage(self) -> str:
        return self._plan_component.describe_active_plan_lineage()

    def describe_active_plan_lineage_at(self, selected_index: int = 0) -> str:
        return self._plan_component.describe_active_plan_lineage_at(selected_index)

    def active_plan_lineage_index(self) -> int:
        return self._plan_component.active_plan_lineage_index()

    def describe_active_plan_advisor(
        self,
        *,
        file_index: int = 0,
        preserve_current_focus: bool = True,
    ) -> str:
        return self._plan_component.describe_active_plan_advisor(
            file_index=file_index,
            preserve_current_focus=preserve_current_focus,
        )

    def _describe_planning_artifact_advisor(self, artifact: PlanningArtifact) -> str:
        return self._plan_component._describe_planning_artifact_advisor(artifact)

    def open_active_plan_advisor(self) -> str:
        return self._plan_component.open_active_plan_advisor()

    def show_advisor_status(self) -> str:
        return self._advisor_component.show_advisor_status()

    def describe_planning_artifacts(self) -> str:
        return self._plan_component.describe_planning_artifacts()

    def describe_planning_artifact(self, identifier: str) -> str:
        return self._plan_component.describe_planning_artifact(identifier)

    def use_planning_artifact(self, identifier: str) -> str:
        return self._plan_component.use_planning_artifact(identifier)

    def revert_to_planning_artifact(self, identifier: str) -> str:
        return self._plan_component.revert_to_planning_artifact(identifier)

    def clear_active_plan(self) -> str:
        return self._plan_component.clear_active_plan()

    def _render_planning_artifact_detail(self, artifact: PlanningArtifact, *, active: bool) -> str:
        return self._plan_component._render_planning_artifact_detail(artifact, active=active)

    def _render_planning_artifact_summary(self, artifact: PlanningArtifact, *, active: bool) -> str:
        return self._plan_component._render_planning_artifact_summary(artifact, active=active)

    def _render_planning_artifact_summary_lines(
        self,
        artifact: PlanningArtifact,
        *,
        active: bool,
    ) -> list[str]:
        return self._plan_component._render_planning_artifact_summary_lines(
            artifact,
            active=active,
        )

    def _render_planning_artifact_lineage(
        self,
        artifact: PlanningArtifact,
        *,
        selected_index: int | None = None,
    ) -> list[str]:
        return self._plan_component._render_planning_artifact_lineage(
            artifact,
            selected_index=selected_index,
        )

    def _render_lineage_actions(
        self,
        lineage: list[PlanningArtifact],
        *,
        current_artifact: PlanningArtifact,
    ) -> list[str]:
        return self._plan_component._render_lineage_actions(
            lineage,
            current_artifact=current_artifact,
        )

    def _lineage_default_action(self, artifact: PlanningArtifact) -> str:
        return self._plan_component._lineage_default_action(artifact)

    def _planning_artifact_lineage(self, artifact: PlanningArtifact) -> list[PlanningArtifact]:
        return self._plan_component._planning_artifact_lineage(artifact)

    def _planning_artifact_lineage_position(self, artifact: PlanningArtifact) -> str:
        return self._plan_component._planning_artifact_lineage_position(artifact)

    def _planning_artifact_audit_item(self, artifact: PlanningArtifact) -> dict[str, Any]:
        return self._plan_component._planning_artifact_audit_item(artifact)

    def _planning_artifact_lineage_audit_summary(
        self,
        audit_items: list[dict[str, Any]],
    ) -> dict[str, str]:
        return self._plan_component._planning_artifact_lineage_audit_summary(audit_items)

    def _render_planning_artifact_audit_items(
        self,
        audit_items: list[dict[str, Any]],
        *,
        selected_index: int,
        current_artifact_id: str,
    ) -> list[str]:
        return self._plan_component._render_planning_artifact_audit_items(
            audit_items,
            selected_index=selected_index,
            current_artifact_id=current_artifact_id,
        )

    def _render_selected_artifact_audit_deltas(
        self,
        lineage: list[PlanningArtifact],
        audit_items: list[dict[str, Any]],
        selected_index: int,
    ) -> list[str]:
        return self._plan_component._render_selected_artifact_audit_deltas(
            lineage,
            audit_items,
            selected_index,
        )

    def _render_artifact_audit_delta(
        self,
        label: str,
        left_item: dict[str, Any],
        right_item: dict[str, Any],
    ) -> list[str]:
        return self._plan_component._render_artifact_audit_delta(label, left_item, right_item)

    def _render_planning_artifact_comparisons(self, artifact: PlanningArtifact) -> list[str]:
        return self._plan_component._render_planning_artifact_comparisons(artifact)

    def _render_recent_plan_drift_analysis(
        self,
        artifact: PlanningArtifact,
        *,
        active: bool,
    ) -> list[str]:
        return self._plan_component._render_recent_plan_drift_analysis(
            artifact,
            active=active,
        )

    def _render_planning_artifact_comparison(
        self,
        label: str,
        base: PlanningArtifact,
        target: PlanningArtifact,
    ) -> list[str]:
        return self._plan_component._render_planning_artifact_comparison(
            label,
            base,
            target,
        )

    def _render_plan_section_diff(
        self,
        label: str,
        before_summary: str,
        after_summary: str,
        *,
        section_key: str,
    ) -> list[str]:
        return self._plan_component._render_plan_section_diff(
            label,
            before_summary,
            after_summary,
            section_key=section_key,
        )

    def _plan_summary_sections(self, summary: str) -> dict[str, str]:
        return self._plan_component._plan_summary_sections(summary)

    def _summarize_planning_summary_diff(self, before: str, after: str) -> list[str]:
        return self._plan_component._summarize_planning_summary_diff(before, after)

    def _render_planning_artifact_advisor_review(self, artifact: PlanningArtifact) -> list[str]:
        return self._plan_component._render_planning_artifact_advisor_review(artifact)

    def _planning_artifact_timeline_entries(
        self,
        artifact: PlanningArtifact,
        *,
        kind_filter: str = "all",
        delta_mode: str = "none",
        phase_filter: str = "none",
        focus_mode: str = "none",
    ) -> list[dict[str, str]]:
        return self._plan_component._planning_artifact_timeline_entries(
            artifact,
            kind_filter=kind_filter,
            delta_mode=delta_mode,
            phase_filter=phase_filter,
            focus_mode=focus_mode,
        )

    def _timeline_entries_for_task_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        task_kind: str,
    ) -> list[dict[str, str]]:
        return self._plan_component._timeline_entries_for_task_snapshot(
            snapshot,
            task_kind=task_kind,
        )

    def _timeline_drift_primary_action(self, artifact: PlanningArtifact) -> str:
        return self._plan_component._timeline_drift_primary_action(artifact)

    def _timeline_entry_sort_key(self, entry: dict[str, str]) -> tuple[str, int, int, int]:
        return self._plan_component._timeline_entry_sort_key(entry)

    def _timeline_audit_summary(self, entries: list[dict[str, str]]) -> dict[str, str]:
        return self._plan_component._timeline_audit_summary(entries)

    def _timeline_section_summaries(self, entries: list[dict[str, str]]) -> dict[str, dict[str, str]]:
        return self._plan_component._timeline_section_summaries(entries)

    def _timeline_bounds(self, entries: list[dict[str, str]]) -> tuple[str | None, str | None]:
        return self._plan_component._timeline_bounds(entries)

    def _format_timeline_span(self, start: str | None, end: str | None) -> str:
        return self._plan_component._format_timeline_span(start, end)

    def _format_timeline_duration(self, start: str | None, end: str | None) -> str:
        return self._plan_component._format_timeline_duration(start, end)

    def _normalize_timeline_kind_filter(self, kind_filter: str) -> str:
        return self._plan_component._normalize_timeline_kind_filter(kind_filter)

    def _normalize_timeline_delta_mode(self, delta_mode: str) -> str:
        return self._plan_component._normalize_timeline_delta_mode(delta_mode)

    def _normalize_timeline_phase_filter(self, phase_filter: str) -> str:
        return self._plan_component._normalize_timeline_phase_filter(phase_filter)

    def _normalize_timeline_focus_mode(self, focus_mode: str) -> str:
        return self._plan_component._normalize_timeline_focus_mode(focus_mode)

    def _normalize_timeline_compare_mode(self, compare_mode: str) -> str:
        return self._plan_component._normalize_timeline_compare_mode(compare_mode)

    def _timeline_entry_matches_filter(self, entry: dict[str, str], *, kind_filter: str) -> bool:
        return self._plan_component._timeline_entry_matches_filter(
            entry,
            kind_filter=kind_filter,
        )

    def _timeline_entry_matches_delta(
        self,
        entry: dict[str, str],
        *,
        artifact: PlanningArtifact,
        delta_mode: str,
    ) -> bool:
        return self._plan_component._timeline_entry_matches_delta(
            entry,
            artifact=artifact,
            delta_mode=delta_mode,
        )

    def _timeline_entry_matches_phase(self, entry: dict[str, str], *, phase_filter: str) -> bool:
        return self._plan_component._timeline_entry_matches_phase(
            entry,
            phase_filter=phase_filter,
        )

    def _timeline_entry_matches_focus(self, entry: dict[str, str], *, focus_mode: str) -> bool:
        return self._plan_component._timeline_entry_matches_focus(
            entry,
            focus_mode=focus_mode,
        )

    def _timeline_compare_items(
        self,
        artifact: PlanningArtifact,
        *,
        kind_filter: str,
        delta_mode: str,
        phase_filter: str,
        focus_mode: str,
        compare_mode: str,
        current_entries: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        return self._plan_component._timeline_compare_items(
            artifact,
            kind_filter=kind_filter,
            delta_mode=delta_mode,
            phase_filter=phase_filter,
            focus_mode=focus_mode,
            compare_mode=compare_mode,
            current_entries=current_entries,
        )

    def _render_timeline_compare_summary(
        self,
        *,
        left_label: str,
        left_entries: list[dict[str, str]],
        right_label: str,
        right_entries: list[dict[str, str]],
        left_primary_action: str,
        left_secondary_action: str,
        right_primary_action: str,
        right_secondary_action: str,
        advisor_primary_action: str,
        advisor_secondary_action: str,
        drift_primary_action: str,
        drift_secondary_action: str,
        artifact: PlanningArtifact,
        current_entries: list[dict[str, str]],
        phase_filter: str,
        left_artifact_id: str | None,
        right_artifact_id: str | None,
    ) -> list[dict[str, str]]:
        return self._plan_component._render_timeline_compare_summary(
            left_label=left_label,
            left_entries=left_entries,
            right_label=right_label,
            right_entries=right_entries,
            left_primary_action=left_primary_action,
            left_secondary_action=left_secondary_action,
            right_primary_action=right_primary_action,
            right_secondary_action=right_secondary_action,
            advisor_primary_action=advisor_primary_action,
            advisor_secondary_action=advisor_secondary_action,
            drift_primary_action=drift_primary_action,
            drift_secondary_action=drift_secondary_action,
            artifact=artifact,
            current_entries=current_entries,
            phase_filter=phase_filter,
            left_artifact_id=left_artifact_id,
            right_artifact_id=right_artifact_id,
        )

    def _timeline_phase_local_compare_items(
        self,
        *,
        artifact: PlanningArtifact,
        current_entries: list[dict[str, str]],
        left_label: str,
        left_summary: dict[str, str],
        right_label: str,
        right_summary: dict[str, str],
        phase_filter: str,
        left_artifact_id: str | None,
        right_artifact_id: str | None,
        left_primary_action: str,
        right_primary_action: str,
        advisor_primary_action: str,
        advisor_secondary_action: str,
        drift_primary_action: str,
        drift_secondary_action: str,
    ) -> list[dict[str, str]]:
        return self._plan_component._timeline_phase_local_compare_items(
            artifact=artifact,
            current_entries=current_entries,
            left_label=left_label,
            left_summary=left_summary,
            right_label=right_label,
            right_summary=right_summary,
            phase_filter=phase_filter,
            left_artifact_id=left_artifact_id,
            right_artifact_id=right_artifact_id,
            left_primary_action=left_primary_action,
            right_primary_action=right_primary_action,
            advisor_primary_action=advisor_primary_action,
            advisor_secondary_action=advisor_secondary_action,
            drift_primary_action=drift_primary_action,
            drift_secondary_action=drift_secondary_action,
        )

    def _timeline_phase_delta_compare_items(
        self,
        *,
        artifact: PlanningArtifact,
        current_entries: list[dict[str, str]],
        phase_filter: str,
        artifact_id: str | None,
    ) -> list[dict[str, str]]:
        return self._plan_component._timeline_phase_delta_compare_items(
            artifact=artifact,
            current_entries=current_entries,
            phase_filter=phase_filter,
            artifact_id=artifact_id,
        )

    def _timeline_entries_with_delta(
        self,
        entries: list[dict[str, str]],
        *,
        artifact: PlanningArtifact,
        delta_mode: str,
    ) -> list[dict[str, str]]:
        return self._plan_component._timeline_entries_with_delta(
            entries,
            artifact=artifact,
            delta_mode=delta_mode,
        )

    def _timeline_phase_local_audit_summary(
        self,
        *,
        artifact: PlanningArtifact,
        entries: list[dict[str, str]],
        phase_filter: str,
        selected_task_index: int = 0,
    ) -> list[str]:
        return self._plan_component._timeline_phase_local_audit_summary(
            artifact=artifact,
            entries=entries,
            phase_filter=phase_filter,
            selected_task_index=selected_task_index,
        )

    def _phase_local_execution_task_ids(self, artifact: PlanningArtifact) -> list[str]:
        return self._plan_component._phase_local_execution_task_ids(artifact)

    def _phase_local_recent_drift_task_id(self, artifact: PlanningArtifact) -> str | None:
        return self._plan_component._phase_local_recent_drift_task_id(artifact)

    def open_phase_local_execution_task(self, identifier: str = "") -> str:
        return self._plan_component.open_phase_local_execution_task(identifier)

    def open_phase_local_recent_drift_task(self, identifier: str = "") -> str:
        return self._plan_component.open_phase_local_recent_drift_task(identifier)

    def focus_active_plan_timeline_task(self, identifier: str) -> str:
        return self._plan_component.focus_active_plan_timeline_task(identifier)

    def clear_active_plan_timeline_focus(self) -> str:
        return self._plan_component.clear_active_plan_timeline_focus()

    def _normalize_replay_source_mode(self, source_mode: str) -> str:
        return self._plan_component._normalize_replay_source_mode(source_mode)

    def _resolve_replay_target(
        self,
        *,
        artifact: PlanningArtifact,
        entries: list[dict[str, str]],
        compare_items: list[dict[str, Any]],
        selected_compare_index: int,
        phase_filter: str,
        selected_phase_local_task_index: int,
        selected_index: int,
        latest: bool,
        source_mode: str,
    ) -> tuple[str, PlanningArtifact, list[dict[str, str]], int, dict[str, str] | None]:
        return self._plan_component._resolve_replay_target(
            artifact=artifact,
            entries=entries,
            compare_items=compare_items,
            selected_compare_index=selected_compare_index,
            phase_filter=phase_filter,
            selected_phase_local_task_index=selected_phase_local_task_index,
            selected_index=selected_index,
            latest=latest,
            source_mode=source_mode,
        )

    def _timeline_command_suffix(
        self,
        *,
        kind_filter: str,
        delta_mode: str,
        phase_filter: str,
        focus_mode: str,
        compare_mode: str,
        artifact_id: str | None,
    ) -> str:
        return self._plan_component._timeline_command_suffix(
            kind_filter=kind_filter,
            delta_mode=delta_mode,
            phase_filter=phase_filter,
            focus_mode=focus_mode,
            compare_mode=compare_mode,
            artifact_id=artifact_id,
        )

    def _lineage_replay_compare_actions(
        self,
        *,
        artifact: PlanningArtifact,
        kind_filter: str,
        delta_mode: str,
        phase_filter: str,
        focus_mode: str,
    ) -> list[str]:
        return self._plan_component._lineage_replay_compare_actions(
            artifact=artifact,
            kind_filter=kind_filter,
            delta_mode=delta_mode,
            phase_filter=phase_filter,
            focus_mode=focus_mode,
        )

    def _render_lineage_replay_compare(
        self,
        *,
        artifact: PlanningArtifact,
        replay_artifact: PlanningArtifact,
        kind_filter: str,
        delta_mode: str,
        phase_filter: str,
        focus_mode: str,
        compare_mode: str,
    ) -> list[str]:
        return self._plan_component._render_lineage_replay_compare(
            artifact=artifact,
            replay_artifact=replay_artifact,
            kind_filter=kind_filter,
            delta_mode=delta_mode,
            phase_filter=phase_filter,
            focus_mode=focus_mode,
            compare_mode=compare_mode,
        )

    def _timeline_entry_task_ids_for_kind(
        self,
        entries: list[dict[str, str]],
        kind: str,
    ) -> set[str]:
        return self._plan_component._timeline_entry_task_ids_for_kind(entries, kind)

    def _timeline_entry_identity(self, entry: dict[str, str]) -> tuple[str, str, str, str]:
        return self._plan_component._timeline_entry_identity(entry)

    def _render_lineage_replay_entry_deltas(
        self,
        current_entries: list[dict[str, str]],
        previous_entries: list[dict[str, str]],
    ) -> list[str]:
        return self._plan_component._render_lineage_replay_entry_deltas(
            current_entries,
            previous_entries,
        )

    def _render_lineage_replay_phase_deltas(
        self,
        current_entries: list[dict[str, str]],
        previous_entries: list[dict[str, str]],
    ) -> list[str]:
        return self._plan_component._render_lineage_replay_phase_deltas(
            current_entries,
            previous_entries,
        )

    def _render_lineage_replay_delta_block(
        self,
        label: str,
        entries: list[dict[str, str]],
    ) -> list[str]:
        return self._plan_component._render_lineage_replay_delta_block(label, entries)

    def _preferred_replay_latest_index(
        self,
        entries: list[dict[str, str]],
        *,
        default: int,
    ) -> int:
        return self._plan_component._preferred_replay_latest_index(
            entries,
            default=default,
        )

    def _latest_replay_entry_index(
        self,
        entries: list[dict[str, str]],
        replay_entries: list[dict[str, Any]],
        *,
        default: int,
    ) -> int:
        return self._plan_component._latest_replay_entry_index(
            entries,
            replay_entries,
            default=default,
        )

    def _latest_replay_entry_index_for_task(
        self,
        entries: list[dict[str, str]],
        task_id: str,
        *,
        default: int,
    ) -> int:
        return self._plan_component._latest_replay_entry_index_for_task(
            entries,
            task_id,
            default=default,
        )

    def _replay_phase_local_task_id(
        self,
        artifact: PlanningArtifact,
        selected_task_index: int,
    ) -> str | None:
        return self._plan_component._replay_phase_local_task_id(
            artifact,
            selected_task_index,
        )

    def _render_replay_linked_blocks(
        self,
        artifact: PlanningArtifact,
        entry: dict[str, str],
    ) -> list[str]:
        return self._plan_component._render_replay_linked_blocks(artifact, entry)

    def _timeline_entries_primary_action(
        self,
        entries: list[dict[str, str]],
        *,
        fallback: str,
    ) -> str:
        return self._plan_component._timeline_entries_primary_action(
            entries,
            fallback=fallback,
        )

    def _timeline_default_view_action(self, kind_filter: str) -> str:
        return self._plan_component._timeline_default_view_action(kind_filter)

    def _resolve_timeline_artifact(self, artifact_id: str | None) -> PlanningArtifact | None:
        return self._plan_component._resolve_timeline_artifact(artifact_id)

    def _timeline_section_compare_items(
        self,
        *,
        left_label: str,
        left_entries: list[dict[str, str]],
        right_label: str,
        right_entries: list[dict[str, str]],
        left_artifact_id: str | None = None,
        right_artifact_id: str | None = None,
    ) -> list[dict[str, str]]:
        return self._plan_component._timeline_section_compare_items(
            left_label=left_label,
            left_entries=left_entries,
            right_label=right_label,
            right_entries=right_entries,
            left_artifact_id=left_artifact_id,
            right_artifact_id=right_artifact_id,
        )

    def _timeline_entries_for_section(
        self,
        entries: list[dict[str, str]],
        section: str,
    ) -> list[dict[str, str]]:
        return self._plan_component._timeline_entries_for_section(entries, section)

    def _timeline_section_default_action(self, section: str) -> str:
        return self._plan_component._timeline_section_default_action(section)

    def _timeline_section_timeline_action(self, section: str, *, artifact_id: str | None) -> str:
        return self._plan_component._timeline_section_timeline_action(
            section,
            artifact_id=artifact_id,
        )

    def _timeline_phase_timeline_action(
        self,
        phase: str,
        *,
        artifact_id: str | None,
        delta_mode: str | None = None,
    ) -> str:
        return self._plan_component._timeline_phase_timeline_action(
            phase,
            artifact_id=artifact_id,
            delta_mode=delta_mode,
        )

    def _previous_planning_artifact(self, artifact: PlanningArtifact) -> PlanningArtifact | None:
        return self._plan_component._previous_planning_artifact(artifact)

    def _latest_plan_drift_timestamp(self) -> str:
        for review in reversed(self.state.advisor_review_history):
            if review.checkpoint == "plan_drift":
                return review.created_at
        if (
            self.state.advisor_last_result is not None
            and self.state.advisor_last_result.checkpoint == "plan_drift"
        ):
            return self.state.advisor_last_result.created_at
        active = self.active_planning_artifact()
        if active is not None:
            return active.created_at
        return self.state.created_at

    def _planning_artifact_scout_snapshots(self, artifact: PlanningArtifact) -> list[dict[str, Any]]:
        return self._plan_component._planning_artifact_scout_snapshots(artifact)

    def _planning_artifact_execution_snapshots(self, artifact: PlanningArtifact) -> list[dict[str, Any]]:
        return self._plan_component._planning_artifact_execution_snapshots(artifact)

    def _render_planning_artifact_scout_outputs(
        self,
        artifact: PlanningArtifact,
        *,
        selected_index: int | None = None,
    ) -> list[str]:
        return self._plan_component._render_planning_artifact_scout_outputs(
            artifact,
            selected_index=selected_index,
        )

    def _render_planning_artifact_scout_detail(
        self,
        artifact: PlanningArtifact,
        *,
        selected_index: int = 0,
        full_detail: bool = False,
    ) -> list[str]:
        return self._plan_component._render_planning_artifact_scout_detail(
            artifact,
            selected_index=selected_index,
            full_detail=full_detail,
        )

    def _render_planning_artifact_execution_outputs(
        self,
        artifact: PlanningArtifact,
        *,
        selected_index: int | None = None,
    ) -> list[str]:
        return self._plan_component._render_planning_artifact_execution_outputs(
            artifact,
            selected_index=selected_index,
        )

    def _render_planning_artifact_execution_detail(
        self,
        artifact: PlanningArtifact,
        *,
        selected_index: int = 0,
        full_detail: bool = False,
    ) -> list[str]:
        return self._plan_component._render_planning_artifact_execution_detail(
            artifact,
            selected_index=selected_index,
            full_detail=full_detail,
        )

    def _render_selected_scout_comparisons(
        self,
        artifact: PlanningArtifact,
        *,
        selected_index: int = 0,
    ) -> list[str]:
        return self._plan_component._render_selected_scout_comparisons(
            artifact,
            selected_index=selected_index,
        )

    def _render_selected_execution_comparisons(
        self,
        artifact: PlanningArtifact,
        *,
        selected_index: int = 0,
    ) -> list[str]:
        return self._plan_component._render_selected_execution_comparisons(
            artifact,
            selected_index=selected_index,
        )

    def _render_selected_execution_context(
        self,
        artifact: PlanningArtifact,
        *,
        selected_index: int = 0,
    ) -> list[str]:
        return self._plan_component._render_selected_execution_context(
            artifact,
            selected_index=selected_index,
        )

    def _render_task_detail_execution_context(self, task: Any) -> list[str]:
        return self._task_detail_component._render_task_detail_execution_context(task)

    def _task_execution_planning_artifact(self, metadata: dict[str, Any]) -> PlanningArtifact | None:
        return self._task_detail_component._task_execution_planning_artifact(metadata)

    def _render_task_detail_active_plan_summary(
        self,
        artifact: PlanningArtifact | None,
        metadata: dict[str, Any],
    ) -> list[str]:
        return self._task_detail_component._render_task_detail_active_plan_summary(
            artifact,
            metadata,
        )

    def _render_task_detail_advisor_context(
        self,
        artifact: PlanningArtifact | None,
        metadata: dict[str, Any],
    ) -> list[str]:
        return self._task_detail_component._render_task_detail_advisor_context(
            artifact,
            metadata,
        )

    def _render_task_detail_drift_context(self, metadata: dict[str, Any]) -> list[str]:
        return self._task_detail_component._render_task_detail_drift_context(metadata)

    def _summarize_text_diff(self, before: str, after: str) -> list[str]:
        return summarize_text_diff(before, after)

    def has_advisor_model(self) -> bool:
        return self._advisor_component.has_advisor_model()

    def uses_interactive_advisor(self) -> bool:
        return self._advisor_component.uses_interactive_advisor()

    def build_advisor_provider(self):
        return self._advisor_component.build_advisor_provider()

    def build_advisor_review_prompt(
        self,
        *,
        checkpoint: str,
        user_prompt: str,
        candidate_text: str,
        pending_tool_names: tuple[str, ...] = (),
        active_plan: PlanningArtifact | None = None,
        plan_drift_context: str | None = None,
    ) -> str:
        return self._advisor_component.build_advisor_review_prompt(
            checkpoint=checkpoint,
            user_prompt=user_prompt,
            candidate_text=candidate_text,
            pending_tool_names=pending_tool_names,
            active_plan=active_plan,
            plan_drift_context=plan_drift_context,
        )

    def build_advisor_revision_prompt(
        self,
        *,
        user_prompt: str,
        draft_text: str,
        advisor_feedback: str,
    ) -> str:
        return self._advisor_component.build_advisor_revision_prompt(
            user_prompt=user_prompt,
            draft_text=draft_text,
            advisor_feedback=advisor_feedback,
        )

    def build_advisor_followup_prompt(
        self,
        *,
        checkpoint: str,
        advisor_review: AdvisorReviewSummary,
        pending_tool_names: tuple[str, ...] = (),
        active_plan: PlanningArtifact | None = None,
    ) -> str:
        return self._advisor_component.build_advisor_followup_prompt(
            checkpoint=checkpoint,
            advisor_review=advisor_review,
            pending_tool_names=pending_tool_names,
            active_plan=active_plan,
        )

    def _render_active_plan_for_advisor(self, artifact: PlanningArtifact) -> str:
        return self._advisor_component._render_active_plan_for_advisor(artifact)

    def build_plan_drift_review_context(
        self,
        *,
        active_plan: PlanningArtifact,
        candidate_text: str,
        pending_tool_names: tuple[str, ...] = (),
    ) -> str:
        return self._advisor_component.build_plan_drift_review_context(
            active_plan=active_plan,
            candidate_text=candidate_text,
            pending_tool_names=pending_tool_names,
        )

    def record_plan_drift_context(self, context: str) -> None:
        self._advisor_component.record_plan_drift_context(context)

    def describe_config(self, *, section: str = "summary") -> str:
        if section not in {"summary", "workspace", "runtime", "permissions", "plugins", "mcp"}:
            return "Usage: /config [summary|workspace|runtime|permissions|plugins|mcp]"
        counts = self._mcp_server_counts()
        checklist_stats = self.checklist_stats()
        execution_contract = self.execution_contract_payload()
        effective_cwd = self.state.effective_cwd or str(self.config.cwd)
        workspace_effective_exists = Path(effective_cwd).exists() if effective_cwd else False
        recommended_actions = self._workspace_recommended_actions(
            workspace_health=self.state.workspace_health,
            workspace_label=self.state.workspace_label,
            session_id=self.state.session_id,
        )
        workspace_action_fields = self._workspace_session_action_fields(
            workspace_health=self.state.workspace_health,
            workspace_label=self.state.workspace_label,
            session_id=self.state.session_id,
        )
        workspace_lines = [
            f"cwd: {self.config.cwd}",
            f"original_cwd: {self.state.original_cwd or self.config.transcript_cwd or self.config.cwd}",
            f"effective_cwd: {effective_cwd}",
            f"workspace_mode: {self.state.workspace_mode}",
            f"workspace_label: {self.state.workspace_label or 'none'}",
            f"workspace_created_at: {self.state.workspace_created_at or 'none'}",
            f"workspace_health: {self.state.workspace_health}",
            f"workspace_cleanup_status: {self.state.workspace_cleanup_status}",
            f"workspace_cleanup_error: {self.state.workspace_cleanup_error or 'none'}",
            f"workspace_effective_cwd_exists: {'yes' if workspace_effective_exists else 'no'}",
            f"workspace_unavailable: {'yes' if self.state.workspace_unavailable else 'no'}",
            f"workspace_unavailable_reason: {self.state.workspace_unavailable_reason or 'none'}",
            f"workspace_fallback_cwd: {self.state.workspace_fallback_cwd or 'none'}",
            "workspace_recommended_action: " + (recommended_actions[0] if recommended_actions else "none"),
            "workspace_recommended_actions: "
            + (", ".join(recommended_actions) if recommended_actions else "none"),
            f"selected_workspace_primary_action: {workspace_action_fields['selected_workspace_primary_action']}",
            f"selected_workspace_secondary_action: {workspace_action_fields['selected_workspace_secondary_action']}",
            f"selected_workspace_tertiary_action: {workspace_action_fields['selected_workspace_tertiary_action']}",
            f"selected_workspace_target: {workspace_action_fields['selected_workspace_target']}",
            *self._render_orphaned_workspace_lines(),
            f"primary action: {workspace_action_fields['selected_workspace_primary_action']}",
            f"secondary action: {workspace_action_fields['selected_workspace_secondary_action']}",
            f"tertiary action: {workspace_action_fields['selected_workspace_tertiary_action']}",
        ]
        mcp_lines = [
            f"provider: {self.config.provider}",
            f"mcp_config_path: {self.config.mcp_config_path}",
            f"mcp_servers: {counts['servers']}",
            f"mcp_connected_servers: {counts['connected']}",
            f"mcp_failed_servers: {counts['failed']}",
            f"mcp_retrying_servers: {counts['retrying']}",
        ]
        plugin_lines = [
            f"project_memory: {'loaded' if self.project_context.memory_content else 'none'}",
            f"project_plugins: {len(self.plugin_registry.list_plugins())}",
            f"enabled_plugins: {len(self.plugin_registry.enabled_plugins(self.state))}",
            f"manual_enabled_plugins: {len(self.state.enabled_plugin_names)}",
            f"manual_disabled_plugins: {len(self.state.disabled_plugin_names)}",
            f"project_skills: {len(self.project_context.skills)}",
            f"enabled_skills: {len(self.active_skills())}",
            f"manual_enabled_skills: {len(self.state.enabled_skill_names)}",
            f"manual_disabled_skills: {len(self.state.disabled_skill_names)}",
        ]
        permission_lines = [
            f"permission_config_path: {self._permission_config_path()}",
            f"workspace_permission_rules: {len(self._workspace_permission_rules)}",
            f"session_permission_rules: {len(self.permission_manager.session_rules)}",
            f"permission_mode: {self.config.permission_mode}",
        ]
        runtime_lines = [
            f"provider: {self.config.provider}",
            f"model: {self.config.model}",
            f"advisor_model: {self.state.advisor_model or 'none'}",
            f"advisor_mode: {self.state.advisor_mode}",
            f"advisor_reviews: {len(self.state.advisor_review_history)}",
            f"advisor_blocks: {self.advisor_block_count()}",
            f"planning_artifacts: {len(self.planning_artifacts())}",
            "active_planning_artifact_id: " + str(self.state.active_planning_artifact_id or "none"),
            f"recent_change_sets: {len(self.state.recent_change_sets)}",
            f"redo_change_sets: {len(self.state.undone_change_sets)}",
            f"session_checklist_tasks: {checklist_stats['total']}",
            f"session_checklist_in_progress: {checklist_stats['in_progress']}",
            *self._symbol_surface_config_fields(),
            f"execution_constraints: {self.state.active_execution_constraint}",
            f"session_execution_mode: {execution_contract['session_execution_mode']}",
            "session_command_policy_name: "
            + str(execution_contract["session_command_policy_name"] or "none"),
            "session_command_policy_source: "
            + str(execution_contract["session_command_policy_source"] or "none"),
            "session_command_policy_allowed_tools: "
            + (
                ", ".join(execution_contract["session_command_policy_allowed_tool_names"])
                if execution_contract["session_command_policy_allowed_tool_names"]
                else "none"
            ),
            "session_command_policy_allowed_bash_prefixes: "
            + (
                ", ".join(execution_contract["session_command_policy_allowed_bash_prefixes"])
                if execution_contract["session_command_policy_allowed_bash_prefixes"]
                else "none"
            ),
            "session_command_policy_require_read_only_subagents: "
            + ("yes" if execution_contract["session_command_policy_require_read_only_subagents"] else "no"),
            f"last_plan_drift_summary: {self._recent_plan_drift_summary() or 'none'}",
            f"max_tokens: {self.config.max_tokens}",
            f"max_turns: {self.config.max_turns}",
            f"max_tool_rounds_per_turn: {self.config.max_tool_rounds_per_turn}",
            f"max_history_messages: {self.config.max_history_messages}",
            f"history_keep_last_messages: {self.config.history_keep_last_messages}",
            f"max_context_summary_chars: {self.config.max_context_summary_chars}",
            f"session_id: {self.state.session_id}",
        ]
        planning_lines = self.describe_planning_lifecycle()[2:]
        if section == "workspace":
            return "\n".join(["current session:", *workspace_lines])
        if section == "runtime":
            return "\n".join(["current session:", *runtime_lines, *planning_lines])
        if section == "permissions":
            return "\n".join(["current session:", *permission_lines])
        if section == "plugins":
            return "\n".join(["current session:", *plugin_lines])
        if section == "mcp":
            return "\n".join(["current session:", *mcp_lines])
        lines = [*workspace_lines, *mcp_lines, *plugin_lines, *permission_lines, *runtime_lines]
        lines.extend(planning_lines)
        return "\n".join(lines)

    def describe_saved_sessions(
        self,
        limit: int = 10,
        *,
        selector: str | None = None,
        section: str = "list",
    ) -> str:
        if section not in {"list", "summary", "workspace", "detail"}:
            return "Usage: /sessions [list|show latest|show <session-id-prefix>|show <session-id-prefix> summary|show <session-id-prefix> workspace]"
        if section != "list" or selector is not None:
            if selector is None:
                selector = "latest"
            resolved = self._resolve_saved_session_summary(selector)
            if resolved is None:
                return f'No saved session found for "{selector}".'
            summary, state = resolved
            if section == "workspace":
                return self._render_saved_session_workspace_detail(summary)
            if section == "summary":
                return self._render_saved_session_summary_detail(summary, state, compact=True)
            return self._render_saved_session_summary_detail(summary, state, compact=False)
        return self._describe_saved_sessions_list(limit=limit)

    def _describe_saved_sessions_list(self, *, limit: int = 10) -> str:
        transcripts = list_transcripts(self.config.transcript_cwd or self.config.cwd, limit=limit)
        if not transcripts:
            return "No saved sessions."
        lines = []
        for item in transcripts:
            updated = item.updated_at or item.created_at or "unknown"
            provider = item.provider or "unknown"
            model = item.model or "unknown"
            summary_flag = "yes" if item.context_summary_present else "no"
            workspace_mode = item.workspace_mode or "main"
            workspace_label = item.workspace_label or "-"
            origin_cwd = item.original_cwd or item.cwd or "-"
            effective_cwd = item.effective_cwd or item.cwd or "-"
            workspace_health = item.workspace_health or self._derive_workspace_health(
                workspace_mode=workspace_mode,
                workspace_cleanup_status=item.workspace_cleanup_status or "none",
                workspace_unavailable=bool(item.workspace_unavailable),
            )
            cleanup_status = item.workspace_cleanup_status or "none"
            workspace_bits = [
                f"workspace={workspace_mode}",
                f"health={workspace_health}",
                f"label={workspace_label}",
            ]
            workspace_bits.append(f"origin={origin_cwd}")
            workspace_bits.append(f"cwd={effective_cwd}")
            if workspace_mode != "main":
                try:
                    if not Path(effective_cwd).exists():
                        workspace_bits.append("cwd_exists=no")
                except OSError:
                    workspace_bits.append("cwd_exists=no")
            workspace_bits.append(f"cleanup={cleanup_status}")
            if item.workspace_unavailable:
                workspace_bits.append("unavailable=yes")
                if item.workspace_fallback_cwd:
                    workspace_bits.append(f"fallback={item.workspace_fallback_cwd}")
            recommended_actions = self._workspace_recommended_actions(
                workspace_health=workspace_health,
                workspace_label=None if workspace_label == "-" else workspace_label,
                session_id=item.session_id,
            )
            if recommended_actions:
                workspace_bits.append("actions=" + " | ".join(recommended_actions))
            execution_bits = [f"execution={item.session_execution_mode or 'main'}"]
            if item.session_command_policy_name:
                execution_bits.append(f"policy={item.session_command_policy_name}")
            if item.session_command_policy_require_read_only_subagents:
                execution_bits.append("read_only_subagents=yes")
            lines.append(
                f"{item.session_id}  updated={updated}  provider={provider}  "
                f"model={model}  {'  '.join([*workspace_bits, *execution_bits])}  messages={item.message_count}  "
                f"compacted={summary_flag}  continuation=saved resumable"
            )
        return "\n".join(lines)

    def _saved_resume_semantics(
        self,
        *,
        session_id: str,
        saved_resumable: bool,
        stay_on_surface: str,
    ) -> dict[str, str]:
        semantics = build_continuation_semantics(
            is_live_attachable=False,
            is_saved_resumable=saved_resumable,
            live_attach_command=None,
            resume_session_id=session_id,
            stay_on_surface=stay_on_surface,
        )
        return {
            "continuation_category": semantics.category,
            "go_to_live_attach": semantics.go_to_live_attach,
            "go_to_saved_resume": semantics.go_to_saved_resume,
            "stay_on_surface": semantics.stay_on_surface,
        }

    def _resolve_saved_session_summary(
        self,
        selector: str,
    ) -> tuple[TranscriptSummary, SessionState] | None:
        cwd = self.config.transcript_cwd or self.config.cwd
        summaries = list_transcripts(cwd)
        if not summaries:
            return None
        raw = selector.strip()
        if raw.lower() == "latest":
            state, path = load_latest_transcript(cwd)
            if state is None or path is None:
                return None
            for summary in summaries:
                if summary.path == path:
                    return summary, state
            return None
        exact = next((item for item in summaries if item.session_id == raw), None)
        candidate = exact
        if candidate is None:
            prefix_matches = [item for item in summaries if item.session_id.startswith(raw)]
            candidate = prefix_matches[0] if prefix_matches else None
        if candidate is None:
            contains_matches = [item for item in summaries if raw in item.session_id]
            candidate = contains_matches[0] if contains_matches else None
        if candidate is None:
            return None
        state, _ = load_transcript_by_session_id(cwd, candidate.session_id)
        if state is None:
            return None
        return candidate, state

    def _render_saved_session_workspace_detail(self, summary: TranscriptSummary) -> str:
        action_fields = self._workspace_session_action_fields(
            workspace_health=summary.workspace_health or "healthy",
            workspace_label=summary.workspace_label,
            session_id=summary.session_id,
        )
        lines = [
            "saved session workspace:",
            f"session_id: {summary.session_id}",
            f"workspace mode: {summary.workspace_mode or 'main'}",
            f"workspace health: {summary.workspace_health or 'healthy'}",
            f"workspace label: {summary.workspace_label or 'none'}",
            f"origin cwd: {summary.original_cwd or summary.cwd or '-'}",
            f"effective cwd: {summary.effective_cwd or summary.cwd or '-'}",
            f"fallback cwd: {summary.workspace_fallback_cwd or 'none'}",
            f"workspace unavailable: {'yes' if summary.workspace_unavailable else 'no'}",
            f"unavailable reason: {summary.workspace_unavailable_reason or 'none'}",
            f"workspace cleanup status: {summary.workspace_cleanup_status or 'none'}",
            "primary action: " + action_fields["selected_workspace_primary_action"],
            "secondary action: " + action_fields["selected_workspace_secondary_action"],
            "tertiary action: " + action_fields["selected_workspace_tertiary_action"],
            "next actions:",
            f"- pyclaude --resume-session {summary.session_id} repl",
            f"- pyclaude --resume-session {summary.session_id} tui",
            f"- /sessions show {summary.session_id} summary",
        ]
        return "\n".join(lines)

    def _render_saved_session_summary_detail(
        self,
        summary: TranscriptSummary,
        state: SessionState,
        *,
        compact: bool,
    ) -> str:
        planning_artifacts = (
            state.planning_artifact_history
            if state.planning_artifact_history
            else state.recent_planning_artifacts
        )
        history_state = self._history_state_payload(
            message_count=summary.message_count,
            context_summary_present=summary.context_summary_present,
        )
        continuation = self._saved_resume_semantics(
            session_id=summary.session_id,
            saved_resumable=True,
            stay_on_surface=f"/sessions show {summary.session_id} summary | /sessions show {summary.session_id} workspace",
        )
        explicit_entry_count = len(state.explicit_context_entries)
        unresolved_explicit_count = sum(1 for entry in state.explicit_context_entries if not entry.resolved)
        lines = [
            "saved session:",
            f"session_id: {summary.session_id}",
            "session source: saved transcript",
            f"continuation category: {continuation['continuation_category']}",
            f"provider: {summary.provider or 'unknown'}",
            f"model: {summary.model or 'unknown'}",
            *self._history_state_lines(history_state),
            f"workspace mode: {summary.workspace_mode or 'main'}",
            f"workspace health: {summary.workspace_health or 'healthy'}",
            f"workspace label: {summary.workspace_label or 'none'}",
            f"workspace fallback cwd: {summary.workspace_fallback_cwd or 'none'}",
            f"workspace unavailable: {'yes' if summary.workspace_unavailable else 'no'}",
            f"recorded changes: {len(state.recent_change_sets)}",
            f"redo changes: {len(state.undone_change_sets)}",
            f"planning artifacts: {len(planning_artifacts)}",
            f"advisor activity: {len(state.advisor_review_history)} review(s)",
            f"explicit context entries: {explicit_entry_count}",
            f"unresolved explicit context entries: {unresolved_explicit_count}",
            "task surfaces: "
            + (
                ", ".join(
                    f"{name}={count}"
                    for name, count in sorted(summary.task_surface_counts.items())
                    if count
                )
                if summary.task_surface_counts
                else "none"
            ),
            f"go_to_live_attach: {continuation['go_to_live_attach']}",
            f"go_to_saved_resume: {continuation['go_to_saved_resume']}",
            f"stay_on_surface: {continuation['stay_on_surface']}",
            "next actions:",
            f"- pyclaude --resume-session {summary.session_id} repl",
            f"- pyclaude --resume-session {summary.session_id} tui",
            f"- /sessions show {summary.session_id} workspace",
        ]
        if compact:
            return "\n".join(lines)
        lines[1:1] = [
            f"resume path: pyclaude --resume-session {summary.session_id} repl",
            f"resume tui path: pyclaude --resume-session {summary.session_id} tui",
        ]
        lines.extend(
            [
                f"advisor model: {state.advisor_model or 'none'}",
                f"advisor mode: {state.advisor_mode}",
                f"active planning artifact id: {summary.active_planning_artifact_id or 'none'}",
                f"session execution mode: {summary.session_execution_mode or 'main'}",
                f"session command policy: {summary.session_command_policy_name or 'none'}",
                f"read only subagents: {'yes' if summary.session_command_policy_require_read_only_subagents else 'no'}",
                f"original cwd: {summary.original_cwd or summary.cwd or '-'}",
                f"effective cwd: {summary.effective_cwd or summary.cwd or '-'}",
                f"workspace cleanup status: {summary.workspace_cleanup_status or 'none'}",
                f"workspace unavailable reason: {summary.workspace_unavailable_reason or 'none'}",
            ]
        )
        return "\n".join(lines)

    def describe_status(self, *, section: str = "summary") -> str:
        if section not in {"summary", "workspace", "workflow", "resume"}:
            return "Usage: /status [summary|workspace|workflow|resume]"
        if section == "workspace":
            return self.describe_current_workspace()
        if section == "workflow":
            return self._describe_status_workflow()
        if section == "resume":
            return self._describe_status_resume()
        return self._describe_status_summary()

    def describe_context(self, *, section: str = "summary") -> str:
        if section == "summary":
            return self._describe_context_summary()
        migration_targets = {
            "files": "/files context",
            "working-set": "/files working-set",
            "focused": "/files focused",
            "changes": "/files changes",
            "tasks": "/files tasks",
            "plan": "/files plan",
            "explicit": "/files explicit",
            "auto": "/files auto",
        }
        if section in migration_targets:
            return (
                "/context now shows current context usage.\n"
                f"Use {migration_targets[section]} for the file-scope view that used to live under /context."
            )
        return "Usage: /context [summary]"

    def describe_files(
        self,
        *,
        section: str = "context",
        selected_index: int = 0,
    ) -> str:
        if section not in {"context", "working-set", "focused", "changes", "tasks", "plan", "explicit", "auto", "show"}:
            return "Usage: /files [context|working-set|focused|changes|tasks|plan|explicit|auto|show <n>]"
        if section == "focused":
            return self._describe_files_focused()
        if section == "changes":
            return self._describe_files_changes()
        if section == "tasks":
            return self._describe_files_filtered(reason="active task", label="tasks")
        if section == "plan":
            return self._describe_files_filtered(reason="active plan", label="plan")
        if section == "explicit":
            return self._describe_files_filtered(reason="explicit context path", label="explicit")
        if section == "auto":
            return self._describe_files_auto()
        if section == "show":
            return self._describe_files_show(selected_index=selected_index)
        return self._describe_files_inventory()

    def describe_diff(
        self,
        *,
        section: str = "summary",
        selector: str | None = None,
        file_index: int = 0,
    ) -> str:
        if section not in {"summary", "focused", "working-set", "change"}:
            return "Usage: /diff [summary|focused|working-set|change <index-or-change-id> [file <n>]]"
        if section == "focused":
            return self._describe_diff_focused()
        if section == "working-set":
            return self._describe_diff_working_set()
        if section == "change":
            if not selector:
                return "Usage: /diff [summary|focused|working-set|change <index-or-change-id> [file <n>]]"
            selected_index = self.resolve_change_stack_index(selector, redo=False)
            if selected_index is None:
                return "Usage: /diff [summary|focused|working-set|change <index-or-change-id> [file <n>]]"
            file_count = self.selected_change_file_count(index=selected_index, limit=10, redo=False)
            if file_count > 0 and file_index >= file_count:
                return "Usage: /diff [summary|focused|working-set|change <index-or-change-id> [file <n>]]"
            self.remember_selected_change_context_focus(index=selected_index, file_index=file_index, redo=False)
            return self.selected_change_detail(index=selected_index, file_index=file_index, limit=10, redo=False)
        return self._describe_diff_summary()

    def _describe_status_summary(self) -> str:
        artifact = self.active_planning_artifact()
        working_set = self.working_set_payload(limit=5)
        focused_payload = self._current_context_focus_payload()
        task_surfaces = self.task_surface_counts_payload()
        checklist_stats = self.checklist_stats()
        explicit_entries = self._explicit_context_entries_payloads()
        file_items = [
            item for item in working_set.get("file_context_files", []) if isinstance(item, dict)
        ]
        _focused_files, _focused_index, focused_item = self._file_context_items_and_index(focused_payload)
        explicit_counts = self._explicit_context_summary_counts(
            entries=explicit_entries,
            files=file_items,
            total_file_count=len(file_items),
        )
        history_state = self._history_state_payload(
            message_count=len(self.state.messages),
            context_summary=self.state.context_summary,
        )
        focused_path = str(focused_item.get("path") or "none") if focused_item is not None else "none"
        focused_source = str(focused_item.get("source") or "none") if focused_item is not None else "none"
        file_count = int(working_set.get("file_context_file_count") or 0)
        active_task_total = sum(
            count
            for key, count in task_surfaces.items()
            if key not in {"completed", "failed", "blocked", "stopped"}
        )
        lines = [
            "current session:",
            f"session_id: {self.state.session_id}",
            f"provider: {self.config.provider}",
            f"model: {self.config.model}",
            f"workspace health: {self.state.workspace_health}",
            f"workspace mode: {self.state.workspace_mode}",
            f"active plan: {artifact.goal if artifact is not None else 'none'}",
            f"advisor mode: {self.state.advisor_mode}",
            *self._history_state_lines(history_state),
            f"recorded changes: {len(self.state.recent_change_sets)}",
            f"redo changes: {len(self.state.undone_change_sets)}",
            f"working set files: {file_count}",
            self._render_file_context_mix_line(file_items),
            f"focused file: {focused_path}",
            f"focused file source: {focused_source}",
            f"explicit context entries: {explicit_counts['entry_count']}",
            f"unresolved explicit context entries: {explicit_counts['unresolved_entry_count']}",
            f"explicit-context files: {explicit_counts['explicit_file_count']}",
            f"active task surfaces: {active_task_total}",
            f"checklist in progress: {checklist_stats['in_progress']}",
            "next actions:",
            "- /files focused",
            "- /diff focused",
            "- /tasks active",
            "- /changes working-set",
            "- /plan",
            "- /add-dir list",
            "- /files explicit",
            "- /workspaces current",
            "- /history all",
        ]
        return "\n".join(lines)

    def _describe_context_summary(self) -> str:
        return render_context_usage(collect_context_usage(self))

    def _describe_context_inventory(self) -> str:
        payload = self.working_set_payload(limit=20)
        return self._render_context_inventory_text(payload, filter_label="all")

    def _describe_context_filtered(self, *, reason: str, label: str) -> str:
        payload = self._filtered_working_set_payload(reason=reason)
        return self._render_context_inventory_text(payload, filter_label=label)

    def _describe_context_auto(self) -> str:
        payload = self.working_set_payload(limit=20)
        files = [item for item in payload.get("file_context_files", []) if isinstance(item, dict)]
        filtered = [
            item
            for item in files
            if "explicit context path" not in self._file_context_scope_reasons(item)
        ]
        return self._render_context_inventory_text(
            self._file_context_payload_from_files(filtered, scope="session"),
            filter_label="auto",
        )

    def _describe_context_focused(self) -> str:
        payload = self._current_context_focus_payload()
        files, _index, focused_item = self._file_context_items_and_index(payload)
        if not files or focused_item is None:
            return "\n".join(
                [
                    "focused file context:",
                    "No focused file context.",
                    "next actions:",
                    "- /files context",
                    "- /status workflow",
                ]
            )
        lines = self._render_context_focused_lines(payload, title="focused file")
        return "\n".join(lines) if lines else "No focused file context."

    def _working_set_files_payload(self) -> dict[str, Any]:
        return self.working_set_payload(limit=20)

    def _diff_working_set_payload(self) -> dict[str, Any]:
        payload = self._working_set_files_payload()
        files = [item for item in payload.get("file_context_files", []) if isinstance(item, dict)]
        filtered = [
            item
            for item in files
            if bool(item.get("has_diff_hunks")) or self._file_context_diff_hunk_count(item) > 0
        ]
        return self._file_context_payload_from_files(filtered, scope="session")

    def _render_files_inventory_text(
        self,
        payload: dict[str, Any] | None,
        *,
        filter_label: str,
    ) -> str:
        files = [item for item in (payload or {}).get("file_context_files", []) if isinstance(item, dict)]
        lines = [
            "working set files:",
            f"filter: {filter_label}",
            f"file_count: {len(files)}",
        ]
        if not files:
            return "\n".join(lines + ["No matching working-set files."])
        lines.append(self._render_file_context_mix_line(files))
        for index, item in enumerate(files, start=1):
            scope_reasons = self._file_context_scope_reasons(item)
            related_change = str(item.get("change_id") or "").strip() or "none"
            lines.append(
                f"{index}. {item['path']}  "
                + f"in_scope_because={', '.join(scope_reasons) if scope_reasons else 'none'}  "
                + f"related_change={related_change}  "
                + f"diff_hunks={self._file_context_diff_hunk_count(item)}  "
                + f"context_only={'yes' if self._file_context_is_context_only(item) else 'no'}"
            )
            lines.append(
                "   next_actions: "
                + self._render_file_context_action_group_summary(
                    item,
                    stay_on_surface_actions=self._files_stay_on_surface_actions(),
                    extra_actions={"go_to_context": self._file_context_context_actions(item)},
                    ordered_keys=(
                        "go_to_change",
                        "go_to_task",
                        "go_to_plan",
                        "go_to_context",
                        "stay_on_surface",
                    ),
                )
            )
        return "\n".join(lines)

    def _describe_files_inventory(self) -> str:
        return self._render_files_inventory_text(
            self._working_set_files_payload(),
            filter_label="all",
        )

    def _describe_files_changes(self) -> str:
        payload = self._working_set_files_payload()
        files = [item for item in payload.get("file_context_files", []) if isinstance(item, dict)]
        filtered = [
            item
            for item in files
            if bool(item.get("has_related_change")) or bool(item.get("has_diff_hunks"))
        ]
        filtered_payload = self._reorder_payload_to_current_focus(
            self._file_context_payload_from_files(filtered, scope="session"),
            required_reason="recent change",
        )
        return self._render_files_inventory_text(
            filtered_payload,
            filter_label="changes",
        )

    def _describe_files_filtered(self, *, reason: str, label: str) -> str:
        payload = self._reorder_payload_to_current_focus(
            self._filtered_working_set_payload(reason=reason),
            required_reason=reason,
        )
        return self._render_files_inventory_text(
            payload,
            filter_label=label,
        )

    def _describe_files_auto(self) -> str:
        payload = self._working_set_files_payload()
        files = [item for item in payload.get("file_context_files", []) if isinstance(item, dict)]
        filtered = [
            item
            for item in files
            if "explicit context path" not in self._file_context_scope_reasons(item)
        ]
        filtered_payload = self._reorder_payload_to_current_focus(
            self._file_context_payload_from_files(filtered, scope="session"),
        )
        return self._render_files_inventory_text(
            filtered_payload,
            filter_label="auto",
        )

    def _describe_files_focused(self) -> str:
        payload = self._current_context_focus_payload()
        files, _index, focused_item = self._file_context_items_and_index(payload)
        if not files or focused_item is None:
            return "\n".join(
                [
                    "focused file:",
                    "No focused file context.",
                    "next_actions:",
                    "- /files",
                    "- /status workflow",
                ]
            )
        lines = self._render_context_focused_lines(payload, title="focused file")
        return "\n".join(lines) if lines else "No focused file context."

    def _describe_files_show(self, *, selected_index: int) -> str:
        payload = self._working_set_files_payload()
        files, _bounded_index, focused_item = self._file_context_items_and_index(
            payload,
            selected_index=selected_index,
        )
        if not files or focused_item is None or selected_index >= len(files):
            return "Usage: /files [context|working-set|focused|changes|tasks|plan|explicit|auto|show <n>]"
        reordered = self._reordered_file_context_payload(payload, selected_index=selected_index)
        lines = self._render_context_focused_lines(reordered, title="focused file")
        return "\n".join(lines) if lines else "Usage: /files [context|working-set|focused|changes|tasks|plan|explicit|auto|show <n>]"

    def _describe_diff_summary(self) -> str:
        payload = self._working_set_files_payload()
        files = [item for item in payload.get("file_context_files", []) if isinstance(item, dict)]
        diff_backed_count = sum(
            1 for item in files if bool(item.get("has_diff_hunks")) or self._file_context_diff_hunk_count(item) > 0
        )
        focused_payload = self._current_context_focus_payload()
        _focused_files, _focused_index, focused_item = self._file_context_items_and_index(focused_payload)
        focused_path = str(focused_item.get("path") or "none") if focused_item is not None else "none"
        focused_diff_hunks = self._file_context_diff_hunk_count(focused_item) if focused_item is not None else 0
        lines = [
            "diff summary:",
            f"recorded undo-stack changes: {len(self.state.recent_change_sets)}",
            f"recorded redo-stack changes: {len(self.state.undone_change_sets)}",
            f"diff-backed working-set files: {diff_backed_count}",
            self._render_file_context_mix_line(files),
            f"focused file: {focused_path}",
            f"focused diff hunks: {focused_diff_hunks}",
            "next actions:",
            "- /diff focused",
            "- /diff working-set",
            "- /files focused",
            "- /changes working-set",
        ]
        return "\n".join(lines)

    def _describe_diff_focused(self) -> str:
        payload = self._current_context_focus_payload()
        files, _index, focused_item = self._file_context_items_and_index(payload)
        if not files or focused_item is None:
            return "\n".join(
                [
                    "focused diff:",
                    "No focused file context.",
                    "next actions:",
                    "- /diff working-set",
                    "- /status workflow",
                ]
            )
        lines = self._render_context_focused_lines(payload, title="focused file")
        actions = self._file_context_item_action_groups(
            focused_item,
            stay_on_surface_actions=self._diff_stay_on_surface_actions(),
        )
        if lines:
            lines[-1] = "- stay_on_surface: " + (
                " | ".join(actions["stay_on_surface"]) if actions["stay_on_surface"] else "none"
            )
        if self._file_context_diff_hunk_count(focused_item) <= 0:
            lines.append("- diff status: no diff hunks on focused file")
            return "\n".join(lines)
        return "\n".join(lines)

    def _describe_diff_working_set(self) -> str:
        payload = self._reorder_payload_to_current_focus(
            self._diff_working_set_payload(),
            required_reason="recent change",
        )
        files = [item for item in payload.get("file_context_files", []) if isinstance(item, dict)]
        lines = [
            "diff-backed working set:",
            f"file_count: {len(files)}",
        ]
        if not files:
            return "\n".join(lines + ["No diff-backed working-set files."])
        lines.append(self._render_file_context_mix_line(files))
        for index, item in enumerate(files, start=1):
            related_change = str(item.get("change_id") or "").strip() or "none"
            lines.append(f"{index}. {item['path']}")
            lines.append(f"- related change: {related_change}")
            lines.append(f"- diff hunks: {self._file_context_diff_hunk_count(item)}")
            lines.extend(
                self._render_file_context_action_group_lines(
                    item,
                    stay_on_surface_actions=self._diff_stay_on_surface_actions(),
                    line_prefix="- ",
                    ordered_keys=("go_to_change", "go_to_task", "go_to_plan", "stay_on_surface"),
                )
            )
            if index < len(files):
                lines.append("")
        return "\n".join(lines)

    def _describe_status_workflow(self) -> str:
        task_surfaces = self.task_surface_counts_payload()
        artifact = self.active_planning_artifact()
        explicit_entries = self._explicit_context_entries_payloads()
        working_set_payload = self.working_set_payload(limit=3)
        focused_payload = self._current_context_focus_payload()
        file_items = [
            item for item in working_set_payload.get("file_context_files", []) if isinstance(item, dict)
        ]
        _focused_files, _focused_index, focused_item = self._file_context_items_and_index(focused_payload)
        explicit_counts = self._explicit_context_summary_counts(
            entries=explicit_entries,
            files=file_items,
            total_file_count=len(file_items),
        )
        history_state = self._history_state_payload(
            message_count=len(self.state.messages),
            context_summary=self.state.context_summary,
        )
        task_surface_summary = (
            ", ".join(f"{name}={count}" for name, count in sorted(task_surfaces.items()) if count)
            if task_surfaces
            else "none"
        )
        lines = ["workflow status:"]
        lines.extend(self._history_state_lines(history_state))
        lines.extend(
            self.render_summary_field_lines(
                [
                    ("recorded changes", len(self.state.recent_change_sets)),
                    ("redo changes", len(self.state.undone_change_sets)),
                    ("task surfaces", task_surface_summary),
                    ("planning artifacts", len(self.planning_artifacts())),
                    ("active plan goal", artifact.goal if artifact is not None else "none"),
                    ("advisor activity", len(self.state.advisor_review_history)),
                    ("explicit context entries", explicit_counts["entry_count"]),
                    ("unresolved explicit context entries", explicit_counts["unresolved_entry_count"]),
                    ("explicit-context files", explicit_counts["explicit_file_count"]),
                ],
            )
        )
        lines.append(self._render_file_context_mix_line(file_items))
        if focused_item is not None:
            lines.extend(
                self.render_summary_field_lines(
                    [
                        ("focused file", str(focused_item.get("path") or "none")),
                        ("focused file source", str(focused_item.get("source") or "none")),
                        ("focused diff hunks", self._file_context_diff_hunk_count(focused_item)),
                    ],
                )
            )
        working_set_lines = self._render_file_context_lines(
            working_set_payload,
            title="Working set",
        )
        if working_set_lines:
            lines.append("")
            lines.extend(working_set_lines)
        status_action_groups = (
            self._file_context_item_action_groups(
                focused_item,
                stay_on_surface_actions=[
                    "/status workflow",
                    "/files focused",
                    "/diff focused",
                    "/changes working-set",
                    "/files explicit",
                    "/files context",
                ],
            )
            if focused_item is not None
            else {
                "go_to_change": [],
                "go_to_task": [],
                "go_to_plan": [],
                "stay_on_surface": [
                    "/status workflow",
                    "/files focused",
                    "/diff focused",
                    "/changes working-set",
                    "/files explicit",
                    "/files context",
                ],
            }
        )
        status_action_groups["go_to_change"] = self._dedupe_action_commands(
            [*status_action_groups.get("go_to_change", []), "/changes working-set"]
        )
        status_action_groups["go_to_task"] = self._dedupe_action_commands(
            [*status_action_groups.get("go_to_task", []), "/tasks active"]
        )
        status_action_groups["go_to_plan"] = self._dedupe_action_commands(
            [*status_action_groups.get("go_to_plan", []), "/plan"]
        )
        lines.append("")
        lines.extend(
            self._render_action_group_lines(
                status_action_groups,
                ordered_keys=("go_to_change", "go_to_task", "go_to_plan", "stay_on_surface"),
            )
        )
        return "\n".join(lines)

    def _describe_status_resume(self) -> str:
        session_id = self.state.session_id
        history_state = self._history_state_payload(
            message_count=len(self.state.messages),
            context_summary=self.state.context_summary,
        )
        saved_summary = self.describe_saved_sessions(selector=session_id, section="summary")
        saved_resumable = not saved_summary.startswith("No saved session found")
        continuation = self._saved_resume_semantics(
            session_id=session_id,
            saved_resumable=saved_resumable,
            stay_on_surface="/status resume | /sessions show latest",
        )
        resume_path = f"pyclaude --resume-session {session_id} repl" if saved_resumable else "unavailable"
        resume_tui_path = f"pyclaude --resume-session {session_id} tui" if saved_resumable else "unavailable"
        lines = [
            "resume status:",
            f"current session_id: {session_id}",
            *self._history_state_lines(history_state),
            f"resume path: {resume_path}",
            f"resume tui path: {resume_tui_path}",
            f"continuation category: {continuation['continuation_category']}",
            f"go_to_live_attach: {continuation['go_to_live_attach']}",
            f"go_to_saved_resume: {continuation['go_to_saved_resume']}",
            f"stay_on_surface: {continuation['stay_on_surface']}",
        ]
        if saved_resumable:
            lines.extend(["", saved_summary])
        else:
            lines.extend(
                [
                    "saved session: not yet persisted",
                    "next actions:",
                    "- /status resume",
                    "- /sessions show latest",
                ]
            )
        return "\n".join(lines)

    def get_python_symbol_index(self) -> PythonProjectIndex:
        return self._runtime_context.get_python_symbol_index()

    def get_js_ts_symbol_index(self) -> JsTsProjectIndex:
        return self._runtime_context.get_js_ts_symbol_index()

    def locate_symbol(
        self,
        symbol: str,
        *,
        path: str = ".",
        max_results: int = 50,
    ) -> SymbolLookupResult:
        return self._symbol_surface_component.locate_symbol(
            symbol,
            path=path,
            max_results=max_results,
        )

    def collect_references(
        self,
        symbol: str,
        *,
        path: str = ".",
        scope: str = "auto",
        max_results: int = 100,
    ) -> ReferenceLookupResult:
        return self._symbol_surface_component.collect_references(
            symbol,
            path=path,
            scope=scope,
            max_results=max_results,
        )

    def build_open_file_target(
        self,
        path: str,
        *,
        line: int = 1,
        column: int = 1,
        end_line: int | None = None,
        end_column: int | None = None,
        label: str = "",
    ) -> EditorTarget:
        return self._symbol_surface_component.build_open_file_target(
            path,
            line=line,
            column=column,
            end_line=end_line,
            end_column=end_column,
            label=label,
        )

    def build_symbol_target(
        self,
        symbol: str,
        *,
        path: str = ".",
        match_index: int = 0,
    ) -> EditorTarget:
        return self._symbol_surface_component.build_symbol_target(
            symbol,
            path=path,
            match_index=match_index,
        )

    def build_diff_targets(self, path: str, *, before: str, after: str) -> DiffTargetResult:
        return self._symbol_surface_component.build_diff_targets(
            path,
            before=before,
            after=after,
        )

    def build_reference_targets(
        self,
        symbol: str,
        *,
        path: str = ".",
        scope: str = "auto",
        max_results: int = 100,
    ) -> ReferenceTargetResult:
        return self._symbol_surface_component.build_reference_targets(
            symbol,
            path=path,
            scope=scope,
            max_results=max_results,
        )

    def build_symbol_action_bundle(
        self,
        symbol: str,
        *,
        path: str = ".",
        scope: str = "workspace",
        max_definition_results: int = 50,
        max_reference_results: int = 100,
    ) -> SymbolActionBundle:
        return self._symbol_surface_component.build_symbol_action_bundle(
            symbol,
            path=path,
            scope=scope,
            max_definition_results=max_definition_results,
            max_reference_results=max_reference_results,
        )

    def _copy_jsonish_payload(self, payload: dict[str, Any] | None) -> dict[str, Any] | None:
        return self._symbol_surface_component._copy_jsonish_payload(payload)

    def _remember_symbol_surface(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._symbol_surface_component._remember_symbol_surface(payload)

    def _format_editor_target_summary(self, target: dict[str, Any] | None) -> str:
        return self._symbol_surface_component._format_editor_target_summary(target)

    def _format_symbol_candidate_summary(self, item: dict[str, Any] | None) -> str:
        if not isinstance(item, dict):
            return "none"
        if "action" in item:
            return self._format_editor_target_summary(item)
        path = str(item.get("path") or "").strip()
        line = int(item.get("line") or 1)
        symbol = str(item.get("symbol") or "").strip()
        kind = str(item.get("kind") or "").strip()
        text = str(item.get("text") or "").strip()
        label_parts = [part for part in (kind, symbol) if part]
        label = " ".join(label_parts).strip() or text
        summary = f"{path}:{line}" if path else symbol or "candidate"
        if label:
            summary = f"{summary} ({label})"
        return summary

    def _render_symbol_candidate_lines(
        self,
        *,
        title: str,
        items: list[dict[str, Any]],
        selected_index: Any,
    ) -> list[str]:
        return self._symbol_surface_component._render_symbol_candidate_lines(
            title=title,
            items=items,
            selected_index=selected_index,
        )

    def _symbol_surface_action_bundle_for_payload(self, payload: dict[str, Any] | None) -> dict[str, str] | None:
        return self._symbol_surface_component._symbol_surface_action_bundle_for_payload(payload)

    def current_symbol_surface_payload(self) -> dict[str, Any] | None:
        return self._symbol_surface_component.current_symbol_surface_payload()

    def current_symbol_surface_action_bundle(self) -> dict[str, str] | None:
        return self._symbol_surface_component.current_symbol_surface_action_bundle()

    def _selected_symbol_navigation_target(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        return self._symbol_surface_component._selected_symbol_navigation_target(payload)

    def _select_symbol_navigation_target(
        self,
        payload: dict[str, Any],
        target: dict[str, Any],
    ) -> dict[str, Any]:
        return self._symbol_surface_component._select_symbol_navigation_target(payload, target)

    def _cycle_symbol_index(self, current: Any, count: int, delta: int) -> int | None:
        return self._symbol_surface_component._cycle_symbol_index(current, count, delta)

    def _symbol_lookup_target_from_payload(self, payload: dict[str, Any], index: int) -> dict[str, Any] | None:
        return self._symbol_surface_component._symbol_lookup_target_from_payload(payload, index)

    def _update_symbol_lookup_selection(self, *, delta: int) -> str:
        return self._symbol_surface_component._update_symbol_lookup_selection(delta=delta)

    def _update_symbol_reference_selection(self, *, delta: int) -> str:
        return self._symbol_surface_component._update_symbol_reference_selection(delta=delta)

    def _update_symbol_definition_selection(self, *, delta: int) -> str:
        return self._symbol_surface_component._update_symbol_definition_selection(delta=delta)

    def symbol_surface_select_next_match(self) -> str:
        return self._symbol_surface_component.symbol_surface_select_next_match()

    def symbol_surface_select_prev_match(self) -> str:
        return self._symbol_surface_component.symbol_surface_select_prev_match()

    def symbol_surface_select_next_definition(self) -> str:
        return self._symbol_surface_component.symbol_surface_select_next_definition()

    def symbol_surface_select_prev_definition(self) -> str:
        return self._symbol_surface_component.symbol_surface_select_prev_definition()

    def symbol_surface_select_next_reference(self) -> str:
        return self._symbol_surface_component.symbol_surface_select_next_reference()

    def symbol_surface_select_prev_reference(self) -> str:
        return self._symbol_surface_component.symbol_surface_select_prev_reference()

    def _editor_target_payload(self, target: EditorTarget | None) -> dict[str, Any] | None:
        if target is None:
            return None
        return target.to_dict()

    def locate_symbol_surface_payload(
        self,
        symbol: str,
        *,
        path: str = ".",
        max_results: int = 50,
    ) -> dict[str, Any]:
        return self._symbol_surface_component.locate_symbol_surface_payload(
            symbol,
            path=path,
            max_results=max_results,
        )

    def collect_references_surface_payload(
        self,
        symbol: str,
        *,
        path: str = ".",
        scope: str = "auto",
        max_results: int = 100,
    ) -> dict[str, Any]:
        return self._symbol_surface_component.collect_references_surface_payload(
            symbol,
            path=path,
            scope=scope,
            max_results=max_results,
        )

    def build_symbol_action_surface_payload(
        self,
        symbol: str,
        *,
        path: str = ".",
        scope: str = "workspace",
        max_definition_results: int = 50,
        max_reference_results: int = 100,
    ) -> dict[str, Any]:
        return self._symbol_surface_component.build_symbol_action_surface_payload(
            symbol,
            path=path,
            scope=scope,
            max_definition_results=max_definition_results,
            max_reference_results=max_reference_results,
        )

    def _render_symbol_surface_text(self, payload: dict[str, Any] | None) -> str:
        return self._symbol_surface_component._render_symbol_surface_text(payload)

    def describe_current_symbol_surface(self) -> str:
        return self._symbol_surface_component.describe_current_symbol_surface()

    def describe_symbol_lookup_surface(
        self,
        symbol: str,
        *,
        path: str = ".",
        max_results: int = 50,
    ) -> str:
        return self._symbol_surface_component.describe_symbol_lookup_surface(
            symbol,
            path=path,
            max_results=max_results,
        )

    def describe_symbol_reference_surface(
        self,
        symbol: str,
        *,
        path: str = ".",
        scope: str = "auto",
        max_results: int = 100,
    ) -> str:
        return self._symbol_surface_component.describe_symbol_reference_surface(
            symbol,
            path=path,
            scope=scope,
            max_results=max_results,
        )

    def describe_symbol_action_surface(
        self,
        symbol: str,
        *,
        path: str = ".",
        scope: str = "workspace",
        max_definition_results: int = 50,
        max_reference_results: int = 100,
    ) -> str:
        return self._symbol_surface_component.describe_symbol_action_surface(
            symbol,
            path=path,
            scope=scope,
            max_definition_results=max_definition_results,
            max_reference_results=max_reference_results,
        )

    def _open_symbol_surface_target(self, target: dict[str, Any] | None) -> str:
        return self._symbol_surface_component._open_symbol_surface_target(target)

    def symbol_surface_primary_action(self) -> str:
        return self._symbol_surface_component.symbol_surface_primary_action()

    def symbol_surface_secondary_action(self) -> str:
        return self._symbol_surface_component.symbol_surface_secondary_action()

    def clear_symbol_surface(self) -> str:
        return self._symbol_surface_component.clear_symbol_surface()

    def _symbol_surface_config_fields(self) -> list[str]:
        return self._symbol_surface_component._symbol_surface_config_fields()

    def clear_history(self) -> None:
        self.state.messages.clear()
        self.state.context_summary = None
        self.persist_state()

    def clear_session_reset(self) -> dict[str, Any]:
        self.persist_state()
        old_session_id = self.state.session_id
        preserved_task_records = deepcopy(self.state.saved_task_records)
        preserved_task_surface_counts = deepcopy(self.state.saved_task_surface_counts)
        self._runtime_context.task_manager = (
            TaskManager.from_snapshot(preserved_task_records)
            if preserved_task_records
            else TaskManager()
        )
        self.state = self._build_fresh_session_state(
            preserved_task_records=preserved_task_records,
            preserved_task_surface_counts=preserved_task_surface_counts,
        )
        self._session_checklist = SessionChecklistStore(
            self.config.transcript_cwd or self.config.cwd,
            session_id=self.state.session_id,
        )
        self._latest_checklist_duplicate_guard = None
        self._current_symbol_surface = None
        self._current_change_focus_payload = None
        self._current_task_focus_payload = None
        self._current_plan_focus_payload = None
        self._last_project_context_reload = None
        self.persist_state()
        transcript_root = self.config.transcript_cwd or self.config.cwd
        transcript_path = get_session_path(transcript_root, self.state.session_id)
        return {
            "old_session_id": old_session_id,
            "session_id": self.state.session_id,
            "transcript_path": str(transcript_path),
            "text": (
                "Started a fresh local session.\n"
                f"old_session_id: {old_session_id}\n"
                f"new_session_id: {self.state.session_id}"
            ),
        }

    def _build_fresh_session_state(
        self,
        *,
        preserved_task_records: list[dict[str, object]],
        preserved_task_surface_counts: dict[str, int],
    ) -> SessionState:
        previous = self.state
        return SessionState(
            session_execution_mode=previous.session_execution_mode,
            session_command_policy_name=previous.session_command_policy_name,
            session_command_policy_source=previous.session_command_policy_source,
            session_command_policy_allowed_tool_names=list(previous.session_command_policy_allowed_tool_names),
            session_command_policy_allowed_bash_prefixes=list(previous.session_command_policy_allowed_bash_prefixes),
            session_command_policy_require_read_only_subagents=(
                previous.session_command_policy_require_read_only_subagents
            ),
            original_cwd=previous.original_cwd,
            effective_cwd=previous.effective_cwd,
            workspace_mode=previous.workspace_mode,
            workspace_label=previous.workspace_label,
            workspace_created_at=previous.workspace_created_at,
            workspace_health=previous.workspace_health,
            workspace_cleanup_status=previous.workspace_cleanup_status,
            workspace_cleanup_error=previous.workspace_cleanup_error,
            workspace_unavailable=previous.workspace_unavailable,
            workspace_unavailable_reason=previous.workspace_unavailable_reason,
            workspace_fallback_cwd=previous.workspace_fallback_cwd,
            advisor_model=previous.advisor_model,
            advisor_mode=previous.advisor_mode,
            active_execution_constraint=previous.active_execution_constraint,
            constraint_source=previous.constraint_source,
            constraint_reason=previous.constraint_reason,
            enabled_plugin_names=list(previous.enabled_plugin_names),
            disabled_plugin_names=list(previous.disabled_plugin_names),
            enabled_skill_names=list(previous.enabled_skill_names),
            disabled_skill_names=list(previous.disabled_skill_names),
            session_permission_rules=deepcopy(previous.session_permission_rules),
            activated_deferred_tool_names=list(previous.activated_deferred_tool_names),
            saved_task_records=preserved_task_records,
            saved_task_surface_counts=preserved_task_surface_counts,
        )

    def clear_change_history(self) -> str:
        self.state.recent_change_sets.clear()
        self.state.undone_change_sets.clear()
        self._current_change_focus_payload = None
        self.persist_state()
        return "Cleared recorded workspace changes for this session."

    def clear_session_workflow_state(self) -> str:
        self.clear_history()
        self.state.recent_change_sets.clear()
        self.state.undone_change_sets.clear()
        self._current_symbol_surface = None
        self._current_change_focus_payload = None
        self._current_task_focus_payload = None
        self._current_plan_focus_payload = None
        self.state.last_symbol_surface_payload = None
        self.persist_state()
        return (
            "Cleared session workflow state: history, recorded changes, and symbol surface."
        )

    def record_workspace_change(
        self,
        *,
        tool_name: str,
        summary: str,
        file_changes: list[WorkspaceFileChange],
        change_kind: str = "workspace_change",
        undoable: bool = True,
    ) -> None:
        if not file_changes:
            return
        self.state.recent_change_sets.append(
            WorkspaceChangeSet(
                tool_name=tool_name,
                summary=summary,
                change_kind=change_kind,
                undoable=undoable,
                files=file_changes,
            )
        )
        self.state.recent_change_sets = self.state.recent_change_sets[-10:]
        self.state.undone_change_sets.clear()
        self.persist_state()

    def record_workspace_audit_event(
        self,
        *,
        tool_name: str,
        summary: str,
        file_changes: list[WorkspaceFileChange],
        audit_kind: str = "workspace_audit",
    ) -> None:
        self.record_workspace_change(
            tool_name=tool_name,
            summary=summary,
            file_changes=file_changes,
            change_kind=audit_kind,
            undoable=False,
        )

    def undo_last_change(self, args: str = "") -> str:
        if not self.state.recent_change_sets:
            return "No recorded workspace changes to undo."
        selection = self._select_changes(
            self.state.recent_change_sets,
            args=args,
            command="/undo",
        )
        if isinstance(selection, str):
            return selection
        non_undoable = [change for change in selection if not change.undoable]
        if non_undoable:
            change = non_undoable[0]
            return f'Change "{change.change_id[:8]}" is audit-only and cannot be undone.'
        undone: list[WorkspaceChangeSet] = []
        for change in selection:
            self.state.recent_change_sets.remove(change)
            self._apply_change_set(change, direction="undo")
            self.state.undone_change_sets.append(change)
            undone.append(change)
        self.state.undone_change_sets = self.state.undone_change_sets[-10:]
        self.persist_state()
        return self._summarize_change_operation("Undid", undone)

    def redo_last_undo(self, args: str = "") -> str:
        if not self.state.undone_change_sets:
            return "No undone workspace changes to redo."
        selection = self._select_changes(
            self.state.undone_change_sets,
            args=args,
            command="/redo",
        )
        if isinstance(selection, str):
            return selection
        non_undoable = [change for change in selection if not change.undoable]
        if non_undoable:
            change = non_undoable[0]
            return f'Change "{change.change_id[:8]}" is audit-only and cannot be redone.'
        redone: list[WorkspaceChangeSet] = []
        for change in selection:
            self.state.undone_change_sets.remove(change)
            self._apply_change_set(change, direction="redo")
            self.state.recent_change_sets.append(change)
            redone.append(change)
        self.state.recent_change_sets = self.state.recent_change_sets[-10:]
        self.persist_state()
        return self._summarize_change_operation("Redid", redone)

    def reload_project_context(self) -> str:
        before = self._project_context_reload_snapshot()
        try:
            self._refresh_plugin_runtime()
            self.persist_state()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self._record_project_context_reload_result(before=before, error=error)
            return f"Failed to reload project context. error={error}"
        self._record_project_context_reload_result(before=before)
        memory_status = "loaded" if self.project_context.memory_content else "none"
        status = self._last_project_context_reload or {}
        return (
            "Reloaded project context. "
            f"memory={memory_status} skills={len(self.project_context.skills)} "
            f"enabled={len(self.active_skills())} "
            f"memory_changed={self._yes_no(status.get('memory_changed'))} "
            f"skill_set_changed={self._yes_no(status.get('skill_set_changed'))} "
            f"plugin_state_changed={self._yes_no(status.get('plugin_state_changed'))}"
        )

    def add_permission_rule(self, decision: str, scope: str, value: str) -> str:
        try:
            rule = PermissionRule(
                decision=PermissionDecision(decision),
                scope=PermissionRuleScope(scope),
                value=value.strip(),
            )
        except ValueError:
            return "Usage: /permissions <allow|ask|deny> <tool|shell|path|risk> <value>"
        if not rule.value:
            return "Permission rule value cannot be empty."
        current = list(self.permission_manager.session_rules)
        if rule in current:
            return f"Permission rule already exists: {rule.describe()}"
        current.append(rule)
        self.permission_manager.set_session_rules(current)
        self.state.session_permission_rules = [
            permission_rule_to_dict(item) for item in current
        ]
        self.persist_state()
        return f"Added session permission rule: {rule.describe()}"

    def remove_permission_rule(self, source: str, index: int) -> str:
        if source == "workspace":
            current = list(self._workspace_permission_rules)
            if not 1 <= index <= len(current):
                return f"Workspace rule index out of range: {index}"
            removed = current.pop(index - 1)
            self._workspace_permission_rules = current
            self.permission_manager.set_workspace_rules(current)
            save_permission_rules(self.config.cwd, current, config_path=self._permission_config_path())
            return f"Removed workspace permission rule: {removed.describe()}"
        if source == "session":
            current = list(self.permission_manager.session_rules)
            if not 1 <= index <= len(current):
                return f"Session rule index out of range: {index}"
            removed = current.pop(index - 1)
            self.permission_manager.set_session_rules(current)
            self.state.session_permission_rules = [
                permission_rule_to_dict(item) for item in current
            ]
            self.persist_state()
            return f"Removed session permission rule: {removed.describe()}"
        return 'Usage: /permissions remove <session|workspace> <index>'

    def clear_permission_rules(self, scope: str = "session") -> str:
        target = scope.strip().lower() or "session"
        if target == "session":
            self.permission_manager.set_session_rules([])
            self.state.session_permission_rules = []
            self.persist_state()
            return "Cleared session permission rules."
        if target == "workspace":
            self._workspace_permission_rules = []
            self.permission_manager.set_workspace_rules([])
            save_permission_rules(self.config.cwd, [], config_path=self._permission_config_path())
            return "Cleared workspace permission rules."
        if target == "all":
            self._workspace_permission_rules = []
            self.permission_manager.clear_rules()
            self.state.session_permission_rules = []
            save_permission_rules(self.config.cwd, [], config_path=self._permission_config_path())
            self.persist_state()
            return "Cleared workspace and session permission rules."
        return 'Usage: /permissions clear [session|workspace|all]'

    def reload_permission_rules(self) -> str:
        self._restore_permission_rules(reload_workspace_only=True)
        return (
            "Reloaded permission rules. "
            f"workspace={len(self._workspace_permission_rules)} "
            f"session={len(self.permission_manager.session_rules)}"
        )

    def save_permission_rules(self) -> str:
        merged: list[PermissionRule] = []
        for rule in [*self._workspace_permission_rules, *self.permission_manager.session_rules]:
            if rule not in merged:
                merged.append(rule)
        path = save_permission_rules(
            self.config.cwd,
            merged,
            config_path=self._permission_config_path(),
        )
        self._workspace_permission_rules = merged
        self.permission_manager.set_workspace_rules(merged)
        self.permission_manager.set_session_rules([])
        self.state.session_permission_rules = []
        self.persist_state()
        return f"Saved {len(merged)} permission rule(s) to {path}."

    def export_permission_rules(self, path: str = "") -> str:
        raw = path.strip()
        target_path = (
            resolve_workspace_path(self.config.cwd, raw)
            if raw
            else self._permission_config_path()
        )
        merged: list[PermissionRule] = []
        for rule in [*self._workspace_permission_rules, *self.permission_manager.session_rules]:
            if rule not in merged:
                merged.append(rule)
        saved_path = save_permission_rules(
            self.config.cwd,
            merged,
            config_path=target_path,
        )
        return f"Exported {len(merged)} permission rule(s) to {saved_path}."

    def enable_plugin(self, name: str) -> str:
        plugin_name = name.strip()
        if not plugin_name:
            return "Usage: /plugin-enable <plugin-name>"
        plugin = self.plugin_registry.get_plugin(plugin_name)
        if plugin is None:
            return f'Unknown plugin "{plugin_name}".'
        if plugin.name in self.state.disabled_plugin_names:
            self.state.disabled_plugin_names = [
                item for item in self.state.disabled_plugin_names if item != plugin.name
            ]
        if plugin.name not in self.state.enabled_plugin_names:
            self.state.enabled_plugin_names.append(plugin.name)
        self._refresh_plugin_runtime()
        self.persist_state()
        return f'Enabled plugin "{plugin.name}".'

    def disable_plugin(self, name: str) -> str:
        plugin_name = name.strip()
        if not plugin_name:
            return "Usage: /plugin-disable <plugin-name>"
        plugin = self.plugin_registry.get_plugin(plugin_name)
        if plugin is None:
            return f'Unknown plugin "{plugin_name}".'
        self.state.enabled_plugin_names = [
            item for item in self.state.enabled_plugin_names if item != plugin.name
        ]
        if plugin.name not in self.state.disabled_plugin_names:
            self.state.disabled_plugin_names.append(plugin.name)
        self._refresh_plugin_runtime()
        self.persist_state()
        return f'Disabled plugin "{plugin.name}".'

    def set_advisor_model(self, model: str, *, mode: str | None = None) -> str:
        advisor_model = model.strip()
        if not advisor_model:
            return "Usage: /advisor <model>"
        self.state.advisor_model = advisor_model
        self.state.advisor_mode = mode or (
            self.state.advisor_mode if self.state.advisor_mode in {"final-review", "interactive-review"} else "final-review"
        )
        self.persist_state()
        return (
            f"Advisor set to {advisor_model}.\n"
            f"Mode: {self.state.advisor_mode}"
        )

    def set_advisor_mode(self, mode: str) -> str:
        advisor_mode = mode.strip().lower()
        if advisor_mode not in ADVISOR_MODES:
            return "Usage: /advisor mode <final-review|interactive-review>"
        if advisor_mode == "off":
            return self.unset_advisor_model()
        if not self.state.advisor_model:
            return 'Set a model first with "/advisor <model>".'
        self.state.advisor_mode = advisor_mode
        self.persist_state()
        return (
            f"Advisor mode set to {advisor_mode}.\n"
            f"Advisor: {self.state.advisor_model}"
        )

    def unset_advisor_model(self) -> str:
        if self.state.advisor_model is None and self.state.advisor_mode == "off":
            return "Advisor already unset."
        previous = self.state.advisor_model
        self.state.advisor_model = None
        self.state.advisor_mode = "off"
        self.persist_state()
        return f"Advisor disabled (was {previous or 'unset'})."

    def record_advisor_review(self, review: AdvisorReviewSummary) -> None:
        self.state.advisor_last_result = review
        self.state.advisor_review_history.append(review)
        if len(self.state.advisor_review_history) > MAX_ADVISOR_HISTORY:
            self.state.advisor_review_history = self.state.advisor_review_history[-MAX_ADVISOR_HISTORY:]
        if review.checkpoint == "plan_drift" and review.status in {"revise", "block"}:
            self.state.plan_drift_count += 1
            self.state.last_plan_drift_status = review.status
            self.state.last_plan_drift_reason = review.reason or None

    def record_planning_artifact(self, artifact: PlanningArtifact) -> None:
        self._plan_component.record_planning_artifact(artifact)

    def prepare_plan_derivation(self, goal: str):
        return self._plan_component.prepare_plan_derivation(goal)

    def run_ultraplan(
        self,
        *,
        goal: str,
        scout_categories: tuple[str, ...],
        supersede_artifact_id: str | None = None,
        derived_from_drift: bool = False,
        derivation_reason: str | None = None,
        sink=None,
    ) -> str:
        goal_text = goal.strip()
        if not goal_text:
            return "Usage: /ultraplan <goal>"
        parent_artifact = (
            self.resolve_planning_artifact(supersede_artifact_id)
            if supersede_artifact_id
            else None
        )
        scout_definitions = _build_ultraplan_scout_definitions(goal_text, scout_categories)
        start_message_count = len(self.state.messages)
        start_context_summary = self.state.context_summary
        start_planning_count = len(self.state.planning_artifact_history)
        start_superseded_links = {
            item.artifact_id: item.superseded_by_artifact_id
            for item in self.state.planning_artifact_history
        }
        start_active_plan_id = self.state.active_planning_artifact_id
        start_advisor_count = len(self.state.advisor_review_history)
        self.state.messages.append(
            {
                "role": "user",
                "content": [{"type": "text", "text": f"/ultraplan {goal_text}"}],
            }
        )
        if sink is not None:
            sink(
                RuntimeEvent(
                    kind="assistant_tool_call",
                    message=f"ultraplan scout phase launching {len(scout_definitions)} read-only sub-agent(s)",
                )
            )

        scout_results, task_ids = self._run_ultraplan_scouts(goal_text, scout_definitions)
        synthesis_prompt = _build_ultraplan_synthesis_prompt(
            goal_text,
            scout_results,
            previous_plan=parent_artifact,
        )
        try:
            self.run_plugin_hooks("before_plan", goal=goal_text, scout_categories=list(scout_categories))
            response, _ = _create_provider_message_with_retries(
                self.provider,
                session=self,
                messages=[{"role": "user", "content": [{"type": "text", "text": synthesis_prompt}]}],
                tools=[],
                system_prompt=self.build_system_prompt(),
                sink=sink or (lambda _event: None),
                allow_streaming=False,
            )
            final_text = response.text.strip()
            if not final_text:
                raise RuntimeError("Ultraplan synthesis returned an empty response.")
            advisor_review = None
            if self.uses_interactive_advisor():
                advisor_review = _request_advisor_review(
                    self,
                    checkpoint="ultraplan_synthesis",
                    user_prompt=goal_text,
                    candidate_text=final_text,
                    pending_tool_names=(),
                    sink=sink or (lambda _event: None),
                )
                if advisor_review is not None:
                    final_text = _prepend_advisor_review_to_plan(final_text, advisor_review)
            self.state.messages.append(
                {"role": "assistant", "content": [{"type": "text", "text": final_text}]}
            )
            self.record_planning_artifact(
                PlanningArtifact(
                    kind="ultraplan",
                    goal=goal_text,
                    summary=_summarize_planning_artifact(final_text),
                    supersedes_artifact_id=parent_artifact.artifact_id if parent_artifact is not None else None,
                    derived_from_drift=derived_from_drift,
                    derivation_reason=derivation_reason or "",
                    used_read_only_subagents=True,
                    scout_categories=[item.category for item in scout_definitions],
                    task_ids=task_ids,
                    advisor_status=advisor_review.status if advisor_review is not None else None,
                    advisor_reason=advisor_review.reason if advisor_review is not None else "",
                    advisor_suggested_changes=(
                        list(advisor_review.suggested_changes) if advisor_review is not None else []
                    ),
                    advisor_risk_flags=list(advisor_review.risk_flags) if advisor_review is not None else [],
                )
            )
            self.run_plugin_hooks(
                "after_plan",
                goal=goal_text,
                summary=final_text,
                advisor_status=advisor_review.status if advisor_review is not None else None,
            )
            self.persist_state()
            return final_text
        except Exception:
            del self.state.messages[start_message_count:]
            self.state.context_summary = start_context_summary
            self.state.planning_artifact_history = self.state.planning_artifact_history[:start_planning_count]
            for item in self.state.planning_artifact_history:
                item.superseded_by_artifact_id = start_superseded_links.get(item.artifact_id)
            self.state.recent_planning_artifacts = list(self.state.planning_artifact_history)
            self.state.active_planning_artifact_id = start_active_plan_id
            self.state.advisor_review_history = self.state.advisor_review_history[:start_advisor_count]
            self.state.advisor_last_result = (
                self.state.advisor_review_history[-1] if self.state.advisor_review_history else None
            )
            raise

    def enable_skill(self, name: str) -> str:
        skill_name = name.strip()
        if not skill_name:
            return "Usage: /skills-enable <skill-name>"
        skill = next((skill for skill in self.project_context.skills if skill.name == skill_name), None)
        if skill is None:
            return f'Unknown skill "{skill_name}".'
        if skill_name in self.state.disabled_skill_names:
            self.state.disabled_skill_names = [
                item for item in self.state.disabled_skill_names if item != skill_name
            ]
        if skill_name not in self.state.enabled_skill_names:
            self.state.enabled_skill_names.append(skill_name)
        self.persist_state()
        return f'Enabled skill "{skill_name}".'

    def disable_skill(self, name: str) -> str:
        skill_name = name.strip()
        if not skill_name:
            return "Usage: /skills-disable <skill-name>"
        if skill_name not in {skill.name for skill in self.project_context.skills}:
            return f'Unknown skill "{skill_name}".'
        self.state.enabled_skill_names = [
            item for item in self.state.enabled_skill_names if item != skill_name
        ]
        if skill_name not in self.state.disabled_skill_names:
            self.state.disabled_skill_names.append(skill_name)
        self.persist_state()
        return f'Disabled skill "{skill_name}".'

    def _reconcile_skill_state(self, *, initial_load: bool = False) -> None:
        known_skill_names = {skill.name for skill in self.project_context.skills}
        self.state.enabled_skill_names = [
            name for name in self.state.enabled_skill_names if name in known_skill_names
        ]
        self.state.disabled_skill_names = [
            name for name in self.state.disabled_skill_names if name in known_skill_names
        ]
        if initial_load:
            return

    def _reconcile_plugin_state(self, *, initial_load: bool = False) -> None:
        known_plugin_names = self.plugin_registry.known_plugin_names()
        self.state.enabled_plugin_names = [
            name for name in self.state.enabled_plugin_names if name in known_plugin_names
        ]
        self.state.disabled_plugin_names = [
            name for name in self.state.disabled_plugin_names if name in known_plugin_names
        ]
        if initial_load:
            return

    def _refresh_command_registry(self) -> None:
        self._runtime_context.refresh_command_registry(self.state)

    def _restore_permission_rules(self, *, reload_workspace_only: bool = False) -> None:
        workspace_rules = load_permission_rules(
            self.config.cwd,
            config_path=self._permission_config_path(),
        )
        self._workspace_permission_rules = list(workspace_rules)
        self.permission_manager.set_workspace_rules(list(workspace_rules))
        if reload_workspace_only:
            return
        restored_session_rules: list[PermissionRule] = []
        for item in self.state.session_permission_rules:
            try:
                restored_session_rules.append(permission_rule_from_dict(item))
            except ValueError:
                continue
        self.permission_manager.set_session_rules(restored_session_rules)
        self.state.session_permission_rules = [
            permission_rule_to_dict(item) for item in restored_session_rules
        ]

    def _permission_config_path(self) -> Path:
        return self.config.permission_config_path or default_permission_config_path(self.config.cwd)

    def _refresh_plugin_runtime(self) -> None:
        plugin_registry = self._session_factory.resolve_plugin_registry(self.config.cwd)
        self._runtime_context.replace_plugin_registry(plugin_registry, self.state)
        self._reconcile_plugin_state()
        self._reconcile_skill_state()
        if self._session_factory.load_mcp_from_config:
            self._runtime_context.replace_mcp_registry(
                self._session_factory.load_mcp_registry_from_config(
                    self.config,
                    state=self.state,
                    plugin_registry=plugin_registry,
                )
            )

    def is_bash_command_allowed(self, command: str) -> bool:
        return self.evaluate_bash_command_policy(command).allowed

    def search_deferred_tools(self, query: str, *, max_results: int = 5) -> dict[str, Any]:
        raw = query.strip()
        if not raw:
            return {
                "matches": [],
                "query": raw,
                "total_deferred_tools": len(self.deferred_tools),
            }
        if raw.casefold().startswith("select:"):
            tool_name = raw.split(":", 1)[1].strip()
            activated = self.activate_deferred_tool(tool_name)
            if not activated:
                raise ValueError(f'Unknown deferred tool "{tool_name}".')
            return {
                "matches": [tool_name],
                "query": raw,
                "activated": tool_name,
                "total_deferred_tools": len(self.deferred_tools),
            }
        matches = [
            tool.name
            for tool in self.deferred_tools
            if tool.matches_search_query(raw)
        ][: max(1, max_results)]
        return {
            "matches": matches,
            "query": raw,
            "total_deferred_tools": len(self.deferred_tools),
        }

    def activate_deferred_tool(self, tool_name: str) -> bool:
        raw = tool_name.strip()
        if not raw:
            return False
        if any(tool.name == raw for tool in self.default_tools):
            return True
        target = next((tool for tool in self.deferred_tools if tool.name == raw), None)
        if target is None:
            return False
        names = set(self.state.activated_deferred_tool_names)
        if raw not in names:
            names.add(raw)
            self.state.activated_deferred_tool_names = sorted(names)
            self.persist_state()
        return True

    def _active_tool_names_for_session(self) -> frozenset[str]:
        names = {tool.name for tool in self.default_tools}
        activated = set(self.state.activated_deferred_tool_names)
        names.update(
            tool.name for tool in self.deferred_tools if tool.name in activated
        )
        return frozenset(names)

    def _available_tools(self):
        allowed_names = self._active_tool_names or self._active_tool_names_for_session()
        return [tool for tool in self.tools if tool.name in allowed_names]

    def _build_active_orchestrator(self):
        if self._active_tool_names is None and len(self._available_tools()) == len(self.tools):
            return self.orchestrator
        from .runtime.orchestrator import ToolOrchestrator

        return ToolOrchestrator(list(self._available_tools()))

    @contextmanager
    def _command_execution_scope(
        self,
        *,
        allowed_tool_names: tuple[str, ...] | None,
        allowed_bash_command_prefixes: tuple[str, ...] | None,
        require_read_only_subagents: bool,
        command_policy_name: str | None = None,
        command_policy_source: str | None = None,
    ):
        previous_tool_names = self._active_tool_names
        previous_bash_prefixes = self._active_bash_command_prefixes
        previous_read_only_subagents = self._require_read_only_subagents
        previous_policy = self._active_command_policy
        policy = self._compile_turn_command_policy(
            allowed_tool_names=allowed_tool_names,
            allowed_bash_command_prefixes=allowed_bash_command_prefixes,
            require_read_only_subagents=require_read_only_subagents,
            command_policy_name=command_policy_name,
            command_policy_source=command_policy_source,
        )
        policy = self._merge_execution_contract_policy(policy)
        self._active_command_policy = policy
        if policy is not None:
            self._active_tool_names = policy.allowed_tool_names
            self._active_bash_command_prefixes = policy.allowed_bash_command_prefixes
            self._require_read_only_subagents = policy.require_read_only_subagents
        try:
            yield
        finally:
            self._active_tool_names = previous_tool_names
            self._active_bash_command_prefixes = previous_bash_prefixes
            self._require_read_only_subagents = previous_read_only_subagents
            self._active_command_policy = previous_policy

    def _compile_turn_command_policy(
        self,
        *,
        allowed_tool_names: tuple[str, ...] | None,
        allowed_bash_command_prefixes: tuple[str, ...] | None,
        require_read_only_subagents: bool,
        command_policy_name: str | None,
        command_policy_source: str | None,
    ) -> TurnCommandPolicy | None:
        has_constraints = (
            allowed_tool_names is not None
            or allowed_bash_command_prefixes is not None
            or require_read_only_subagents
        )
        if not has_constraints and not command_policy_name and not command_policy_source:
            return None
        name = (command_policy_name or "restricted-turn").strip() or "restricted-turn"
        source = (command_policy_source or "interactive-turn").strip() or "interactive-turn"
        enforce_read_only_bash = name in READ_ONLY_COMMAND_POLICY_NAMES
        return TurnCommandPolicy(
            name=name,
            source=source,
            allowed_tool_names=(
                frozenset(allowed_tool_names) if allowed_tool_names is not None else None
            ),
            allowed_bash_command_prefixes=(
                tuple(allowed_bash_command_prefixes)
                if allowed_bash_command_prefixes is not None
                else None
            ),
            require_read_only_subagents=require_read_only_subagents,
            enforce_read_only_bash=enforce_read_only_bash,
        )

    def _merge_execution_contract_policy(
        self,
        turn_policy: TurnCommandPolicy | None,
    ) -> TurnCommandPolicy | None:
        session_policy = self.session_command_policy()
        if session_policy is None:
            return turn_policy
        if turn_policy is None:
            return session_policy
        merged_tool_names: frozenset[str] | None
        if session_policy.allowed_tool_names is None:
            merged_tool_names = turn_policy.allowed_tool_names
        elif turn_policy.allowed_tool_names is None:
            merged_tool_names = session_policy.allowed_tool_names
        else:
            merged_tool_names = session_policy.allowed_tool_names & turn_policy.allowed_tool_names
        merged_bash_prefixes: tuple[str, ...] | None
        if session_policy.allowed_bash_command_prefixes is None:
            merged_bash_prefixes = turn_policy.allowed_bash_command_prefixes
        elif turn_policy.allowed_bash_command_prefixes is None:
            merged_bash_prefixes = session_policy.allowed_bash_command_prefixes
        else:
            merged_bash_prefixes = tuple(
                prefix
                for prefix in turn_policy.allowed_bash_command_prefixes
                if prefix in session_policy.allowed_bash_command_prefixes
            )
        return TurnCommandPolicy(
            name=session_policy.name,
            source=session_policy.source,
            allowed_tool_names=merged_tool_names,
            allowed_bash_command_prefixes=merged_bash_prefixes,
            require_read_only_subagents=(
                session_policy.require_read_only_subagents or turn_policy.require_read_only_subagents
            ),
            enforce_read_only_bash=(
                session_policy.enforce_read_only_bash or turn_policy.enforce_read_only_bash
            ),
        )

    def _format_bash_command_policy_violation(
        self,
        policy: TurnCommandPolicy,
        segment: ShellCommandSegment,
        *,
        allowed_prefixes: tuple[str, ...],
        reason_kind: str,
    ) -> str:
        allowed = ", ".join(allowed_prefixes)
        segment_label = f'segment {segment.index} "{segment.raw_command}"'
        mode = f'command mode "{policy.name}"'
        if reason_kind == "prefix":
            return (
                f'Bash command is not allowed in {mode}: {segment_label} does not match any '
                f"allowed prefix. Allowed prefixes: {allowed}"
            )
        if reason_kind == "complex_feature":
            detail = ", ".join(segment.features) if segment.features else "complex shell feature"
            return (
                f'Bash command is not allowed in {mode}: {segment_label} uses '
                f"complex shell syntax (complex_feature={detail}). Allowed prefixes: {allowed}"
            )
        if reason_kind == "uncertain":
            detail = segment.uncertainty_reason or "complex shell feature detected"
            return (
                f'Bash command is not allowed in {mode}: {segment_label} uses syntax that '
                f"cannot be safely validated ({detail}). Allowed prefixes: {allowed}"
            )
        return (
            f'Bash command is not allowed in {mode}: {segment_label} performs a '
            f'{segment.risk_level} action, but this mode only permits read-oriented bash segments. '
            f"Allowed prefixes: {allowed}"
        )

    def reload_mcp_from_config(self) -> str:
        plugin_registry = self._session_factory.resolve_plugin_registry(self.config.cwd)
        self._runtime_context.replace_plugin_registry(plugin_registry, self.state)
        self._reconcile_plugin_state()
        new_registry = self._session_factory.load_mcp_registry_from_config(
            self.config,
            state=self.state,
            plugin_registry=plugin_registry,
        )
        self._runtime_context.replace_mcp_registry(new_registry)
        self.persist_state()
        if self.mcp_registry is None:
            return "Reloaded MCP configuration. No servers configured."
        counts = self._mcp_server_counts()
        return (
            "Reloaded MCP configuration. "
            f"servers={counts['servers']} "
            f"tools={counts['tools']} "
            f"resources={counts['resources']} "
            f"failed={counts['failed']} "
            f"retrying={counts['retrying']}"
        )

    def _prompt_user_questions(self, request: UserQuestionRequest) -> UserQuestionResponse:
        answers: dict[str, str] = {}
        for question in request.questions:
            print(f"[{question.header}] {question.question}")
            for index, option in enumerate(question.options, start=1):
                print(f"  {index}. {option.label} - {option.description}")
            while True:
                if question.multi_select:
                    raw_answer = input("Select one or more options (comma-separated numbers or labels, blank to cancel): ").strip()
                else:
                    raw_answer = input("Select one option (number or label, blank to cancel): ").strip()
                if not raw_answer:
                    return UserQuestionResponse(answers=answers, canceled=True)
                try:
                    selected_labels = self._resolve_question_answer(question, raw_answer)
                except ValueError as exc:
                    print(f"error: {exc}")
                    continue
                answers[question.question] = ", ".join(selected_labels)
                break
        return UserQuestionResponse(answers=answers, canceled=False)

    def _resolve_question_answer(self, question, raw_answer: str) -> list[str]:
        tokens = [token.strip() for token in raw_answer.split(",") if token.strip()]
        if not tokens:
            raise ValueError("Question answer cannot be empty.")
        if not question.multi_select and len(tokens) != 1:
            raise ValueError("This question accepts only one answer.")
        resolved: list[str] = []
        labels = {option.label.casefold(): option.label for option in question.options}
        for token in tokens:
            if token.isdigit():
                index = int(token) - 1
                if not 0 <= index < len(question.options):
                    raise ValueError(f"Invalid option number: {token}")
                resolved.append(question.options[index].label)
                continue
            label = labels.get(token.casefold())
            if label is None:
                raise ValueError(f"Unknown option: {token}")
            resolved.append(label)
        if not question.multi_select and len(resolved) > 1:
            raise ValueError("This question accepts only one answer.")
        return resolved

    def reconnect_mcp_server(self, name: str) -> str:
        server_name = name.strip()
        if not server_name:
            return "Usage: /mcp-reconnect <server-name>"
        if self.mcp_registry is None or server_name not in self.mcp_registry.list_servers():
            return f'Unknown MCP server "{server_name}".'
        server = self.mcp_registry.reconnect_server(server_name)
        self._runtime_context.replace_mcp_registry(self.mcp_registry)
        self.persist_state()
        if server.status != "connected":
            retry_in = self.mcp_registry.retry_wait_seconds(server_name)
            retry_text = f" retry_in={retry_in}s" if retry_in else ""
            return (
                f'Reconnect failed for "{server_name}": '
                f"status={server.status} error={server.last_error or 'unknown'}{retry_text}"
            )
        return (
            f'Reconnected MCP server "{server_name}". '
            f"tools={len(server.tools)} resources={len(server.resources)} version="
            f"{server.initialize_result.server_version if server.initialize_result is not None else 'unknown'}"
        )

    def handle_mcp_server_failure(self, server_name: str, error_text: str) -> None:
        if self.mcp_registry is None or server_name not in self.mcp_registry.list_servers():
            return
        self.mcp_registry.mark_server_failed(server_name, error_text)

    def ensure_mcp_server_connected(self, server_name: str):
        if self.mcp_registry is None or server_name not in self.mcp_registry.list_servers():
            return None
        return self.mcp_registry.ensure_server_connected(server_name)

    def close(self) -> None:
        try:
            self._runtime_context.close()
        finally:
            if self._workspace_cleanup is not None:
                try:
                    self._workspace_cleanup()
                    if self.state.workspace_mode != "main":
                        self.state.workspace_cleanup_status = "completed"
                        self.state.workspace_cleanup_error = None
                        self.state.workspace_health = self._derive_workspace_health(
                            workspace_mode=self.state.workspace_mode,
                            workspace_cleanup_status=self.state.workspace_cleanup_status,
                            workspace_unavailable=bool(self.state.workspace_unavailable),
                        )
                except Exception as exc:  # noqa: BLE001
                    if self.state.workspace_mode != "main":
                        self.state.workspace_cleanup_status = "failed"
                        self.state.workspace_cleanup_error = f"{type(exc).__name__}: {exc}"
                        self.state.workspace_health = self._derive_workspace_health(
                            workspace_mode=self.state.workspace_mode,
                            workspace_cleanup_status=self.state.workspace_cleanup_status,
                            workspace_unavailable=bool(self.state.workspace_unavailable),
                        )
                finally:
                    self._workspace_cleanup = None

    def run_plugin_hooks(self, hook_name: str, **payload: Any) -> list[str]:
        del payload
        return self.plugin_registry.enabled_hook_plugin_names(self.state, hook_name)

    def validate_provider_capabilities(self) -> None:
        capabilities = getattr(self.provider, "capabilities", None)
        if capabilities is None:
            raise ProviderCapabilityError("Provider does not declare runtime capabilities.")
        if self.tool_specs() and not capabilities.supports_tool_calling:
            raise ProviderCapabilityError(
                f'Provider "{capabilities.provider}" with model "{capabilities.model}" '
                "does not support tool calling, but this coding session requires tools."
            )

    def _summarize_message(self, message: dict[str, Any]) -> str:
        content = message.get("content", [])
        if not content:
            return "(empty)"

        text_parts: list[str] = []
        tool_uses: list[str] = []
        tool_results = 0
        tool_result_error: str | None = None
        for block in content:
            block_type = block.get("type")
            if block_type == "text":
                text = (block.get("text") or "").strip()
                if text:
                    text_parts.append(text)
            elif block_type == "tool_use":
                tool_uses.append(block.get("name", "unknown"))
            elif block_type == "tool_result":
                tool_results += 1
                if block.get("is_error") and tool_result_error is None:
                    raw_error = str(block.get("content") or "").strip()
                    if raw_error:
                        tool_result_error = self._compact_tool_result_error(raw_error)

        parts: list[str] = []
        if text_parts:
            text = " ".join(text_parts)
            if len(text) > 100:
                text = text[:97] + "..."
            parts.append(text)
        if tool_uses:
            parts.append("tool_use=" + ",".join(tool_uses))
        if tool_results:
            suffix = "s" if tool_results != 1 else ""
            parts.append(f"tool_result={tool_results} block{suffix}")
        if tool_result_error:
            parts.append(f"tool_error={tool_result_error}")
        return " | ".join(parts) if parts else "(non-text message)"

    def _compact_tool_result_error(self, text: str) -> str:
        compact = " ".join(part.strip() for part in text.splitlines() if part.strip())
        if len(compact) > 140:
            compact = compact[:137] + "..."
        return compact

    def _recent_workspace_audit_history_lines(self, limit: int = 3) -> list[str]:
        audits = [
            change
            for change in reversed(self.state.recent_change_sets)
            if change.change_kind == "workspace_audit"
        ][:limit]
        lines: list[str] = []
        for index, change in enumerate(audits, start=1):
            visible_files = self._visible_change_files(change)
            path_preview = ", ".join(file_change.path for file_change in visible_files[:2])
            if len(visible_files) > 2:
                path_preview += f", ... +{len(visible_files) - 2}"
            suffix = f" | paths={path_preview}" if path_preview else ""
            action = "cleanup applied"
            if change.tool_name == "workspace_repair":
                action = "repair applied"
            lines.append(
                f"{index}. {action} | {change.summary}{suffix}"
            )
        return lines

    def _compact_multiline_text(self, text: str, *, max_lines: int, max_chars: int) -> str:
        return compact_multiline_text(text, max_lines=max_lines, max_chars=max_chars)

    def _normalize_advisor_state(self) -> None:
        self._advisor_component._normalize_advisor_state()

    def _normalize_execution_contract_state(self) -> None:
        if not isinstance(self.state.session_execution_mode, str) or not self.state.session_execution_mode:
            self.state.session_execution_mode = "main"
        self.state.session_command_policy_name = (
            str(self.state.session_command_policy_name)
            if self.state.session_command_policy_name is not None
            else None
        )
        self.state.session_command_policy_source = (
            str(self.state.session_command_policy_source)
            if self.state.session_command_policy_source is not None
            else None
        )
        self.state.session_command_policy_allowed_tool_names = [
            str(item)
            for item in (self.state.session_command_policy_allowed_tool_names or [])
            if str(item)
        ]
        self.state.session_command_policy_allowed_bash_prefixes = [
            str(item)
            for item in (self.state.session_command_policy_allowed_bash_prefixes or [])
            if str(item)
        ]
        self.state.session_command_policy_require_read_only_subagents = bool(
            self.state.session_command_policy_require_read_only_subagents
        )
        if (
            self.state.session_command_policy_name is None
            and self.state.session_command_policy_source is None
            and not self.state.session_command_policy_allowed_tool_names
            and not self.state.session_command_policy_allowed_bash_prefixes
            and not self.state.session_command_policy_require_read_only_subagents
        ):
            if self.state.session_execution_mode not in {"main", "background-session", "child-session"}:
                self.state.session_execution_mode = "main"

    def _recent_plan_drift_summary(self) -> str | None:
        return self._advisor_component._recent_plan_drift_summary()

    def _normalize_workspace_state(self) -> None:
        effective_cwd = str(self.config.cwd)
        original_cwd = str(self.config.transcript_cwd or self.config.cwd)
        if self.state.effective_cwd is None:
            self.state.effective_cwd = effective_cwd
        if self.state.original_cwd is None:
            self.state.original_cwd = original_cwd
        if self.state.workspace_mode not in {"main", "snapshot", "worktree"}:
            self.state.workspace_mode = "main"
        if self.state.original_cwd == self.state.effective_cwd:
            self.state.workspace_mode = "main"
        if self.state.workspace_mode == "main":
            self.state.workspace_unavailable = False
            self.state.workspace_unavailable_reason = None
            self.state.workspace_fallback_cwd = None
            self.state.workspace_health = self._derive_workspace_health(
                workspace_mode=self.state.workspace_mode,
                workspace_cleanup_status=self.state.workspace_cleanup_status,
                workspace_unavailable=False,
            )
            return
        effective_exists = Path(self.state.effective_cwd or effective_cwd).exists()
        if self.state.workspace_fallback_cwd is None:
            self.state.workspace_fallback_cwd = self.state.original_cwd or original_cwd
        if effective_exists:
            self.state.workspace_unavailable = False
            self.state.workspace_unavailable_reason = None
            self.state.workspace_health = self._derive_workspace_health(
                workspace_mode=self.state.workspace_mode,
                workspace_cleanup_status=self.state.workspace_cleanup_status,
                workspace_unavailable=False,
            )
            return
        self.state.workspace_unavailable = True
        if not self.state.workspace_unavailable_reason:
            self.state.workspace_unavailable_reason = (
                f"Isolated workspace is unavailable: expected {self.state.effective_cwd}"
            )
        self.state.workspace_health = self._derive_workspace_health(
            workspace_mode=self.state.workspace_mode,
            workspace_cleanup_status=self.state.workspace_cleanup_status,
            workspace_unavailable=True,
        )

    def _run_ultraplan_scouts(
        self,
        goal: str,
        scout_definitions: tuple["_UltraplanScoutDefinition", ...],
    ) -> tuple[dict[str, str], list[str]]:
        results: dict[str, str] = {}
        task_ids: list[str] = []
        tasks = []
        for definition in scout_definitions:
            task = self.task_manager.create(
                "ultraplan_scout",
                definition.description,
                parent_session_id=self.state.session_id,
                provider=self.config.provider,
                model=self.config.model,
                cwd=str(self.config.cwd),
                planner_kind="ultraplan",
                task_role="scout",
                scout_category=definition.category,
                read_only=True,
            )
            self.task_manager.set_progress(task.id, "Queued read-only scout")
            task_ids.append(task.id)
            tasks.append((task.id, definition))

            def worker(task_id=task.id, scout_definition=definition) -> None:
                child = None
                output = ""
                error: Exception | None = None
                try:
                    child = self.create_child_session(interactive=False, isolated_workspace=True)
                    self.task_manager.set_progress(
                        task_id,
                        "Running read-only scout",
                        scout_category=scout_definition.category,
                        **self._task_workspace_metadata(child),
                    )
                    output = child.ask(
                        _build_read_only_subagent_prompt(
                            description=scout_definition.description,
                            prompt=scout_definition.prompt,
                        ),
                        sink=self._build_background_task_sink(task_id),
                        allowed_tool_names=READ_ONLY_SUBAGENT_TOOL_NAMES,
                        allowed_bash_command_prefixes=READ_ONLY_SUBAGENT_BASH_PREFIXES,
                        require_read_only_subagents=True,
                        command_policy_name="read-only-subagent",
                        command_policy_source="ultraplan-scout",
                    )
                except Exception as exc:  # noqa: BLE001
                    error = exc
                finally:
                    if child is not None:
                        child.close()
                workspace_metadata = self._task_workspace_metadata(child)
                if error is None:
                    self.task_manager.complete(task_id, output, **workspace_metadata)
                else:
                    self.task_manager.fail(task_id, f"{type(error).__name__}: {error}", **workspace_metadata)

            Thread(target=worker, daemon=True).start()

        for task_id, definition in tasks:
            task = self.task_manager.wait_for_task(task_id, timeout_sec=max(self.config.max_turns * 10, 30))
            if task.status == "completed":
                results[definition.category] = task.output.strip() or "(empty scout result)"
            else:
                detail = task.error or task.output or task.progress_summary or "Scout failed."
                results[definition.category] = f"[{task.status}] {detail}"
        return results, task_ids

    def _build_background_task_sink(self, task_id: str):
        def sink(event: RuntimeEvent) -> None:
            summary = self._summarize_runtime_event(event)
            if summary:
                metadata: dict[str, Any] = {}
                permission_context = PermissionDisplayContext(
                    decision_reason=event.decision_reason or "",
                    permission_rules=event.permission_rules,
                    command_mode_name=event.command_mode_name or "",
                    command_mode_allowed_prefixes=event.command_mode_allowed_prefixes,
                    command_mode_violating_segment=event.command_mode_violating_segment or "",
                    command_mode_violating_segment_index=event.command_mode_violating_segment_index,
                    command_mode_complex_features=event.command_mode_complex_features,
                )
                if has_permission_display_context(permission_context):
                    metadata.update(
                        {
                            "permission_display_decision_reason": permission_context.decision_reason,
                            "permission_display_permission_rules": list(permission_context.permission_rules),
                            "permission_display_command_mode_name": permission_context.command_mode_name,
                            "permission_display_command_mode_allowed_prefixes": list(
                                permission_context.command_mode_allowed_prefixes
                            ),
                            "permission_display_command_mode_violating_segment": (
                                permission_context.command_mode_violating_segment
                            ),
                            "permission_display_command_mode_violating_segment_index": (
                                permission_context.command_mode_violating_segment_index
                            ),
                            "permission_display_command_mode_complex_features": list(
                                permission_context.command_mode_complex_features
                            ),
                        }
                    )
                self.task_manager.set_progress(task_id, summary, **metadata)
                self.task_manager.append_output(task_id, summary + "\n")

        return sink

    def _task_permission_display_context(self, metadata: dict[str, Any]) -> PermissionDisplayContext:
        rules = metadata.get("permission_display_permission_rules") or ()
        prefixes = metadata.get("permission_display_command_mode_allowed_prefixes") or ()
        complex_features = metadata.get("permission_display_command_mode_complex_features") or ()
        return PermissionDisplayContext(
            decision_reason=str(metadata.get("permission_display_decision_reason") or ""),
            permission_rules=tuple(str(rule) for rule in rules),
            command_mode_name=str(metadata.get("permission_display_command_mode_name") or ""),
            command_mode_allowed_prefixes=tuple(str(prefix) for prefix in prefixes),
            command_mode_violating_segment=str(
                metadata.get("permission_display_command_mode_violating_segment") or ""
            ),
            command_mode_violating_segment_index=metadata.get(
                "permission_display_command_mode_violating_segment_index"
            ),
            command_mode_complex_features=tuple(str(feature) for feature in complex_features),
        )

    def _render_task_permission_display_lines(self, task: Any) -> list[str]:
        context = self._task_permission_display_context(task.metadata)
        if not has_permission_display_context(context):
            return []
        return render_permission_display_lines(
            context,
            policy_label="    policy",
            matched_rules_header="    matched_rules:",
            command_mode_header="    command_mode:",
            bullet_prefix="    - ",
            nested_bullet_prefix="    - ",
        )

    def _summarize_runtime_event(self, event: RuntimeEvent) -> str:
        if event.kind == "assistant_text":
            text = event.message.strip()
            if len(text) > 240:
                text = text[:237] + "..."
            return f"[assistant] {text}"
        if event.kind == "plan_execution":
            return f"[plan] {event.message}"
        if event.kind == "task_progress":
            task_label = event.task_id or "task"
            return f"[task:{task_label}] {event.message}"
        if event.kind in {
            "advisor",
            "advisor_review_started",
            "advisor_review_result",
            "advisor_revision_requested",
            "advisor_error",
        }:
            return f"[advisor] {event.message}"
        if event.kind == "assistant_tool_call":
            return f"[assistant->tools] {event.message}"
        if event.kind == "assistant_tool_result_ready":
            return f"[tools->assistant] {event.message}"
        if event.kind == "tool_started":
            tool_name = event.tool_name or "unknown"
            return f"[tool:start] {tool_name} {event.message}"
        if event.kind == "tool_finished":
            tool_name = event.tool_name or "unknown"
            suffix = f" ({event.duration_ms}ms)" if event.duration_ms is not None else ""
            return f"[tool:ok] {tool_name}{suffix}"
        if event.kind == "tool_failed":
            tool_name = event.tool_name or "unknown"
            suffix = f" ({event.duration_ms}ms)" if event.duration_ms is not None else ""
            summary = f"[tool:error] {tool_name}{suffix}: {event.message}"
            detail = render_permission_display_compact(
                PermissionDisplayContext(
                    decision_reason=event.decision_reason or "",
                    permission_rules=event.permission_rules,
                    command_mode_name=event.command_mode_name or "",
                    command_mode_allowed_prefixes=event.command_mode_allowed_prefixes,
                    command_mode_violating_segment=event.command_mode_violating_segment or "",
                    command_mode_violating_segment_index=event.command_mode_violating_segment_index,
                    command_mode_complex_features=event.command_mode_complex_features,
                )
            )
            if detail:
                summary += f" [{detail}]"
            return summary
        if event.kind == "provider_retry":
            return f"[provider:retry] {event.message}"
        if event.kind == "context_compacted":
            return f"[context] {event.message}"
        if event.kind == "tool_result":
            status = "error" if event.is_error else "ok"
            return f"[tool:result] {status}"
        return ""

    def _describe_file_change(self, file_change: WorkspaceFileChange) -> str:
        return describe_workspace_change(file_change)

    def _render_file_change_summary(self, file_change: WorkspaceFileChange) -> str:
        rendered = render_change_summary(
            file_change.path,
            file_change.before_content,
            file_change.after_content,
            max_preview_lines=3,
        )
        lines = [self._describe_file_change(file_change)]
        lines.extend(workspace_change_metadata_lines(file_change))
        lines.append(rendered)
        return "\n".join(lines).replace("\n", "\n     ")

    def _render_file_change_detail(self, file_change: WorkspaceFileChange) -> str:
        rendered = render_change_detail(
            file_change.path,
            file_change.before_content,
            file_change.after_content,
            max_lines=12,
        )
        lines = [self._describe_file_change(file_change)]
        lines.extend(workspace_change_metadata_lines(file_change))
        lines.append(rendered)
        return "\n".join(lines).replace("\n", "\n     ")

    def _render_change_entry(self, change: WorkspaceChangeSet) -> str:
        summary = change.summary.strip()
        if len(summary) > 52:
            summary = summary[:49] + "..."
        counts = self._count_change_actions(change)
        parts: list[str] = []
        if counts["create"]:
            parts.append(f"c{counts['create']}")
        if counts["update"]:
            parts.append(f"u{counts['update']}")
        if counts["delete"]:
            parts.append(f"d{counts['delete']}")
        if counts["move"]:
            parts.append(f"m{counts['move']}")
        visible_files = self._visible_change_files(change)
        action_summary = " ".join(parts) if parts else f"{len(visible_files)}f"
        suffix = " audit" if not change.undoable or change.change_kind != "workspace_change" else ""
        return (
            f"{change.change_id[:8]}  [{change.tool_name}] "
            f"{summary} ({action_summary}{suffix})"
        )

    def _count_change_actions(self, change: WorkspaceChangeSet) -> dict[str, int]:
        return count_workspace_change_actions(self._visible_change_files(change))

    def _visible_change_files(self, change: WorkspaceChangeSet) -> list[WorkspaceFileChange]:
        return [file_change for file_change in change.files if is_visible_workspace_change(file_change)]

    def _apply_change_set(self, change: WorkspaceChangeSet, *, direction: str) -> None:
        if not change.undoable:
            raise PermissionDeniedError(
                f'Change "{change.change_id[:8]}" is audit-only and cannot be {direction}ne.'
            )
        file_changes = reversed(change.files) if direction == "undo" else change.files
        for file_change in file_changes:
            path = resolve_workspace_path(self.config.cwd, file_change.path)
            target_exists = (
                file_change.existed_before if direction == "undo" else file_change.after_content is not None
            )
            target_content = (
                file_change.before_content if direction == "undo" else file_change.after_content
            )
            if target_exists:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(target_content or "", encoding="utf-8")
            elif path.exists():
                path.unlink()

    def _summarize_change_operation(self, verb: str, changes: list[WorkspaceChangeSet]) -> str:
        if not changes:
            return f"{verb} 0 change(s)."
        lines = [f"{verb} {len(changes)} change(s)."]
        for change in changes[:3]:
            lines.append(f"- {change.summary} [{change.change_id[:8]}]")
        if len(changes) > 3:
            lines.append(f"- ... {len(changes) - 3} more change(s)")
        return "\n".join(lines)

    def _advisor_context_lines(self, *, exclude_latest_assistant: bool) -> list[str]:
        return self._advisor_component._advisor_context_lines(
            exclude_latest_assistant=exclude_latest_assistant
        )

    def _parse_change_step_count(self, args: str, *, command: str) -> int | str:
        raw = args.strip()
        if not raw:
            return 1
        try:
            count = int(raw)
        except ValueError:
            return f"Usage: {command} [count]"
        if count <= 0:
            return f"Usage: {command} [count]"
        return count

    def _select_changes(
        self,
        stack: list[WorkspaceChangeSet],
        *,
        args: str,
        command: str,
    ) -> list[WorkspaceChangeSet] | str:
        raw = args.strip()
        if not raw:
            return [stack[-1]]
        prefer_count = raw.isdigit() and len(raw) <= 2
        if prefer_count:
            try:
                count = self._parse_change_step_count(raw, command=command)
                if isinstance(count, str):
                    raise ValueError
                return list(reversed(stack[-min(count, len(stack)) :]))
            except ValueError:
                return f'Unknown change id "{raw}". Use /changes to inspect the stack.'
        matches = [change for change in stack if change.change_id.startswith(raw)]
        if matches:
            if len(matches) > 1:
                ids = ", ".join(change.change_id[:8] for change in matches[:5])
                return f'Ambiguous change id "{raw}". Matches: {ids}'
            return matches
        try:
            count = self._parse_change_step_count(raw, command=command)
            if isinstance(count, str):
                raise ValueError
            return list(reversed(stack[-min(count, len(stack)) :]))
        except ValueError:
            return f'Unknown change id "{raw}". Use /changes to inspect the stack.'

    def _mcp_next_steps(
        self,
        source: str,
        *,
        server_name: str,
        tool_name: str,
        model_verification: bool = False,
    ) -> list[str]:
        if source == "ok":
            if model_verification:
                return [
                    "Model chain succeeded. This confirms the model, MCP server, and transport worked together.",
                    f'Use /mcp-call {server_name} {tool_name} ... if you want to inspect the raw MCP tool result separately.',
                ]
            return [
                "Direct MCP call succeeded.",
                f'Use /mcp-verify {server_name} {tool_name} ... to verify the model can invoke the same tool through the normal agent loop.',
            ]
        if source == "config":
            return [
                "Check .pyclaude/mcp_servers.json and confirm the server name is configured correctly.",
                f'Run /mcp and /mcp-tools to confirm "{server_name}" and "{tool_name}" are visible to this session.',
            ]
        if source == "transport":
            return [
                "Check the MCP server URL/process/auth settings and whether the server is reachable.",
                f'Run /mcp or /mcp-reconnect {server_name} to inspect health, retry_in, and reconnect state.',
            ]
        if source == "mcp_server":
            return [
                f'Confirm "{tool_name}" is actually exposed by "{server_name}".',
                f'Run /mcp-tools or /mcp-refresh to refresh the advertised tool list for "{server_name}".',
            ]
        if source == "mcp_tool":
            return [
                "The server was reachable, but the tool itself returned an error payload.",
                "Verify the input arguments and try a direct /mcp-call first before re-running model verification.",
            ]
        if source == "model":
            return [
                f'Run /mcp-call {server_name} {tool_name} ... first to confirm MCP itself works outside the model loop.',
                "If direct MCP succeeds, treat this as a model/tool-calling issue: switch to a stronger tool-calling model or tighten the prompt.",
            ]
        return []

    def _mcp_health_summary(self) -> str:
        counts = self._mcp_server_counts()
        return (
            "summary: "
            f"connected={counts['connected']} failed={counts['failed']} retrying={counts['retrying']}"
        )

    def _mcp_server_counts(self) -> dict[str, int]:
        counts = {
            "servers": 0,
            "connected": 0,
            "failed": 0,
            "retrying": 0,
            "tools": 0,
            "resources": 0,
        }
        if self.mcp_registry is None:
            return counts
        counts["servers"] = len(self.mcp_registry.list_servers())
        for server_name in self.mcp_registry.list_servers():
            server = self.mcp_registry.get_server(server_name)
            if server.status == "connected":
                counts["connected"] += 1
            elif server.status == "retrying":
                counts["retrying"] += 1
            elif server.status == "failed":
                counts["failed"] += 1
            counts["tools"] += len(server.tools)
            counts["resources"] += len(server.resources)
        return counts


_SESSION_COMPONENT_METHODS: dict[str, tuple[str, ...]] = {
    "_workspace_component": (
        "current_workspace_action_bundle",
        "task_workspace_action_bundle",
        "task_workspace_detail_metadata",
        "describe_orphaned_workspaces",
        "workspace_cleanup_preview",
        "workspace_repair",
        "workspace_cleanup_apply",
        "runtime_cwd",
        "workspace_unavailable",
    ),
    "_task_detail_component": (
        "task_surface_counts_payload",
        "task_surface_summary_lines",
        "resolve_task",
        "describe_tasks",
        "describe_task_detail",
        "open_task_detail",
        "describe_task_drift_detail",
        "open_task_detail_advisor",
        "open_task_drift_detail",
    ),
    "_symbol_surface_component": (
        "locate_symbol",
        "collect_references",
        "build_open_file_target",
        "build_symbol_target",
        "build_diff_targets",
        "build_reference_targets",
        "build_symbol_action_bundle",
        "current_symbol_surface_payload",
        "current_symbol_surface_action_bundle",
        "symbol_surface_select_next_match",
        "symbol_surface_select_prev_match",
        "symbol_surface_select_next_definition",
        "symbol_surface_select_prev_definition",
        "symbol_surface_select_next_reference",
        "symbol_surface_select_prev_reference",
        "locate_symbol_surface_payload",
        "collect_references_surface_payload",
        "build_symbol_action_surface_payload",
        "describe_current_symbol_surface",
        "describe_symbol_lookup_surface",
        "describe_symbol_reference_surface",
        "describe_symbol_action_surface",
        "symbol_surface_primary_action",
        "symbol_surface_secondary_action",
        "clear_symbol_surface",
    ),
    "_advisor_component": (
        "has_advisor_model",
        "uses_interactive_advisor",
        "build_advisor_provider",
        "build_advisor_review_prompt",
        "build_advisor_revision_prompt",
        "build_advisor_followup_prompt",
        "build_plan_drift_review_context",
        "record_plan_drift_context",
        "describe_advisor",
        "show_advisor_status",
    ),
    "_plan_component": (
        "latest_planning_artifact",
        "planning_artifacts",
        "active_planning_artifact",
        "resolve_planning_artifact",
        "begin_plan_execution",
        "clear_plan_execution",
        "start_active_plan_execution_task",
        "update_execution_task",
        "complete_execution_task",
        "fail_execution_task",
        "describe_planning_lifecycle",
        "describe_active_plan",
        "describe_active_plan_scouts",
        "describe_active_plan_scouts_at",
        "describe_active_plan_execution",
        "describe_active_plan_execution_at",
        "describe_active_plan_timeline",
        "describe_active_plan_replay",
        "describe_active_plan_audit",
        "describe_active_plan_timeline_at",
        "describe_active_plan_replay_at",
        "describe_active_plan_audit_at",
        "describe_active_plan_lineage",
        "describe_active_plan_lineage_at",
        "active_plan_lineage_index",
        "describe_active_plan_advisor",
        "open_active_plan_advisor",
        "describe_planning_artifacts",
        "describe_planning_artifact",
        "use_planning_artifact",
        "revert_to_planning_artifact",
        "clear_active_plan",
        "record_planning_artifact",
        "prepare_plan_derivation",
        "run_ultraplan",
    ),
}


def _make_session_component_delegate(component_attr: str, method_name: str):
    def delegated(self: Session, *args: Any, **kwargs: Any):
        component = getattr(self, component_attr)
        method = getattr(component, method_name)
        return method(*args, **kwargs)

    delegated.__name__ = method_name
    delegated.__qualname__ = f"Session.{method_name}"
    delegated.__doc__ = (
        f"Delegates Session.{method_name} to the {component_attr} collaborator."
    )
    return delegated


def _install_session_component_delegates() -> None:
    if getattr(Session, "_session_component_delegates_installed", False):
        return

    original_init = Session.__init__

    def componentized_init(self: Session, *args: Any, **kwargs: Any) -> None:
        self._workspace_component = WorkspaceSessionComponent(self)
        self._task_detail_component = TaskDetailSessionComponent(self)
        self._symbol_surface_component = SymbolSurfaceSessionComponent(self)
        self._advisor_component = AdvisorSessionComponent(self)
        self._plan_component = PlanSessionComponent(self)
        original_init(self, *args, **kwargs)

    Session._original_init = original_init
    Session.__init__ = componentized_init

    for component_attr, method_names in _SESSION_COMPONENT_METHODS.items():
        for method_name in method_names:
            alias_name = f"_original_{method_name}"
            if not hasattr(Session, alias_name):
                setattr(Session, alias_name, getattr(Session, method_name))
            setattr(
                Session,
                method_name,
                _make_session_component_delegate(component_attr, method_name),
            )

    Session._session_component_delegates_installed = True


_install_session_component_delegates()


def _render_mcp_content(content: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in content:
        if item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        else:
            parts.append(str(item))
    rendered = "\n".join(part for part in parts if part).strip()
    return rendered or "(empty MCP result)"


def _build_read_only_subagent_prompt(*, description: str, prompt: str) -> str:
    return (
        "You are a read-only planning sub-agent.\n"
        "Do not modify files, do not create commits, and do not run commands with side effects.\n"
        "Use only inspection tools and read-only shell commands. Return findings, constraints, risks, and concrete recommendations.\n\n"
        f"Subtask: {description}\n\n"
        f"{prompt}"
    )


def _build_ultraplan_scout_definitions(
    goal: str,
    categories: tuple[str, ...],
) -> tuple[_UltraplanScoutDefinition, ...]:
    prompts = {
        "architecture-boundaries": (
            "Map the modules, ownership boundaries, and extension points relevant to this goal. "
            "Call out coupling and places where edits are likely required."
        ),
        "data-flow-interfaces": (
            "Trace the data flow, runtime entrypoints, and key interfaces involved in this goal. "
            "Highlight request/response shapes, state transitions, and protocol boundaries."
        ),
        "tests-regressions": (
            "Identify the test surface, likely regressions, and the smallest useful verification plan. "
            "Call out missing tests and brittle areas."
        ),
        "risks-unknowns": (
            "Focus on constraints, unsafe assumptions, migration risks, and unknowns that could block implementation. "
            "Highlight approval, persistence, and compatibility risks when relevant."
        ),
    }
    definitions: list[_UltraplanScoutDefinition] = []
    for category in categories:
        prompt = prompts.get(category)
        if prompt is None:
            continue
        description = category.replace("-", " ")
        definitions.append(
            _UltraplanScoutDefinition(
                category=category,
                description=f"Scout {description} for goal: {goal}",
                prompt=(
                    f"Goal: {goal}\n\n"
                    f"{prompt}\n\n"
                    "Return a concise report with observations, impacted files/modules, risks, and concrete recommendations."
                ),
            )
        )
    return tuple(definitions)


def _build_ultraplan_synthesis_prompt(
    goal: str,
    scout_results: dict[str, str],
    *,
    previous_plan: PlanningArtifact | None = None,
) -> str:
    sections = [
        "You are synthesizing a multi-scout implementation plan.",
        "Use the scout reports below to produce one integrated plan.",
        "Return markdown with exactly these top-level sections:",
        "1. Current Architecture",
        "2. Implementation Plan",
        "3. Risks / Open Questions",
        "4. Verification Checklist",
        "",
        f"Goal: {goal}",
        "",
    ]
    if previous_plan is not None:
        sections.extend(
            [
                "Previous active plan to revise:",
                f"- artifact_id: {previous_plan.artifact_id}",
                f"- goal: {previous_plan.goal}",
                "- summary:",
                previous_plan.summary.strip() or "(empty prior summary)",
                "",
            ]
        )
    sections.append("Scout reports:")
    for category, content in scout_results.items():
        sections.extend(
            [
                f"## {category}",
                content.strip() or "(empty scout result)",
                "",
            ]
        )
    sections.extend(
        [
            "Requirements:",
            "- Ground the plan in the scout findings instead of ignoring them.",
            "- If a previous active plan is provided, explicitly preserve its valid parts and replace weak ones.",
            "- Name concrete modules/files when possible.",
            "- Keep the plan actionable and ordered.",
            "- Verification Checklist should be a flat checklist.",
        ]
    )
    return "\n".join(sections)


def _summarize_planning_artifact(text: str, *, max_chars: int = 600) -> str:
    stripped = text.strip()
    if len(stripped) <= max_chars:
        return stripped
    return stripped[: max_chars - 3] + "..."


def _prepend_advisor_review_to_plan(text: str, review: AdvisorReviewSummary) -> str:
    lines = [
        "Advisor Review",
        f"- status: {review.status}",
    ]
    if review.reason:
        lines.append(f"- reason: {review.reason}")
    if review.risk_flags:
        lines.append("- risk_flags: " + ", ".join(review.risk_flags))
    if review.suggested_changes:
        lines.append("- suggested_changes:")
        lines.extend(f"  - {item}" for item in review.suggested_changes)
    return "\n".join(lines) + "\n\n" + text.strip()
