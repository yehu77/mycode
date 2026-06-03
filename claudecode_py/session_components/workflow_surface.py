from __future__ import annotations

from typing import Any


def dedupe_action_commands(commands: list[str]) -> list[str]:
    deduped: list[str] = []
    for command in commands:
        normalized = str(command).strip()
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return deduped


def build_file_context_item_action_groups(
    session: Any,
    item: dict[str, Any],
    *,
    stay_on_surface_actions: list[str],
) -> dict[str, list[str]]:
    change_actions: list[str] = []
    change_navigation = session._resolve_change_navigation_for_file_context_item(item)
    if change_navigation is not None:
        change_actions.append(str(change_navigation["change_command"]))
        file_command = str(change_navigation.get("change_file_command") or "").strip()
        if file_command:
            change_actions.append(file_command)
    path = str(item.get("path") or "").strip()
    task_actions = session._related_task_commands_for_change_path(path) if path else []
    plan_actions = session._related_plan_commands_for_change_path(path) if path else []
    return {
        "go_to_change": dedupe_action_commands(change_actions),
        "go_to_task": dedupe_action_commands(task_actions),
        "go_to_plan": dedupe_action_commands(plan_actions),
        "stay_on_surface": dedupe_action_commands(stay_on_surface_actions),
    }


def build_file_surface_action_groups(
    session: Any,
    item: dict[str, Any],
    *,
    stay_on_surface_actions: list[str],
    inspect_focused_file_actions: list[str] | None = None,
    inspect_focused_diff_actions: list[str] | None = None,
    inspect_explicit_context_actions: list[str] | None = None,
) -> dict[str, list[str]]:
    action_groups = build_file_context_item_action_groups(
        session,
        item,
        stay_on_surface_actions=stay_on_surface_actions,
    )
    return {
        "inspect_focused_file": dedupe_action_commands(inspect_focused_file_actions or []),
        "inspect_focused_diff": dedupe_action_commands(inspect_focused_diff_actions or []),
        "inspect_change": dedupe_action_commands(action_groups.get("go_to_change", [])),
        "inspect_task": dedupe_action_commands(action_groups.get("go_to_task", [])),
        "inspect_active_plan": dedupe_action_commands(action_groups.get("go_to_plan", [])),
        "inspect_explicit_context": dedupe_action_commands(inspect_explicit_context_actions or []),
        "stay_on_surface": dedupe_action_commands(action_groups.get("stay_on_surface", [])),
    }


def render_action_group_lines(
    action_groups: dict[str, list[str]],
    *,
    heading: str = "next_actions:",
    line_prefix: str = "- ",
    ordered_keys: tuple[str, ...] | None = None,
) -> list[str]:
    keys = ordered_keys or tuple(action_groups.keys())
    lines = [heading]
    for key in keys:
        actions = dedupe_action_commands(list(action_groups.get(key, [])))
        lines.append(f"{line_prefix}{key}: " + (" | ".join(actions) if actions else "none"))
    return lines


def render_action_group_summary(
    action_groups: dict[str, list[str]],
    *,
    ordered_keys: tuple[str, ...] | None = None,
    separator: str = "; ",
    key_value_separator: str = "=",
) -> str:
    keys = ordered_keys or tuple(action_groups.keys())
    parts: list[str] = []
    for key in keys:
        actions = dedupe_action_commands(list(action_groups.get(key, [])))
        value = " | ".join(actions) if actions else "none"
        parts.append(f"{key}{key_value_separator}{value}")
    return separator.join(parts)


def render_workflow_action_sections(
    action_groups: dict[str, list[str]],
    *,
    heading: str = "next_actions:",
    ordered_keys: tuple[str, ...] | None = None,
    line_prefix: str = "- ",
) -> list[str]:
    return render_action_group_lines(
        action_groups,
        heading=heading,
        ordered_keys=ordered_keys,
        line_prefix=line_prefix,
    )


def render_navigation_section(
    commands: dict[str, str | None],
    *,
    heading: str = "navigation:",
    line_prefix: str = "- ",
    ordered_keys: tuple[str, ...] | None = None,
    include_empty: bool = True,
) -> list[str]:
    keys = ordered_keys or tuple(commands.keys())
    lines = [heading]
    for key in keys:
        raw_value = commands.get(key)
        value = str(raw_value).strip() if raw_value is not None else ""
        if not value and not include_empty:
            continue
        lines.append(f"{line_prefix}{key}: " + (value or "none"))
    return lines


def render_primary_secondary_action_section(
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
    lines: list[str] = []
    for key, raw_value in (
        (primary_label, primary_action),
        (secondary_label, secondary_action),
    ):
        value = str(raw_value).strip() if raw_value is not None else ""
        if value:
            lines.append(f"{line_prefix}{key}: {value}")
    if next_actions:
        rendered_next = render_navigation_section(
            next_actions,
            heading=next_actions_heading,
            line_prefix="- ",
            include_empty=include_empty_next_actions,
        )
        lines.extend(f"{line_prefix}{line}" if line_prefix else line for line in rendered_next)
    return lines


def render_summary_field_lines(
    summary_fields: list[tuple[str, Any | None]],
    *,
    line_prefix: str = "",
    include_empty: bool = False,
    empty_value: str = "none",
) -> list[str]:
    lines: list[str] = []
    for key, raw_value in summary_fields:
        value = str(raw_value).strip() if raw_value is not None else ""
        if not value and not include_empty:
            continue
        lines.append(f"{line_prefix}{key}: " + (value or empty_value))
    return lines


def render_surface_metadata_section(
    title: str,
    *,
    summary_fields: list[tuple[str, Any | None]],
    action_groups: dict[str, list[str]] | None = None,
    action_order: tuple[str, ...] | None = None,
    line_prefix: str = "- ",
    include_empty_fields: bool = False,
) -> list[str]:
    lines = [title] if title else []
    lines.extend(
        render_summary_field_lines(
            summary_fields,
            line_prefix=line_prefix,
            include_empty=include_empty_fields,
        )
    )
    if action_groups:
        lines.extend(
            render_workflow_action_sections(
                action_groups,
                heading=f"{line_prefix}next_actions:",
                ordered_keys=action_order,
                line_prefix=f"{line_prefix}- ",
            )
        )
    return lines


def render_selected_surface_summary(
    title: str,
    *,
    summary_fields: list[tuple[str, str | None]],
    metadata_line: str | None = None,
    focused_file_lines: list[str] | None = None,
    action_groups: dict[str, list[str]] | None = None,
    action_order: tuple[str, ...] | None = None,
    line_prefix: str = "- ",
) -> list[str]:
    lines = render_surface_metadata_section(
        title,
        summary_fields=summary_fields,
        action_groups=action_groups,
        action_order=action_order,
        line_prefix=line_prefix,
    )
    if metadata_line:
        lines.append(f"{line_prefix}metadata: {metadata_line}")
    if focused_file_lines:
        lines.extend(focused_file_lines)
    return lines


def render_file_context_action_group_lines(
    session: Any,
    item: dict[str, Any],
    *,
    stay_on_surface_actions: list[str],
    heading: str = "next_actions:",
    line_prefix: str = "- ",
    ordered_keys: tuple[str, ...] | None = None,
) -> list[str]:
    return render_action_group_lines(
        build_file_context_item_action_groups(
            session,
            item,
            stay_on_surface_actions=stay_on_surface_actions,
        ),
        heading=heading,
        line_prefix=line_prefix,
        ordered_keys=ordered_keys or ("go_to_change", "go_to_task", "go_to_plan", "stay_on_surface"),
    )


def render_file_context_action_group_summary(
    session: Any,
    item: dict[str, Any],
    *,
    stay_on_surface_actions: list[str],
    extra_actions: dict[str, list[str]] | None = None,
    ordered_keys: tuple[str, ...] | None = None,
) -> str:
    action_groups = build_file_context_item_action_groups(
        session,
        item,
        stay_on_surface_actions=stay_on_surface_actions,
    )
    if extra_actions:
        for key, values in extra_actions.items():
            action_groups[key] = dedupe_action_commands([*action_groups.get(key, []), *values])
    return render_action_group_summary(
        action_groups,
        ordered_keys=ordered_keys or ("go_to_change", "go_to_task", "go_to_plan", "stay_on_surface"),
    )


def render_file_surface_action_group_lines(
    session: Any,
    item: dict[str, Any],
    *,
    stay_on_surface_actions: list[str],
    inspect_focused_file_actions: list[str] | None = None,
    inspect_focused_diff_actions: list[str] | None = None,
    inspect_explicit_context_actions: list[str] | None = None,
    heading: str = "next_actions:",
    line_prefix: str = "- ",
    ordered_keys: tuple[str, ...] | None = None,
) -> list[str]:
    return render_action_group_lines(
        build_file_surface_action_groups(
            session,
            item,
            stay_on_surface_actions=stay_on_surface_actions,
            inspect_focused_file_actions=inspect_focused_file_actions,
            inspect_focused_diff_actions=inspect_focused_diff_actions,
            inspect_explicit_context_actions=inspect_explicit_context_actions,
        ),
        heading=heading,
        line_prefix=line_prefix,
        ordered_keys=ordered_keys
        or (
            "inspect_focused_file",
            "inspect_focused_diff",
            "inspect_change",
            "inspect_task",
            "inspect_active_plan",
            "inspect_explicit_context",
            "stay_on_surface",
        ),
    )


def render_file_surface_action_group_summary(
    session: Any,
    item: dict[str, Any],
    *,
    stay_on_surface_actions: list[str],
    inspect_focused_file_actions: list[str] | None = None,
    inspect_focused_diff_actions: list[str] | None = None,
    inspect_explicit_context_actions: list[str] | None = None,
    ordered_keys: tuple[str, ...] | None = None,
) -> str:
    return render_action_group_summary(
        build_file_surface_action_groups(
            session,
            item,
            stay_on_surface_actions=stay_on_surface_actions,
            inspect_focused_file_actions=inspect_focused_file_actions,
            inspect_focused_diff_actions=inspect_focused_diff_actions,
            inspect_explicit_context_actions=inspect_explicit_context_actions,
        ),
        ordered_keys=ordered_keys
        or (
            "inspect_focused_file",
            "inspect_focused_diff",
            "inspect_change",
            "inspect_task",
            "inspect_active_plan",
            "inspect_explicit_context",
            "stay_on_surface",
        ),
    )


def render_focused_file_context_lines(
    session: Any,
    payload: dict[str, Any] | None,
    *,
    selected_index: int = 0,
    title: str = "focused file",
    include_next_actions: bool = True,
) -> list[str]:
    files, bounded_index, focused_item = session._file_context_items_and_index(
        payload,
        selected_index=selected_index,
    )
    if not files or focused_item is None:
        return []
    lines = [title + ":"]
    scope = str((payload or {}).get("file_context_scope") or "").strip()
    if scope:
        lines.append(f"- scope: {scope}")
    lines.append(f"- file_focus: {bounded_index + 1}/{len(files)}")
    focused_path = str(focused_item.get("path") or "").strip()
    if focused_path:
        lines.append(f"- focused file: {focused_path}")
    source = str(focused_item.get("source") or "").strip()
    if source:
        lines.append(f"- source: {source}")
    scope_reasons = session._file_context_scope_reasons(focused_item)
    if scope_reasons:
        lines.append("- in scope because: " + ", ".join(scope_reasons))
    lines.append("- context origin: " + session._file_context_origin_label(focused_item))
    related_change = str(focused_item.get("change_id") or "").strip()
    change_navigation = session._resolve_change_navigation_for_file_context_item(focused_item)
    if related_change:
        if change_navigation is not None:
            lines.append(f"- related change: {related_change} ({change_navigation['stack_label']})")
        else:
            lines.append(f"- related change: {related_change}")
    lines.append(f"- diff hunks: {session._file_context_diff_hunk_count(focused_item)}")
    lines.append("- context-only: " + ("yes" if session._file_context_is_context_only(focused_item) else "no"))
    primary_target = focused_item.get("target")
    if isinstance(primary_target, dict):
        lines.append("- primary target: " + session._format_target_summary(primary_target))
    secondary_target = session._file_context_secondary_target(focused_item)
    if isinstance(secondary_target, dict):
        lines.append("- secondary target: " + session._format_target_summary(secondary_target))
    lines.extend(
        session._file_context_navigation_legend_lines(
            primary_target if isinstance(primary_target, dict) else None,
            secondary_target if isinstance(secondary_target, dict) else None,
        )
    )
    if include_next_actions and change_navigation is not None:
        change_actions = [str(change_navigation["change_command"])]
        file_command = str(change_navigation.get("change_file_command") or "").strip()
        if file_command:
            change_actions.append(file_command)
        lines.extend(
            render_action_group_lines(
                {"go_to_change": change_actions},
                heading="- next_actions:",
                line_prefix="  - ",
                ordered_keys=("go_to_change",),
            )
        )
    return lines


def render_resolved_file_context_sections(
    session: Any,
    context_selection: dict[str, Any] | None,
    *,
    focused_title: str = "focused file",
    include_file_context: bool = True,
) -> list[str]:
    if not isinstance(context_selection, dict):
        return []
    payload = context_selection.get("payload")
    if not isinstance(payload, dict):
        return []
    selected_index = int(context_selection.get("selected_index") or 0)
    lines = render_focused_file_context_lines(
        session,
        payload,
        selected_index=selected_index,
        title=focused_title,
    )
    if not include_file_context:
        return lines
    reordered_payload = context_selection.get("reordered_payload")
    if isinstance(reordered_payload, dict):
        file_context_lines = session._render_file_context_lines(
            reordered_payload,
            title="file_context",
        )
        if file_context_lines:
            lines.extend(file_context_lines)
    return lines
