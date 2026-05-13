from __future__ import annotations

import difflib
from datetime import datetime
from typing import Any

MAX_PLANNING_ARTIFACTS = 5


class PlanSessionComponent:
    def __init__(self, session: Any) -> None:
        self._session = session

    def latest_planning_artifact(self):
        artifacts = self.planning_artifacts()
        if not artifacts:
            return None
        active = self.active_planning_artifact()
        if active is not None:
            return active
        return artifacts[-1]

    def planning_artifacts(self):
        session = self._session
        artifacts = (
            session.state.planning_artifact_history
            if session.state.planning_artifact_history
            else session.state.recent_planning_artifacts
        )
        return list(artifacts)

    def active_planning_artifact(self):
        artifacts = self.planning_artifacts()
        active_id = self._session.state.active_planning_artifact_id
        if active_id:
            for artifact in artifacts:
                if artifact.artifact_id == active_id:
                    return artifact
        return None

    def resolve_planning_artifact(self, identifier: str):
        artifacts = self.planning_artifacts()
        raw = identifier.strip()
        if not raw:
            return None
        if raw == "latest":
            return artifacts[-1] if artifacts else None
        matches = [
            artifact
            for artifact in artifacts
            if artifact.artifact_id == raw or artifact.artifact_id.startswith(raw)
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def begin_plan_execution(self, artifact) -> None:
        self._session._original_begin_plan_execution(artifact)

    def clear_plan_execution(self) -> None:
        self._session._original_clear_plan_execution()

    def start_active_plan_execution_task(self, *, prompt: str, artifact):
        return self._session._original_start_active_plan_execution_task(prompt=prompt, artifact=artifact)

    def update_execution_task(self, task_id: str, summary: str, **metadata: Any) -> None:
        self._session._original_update_execution_task(task_id, summary, **metadata)

    def complete_execution_task(self, task_id: str, output: str, **metadata: Any) -> None:
        self._session._original_complete_execution_task(task_id, output, **metadata)

    def fail_execution_task(self, task_id: str, error: str, **metadata: Any) -> None:
        self._session._original_fail_execution_task(task_id, error, **metadata)

    def describe_planning_lifecycle(self) -> list[str]:
        return self._session._original_describe_planning_lifecycle()

    def describe_active_plan(
        self,
        *,
        file_index: int = 0,
        preserve_current_focus: bool = True,
    ) -> str:
        artifact = self.active_planning_artifact()
        if artifact is None:
            return "No active planning artifact."
        effective_file_index = int(
            self._session.resolve_active_plan_file_context(  # noqa: SLF001
                identifier=artifact.artifact_id,
                file_index=file_index,
                preserve_current_focus=preserve_current_focus and file_index == 0,
            )["selected_index"]
        )
        return self._render_planning_artifact_summary(artifact, active=True, file_index=effective_file_index)

    def describe_active_plan_scouts(
        self,
        *,
        file_index: int = 0,
        preserve_current_focus: bool = True,
    ) -> str:
        return self.describe_active_plan_scouts_at(
            0,
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
        artifact = self.active_planning_artifact()
        if artifact is None:
            return "No active planning artifact for /plan scouts."
        snapshots = self._planning_artifact_scout_snapshots(artifact)
        scout_context = self._session.resolve_active_plan_scout_file_context(  # noqa: SLF001
            selected_index=selected_index,
            file_index=file_index,
            preserve_current_focus=preserve_current_focus and file_index == 0,
        )
        effective_file_index = int(scout_context["selected_index"])
        lines = [
            f"artifact_id: {artifact.artifact_id}",
            f"goal: {artifact.goal}",
            f"lineage_position: {self._planning_artifact_lineage_position(artifact)}",
            f"detail_mode: {'full' if full_detail else 'compact'}",
            "scout_outputs:",
        ]
        scout_lines = self._render_planning_artifact_scout_outputs(artifact, selected_index=selected_index)
        if scout_lines:
            lines.extend(scout_lines)
            if snapshots:
                normalized_index = max(0, min(selected_index, len(snapshots) - 1))
                selected_snapshot = snapshots[normalized_index]
                lines.append("")
                lines.append("selected_scout_summary:")
                lines.extend(
                    self._render_selected_child_workflow_summary(
                        selected_snapshot,
                        file_context_payload=self._session.task_file_context_payload(
                            selected_snapshot["task_id"]
                        ),
                        file_index=effective_file_index,
                        parent_command="/plan scouts",
                        category_label="category",
                        category_value=str(selected_snapshot.get("category") or "(unknown)"),
                        stay_actions=["/plan scouts"],
                    )
                )
            detail_lines = self._render_planning_artifact_scout_detail(
                artifact,
                selected_index=selected_index,
                full_detail=full_detail,
            )
            if detail_lines:
                lines.append("")
                lines.append("selected_scout_detail:")
                lines.extend(detail_lines)
            comparison_lines = self._render_selected_scout_comparisons(
                artifact,
                selected_index=selected_index,
            )
            if comparison_lines:
                lines.append("")
                lines.append("selected_scout_comparisons:")
                lines.extend(comparison_lines)
        else:
            lines.append("(none)")
        if snapshots:
            normalized_index = max(0, min(selected_index, len(snapshots) - 1))
            selected_snapshot = snapshots[normalized_index]
            lines.append("")
            lines.append(f"selected_scout: {normalized_index + 1}/{len(snapshots)}")
            lines.append(f"selected_scout_task_id: {selected_snapshot['task_id']}")
            lines.append(
                "selected_scout_task_action: "
                + self._focus_preserving_task_show_action(str(selected_snapshot["task_id"]))
            )
            selected_task_context = self._session.resolve_task_file_context(  # noqa: SLF001
                str(selected_snapshot["task_id"]),
                file_index=effective_file_index,
                preserve_current_focus=False,
            )
            scout_context_lines = self._session.render_resolved_file_context_sections(  # noqa: SLF001
                selected_task_context,
                focused_title="selected scout focused file",
                include_file_context=False,
            )
            if scout_context_lines:
                lines.append("")
                lines.extend(scout_context_lines)
            lines.append("")
            lines.extend(
                    self._render_selected_child_next_action_lines(
                        selected_snapshot,
                        file_context_payload=selected_task_context["payload"],
                    parent_actions=[
                        self._focus_preserving_task_show_action(str(selected_snapshot["task_id"]))
                    ],
                    stay_actions=["/plan scouts"],
                    file_index=effective_file_index,
                )
            )
        return "\n".join(lines)

    def describe_active_plan_execution(
        self,
        *,
        file_index: int = 0,
        preserve_current_focus: bool = True,
    ) -> str:
        return self.describe_active_plan_execution_at(
            0,
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
        artifact = self.active_planning_artifact()
        if artifact is None:
            return "No active planning artifact for /plan execution."
        snapshots = self._planning_artifact_execution_snapshots(artifact)
        execution_context = self._session.resolve_active_plan_execution_file_context(  # noqa: SLF001
            selected_index=selected_index,
            file_index=file_index,
            preserve_current_focus=preserve_current_focus and file_index == 0,
        )
        effective_file_index = int(execution_context["selected_index"])
        lines = [
            f"artifact_id: {artifact.artifact_id}",
            f"goal: {artifact.goal}",
            f"lineage_position: {self._planning_artifact_lineage_position(artifact)}",
            f"detail_mode: {'full' if full_detail else 'compact'}",
            "execution_tasks:",
        ]
        execution_lines = self._render_planning_artifact_execution_outputs(
            artifact,
            selected_index=selected_index,
        )
        if execution_lines:
            lines.extend(execution_lines)
            if snapshots:
                normalized_index = max(0, min(selected_index, len(snapshots) - 1))
                selected_snapshot = snapshots[normalized_index]
                lines.append("")
                lines.append("selected_execution_summary:")
                lines.extend(
                    self._render_selected_child_workflow_summary(
                        selected_snapshot,
                        file_context_payload=self._session.task_file_context_payload(
                            selected_snapshot["task_id"]
                        ),
                        file_index=effective_file_index,
                        parent_command="/plan execution",
                        category_label="phase",
                        category_value=str(selected_snapshot.get("phase") or "(unknown)"),
                        extra_bits=[f"plan_status={selected_snapshot.get('plan_status', '(unknown)')}"],
                        stay_actions=["/plan execution", "/plan advisor", "/advisor status"],
                    )
                )
            detail_lines = self._render_planning_artifact_execution_detail(
                artifact,
                selected_index=selected_index,
                full_detail=full_detail,
            )
            if detail_lines:
                lines.append("")
                lines.append("selected_execution_detail:")
                lines.extend(detail_lines)
            comparison_lines = self._render_selected_execution_comparisons(
                artifact,
                selected_index=selected_index,
            )
            if comparison_lines:
                lines.append("")
                lines.append("selected_execution_comparisons:")
                lines.extend(comparison_lines)
            context_lines = self._render_selected_execution_context(
                artifact,
                selected_index=selected_index,
            )
            if context_lines:
                lines.append("")
                lines.append("selected_execution_context:")
                lines.extend(context_lines)
        else:
            lines.append("(none)")
        if snapshots:
            normalized_index = max(0, min(selected_index, len(snapshots) - 1))
            selected_snapshot = snapshots[normalized_index]
            lines.append("")
            lines.append(f"selected_execution: {normalized_index + 1}/{len(snapshots)}")
            lines.append(f"selected_execution_task_id: {selected_snapshot['task_id']}")
            lines.append(
                "selected_execution_task_action: "
                + self._focus_preserving_task_show_action(str(selected_snapshot["task_id"]))
            )
            lines.append("selected_execution_plan_advisor_action: /plan advisor")
            lines.append("selected_execution_advisor_status_action: /advisor status")
            selected_task_context = self._session.resolve_task_file_context(  # noqa: SLF001
                str(selected_snapshot["task_id"]),
                file_index=effective_file_index,
                preserve_current_focus=False,
            )
            execution_context_lines = self._session.render_resolved_file_context_sections(  # noqa: SLF001
                selected_task_context,
                focused_title="selected execution focused file",
                include_file_context=False,
            )
            if execution_context_lines:
                lines.append("")
                lines.extend(execution_context_lines)
            lines.append("")
            lines.extend(
                    self._render_selected_child_next_action_lines(
                        selected_snapshot,
                        file_context_payload=selected_task_context["payload"],
                    parent_actions=[
                        self._focus_preserving_task_show_action(str(selected_snapshot["task_id"]))
                    ],
                    stay_actions=["/plan execution", "/plan advisor", "/advisor status"],
                    file_index=effective_file_index,
                )
            )
        return "\n".join(lines)

    def active_plan_scout_count(self) -> int:
        artifact = self.active_planning_artifact()
        if artifact is None:
            return 0
        return len(self._planning_artifact_scout_snapshots(artifact))

    def active_plan_execution_count(self) -> int:
        artifact = self.active_planning_artifact()
        if artifact is None:
            return 0
        return len(self._planning_artifact_execution_snapshots(artifact))

    def active_plan_scout_file_context_payload(
        self,
        *,
        selected_index: int = 0,
    ) -> dict[str, Any] | None:
        artifact = self.active_planning_artifact()
        if artifact is None:
            return None
        snapshots = self._planning_artifact_scout_snapshots(artifact)
        if not snapshots:
            return None
        snapshot = snapshots[max(0, min(selected_index, len(snapshots) - 1))]
        return self._session.task_file_context_payload(str(snapshot["task_id"]))

    def active_plan_execution_file_context_payload(
        self,
        *,
        selected_index: int = 0,
    ) -> dict[str, Any] | None:
        artifact = self.active_planning_artifact()
        if artifact is None:
            return None
        snapshots = self._planning_artifact_execution_snapshots(artifact)
        if not snapshots:
            return None
        snapshot = snapshots[max(0, min(selected_index, len(snapshots) - 1))]
        return self._session.task_file_context_payload(str(snapshot["task_id"]))

    def _render_selected_child_workflow_summary(
        self,
        snapshot: dict[str, Any],
        *,
        file_context_payload: dict[str, Any] | None,
        file_index: int,
        parent_command: str,
        category_label: str,
        category_value: str,
        extra_bits: list[str] | None = None,
        stay_actions: list[str] | None = None,
    ) -> list[str]:
        session = self._session
        focused_file_lines: list[str] = []
        progress_summary = str(snapshot.get("progress_summary") or "").strip()
        _files, _bounded_index, focused_item = session._file_context_items_and_index(
            file_context_payload,
            selected_index=file_index,
        )
        action_groups = {
            "go_to_task": [f"/task show {snapshot['task_id']}"],
            "go_to_change": [],
            "stay_on_surface": stay_actions or [parent_command],
        }
        if focused_item is not None:
            focused_path = str(focused_item.get("path") or "").strip()
            if focused_path:
                focused_file_lines.append(f"- focused file: {focused_path}")
            scope_reasons = session._file_context_scope_reasons(focused_item)
            if scope_reasons:
                focused_file_lines.append("- in scope because: " + ", ".join(scope_reasons))
            related_change = str(focused_item.get("change_id") or "").strip()
            if related_change:
                focused_file_lines.append(f"- related change: {related_change}")
            focused_file_lines.append(
                f"- diff hunks: {session._file_context_diff_hunk_count(focused_item)}"
            )
            focused_file_lines.append(
                "- context-only: "
                + ("yes" if session._file_context_is_context_only(focused_item) else "no")
            )
            change_navigation = session._resolve_change_navigation_for_file_context_item(focused_item)
            if change_navigation is not None:
                action_groups["go_to_change"].append(str(change_navigation["change_command"]))
                file_command = str(change_navigation.get("change_file_command") or "").strip()
                if file_command:
                    action_groups["go_to_change"].append(file_command)
        return session.render_selected_surface_summary(  # noqa: SLF001
            "",
            summary_fields=[
                ("task_id", str(snapshot["task_id"])),
                ("status", str(snapshot["status"])),
                ("kind", str(snapshot.get("kind", "(unknown)"))),
                (category_label, category_value),
                ("description", str(snapshot["description"])),
                ("progress_summary", progress_summary or None),
            ],
            metadata_line=", ".join(bit for bit in (extra_bits or []) if bit) or None,
            focused_file_lines=focused_file_lines or None,
            action_groups=action_groups,
            action_order=("go_to_task", "go_to_change", "stay_on_surface"),
        )

    def _render_selected_child_next_action_lines(
        self,
        snapshot: dict[str, Any],
        *,
        file_context_payload: dict[str, Any] | None,
        parent_actions: list[str],
        stay_actions: list[str],
        file_index: int,
        prefix: str = "",
    ) -> list[str]:
        session = self._session
        change_actions: list[str] = []
        _files, _bounded_index, focused_item = session._file_context_items_and_index(
            file_context_payload,
            selected_index=file_index,
        )
        if focused_item is not None:
            change_navigation = session._resolve_change_navigation_for_file_context_item(focused_item)
            if change_navigation is not None:
                change_actions.append(str(change_navigation["change_command"]))
                file_command = change_navigation.get("change_file_command")
                if isinstance(file_command, str) and file_command.strip():
                    change_actions.append(file_command)
        return [
            f"{prefix}{line}"
            for line in session.render_workflow_action_sections(  # noqa: SLF001
                {
                    "go_to_task": session._dedupe_action_commands(parent_actions),  # noqa: SLF001
                    "go_to_change": session._dedupe_action_commands(change_actions),  # noqa: SLF001
                    "stay_on_surface": session._dedupe_action_commands(stay_actions),  # noqa: SLF001
                },
                ordered_keys=("go_to_task", "go_to_change", "stay_on_surface"),
            )
        ]

    def _dedupe_actions(self, actions: list[str]) -> list[str]:
        return self._session._dedupe_action_commands(actions)  # noqa: SLF001

    def _focus_preserving_task_show_action(self, task_id: str, *, fallback_file_index: int = 0) -> str:
        return self._session.focus_preserving_task_show_command(  # noqa: SLF001
            task_id,
            fallback_file_index=fallback_file_index,
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
        return self.describe_active_plan_timeline_at(
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
        return self.describe_active_plan_replay_at(
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
        return self.describe_active_plan_audit_at(
            selected_index,
            artifact_id=artifact_id,
        )

    def describe_active_plan_timeline_at(
        self,
        selected_index: int = 0,
        **kwargs: Any,
    ) -> str:
        session = self._session
        kind_filter = str(kwargs.get("kind_filter", "all"))
        delta_mode = str(kwargs.get("delta_mode", "none"))
        phase_filter = str(kwargs.get("phase_filter", "none"))
        focus_mode = str(kwargs.get("focus_mode", "none"))
        compare_mode = str(kwargs.get("compare_mode", "none"))
        selected_compare_index = int(kwargs.get("selected_compare_index", 0))
        selected_phase_local_task_index = int(kwargs.get("selected_phase_local_task_index", 0))
        artifact_id = kwargs.get("artifact_id")
        artifact = self._resolve_timeline_artifact(artifact_id)
        if artifact is None:
            return "No active planning artifact for /plan timeline."
        normalized_filter = self._normalize_timeline_kind_filter(kind_filter)
        normalized_delta = self._normalize_timeline_delta_mode(delta_mode)
        normalized_phase = self._normalize_timeline_phase_filter(phase_filter)
        normalized_focus = self._normalize_timeline_focus_mode(focus_mode)
        normalized_compare = self._normalize_timeline_compare_mode(compare_mode)
        entries = self._planning_artifact_timeline_entries(
            artifact,
            kind_filter=normalized_filter,
            delta_mode=normalized_delta,
            phase_filter=normalized_phase,
            focus_mode=normalized_focus,
        )
        audit_summary = self._timeline_audit_summary(entries)
        section_summaries = self._timeline_section_summaries(entries)
        normalized_index = max(0, min(selected_index, len(entries) - 1)) if entries else 0
        selected_entry = entries[normalized_index] if entries else None
        compare_items = self._timeline_compare_items(
            artifact,
            kind_filter=normalized_filter,
            delta_mode=normalized_delta,
            phase_filter=normalized_phase,
            focus_mode=normalized_focus,
            compare_mode=normalized_compare,
            current_entries=entries,
        )
        normalized_compare_index = (
            max(0, min(selected_compare_index, len(compare_items) - 1)) if compare_items else 0
        )
        selected_compare = compare_items[normalized_compare_index] if compare_items else None
        lines = [
            f"artifact_id: {artifact.artifact_id}",
            f"goal: {artifact.goal}",
            f"lineage_position: {session._planning_artifact_lineage_position(artifact)}",
            f"timeline_artifact: {artifact.artifact_id}",
            f"timeline_filter: {normalized_filter}",
            f"timeline_delta: {normalized_delta}",
            f"timeline_phase: {normalized_phase}",
            f"timeline_focus: {normalized_focus}",
            f"timeline_compare: {normalized_compare}",
            f"selected_timeline: {normalized_index + 1}/{len(entries) if entries else 1}",
            f"selected_timeline_compare: {normalized_compare_index + 1}/{len(compare_items) if compare_items else 1}",
            "audit_summary:",
            f"- entries: {audit_summary['entry_count']}",
            f"- task_count: {audit_summary['task_count']}",
            f"- session_span: {audit_summary['session_span']}",
            f"- session_duration: {audit_summary['session_duration']}",
            f"- last_updated: {audit_summary['last_updated']}",
            f"- kinds: {audit_summary['kinds']}",
            f"- sections: {audit_summary['sections']}",
            f"- latest_execution_status: {audit_summary['latest_execution_status']}",
            f"- latest_advisor_status: {audit_summary['latest_advisor_status']}",
            f"- latest_drift_status: {audit_summary['latest_drift_status']}",
        ]
        phase_local_audit_summary = self._timeline_phase_local_audit_summary(
            artifact=artifact,
            entries=entries,
            phase_filter=normalized_phase,
            selected_task_index=selected_phase_local_task_index,
        )
        if phase_local_audit_summary:
            lines.append("phase_local_audit_summary:")
            lines.extend(f"- {line}" for line in phase_local_audit_summary)
        if compare_items:
            lines.append("compare_lens:")
            for index, item in enumerate(compare_items, start=1):
                marker = ">" if index - 1 == normalized_compare_index else "-"
                lines.append(f"{marker} {item['label']}: {item['summary']}")
                detail = str(item.get("detail") or "").strip()
                if detail:
                    lines.extend(f"  {line}" for line in detail.splitlines())
        lines.append("timeline:")
        if entries:
            current_section = None
            for index, entry in enumerate(entries, start=1):
                section = str(entry.get("section") or "Timeline")
                if section != current_section:
                    summary = section_summaries.get(section, {})
                    lines.append(
                        f"[{section}] entries={summary.get('entry_count', 0)} "
                        f"tasks={summary.get('task_count', 0)} "
                        f"span={summary.get('span', 'none')} "
                        f"duration={summary.get('duration', '0s')} "
                        f"last_updated={summary.get('last_updated', 'none')} "
                        f"kinds={summary.get('kinds', 'none')} "
                        f"latest={summary.get('latest_status', 'none')}"
                    )
                    current_section = section
                marker = ">" if index - 1 == normalized_index else "-"
                lines.append(f"{marker} {entry['timestamp']} [{entry['kind']}] {entry['summary']}")
                detail = str(entry.get("detail") or "").strip()
                if detail:
                    lines.extend(f"  {line}" for line in detail.splitlines())
        else:
            lines.append("(none)")
        if selected_entry is not None:
            lines.append("")
            lines.append(f"selected_timeline_section: {selected_entry.get('section', 'Timeline')}")
            lines.append(f"selected_timeline_kind: {selected_entry['kind']}")
            lines.append(f"selected_timeline_summary: {selected_entry['summary']}")
            if selected_entry.get("task_id"):
                lines.append(f"selected_timeline_task_id: {selected_entry['task_id']}")
            primary_action = str(selected_entry.get("primary_action") or "").strip()
            secondary_action = str(selected_entry.get("secondary_action") or "").strip()
            if primary_action:
                lines.append(f"selected_timeline_primary_action: {primary_action}")
            if secondary_action:
                lines.append(f"selected_timeline_secondary_action: {secondary_action}")
        if selected_compare is not None:
            lines.append("")
            lines.append(f"selected_timeline_compare_label: {selected_compare['label']}")
            lines.append(f"selected_timeline_compare_summary: {selected_compare['summary']}")
            compare_primary_action = str(selected_compare.get("primary_action") or "").strip()
            compare_secondary_action = str(selected_compare.get("secondary_action") or "").strip()
            if compare_primary_action:
                lines.append(f"selected_timeline_compare_primary_action: {compare_primary_action}")
            if compare_secondary_action:
                lines.append(f"selected_timeline_compare_secondary_action: {compare_secondary_action}")
        lines.append("")
        timeline_actions = {
            "primary": str(selected_entry.get("primary_action") or "").strip() if selected_entry is not None else None,
            "secondary": str(selected_entry.get("secondary_action") or "").strip() if selected_entry is not None else None,
            "compare_primary": str(selected_compare.get("primary_action") or "").strip() if selected_compare is not None else None,
            "compare_secondary": str(selected_compare.get("secondary_action") or "").strip() if selected_compare is not None else None,
        }
        lines.extend(
            session.render_navigation_section(
                timeline_actions,
                heading="next_actions:",
                line_prefix="- ",
                include_empty=False,
            )
        )
        lines.append("- /plan timeline all")
        lines.append("- /plan timeline execution")
        lines.append("- /plan timeline advisor")
        lines.append("- /plan timeline all delta=after-drift")
        lines.append("- /plan timeline all focus=execution")
        lines.append("- /plan timeline all compare=after-drift-vs-all")
        lines.append("- /plan timeline all compare=execution-vs-scout")
        lines.append("- /plan scouts")
        lines.append("- /plan execution")
        lines.append("- /plan advisor")
        lines.append("- /plan lineage")
        return "\n".join(lines)

    def describe_active_plan_replay_at(self, selected_index: int = 0, **kwargs: Any) -> str:
        session = self._session
        kind_filter = str(kwargs.get("kind_filter", "all"))
        delta_mode = str(kwargs.get("delta_mode", "none"))
        phase_filter = str(kwargs.get("phase_filter", "none"))
        focus_mode = str(kwargs.get("focus_mode", "none"))
        compare_mode = str(kwargs.get("compare_mode", "none"))
        selected_compare_index = int(kwargs.get("selected_compare_index", 0))
        selected_phase_local_task_index = int(kwargs.get("selected_phase_local_task_index", 0))
        latest = bool(kwargs.get("latest", False))
        source_mode = str(kwargs.get("source_mode", "auto"))
        artifact_id = kwargs.get("artifact_id")
        artifact = self._resolve_timeline_artifact(artifact_id)
        if artifact is None:
            return "No active planning artifact for /plan replay."
        normalized_filter = self._normalize_timeline_kind_filter(kind_filter)
        normalized_delta = self._normalize_timeline_delta_mode(delta_mode)
        normalized_phase = self._normalize_timeline_phase_filter(phase_filter)
        normalized_focus = self._normalize_timeline_focus_mode(focus_mode)
        normalized_compare = self._normalize_timeline_compare_mode(compare_mode)
        normalized_source = self._normalize_replay_source_mode(source_mode)
        entries = self._planning_artifact_timeline_entries(
            artifact,
            kind_filter=normalized_filter,
            delta_mode=normalized_delta,
            phase_filter=normalized_phase,
            focus_mode=normalized_focus,
        )
        compare_items = self._timeline_compare_items(
            artifact,
            kind_filter=normalized_filter,
            delta_mode=normalized_delta,
            phase_filter=normalized_phase,
            focus_mode=normalized_focus,
            compare_mode=normalized_compare,
            current_entries=entries,
        )
        normalized_compare_index = (
            max(0, min(selected_compare_index, len(compare_items) - 1)) if compare_items else 0
        )
        selected_compare = compare_items[normalized_compare_index] if compare_items else None
        replay_source, replay_artifact, replay_entries, replay_index, selected_entry = self._resolve_replay_target(
            artifact=artifact,
            entries=entries,
            compare_items=compare_items,
            selected_compare_index=normalized_compare_index,
            phase_filter=normalized_phase,
            selected_phase_local_task_index=selected_phase_local_task_index,
            selected_index=selected_index,
            latest=latest,
            source_mode=normalized_source,
        )
        lines = [
            f"artifact_id: {artifact.artifact_id}",
            f"goal: {artifact.goal}",
            f"lineage_position: {session._planning_artifact_lineage_position(artifact)}",
            f"replay_artifact: {replay_artifact.artifact_id}",
            f"replay_goal: {replay_artifact.goal}",
            f"replay_lineage_position: {session._planning_artifact_lineage_position(replay_artifact)}",
            f"replay_filter: {normalized_filter}",
            f"replay_delta: {normalized_delta}",
            f"replay_phase: {normalized_phase}",
            f"replay_focus: {normalized_focus}",
            f"replay_compare: {normalized_compare}",
            f"replay_source: {replay_source}",
            f"replay_cursor: {replay_index + 1}/{len(replay_entries) if replay_entries else 0}",
        ]
        if selected_entry is None:
            lines.append("selected_replay_entry: (none)")
            return "\n".join(lines)
        previous_summary = replay_entries[replay_index - 1]["summary"] if replay_index > 0 else "none"
        next_summary = replay_entries[replay_index + 1]["summary"] if replay_index + 1 < len(replay_entries) else "none"
        lines.append(f"replay_previous_summary: {previous_summary}")
        lines.append(f"replay_next_summary: {next_summary}")
        if selected_compare is not None and replay_source == "compare-item":
            lines.append(f"replay_source_context: compare:{selected_compare['label']}")
        elif replay_source == "phase-local-summary":
            lines.append("replay_source_context: phase-local-summary")
        elif replay_source == "phase-slice":
            lines.append(f"replay_source_context: phase:{normalized_phase}")
        else:
            lines.append("replay_source_context: timeline-entry")
        lines.append("selected_replay_entry:")
        lines.append(f"- timestamp: {selected_entry['timestamp']}")
        lines.append(f"- section: {selected_entry.get('section', 'Timeline')}")
        lines.append(f"- kind: {selected_entry['kind']}")
        lines.append(f"- summary: {selected_entry['summary']}")
        if selected_entry.get("task_id"):
            lines.append(f"- task_id: {selected_entry['task_id']}")
        detail = str(selected_entry.get("detail") or "").strip()
        if detail:
            lines.append("- detail:")
            lines.extend(f"  {line}" for line in detail.splitlines())
        linked_blocks = session._render_replay_linked_blocks(replay_artifact, selected_entry)
        if linked_blocks:
            lines.append("linked_blocks:")
            lines.extend(linked_blocks)
        lineage_compare_lines = self._render_lineage_replay_compare(
            artifact=artifact,
            replay_artifact=replay_artifact,
            kind_filter=normalized_filter,
            delta_mode=normalized_delta,
            phase_filter=normalized_phase,
            focus_mode=normalized_focus,
            compare_mode=normalized_compare,
        )
        if lineage_compare_lines:
            lines.append("lineage_replay_compare:")
            lines.extend(lineage_compare_lines)
        primary_action = str(selected_entry.get("primary_action") or "").strip()
        secondary_action = str(selected_entry.get("secondary_action") or "").strip()
        lines.extend(
            session.render_primary_secondary_action_section(
                primary_action=primary_action,
                secondary_action=secondary_action,
                primary_label="selected_replay_primary_action",
                secondary_label="selected_replay_secondary_action",
            )
        )
        replay_command_args = [normalized_filter]
        if normalized_delta != "none":
            replay_command_args.append(f"delta={normalized_delta}")
        if normalized_phase != "none":
            replay_command_args.append(f"phase={normalized_phase}")
        if normalized_focus != "none":
            replay_command_args.append(f"focus={normalized_focus}")
        if normalized_compare != "none":
            replay_command_args.append(f"compare={normalized_compare}")
        replay_command_args.append(f"artifact={replay_artifact.artifact_id}")
        replay_actions: dict[str, str | None] = {
            "previous": "/plan replay " + " ".join([f"at={replay_index}", *replay_command_args]) if replay_index > 0 else None,
            "next": "/plan replay " + " ".join([f"at={replay_index + 2}", *replay_command_args]) if replay_index + 1 < len(replay_entries) else None,
            "primary": primary_action or None,
            "secondary": secondary_action or None,
        }
        lines.extend(
            session.render_navigation_section(
                replay_actions,
                heading="next_actions:",
                line_prefix="- ",
                include_empty=False,
            )
        )
        if normalized_compare == "active-vs-previous":
            compare_artifact_actions = session._lineage_replay_compare_actions(
                artifact=artifact,
                kind_filter=normalized_filter,
                delta_mode=normalized_delta,
                phase_filter=normalized_phase,
                focus_mode=normalized_focus,
            )
            lines.extend(compare_artifact_actions)
        lines.append("- /plan timeline all")
        lines.append("- /plan execution")
        lines.append("- /plan advisor")
        return "\n".join(lines)

    def describe_active_plan_audit_at(self, selected_index: int | None = None, **kwargs: Any) -> str:
        session = self._session
        artifact_id = kwargs.get("artifact_id")
        artifact = self._resolve_timeline_artifact(artifact_id)
        if artifact is None:
            return "No active planning artifact for /plan audit."
        lineage = self._planning_artifact_lineage(artifact)
        if not lineage:
            return "No planning artifact lineage available for /plan audit."
        artifact_index = 0
        for index, item in enumerate(lineage):
            if item.artifact_id == artifact.artifact_id:
                artifact_index = index
                break
        normalized_index = artifact_index if selected_index is None else max(0, min(selected_index, len(lineage) - 1))
        selected = lineage[normalized_index]
        audit_items = [self._planning_artifact_audit_item(item) for item in lineage]
        lineage_summary = self._planning_artifact_lineage_audit_summary(audit_items)
        selected_item = audit_items[normalized_index]
        selected_summary = selected_item["audit_summary"]
        selected_sections = selected_item["section_summaries"]
        selected_primary_action = str(selected_item["primary_action"])
        selected_secondary_action = str(selected_item["secondary_action"])
        lines = [
            f"artifact_id: {artifact.artifact_id}",
            f"goal: {artifact.goal}",
            f"lineage_position: {self._planning_artifact_lineage_position(artifact)}",
            f"selected_lineage_audit: {normalized_index + 1}/{len(lineage)}",
            f"selected_audit_artifact_id: {selected.artifact_id}",
            f"selected_audit_goal: {selected.goal}",
            f"selected_audit_lineage_position: {self._planning_artifact_lineage_position(selected)}",
            "lineage_audit_summary:",
            f"- artifacts: {lineage_summary['artifact_count']}",
            f"- total_entries: {lineage_summary['entry_count']}",
            f"- total_tasks: {lineage_summary['task_count']}",
            f"- latest_updated: {lineage_summary['latest_updated']}",
            f"- active_artifact: {lineage_summary['active_artifact']}",
            f"- latest_execution_status: {lineage_summary['latest_execution_status']}",
            f"- latest_advisor_status: {lineage_summary['latest_advisor_status']}",
            f"- latest_drift_status: {lineage_summary['latest_drift_status']}",
            "artifacts:",
        ]
        lines.extend(
            self._render_planning_artifact_audit_items(
                audit_items,
                selected_index=normalized_index,
                current_artifact_id=artifact.artifact_id,
            )
        )
        lines.append("")
        lines.append("selected_artifact_audit_summary:")
        lines.append(f"- entries: {selected_summary['entry_count']}")
        lines.append(f"- task_count: {selected_summary['task_count']}")
        lines.append(f"- session_span: {selected_summary['session_span']}")
        lines.append(f"- session_duration: {selected_summary['session_duration']}")
        lines.append(f"- last_updated: {selected_summary['last_updated']}")
        lines.append(f"- kinds: {selected_summary['kinds']}")
        lines.append(f"- sections: {selected_summary['sections']}")
        lines.append(f"- latest_execution_status: {selected_summary['latest_execution_status']}")
        lines.append(f"- latest_advisor_status: {selected_summary['latest_advisor_status']}")
        lines.append(f"- latest_drift_status: {selected_summary['latest_drift_status']}")
        lines.append("selected_artifact_phase_summaries:")
        for section in ("Plan Setup", "Scout Research", "Execution Loop", "Advisor & Drift"):
            summary = selected_sections.get(section)
            if summary is None:
                continue
            lines.append(
                f"- {section}: entries={summary['entry_count']} tasks={summary['task_count']} "
                f"span={summary['span']} duration={summary['duration']} "
                f"last_updated={summary['last_updated']} kinds={summary['kinds']} "
                f"latest={summary['latest_status']}"
            )
        selected_delta_lines = self._render_selected_artifact_audit_deltas(lineage, audit_items, normalized_index)
        if selected_delta_lines:
            lines.append("selected_artifact_deltas:")
            lines.extend(selected_delta_lines)
        lines.append("")
        lines.extend(
            session.render_primary_secondary_action_section(
                primary_action=selected_primary_action,
                secondary_action=selected_secondary_action,
                primary_label="selected_audit_primary_action",
                secondary_label="selected_audit_secondary_action",
            )
        )
        lines.extend(
            session.render_navigation_section(
                {
                    "selected_replay": selected_primary_action,
                    "selected_timeline": selected_secondary_action,
                    "selected_show": f"/plan show {selected.artifact_id}",
                    "selected_replay_previous": f"/plan replay latest artifact={selected.artifact_id}",
                    "previous_show": f"/plan show {selected.supersedes_artifact_id}" if selected.supersedes_artifact_id else None,
                    "next_show": f"/plan show {selected.superseded_by_artifact_id}" if selected.superseded_by_artifact_id else None,
                },
                heading="next_actions:",
                line_prefix="- ",
                include_empty=False,
            )
        )
        lines.append("- /plan lineage")
        lines.append("- /plan timeline all")
        lines.append("- /plan replay latest")
        return "\n".join(lines)

    def describe_active_plan_lineage(self) -> str:
        return self.describe_active_plan_lineage_at(0)

    def describe_active_plan_lineage_at(self, selected_index: int = 0) -> str:
        session = self._session
        artifact = self.active_planning_artifact()
        if artifact is None:
            return "No active planning artifact for /plan lineage."
        lineage = self._planning_artifact_lineage(artifact)
        normalized_index = max(0, min(selected_index, len(lineage) - 1)) if lineage else 0
        selected = lineage[normalized_index] if lineage else artifact
        lines = [
            f"artifact_id: {artifact.artifact_id}",
            f"goal: {artifact.goal}",
            f"lineage_position: {self._planning_artifact_lineage_position(artifact)}",
            f"selected_lineage: {normalized_index + 1}/{len(lineage) if lineage else 1}",
            f"selected_lineage_artifact_id: {selected.artifact_id}",
            f"selected_lineage_goal: {selected.goal}",
            f"selected_lineage_default_action: {self._lineage_default_action(selected)}",
            "lineage:",
        ]
        lineage_lines = self._render_planning_artifact_lineage(artifact, selected_index=normalized_index)
        if lineage_lines:
            lines.extend(lineage_lines)
        else:
            lines.append("(none)")
        comparison_lines = self._render_planning_artifact_comparisons(selected)
        if comparison_lines:
            lines.append("")
            lines.append("comparisons:")
            lines.extend(comparison_lines)
        action_lines = self._render_lineage_actions(lineage, current_artifact=selected)
        if action_lines:
            lines.append("")
            lines.append("next_actions:")
            lines.extend(action_lines)
        return "\n".join(lines)

    def active_plan_lineage_index(self) -> int:
        artifact = self.active_planning_artifact()
        if artifact is None:
            return 0
        lineage = self._planning_artifact_lineage(artifact)
        for index, item in enumerate(lineage):
            if item.artifact_id == artifact.artifact_id:
                return index
        return 0

    def _render_planning_artifact_lineage(
        self,
        artifact,
        *,
        selected_index: int | None = None,
    ) -> list[str]:
        session = self._session
        lineage = self._planning_artifact_lineage(artifact)
        if not lineage:
            return []
        active_id = session.state.active_planning_artifact_id
        lines: list[str] = []
        normalized_index = None
        if selected_index is not None and lineage:
            normalized_index = max(0, min(selected_index, len(lineage) - 1))
        for index, item in enumerate(lineage):
            roles = []
            if item.artifact_id == artifact.artifact_id:
                roles.append("current")
            if item.artifact_id == active_id:
                roles.append("active")
            role_suffix = f" ({', '.join(roles)})" if roles else ""
            marker = ">" if normalized_index == index else "-"
            line = (
                f"{marker} {index + 1}. {item.artifact_id}{role_suffix}: goal={item.goal} "
                f"supersedes={item.supersedes_artifact_id or 'none'} "
                f"superseded_by={item.superseded_by_artifact_id or 'none'}"
            )
            action_bits = [f"/plan show {item.artifact_id}"]
            if item.artifact_id != active_id:
                action_bits.append(f"/plan revert {item.artifact_id}")
            else:
                action_bits.append(f"/plan derive {item.goal}")
            line += " actions=" + " | ".join(action_bits)
            lines.append(line)
        return lines

    def _render_lineage_actions(
        self,
        lineage,
        *,
        current_artifact,
    ) -> list[str]:
        session = self._session
        if not lineage:
            return []
        lines: list[str] = [f"- selected: /plan derive {current_artifact.goal}"]
        current_index = 0
        active_id = session.state.active_planning_artifact_id
        for index, item in enumerate(lineage):
            if item.artifact_id == current_artifact.artifact_id:
                current_index = index
                break
        if current_index > 0:
            previous = lineage[current_index - 1]
            lines.append(f"- revert_to_previous: /plan revert {previous.artifact_id}")
        if current_index < len(lineage) - 1:
            next_item = lineage[current_index + 1]
            lines.append(f"- inspect_newer_revision: /plan show {next_item.artifact_id}")
            lines.append(f"- reactivate_newer_revision: /plan revert {next_item.artifact_id}")
        lines.append(f"- inspect_selected: /plan show {current_artifact.artifact_id}")
        if active_id and active_id != current_artifact.artifact_id:
            lines.append(f"- inspect_active_plan: /plan show {active_id}")
        return lines

    def _lineage_default_action(self, artifact) -> str:
        active_id = self._session.state.active_planning_artifact_id
        if artifact.artifact_id == active_id:
            return f"/plan derive {artifact.goal}"
        return f"/plan revert {artifact.artifact_id}"

    def _planning_artifact_lineage(self, artifact) -> list[Any]:
        artifact_map = {item.artifact_id: item for item in self.planning_artifacts()}
        root = artifact
        seen: set[str] = set()
        while (
            root.supersedes_artifact_id
            and root.supersedes_artifact_id in artifact_map
            and root.artifact_id not in seen
        ):
            seen.add(root.artifact_id)
            root = artifact_map[root.supersedes_artifact_id]
        lineage = [root]
        seen = {root.artifact_id}
        current = root
        while (
            current.superseded_by_artifact_id
            and current.superseded_by_artifact_id in artifact_map
            and current.superseded_by_artifact_id not in seen
        ):
            current = artifact_map[current.superseded_by_artifact_id]
            lineage.append(current)
            seen.add(current.artifact_id)
        return lineage

    def _planning_artifact_lineage_position(self, artifact) -> str:
        lineage = self._planning_artifact_lineage(artifact)
        if not lineage:
            return "unknown"
        for index, item in enumerate(lineage, start=1):
            if item.artifact_id == artifact.artifact_id:
                return f"{index}/{len(lineage)}"
        return f"?/{len(lineage)}"

    def _planning_artifact_audit_item(self, artifact) -> dict[str, Any]:
        entries = self._planning_artifact_timeline_entries(
            artifact,
            kind_filter="all",
            delta_mode="none",
            phase_filter="none",
            focus_mode="none",
        )
        return {
            "artifact": artifact,
            "entries": entries,
            "audit_summary": self._timeline_audit_summary(entries),
            "section_summaries": self._timeline_section_summaries(entries),
            "primary_action": f"/plan replay latest artifact={artifact.artifact_id}",
            "secondary_action": f"/plan timeline all artifact={artifact.artifact_id}",
        }

    def _planning_artifact_lineage_audit_summary(
        self,
        audit_items: list[dict[str, Any]],
    ) -> dict[str, str]:
        if not audit_items:
            return {
                "artifact_count": "0",
                "entry_count": "0",
                "task_count": "0",
                "latest_updated": "none",
                "active_artifact": "none",
                "latest_execution_status": "none",
                "latest_advisor_status": "none",
                "latest_drift_status": "none",
            }
        last_updated_values = [
            str(item["audit_summary"]["last_updated"])
            for item in audit_items
            if str(item["audit_summary"]["last_updated"]) != "none"
        ]
        latest_item = audit_items[-1]["audit_summary"]
        return {
            "artifact_count": str(len(audit_items)),
            "entry_count": str(sum(int(item["audit_summary"]["entry_count"]) for item in audit_items)),
            "task_count": str(sum(int(item["audit_summary"]["task_count"]) for item in audit_items)),
            "latest_updated": max(last_updated_values) if last_updated_values else "none",
            "active_artifact": self._session.state.active_planning_artifact_id or "none",
            "latest_execution_status": str(latest_item["latest_execution_status"]),
            "latest_advisor_status": str(latest_item["latest_advisor_status"]),
            "latest_drift_status": str(latest_item["latest_drift_status"]),
        }

    def _render_planning_artifact_audit_items(
        self,
        audit_items: list[dict[str, Any]],
        *,
        selected_index: int,
        current_artifact_id: str,
    ) -> list[str]:
        active_id = self._session.state.active_planning_artifact_id
        lines: list[str] = []
        for index, item in enumerate(audit_items):
            artifact = item["artifact"]
            summary = item["audit_summary"]
            roles = []
            if artifact.artifact_id == current_artifact_id:
                roles.append("current")
            if artifact.artifact_id == active_id:
                roles.append("active")
            role_suffix = f" ({', '.join(roles)})" if roles else ""
            drift_suffix = " derived_from_drift=yes" if artifact.derived_from_drift else ""
            marker = ">" if index == selected_index else "-"
            line = (
                f"{marker} {index + 1}. {artifact.artifact_id}{role_suffix}: goal={artifact.goal} "
                f"entries={summary['entry_count']} tasks={summary['task_count']} "
                f"latest_execution={summary['latest_execution_status']} "
                f"latest_advisor={summary['latest_advisor_status']} "
                f"latest_drift={summary['latest_drift_status']}{drift_suffix} "
                f"actions={item['primary_action']} | {item['secondary_action']} | /plan show {artifact.artifact_id}"
            )
            lines.append(line)
        return lines

    def _render_selected_artifact_audit_deltas(
        self,
        lineage,
        audit_items: list[dict[str, Any]],
        selected_index: int,
    ) -> list[str]:
        lines: list[str] = []
        if selected_index > 0:
            lines.extend(
                self._render_artifact_audit_delta(
                    "against_previous",
                    audit_items[selected_index - 1],
                    audit_items[selected_index],
                )
            )
        if selected_index < len(lineage) - 1:
            lines.extend(
                self._render_artifact_audit_delta(
                    "against_next",
                    audit_items[selected_index],
                    audit_items[selected_index + 1],
                )
            )
        return lines

    def _render_artifact_audit_delta(
        self,
        label: str,
        left_item: dict[str, Any],
        right_item: dict[str, Any],
    ) -> list[str]:
        left_artifact = left_item["artifact"]
        right_artifact = right_item["artifact"]
        left_summary = left_item["audit_summary"]
        right_summary = right_item["audit_summary"]
        lines = [
            (
                f"- {label}: {left_artifact.artifact_id} -> {right_artifact.artifact_id} "
                f"entry_delta={int(right_summary['entry_count']) - int(left_summary['entry_count'])} "
                f"task_delta={int(right_summary['task_count']) - int(left_summary['task_count'])}"
            )
        ]
        if str(left_summary["latest_execution_status"]) != str(right_summary["latest_execution_status"]):
            lines.append(
                "  latest_execution_changed: "
                f"{left_summary['latest_execution_status']} -> {right_summary['latest_execution_status']}"
            )
        if str(left_summary["latest_advisor_status"]) != str(right_summary["latest_advisor_status"]):
            lines.append(
                "  latest_advisor_changed: "
                f"{left_summary['latest_advisor_status']} -> {right_summary['latest_advisor_status']}"
            )
        if str(left_summary["latest_drift_status"]) != str(right_summary["latest_drift_status"]):
            lines.append(
                "  latest_drift_changed: "
                f"{left_summary['latest_drift_status']} -> {right_summary['latest_drift_status']}"
            )
        return lines

    def _render_planning_artifact_comparisons(self, artifact) -> list[str]:
        lineage = self._planning_artifact_lineage(artifact)
        if not lineage:
            return []
        try:
            index = next(i for i, item in enumerate(lineage) if item.artifact_id == artifact.artifact_id)
        except StopIteration:
            return []
        lines: list[str] = []
        if index > 0:
            lines.extend(
                self._render_planning_artifact_comparison(
                    "against_previous",
                    lineage[index - 1],
                    artifact,
                )
            )
        if index < len(lineage) - 1:
            lines.extend(
                self._render_planning_artifact_comparison(
                    "against_next",
                    artifact,
                    lineage[index + 1],
                )
            )
        return lines

    def _planning_artifact_timeline_entries(
        self,
        artifact,
        *,
        kind_filter: str = "all",
        delta_mode: str = "none",
        phase_filter: str = "none",
        focus_mode: str = "none",
    ) -> list[dict[str, str]]:
        session = self._session
        entries: list[dict[str, str]] = [
            {
                "timestamp": artifact.created_at,
                "kind": "plan",
                "section": "Plan Setup",
                "summary": (
                    f"created kind={artifact.kind} goal={artifact.goal}"
                    + (" derived_from_drift=yes" if artifact.derived_from_drift else "")
                ),
                "detail": session._compact_multiline_text(
                    artifact.summary,
                    max_lines=6,
                    max_chars=800,
                ),
                "primary_action": f"/plan show {artifact.artifact_id}",
                "secondary_action": "/plan lineage",
            }
        ]
        if artifact.advisor_status:
            detail_lines: list[str] = []
            if artifact.advisor_reason:
                detail_lines.append(f"reason: {artifact.advisor_reason}")
            if artifact.advisor_risk_flags:
                detail_lines.append("risk_flags: " + ", ".join(artifact.advisor_risk_flags))
            if artifact.advisor_suggested_changes:
                detail_lines.append("suggested_changes:")
                detail_lines.extend(f"  - {item}" for item in artifact.advisor_suggested_changes)
            entries.append(
                {
                    "timestamp": artifact.created_at,
                    "kind": "advisor",
                    "section": "Plan Setup",
                    "summary": f"artifact_review status={artifact.advisor_status}",
                    "detail": "\n".join(detail_lines),
                    "primary_action": "/plan advisor",
                    "secondary_action": "/advisor status",
                }
            )
        for snapshot in session._planning_artifact_scout_snapshots(artifact):
            entries.extend(self._timeline_entries_for_task_snapshot(snapshot, task_kind="scout"))
        for snapshot in session._planning_artifact_execution_snapshots(artifact):
            entries.extend(self._timeline_entries_for_task_snapshot(snapshot, task_kind="execution"))
        for review in session.state.advisor_review_history:
            detail_lines = []
            if review.reason:
                detail_lines.append(f"reason: {review.reason}")
            if review.risk_flags:
                detail_lines.append("risk_flags: " + ", ".join(review.risk_flags))
            if review.suggested_changes:
                detail_lines.append("suggested_changes:")
                detail_lines.extend(f"  - {item}" for item in review.suggested_changes)
            entries.append(
                {
                    "timestamp": review.created_at,
                    "kind": "advisor",
                    "section": "Advisor & Drift",
                    "summary": f"session_review checkpoint={review.checkpoint} status={review.status}",
                    "detail": "\n".join(detail_lines),
                    "primary_action": "/advisor status",
                    "secondary_action": "/plan advisor",
                }
            )
        if session.state.last_plan_drift_context:
            detail_lines = []
            if session.state.last_plan_drift_reason:
                detail_lines.append(f"reason: {session.state.last_plan_drift_reason}")
            compact = session._compact_multiline_text(
                session.state.last_plan_drift_context,
                max_lines=8,
                max_chars=1200,
            )
            detail_lines.extend(compact.splitlines())
            entries.append(
                {
                    "timestamp": session._latest_plan_drift_timestamp(),
                    "kind": "drift",
                    "section": "Advisor & Drift",
                    "summary": "plan_drift"
                    + (
                        f" status={session.state.last_plan_drift_status}"
                        if session.state.last_plan_drift_status
                        else ""
                    ),
                    "detail": "\n".join(detail_lines),
                    "primary_action": self._timeline_drift_primary_action(artifact),
                    "secondary_action": "/plan execution",
                }
            )
        filtered = [
            entry
            for entry in entries
            if self._timeline_entry_matches_filter(entry, kind_filter=kind_filter)
        ]
        filtered = [
            entry
            for entry in filtered
            if self._timeline_entry_matches_delta(entry, artifact=artifact, delta_mode=delta_mode)
        ]
        filtered = [
            entry
            for entry in filtered
            if self._timeline_entry_matches_phase(entry, phase_filter=phase_filter)
        ]
        filtered = [
            entry
            for entry in filtered
            if self._timeline_entry_matches_focus(entry, focus_mode=focus_mode)
        ]
        return sorted(filtered, key=self._timeline_entry_sort_key)

    def _timeline_entries_for_task_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        task_kind: str,
    ) -> list[dict[str, str]]:
        session = self._session
        task_id = str(snapshot.get("task_id") or "(unknown)")
        status = str(snapshot.get("status") or "unknown")
        description = str(snapshot.get("description") or "").strip()
        metadata = snapshot.get("metadata") or {}
        summary_bits = [f"task={task_id}", f"status={status}"]
        if task_kind == "scout":
            category = str(snapshot.get("category") or "(unknown)").strip()
            summary = f"category={category} " + " ".join(summary_bits)
            primary_action = self._focus_preserving_task_show_action(task_id)
            secondary_action = self._session.active_plan_scout_command_for_task(task_id) or "/plan scouts"
        else:
            phase = str(snapshot.get("phase") or "running").strip()
            plan_status = str(snapshot.get("plan_status") or "on-plan").strip()
            summary = f"phase={phase} plan_status={plan_status} " + " ".join(summary_bits)
            primary_action = self._focus_preserving_task_show_action(task_id)
            if snapshot.get("drift_status") or metadata.get("constraint_source"):
                secondary_action = f"/task drift {task_id}"
            else:
                secondary_action = f"/task advisor {task_id}"
        if description:
            summary += f" desc={description}"
        start_detail_bits: list[str] = []
        if task_kind == "execution":
            mode = str(snapshot.get("mode") or metadata.get("plan_execution_mode") or "").strip()
            if mode:
                start_detail_bits.append(f"mode: {mode}")
            if metadata.get("constraint_source"):
                start_detail_bits.append(f"constraint_source: {metadata['constraint_source']}")
            if metadata.get("drift_status"):
                start_detail_bits.append(f"drift_status: {metadata['drift_status']}")
        elif metadata.get("planner_kind"):
            start_detail_bits.append(f"planner_kind: {metadata['planner_kind']}")
        created_at = str(snapshot.get("created_at") or "")
        entries: list[dict[str, str]] = []
        if created_at:
            entries.append(
                {
                    "timestamp": created_at,
                    "kind": task_kind,
                    "section": "Scout Research" if task_kind == "scout" else "Execution Loop",
                    "summary": f"started {summary}",
                    "detail": "\n".join(start_detail_bits),
                    "task_id": task_id,
                    "primary_action": primary_action,
                    "secondary_action": secondary_action,
                }
            )
        detail_text = str(snapshot.get("output") or snapshot.get("error") or snapshot.get("detail") or "").strip()
        progress = str(snapshot.get("progress_summary") or "").strip()
        end_bits: list[str] = []
        if progress:
            end_bits.append(f"progress: {progress}")
        if detail_text:
            end_bits.extend(
                session._compact_multiline_text(
                    detail_text,
                    max_lines=6,
                    max_chars=800,
                ).splitlines()
            )
        end_timestamp = str(snapshot.get("ended_at") or snapshot.get("updated_at") or "")
        if end_timestamp and end_timestamp != created_at:
            entries.append(
                {
                    "timestamp": end_timestamp,
                    "kind": task_kind,
                    "section": "Scout Research" if task_kind == "scout" else "Execution Loop",
                    "summary": f"updated {summary}",
                    "detail": "\n".join(end_bits),
                    "task_id": task_id,
                    "primary_action": primary_action,
                    "secondary_action": secondary_action,
                }
            )
        elif end_bits and entries:
            existing = entries[0].get("detail", "")
            entries[0]["detail"] = "\n".join(bit for bit in [existing, *end_bits] if bit)
        return entries

    def _timeline_drift_primary_action(self, artifact) -> str:
        for snapshot in reversed(self._session._planning_artifact_execution_snapshots(artifact)):
            task_id = str(snapshot.get("task_id") or "").strip()
            if task_id and (snapshot.get("drift_status") or snapshot.get("constraint_source")):
                return f"/task drift {task_id}"
        return "/advisor status"

    def _timeline_entry_sort_key(self, entry: dict[str, str]) -> tuple[str, int, int, int]:
        section_order = {
            "Plan Setup": 0,
            "Scout Research": 1,
            "Execution Loop": 2,
            "Advisor & Drift": 3,
        }
        kind_order = {
            "plan": 0,
            "advisor": 1,
            "scout": 2,
            "execution": 3,
            "drift": 4,
        }
        summary = str(entry.get("summary") or "")
        event_order = 0
        if summary.startswith("updated "):
            event_order = 1
        return (
            str(entry.get("timestamp") or ""),
            section_order.get(str(entry.get("section") or "Timeline"), 99),
            kind_order.get(str(entry.get("kind") or ""), 99),
            event_order,
        )

    def _timeline_audit_summary(self, entries: list[dict[str, str]]) -> dict[str, str]:
        if not entries:
            return {
                "entry_count": "0",
                "task_count": "0",
                "session_span": "none",
                "session_duration": "0s",
                "last_updated": "none",
                "kinds": "none",
                "sections": "none",
                "latest_execution_status": "none",
                "latest_advisor_status": "none",
                "latest_drift_status": "none",
            }
        kind_counts: dict[str, int] = {}
        section_counts: dict[str, int] = {}
        task_ids: set[str] = set()
        latest_execution_status = "none"
        latest_advisor_status = "none"
        latest_drift_status = "none"
        for entry in entries:
            kind = str(entry.get("kind") or "unknown")
            section = str(entry.get("section") or "Timeline")
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            section_counts[section] = section_counts.get(section, 0) + 1
            task_id = str(entry.get("task_id") or "").strip()
            if task_id:
                task_ids.add(task_id)
            summary = str(entry.get("summary") or "")
            if kind == "execution":
                latest_execution_status = summary
            elif kind == "advisor":
                latest_advisor_status = summary
            elif kind == "drift":
                latest_drift_status = summary
        span_start, span_end = self._timeline_bounds(entries)
        return {
            "entry_count": str(len(entries)),
            "task_count": str(len(task_ids)),
            "session_span": self._format_timeline_span(span_start, span_end),
            "session_duration": self._format_timeline_duration(span_start, span_end),
            "last_updated": span_end or "none",
            "kinds": ", ".join(f"{key}={kind_counts[key]}" for key in sorted(kind_counts)),
            "sections": ", ".join(f"{key}={section_counts[key]}" for key in section_counts),
            "latest_execution_status": latest_execution_status,
            "latest_advisor_status": latest_advisor_status,
            "latest_drift_status": latest_drift_status,
        }

    def _timeline_section_summaries(self, entries: list[dict[str, str]]) -> dict[str, dict[str, str]]:
        summaries: dict[str, dict[str, str]] = {}
        for entry in entries:
            section = str(entry.get("section") or "Timeline")
            current = summaries.setdefault(
                section,
                {
                    "entry_count": "0",
                    "task_count": "0",
                    "span": "none",
                    "duration": "0s",
                    "last_updated": "none",
                    "kinds": "none",
                    "latest_status": "none",
                },
            )
            current["entry_count"] = str(int(current["entry_count"]) + 1)
            kinds = current.setdefault("_kind_counts", {})
            assert isinstance(kinds, dict)
            kind = str(entry.get("kind") or "unknown")
            kinds[kind] = int(kinds.get(kind, 0)) + 1
            current["kinds"] = ", ".join(f"{key}={kinds[key]}" for key in sorted(kinds))
            current["latest_status"] = str(entry.get("summary") or "none")
            task_ids = current.setdefault("_task_ids", set())
            assert isinstance(task_ids, set)
            task_id = str(entry.get("task_id") or "").strip()
            if task_id:
                task_ids.add(task_id)
                current["task_count"] = str(len(task_ids))
            timestamps = current.setdefault("_timestamps", [])
            assert isinstance(timestamps, list)
            timestamp = str(entry.get("timestamp") or "").strip()
            if timestamp:
                timestamps.append(timestamp)
                timestamps.sort()
                current["span"] = self._format_timeline_span(timestamps[0], timestamps[-1])
                current["duration"] = self._format_timeline_duration(timestamps[0], timestamps[-1])
                current["last_updated"] = timestamps[-1]
        for current in summaries.values():
            current.pop("_kind_counts", None)
            current.pop("_task_ids", None)
            current.pop("_timestamps", None)
        return summaries

    def _timeline_bounds(self, entries: list[dict[str, str]]) -> tuple[str | None, str | None]:
        timestamps = sorted(
            str(entry.get("timestamp") or "").strip()
            for entry in entries
            if str(entry.get("timestamp") or "").strip()
        )
        if not timestamps:
            return None, None
        return timestamps[0], timestamps[-1]

    def _format_timeline_span(self, start: str | None, end: str | None) -> str:
        if not start and not end:
            return "none"
        if not start:
            return end or "none"
        if not end or end == start:
            return start
        return f"{start} -> {end}"

    def _format_timeline_duration(self, start: str | None, end: str | None) -> str:
        if not start or not end:
            return "0s"
        try:
            start_dt = datetime.fromisoformat(start)
            end_dt = datetime.fromisoformat(end)
        except ValueError:
            return "0s"
        total_seconds = int(max((end_dt - start_dt).total_seconds(), 0))
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}h{minutes}m{seconds}s"
        if minutes:
            return f"{minutes}m{seconds}s"
        return f"{seconds}s"

    def _normalize_timeline_kind_filter(self, kind_filter: str) -> str:
        normalized = kind_filter.strip().lower()
        if normalized in {"plan", "scout", "execution", "advisor", "drift"}:
            return normalized
        return "all"

    def _normalize_timeline_delta_mode(self, delta_mode: str) -> str:
        normalized = delta_mode.strip().lower()
        if normalized in {"before-drift", "after-drift", "since-derived"}:
            return normalized
        return "none"

    def _normalize_timeline_phase_filter(self, phase_filter: str) -> str:
        normalized = phase_filter.strip().lower()
        if normalized in {"plan-setup", "scout-research", "execution-loop", "advisor-drift"}:
            return normalized
        return "none"

    def _normalize_timeline_focus_mode(self, focus_mode: str) -> str:
        normalized = focus_mode.strip().lower()
        if normalized in {"scout", "execution"} or normalized.startswith("task:"):
            return normalized
        return "none"

    def _normalize_timeline_compare_mode(self, compare_mode: str) -> str:
        normalized = compare_mode.strip().lower()
        if normalized in {"after-drift-vs-all", "execution-vs-scout", "active-vs-previous"}:
            return normalized
        return "none"

    def _timeline_entry_matches_filter(self, entry: dict[str, str], *, kind_filter: str) -> bool:
        if kind_filter == "all":
            return True
        return str(entry.get("kind") or "").strip().lower() == kind_filter

    def _timeline_entry_matches_delta(
        self,
        entry: dict[str, str],
        *,
        artifact,
        delta_mode: str,
    ) -> bool:
        if delta_mode == "none":
            return True
        timestamp = str(entry.get("timestamp") or "").strip()
        if not timestamp:
            return True
        drift_timestamp = self._session._latest_plan_drift_timestamp()
        if delta_mode == "after-drift":
            return not drift_timestamp or timestamp >= drift_timestamp
        if delta_mode == "before-drift":
            return not drift_timestamp or timestamp < drift_timestamp
        if delta_mode == "since-derived":
            if artifact.supersedes_artifact_id:
                return timestamp >= artifact.created_at
            return True
        return True

    def _timeline_entry_matches_phase(self, entry: dict[str, str], *, phase_filter: str) -> bool:
        if phase_filter == "none":
            return True
        section = (
            str(entry.get("section") or "Timeline")
            .strip()
            .lower()
            .replace(" & ", "-")
            .replace(" ", "-")
        )
        return section == phase_filter

    def _timeline_entry_matches_focus(self, entry: dict[str, str], *, focus_mode: str) -> bool:
        if focus_mode == "none":
            return True
        kind = str(entry.get("kind") or "").strip().lower()
        if focus_mode == "scout":
            return kind == "scout"
        if focus_mode == "execution":
            return kind == "execution"
        if focus_mode.startswith("task:"):
            target = focus_mode.split(":", 1)[1].strip()
            if not target:
                return True
            task_id = str(entry.get("task_id") or "").strip()
            return task_id == target or task_id.startswith(target)
        return True

    def _timeline_compare_items(
        self,
        artifact,
        *,
        kind_filter: str,
        delta_mode: str,
        phase_filter: str,
        focus_mode: str,
        compare_mode: str,
        current_entries: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        if compare_mode == "none":
            return []
        if compare_mode == "after-drift-vs-all":
            baseline = self._planning_artifact_timeline_entries(
                artifact,
                kind_filter=kind_filter,
                delta_mode="none",
                phase_filter=phase_filter,
                focus_mode=focus_mode,
            )
            return self._render_timeline_compare_summary(
                left_label="after-drift",
                left_entries=current_entries,
                right_label="all",
                right_entries=baseline,
                left_primary_action=self._timeline_entries_primary_action(
                    current_entries,
                    fallback=self._timeline_drift_primary_action(artifact),
                ),
                left_secondary_action="/plan timeline all delta=after-drift",
                right_primary_action=self._timeline_entries_primary_action(
                    baseline,
                    fallback=self._timeline_default_view_action(kind_filter),
                ),
                right_secondary_action="/plan execution",
                advisor_primary_action="/plan advisor",
                advisor_secondary_action="/advisor status",
                drift_primary_action=self._timeline_drift_primary_action(artifact),
                drift_secondary_action="/plan execution",
                artifact=artifact,
                current_entries=current_entries,
                phase_filter=phase_filter,
                left_artifact_id=artifact.artifact_id,
                right_artifact_id=artifact.artifact_id,
            )
        if compare_mode == "execution-vs-scout":
            execution_entries = self._planning_artifact_timeline_entries(
                artifact,
                kind_filter="execution",
                delta_mode=delta_mode,
                phase_filter=phase_filter,
                focus_mode="none" if focus_mode.startswith("task:") else focus_mode,
            )
            scout_entries = self._planning_artifact_timeline_entries(
                artifact,
                kind_filter="scout",
                delta_mode=delta_mode,
                phase_filter=phase_filter,
                focus_mode="none" if focus_mode.startswith("task:") else focus_mode,
            )
            return self._render_timeline_compare_summary(
                left_label="execution",
                left_entries=execution_entries,
                right_label="scout",
                right_entries=scout_entries,
                left_primary_action=self._timeline_entries_primary_action(
                    execution_entries,
                    fallback="/plan execution",
                ),
                left_secondary_action="/plan execution",
                right_primary_action=self._timeline_entries_primary_action(
                    scout_entries,
                    fallback="/plan scouts",
                ),
                right_secondary_action="/plan scouts",
                advisor_primary_action="/plan advisor",
                advisor_secondary_action="/advisor status",
                drift_primary_action=self._timeline_drift_primary_action(artifact),
                drift_secondary_action="/plan execution",
                artifact=artifact,
                current_entries=current_entries,
                phase_filter=phase_filter,
                left_artifact_id=artifact.artifact_id,
                right_artifact_id=artifact.artifact_id,
            )
        if compare_mode == "active-vs-previous":
            previous = self._previous_planning_artifact(artifact)
            if previous is None:
                return [
                    {
                        "label": "active-vs-previous",
                        "summary": "no previous lineage artifact",
                        "detail": "",
                        "primary_action": f"/plan show {artifact.artifact_id}",
                        "secondary_action": "/plan lineage",
                    }
                ]
            previous_entries = self._planning_artifact_timeline_entries(
                previous,
                kind_filter=kind_filter,
                delta_mode="none",
                phase_filter=phase_filter,
                focus_mode=focus_mode,
            )
            return self._render_timeline_compare_summary(
                left_label="active",
                left_entries=current_entries,
                right_label="previous",
                right_entries=previous_entries,
                left_primary_action=self._timeline_entries_primary_action(
                    current_entries,
                    fallback=self._timeline_default_view_action(kind_filter),
                ),
                left_secondary_action=f"/plan show {artifact.artifact_id}",
                right_primary_action=self._timeline_entries_primary_action(
                    previous_entries,
                    fallback=f"/plan show {previous.artifact_id}",
                ),
                right_secondary_action=f"/plan show {previous.artifact_id}",
                advisor_primary_action="/plan advisor",
                advisor_secondary_action="/advisor status",
                drift_primary_action=self._timeline_drift_primary_action(artifact),
                drift_secondary_action="/plan execution",
                artifact=artifact,
                current_entries=current_entries,
                phase_filter=phase_filter,
                left_artifact_id=artifact.artifact_id,
                right_artifact_id=previous.artifact_id,
            )
        return []

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
        artifact,
        current_entries: list[dict[str, str]],
        phase_filter: str,
        left_artifact_id: str | None,
        right_artifact_id: str | None,
    ) -> list[dict[str, str]]:
        left_summary = self._timeline_audit_summary(left_entries)
        right_summary = self._timeline_audit_summary(right_entries)
        items = [
            {
                "label": left_label,
                "summary": (
                    f"entries={left_summary['entry_count']} "
                    f"tasks={left_summary['task_count']} "
                    f"latest_execution={left_summary['latest_execution_status']}"
                ),
                "detail": (
                    f"latest_advisor={left_summary['latest_advisor_status']}\n"
                    f"latest_drift={left_summary['latest_drift_status']}"
                ),
                "primary_action": left_primary_action,
                "secondary_action": left_secondary_action,
                "replay_entries": left_entries,
                "replay_artifact_id": left_artifact_id or artifact.artifact_id,
            },
            {
                "label": right_label,
                "summary": (
                    f"entries={right_summary['entry_count']} "
                    f"tasks={right_summary['task_count']} "
                    f"latest_execution={right_summary['latest_execution_status']}"
                ),
                "detail": (
                    f"latest_advisor={right_summary['latest_advisor_status']}\n"
                    f"latest_drift={right_summary['latest_drift_status']}"
                ),
                "primary_action": right_primary_action,
                "secondary_action": right_secondary_action,
                "replay_entries": right_entries,
                "replay_artifact_id": right_artifact_id or artifact.artifact_id,
            },
            {
                "label": "delta",
                "summary": (
                    f"entry_count={int(left_summary['entry_count']) - int(right_summary['entry_count'])} "
                    f"task_count={int(left_summary['task_count']) - int(right_summary['task_count'])}"
                ),
                "detail": (
                    f"{left_label}.latest_execution={left_summary['latest_execution_status']}\n"
                    f"{right_label}.latest_execution={right_summary['latest_execution_status']}"
                ),
                "primary_action": left_primary_action,
                "secondary_action": right_primary_action,
                "replay_entries": left_entries,
                "replay_artifact_id": left_artifact_id or artifact.artifact_id,
            },
            {
                "label": "latest_advisor",
                "summary": (
                    f"{left_label}={left_summary['latest_advisor_status']} | "
                    f"{right_label}={right_summary['latest_advisor_status']}"
                ),
                "detail": (
                    f"{left_label}.latest_advisor={left_summary['latest_advisor_status']}\n"
                    f"{right_label}.latest_advisor={right_summary['latest_advisor_status']}"
                ),
                "primary_action": advisor_primary_action,
                "secondary_action": advisor_secondary_action,
                "replay_entries": [
                    item for item in left_entries if str(item.get("kind") or "") == "advisor"
                ],
                "replay_artifact_id": left_artifact_id or artifact.artifact_id,
            },
            {
                "label": "latest_drift",
                "summary": (
                    f"{left_label}={left_summary['latest_drift_status']} | "
                    f"{right_label}={right_summary['latest_drift_status']}"
                ),
                "detail": (
                    f"{left_label}.latest_drift={left_summary['latest_drift_status']}\n"
                    f"{right_label}.latest_drift={right_summary['latest_drift_status']}"
                ),
                "primary_action": drift_primary_action,
                "secondary_action": drift_secondary_action,
                "replay_entries": [
                    item for item in left_entries if str(item.get("kind") or "") == "drift"
                ],
                "replay_artifact_id": left_artifact_id or artifact.artifact_id,
            },
        ]
        if phase_filter != "none":
            items.extend(
                self._timeline_phase_local_compare_items(
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
            )
        else:
            items.extend(
                self._timeline_section_compare_items(
                    left_label=left_label,
                    left_entries=left_entries,
                    right_label=right_label,
                    right_entries=right_entries,
                    left_artifact_id=left_artifact_id,
                    right_artifact_id=right_artifact_id,
                )
            )
        return items

    def _timeline_phase_local_compare_items(
        self,
        *,
        artifact,
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
        left_phase_action = self._timeline_phase_timeline_action(
            phase_filter,
            artifact_id=left_artifact_id,
        )
        right_phase_action = self._timeline_phase_timeline_action(
            phase_filter,
            artifact_id=right_artifact_id,
        )
        return [
            {
                "label": "local:entries",
                "summary": (
                    f"{left_label}={left_summary['entry_count']} | "
                    f"{right_label}={right_summary['entry_count']} "
                    f"delta={int(left_summary['entry_count']) - int(right_summary['entry_count'])}"
                ),
                "detail": (
                    f"{left_label}.last_updated={left_summary['last_updated']}\n"
                    f"{right_label}.last_updated={right_summary['last_updated']}"
                ),
                "primary_action": left_phase_action,
                "secondary_action": right_phase_action,
                "replay_entries": current_entries,
            },
            {
                "label": "local:tasks",
                "summary": (
                    f"{left_label}={left_summary['task_count']} | "
                    f"{right_label}={right_summary['task_count']} "
                    f"delta={int(left_summary['task_count']) - int(right_summary['task_count'])}"
                ),
                "detail": (
                    f"{left_label}.kinds={left_summary['kinds']}\n"
                    f"{right_label}.kinds={right_summary['kinds']}"
                ),
                "primary_action": left_phase_action,
                "secondary_action": right_phase_action,
                "replay_entries": current_entries,
            },
            {
                "label": "local:execution",
                "summary": (
                    f"{left_label}={left_summary['latest_execution_status']} | "
                    f"{right_label}={right_summary['latest_execution_status']}"
                ),
                "detail": (
                    f"{left_label}.execution={left_summary['latest_execution_status']}\n"
                    f"{right_label}.execution={right_summary['latest_execution_status']}"
                ),
                "primary_action": left_primary_action,
                "secondary_action": right_primary_action,
                "replay_entries": [
                    item for item in current_entries if str(item.get("kind") or "") == "execution"
                ],
            },
            {
                "label": "local:advisor",
                "summary": (
                    f"{left_label}={left_summary['latest_advisor_status']} | "
                    f"{right_label}={right_summary['latest_advisor_status']}"
                ),
                "detail": (
                    f"{left_label}.advisor={left_summary['latest_advisor_status']}\n"
                    f"{right_label}.advisor={right_summary['latest_advisor_status']}"
                ),
                "primary_action": advisor_primary_action,
                "secondary_action": advisor_secondary_action,
                "replay_entries": [
                    item for item in current_entries if str(item.get("kind") or "") == "advisor"
                ],
            },
            {
                "label": "local:drift",
                "summary": (
                    f"{left_label}={left_summary['latest_drift_status']} | "
                    f"{right_label}={right_summary['latest_drift_status']}"
                ),
                "detail": (
                    f"{left_label}.drift={left_summary['latest_drift_status']}\n"
                    f"{right_label}.drift={right_summary['latest_drift_status']}"
                ),
                "primary_action": drift_primary_action,
                "secondary_action": drift_secondary_action,
                "replay_entries": [
                    item for item in current_entries if str(item.get("kind") or "") == "drift"
                ],
            },
        ] + self._timeline_phase_delta_compare_items(
            artifact=artifact,
            current_entries=current_entries,
            phase_filter=phase_filter,
            artifact_id=left_artifact_id,
        )

    def _timeline_phase_delta_compare_items(
        self,
        *,
        artifact,
        current_entries: list[dict[str, str]],
        phase_filter: str,
        artifact_id: str | None,
    ) -> list[dict[str, str]]:
        if phase_filter != "execution-loop":
            return []
        before_entries = self._timeline_entries_with_delta(
            current_entries,
            artifact=artifact,
            delta_mode="before-drift",
        )
        after_entries = self._timeline_entries_with_delta(
            current_entries,
            artifact=artifact,
            delta_mode="after-drift",
        )
        if not before_entries and not after_entries:
            return []
        before_summary = self._timeline_audit_summary(before_entries)
        after_summary = self._timeline_audit_summary(after_entries)
        before_action = self._timeline_phase_timeline_action(
            phase_filter,
            artifact_id=artifact_id,
            delta_mode="before-drift",
        )
        after_action = self._timeline_phase_timeline_action(
            phase_filter,
            artifact_id=artifact_id,
            delta_mode="after-drift",
        )
        return [
            {
                "label": "local:before-drift",
                "summary": (
                    f"entries={before_summary['entry_count']} "
                    f"tasks={before_summary['task_count']} "
                    f"latest_execution={before_summary['latest_execution_status']}"
                ),
                "detail": (
                    f"last_updated={before_summary['last_updated']}\n"
                    f"latest_drift={before_summary['latest_drift_status']}"
                ),
                "primary_action": before_action,
                "secondary_action": after_action,
                "replay_entries": before_entries,
            },
            {
                "label": "local:after-drift",
                "summary": (
                    f"entries={after_summary['entry_count']} "
                    f"tasks={after_summary['task_count']} "
                    f"latest_execution={after_summary['latest_execution_status']}"
                ),
                "detail": (
                    f"last_updated={after_summary['last_updated']}\n"
                    f"latest_drift={after_summary['latest_drift_status']}"
                ),
                "primary_action": after_action,
                "secondary_action": before_action,
                "replay_entries": after_entries,
            },
            {
                "label": "local:execution-change",
                "summary": (
                    f"before={before_summary['latest_execution_status']} | "
                    f"after={after_summary['latest_execution_status']}"
                ),
                "detail": (
                    f"before.tasks={before_summary['task_count']}\n"
                    f"after.tasks={after_summary['task_count']}"
                ),
                "primary_action": after_action,
                "secondary_action": before_action,
                "replay_entries": after_entries or before_entries,
            },
        ]

    def _timeline_entries_with_delta(
        self,
        entries: list[dict[str, str]],
        *,
        artifact,
        delta_mode: str,
    ) -> list[dict[str, str]]:
        return [
            entry
            for entry in entries
            if self._timeline_entry_matches_delta(entry, artifact=artifact, delta_mode=delta_mode)
        ]

    def _timeline_phase_local_audit_summary(
        self,
        *,
        artifact,
        entries: list[dict[str, str]],
        phase_filter: str,
        selected_task_index: int = 0,
    ) -> list[str]:
        session = self._session
        if phase_filter != "execution-loop":
            return []
        before_entries = self._timeline_entries_with_delta(
            entries,
            artifact=artifact,
            delta_mode="before-drift",
        )
        after_entries = self._timeline_entries_with_delta(
            entries,
            artifact=artifact,
            delta_mode="after-drift",
        )
        if not before_entries and not after_entries:
            return []
        before_summary = self._timeline_audit_summary(before_entries)
        after_summary = self._timeline_audit_summary(after_entries)
        entry_delta = int(after_summary["entry_count"]) - int(before_summary["entry_count"])
        task_delta = int(after_summary["task_count"]) - int(before_summary["task_count"])
        execution_task_ids: list[str] = []
        recent_drift_linked_task_id: str | None = None
        recent_drift_linked_task_action: str | None = None
        drift_actions_by_task_id: dict[str, str] = {}
        for entry in entries:
            if str(entry.get("kind") or "") != "execution":
                continue
            task_id = str(entry.get("task_id") or "").strip()
            if task_id and task_id not in execution_task_ids:
                execution_task_ids.append(task_id)
            secondary_action = str(entry.get("secondary_action") or "").strip()
            if secondary_action.startswith("/task drift "):
                if task_id:
                    drift_actions_by_task_id[task_id] = secondary_action
                recent_drift_linked_task_id = task_id or None
                recent_drift_linked_task_action = secondary_action
        if execution_task_ids:
            normalized_index = max(0, min(selected_task_index, len(execution_task_ids) - 1))
            selected_phase_local_task_id = execution_task_ids[normalized_index]
            selected_phase_local_task_position = f"{normalized_index + 1}/{len(execution_task_ids)}"
        else:
            selected_phase_local_task_id = None
            selected_phase_local_task_position = "0/0"
        selected_phase_local_task_action = (
            self._focus_preserving_task_show_action(selected_phase_local_task_id)
            if selected_phase_local_task_id
            else "none"
        )
        if selected_phase_local_task_id and selected_phase_local_task_id in drift_actions_by_task_id:
            recent_drift_linked_task_id = selected_phase_local_task_id
            recent_drift_linked_task_action = drift_actions_by_task_id[selected_phase_local_task_id]
        return [
            "phase: execution-loop",
            (
                "before_drift: "
                f"entries={before_summary['entry_count']} "
                f"tasks={before_summary['task_count']} "
                f"latest_execution={before_summary['latest_execution_status']} "
                f"last_updated={before_summary['last_updated']}"
            ),
            (
                "after_drift: "
                f"entries={after_summary['entry_count']} "
                f"tasks={after_summary['task_count']} "
                f"latest_execution={after_summary['latest_execution_status']} "
                f"last_updated={after_summary['last_updated']}"
            ),
            (
                "change_summary: "
                f"entry_delta={entry_delta} "
                f"task_delta={task_delta} "
                f"execution_change={before_summary['latest_execution_status']} -> {after_summary['latest_execution_status']}"
            ),
            "execution_task_ids: " + (", ".join(execution_task_ids) if execution_task_ids else "none"),
            "execution_task_actions: "
            + (
                " | ".join(
                    f"{task_id}={self._focus_preserving_task_show_action(task_id)}"
                    for task_id in execution_task_ids
                )
                if execution_task_ids
                else "none"
            ),
            "selected_phase_local_task_id: " + (selected_phase_local_task_id or "none"),
            "selected_phase_local_task_position: " + selected_phase_local_task_position,
            "selected_phase_local_task_action: " + selected_phase_local_task_action,
            "recent_drift_linked_task: " + (recent_drift_linked_task_id or "none"),
            "recent_drift_linked_task_action: " + (recent_drift_linked_task_action or "none"),
        ]

    def _normalize_replay_source_mode(self, source_mode: str) -> str:
        normalized = source_mode.strip().lower()
        if normalized in {"auto", "timeline-entry", "compare-item", "phase-slice", "phase-local-summary"}:
            return normalized
        return "auto"

    def _resolve_replay_target(
        self,
        *,
        artifact,
        entries: list[dict[str, str]],
        compare_items: list[dict[str, Any]],
        selected_compare_index: int,
        phase_filter: str,
        selected_phase_local_task_index: int,
        selected_index: int,
        latest: bool,
        source_mode: str,
    ) -> tuple[str, Any, list[dict[str, str]], int, dict[str, str] | None]:
        if not entries:
            return source_mode, artifact, entries, 0, None
        replay_artifact = artifact
        replay_entries = entries
        if latest:
            replay_index = self._preferred_replay_latest_index(
                replay_entries,
                default=len(replay_entries) - 1,
            )
            return (
                "timeline-entry" if source_mode == "auto" else source_mode,
                replay_artifact,
                replay_entries,
                replay_index,
                replay_entries[replay_index],
            )
        if source_mode == "auto":
            if compare_items:
                source_mode = "compare-item"
            elif phase_filter != "none":
                source_mode = "phase-slice"
            else:
                source_mode = "timeline-entry"
        replay_index = max(0, min(selected_index, len(replay_entries) - 1))
        if source_mode == "compare-item" and compare_items:
            compare_item = compare_items[max(0, min(selected_compare_index, len(compare_items) - 1))]
            compare_replay_entries = compare_item.get("replay_entries") or []
            replay_artifact_id = str(compare_item.get("replay_artifact_id") or "").strip()
            resolved_compare_artifact = self._resolve_timeline_artifact(replay_artifact_id)
            if resolved_compare_artifact is not None:
                replay_artifact = resolved_compare_artifact
            if compare_replay_entries:
                replay_entries = compare_replay_entries
                replay_index = self._preferred_replay_latest_index(
                    replay_entries,
                    default=max(0, min(selected_index, len(replay_entries) - 1)),
                )
        elif source_mode == "phase-local-summary":
            task_id = self._replay_phase_local_task_id(artifact, selected_phase_local_task_index)
            if task_id:
                replay_index = self._latest_replay_entry_index_for_task(
                    replay_entries,
                    task_id,
                    default=replay_index,
                )
        elif source_mode == "phase-slice":
            replay_index = len(replay_entries) - 1
        return source_mode, replay_artifact, replay_entries, replay_index, replay_entries[replay_index]

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
        parts = [kind_filter]
        if delta_mode != "none":
            parts.append(f"delta={delta_mode}")
        if phase_filter != "none":
            parts.append(f"phase={phase_filter}")
        if focus_mode != "none":
            parts.append(f"focus={focus_mode}")
        if compare_mode != "none":
            parts.append(f"compare={compare_mode}")
        if artifact_id:
            parts.append(f"artifact={artifact_id}")
        return " ".join(parts)

    def _lineage_replay_compare_actions(
        self,
        *,
        artifact,
        kind_filter: str,
        delta_mode: str,
        phase_filter: str,
        focus_mode: str,
    ) -> list[str]:
        previous = self._previous_planning_artifact(artifact)
        if previous is None:
            return []
        current_suffix = self._timeline_command_suffix(
            kind_filter=kind_filter,
            delta_mode=delta_mode,
            phase_filter=phase_filter,
            focus_mode=focus_mode,
            compare_mode="none",
            artifact_id=artifact.artifact_id,
        )
        previous_suffix = self._timeline_command_suffix(
            kind_filter=kind_filter,
            delta_mode=delta_mode,
            phase_filter=phase_filter,
            focus_mode=focus_mode,
            compare_mode="none",
            artifact_id=previous.artifact_id,
        )
        return [
            f"- compare_current_replay: /plan replay latest {current_suffix}",
            f"- compare_previous_replay: /plan replay latest {previous_suffix}",
            f"- compare_current_timeline: /plan timeline {current_suffix}",
            f"- compare_previous_timeline: /plan timeline {previous_suffix}",
        ]

    def _render_lineage_replay_compare(
        self,
        *,
        artifact,
        replay_artifact,
        kind_filter: str,
        delta_mode: str,
        phase_filter: str,
        focus_mode: str,
        compare_mode: str,
    ) -> list[str]:
        if compare_mode != "active-vs-previous":
            return []
        previous = self._previous_planning_artifact(artifact)
        if previous is None:
            return ["- previous_artifact: none"]
        current_entries = self._planning_artifact_timeline_entries(
            artifact,
            kind_filter=kind_filter,
            delta_mode=delta_mode,
            phase_filter=phase_filter,
            focus_mode=focus_mode,
        )
        previous_entries = self._planning_artifact_timeline_entries(
            previous,
            kind_filter=kind_filter,
            delta_mode=delta_mode,
            phase_filter=phase_filter,
            focus_mode=focus_mode,
        )
        current_summary = self._timeline_audit_summary(current_entries)
        previous_summary = self._timeline_audit_summary(previous_entries)
        current_scout_tasks = self._timeline_entry_task_ids_for_kind(current_entries, "scout")
        previous_scout_tasks = self._timeline_entry_task_ids_for_kind(previous_entries, "scout")
        current_execution_tasks = self._timeline_entry_task_ids_for_kind(current_entries, "execution")
        previous_execution_tasks = self._timeline_entry_task_ids_for_kind(previous_entries, "execution")
        selected_side = "previous" if replay_artifact.artifact_id == previous.artifact_id else "active"
        lines = [
            f"- current_artifact: {artifact.artifact_id} goal={artifact.goal} lineage={self._planning_artifact_lineage_position(artifact)}",
            f"- previous_artifact: {previous.artifact_id} goal={previous.goal} lineage={self._planning_artifact_lineage_position(previous)}",
            f"- selected_side: {selected_side}",
            (
                f"- entry_delta: current={current_summary['entry_count']} "
                f"previous={previous_summary['entry_count']} "
                f"delta={int(current_summary['entry_count']) - int(previous_summary['entry_count'])}"
            ),
            (
                f"- task_delta: current={current_summary['task_count']} "
                f"previous={previous_summary['task_count']} "
                f"delta={int(current_summary['task_count']) - int(previous_summary['task_count'])}"
            ),
            (
                f"- latest_execution_status: current={current_summary['latest_execution_status']} "
                f"previous={previous_summary['latest_execution_status']}"
            ),
            (
                f"- latest_advisor_status: current={current_summary['latest_advisor_status']} "
                f"previous={previous_summary['latest_advisor_status']}"
            ),
            (
                f"- latest_drift_status: current={current_summary['latest_drift_status']} "
                f"previous={previous_summary['latest_drift_status']}"
            ),
            f"- added_scout_tasks: {', '.join(sorted(current_scout_tasks - previous_scout_tasks)) or 'none'}",
            f"- removed_scout_tasks: {', '.join(sorted(previous_scout_tasks - current_scout_tasks)) or 'none'}",
            f"- added_execution_tasks: {', '.join(sorted(current_execution_tasks - previous_execution_tasks)) or 'none'}",
            f"- removed_execution_tasks: {', '.join(sorted(previous_execution_tasks - current_execution_tasks)) or 'none'}",
        ]
        lines.extend(self._render_lineage_replay_entry_deltas(current_entries, previous_entries))
        lines.extend(
            self._lineage_replay_compare_actions(
                artifact=artifact,
                kind_filter=kind_filter,
                delta_mode=delta_mode,
                phase_filter=phase_filter,
                focus_mode=focus_mode,
            )
        )
        return lines

    def _timeline_entry_task_ids_for_kind(
        self,
        entries: list[dict[str, str]],
        kind: str,
    ) -> set[str]:
        return {
            str(entry.get("task_id") or "").strip()
            for entry in entries
            if str(entry.get("kind") or "").strip() == kind
            and str(entry.get("task_id") or "").strip()
        }

    def _timeline_entry_identity(self, entry: dict[str, str]) -> tuple[str, str, str, str]:
        return (
            str(entry.get("timestamp") or ""),
            str(entry.get("kind") or ""),
            str(entry.get("task_id") or ""),
            str(entry.get("summary") or ""),
        )

    def _render_replay_linked_blocks(
        self,
        artifact,
        entry: dict[str, str],
    ) -> list[str]:
        session = self._session
        kind = str(entry.get("kind") or "").strip()
        task_id = str(entry.get("task_id") or "").strip()
        lines: list[str] = []
        if kind == "plan":
            lines.append("- plan_summary:")
            lines.extend(
                f"  {line}"
                for line in session._compact_multiline_text(
                    artifact.summary,
                    max_lines=12,
                    max_chars=1600,
                ).splitlines()
            )
            lines.append(f"- lineage_context: {self._planning_artifact_lineage_position(artifact)}")
            lines.append(
                "- derive_relation: "
                + str(artifact.supersedes_artifact_id or "none")
                + " -> "
                + str(artifact.superseded_by_artifact_id or "none")
            )
            return lines
        task = session.resolve_task(task_id) if task_id else None
        if kind in {"scout", "execution"} and task is not None:
            lines.append("- task:")
            lines.append(f"  task_id: {task.id}")
            lines.append(f"  kind: {task.kind}")
            lines.append(f"  status: {task.status}")
            if task.progress_summary:
                lines.append(f"  progress_summary: {task.progress_summary}")
            if task.output:
                lines.append("  output:")
                lines.extend(
                    f"    {line}"
                    for line in session._compact_multiline_text(
                        task.output,
                        max_lines=10 if kind == "scout" else 12,
                        max_chars=1600,
                    ).splitlines()
                )
            if task.error:
                lines.append("  error:")
                lines.extend(
                    f"    {line}"
                    for line in session._compact_multiline_text(
                        task.error,
                        max_lines=10,
                        max_chars=1600,
                    ).splitlines()
                )
        if kind == "execution" and task is not None:
            execution_lines = session._render_task_detail_execution_context(task)
            if execution_lines:
                lines.append("- execution_context:")
                lines.extend(f"  {line}" for line in execution_lines)
        elif kind == "scout" and task is not None:
            metadata = task.metadata or {}
            lines.append("- scout_context:")
            if metadata.get("scout_category"):
                lines.append(f"  scout_category: {metadata['scout_category']}")
            lines.append(f"  task_summary: {task.description}")
        elif kind == "advisor":
            lines.append("- advisor_context:")
            if session.state.advisor_last_result is not None:
                lines.append(f"  checkpoint: {session.state.advisor_last_result.checkpoint}")
                lines.append(f"  result: {session.state.advisor_last_result.status}")
                if session.state.advisor_last_result.reason:
                    lines.append(f"  reason: {session.state.advisor_last_result.reason}")
                if session.state.advisor_last_result.risk_flags:
                    lines.append("  risk_flags: " + ", ".join(session.state.advisor_last_result.risk_flags))
        elif kind == "drift":
            lines.append("- drift_context:")
            if session.state.last_plan_drift_status:
                lines.append(f"  drift_status: {session.state.last_plan_drift_status}")
            if session.state.last_plan_drift_reason:
                lines.append(f"  drift_reason: {session.state.last_plan_drift_reason}")
            if session.state.last_plan_drift_context:
                lines.append("  analysis:")
                lines.extend(
                    f"    {line}"
                    for line in session._compact_multiline_text(
                        session.state.last_plan_drift_context,
                        max_lines=10,
                        max_chars=1600,
                    ).splitlines()
                )
        return lines

    def _timeline_entries_primary_action(
        self,
        entries: list[dict[str, str]],
        *,
        fallback: str,
    ) -> str:
        for entry in reversed(entries):
            action = str(entry.get("primary_action") or "").strip()
            if action:
                return action
        return fallback

    def _timeline_default_view_action(self, kind_filter: str) -> str:
        if kind_filter == "scout":
            return "/plan scouts"
        if kind_filter == "advisor":
            return "/plan advisor"
        if kind_filter == "drift":
            return "/advisor status"
        return "/plan execution"

    def _resolve_timeline_artifact(self, artifact_id: str | None):
        raw = (artifact_id or "").strip()
        if not raw or raw == "active":
            return self.active_planning_artifact()
        if raw == "previous":
            active = self.active_planning_artifact()
            if active is None:
                return None
            return self._previous_planning_artifact(active)
        return self.resolve_planning_artifact(raw)

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
        left_sections = self._timeline_section_summaries(left_entries)
        right_sections = self._timeline_section_summaries(right_entries)
        ordered_sections = [
            "Plan Setup",
            "Scout Research",
            "Execution Loop",
            "Advisor & Drift",
        ]
        items: list[dict[str, str]] = []
        for section in ordered_sections:
            if section not in left_sections and section not in right_sections:
                continue
            left_section_entries = self._timeline_entries_for_section(left_entries, section)
            right_section_entries = self._timeline_entries_for_section(right_entries, section)
            empty_section_summary = {
                "entry_count": "0",
                "task_count": "0",
                "span": "none",
                "duration": "0s",
                "last_updated": "none",
                "kinds": "none",
                "latest_status": "none",
            }
            left_summary = left_sections.get(section, empty_section_summary)
            right_summary = right_sections.get(section, empty_section_summary)
            items.append(
                {
                    "label": f"phase:{section}",
                    "summary": (
                        f"{left_label}.entries={left_summary['entry_count']} "
                        f"{right_label}.entries={right_summary['entry_count']} "
                        f"delta={int(left_summary['entry_count']) - int(right_summary['entry_count'])}"
                    ),
                    "detail": (
                        f"{left_label}.tasks={left_summary['task_count']} latest={left_summary['latest_status']}\n"
                        f"{right_label}.tasks={right_summary['task_count']} latest={right_summary['latest_status']}"
                    ),
                    "primary_action": self._timeline_section_timeline_action(
                        section,
                        artifact_id=left_artifact_id,
                    ),
                    "secondary_action": self._timeline_section_timeline_action(
                        section,
                        artifact_id=right_artifact_id,
                    ),
                    "replay_entries": left_section_entries,
                }
            )
        return items

    def _timeline_entries_for_section(
        self,
        entries: list[dict[str, str]],
        section: str,
    ) -> list[dict[str, str]]:
        return [entry for entry in entries if str(entry.get("section") or "Timeline") == section]

    def _timeline_section_default_action(self, section: str) -> str:
        if section == "Plan Setup":
            return "/plan show latest"
        if section == "Scout Research":
            return "/plan scouts"
        if section == "Execution Loop":
            return "/plan execution"
        if section == "Advisor & Drift":
            return "/plan advisor"
        return "/plan timeline all"

    def _timeline_section_timeline_action(self, section: str, *, artifact_id: str | None) -> str:
        phase_map = {
            "Plan Setup": "plan-setup",
            "Scout Research": "scout-research",
            "Execution Loop": "execution-loop",
            "Advisor & Drift": "advisor-drift",
        }
        phase = phase_map.get(section, "none")
        return self._timeline_phase_timeline_action(phase, artifact_id=artifact_id)

    def _timeline_phase_timeline_action(
        self,
        phase: str,
        *,
        artifact_id: str | None,
        delta_mode: str | None = None,
    ) -> str:
        parts = ["/plan", "timeline", "all", f"phase={phase}"]
        if delta_mode and delta_mode != "none":
            parts.append(f"delta={delta_mode}")
        if artifact_id:
            parts.append(f"artifact={artifact_id}")
        return " ".join(parts)

    def _previous_planning_artifact(self, artifact):
        if not artifact.supersedes_artifact_id:
            return None
        return self.resolve_planning_artifact(artifact.supersedes_artifact_id)

    def _render_lineage_replay_entry_deltas(
        self,
        current_entries: list[dict[str, str]],
        previous_entries: list[dict[str, str]],
    ) -> list[str]:
        current_by_identity = {
            self._timeline_entry_identity(entry): entry for entry in current_entries
        }
        previous_by_identity = {
            self._timeline_entry_identity(entry): entry for entry in previous_entries
        }
        lines: list[str] = []
        for kind in ("scout", "execution", "advisor", "drift"):
            added = [
                current_by_identity[key]
                for key in current_by_identity.keys() - previous_by_identity.keys()
                if str(current_by_identity[key].get("kind") or "") == kind
            ]
            removed = [
                previous_by_identity[key]
                for key in previous_by_identity.keys() - current_by_identity.keys()
                if str(previous_by_identity[key].get("kind") or "") == kind
            ]
            if not added and not removed:
                continue
            lines.append(f"- entry_delta_{kind}:")
            lines.extend(self._render_lineage_replay_delta_block("added", added))
            lines.extend(self._render_lineage_replay_delta_block("removed", removed))
        lines.extend(self._render_lineage_replay_phase_deltas(current_entries, previous_entries))
        return lines

    def _render_lineage_replay_phase_deltas(
        self,
        current_entries: list[dict[str, str]],
        previous_entries: list[dict[str, str]],
    ) -> list[str]:
        current_by_identity = {
            self._timeline_entry_identity(entry): entry for entry in current_entries
        }
        previous_by_identity = {
            self._timeline_entry_identity(entry): entry for entry in previous_entries
        }
        ordered_sections = [
            "Plan Setup",
            "Scout Research",
            "Execution Loop",
            "Advisor & Drift",
        ]
        lines: list[str] = []
        for section in ordered_sections:
            added = [
                current_by_identity[key]
                for key in current_by_identity.keys() - previous_by_identity.keys()
                if str(current_by_identity[key].get("section") or "Timeline") == section
            ]
            removed = [
                previous_by_identity[key]
                for key in previous_by_identity.keys() - current_by_identity.keys()
                if str(previous_by_identity[key].get("section") or "Timeline") == section
            ]
            if not added and not removed:
                continue
            lines.append(
                f"- phase_entry_delta:{section}: added={len(added)} removed={len(removed)}"
            )
            lines.extend(self._render_lineage_replay_delta_block("added", added))
            lines.extend(self._render_lineage_replay_delta_block("removed", removed))
        return lines

    def _render_lineage_replay_delta_block(
        self,
        label: str,
        entries: list[dict[str, str]],
    ) -> list[str]:
        if not entries:
            return [f"  {label}: none"]
        lines = [f"  {label}:"]
        sorted_entries = sorted(entries, key=self._timeline_entry_sort_key)
        for entry in sorted_entries:
            summary = str(entry.get("summary") or "").strip() or "(no summary)"
            timestamp = str(entry.get("timestamp") or "").strip() or "unknown"
            task_id = str(entry.get("task_id") or "").strip()
            primary_action = str(entry.get("primary_action") or "").strip() or "none"
            secondary_action = str(entry.get("secondary_action") or "").strip() or "none"
            bits = [f"{timestamp}"]
            if task_id:
                bits.append(f"task={task_id}")
            bits.append(summary)
            bits.append(f"actions={primary_action} | {secondary_action}")
            lines.append("    - " + " :: ".join(bits))
        return lines

    def _preferred_replay_latest_index(
        self,
        entries: list[dict[str, str]],
        *,
        default: int,
    ) -> int:
        for index in range(len(entries) - 1, -1, -1):
            if str(entries[index].get("task_id") or "").strip():
                return index
        for index in range(len(entries) - 1, -1, -1):
            if str(entries[index].get("kind") or "") != "plan":
                return index
        return default

    def _latest_replay_entry_index(
        self,
        entries: list[dict[str, str]],
        replay_entries: list[dict[str, Any]],
        *,
        default: int,
    ) -> int:
        replay_keys = {
            (
                str(item.get("timestamp") or ""),
                str(item.get("kind") or ""),
                str(item.get("task_id") or ""),
                str(item.get("summary") or ""),
            )
            for item in replay_entries
        }
        for index in range(len(entries) - 1, -1, -1):
            key = (
                str(entries[index].get("timestamp") or ""),
                str(entries[index].get("kind") or ""),
                str(entries[index].get("task_id") or ""),
                str(entries[index].get("summary") or ""),
            )
            if key in replay_keys:
                return index
        return default

    def _latest_replay_entry_index_for_task(
        self,
        entries: list[dict[str, str]],
        task_id: str,
        *,
        default: int,
    ) -> int:
        for index in range(len(entries) - 1, -1, -1):
            if str(entries[index].get("task_id") or "").strip() == task_id:
                return index
        return default

    def _replay_phase_local_task_id(
        self,
        artifact,
        selected_task_index: int,
    ) -> str | None:
        task_ids = self._phase_local_execution_task_ids(artifact)
        if not task_ids:
            return None
        normalized_index = max(0, min(selected_task_index, len(task_ids) - 1))
        return task_ids[normalized_index]

    def _scout_tasks_for_artifact(self, artifact) -> list[Any]:
        session = self._session
        artifact_task_ids = set(artifact.task_ids)
        tasks = [
            task
            for task in session.task_manager.list()
            if (
                task.metadata.get("task_role") == "scout"
                or task.kind == "ultraplan_scout"
                or task.metadata.get("scout_category") is not None
            )
            and (
                task.id in artifact_task_ids
                or task.metadata.get("active_plan_id") == artifact.artifact_id
            )
        ]
        tasks.sort(key=lambda task: task.created_at)
        return tasks

    def _execution_tasks_for_artifact(self, artifact) -> list[Any]:
        session = self._session
        tasks = [
            task
            for task in session.task_manager.list()
            if task.metadata.get("task_role") == "execution"
            and task.metadata.get("active_plan_id") == artifact.artifact_id
        ]
        tasks.sort(key=lambda task: task.created_at)
        return tasks

    def _phase_local_execution_task_ids(self, artifact) -> list[str]:
        entries = self._planning_artifact_timeline_entries(
            artifact,
            kind_filter="execution",
            delta_mode="none",
            phase_filter="execution-loop",
            focus_mode="none",
        )
        task_ids: list[str] = []
        for entry in entries:
            task_id = str(entry.get("task_id") or "").strip()
            if task_id and task_id not in task_ids:
                task_ids.append(task_id)
        return task_ids

    def _phase_local_recent_drift_task_id(self, artifact) -> str | None:
        entries = self._planning_artifact_timeline_entries(
            artifact,
            kind_filter="execution",
            delta_mode="none",
            phase_filter="execution-loop",
            focus_mode="none",
        )
        recent_task_id: str | None = None
        for entry in entries:
            secondary_action = str(entry.get("secondary_action") or "").strip()
            if secondary_action.startswith("/task drift "):
                task_id = str(entry.get("task_id") or "").strip()
                if task_id:
                    recent_task_id = task_id
        return recent_task_id

    def open_phase_local_execution_task(self, identifier: str = "") -> str:
        artifact = self.active_planning_artifact()
        if artifact is None:
            return "No active planning artifact for phase-local execution task."
        task_id = identifier.strip()
        if not task_id:
            task_ids = self._phase_local_execution_task_ids(artifact)
            task_id = task_ids[-1] if task_ids else ""
        if not task_id:
            return "No execution task available in the current phase-local audit summary."
        return self._session.open_task_detail(task_id)

    def open_phase_local_recent_drift_task(self, identifier: str = "") -> str:
        artifact = self.active_planning_artifact()
        if artifact is None:
            return "No active planning artifact for phase-local drift task."
        task_id = identifier.strip() or (self._phase_local_recent_drift_task_id(artifact) or "")
        if not task_id:
            return "No drift-linked execution task available in the current phase-local audit summary."
        return self._session.open_task_drift_detail(task_id)

    def focus_active_plan_timeline_task(self, identifier: str) -> str:
        task_id = identifier.strip()
        if not task_id:
            return "Usage: task id is required."
        return self.describe_active_plan_timeline(focus_mode=f"task:{task_id}")

    def clear_active_plan_timeline_focus(self) -> str:
        return self.describe_active_plan_timeline(focus_mode="none")

    def _describe_planning_artifact_advisor(
        self,
        artifact,
        *,
        file_index: int = 0,
        preserve_current_focus: bool = True,
    ) -> str:
        session = self._session
        plan_context = session.resolve_active_plan_file_context(  # noqa: SLF001
            identifier=artifact.artifact_id,
            file_index=file_index,
            preserve_current_focus=preserve_current_focus and file_index == 0,
        )
        effective_file_index = int(plan_context["selected_index"])
        lines = [
            f"artifact_id: {artifact.artifact_id}",
            f"goal: {artifact.goal}",
            f"lineage_position: {self._planning_artifact_lineage_position(artifact)}",
            "advisor_review:",
        ]
        advisor_lines = self._render_planning_artifact_advisor_review(artifact)
        if advisor_lines:
            lines.extend(advisor_lines)
        else:
            lines.append("(none)")
        if session.state.advisor_last_result is not None:
            lines.append("")
            lines.append("latest_session_advisor_review:")
            lines.append(
                f"- {session.state.advisor_last_result.checkpoint}/{session.state.advisor_last_result.status}"
            )
            if session.state.advisor_last_result.reason:
                lines.append(f"- reason: {session.state.advisor_last_result.reason}")
            if session.state.advisor_last_result.risk_flags:
                lines.append(
                    "- risk_flags: " + ", ".join(session.state.advisor_last_result.risk_flags)
                )
            if session.state.advisor_last_result.suggested_changes:
                lines.append("- suggested_changes:")
                lines.extend(
                    f"  - {item}" for item in session.state.advisor_last_result.suggested_changes
                )
        drift_lines = self._render_recent_plan_drift_analysis(artifact, active=True)
        if drift_lines:
            lines.append("")
            lines.append("recent_plan_drift_analysis:")
            lines.extend(drift_lines)
        lines.append("")
        lines.append(
            "active_plan_risk_flags: "
            + (", ".join(artifact.advisor_risk_flags) if artifact.advisor_risk_flags else "(none)")
        )
        lines.append("derived_from_drift: " + ("yes" if artifact.derived_from_drift else "no"))
        lines.append(f"derivation_reason: {artifact.derivation_reason or 'none'}")
        file_context_payload = plan_context["payload"]
        file_context_sections = session.render_resolved_file_context_sections(plan_context)  # noqa: SLF001
        if file_context_sections:
            lines.append("")
            lines.extend(file_context_sections)
        focused_item = plan_context["focused_item"]
        focused_path = str(focused_item.get("path") or "").strip() if focused_item is not None else ""
        change_actions: list[str] = []
        task_actions: list[str] = []
        if focused_item is not None:
            change_navigation = session._resolve_change_navigation_for_file_context_item(focused_item)
            if change_navigation is not None:
                change_actions.append(str(change_navigation["change_command"]))
                file_command = str(change_navigation.get("change_file_command") or "").strip()
                if file_command:
                    change_actions.append(file_command)
        if focused_path:
            task_actions.extend(session._related_task_commands_for_change_path(focused_path))
        plan_actions = ["/plan advisor"]
        if focused_path:
            plan_actions = session._related_plan_commands_for_change_path(focused_path) + plan_actions
        lines.append("")
        lines.extend(
            session.render_workflow_action_sections(  # noqa: SLF001
                {
                    "go_to_task": self._dedupe_actions(task_actions),
                    "go_to_change": self._dedupe_actions(change_actions),
                    "go_to_plan": self._dedupe_actions(plan_actions),
                    "stay_on_surface": ["/plan advisor"],
                },
                ordered_keys=("go_to_task", "go_to_change", "go_to_plan", "stay_on_surface"),
                line_prefix="",
            )
        )
        return "\n".join(lines)

    def _render_planning_artifact_detail(self, artifact, *, active: bool) -> str:
        lines = self._render_planning_artifact_summary_lines(artifact, active=active)
        scout_lines = self._render_planning_artifact_scout_outputs(artifact)
        if scout_lines:
            lines.append("")
            lines.append("scout_outputs:")
            lines.extend(scout_lines)
            detail_lines = self._render_planning_artifact_scout_detail(artifact, selected_index=0)
            if detail_lines:
                lines.append("")
                lines.append("selected_scout_detail:")
                lines.extend(detail_lines)
        return "\n".join(lines)

    def _render_planning_artifact_summary(self, artifact, *, active: bool, file_index: int = 0) -> str:
        return "\n".join(
            self._render_planning_artifact_summary_lines(
                artifact,
                active=active,
                file_index=file_index,
            )
        )

    def _render_planning_artifact_summary_lines(
        self,
        artifact,
        *,
        active: bool,
        file_index: int = 0,
    ) -> list[str]:
        session = self._session
        plan_context = session.resolve_active_plan_file_context(  # noqa: SLF001
            identifier=artifact.artifact_id,
            file_index=file_index,
            preserve_current_focus=False,
        )
        resolved_file_index = int(plan_context["selected_index"])
        scout_tasks = self._scout_tasks_for_artifact(artifact)
        execution_tasks = self._execution_tasks_for_artifact(artifact)
        lines = [
            f"artifact_id: {artifact.artifact_id}",
            f"active: {'yes' if active else 'no'}",
            f"currently_executing: {'yes' if artifact.artifact_id == session.state.active_execution_plan_id else 'no'}",
            f"kind: {artifact.kind}",
            f"goal: {artifact.goal}",
            f"created_at: {artifact.created_at}",
            f"supersedes_artifact_id: {artifact.supersedes_artifact_id or 'none'}",
            f"superseded_by_artifact_id: {artifact.superseded_by_artifact_id or 'none'}",
            f"lineage_position: {self._planning_artifact_lineage_position(artifact)}",
            f"derived_from_drift: {'yes' if artifact.derived_from_drift else 'no'}",
            f"derivation_reason: {artifact.derivation_reason or 'none'}",
            f"used_read_only_subagents: {'yes' if artifact.used_read_only_subagents else 'no'}",
            f"scout_categories: {', '.join(artifact.scout_categories) if artifact.scout_categories else '(none)'}",
            f"task_ids: {', '.join(artifact.task_ids) if artifact.task_ids else '(none)'}",
            f"scout_task_count: {len(scout_tasks)}",
            f"execution_task_count: {len(execution_tasks)}",
            "execution_task_ids: "
            + (", ".join(task.id for task in execution_tasks) if execution_tasks else "(none)"),
            f"advisor_status: {artifact.advisor_status or 'none'}",
            "advisor_risk_flags: "
            + (", ".join(artifact.advisor_risk_flags) if artifact.advisor_risk_flags else "(none)"),
            "summary:",
            artifact.summary,
        ]
        advisor_lines = self._render_planning_artifact_advisor_review(artifact)
        if advisor_lines:
            lines.append("")
            lines.append("advisor_review:")
            lines.extend(advisor_lines)
        lineage_lines = self._render_planning_artifact_lineage(artifact)
        if lineage_lines:
            lines.append("")
            lines.append("lineage:")
            lines.extend(lineage_lines)
        comparison_lines = self._render_planning_artifact_comparisons(artifact)
        if comparison_lines:
            lines.append("")
            lines.append("comparisons:")
            lines.extend(comparison_lines)
        if active and session.state.advisor_last_result is not None:
            lines.append("")
            lines.append("latest_session_advisor_review:")
            lines.append(
                f"- {session.state.advisor_last_result.checkpoint}/{session.state.advisor_last_result.status}"
            )
            if session.state.advisor_last_result.reason:
                lines.append(f"- reason: {session.state.advisor_last_result.reason}")
            if session.state.advisor_last_result.risk_flags:
                lines.append("- risk_flags: " + ", ".join(session.state.advisor_last_result.risk_flags))
        drift_lines = self._render_recent_plan_drift_analysis(artifact, active=active)
        if drift_lines:
            lines.append("")
            lines.append("recent_plan_drift_analysis:")
            lines.extend(drift_lines)
        file_context_sections = session.render_resolved_file_context_sections(plan_context)  # noqa: SLF001
        if file_context_sections:
            lines.append("")
            lines.extend(file_context_sections)
        lines.append("")
        lines.append("next_actions:")
        if active:
            lines.append(f"- /plan derive {artifact.goal}")
            lines.append("- /plan scouts")
            lines.append("- /plan lineage")
        else:
            lines.append(f"- /plan revert {artifact.artifact_id}")
            lines.append(f"- /plan use {artifact.artifact_id}")
        if artifact.supersedes_artifact_id:
            lines.append(f"- /plan show {artifact.supersedes_artifact_id}")
        if artifact.superseded_by_artifact_id:
            lines.append(f"- /plan show {artifact.superseded_by_artifact_id}")
        return lines

    def _render_recent_plan_drift_analysis(
        self,
        artifact,
        *,
        active: bool,
    ) -> list[str]:
        session = self._session
        if not active or not session.state.last_plan_drift_context:
            return []
        compact = session._compact_multiline_text(
            session.state.last_plan_drift_context,
            max_lines=12,
            max_chars=1400,
        )
        return [f"- {line}" for line in compact.splitlines()]

    def _render_planning_artifact_comparison(
        self,
        label: str,
        base,
        target,
    ) -> list[str]:
        session = self._session
        lines = [f"- {label}: {base.artifact_id} -> {target.artifact_id}"]
        if base.goal != target.goal:
            lines.append(f"  goal_changed: {base.goal} -> {target.goal}")
        if (base.advisor_status or "none") != (target.advisor_status or "none"):
            lines.append(
                "  advisor_status_changed: "
                f"{base.advisor_status or 'none'} -> {target.advisor_status or 'none'}"
            )
        if base.advisor_risk_flags != target.advisor_risk_flags:
            lines.append(
                "  risk_flags_changed: "
                f"{', '.join(base.advisor_risk_flags) if base.advisor_risk_flags else '(none)'}"
                " -> "
                f"{', '.join(target.advisor_risk_flags) if target.advisor_risk_flags else '(none)'}"
            )
        if base.derived_from_drift != target.derived_from_drift:
            lines.append(
                "  derived_from_drift_changed: "
                f"{'yes' if base.derived_from_drift else 'no'} -> "
                f"{'yes' if target.derived_from_drift else 'no'}"
            )
        if (base.derivation_reason or "none") != (target.derivation_reason or "none"):
            lines.append(
                "  derivation_reason_changed: "
                f"{base.derivation_reason or 'none'} -> {target.derivation_reason or 'none'}"
            )
        lines.extend(
            self._render_plan_section_diff(
                "implementation_plan_diff",
                base.summary,
                target.summary,
                section_key="implementation_plan",
            )
        )
        lines.extend(
            self._render_plan_section_diff(
                "verification_checklist_diff",
                base.summary,
                target.summary,
                section_key="verification_checklist",
            )
        )
        lines.extend(
            self._render_plan_section_diff(
                "risks_open_questions_diff",
                base.summary,
                target.summary,
                section_key="risks_open_questions",
            )
        )
        diff_lines = self._summarize_planning_summary_diff(base.summary, target.summary)
        if diff_lines:
            lines.append("  summary_diff:")
            lines.extend(f"    {line}" for line in diff_lines)
        return lines

    def _render_plan_section_diff(
        self,
        label: str,
        before_summary: str,
        after_summary: str,
        *,
        section_key: str,
    ) -> list[str]:
        before_sections = self._plan_summary_sections(before_summary)
        after_sections = self._plan_summary_sections(after_summary)
        before = before_sections.get(section_key, "").strip()
        after = after_sections.get(section_key, "").strip()
        if before == after:
            return []
        diff_lines = self._session._summarize_text_diff(before, after)
        if not diff_lines:
            return []
        lines = [f"  {label}:"]
        lines.extend(f"    {line}" for line in diff_lines)
        return lines

    def _plan_summary_sections(self, summary: str) -> dict[str, str]:
        header_aliases = {
            "current architecture": "current_architecture",
            "1. current architecture": "current_architecture",
            "implementation plan": "implementation_plan",
            "2. implementation plan": "implementation_plan",
            "risks / open questions": "risks_open_questions",
            "3. risks / open questions": "risks_open_questions",
            "verification checklist": "verification_checklist",
            "4. verification checklist": "verification_checklist",
        }
        sections: dict[str, list[str]] = {}
        current_key: str | None = None
        for raw_line in summary.splitlines():
            stripped = raw_line.strip()
            normalized = stripped.lstrip("#").strip().lower()
            next_key = header_aliases.get(normalized)
            if next_key is not None:
                current_key = next_key
                sections.setdefault(current_key, [])
                continue
            if current_key is not None:
                sections.setdefault(current_key, []).append(raw_line)
        return {key: "\n".join(lines).strip() for key, lines in sections.items()}

    def _summarize_planning_summary_diff(self, before: str, after: str) -> list[str]:
        diff = list(
            difflib.unified_diff(
                before.splitlines(),
                after.splitlines(),
                fromfile="before",
                tofile="after",
                lineterm="",
            )
        )
        filtered = [line for line in diff[2:] if line.startswith("+") or line.startswith("-")]
        if not filtered:
            return []
        if len(filtered) > 8:
            filtered = filtered[:8] + [f"... {len(diff[2:]) - 8} more diff line(s)"]
        return filtered

    def _render_planning_artifact_advisor_review(self, artifact) -> list[str]:
        if not artifact.advisor_status:
            return []
        lines = [f"- status: {artifact.advisor_status}"]
        if artifact.advisor_reason:
            lines.append(f"- reason: {artifact.advisor_reason}")
        if artifact.advisor_risk_flags:
            lines.append("- risk_flags: " + ", ".join(artifact.advisor_risk_flags))
        if artifact.advisor_suggested_changes:
            lines.append("- suggested_changes:")
            lines.extend(f"  - {item}" for item in artifact.advisor_suggested_changes)
        return lines

    def _planning_artifact_scout_snapshots(self, artifact) -> list[dict[str, Any]]:
        session = self._session
        snapshots: list[dict[str, Any]] = []
        tasks = self._scout_tasks_for_artifact(artifact)
        seen_task_ids = {task.id for task in tasks}
        for task in tasks:
            snapshots.append(
                {
                    "task_id": task.id,
                    "kind": task.kind,
                    "missing": False,
                    "status": task.status,
                    "created_at": task.created_at,
                    "updated_at": task.updated_at,
                    "ended_at": task.ended_at,
                    "category": task.metadata.get("scout_category", "(unknown)"),
                    "description": task.description,
                    "progress_summary": task.progress_summary,
                    "metadata": dict(task.metadata),
                    "detail": (task.output or task.error or task.progress_summary or "").strip(),
                    "output": task.output.strip(),
                    "error": (task.error or "").strip(),
                }
            )
        for task_id in artifact.task_ids:
            if task_id in seen_task_ids:
                continue
            task = session.task_manager.get(task_id)
            if task is not None:
                continue
            snapshots.append(
                {
                    "task_id": task_id,
                    "missing": True,
                    "status": "missing",
                    "category": "(unknown)",
                    "description": "(missing task)",
                    "detail": "",
                }
            )
        return snapshots

    def _planning_artifact_execution_snapshots(self, artifact) -> list[dict[str, Any]]:
        tasks = self._execution_tasks_for_artifact(artifact)
        snapshots: list[dict[str, Any]] = []
        for task in tasks:
            metadata = dict(task.metadata)
            snapshots.append(
                {
                    "task_id": task.id,
                    "kind": task.kind,
                    "missing": False,
                    "status": task.status,
                    "created_at": task.created_at,
                    "updated_at": task.updated_at,
                    "ended_at": task.ended_at,
                    "phase": metadata.get("plan_execution_phase", "(unknown)"),
                    "mode": metadata.get("plan_execution_mode", "(unknown)"),
                    "plan_status": metadata.get("plan_status", "(unknown)"),
                    "description": task.description,
                    "progress_summary": task.progress_summary,
                    "metadata": metadata,
                    "detail": (task.output or task.error or task.progress_summary or "").strip(),
                    "output": task.output.strip(),
                    "error": (task.error or "").strip(),
                }
            )
        return snapshots

    def _render_planning_artifact_scout_outputs(
        self,
        artifact,
        *,
        selected_index: int | None = None,
    ) -> list[str]:
        snapshots = self._planning_artifact_scout_snapshots(artifact)
        if not snapshots:
            return []
        lines: list[str] = []
        normalized_index = None
        if selected_index is not None and snapshots:
            normalized_index = max(0, min(selected_index, len(snapshots) - 1))
        for index, snapshot in enumerate(snapshots):
            marker = ">" if normalized_index == index else "-"
            lines.append(
                f"{marker} {index + 1}. {snapshot['task_id']}: status={snapshot['status']} "
                f"category={snapshot['category']} description={snapshot['description']}"
            )
            if snapshot["missing"]:
                lines.append("  missing")
        return lines

    def _render_planning_artifact_scout_detail(
        self,
        artifact,
        *,
        selected_index: int = 0,
        full_detail: bool = False,
    ) -> list[str]:
        session = self._session
        snapshots = self._planning_artifact_scout_snapshots(artifact)
        if not snapshots:
            return []
        snapshot = snapshots[max(0, min(selected_index, len(snapshots) - 1))]
        lines = [
            f"- task_id: {snapshot['task_id']}",
            f"- kind: {snapshot.get('kind', '(unknown)')}",
            f"- status: {snapshot['status']}",
            f"- category: {snapshot['category']}",
            f"- description: {snapshot['description']}",
        ]
        if snapshot["missing"]:
            lines.append("- detail: missing")
            return lines
        if snapshot.get("created_at"):
            lines.append(f"- created_at: {snapshot['created_at']}")
        if snapshot.get("updated_at"):
            lines.append(f"- updated_at: {snapshot['updated_at']}")
        if snapshot.get("ended_at"):
            lines.append(f"- ended_at: {snapshot['ended_at']}")
        progress_summary = str(snapshot.get("progress_summary") or "").strip()
        if progress_summary:
            lines.append(f"- progress_summary: {progress_summary}")
        metadata = snapshot.get("metadata") or {}
        metadata_bits = []
        for key in ("planner_kind", "task_role", "scout_category", "workspace_mode", "child_cwd", "active_plan_id"):
            value = metadata.get(key)
            if value:
                metadata_bits.append(f"{key}={value}")
        if metadata_bits:
            lines.append("- metadata: " + ", ".join(metadata_bits))
        output_text = str(snapshot.get("output") or "").strip()
        error_text = str(snapshot.get("error") or "").strip()
        if not output_text and not error_text and not snapshot["detail"]:
            lines.append("- detail: (no scout output)")
            return lines
        if output_text:
            lines.append("- output:")
            compact_output = session._compact_multiline_text(
                output_text,
                max_lines=200 if full_detail else 30,
                max_chars=12000 if full_detail else 3200,
            )
            lines.extend(f"  {line}" for line in compact_output.splitlines())
        if error_text:
            lines.append("- error:")
            compact_error = session._compact_multiline_text(
                error_text,
                max_lines=120 if full_detail else 20,
                max_chars=9000 if full_detail else 2200,
            )
            lines.extend(f"  {line}" for line in compact_error.splitlines())
        if not output_text and snapshot["detail"]:
            lines.append("- detail:")
            compact = session._compact_multiline_text(
                str(snapshot["detail"]),
                max_lines=120 if full_detail else 20,
                max_chars=9000 if full_detail else 2200,
            )
            lines.extend(f"  {line}" for line in compact.splitlines())
        return lines

    def _render_planning_artifact_execution_outputs(
        self,
        artifact,
        *,
        selected_index: int | None = None,
    ) -> list[str]:
        snapshots = self._planning_artifact_execution_snapshots(artifact)
        if not snapshots:
            return []
        lines: list[str] = []
        normalized_index = None
        if selected_index is not None and snapshots:
            normalized_index = max(0, min(selected_index, len(snapshots) - 1))
        for index, snapshot in enumerate(snapshots):
            marker = ">" if normalized_index == index else "-"
            lines.append(
                f"{marker} {index + 1}. {snapshot['task_id']}: status={snapshot['status']} "
                f"phase={snapshot['phase']} plan_status={snapshot['plan_status']} "
                f"description={snapshot['description']}"
            )
        return lines

    def _render_planning_artifact_execution_detail(
        self,
        artifact,
        *,
        selected_index: int = 0,
        full_detail: bool = False,
    ) -> list[str]:
        session = self._session
        snapshots = self._planning_artifact_execution_snapshots(artifact)
        if not snapshots:
            return []
        snapshot = snapshots[max(0, min(selected_index, len(snapshots) - 1))]
        lines = [
            f"- task_id: {snapshot['task_id']}",
            f"- kind: {snapshot.get('kind', '(unknown)')}",
            f"- status: {snapshot['status']}",
            f"- mode: {snapshot['mode']}",
            f"- phase: {snapshot['phase']}",
            f"- plan_status: {snapshot['plan_status']}",
            f"- description: {snapshot['description']}",
        ]
        if snapshot.get("created_at"):
            lines.append(f"- created_at: {snapshot['created_at']}")
        if snapshot.get("updated_at"):
            lines.append(f"- updated_at: {snapshot['updated_at']}")
        if snapshot.get("ended_at"):
            lines.append(f"- ended_at: {snapshot['ended_at']}")
        progress_summary = str(snapshot.get("progress_summary") or "").strip()
        if progress_summary:
            lines.append(f"- progress_summary: {progress_summary}")
        metadata = snapshot.get("metadata") or {}
        metadata_bits = []
        for key in (
            "planner_kind",
            "task_role",
            "active_plan_id",
            "active_plan_goal",
            "plan_execution_mode",
            "plan_execution_phase",
            "plan_status",
            "drift_status",
            "drift_reason",
            "constraint_source",
            "workspace_mode",
            "child_cwd",
        ):
            value = metadata.get(key)
            if value:
                metadata_bits.append(f"{key}={value}")
        if metadata_bits:
            lines.append("- metadata: " + ", ".join(metadata_bits))
        output_text = str(snapshot.get("output") or "").strip()
        error_text = str(snapshot.get("error") or "").strip()
        if not output_text and not error_text and not snapshot["detail"]:
            lines.append("- detail: (no execution output)")
            return lines
        if output_text:
            lines.append("- output:")
            compact_output = session._compact_multiline_text(
                output_text,
                max_lines=200 if full_detail else 30,
                max_chars=12000 if full_detail else 3200,
            )
            lines.extend(f"  {line}" for line in compact_output.splitlines())
        if error_text:
            lines.append("- error:")
            compact_error = session._compact_multiline_text(
                error_text,
                max_lines=120 if full_detail else 20,
                max_chars=9000 if full_detail else 2200,
            )
            lines.extend(f"  {line}" for line in compact_error.splitlines())
        if not output_text and snapshot["detail"]:
            lines.append("- detail:")
            compact = session._compact_multiline_text(
                str(snapshot["detail"]),
                max_lines=120 if full_detail else 20,
                max_chars=9000 if full_detail else 2200,
            )
            lines.extend(f"  {line}" for line in compact.splitlines())
        return lines

    def _render_selected_scout_comparisons(
        self,
        artifact,
        *,
        selected_index: int = 0,
    ) -> list[str]:
        session = self._session
        snapshots = self._planning_artifact_scout_snapshots(artifact)
        if len(snapshots) <= 1:
            return []
        selected = snapshots[max(0, min(selected_index, len(snapshots) - 1))]
        lines: list[str] = []
        for index, snapshot in enumerate(snapshots):
            if snapshot["task_id"] == selected["task_id"]:
                continue
            lines.append(
                f"- against {index + 1}/{len(snapshots)}: "
                f"{selected['category']} -> {snapshot['category']}"
            )
            if selected["status"] != snapshot["status"]:
                lines.append(f"  status_changed: {selected['status']} -> {snapshot['status']}")
            selected_progress = str(selected.get("progress_summary") or "").strip() or "(none)"
            snapshot_progress = str(snapshot.get("progress_summary") or "").strip() or "(none)"
            if selected_progress != snapshot_progress:
                lines.append(f"  progress_changed: {selected_progress} -> {snapshot_progress}")
            selected_text = str(selected.get("output") or selected.get("error") or selected.get("detail") or "").strip()
            snapshot_text = str(snapshot.get("output") or snapshot.get("error") or snapshot.get("detail") or "").strip()
            diff_lines = session._summarize_text_diff(selected_text, snapshot_text)
            if diff_lines:
                lines.append("  detail_diff:")
                lines.extend(f"    {line}" for line in diff_lines)
        return lines

    def _render_selected_execution_comparisons(
        self,
        artifact,
        *,
        selected_index: int = 0,
    ) -> list[str]:
        session = self._session
        snapshots = self._planning_artifact_execution_snapshots(artifact)
        if len(snapshots) <= 1:
            return []
        selected = snapshots[max(0, min(selected_index, len(snapshots) - 1))]
        lines: list[str] = []
        for index, snapshot in enumerate(snapshots):
            if snapshot["task_id"] == selected["task_id"]:
                continue
            lines.append(
                f"- against {index + 1}/{len(snapshots)}: "
                f"{selected['phase']} -> {snapshot['phase']}"
            )
            if selected["status"] != snapshot["status"]:
                lines.append(f"  status_changed: {selected['status']} -> {snapshot['status']}")
            if selected["phase"] != snapshot["phase"]:
                lines.append(f"  phase_changed: {selected['phase']} -> {snapshot['phase']}")
            if selected["plan_status"] != snapshot["plan_status"]:
                lines.append(
                    f"  plan_status_changed: {selected['plan_status']} -> {snapshot['plan_status']}"
                )
            selected_progress = str(selected.get("progress_summary") or "").strip() or "(none)"
            snapshot_progress = str(snapshot.get("progress_summary") or "").strip() or "(none)"
            if selected_progress != snapshot_progress:
                lines.append(f"  progress_changed: {selected_progress} -> {snapshot_progress}")
            selected_text = str(selected.get("output") or selected.get("error") or selected.get("detail") or "").strip()
            snapshot_text = str(snapshot.get("output") or snapshot.get("error") or snapshot.get("detail") or "").strip()
            diff_lines = session._summarize_text_diff(selected_text, snapshot_text)
            if diff_lines:
                lines.append("  detail_diff:")
                lines.extend(f"    {line}" for line in diff_lines)
        return lines

    def _render_selected_execution_context(
        self,
        artifact,
        *,
        selected_index: int = 0,
    ) -> list[str]:
        session = self._session
        snapshots = self._planning_artifact_execution_snapshots(artifact)
        if not snapshots:
            return []
        snapshot = snapshots[max(0, min(selected_index, len(snapshots) - 1))]
        metadata = snapshot.get("metadata") or {}
        lines: list[str] = []
        constraint_source = str(metadata.get("constraint_source") or "").strip()
        drift_status = str(metadata.get("drift_status") or "").strip()
        drift_reason = str(metadata.get("drift_reason") or "").strip()
        if constraint_source:
            lines.append(f"- linked_constraint_source: {constraint_source}")
        if drift_status:
            lines.append(f"- linked_drift_status: {drift_status}")
        if drift_reason:
            lines.append(f"- linked_drift_reason: {drift_reason}")
        if session.state.constraint_reason and (constraint_source or drift_status):
            lines.append(f"- linked_constraint_reason: {session.state.constraint_reason}")
        if session.state.advisor_last_result is not None and (constraint_source or drift_status):
            review = session.state.advisor_last_result
            lines.append("- linked_advisor_review:")
            lines.append(f"  - {review.checkpoint}/{review.status}")
            if review.reason:
                lines.append(f"  - reason: {review.reason}")
            if review.risk_flags:
                lines.append("  - risk_flags: " + ", ".join(review.risk_flags))
            if review.suggested_changes:
                lines.append("  - suggested_changes:")
                lines.extend(f"    - {item}" for item in review.suggested_changes)
        if session.state.last_plan_drift_context and (constraint_source or drift_status):
            lines.append("- linked_plan_drift_analysis:")
            compact = session._compact_multiline_text(
                session.state.last_plan_drift_context,
                max_lines=10,
                max_chars=1200,
            )
            lines.extend(f"  {line}" for line in compact.splitlines())
        return lines

    def describe_active_plan_advisor(
        self,
        *,
        file_index: int = 0,
        preserve_current_focus: bool = True,
    ) -> str:
        artifact = self.active_planning_artifact()
        if artifact is None:
            return "No active planning artifact for advisor detail."
        return self._describe_planning_artifact_advisor(
            artifact,
            file_index=file_index,
            preserve_current_focus=preserve_current_focus,
        )

    def open_active_plan_advisor(self) -> str:
        return self.describe_active_plan_advisor()

    def describe_planning_artifacts(self) -> str:
        session = self._session
        artifacts = list(reversed(session.state.planning_artifact_history))
        if not artifacts:
            return "No planning artifacts."
        active_id = session.state.active_planning_artifact_id
        lines = ["planning artifacts:"]
        for artifact in artifacts:
            marker = "*" if artifact.artifact_id == active_id else "-"
            relation_bits = []
            if artifact.supersedes_artifact_id:
                relation_bits.append(f"supersedes={artifact.supersedes_artifact_id}")
            if artifact.superseded_by_artifact_id:
                relation_bits.append(f"superseded_by={artifact.superseded_by_artifact_id}")
            if artifact.derived_from_drift:
                relation_bits.append("derived_from_drift=yes")
            relation_suffix = f" {' '.join(relation_bits)}" if relation_bits else ""
            lines.append(
                f"{marker} {artifact.artifact_id} kind={artifact.kind} goal={artifact.goal} "
                f"created_at={artifact.created_at} scouts={len(artifact.scout_categories)} "
                f"tasks={len(artifact.task_ids)}{relation_suffix}"
            )
        return "\n".join(lines)

    def describe_planning_artifact(self, identifier: str) -> str:
        session = self._session
        target = identifier.strip() or "latest"
        artifact = self.resolve_planning_artifact(target)
        if artifact is None:
            return f'Unknown planning artifact "{target}".'
        return self._render_planning_artifact_detail(
            artifact,
            active=artifact.artifact_id == session.state.active_planning_artifact_id,
        )

    def use_planning_artifact(self, identifier: str) -> str:
        session = self._session
        target = identifier.strip()
        if not target:
            return "Usage: /plan use <artifact-id|latest>"
        artifact = self.resolve_planning_artifact(target)
        if artifact is None:
            return f'Unknown planning artifact "{target}".'
        session.state.active_planning_artifact_id = artifact.artifact_id
        session.persist_state()
        return (
            f'Active planning artifact set to {artifact.artifact_id}.\n'
            f"Goal: {artifact.goal}"
        )

    def revert_to_planning_artifact(self, identifier: str) -> str:
        session = self._session
        target = identifier.strip()
        if not target:
            return "Usage: /plan revert <artifact-id|latest>"
        artifact = self.resolve_planning_artifact(target)
        if artifact is None:
            return f'Unknown planning artifact "{target}".'
        session.state.active_planning_artifact_id = artifact.artifact_id
        session.persist_state()
        return (
            f"Reactivated planning artifact {artifact.artifact_id}.\n"
            f"Goal: {artifact.goal}"
        )

    def clear_active_plan(self) -> str:
        session = self._session
        if session.state.active_planning_artifact_id is None:
            return "No active planning artifact."
        previous = session.state.active_planning_artifact_id
        session.state.active_planning_artifact_id = None
        session.persist_state()
        return f"Cleared active planning artifact {previous}."

    def record_planning_artifact(self, artifact) -> None:
        session = self._session
        if artifact.supersedes_artifact_id:
            for item in session.state.planning_artifact_history:
                if item.artifact_id == artifact.supersedes_artifact_id:
                    item.superseded_by_artifact_id = artifact.artifact_id
                    break
        session.state.planning_artifact_history.append(artifact)
        if len(session.state.planning_artifact_history) > MAX_PLANNING_ARTIFACTS:
            session.state.planning_artifact_history = session.state.planning_artifact_history[
                -MAX_PLANNING_ARTIFACTS:
            ]
        valid_ids = {item.artifact_id for item in session.state.planning_artifact_history}
        for item in session.state.planning_artifact_history:
            if item.supersedes_artifact_id not in valid_ids:
                item.supersedes_artifact_id = None
            if item.superseded_by_artifact_id not in valid_ids:
                item.superseded_by_artifact_id = None
        session.state.recent_planning_artifacts = list(session.state.planning_artifact_history)
        session.state.active_planning_artifact_id = artifact.artifact_id

    def prepare_plan_derivation(self, goal: str):
        session = self._session
        active = self.active_planning_artifact()
        if active is None:
            return 'No active planning artifact. Use "/ultraplan <goal>" first.'
        request = goal.strip() or active.goal
        derived_from_drift = bool(
            session.state.last_plan_drift_status
            and session.state.last_plan_drift_context
            and session.state.active_planning_artifact_id == active.artifact_id
        )
        derivation_reason = session.state.last_plan_drift_reason if derived_from_drift else ""
        from ..commands.prompt_commands import build_ultraplan_execution

        return build_ultraplan_execution(
            session,
            request,
            metadata_extra={
                "supersede_artifact_id": active.artifact_id,
                "derived_from_active_plan": True,
                "derived_from_drift": derived_from_drift,
                "derivation_reason": derivation_reason,
            },
            derivation_context={
                "artifact_id": active.artifact_id,
                "goal": active.goal,
                "summary": active.summary,
                "derivation_reason": derivation_reason,
                "last_plan_drift_context": session.state.last_plan_drift_context or "",
            },
            progress_message=(
                "Revising active plan from drift feedback"
                if derived_from_drift
                else "Revising active plan"
            ),
        )

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
        return self._session._original_run_ultraplan(
            goal=goal,
            scout_categories=scout_categories,
            supersede_artifact_id=supersede_artifact_id,
            derived_from_drift=derived_from_drift,
            derivation_reason=derivation_reason,
            sink=sink,
        )
