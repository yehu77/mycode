from __future__ import annotations

from pathlib import Path
from typing import Any

from ..storage.session_checklist import ChecklistTask
from ..text_utils import compact_multiline_text, summarize_text_diff


READ_ONLY_COMMAND_POLICY_NAMES = frozenset(
    {
        "review",
        "security-review",
        "ultraplan",
        "read-only-subagent",
        "read-only-turn",
    }
)
_TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "stopped", "canceled", "deleted"})
_TASKS_WORKFLOW_MODES = frozenset({"list", "active", "changes", "context"})


def _workspace_action_bundle(
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


def _checklist_recommended_actions(
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


def _checklist_action_bundle(
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


class TaskDetailSessionComponent:
    def __init__(self, session: Any) -> None:
        self._session = session

    def _task_surface_kind(self, task: Any) -> str:
        metadata = task.metadata or {}
        if str(metadata.get("workspace_action") or "").strip() or str(task.kind).strip() == "workspace":
            return "workspace_maintenance"
        task_role = str(metadata.get("task_role") or "").strip()
        plan_execution_mode = str(metadata.get("plan_execution_mode") or "").strip()
        child_execution_mode = str(metadata.get("child_execution_mode") or "").strip()
        if (
            task_role == "background"
            or plan_execution_mode == "background_agent"
            or child_execution_mode == "background-agent"
        ):
            return "background_execution"
        if task_role == "scout" or child_execution_mode in {"child-session", "read-only-subagent"}:
            return "child_execution"
        if task_role == "execution" or str(task.kind).strip() == "plan_execution":
            return "active_plan_execution"
        return "other_task"

    def _task_surface_header(self, surface_kind: str) -> str:
        return {
            "workspace_maintenance": "workspace maintenance tasks:",
            "child_execution": "child execution tasks:",
            "background_execution": "background execution tasks:",
            "active_plan_execution": "active plan execution tasks:",
            "other_task": "other tasks:",
        }.get(surface_kind, f"{surface_kind}:")

    def _background_reverse_hint_lines(self, metadata: dict[str, Any]) -> list[str]:
        background_session_id = str(metadata.get("background_session_id") or "").strip()
        background_reverse_hint = str(metadata.get("background_reverse_hint") or "").strip()
        parent_session_id = str(metadata.get("parent_session_id") or "").strip()
        task_role = str(metadata.get("task_role") or "").strip()
        plan_execution_mode = str(metadata.get("plan_execution_mode") or "").strip()
        child_execution_mode = str(metadata.get("child_execution_mode") or "").strip()
        is_background_linked = (
            task_role in {"background", "execution"}
            or plan_execution_mode == "background_agent"
            or child_execution_mode == "background-agent"
        )
        if not is_background_linked:
            return []
        lines = ["background_linkage:"]
        lines.append(f"- background_session_id: {background_session_id or 'none'}")
        if background_reverse_hint:
            lines.append(f"- background_reverse_hint: {background_reverse_hint}")
        elif parent_session_id:
            lines.append(
                "- background_reverse_hint: "
                + f"owning_session={parent_session_id}; actions=/tasks active | /status workflow"
            )
        else:
            lines.append("- background_reverse_hint: /tasks active | /status workflow")
        return lines

    def _task_surface_counts_payload(self) -> dict[str, int]:
        session = self._session
        counts = {
            "checklist": session.checklist_stats()["total"],
            "workspace_maintenance": 0,
            "child_execution": 0,
            "background_execution": 0,
            "active_plan_execution": 0,
            "other_task": 0,
        }
        for task in session.task_manager.list():
            surface_kind = self._task_surface_kind(task)
            counts[surface_kind] = counts.get(surface_kind, 0) + 1
        return counts

    def task_surface_counts_payload(self) -> dict[str, int]:
        return dict(self._task_surface_counts_payload())

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

    def resolve_task(self, identifier: str) -> Any | None:
        raw = identifier.strip()
        if not raw:
            return None
        tasks = self._session.task_manager.list()
        matches = [task for task in tasks if task.id == raw or task.id.startswith(raw)]
        if len(matches) == 1:
            return matches[0]
        return None

    def describe_tasks(self, *, mode: str = "list") -> str:
        if mode in _TASKS_WORKFLOW_MODES and mode != "list":
            return self._describe_task_workflow_overview(mode=mode)
        session = self._session
        tasks = session.task_manager.list()
        checklist_lines = self._render_session_checklist_lines()
        checklist_stats = session.checklist_stats()
        latest_artifact = session.active_planning_artifact()
        lines = ["planning lifecycle:"]
        lines.extend(session.describe_planning_lifecycle())
        lines.append("")
        lines.extend(self.task_surface_summary_lines())
        workflow_lines = self._render_task_workflow_overview(
            self._task_workflow_entries(),
            title="task workflow overview:",
        )
        if workflow_lines:
            lines.append("")
            lines.extend(workflow_lines)
        lines.append("")
        lines.append("workspace diagnostics:")
        lines.extend(session._render_orphaned_workspace_lines())
        audit_lines = session._recent_workspace_audit_history_lines()
        if audit_lines:
            lines.append("")
            lines.append("workspace_audit:")
            lines.extend(audit_lines)
        lines.append("")
        lines.append("session_checklist:")
        lines.extend(checklist_lines)
        if not tasks:
            lines.append("")
            lines.append("No background tasks." if checklist_stats["total"] else "No tasks.")
            return "\n".join(lines)
        grouped: list[tuple[str, list[Any]]] = []
        grouped_tasks: dict[str, list[Any]] = {
            "workspace_maintenance": [],
            "child_execution": [],
            "background_execution": [],
            "active_plan_execution": [],
            "other_task": [],
        }
        for task in tasks:
            grouped_tasks.setdefault(self._task_surface_kind(task), []).append(task)
        for surface_kind in (
            "workspace_maintenance",
            "child_execution",
            "background_execution",
            "active_plan_execution",
            "other_task",
        ):
            task_group = grouped_tasks.get(surface_kind) or []
            if task_group:
                grouped.append((self._task_surface_header(surface_kind), task_group))
        for header, task_group in grouped:
            lines.append("")
            lines.append(header)
            for task in task_group:
                updated = task.updated_at or task.created_at
                summary = f"  progress={task.progress_summary}" if task.progress_summary else ""
                surface_kind = self._task_surface_kind(task)
                metadata_bits = []
                if latest_artifact is not None and (
                    task.id in latest_artifact.task_ids
                    or task.metadata.get("active_plan_id") == latest_artifact.artifact_id
                ):
                    metadata_bits.append("active_plan_task=yes")
                metadata_bits.append(f"task_surface={surface_kind}")
                if task.metadata.get("task_role"):
                    metadata_bits.append(f"task_role={task.metadata['task_role']}")
                if task.metadata.get("planner_kind"):
                    metadata_bits.append(f"planner={task.metadata['planner_kind']}")
                if task.metadata.get("scout_category"):
                    metadata_bits.append(f"scout={task.metadata['scout_category']}")
                if task.metadata.get("active_plan_id"):
                    metadata_bits.append(f"plan={task.metadata['active_plan_id']}")
                if task.metadata.get("plan_execution_mode"):
                    metadata_bits.append(f"mode={task.metadata['plan_execution_mode']}")
                if task.metadata.get("plan_execution_phase"):
                    metadata_bits.append(f"phase={task.metadata['plan_execution_phase']}")
                if task.metadata.get("plan_status"):
                    metadata_bits.append(f"plan_status={task.metadata['plan_status']}")
                if task.metadata.get("drift_status"):
                    metadata_bits.append(f"drift_status={task.metadata['drift_status']}")
                if task.metadata.get("constraint_source"):
                    metadata_bits.append(f"constraint={task.metadata['constraint_source']}")
                if task.metadata.get("child_execution_mode"):
                    metadata_bits.append(f"execution={task.metadata['child_execution_mode']}")
                if task.metadata.get("child_command_policy_name"):
                    metadata_bits.append(f"policy={task.metadata['child_command_policy_name']}")
                if task.metadata.get("child_command_policy_require_read_only_subagents"):
                    metadata_bits.append("read_only_subagents=yes")
                execution_detail = self._task_execution_contract_metadata(task)
                if execution_detail is not None:
                    if execution_detail["allowed_tools"]:
                        metadata_bits.append(
                            f"allowed_tools={len(execution_detail['allowed_tools'])}"
                        )
                    if execution_detail["allowed_bash_prefixes"]:
                        metadata_bits.append(
                            f"allowed_bash_prefixes={len(execution_detail['allowed_bash_prefixes'])}"
                        )
                if task.metadata.get("workspace_action"):
                    metadata_bits.append(f"workspace_action={task.metadata['workspace_action']}")
                if task.metadata.get("workspace_target"):
                    metadata_bits.append(f"workspace_target={task.metadata['workspace_target']}")
                if task.metadata.get("workspace_health_before"):
                    metadata_bits.append(f"health_before={task.metadata['workspace_health_before']}")
                if task.metadata.get("workspace_health_after"):
                    metadata_bits.append(f"health_after={task.metadata['workspace_health_after']}")
                metadata_bits.extend(self._workspace_summary_bits_from_metadata(task.metadata))
                metadata_suffix = f"  {' '.join(metadata_bits)}" if metadata_bits else ""
                lines.append(
                    f"{task.id}  status={task.status}  kind={task.kind}  updated={updated}  "
                    f"description={task.description}{summary}{metadata_suffix}"
                )
                workspace_action_fields = self._workspace_task_action_fields(task.metadata)
                if workspace_action_fields:
                    lines.append(
                        "  selected_workspace_primary_action: "
                        + workspace_action_fields["selected_workspace_primary_action"]
                    )
                    lines.append(
                        "  selected_workspace_secondary_action: "
                        + workspace_action_fields["selected_workspace_secondary_action"]
                    )
                    lines.append(
                        "  selected_workspace_tertiary_action: "
                        + workspace_action_fields["selected_workspace_tertiary_action"]
                    )
                    lines.append(
                        "  selected_workspace_target: "
                        + workspace_action_fields["selected_workspace_target"]
                    )
                permission_lines = session._render_task_permission_display_lines(task)
                if permission_lines:
                    lines.extend(permission_lines)
        return "\n".join(lines)

    def _describe_task_workflow_overview(self, mode: str) -> str:
        entries = self._task_workflow_entries(mode=mode)
        filter_label = "all" if mode == "list" else mode
        lines = self._render_task_workflow_overview(
            entries,
            title="task workflow overview:",
            filter_label=filter_label,
        )
        if lines:
            return "\n".join(lines)
        return "\n".join(
            [
                "task workflow overview:",
                f"filter: {filter_label}",
                "No matching tasks.",
            ]
        )

    def _task_workflow_entries(self, *, mode: str = "list") -> list[dict[str, Any]]:
        session = self._session
        entries: list[dict[str, Any]] = []
        for task in session.task_manager.list():
            entry = self._task_workflow_entry(task)
            if self._task_workflow_entry_matches_mode(entry, mode=mode):
                entries.append(entry)
        for task in session.checklist_tasks():
            entry = self._checklist_workflow_entry(task)
            if self._task_workflow_entry_matches_mode(entry, mode=mode):
                entries.append(entry)
        entries.sort(
            key=lambda entry: str(entry.get("updated_at") or entry.get("created_at") or ""),
            reverse=True,
        )
        return entries

    def _task_workflow_entry_matches_mode(self, entry: dict[str, Any], *, mode: str) -> bool:
        if mode == "list":
            return True
        if mode == "active":
            return not bool(entry.get("terminal"))
        if mode == "changes":
            return bool(entry.get("has_related_change"))
        if mode == "context":
            return bool(entry.get("is_context_only"))
        return False

    def _task_workflow_entry(self, task: Any) -> dict[str, Any]:
        session = self._session
        metadata = task.metadata or {}
        background_linkage_lines = self._background_reverse_hint_lines(metadata)
        background_session_id = str(metadata.get("background_session_id") or "").strip()
        background_reverse_hint = str(metadata.get("background_reverse_hint") or "").strip()
        context_selection = session.resolve_task_file_context(  # noqa: SLF001
            task.id,
            file_index=0,
            preserve_current_focus=True,
        )
        file_context_payload = context_selection["payload"]
        bounded_index = int(context_selection["selected_index"])
        focused_item = context_selection["focused_item"]
        related_change = ""
        diff_hunks = 0
        context_only = False
        focused_file = ""
        scope_reasons: list[str] = []
        change_actions: list[str] = []
        if focused_item is not None:
            focused_file = str(focused_item.get("path") or "").strip()
            related_change = str(focused_item.get("change_id") or "").strip()
            diff_hunks = session._file_context_diff_hunk_count(focused_item)
            context_only = session._file_context_is_context_only(focused_item)
            scope_reasons = session._file_context_scope_reasons(focused_item)
            change_navigation = session._resolve_change_navigation_for_file_context_item(focused_item)
            if change_navigation is not None:
                change_actions.append(str(change_navigation["change_command"]))
                file_command = change_navigation.get("change_file_command")
                if isinstance(file_command, str) and file_command.strip():
                    change_actions.append(file_command)
        task_actions, plan_actions = self._task_surface_navigation_actions(
            task,
            current_surface="show",
            file_index=bounded_index,
            file_context_payload=file_context_payload,
        )
        return {
            "id": task.id,
            "status": task.status,
            "kind": task.kind,
            "surface_kind": self._task_surface_kind(task),
            "description": task.description,
            "progress_summary": task.progress_summary,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "terminal": str(task.status).strip().lower() in _TERMINAL_TASK_STATUSES,
            "focused_file": focused_file,
            "scope_reasons": scope_reasons,
            "related_change": related_change,
            "has_related_change": bool(related_change),
            "diff_hunks": diff_hunks,
            "has_diff_hunks": diff_hunks > 0,
            "is_context_only": context_only,
            "background_session_id": background_session_id,
            "background_reverse_hint": background_reverse_hint,
            "background_linked": bool(background_linkage_lines),
            "action_groups": {
                "go_to_task": session._dedupe_action_commands(task_actions),  # noqa: SLF001
                "go_to_change": session._dedupe_action_commands(change_actions),  # noqa: SLF001
                "go_to_plan": session._dedupe_action_commands(plan_actions),  # noqa: SLF001
                "stay_on_surface": session._dedupe_action_commands(  # noqa: SLF001
                    ["/tasks list", "/tasks active", "/tasks changes", "/tasks context"]
                ),
            },
        }

    def _checklist_workflow_entry(self, task: ChecklistTask) -> dict[str, Any]:
        session = self._session
        context_selection = session.resolve_task_file_context(  # noqa: SLF001
            task.id,
            file_index=0,
            preserve_current_focus=True,
        )
        file_context_payload = context_selection["payload"]
        bounded_index = int(context_selection["selected_index"])
        focused_item = context_selection["focused_item"]
        related_change = ""
        diff_hunks = 0
        context_only = False
        focused_file = ""
        scope_reasons: list[str] = []
        change_actions: list[str] = []
        if focused_item is not None:
            focused_file = str(focused_item.get("path") or "").strip()
            related_change = str(focused_item.get("change_id") or "").strip()
            diff_hunks = session._file_context_diff_hunk_count(focused_item)
            context_only = session._file_context_is_context_only(focused_item)
            scope_reasons = session._file_context_scope_reasons(focused_item)
            change_navigation = session._resolve_change_navigation_for_file_context_item(focused_item)
            if change_navigation is not None:
                change_actions.append(str(change_navigation["change_command"]))
                file_command = change_navigation.get("change_file_command")
                if isinstance(file_command, str) and file_command.strip():
                    change_actions.append(file_command)
        return {
            "id": task.id,
            "status": task.status,
            "kind": "session_checklist",
            "surface_kind": "checklist",
            "description": task.subject,
            "progress_summary": task.active_form,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "terminal": str(task.status).strip().lower() == "completed",
            "focused_file": focused_file,
            "scope_reasons": scope_reasons,
            "related_change": related_change,
            "has_related_change": bool(related_change),
            "diff_hunks": diff_hunks,
            "has_diff_hunks": diff_hunks > 0,
            "is_context_only": context_only,
            "action_groups": {
                "go_to_task": [
                    session.focus_preserving_task_show_command(  # noqa: SLF001
                        task.id,
                        file_context_payload=file_context_payload,
                        file_index=bounded_index,
                    )
                ],
                "go_to_change": session._dedupe_action_commands(change_actions),  # noqa: SLF001
                "go_to_plan": [],
                "stay_on_surface": ["/tasks list", "/tasks active", "/tasks changes", "/tasks context"],
            },
        }

    def _render_task_workflow_overview(
        self,
        entries: list[dict[str, Any]],
        *,
        title: str,
        filter_label: str | None = None,
    ) -> list[str]:
        lines = [title]
        if filter_label:
            lines.append(f"filter: {filter_label}")
        if not entries:
            return lines
        for entry in entries:
            progress_summary = str(entry.get("progress_summary") or "").strip()
            summary = f"  progress={progress_summary}" if progress_summary else ""
            lines.append(
                f"{entry['id']}  status={entry['status']}  kind={entry['kind']}  "
                f"surface={entry['surface_kind']}  description={entry['description']}{summary}"
            )
            focused_file = str(entry.get("focused_file") or "").strip()
            scope_reasons = [str(item) for item in entry.get("scope_reasons", []) if str(item).strip()]
            related_change = str(entry.get("related_change") or "").strip()
            action_groups = entry.get("action_groups")
            lines.extend(
                self._session.render_surface_metadata_section(  # noqa: SLF001
                    "",
                    summary_fields=[
                        ("focused file", focused_file or None),
                        ("in scope because", ", ".join(scope_reasons) if scope_reasons else None),
                        ("related change", related_change or None),
                        ("diff hunks", int(entry.get("diff_hunks") or 0)),
                        ("context-only", "yes" if bool(entry.get("is_context_only")) else "no"),
                        ("background session", str(entry.get("background_session_id") or "").strip() or None),
                        (
                            "background reverse hint",
                            str(entry.get("background_reverse_hint") or "").strip() or None,
                        ),
                    ],
                    action_groups=action_groups if isinstance(action_groups, dict) and action_groups else None,
                    action_order=("go_to_task", "go_to_change", "go_to_plan", "stay_on_surface"),
                    line_prefix="  ",
                )
            )
        return lines

    def _render_task_surface_next_action_lines(
        self,
        *,
        task_id: str,
        file_context_payload: dict[str, Any] | None,
        file_index: int,
        stay_actions: list[str],
        plan_actions: list[str] | None = None,
    ) -> list[str]:
        session = self._session
        go_to_change: list[str] = []
        _files, _bounded_index, focused_item = session._file_context_items_and_index(
            file_context_payload,
            selected_index=file_index,
        )
        if focused_item is not None:
            change_navigation = session._resolve_change_navigation_for_file_context_item(focused_item)
            if change_navigation is not None:
                go_to_change.append(str(change_navigation["change_command"]))
                file_command = str(change_navigation.get("change_file_command") or "").strip()
                if file_command:
                    go_to_change.append(file_command)
        focus_preserving_task_show = session.focus_preserving_task_show_command(  # noqa: SLF001
            task_id,
            file_context_payload=file_context_payload,
            file_index=file_index,
        )
        deduped_plan_actions = session._dedupe_action_commands(list(plan_actions or []))  # noqa: SLF001
        deduped_stay_actions = session._dedupe_action_commands(  # noqa: SLF001
            [focus_preserving_task_show, *stay_actions]
        )
        deduped_change_actions = session._dedupe_action_commands(go_to_change)  # noqa: SLF001
        return session.render_workflow_action_sections(  # noqa: SLF001
            {
                "go_to_change": deduped_change_actions,
                "go_to_plan": deduped_plan_actions,
                "stay_on_surface": deduped_stay_actions,
            },
            ordered_keys=("go_to_change", "go_to_plan", "stay_on_surface"),
        )

    def _task_surface_navigation_actions(
        self,
        task: Any,
        *,
        current_surface: str,
        file_index: int = 0,
        file_context_payload: dict[str, Any] | None = None,
    ) -> tuple[list[str], list[str]]:
        metadata = task.metadata or {}
        surface_kind = self._task_surface_kind(task)
        task_id = str(task.id)
        task_show_action = self._session.focus_preserving_task_show_command(  # noqa: SLF001
            task_id,
            file_context_payload=file_context_payload,
            file_index=file_index,
        )
        stay_actions: list[str] = []
        if current_surface == "advisor":
            stay_actions.append(f"/task advisor {task_id}")
            stay_actions.append(task_show_action)
            if str(metadata.get("task_role") or "").strip() == "execution":
                stay_actions.append(f"/task drift {task_id}")
        elif current_surface == "drift":
            stay_actions.append(f"/task drift {task_id}")
            stay_actions.append(task_show_action)
            if str(metadata.get("task_role") or "").strip() == "execution":
                stay_actions.append(f"/task advisor {task_id}")
        else:
            stay_actions.append(task_show_action)
            if str(metadata.get("task_role") or "").strip() == "execution":
                stay_actions.append(f"/task advisor {task_id}")
                stay_actions.append(f"/task drift {task_id}")
        plan_actions: list[str] = []
        if surface_kind == "active_plan_execution" or str(metadata.get("task_role") or "").strip() == "execution":
            execution_action = self._session.active_plan_execution_command_for_task(
                task_id,
                file_index=file_index,
            )
            if execution_action:
                plan_actions.append(execution_action)
            plan_actions.extend(["/plan execution", "/plan advisor"])
        if (
            str(metadata.get("task_role") or "").strip() == "scout"
            or surface_kind == "child_execution"
        ):
            scout_action = self._session.active_plan_scout_command_for_task(
                task_id,
                file_index=file_index,
            )
            if scout_action:
                plan_actions.append(scout_action)
            plan_actions.append("/plan scouts")
        return (
            self._session._dedupe_action_commands(stay_actions),  # noqa: SLF001
            self._session._dedupe_action_commands(plan_actions),  # noqa: SLF001
        )

    def describe_task_detail(
        self,
        identifier: str,
        *,
        file_index: int = 0,
        preserve_current_focus: bool = True,
    ) -> str:
        session = self._session
        task = self.resolve_task(identifier)
        if task is None:
            checklist_task = session.resolve_checklist_task(identifier)
            if checklist_task is not None:
                return self._describe_checklist_task_detail(
                    checklist_task,
                    file_index=file_index,
                    preserve_current_focus=preserve_current_focus,
                )
            return f'Unknown task "{identifier.strip()}".'
        effective_file_index = (
            int(
                session.resolve_task_file_context(  # noqa: SLF001
                    task.id,
                    file_index=file_index,
                    preserve_current_focus=preserve_current_focus,
                )["selected_index"]
            )
        )
        lines = [
            f"task_id: {task.id}",
            f"kind: {task.kind}",
            f"task_surface: {self._task_surface_kind(task)}",
            f"status: {task.status}",
            f"description: {task.description}",
            f"created_at: {task.created_at}",
        ]
        if task.updated_at:
            lines.append(f"updated_at: {task.updated_at}")
        if task.ended_at:
            lines.append(f"ended_at: {task.ended_at}")
        if task.progress_summary:
            lines.append(f"progress_summary: {task.progress_summary}")
        if task.metadata:
            lines.append("metadata:")
            for key in sorted(task.metadata):
                value = task.metadata.get(key)
                lines.append(f"- {key}: {value}")
        reverse_hint_lines = self._background_reverse_hint_lines(task.metadata or {})
        if reverse_hint_lines:
            lines.extend(reverse_hint_lines)
        execution_context_lines = self._render_task_detail_execution_context(task)
        if execution_context_lines:
            lines.append("execution_context:")
            lines.extend(execution_context_lines)
        execution_contract = self._task_execution_contract_metadata(task)
        if execution_contract is not None:
            lines.append("execution_contract:")
            lines.append(f"- task_surface: {execution_contract['task_surface']}")
            lines.append(f"- execution_mode: {execution_contract['execution_mode']}")
            if execution_contract["execution_policy"]:
                lines.append(f"- execution_policy: {execution_contract['execution_policy']}")
            if execution_contract["execution_policy_source"]:
                lines.append(
                    f"- execution_policy_source: {execution_contract['execution_policy_source']}"
                )
            if execution_contract["allowed_tools"]:
                lines.append("- allowed_tools: " + ", ".join(execution_contract["allowed_tools"]))
            if execution_contract["allowed_bash_prefixes"]:
                lines.append(
                    "- allowed_bash_prefixes: "
                    + ", ".join(execution_contract["allowed_bash_prefixes"])
                )
            lines.append(
                "- read_only_subagents: "
                + ("yes" if execution_contract["read_only_subagents"] else "no")
            )
            if execution_contract["workspace_mode"]:
                lines.append(f"- workspace_mode: {execution_contract['workspace_mode']}")
            if execution_contract["workspace_health"]:
                lines.append(f"- workspace_health: {execution_contract['workspace_health']}")
        if task.metadata.get("child_execution_mode") or task.metadata.get("child_command_policy_name"):
            lines.append("child_execution_contract:")
            lines.append(f"- execution_mode: {task.metadata.get('child_execution_mode') or 'main'}")
            if task.metadata.get("child_command_policy_name"):
                lines.append(f"- command_policy_name: {task.metadata['child_command_policy_name']}")
            if task.metadata.get("child_command_policy_source"):
                lines.append(f"- command_policy_source: {task.metadata['child_command_policy_source']}")
            child_allowed_tools = (
                task.metadata.get("child_command_policy_allowed_tool_names")
                or task.metadata.get("child_command_policy_allowed_tools")
                or []
            )
            if child_allowed_tools:
                lines.append("- allowed_tools: " + ", ".join(str(item) for item in child_allowed_tools))
            child_allowed_prefixes = task.metadata.get("child_command_policy_allowed_bash_prefixes") or []
            if child_allowed_prefixes:
                lines.append("- allowed_bash_prefixes: " + ", ".join(str(item) for item in child_allowed_prefixes))
            if task.metadata.get("child_command_policy_require_read_only_subagents"):
                lines.append("- read_only_subagents: yes")
        workspace_context_lines = self._render_task_workspace_context(task.metadata or {})
        if workspace_context_lines:
            lines.append("workspace_context:")
            lines.extend(workspace_context_lines)
        workspace_action_fields = self._workspace_task_action_fields(task.metadata or {})
        if workspace_action_fields:
            lines.append("workspace_actions:")
            lines.append(
                f"selected_workspace_primary_action: {workspace_action_fields['selected_workspace_primary_action']}"
            )
            lines.append(
                f"selected_workspace_secondary_action: {workspace_action_fields['selected_workspace_secondary_action']}"
            )
            lines.append(
                f"selected_workspace_tertiary_action: {workspace_action_fields['selected_workspace_tertiary_action']}"
            )
            lines.append(
                f"selected_workspace_target: {workspace_action_fields['selected_workspace_target']}"
            )
        workspace_detail_metadata = self._workspace_task_detail_metadata(task.metadata or {})
        if workspace_detail_metadata:
            lines.append("workspace_task_detail:")
            lines.append(f"workspace_action: {workspace_detail_metadata['workspace_action']}")
            lines.append(f"workspace_target: {workspace_detail_metadata['workspace_target']}")
            lines.append(
                f"workspace_health_before: {workspace_detail_metadata['workspace_health_before'] or 'none'}"
            )
            lines.append(
                f"workspace_health_after: {workspace_detail_metadata['workspace_health_after'] or 'none'}"
            )
            recommended_actions = workspace_detail_metadata.get("workspace_recommended_actions") or []
            if recommended_actions:
                lines.append("workspace_recommended_actions:")
                lines.extend(f"- {action}" for action in recommended_actions)
            if workspace_detail_metadata["workspace_planned_paths"]:
                lines.append("workspace_planned_paths:")
                lines.extend(
                    f"- {path}" for path in workspace_detail_metadata["workspace_planned_paths"]
                )
            if workspace_detail_metadata["workspace_applied_paths"]:
                lines.append("workspace_applied_paths:")
                lines.extend(
                    f"- {path}" for path in workspace_detail_metadata["workspace_applied_paths"]
                )
            if workspace_detail_metadata["workspace_failure_reason"]:
                lines.append(
                    f"workspace_failure_reason: {workspace_detail_metadata['workspace_failure_reason']}"
                )
        task_context = session.resolve_task_file_context(  # noqa: SLF001
            task.id,
            file_index=effective_file_index,
            preserve_current_focus=False,
        )
        file_context_payload = task_context["payload"]
        lines.extend(session.render_resolved_file_context_sections(task_context))  # noqa: SLF001
        stay_actions, plan_actions = self._task_surface_navigation_actions(
            task,
            current_surface="show",
            file_index=effective_file_index,
            file_context_payload=file_context_payload,
        )
        lines.extend(
            self._render_task_surface_next_action_lines(
                task_id=task.id,
                file_context_payload=file_context_payload,
                file_index=effective_file_index,
                stay_actions=stay_actions,
                plan_actions=plan_actions,
            )
        )
        permission_context_lines = session._render_task_permission_display_lines(task)
        if permission_context_lines:
            lines.append("permission_context:")
            lines.extend(permission_context_lines)
        if task.output:
            lines.append("output:")
            lines.append(
                compact_multiline_text(
                    task.output,
                    max_lines=80,
                    max_chars=12000,
                )
            )
        if task.error:
            lines.append("error:")
            lines.append(
                compact_multiline_text(
                    task.error,
                    max_lines=60,
                    max_chars=9000,
                )
            )
        return "\n".join(lines)

    def _describe_checklist_task_detail(
        self,
        task: ChecklistTask,
        *,
        file_index: int = 0,
        preserve_current_focus: bool = True,
    ) -> str:
        session = self._session
        metadata = self._checklist_task_detail_metadata(task)
        effective_file_index = (
            int(
                session.resolve_task_file_context(  # noqa: SLF001
                    task.id,
                    file_index=file_index,
                    preserve_current_focus=preserve_current_focus,
                )["selected_index"]
            )
        )
        lines = [
            f"task_id: {task.id}",
            "kind: session_checklist",
            "task_surface: checklist",
            f"task_list_id: {session.checklist_task_list_id()}",
            f"status: {task.status}",
            f"subject: {task.subject}",
            f"description: {task.description}",
            f"active_form: {task.active_form}",
            f"owner: {task.owner or 'none'}",
            f"created_at: {task.created_at}",
            f"updated_at: {task.updated_at}",
            f"checklist_total_tasks: {metadata['checklist_total_tasks']}",
            f"checklist_in_progress_tasks: {metadata['checklist_in_progress_tasks']}",
        ]
        if task.blocks:
            lines.append("blocks:")
            lines.extend(f"- {item}" for item in task.blocks)
        if task.blocked_by:
            lines.append("blocked_by:")
            lines.extend(f"- {item}" for item in task.blocked_by)
        if task.metadata:
            lines.append("metadata:")
            for key in sorted(task.metadata):
                lines.append(f"- {key}: {task.metadata[key]}")
        lines.append("checklist_actions:")
        lines.append(f"selected_checklist_primary_action: {metadata['selected_checklist_primary_action']}")
        lines.append(f"selected_checklist_secondary_action: {metadata['selected_checklist_secondary_action']}")
        lines.append(f"selected_checklist_tertiary_action: {metadata['selected_checklist_tertiary_action']}")
        lines.append(f"selected_checklist_edit_subject_action: {metadata['selected_checklist_edit_subject_action']}")
        lines.append(
            f"selected_checklist_edit_description_action: {metadata['selected_checklist_edit_description_action']}"
        )
        lines.append(f"selected_checklist_edit_owner_action: {metadata['selected_checklist_edit_owner_action']}")
        lines.append(
            f"selected_checklist_edit_active_form_action: {metadata['selected_checklist_edit_active_form_action']}"
        )
        lines.append(f"selected_checklist_edit_blocks_action: {metadata['selected_checklist_edit_blocks_action']}")
        lines.append(
            f"selected_checklist_edit_blocked_by_action: {metadata['selected_checklist_edit_blocked_by_action']}"
        )
        lines.append(
            f"selected_checklist_edit_metadata_action: {metadata['selected_checklist_edit_metadata_action']}"
        )
        lines.append(f"selected_checklist_target: {metadata['selected_checklist_target']}")
        lines.append("checklist_task_detail:")
        lines.append(f"checklist_task_id: {task.id}")
        lines.append(f"checklist_subject: {task.subject}")
        lines.append(f"checklist_description: {task.description}")
        lines.append(f"checklist_active_form: {task.active_form}")
        lines.append(f"checklist_status: {task.status}")
        lines.append(f"checklist_owner: {task.owner or 'none'}")
        lines.append("checklist_recommended_actions:")
        lines.extend(f"- {action}" for action in metadata["checklist_recommended_actions"])
        if metadata.get("checklist_duplicate_message"):
            lines.append("checklist_duplicate_guard:")
            lines.append(f"checklist_duplicate_message: {metadata['checklist_duplicate_message']}")
            if metadata.get("checklist_duplicate_reason"):
                lines.append(f"checklist_duplicate_reason: {metadata['checklist_duplicate_reason']}")
            if metadata.get("checklist_duplicate_matched_task_id"):
                lines.append(
                    "checklist_duplicate_matched_task_id: "
                    + str(metadata["checklist_duplicate_matched_task_id"])
                )
            if metadata.get("checklist_duplicate_recommended_action"):
                lines.append(
                    "checklist_duplicate_recommended_action: "
                    + str(metadata["checklist_duplicate_recommended_action"])
                )
        if task.blocks:
            lines.append("checklist_blocks:")
            lines.extend(f"- {item}" for item in task.blocks)
        if task.blocked_by:
            lines.append("checklist_blocked_by:")
            lines.extend(f"- {item}" for item in task.blocked_by)
        if task.metadata:
            lines.append("checklist_metadata:")
            for key in sorted(task.metadata):
                lines.append(f"- {key}: {task.metadata[key]}")
        task_context = session.resolve_task_file_context(  # noqa: SLF001
            task.id,
            file_index=effective_file_index,
            preserve_current_focus=False,
        )
        file_context_payload = task_context["payload"]
        lines.extend(session.render_resolved_file_context_sections(task_context))  # noqa: SLF001
        lines.extend(
            self._render_task_surface_next_action_lines(
                task_id=task.id,
                file_context_payload=file_context_payload,
                file_index=effective_file_index,
                stay_actions=[
                    session.focus_preserving_task_show_command(  # noqa: SLF001
                        task.id,
                        file_context_payload=file_context_payload,
                        file_index=effective_file_index,
                    )
                ],
                plan_actions=[],
            )
        )
        return "\n".join(lines)

    def _recent_task_activity_lines(self, limit: int = 6) -> list[str]:
        session = self._session
        entries: list[tuple[str, str]] = []
        for task in session.task_manager.list():
            updated = str(task.updated_at or task.created_at or "")
            surface_kind = self._task_surface_kind(task)
            detail_bits = [
                f"task_surface={surface_kind}",
                f"task={task.id}",
                f"status={task.status}",
                f"kind={task.kind}",
            ]
            if task.metadata.get("workspace_action"):
                detail_bits.append(f"workspace_action={task.metadata['workspace_action']}")
            if task.metadata.get("task_role"):
                detail_bits.append(f"task_role={task.metadata['task_role']}")
            if task.metadata.get("child_execution_mode"):
                detail_bits.append(f"execution={task.metadata['child_execution_mode']}")
            if task.metadata.get("child_command_policy_name"):
                detail_bits.append(f"policy={task.metadata['child_command_policy_name']}")
            execution_detail = self._task_execution_contract_metadata(task)
            if execution_detail is not None:
                if execution_detail["read_only_subagents"]:
                    detail_bits.append("read_only_subagents=yes")
                if execution_detail["allowed_tools"]:
                    detail_bits.append(f"allowed_tools={len(execution_detail['allowed_tools'])}")
                if execution_detail["allowed_bash_prefixes"]:
                    detail_bits.append(
                        f"allowed_bash_prefixes={len(execution_detail['allowed_bash_prefixes'])}"
                    )
            detail_bits.append(f"description={task.description}")
            entries.append((updated, " ".join(detail_bits)))
        for task in session.checklist_tasks():
            updated = str(task.updated_at or task.created_at or "")
            entries.append(
                (
                    updated,
                    " ".join(
                        [
                            "task_surface=checklist",
                            f"task={task.id}",
                            f"status={task.status}",
                            f"subject={task.subject}",
                            f"owner={task.owner or 'none'}",
                        ]
                    ),
                )
            )
        entries.sort(key=lambda item: item[0], reverse=True)
        return [entry for _timestamp, entry in entries[:limit]]

    def open_task_detail(self, identifier: str) -> str:
        return self.describe_task_detail(identifier)

    def describe_task_drift_detail(self, identifier: str) -> str:
        session = self._session
        task = self.resolve_task(identifier)
        if task is None:
            return f'Unknown task "{identifier.strip()}".'
        metadata = task.metadata or {}
        if str(metadata.get("task_role") or "").strip() != "execution":
            return f"Task {task.id} has no execution drift detail."
        lines = [
            f"task_id: {task.id}",
            f"description: {task.description}",
            "drift_detail:",
        ]
        drift_lines = self._render_task_detail_drift_context(metadata)
        if drift_lines:
            lines.extend(drift_lines)
        else:
            lines.append("(none)")
        reverse_hint_lines = self._background_reverse_hint_lines(metadata)
        if reverse_hint_lines:
            lines.append("")
            lines.extend(reverse_hint_lines)
        advisor_lines = self._render_task_detail_advisor_context(
            self._task_execution_planning_artifact(metadata),
            metadata,
        )
        if advisor_lines:
            lines.append("")
            lines.append("advisor_context:")
            lines.extend(advisor_lines)
        task_context = session.resolve_task_file_context(  # noqa: SLF001
            task.id,
            file_index=0,
            preserve_current_focus=True,
        )
        effective_file_index = int(task_context["selected_index"])
        file_context_payload = task_context["payload"]
        file_context_sections = session.render_resolved_file_context_sections(task_context)  # noqa: SLF001
        if file_context_sections:
            lines.append("")
            lines.extend(file_context_sections)
        stay_actions, plan_actions = self._task_surface_navigation_actions(
            task,
            current_surface="drift",
            file_index=effective_file_index,
            file_context_payload=file_context_payload,
        )
        lines.append("")
        lines.extend(
            self._render_task_surface_next_action_lines(
                task_id=task.id,
                file_context_payload=file_context_payload,
                file_index=effective_file_index,
                stay_actions=stay_actions,
                plan_actions=plan_actions,
            )
        )
        task_id = task.id
        focus_preserving_task_show = session.focus_preserving_task_show_command(  # noqa: SLF001
            task_id,
            file_context_payload=file_context_payload,
            file_index=effective_file_index,
        )
        active_plan_execution_command = session.active_plan_execution_command_for_task(
            task_id,
            file_index=effective_file_index,
        )
        lines.append("")
        lines.extend(
            session.render_navigation_section(  # noqa: SLF001
                {
                    "open_task_detail": focus_preserving_task_show,
                    "open_task_advisor": f"/task advisor {task_id}",
                    "open_active_plan_execution": active_plan_execution_command or "/plan execution",
                    "open_active_plan_advisor": "/plan advisor",
                }
            )
        )
        return "\n".join(lines)

    def open_task_detail_advisor(self, identifier: str) -> str:
        task = self.resolve_task(identifier)
        if task is None:
            return f'Unknown task "{identifier.strip()}".'
        metadata = task.metadata or {}
        if str(metadata.get("task_role") or "").strip() != "execution":
            return f"Task {task.id} has no linked plan advisor detail."
        artifact = self._task_execution_planning_artifact(metadata)
        if artifact is None:
            return f"Task {task.id} has no linked planning artifact."
        task_context = self._session.resolve_task_file_context(  # noqa: SLF001
            task.id,
            file_index=0,
            preserve_current_focus=True,
        )
        effective_file_index = int(task_context["selected_index"])
        lines = [
            self._session._plan_component._describe_planning_artifact_advisor(
                artifact,
                file_index=effective_file_index,
                preserve_current_focus=False,
            )
        ]
        reverse_hint_lines = self._background_reverse_hint_lines(metadata)
        if reverse_hint_lines:
            lines.append("")
            lines.extend(reverse_hint_lines)
        file_context_payload = task_context["payload"]
        file_context_sections = self._session.render_resolved_file_context_sections(task_context)  # noqa: SLF001
        if file_context_sections:
            lines.append("")
            lines.extend(file_context_sections)
        stay_actions, plan_actions = self._task_surface_navigation_actions(
            task,
            current_surface="advisor",
            file_index=effective_file_index,
            file_context_payload=file_context_payload,
        )
        lines.append("")
        lines.extend(
            self._render_task_surface_next_action_lines(
                task_id=task.id,
                file_context_payload=file_context_payload,
                file_index=effective_file_index,
                stay_actions=stay_actions,
                plan_actions=plan_actions,
            )
        )
        focus_preserving_task_show = self._session.focus_preserving_task_show_command(  # noqa: SLF001
            task.id,
            file_context_payload=file_context_payload,
            file_index=effective_file_index,
        )
        active_plan_execution_command = self._session.active_plan_execution_command_for_task(
            task.id,
            file_index=effective_file_index,
        )
        lines.append("")
        lines.extend(
            self._session.render_navigation_section(  # noqa: SLF001
                {
                    "open_task_detail": focus_preserving_task_show,
                    "open_task_drift": f"/task drift {task.id}",
                    "open_active_plan_advisor": "/plan advisor",
                    "open_active_plan_execution": active_plan_execution_command or "/plan execution",
                }
            )
        )
        return "\n".join(lines)

    def open_task_drift_detail(self, identifier: str) -> str:
        return self.describe_task_drift_detail(identifier)

    def _task_execution_contract_metadata(self, task: Any) -> dict[str, Any] | None:
        metadata = task.metadata or {}
        child_execution_mode = str(metadata.get("child_execution_mode") or "").strip()
        command_policy_name = str(metadata.get("child_command_policy_name") or "").strip()
        command_policy_source = str(metadata.get("child_command_policy_source") or "").strip()
        allowed_tools_raw = (
            metadata.get("child_command_policy_allowed_tool_names")
            or metadata.get("child_command_policy_allowed_tools")
            or []
        )
        allowed_prefixes_raw = metadata.get("child_command_policy_allowed_bash_prefixes") or []
        allowed_tools = [str(item) for item in allowed_tools_raw if str(item).strip()]
        allowed_prefixes = [str(item) for item in allowed_prefixes_raw if str(item).strip()]
        read_only_subagents = bool(
            metadata.get("child_command_policy_require_read_only_subagents")
        ) or child_execution_mode == "read-only-subagent"
        if not read_only_subagents and command_policy_name:
            read_only_subagents = command_policy_name in READ_ONLY_COMMAND_POLICY_NAMES
        task_surface = self._task_surface_kind(task)
        workspace_mode = str(metadata.get("workspace_mode") or "").strip()
        workspace_health = str(metadata.get("workspace_health") or "").strip()
        if not any(
            (
                child_execution_mode,
                command_policy_name,
                command_policy_source,
                allowed_tools,
                allowed_prefixes,
                read_only_subagents,
                workspace_mode,
                workspace_health,
            )
        ):
            return None
        return {
            "task_surface": task_surface,
            "execution_mode": child_execution_mode or "main",
            "execution_policy": command_policy_name or None,
            "execution_policy_source": command_policy_source or None,
            "allowed_tools": allowed_tools,
            "allowed_bash_prefixes": allowed_prefixes,
            "read_only_subagents": read_only_subagents,
            "workspace_mode": workspace_mode or None,
            "workspace_health": workspace_health or None,
        }

    def _workspace_summary_bits_from_metadata(self, metadata: dict[str, Any]) -> list[str]:
        workspace_mode = str(metadata.get("workspace_mode") or "").strip()
        if not workspace_mode:
            return []
        bits = [f"workspace={workspace_mode}"]
        health = str(metadata.get("workspace_health") or "").strip()
        if health:
            bits.append(f"health={health}")
        label = str(metadata.get("workspace_label") or "").strip()
        if label:
            bits.append(f"label={label}")
        origin = str(metadata.get("original_cwd") or "").strip()
        if origin:
            bits.append(f"origin={origin}")
        cwd = str(
            metadata.get("effective_cwd")
            or metadata.get("child_cwd")
            or metadata.get("cwd")
            or ""
        ).strip()
        if cwd:
            bits.append(f"cwd={cwd}")
            if not Path(cwd).exists():
                bits.append("cwd_exists=no")
        cleanup = str(metadata.get("workspace_cleanup_status") or "").strip()
        if cleanup and cleanup != "none":
            bits.append(f"cleanup={cleanup}")
        if bool(metadata.get("workspace_unavailable")):
            bits.append("unavailable=yes")
            fallback = str(metadata.get("workspace_fallback_cwd") or "").strip()
            if fallback:
                bits.append(f"fallback={fallback}")
        return bits

    def _render_task_workspace_context(self, metadata: dict[str, Any]) -> list[str]:
        lines: list[str] = []
        workspace_mode = str(metadata.get("workspace_mode") or "").strip()
        if not workspace_mode:
            return lines
        lines.append(f"- workspace_mode: {workspace_mode}")
        workspace_health = str(metadata.get("workspace_health") or "").strip()
        if workspace_health:
            lines.append(f"- workspace_health: {workspace_health}")
        label = str(metadata.get("workspace_label") or "").strip()
        if label:
            lines.append(f"- workspace_label: {label}")
        created_at = str(metadata.get("workspace_created_at") or "").strip()
        if created_at:
            lines.append(f"- workspace_created_at: {created_at}")
        origin = str(metadata.get("original_cwd") or "").strip()
        if origin:
            lines.append(f"- original_cwd: {origin}")
        effective = str(
            metadata.get("effective_cwd")
            or metadata.get("child_cwd")
            or metadata.get("cwd")
            or ""
        ).strip()
        if effective:
            lines.append(f"- effective_cwd: {effective}")
            lines.append(f"- effective_cwd_exists: {'yes' if Path(effective).exists() else 'no'}")
        cleanup = str(metadata.get("workspace_cleanup_status") or "").strip()
        if cleanup and cleanup != "none":
            lines.append(f"- cleanup_status: {cleanup}")
        cleanup_error = str(metadata.get("workspace_cleanup_error") or "").strip()
        if cleanup_error:
            lines.append(f"- cleanup_error: {cleanup_error}")
        if bool(metadata.get("workspace_unavailable")):
            lines.append("- workspace_unavailable: yes")
            reason = str(metadata.get("workspace_unavailable_reason") or "").strip()
            if reason:
                lines.append(f"- workspace_unavailable_reason: {reason}")
            fallback = str(metadata.get("workspace_fallback_cwd") or "").strip()
            if fallback:
                lines.append(f"- workspace_fallback_cwd: {fallback}")
        actions = metadata.get("workspace_recommended_actions") or ()
        if actions:
            lines.append("- workspace_recommended_actions:")
            for action in actions:
                lines.append(f"  - {action}")
        return lines

    def _workspace_task_action_bundle(self, metadata: dict[str, Any]) -> dict[str, str] | None:
        workspace_action = str(metadata.get("workspace_action") or "").strip()
        if not workspace_action:
            return None
        workspace_health = (
            str(metadata.get("workspace_health_after") or "").strip()
            or str(metadata.get("workspace_health") or "").strip()
            or str(metadata.get("workspace_health_before") or "").strip()
            or "healthy"
        )
        workspace_target = str(metadata.get("workspace_target") or "").strip() or "all"
        return _workspace_action_bundle(
            workspace_health=workspace_health,
            workspace_action=workspace_action,
            workspace_target=workspace_target,
        )

    def _workspace_task_action_fields(self, metadata: dict[str, Any]) -> dict[str, str]:
        bundle = self._workspace_task_action_bundle(metadata)
        if bundle is None:
            return {}
        return {
            "selected_workspace_primary_action": bundle["primary_action"],
            "selected_workspace_secondary_action": bundle["secondary_action"],
            "selected_workspace_tertiary_action": bundle["tertiary_action"],
            "selected_workspace_target": bundle["target"],
        }

    def _workspace_task_detail_metadata(self, metadata: dict[str, Any]) -> dict[str, Any] | None:
        session = self._session
        action = str(metadata.get("workspace_action") or "").strip()
        if not action:
            return None
        bundle = self._workspace_task_action_bundle(metadata)
        if bundle is None:
            return None
        planned_paths_raw = metadata.get("workspace_planned_paths")
        applied_paths_raw = metadata.get("workspace_applied_paths")
        planned_paths = (
            [str(item) for item in planned_paths_raw]
            if isinstance(planned_paths_raw, (list, tuple))
            else []
        )
        applied_paths = (
            [str(item) for item in applied_paths_raw]
            if isinstance(applied_paths_raw, (list, tuple))
            else []
        )
        failure_reason = str(metadata.get("workspace_failure_reason") or "").strip() or None
        workspace_health_after = str(metadata.get("workspace_health_after") or "").strip()
        workspace_health_before = str(metadata.get("workspace_health_before") or "").strip()
        recommended_actions_raw = metadata.get("workspace_recommended_actions")
        if isinstance(recommended_actions_raw, (list, tuple)):
            recommended_actions = [str(item) for item in recommended_actions_raw]
        else:
            recommended_actions = session._workspace_task_recommended_actions(
                workspace_target=str(metadata.get("workspace_target") or "").strip() or bundle["target"],
                workspace_health_before=workspace_health_before or None,
                workspace_health_after=workspace_health_after or None,
                workspace_health=bundle["workspace_health"],
            )
        return {
            "workspace_action": action,
            "workspace_target": str(metadata.get("workspace_target") or "").strip() or bundle["target"],
            "workspace_health_before": workspace_health_before or None,
            "workspace_health_after": workspace_health_after or None,
            "workspace_planned_paths": planned_paths,
            "workspace_applied_paths": applied_paths,
            "workspace_failure_reason": failure_reason,
            "workspace_recommended_actions": recommended_actions,
            "workspace_primary_action": bundle["primary_action"],
            "workspace_secondary_action": bundle["secondary_action"],
            "workspace_tertiary_action": bundle["tertiary_action"],
            "workspace_action_target": bundle["target"],
            "workspace_health": bundle["workspace_health"],
        }

    def _normalized_checklist_duplicate_guard(
        self,
        *,
        task_id: str | None = None,
    ) -> dict[str, Any] | None:
        guard = self._session._latest_checklist_duplicate_guard
        if not isinstance(guard, dict) or not guard:
            return None
        matched_task_id = str(guard.get("matched_task_id") or guard.get("matchedTaskId") or "").strip()
        if task_id is not None and matched_task_id != task_id.strip():
            return None
        matched_task_ids_raw = guard.get("matched_task_ids") or guard.get("matchedTaskIds") or []
        matched_task_ids = [str(item).strip() for item in matched_task_ids_raw if str(item).strip()]
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
        return self._normalized_checklist_duplicate_guard()

    def _checklist_task_detail_metadata(self, task: ChecklistTask) -> dict[str, Any]:
        session = self._session
        stats = session.checklist_stats()
        action_bundle = _checklist_action_bundle(task_id=task.id, checklist_status=task.status)
        recommended_actions = _checklist_recommended_actions(
            task_id=task.id,
            checklist_status=task.status,
        )
        detail = {
            "checklist_task_id": task.id,
            "checklist_task_list_id": session.checklist_task_list_id(),
            "checklist_subject": task.subject,
            "checklist_description": task.description,
            "checklist_active_form": task.active_form,
            "checklist_status": task.status,
            "checklist_owner": task.owner or "none",
            "checklist_blocks": list(task.blocks),
            "checklist_blocked_by": list(task.blocked_by),
            "checklist_metadata": dict(task.metadata),
            "checklist_created_at": task.created_at,
            "checklist_updated_at": task.updated_at,
            "checklist_total_tasks": stats["total"],
            "checklist_in_progress_tasks": stats["in_progress"],
            "checklist_recommended_actions": list(recommended_actions),
            "checklist_primary_action": action_bundle["primary_action"],
            "checklist_secondary_action": action_bundle["secondary_action"],
            "checklist_tertiary_action": action_bundle["tertiary_action"],
            "checklist_edit_subject_action": action_bundle["edit_subject_action"],
            "checklist_edit_description_action": action_bundle["edit_description_action"],
            "checklist_edit_owner_action": action_bundle["edit_owner_action"],
            "checklist_edit_active_form_action": action_bundle["edit_active_form_action"],
            "checklist_edit_blocks_action": action_bundle["edit_blocks_action"],
            "checklist_edit_blocked_by_action": action_bundle["edit_blocked_by_action"],
            "checklist_edit_metadata_action": action_bundle["edit_metadata_action"],
            "checklist_action_target": action_bundle["target"],
            "selected_checklist_primary_action": action_bundle["primary_action"],
            "selected_checklist_secondary_action": action_bundle["secondary_action"],
            "selected_checklist_tertiary_action": action_bundle["tertiary_action"],
            "selected_checklist_edit_subject_action": action_bundle["edit_subject_action"],
            "selected_checklist_edit_description_action": action_bundle["edit_description_action"],
            "selected_checklist_edit_owner_action": action_bundle["edit_owner_action"],
            "selected_checklist_edit_active_form_action": action_bundle["edit_active_form_action"],
            "selected_checklist_edit_blocks_action": action_bundle["edit_blocks_action"],
            "selected_checklist_edit_blocked_by_action": action_bundle["edit_blocked_by_action"],
            "selected_checklist_edit_metadata_action": action_bundle["edit_metadata_action"],
            "selected_checklist_target": action_bundle["target"],
        }
        duplicate_guard = self._normalized_checklist_duplicate_guard(task_id=task.id)
        if duplicate_guard is not None:
            detail["checklist_duplicate_guard"] = duplicate_guard
            detail["checklist_duplicate_message"] = duplicate_guard["message"]
            detail["checklist_duplicate_reason"] = duplicate_guard["reason"]
            detail["checklist_duplicate_matched_task_id"] = duplicate_guard["matched_task_id"]
            detail["checklist_duplicate_recommended_action"] = duplicate_guard["recommended_action"]
        return detail

    def checklist_task_detail_metadata(self, identifier: str) -> dict[str, Any] | None:
        task = self._session.resolve_checklist_task(identifier)
        if task is None:
            return None
        return self._checklist_task_detail_metadata(task)

    def _render_session_checklist_lines(self) -> list[str]:
        session = self._session
        tasks = session.checklist_tasks()
        stats = session.checklist_stats()
        lines = [
            f"session_checklist_tasks: {stats['total']}",
            f"session_checklist_in_progress: {stats['in_progress']}",
        ]
        duplicate_lines = self._checklist_duplicate_prompt_lines(prefix="session_checklist_duplicate_guard")
        if duplicate_lines:
            lines.extend(duplicate_lines)
        if not tasks:
            lines.append("- none")
            return lines
        for task in tasks:
            action_bundle = _checklist_action_bundle(task_id=task.id, checklist_status=task.status)
            bits = [
                f"status={task.status}",
                f"subject={task.subject}",
                f"active_form={task.active_form}",
            ]
            if task.description:
                bits.append(f"description={task.description}")
            if task.owner:
                bits.append(f"owner={task.owner}")
            if task.blocks:
                bits.append("blocks=" + ",".join(task.blocks))
            if task.blocked_by:
                bits.append("blocked_by=" + ",".join(task.blocked_by))
            bits.append(f"selected_checklist_primary_action={action_bundle['primary_action']}")
            bits.append(f"selected_checklist_secondary_action={action_bundle['secondary_action']}")
            bits.append(f"selected_checklist_edit_subject_action={action_bundle['edit_subject_action']}")
            bits.append(
                f"selected_checklist_edit_description_action={action_bundle['edit_description_action']}"
            )
            bits.append(f"selected_checklist_edit_owner_action={action_bundle['edit_owner_action']}")
            bits.append(
                "selected_checklist_edit_active_form_action="
                + action_bundle["edit_active_form_action"]
            )
            bits.append(f"selected_checklist_edit_blocks_action={action_bundle['edit_blocks_action']}")
            bits.append(
                "selected_checklist_edit_blocked_by_action="
                + action_bundle["edit_blocked_by_action"]
            )
            bits.append(
                "selected_checklist_edit_metadata_action="
                + action_bundle["edit_metadata_action"]
            )
            bits.append(f"selected_checklist_target={action_bundle['target']}")
            lines.append(f"- {task.id}  " + "  ".join(bits))
        return lines

    def _checklist_prompt_context(self, *, max_tasks: int = 6) -> str | None:
        session = self._session
        tasks = session.checklist_tasks()
        duplicate_lines = self._checklist_duplicate_prompt_lines(
            prefix="Recent checklist duplicate guard:",
            bullet_prefix="- ",
        )
        if not tasks and not duplicate_lines:
            return None
        lines: list[str] = []
        if tasks:
            prioritized = sorted(
                tasks,
                key=lambda item: (
                    0 if item.status == "in_progress" else 1 if item.status == "pending" else 2,
                    item.updated_at or item.created_at,
                    item.id,
                ),
            )
            stats = session.checklist_stats()
            lines.extend(
                [
                    "Session checklist to treat as active execution context:",
                    f"- total={stats['total']} in_progress={stats['in_progress']}",
                    "- Prefer reusing and updating these checklist tasks as progress changes.",
                    "- Call session_task_list before creating new checklist tasks so you do not duplicate existing work.",
                    "- Call session_task_get before updating a specific checklist task unless you just created or listed it in this turn.",
                    "- Prefer session_task_update over creating a second task for the same outcome.",
                    "- Use todo_write only when you intentionally want to rewrite the full checklist.",
                ]
            )
            for task in prioritized[:max_tasks]:
                bits = [
                    f"id={task.id}",
                    f"status={task.status}",
                    f"subject={task.subject}",
                    f"active_form={task.active_form}",
                ]
                if task.owner:
                    bits.append(f"owner={task.owner}")
                if task.blocks:
                    bits.append("blocks=" + ",".join(task.blocks))
                if task.blocked_by:
                    bits.append("blocked_by=" + ",".join(task.blocked_by))
                lines.append("- " + "  ".join(bits))
            remaining = len(prioritized) - max_tasks
            if remaining > 0:
                lines.append(f"- ... {remaining} more checklist task(s)")
        if duplicate_lines:
            if lines:
                lines.append("")
            lines.extend(duplicate_lines)
        return "\n".join(lines)

    def _checklist_duplicate_prompt_lines(
        self,
        *,
        prefix: str,
        bullet_prefix: str = "",
    ) -> list[str]:
        guard = self._session._latest_checklist_duplicate_guard
        if not isinstance(guard, dict) or not guard:
            return []
        matched_task_id = str(guard.get("matched_task_id") or "").strip()
        reason = str(guard.get("reason") or "").strip()
        recommended_action = str(guard.get("recommended_action") or "").strip()
        candidate_subject = str(guard.get("candidate_subject") or "").strip()
        lines = [prefix]
        if candidate_subject:
            lines.append(f"{bullet_prefix}candidate_subject={candidate_subject}")
        if matched_task_id:
            lines.append(f"{bullet_prefix}matched_task_id={matched_task_id}")
        if reason:
            lines.append(f"{bullet_prefix}reason={reason}")
        if recommended_action:
            lines.append(f"{bullet_prefix}recommended_action={recommended_action}")
        lines.append(
            f"{bullet_prefix}next_step=Use session_task_get {matched_task_id or '<task-id>'} before session_task_update; do not create another checklist task for the same outcome."
        )
        return lines

    def _render_task_detail_execution_context(self, task: Any) -> list[str]:
        metadata = task.metadata or {}
        if str(metadata.get("task_role") or "").strip() != "execution":
            return []
        lines: list[str] = []
        artifact = self._task_execution_planning_artifact(metadata)
        plan_lines = self._render_task_detail_active_plan_summary(artifact, metadata)
        if plan_lines:
            lines.append("- active_plan:")
            lines.extend(f"  {line}" for line in plan_lines)
        advisor_lines = self._render_task_detail_advisor_context(artifact, metadata)
        if advisor_lines:
            lines.append("- advisor_context:")
            lines.extend(f"  {line}" for line in advisor_lines)
        drift_lines = self._render_task_detail_drift_context(metadata)
        if drift_lines:
            lines.append("- drift_context:")
            lines.extend(f"  {line}" for line in drift_lines)
        lines.append(f"- active_plan_advisor_action: /task advisor {task.id}")
        lines.append(f"- drift_detail_action: /task drift {task.id}")
        lines.append("- active_plan_execution_action: /plan execution")
        return lines

    def _task_execution_planning_artifact(self, metadata: dict[str, Any]) -> Any | None:
        active_plan_id = str(metadata.get("active_plan_id") or "").strip()
        if not active_plan_id:
            return None
        return self._session.resolve_planning_artifact(active_plan_id)

    def _render_task_detail_active_plan_summary(
        self,
        artifact: Any | None,
        metadata: dict[str, Any],
    ) -> list[str]:
        if artifact is None:
            active_plan_id = str(metadata.get("active_plan_id") or "").strip()
            active_plan_goal = str(metadata.get("active_plan_goal") or "").strip()
            if not active_plan_id and not active_plan_goal:
                return []
            lines = []
            if active_plan_id:
                lines.append(f"artifact_id: {active_plan_id}")
            if active_plan_goal:
                lines.append(f"goal: {active_plan_goal}")
            lines.append("status: missing")
            return lines
        lines = [
            f"artifact_id: {artifact.artifact_id}",
            f"goal: {artifact.goal}",
            f"kind: {artifact.kind}",
            f"lineage_position: {self._session._planning_artifact_lineage_position(artifact)}",
        ]
        if artifact.advisor_status:
            lines.append(f"advisor_status: {artifact.advisor_status}")
        if artifact.advisor_risk_flags:
            lines.append("risk_flags: " + ", ".join(artifact.advisor_risk_flags))
        lines.append(f"derived_from_drift: {'yes' if artifact.derived_from_drift else 'no'}")
        if artifact.derivation_reason:
            lines.append(f"derivation_reason: {artifact.derivation_reason}")
        return lines

    def _render_task_detail_advisor_context(
        self,
        artifact: Any | None,
        metadata: dict[str, Any],
    ) -> list[str]:
        review = self._session.state.advisor_last_result
        has_link = any(
            str(metadata.get(key) or "").strip()
            for key in ("constraint_source", "drift_status", "drift_reason")
        )
        if artifact is None and review is None and not has_link:
            return []
        lines: list[str] = []
        if artifact is not None:
            lines.append("active_plan_review:")
            advisor_lines = self._session._render_planning_artifact_advisor_review(artifact)
            if advisor_lines:
                lines.extend(f"  {line}" for line in advisor_lines)
            else:
                lines.append("  (none)")
        if review is not None:
            lines.append("latest_session_review:")
            lines.append(f"  - {review.checkpoint}/{review.status}")
            if review.reason:
                lines.append(f"  - reason: {review.reason}")
            if review.risk_flags:
                lines.append("  - risk_flags: " + ", ".join(review.risk_flags))
            if review.suggested_changes:
                lines.append("  - suggested_changes:")
                lines.extend(f"    - {item}" for item in review.suggested_changes)
        return lines

    def _render_task_detail_drift_context(self, metadata: dict[str, Any]) -> list[str]:
        session = self._session
        constraint_source = str(metadata.get("constraint_source") or "").strip()
        drift_status = str(metadata.get("drift_status") or "").strip()
        drift_reason = str(metadata.get("drift_reason") or "").strip()
        if not any((constraint_source, drift_status, drift_reason, session.state.last_plan_drift_context)):
            return []
        lines: list[str] = []
        if constraint_source:
            lines.append(f"constraint_source: {constraint_source}")
        if session.state.constraint_reason and constraint_source:
            lines.append(f"constraint_reason: {session.state.constraint_reason}")
        if drift_status:
            lines.append(f"drift_status: {drift_status}")
        if drift_reason:
            lines.append(f"drift_reason: {drift_reason}")
        if session.state.last_plan_drift_status:
            lines.append(f"last_plan_drift_status: {session.state.last_plan_drift_status}")
        if session.state.last_plan_drift_reason:
            lines.append(f"last_plan_drift_reason: {session.state.last_plan_drift_reason}")
        if session.state.last_plan_drift_context:
            lines.append("analysis:")
            compact = compact_multiline_text(
                session.state.last_plan_drift_context,
                max_lines=10,
                max_chars=1200,
            )
            lines.extend(f"  {line}" for line in compact.splitlines())
        return lines
