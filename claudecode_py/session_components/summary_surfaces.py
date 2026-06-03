from __future__ import annotations

from pathlib import Path
from typing import Any

from ..context_usage import collect_context_usage
from ..providers import format_capabilities


def _status_section(title: str, lines: list[str]) -> list[str]:
    rendered = [f"{title}:"]
    rendered.extend(lines or ["- none"])
    return rendered


def _append_status_section(target: list[str], title: str, lines: list[str]) -> None:
    if target:
        target.append("")
    target.extend(_status_section(title, lines))


def _context_usage_summary(session: Any) -> str:
    report = collect_context_usage(session)
    return f"{report.total_tokens} / {report.max_tokens} ({report.percentage:.1f}%)"


def _status_model_provider_lines(session: Any) -> list[str]:
    execution_contract = session.execution_contract_payload()
    return [
        f"provider: {session.config.provider}",
        f"model: {session.config.model}",
        f"advisor model: {session.state.advisor_model or session.config.model}",
        f"advisor mode: {session.state.advisor_mode}",
        f"session mode: {execution_contract['session_execution_mode']}",
        f"context usage: {_context_usage_summary(session)}",
    ]


def _status_memory_lifecycle_lines(
    session: Any,
    *,
    latest_compact_boundary: Any | None,
    compaction_policy: dict[str, Any],
    memory_payload: dict[str, Any],
    history_state: dict[str, Any],
) -> list[str]:
    narrative = session._runtime_narrative_payload(
        compaction_policy=compaction_policy,
        latest_compact_boundary=latest_compact_boundary,
    )
    lines = [
        *session._history_state_lines(history_state),
        *session._runtime_compact_lifecycle_narrative_lines(narrative=narrative),
        *session._runtime_budget_narrative_lines(narrative=narrative),
        "tool-result artifacts: "
        + str(memory_payload.get("memory_tool_result_artifacts") or 0),
        "tool-result replacements: "
        + str(memory_payload.get("memory_tool_result_replacements") or 0),
        "replacement-aware compaction: "
        + str(memory_payload.get("memory_replacement_aware_compaction") or "no"),
        "latest memory operation: "
        + str(memory_payload.get("memory_last_operation") or "none"),
        "latest memory summary: "
        + str(memory_payload.get("memory_last_operation_summary") or "none"),
    ]
    lines.extend(
        session._history_lifecycle_lines(
            list(session.state.history_boundaries),
            latest_compact_trigger=(
                None
                if narrative["compact"]["latest_compact_trigger"] == "none"
                else narrative["compact"]["latest_compact_trigger"]
            ),
            latest_compact_reason=narrative["compact"]["latest_compact_reason"],
            latest_compact_summary=narrative["compact"]["latest_compact_summary"],
        )
    )
    return lines


def _status_background_notification_lines(background_handoff: dict[str, Any]) -> list[str]:
    return [
        "background notifications: "
        + str(background_handoff.get("background_handoff_count") or 0),
        "latest background handoff: "
        + str(background_handoff.get("background_handoff_selected_bg_id") or "none"),
        "latest background state: "
        + str(background_handoff.get("background_handoff_selected_completion_state") or "none"),
        "latest background summary: "
        + str(background_handoff.get("background_handoff_selected_completion_summary") or "none"),
    ]


def _status_workspace_state_lines(
    session: Any,
    *,
    file_items: list[dict[str, Any]],
    focused_item: dict[str, Any] | None,
    explicit_counts: dict[str, int],
) -> list[str]:
    file_count = len(file_items)
    workspace_health_payload = session._status_runtime_health_payload()
    recommended_actions = session._workspace_recommended_actions(
        workspace_health=session.state.workspace_health,
        workspace_label=session.state.workspace_label,
        session_id=session.state.session_id,
    )
    workspace_action_fields = session._workspace_session_action_fields(
        workspace_health=session.state.workspace_health,
        workspace_label=session.state.workspace_label,
        session_id=session.state.session_id,
    )
    lines = [
        f"workspace state: mode={session.state.workspace_mode} health={session.state.workspace_health}",
        "workspace anomaly: "
        + str(workspace_health_payload.get("status_workspace_anomaly") or "none"),
        "workspace recovery: " + (recommended_actions[0] if recommended_actions else "none"),
        "workspace_recommended_actions: "
        + (", ".join(recommended_actions) if recommended_actions else "none"),
        f"selected_workspace_primary_action: {workspace_action_fields['selected_workspace_primary_action']}",
        f"selected_workspace_secondary_action: {workspace_action_fields['selected_workspace_secondary_action']}",
        f"selected_workspace_tertiary_action: {workspace_action_fields['selected_workspace_tertiary_action']}",
        f"working set: {file_count} file(s)",
        session._render_file_context_mix_line(file_items),
        f"focused file: {str(focused_item.get('path') or 'none') if focused_item is not None else 'none'}",
        f"focused file source: {str(focused_item.get('source') or 'none') if focused_item is not None else 'none'}",
        "focused file in scope because: "
        + (
            ", ".join(session._file_context_scope_reasons(focused_item))
            if focused_item is not None and session._file_context_scope_reasons(focused_item)
            else "none"
        ),
        "focused file context origin: "
        + (
            session._file_context_origin_label(focused_item)
            if focused_item is not None
            else "none"
        ),
        f"explicit context entries: {explicit_counts['entry_count']}",
        f"unresolved explicit context entries: {explicit_counts['unresolved_entry_count']}",
        f"explicit-context files: {explicit_counts['explicit_file_count']}",
        f"explicit-only files: {explicit_counts['explicit_only_file_count']}",
        f"automatic-only files: {explicit_counts['automatic_file_count']}",
        f"overlapping files: {explicit_counts['overlapping_file_count']}",
    ]
    return lines


def _status_active_workflow_lines(
    session: Any,
    *,
    artifact: Any | None,
    task_surfaces: dict[str, Any],
    checklist_stats: dict[str, int],
    workflow: bool = False,
) -> list[str]:
    active_task_total = sum(
        count
        for key, count in task_surfaces.items()
        if key not in {"completed", "failed", "blocked", "stopped"}
    )
    task_surface_summary = (
        ", ".join(f"{name}={count}" for name, count in sorted(task_surfaces.items()) if count)
        if task_surfaces
        else "none"
    )
    runtime_narrative = session._runtime_narrative_payload()
    tool_schema_surface = session.tool_schema_surface_payload()
    lines = [
        f"active plan: {artifact.goal if artifact is not None else 'none'}",
        f"active task: {active_task_total}",
        f"task surfaces: {task_surface_summary}",
        *session._runtime_progress_narrative_lines(
            narrative=runtime_narrative,
            workflow=False,
        ),
        f"recorded changes: {len(session.state.recent_change_sets)}",
        f"redo changes: {len(session.state.undone_change_sets)}",
        f"checklist in progress: {checklist_stats['in_progress']}",
    ]
    if workflow:
        lines.extend(
            session._runtime_progress_narrative_lines(
                narrative=runtime_narrative,
                workflow=True,
            )[2:]
        )
        lines.extend(
            [
                "tool schema cache: "
                + (
                    f"cached={tool_schema_surface.get('tool_schema_cached_count', 0)} "
                    f"key={tool_schema_surface.get('tool_schema_cache_key', 'none')} "
                    f"epoch={tool_schema_surface.get('tool_schema_registry_epoch', 0)}"
                ),
                "tool schema order: "
                + str(tool_schema_surface.get("tool_schema_order_summary") or "none"),
                "tool-result replacement: "
                + str(
                    session.runtime_progress_surface_payload().get(
                        "runtime_tool_result_replacement_summary"
                    )
                    or session.tool_result_replacement_surface_payload().get("replacement_last_summary")
                    or "none"
                ),
                "tool-result artifact: "
                + str(
                    session.runtime_progress_surface_payload().get(
                        "runtime_tool_result_artifact_summary"
                    )
                    or session.tool_result_artifact_surface_payload().get("artifact_last_summary")
                    or "none"
                ),
                "tool-result microcompact: "
                + str(
                    session.runtime_progress_surface_payload().get(
                        "runtime_tool_result_microcompact_summary"
                    )
                    or "none"
                ),
            ]
        )
    return lines

def _status_project_context_health_lines(session: Any) -> list[str]:
    health_payload = session._status_runtime_health_payload()
    return [
        "project context: " + str(health_payload.get("status_project_context_summary") or "none"),
        "project-context reload health: "
        + str(health_payload.get("status_project_context_reload_health") or "latest reload: none"),
        "project-context issue: " + str(health_payload.get("status_project_context_issue") or "none"),
        "skill registry: " + str(health_payload.get("status_skill_registry_summary") or "none"),
        "skill prompt composition: " + str(health_payload.get("status_skill_prompt_summary") or "none"),
        "skill reload state: " + str(health_payload.get("status_skill_reload_state") or "latest reload: none"),
        "manual skill overrides: "
        + str(health_payload.get("status_skill_manual_overrides") or "enabled=0 disabled=0"),
        "skill diagnostics: " + str(health_payload.get("status_skill_diagnostics") or 0),
        "plugins health: " + str(health_payload.get("status_plugins_health") or "none"),
        "plugin registry: " + str(health_payload.get("status_plugin_registry_summary") or "none"),
        "plugin reload state: " + str(health_payload.get("status_plugin_reload_state") or "latest reload: none"),
        "manual plugin overrides: "
        + str(health_payload.get("status_plugin_manual_overrides") or "enabled=0 disabled=0"),
        "mcp health: " + str(health_payload.get("status_mcp_health") or "none"),
        "mcp issue: " + str(health_payload.get("status_mcp_issue") or "none"),
        "permission mode: " + str(health_payload.get("status_permission_mode") or "default"),
        "permission summary: " + str(health_payload.get("status_permission_summary") or "none"),
        "workspace anomaly: " + str(health_payload.get("status_workspace_anomaly") or "none"),
        "runtime health alert: " + str(health_payload.get("status_runtime_health_alert") or "none"),
    ]


def _status_next_action_lines(
    session: Any,
    *,
    background_handoff: dict[str, Any],
    workflow: bool,
) -> list[str]:
    return session.render_status_action_family_lines(
        session._status_action_groups_payload(workflow=workflow),
        resume=False,
    )


def describe_provider(session: Any, *, section: str = "summary") -> str:
    if section not in {"summary", "capabilities", "advisor"}:
        return "Usage: /model [summary|capabilities|advisor]"
    capabilities = getattr(session.provider, "capabilities", None)
    if capabilities is None:
        capability_text = (
            f"provider: {session.config.provider}\n"
            f"model: {session.config.model}\n"
            "notes: provider does not declare capabilities"
        )
    else:
        capability_text = format_capabilities(capabilities)
    if section == "capabilities":
        return capability_text
    if section == "advisor":
        advisor_model = session.state.advisor_model or session.config.model
        relationship = (
            "shared-runtime-model"
            if not session.state.advisor_model or session.state.advisor_model == session.config.model
            else "separate-advisor-model"
        )
        return "\n".join(
            [
                "current session:",
                f"provider: {session.config.provider}",
                f"runtime model: {session.config.model}",
                f"advisor_model: {advisor_model}",
                f"advisor_mode: {session.state.advisor_mode}",
                f"advisor_relationship: {relationship}",
                "active_plan: " + ("yes" if session.active_planning_artifact() is not None else "no"),
            ]
        )
    lines: list[str] = []
    _append_status_section(lines, "model and provider", _status_model_provider_lines(session))
    if capabilities is None:
        lines.append("")
        lines.append("provider capabilities: provider does not declare capabilities")
    else:
        lines.append("")
        lines.append("provider capabilities:")
        lines.extend(capability_text.splitlines())
    return "\n".join(lines)


def describe_config(session: Any, *, section: str = "summary") -> str:
    if section not in {"summary", "workspace", "runtime", "permissions", "plugins", "mcp"}:
        return "Usage: /config [summary|workspace|runtime|permissions|plugins|mcp]"
    counts = session._mcp_server_counts()
    checklist_stats = session.checklist_stats()
    execution_contract = session.execution_contract_payload()
    effective_cwd = session.state.effective_cwd or str(session.config.cwd)
    workspace_effective_exists = Path(effective_cwd).exists() if effective_cwd else False
    recommended_actions = session._workspace_recommended_actions(
        workspace_health=session.state.workspace_health,
        workspace_label=session.state.workspace_label,
        session_id=session.state.session_id,
    )
    workspace_action_fields = session._workspace_session_action_fields(
        workspace_health=session.state.workspace_health,
        workspace_label=session.state.workspace_label,
        session_id=session.state.session_id,
    )
    workspace_lines = [
        f"cwd: {session.config.cwd}",
        f"original_cwd: {session.state.original_cwd or session.config.transcript_cwd or session.config.cwd}",
        f"effective_cwd: {effective_cwd}",
        f"workspace_mode: {session.state.workspace_mode}",
        f"workspace_label: {session.state.workspace_label or 'none'}",
        f"workspace_created_at: {session.state.workspace_created_at or 'none'}",
        f"workspace_health: {session.state.workspace_health}",
        "workspace_anomaly: "
        + str(session._status_runtime_health_payload().get("status_workspace_anomaly") or "none"),
        "workspace_recovery: " + (recommended_actions[0] if recommended_actions else "none"),
        f"workspace_cleanup_status: {session.state.workspace_cleanup_status}",
        f"workspace_cleanup_error: {session.state.workspace_cleanup_error or 'none'}",
        f"workspace_effective_cwd_exists: {'yes' if workspace_effective_exists else 'no'}",
        f"workspace_unavailable: {'yes' if session.state.workspace_unavailable else 'no'}",
        f"workspace_unavailable_reason: {session.state.workspace_unavailable_reason or 'none'}",
        f"workspace_fallback_cwd: {session.state.workspace_fallback_cwd or 'none'}",
        "workspace_recommended_action: " + (recommended_actions[0] if recommended_actions else "none"),
        "workspace_recommended_actions: "
        + (", ".join(recommended_actions) if recommended_actions else "none"),
        f"selected_workspace_primary_action: {workspace_action_fields['selected_workspace_primary_action']}",
        f"selected_workspace_secondary_action: {workspace_action_fields['selected_workspace_secondary_action']}",
        f"selected_workspace_tertiary_action: {workspace_action_fields['selected_workspace_tertiary_action']}",
        f"selected_workspace_target: {workspace_action_fields['selected_workspace_target']}",
        *session._render_orphaned_workspace_lines(),
        f"primary action: {workspace_action_fields['selected_workspace_primary_action']}",
        f"secondary action: {workspace_action_fields['selected_workspace_secondary_action']}",
        f"tertiary action: {workspace_action_fields['selected_workspace_tertiary_action']}",
    ]
    mcp_lines = [
        f"provider: {session.config.provider}",
        f"mcp_config_path: {session.config.mcp_config_path}",
        f"mcp_servers: {counts['servers']}",
        f"mcp_connected_servers: {counts['connected']}",
        f"mcp_failed_servers: {counts['failed']}",
        f"mcp_retrying_servers: {counts['retrying']}",
    ]
    plugin_lines = [
        "skill registry: "
        + str(session._status_runtime_health_payload().get("status_skill_registry_summary") or "none"),
        "skill diagnostics: "
        + str(session._status_runtime_health_payload().get("status_skill_diagnostics") or 0),
        "skill reload state: "
        + str(session._status_runtime_health_payload().get("status_skill_reload_state") or "latest reload: none"),
        "manual skill overrides: "
        + str(session._status_runtime_health_payload().get("status_skill_manual_overrides") or "enabled=0 disabled=0"),
        "plugin registry: "
        + str(session._status_runtime_health_payload().get("status_plugin_registry_summary") or "none"),
        "plugin diagnostics: "
        + str(session.plugin_surface_payload().get("plugin_diagnostic_count") or 0),
        "plugin reload state: "
        + str(session._status_runtime_health_payload().get("status_plugin_reload_state") or "latest reload: none"),
        "manual plugin overrides: "
        + str(session._status_runtime_health_payload().get("status_plugin_manual_overrides") or "enabled=0 disabled=0"),
        f"project_memory: {'loaded' if session.project_context.memory_content else 'none'}",
    ]
    permission_lines = [
        f"permission_config_path: {session._permission_config_path()}",
        f"workspace_permission_rules: {len(session._workspace_permission_rules)}",
        f"session_permission_rules: {len(session.permission_manager.session_rules)}",
        f"permission_mode: {session.config.permission_mode}",
    ]
    runtime_lines = [
        f"provider: {session.config.provider}",
        f"model: {session.config.model}",
        f"advisor_model: {session.state.advisor_model or 'none'}",
        f"advisor_mode: {session.state.advisor_mode}",
        f"advisor_reviews: {len(session.state.advisor_review_history)}",
        f"advisor_blocks: {session.advisor_block_count()}",
        f"planning_artifacts: {len(session.planning_artifacts())}",
        "active_planning_artifact_id: " + str(session.state.active_planning_artifact_id or "none"),
        f"recent_change_sets: {len(session.state.recent_change_sets)}",
        f"redo_change_sets: {len(session.state.undone_change_sets)}",
        f"session_checklist_tasks: {checklist_stats['total']}",
        f"session_checklist_in_progress: {checklist_stats['in_progress']}",
        *session._symbol_surface_config_fields(),
        f"execution_constraints: {session.state.active_execution_constraint}",
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
        f"last_plan_drift_summary: {session._recent_plan_drift_summary() or 'none'}",
        f"max_tokens: {session.config.max_tokens}",
        f"max_turns: {session.config.max_turns}",
        f"max_tool_rounds_per_turn: {session.config.max_tool_rounds_per_turn}",
        f"max_history_messages: {session.config.max_history_messages}",
        f"history_keep_last_messages: {session.config.history_keep_last_messages}",
        f"max_context_summary_chars: {session.config.max_context_summary_chars}",
        f"session_id: {session.state.session_id}",
    ]
    planning_lines = session.describe_planning_lifecycle()[2:]
    if section == "workspace":
        return "\n".join(["workspace state:", *workspace_lines])
    if section == "runtime":
        return "\n".join(["active workflow:", *runtime_lines, *planning_lines])
    if section == "permissions":
        return "\n".join(["execution state:", *permission_lines])
    if section == "plugins":
        return "\n".join(["project-context health:", *plugin_lines])
    if section == "mcp":
        return "\n".join(["model and provider:", *mcp_lines])
    lines: list[str] = []
    _append_status_section(
        lines,
        "session identity",
        [
            f"session_id: {session.state.session_id}",
            f"workspace state: mode={session.state.workspace_mode} health={session.state.workspace_health}",
            f"workspace label: {session.state.workspace_label or 'none'}",
        ],
    )
    _append_status_section(lines, "model and provider", _status_model_provider_lines(session))
    _append_status_section(lines, "workspace state", workspace_lines)
    _append_status_section(lines, "project-context health", plugin_lines)
    _append_status_section(lines, "execution state", permission_lines + runtime_lines + planning_lines)
    _append_status_section(lines, "mcp state", mcp_lines)
    return "\n".join(lines)


def describe_status(session: Any, *, section: str = "summary") -> str:
    if section not in {"summary", "workspace", "workflow", "resume"}:
        return "Usage: /status [summary|workspace|workflow|resume]"
    if section == "workspace":
        return session.describe_current_workspace()
    if section == "workflow":
        return session._describe_status_workflow()
    if section == "resume":
        return session._describe_status_resume()
    return session._describe_status_summary()


def describe_status_summary(session: Any) -> str:
    artifact = session.active_planning_artifact()
    working_set = session.working_set_payload(limit=5)
    focused_payload = session._current_context_focus_payload()
    task_surfaces = session.task_surface_counts_payload()
    checklist_stats = session.checklist_stats()
    explicit_entries = session._explicit_context_entries_payloads()
    file_items = [
        item for item in working_set.get("file_context_files", []) if isinstance(item, dict)
    ]
    _focused_files, _focused_index, focused_item = session._file_context_items_and_index(focused_payload)
    explicit_counts = session._explicit_context_summary_counts(
        entries=explicit_entries,
        files=file_items,
        total_file_count=len(file_items),
    )
    history_state = session._history_state_payload(
        message_count=len(session.state.messages),
        context_summary=session.state.context_summary,
    )
    compaction_policy = session.compaction_policy_payload()
    memory_payload = session.memory_surface_payload()
    latest_compact_boundary = session._latest_history_boundary(kind="compact")
    background_handoff = session.background_handoff_payload() if hasattr(session, "background_handoff_payload") else {}
    focused_path = str(focused_item.get("path") or "none") if focused_item is not None else "none"
    focused_source = str(focused_item.get("source") or "none") if focused_item is not None else "none"
    file_count = int(working_set.get("file_context_file_count") or 0)
    active_task_total = sum(
        count
        for key, count in task_surfaces.items()
        if key not in {"completed", "failed", "blocked", "stopped"}
    )
    lines: list[str] = []
    _append_status_section(
        lines,
        "session identity",
        [
            f"session_id: {session.state.session_id}",
            f"workspace state: mode={session.state.workspace_mode} health={session.state.workspace_health}",
            f"workspace label: {session.state.workspace_label or 'none'}",
        ],
    )
    _append_status_section(lines, "model and provider", _status_model_provider_lines(session))
    _append_status_section(
        lines,
        "memory lifecycle",
        _status_memory_lifecycle_lines(
            session,
            latest_compact_boundary=latest_compact_boundary,
            compaction_policy=compaction_policy,
            memory_payload=memory_payload,
            history_state=history_state,
        ),
    )
    _append_status_section(
        lines,
        "background notifications",
        _status_background_notification_lines(background_handoff),
    )
    _append_status_section(
        lines,
        "workspace state",
        _status_workspace_state_lines(
            session,
            file_items=file_items,
            focused_item=focused_item,
            explicit_counts=explicit_counts,
        ),
    )
    _append_status_section(
        lines,
        "active workflow",
        _status_active_workflow_lines(
            session,
            artifact=artifact,
            task_surfaces=task_surfaces,
            checklist_stats=checklist_stats,
            workflow=False,
        ),
    )
    _append_status_section(lines, "project-context health", _status_project_context_health_lines(session))
    _append_status_section(
        lines,
        "next actions",
        _status_next_action_lines(
            session,
            background_handoff=background_handoff,
            workflow=False,
        ),
    )
    return "\n".join(lines)


def describe_status_workflow(session: Any) -> str:
    task_surfaces = session.task_surface_counts_payload()
    artifact = session.active_planning_artifact()
    explicit_entries = session._explicit_context_entries_payloads()
    full_working_set_payload = session.working_set_payload(limit=20)
    full_file_items = [
        item for item in full_working_set_payload.get("file_context_files", []) if isinstance(item, dict)
    ]
    working_set_payload = session.working_set_payload(limit=3)
    focused_payload = session._current_context_focus_payload()
    file_items = [
        item for item in working_set_payload.get("file_context_files", []) if isinstance(item, dict)
    ]
    _focused_files, _focused_index, focused_item = session._file_context_items_and_index(focused_payload)
    explicit_counts = session._explicit_context_summary_counts(
        entries=explicit_entries,
        files=full_file_items,
        total_file_count=len(full_file_items),
    )
    history_state = session._history_state_payload(
        message_count=len(session.state.messages),
        context_summary=session.state.context_summary,
    )
    compaction_policy = session.compaction_policy_payload()
    memory_payload = session.memory_surface_payload()
    latest_compact_boundary = session._latest_history_boundary(kind="compact")
    background_handoff = session.background_handoff_payload() if hasattr(session, "background_handoff_payload") else {}
    lines: list[str] = []
    _append_status_section(
        lines,
        "session identity",
        [
            f"session_id: {session.state.session_id}",
            f"workspace state: mode={session.state.workspace_mode} health={session.state.workspace_health}",
            f"workspace label: {session.state.workspace_label or 'none'}",
        ],
    )
    _append_status_section(lines, "model and provider", _status_model_provider_lines(session))
    memory_lines = _status_memory_lifecycle_lines(
        session,
        latest_compact_boundary=latest_compact_boundary,
        compaction_policy=compaction_policy,
        memory_payload=memory_payload,
        history_state=history_state,
    )
    memory_lines.extend(
        session.render_summary_field_lines(
            [
                (
                    "latest memory focus policy",
                    memory_payload.get("memory_last_operation_task_plan_file_focus") or "none",
                ),
                (
                    "latest memory advisor policy",
                    memory_payload.get("memory_last_operation_advisor_review_state") or "none",
                ),
            ],
        )
    )
    _append_status_section(lines, "memory lifecycle", memory_lines)
    _append_status_section(
        lines,
        "background notifications",
        _status_background_notification_lines(background_handoff),
    )
    workspace_lines = _status_workspace_state_lines(
        session,
        file_items=file_items,
        focused_item=focused_item,
        explicit_counts=explicit_counts,
    )
    workspace_lines.append(session._render_file_context_mix_line(file_items))
    if focused_item is not None:
        workspace_lines.extend(
            [
                f"focused diff hunks: {session._file_context_diff_hunk_count(focused_item)}",
            ]
        )
    _append_status_section(lines, "workspace state", workspace_lines)
    active_workflow_lines = _status_active_workflow_lines(
        session,
        artifact=artifact,
        task_surfaces=task_surfaces,
        checklist_stats=session.checklist_stats(),
        workflow=True,
    )
    active_workflow_lines.extend(
        session.render_summary_field_lines(
            [
                ("planning artifacts", len(session.planning_artifacts())),
                ("advisor activity", len(session.state.advisor_review_history)),
            ],
        )
    )
    _append_status_section(lines, "active workflow", active_workflow_lines)
    _append_status_section(lines, "project-context health", _status_project_context_health_lines(session))
    working_set_lines = session._render_file_context_lines(
        working_set_payload,
        title="Working set",
    )
    if working_set_lines:
        lines.extend(["", *working_set_lines])
    status_action_groups = (
        session._file_context_item_action_groups(
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
    status_action_groups["go_to_change"] = session._dedupe_action_commands(
        [
            *status_action_groups.get("go_to_change", []),
            "/changes working-set",
            str(background_handoff.get("background_handoff_changes_action") or "/changes working-set"),
        ]
    )
    status_action_groups["go_to_task"] = session._dedupe_action_commands(
        [
            *status_action_groups.get("go_to_task", []),
            "/tasks active",
            str(background_handoff.get("background_handoff_task_action") or "/tasks active"),
        ]
    )
    status_action_groups["go_to_plan"] = session._dedupe_action_commands(
        [
            *status_action_groups.get("go_to_plan", []),
            "/plan",
            str(background_handoff.get("background_handoff_resume_action") or "/plan"),
        ]
    )
    next_action_lines = _status_next_action_lines(
        session,
        background_handoff=background_handoff,
        workflow=True,
    )
    next_action_lines.append("")
    next_action_lines.extend(
        session._render_action_group_lines(
            status_action_groups,
            ordered_keys=("go_to_change", "go_to_task", "go_to_plan", "stay_on_surface"),
        )
    )
    _append_status_section(lines, "next actions", next_action_lines)
    return "\n".join(lines)


def describe_status_resume(session: Any) -> str:
    session_id = session.state.session_id
    history_state = session._history_state_payload(
        message_count=len(session.state.messages),
        context_summary=session.state.context_summary,
    )
    saved_summary = session.describe_saved_sessions(selector=session_id, section="summary")
    saved_resumable = not saved_summary.startswith("No saved session found")
    memory_payload = session.memory_surface_payload()
    latest_compact_boundary = session._latest_history_boundary(kind="compact")
    continuation = session._saved_resume_semantics(
        session_id=session_id,
        saved_resumable=saved_resumable,
        stay_on_surface="/status resume | /sessions show latest",
    )
    resume_path = f"pyclaude --resume-session {session_id} repl" if saved_resumable else "unavailable"
    resume_tui_path = f"pyclaude --resume-session {session_id} tui" if saved_resumable else "unavailable"
    compaction_policy = session.compaction_policy_payload()
    lines: list[str] = []
    _append_status_section(
        lines,
        "session identity",
        [
            f"session_id: {session_id}",
            "resume state: "
            + ("saved resumable" if saved_resumable else "not yet persisted"),
            f"workspace state: mode={session.state.workspace_mode} health={session.state.workspace_health}",
        ],
    )
    _append_status_section(lines, "model and provider", _status_model_provider_lines(session))
    memory_lines = _status_memory_lifecycle_lines(
        session,
        latest_compact_boundary=latest_compact_boundary,
        compaction_policy=compaction_policy,
        memory_payload=memory_payload,
        history_state=history_state,
    )
    memory_lines.extend(session._memory_operation_surface_policy_lines("resume"))
    _append_status_section(lines, "memory lifecycle", memory_lines)
    _append_status_section(lines, "background notifications", ["background notifications: 0"])
    _append_status_section(
        lines,
        "workspace state",
        [
            f"resume path: {resume_path}",
            f"resume tui path: {resume_tui_path}",
        ],
    )
    _append_status_section(
        lines,
        "active workflow",
        [
            *_status_active_workflow_lines(
                session,
                artifact=session.active_planning_artifact(),
                task_surfaces=session.task_surface_counts_payload(),
                checklist_stats=session.checklist_stats(),
                workflow=False,
            ),
            f"continuation category: {continuation['continuation_category']}",
            f"go_to_live_attach: {continuation['go_to_live_attach']}",
            f"go_to_saved_resume: {continuation['go_to_saved_resume']}",
            f"stay_on_surface: {continuation['stay_on_surface']}",
        ],
    )
    _append_status_section(lines, "project-context health", _status_project_context_health_lines(session))
    resume_actions = session._status_action_groups_payload(
        workflow=False,
        session_id=session_id,
        saved_resumable=saved_resumable,
    )
    _append_status_section(
        lines,
        "next actions",
        [
            *session.render_status_action_family_lines(resume_actions, resume=True),
            "- inspect status summary: /status summary",
        ],
    )
    if saved_resumable:
        lines.extend(["", saved_summary])
    else:
        lines.extend(["", "saved session: not yet persisted"])
    return "\n".join(lines)
