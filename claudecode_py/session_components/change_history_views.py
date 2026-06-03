from __future__ import annotations

from typing import Any


def history_section_lines(session: Any, *, limit: int = 12) -> dict[str, list[str]]:
    audit_lines = session._recent_workspace_audit_history_lines()
    task_history_lines = session._recent_task_activity_lines()
    visible_messages = session.state.messages[-limit:]
    history_state = session._history_state_payload(
        message_count=len(session.state.messages),
        context_summary=session.state.context_summary,
    )
    message_lines: list[str] = []
    message_lines.extend(session._history_state_lines(history_state, include_summary_preview=True))
    boundary_lines = session._history_boundary_lines(limit=min(limit, 4))
    if boundary_lines:
        message_lines.append("")
        message_lines.extend(boundary_lines)
        guidance_lines = session._history_rewind_guidance_lines()
        if guidance_lines:
            message_lines.append("")
            message_lines.extend(guidance_lines)
    for index, message in enumerate(visible_messages, start=1):
        role = message.get("role", "unknown")
        summary = session._summarize_message(message)
        message_lines.append(f"{index}. {role}: {summary}")
    if not visible_messages:
        if history_state["history_cleared"]:
            message_lines.append("History has been cleared.")
        else:
            message_lines.append("No active messages in current history.")
    change_lines: list[str] = []
    undo_text = session.describe_change_stack(limit=min(limit, 5))
    if undo_text != "No recorded workspace changes.":
        change_lines.extend(["recent changes:", *undo_text.splitlines()])
    working_set_text = session.describe_working_set(limit=min(limit, 5))
    if working_set_text:
        if change_lines:
            change_lines.append("")
        change_lines.extend(working_set_text.splitlines())
    focused_lines = session._history_focus_summary_lines()
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


def render_selected_change_next_action_lines(
    session: Any,
    selected: Any,
    *,
    focused_path: str,
    file_index: int,
    redo: bool,
) -> list[str]:
    selector = selected.change_id[:8]
    command_prefix = "/changes show redo " if redo else "/changes show "
    base_command = f"{command_prefix}{selector}"
    stay_actions = session._dedupe_action_commands(
        [base_command, f"{base_command} file {file_index + 1}", "/changes working-set"]
    )
    working_set_index = session._find_matching_file_context_index(
        session._working_set_files_payload(),
        path=focused_path,
    )
    inspect_focused_file_actions = (
        [f"/files show {working_set_index + 1}"]
        if working_set_index is not None
        else ["/files focused"]
    )
    inspect_focused_diff_actions = [f"{base_command} file {file_index + 1}"]
    task_actions = session._related_task_commands_for_change_path(focused_path)
    plan_actions = session._related_plan_commands_for_change_path(focused_path)
    return [
        *session._render_action_group_lines(
            {
                "inspect_focused_file": inspect_focused_file_actions,
                "inspect_focused_diff": inspect_focused_diff_actions,
                "inspect_task": task_actions,
                "inspect_active_plan": plan_actions,
                "stay_on_surface": stay_actions,
            },
            ordered_keys=(
                "inspect_focused_file",
                "inspect_focused_diff",
                "inspect_task",
                "inspect_active_plan",
                "stay_on_surface",
            ),
        )
    ]


def visible_change_stack(
    session: Any,
    *,
    redo: bool = False,
    limit: int | None = None,
) -> list[Any]:
    stack = session.state.undone_change_sets if redo else session.state.recent_change_sets
    if limit is None:
        return list(reversed(stack))
    return list(reversed(stack[-limit:]))


def change_stack_entry_action_groups(
    session: Any,
    *,
    selected_index: int,
    limit: int,
    redo: bool,
) -> dict[str, list[str]]:
    visible = session._visible_change_stack(redo=redo, limit=limit)
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
    payload = session.selected_change_detail_metadata(
        index=bounded_index,
        limit=limit,
        redo=redo,
        preserve_current_focus=True,
    )
    _files, focused_index, focused_item = session._file_context_items_and_index(payload)
    stay_actions = [base_command, "/changes list", "/changes working-set"]
    if focused_item is None:
        return {
            "go_to_change": [base_command],
            "go_to_task": [],
            "go_to_plan": [],
            "stay_on_surface": session._dedupe_action_commands(stay_actions),
        }
    stay_actions.insert(1, f"{base_command} file {focused_index + 1}")
    return session._file_context_item_action_groups(
        focused_item,
        stay_on_surface_actions=stay_actions,
    )


def render_change_stack_lines(
    session: Any,
    changes: list[Any],
    *,
    title: str,
    include_file_preview: bool = True,
    redo: bool = False,
) -> list[str]:
    lines = [title]
    for index, change in enumerate(changes, start=1):
        visible_files = session._visible_change_files(change)
        lines.append(
            f"{index}. {change.change_id}  tool={change.tool_name}  files={len(visible_files)}  "
            f"created={change.created_at}  kind={change.change_kind}  "
            f"undoable={'yes' if change.undoable else 'no'}"
        )
        lines.append(f"   summary: {change.summary}")
        if include_file_preview:
            for file_change in visible_files[:3]:
                lines.append("   - " + session._render_file_change_summary(file_change))
            if len(visible_files) > 3:
                lines.append(f"   - ... {len(visible_files) - 3} more file(s)")
        lines.extend(
            "   " + line
            for line in session._render_action_group_lines(
                session._change_stack_entry_action_groups(
                    selected_index=index - 1,
                    limit=len(changes),
                    redo=redo,
                ),
                line_prefix="- ",
                ordered_keys=("go_to_change", "go_to_task", "go_to_plan", "stay_on_surface"),
            )
        )
    return lines


def describe_change_stack(session: Any, *, redo: bool = False, limit: int = 5) -> str:
    stack = session._visible_change_stack(redo=redo, limit=limit)
    if not stack:
        return "No undone workspace changes." if redo else "No recorded workspace changes."
    lines = session._render_change_stack_lines(
        stack,
        title="Redo stack:" if redo else "Undo stack:",
        include_file_preview=not redo,
        redo=redo,
    )
    return "\n".join(lines)


def resolve_change_stack_index(
    session: Any,
    selector: str,
    *,
    redo: bool = False,
    limit: int | None = None,
) -> int | None:
    raw = selector.strip()
    if not raw:
        return None
    visible = session._visible_change_stack(redo=redo, limit=limit)
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


def recent_change_entries(session: Any, limit: int = 5) -> list[str]:
    changes = list(reversed(session.state.recent_change_sets[-limit:]))
    return [session._render_change_entry(change) for change in changes]


def recent_redo_entries(session: Any, limit: int = 5) -> list[str]:
    changes = list(reversed(session.state.undone_change_sets[-limit:]))
    return [session._render_change_entry(change) for change in changes]


def selected_change_file_count(session: Any, *, index: int = 0, limit: int = 5, redo: bool = False) -> int:
    visible = session._visible_change_stack(redo=redo, limit=limit)
    if not visible:
        return 0
    selected = visible[max(0, min(index, len(visible) - 1))]
    return len(session._visible_change_files(selected))


def selected_change_detail(
    session: Any,
    *,
    index: int = 0,
    file_index: int = 0,
    limit: int = 5,
    redo: bool = False,
    preserve_current_focus: bool = False,
) -> str:
    visible = session._visible_change_stack(redo=redo, limit=limit)
    if not visible:
        return "No selected change."
    selected = visible[max(0, min(index, len(visible) - 1))]
    counts = session._count_change_actions(selected)
    visible_files = session._visible_change_files(selected)
    resolved_file_index = file_index
    if visible_files and preserve_current_focus:
        resolved_file_index = session.preferred_selected_change_file_index(
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
            lines.append(f"{marker} {current_index}. {session._describe_file_change(file_change)}")
        if len(visible_files) > 8:
            lines.append(f"  ... {len(visible_files) - 8} more file(s)")
        focused = visible_files[clamped_file_index]
        lines.append("")
        lines.append(f"Focused file ({clamped_file_index + 1}/{len(visible_files)})")
        lines.append(session._render_file_change_detail(focused))
        metadata = session.selected_change_detail_metadata(
            index=index,
            file_index=clamped_file_index,
            limit=limit,
            redo=redo,
            preserve_current_focus=False,
        )
        context_lines = session._render_file_context_lines(
            metadata,
            title="Focused file context",
        )
        if context_lines:
            lines.append("")
            lines.extend(context_lines)
        lines.append("")
        lines.extend(
            session._render_selected_change_next_action_lines(
                selected,
                focused_path=focused.path,
                file_index=clamped_file_index,
                redo=redo,
            )
        )
    return "\n".join(lines)
