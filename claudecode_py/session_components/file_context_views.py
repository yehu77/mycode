from __future__ import annotations

from typing import Any


def history_focus_summary_lines(session: Any) -> list[str]:
    payload = session._current_context_focus_payload()
    _files, _bounded_index, focused_item = session._file_context_items_and_index(payload)
    if focused_item is None:
        return []
    path = str(focused_item.get("path") or "").strip()
    if not path:
        return []
    source = str(focused_item.get("source") or "").strip()
    action_groups = session._file_context_item_action_groups(
        focused_item,
        stay_on_surface_actions=[
            "/history changes",
            "/files focused",
            "/diff focused",
            "/status workflow",
        ],
    )
    return session.render_surface_metadata_section(
        "focused file:",
        summary_fields=[
            ("focused file", path),
            ("source", source or None),
            ("diff hunks", session._file_context_diff_hunk_count(focused_item)),
        ],
        action_groups=action_groups,
        action_order=("go_to_change", "go_to_task", "go_to_plan", "stay_on_surface"),
    )


def describe_recent_changes(session: Any, limit: int = 5) -> str:
    changes = session.state.recent_change_sets[-limit:]
    redos = session.state.undone_change_sets[-limit:]
    if not changes and not redos:
        return "No recorded workspace changes."
    lines: list[str] = []
    if changes:
        lines.extend(
            session._render_change_stack_lines(
                list(reversed(changes)),
                title="Undo stack:",
                redo=False,
            )
        )
    if redos:
        if lines:
            lines.append("")
        lines.extend(
            session._render_change_stack_lines(
                list(reversed(redos)),
                title="Redo stack:",
                include_file_preview=False,
                redo=True,
            )
        )
    working_set_lines = session._render_file_context_lines(
        session.working_set_payload(limit=limit),
        title="Working set",
    )
    if working_set_lines:
        if lines:
            lines.append("")
        lines.extend(working_set_lines)
    return "\n".join(lines)


def describe_working_set(session: Any, limit: int = 5) -> str:
    payload = session.working_set_payload(limit=limit)
    files = [item for item in payload.get("file_context_files", []) if isinstance(item, dict)]
    rendered_working_set = session._render_file_context_lines(
        payload,
        title="Working set",
    )
    if not rendered_working_set:
        return "\n".join(
            [
                "Working set:",
                "- file_count: 0",
            ]
        )
    lines = [
        "working set compare:",
        *session._explicit_context_compare_summary_lines(
            files=files,
            total_file_count=len(files),
        ),
        "",
    ]
    lines.extend(rendered_working_set)
    return "\n".join(lines)


def render_context_inventory_text(
    session: Any,
    payload: dict[str, Any] | None,
    *,
    filter_label: str,
) -> str:
    files = [item for item in (payload or {}).get("file_context_files", []) if isinstance(item, dict)]
    lines = [
        "working set:",
        f"filter: {filter_label}",
        f"file_count: {len(files)}",
    ]
    if not files:
        return "\n".join(lines + ["No matching working-set files."])
    for index, item in enumerate(files, start=1):
        lines.append("")
        lines.append(f"{index}. {item['path']}")
        scope_reasons = session._file_context_scope_reasons(item)
        if scope_reasons:
            lines.append("- in scope because: " + ", ".join(scope_reasons))
        related_change = str(item.get("change_id") or "").strip()
        change_navigation = session._resolve_change_navigation_for_file_context_item(item)
        if related_change:
            if change_navigation is not None:
                lines.append(f"- related change: {related_change} ({change_navigation['stack_label']})")
            else:
                lines.append(f"- related change: {related_change}")
        else:
            lines.append("- related change: none")
        lines.append(f"- diff hunks: {session._file_context_diff_hunk_count(item)}")
        lines.append("- context-only: " + ("yes" if session._file_context_is_context_only(item) else "no"))
        primary_target = item.get("target")
        if isinstance(primary_target, dict):
            lines.append("- primary target: " + session._format_target_summary(primary_target))
        else:
            lines.append("- primary target: none")
        secondary_target = session._file_context_secondary_target(item)
        if isinstance(secondary_target, dict):
            lines.append("- secondary target: " + session._format_target_summary(secondary_target))
        else:
            lines.append("- secondary target: none")
        lines.extend(
            session._render_file_context_action_group_lines(
                item,
                stay_on_surface_actions=session._context_stay_on_surface_actions(),
                line_prefix="- ",
                ordered_keys=("go_to_change", "go_to_task", "go_to_plan", "stay_on_surface"),
            )
        )
    return "\n".join(lines)


def describe_context_inventory(session: Any) -> str:
    payload = session.working_set_payload(limit=20)
    return session._render_context_inventory_text(payload, filter_label="all")


def describe_context_filtered(session: Any, *, reason: str, label: str) -> str:
    payload = session._filtered_working_set_payload(reason=reason)
    return session._render_context_inventory_text(payload, filter_label=label)


def describe_context_auto(session: Any) -> str:
    payload = session.working_set_payload(limit=20)
    files = [item for item in payload.get("file_context_files", []) if isinstance(item, dict)]
    filtered = [
        item
        for item in files
        if "explicit context path" not in session._file_context_scope_reasons(item)
    ]
    return session._render_context_inventory_text(
        session._file_context_payload_from_files(filtered, scope="session"),
        filter_label="auto",
    )


def describe_context_focused(session: Any) -> str:
    payload = session._current_context_focus_payload()
    files, _index, focused_item = session._file_context_items_and_index(payload)
    if not files or focused_item is None:
        return "\n".join(
            [
                "focused file:",
                "No focused file context.",
                "next actions:",
                "- /files context",
                "- /status workflow",
            ]
        )
    lines = session._render_context_focused_lines(payload, title="focused file")
    return "\n".join(lines) if lines else "No focused file context."


def working_set_files_payload(session: Any) -> dict[str, Any]:
    return session.working_set_payload(limit=20)


def diff_working_set_payload(session: Any) -> dict[str, Any]:
    payload = session._working_set_files_payload()
    files = [item for item in payload.get("file_context_files", []) if isinstance(item, dict)]
    filtered = [
        item
        for item in files
        if bool(item.get("has_diff_hunks")) or session._file_context_diff_hunk_count(item) > 0
    ]
    return session._file_context_payload_from_files(filtered, scope="session")


def render_files_inventory_text(
    session: Any,
    payload: dict[str, Any] | None,
    *,
    filter_label: str,
    summary_lines: list[str] | None = None,
) -> str:
    files = [item for item in (payload or {}).get("file_context_files", []) if isinstance(item, dict)]
    lines = [
        "working set:",
        f"filter: {filter_label}",
        f"file_count: {len(files)}",
    ]
    if summary_lines:
        lines.extend(summary_lines)
    if not files:
        return "\n".join(lines + ["No matching working-set files."])
    lines.append(session._render_file_context_mix_line(files))
    for index, item in enumerate(files, start=1):
        scope_reasons = session._file_context_scope_reasons(item)
        related_change = str(item.get("change_id") or "").strip() or "none"
        lines.append(
            f"{index}. {item['path']}  "
            + f"in_scope_because={', '.join(scope_reasons) if scope_reasons else 'none'}  "
            + f"context_origin={session._file_context_origin_label(item)}  "
            + f"related_change={related_change}  "
            + f"diff_hunks={session._file_context_diff_hunk_count(item)}  "
            + f"context_only={'yes' if session._file_context_is_context_only(item) else 'no'}"
        )
        lines.append(
            "   next_actions: "
            + session._render_file_surface_action_group_summary(
                item,
                stay_on_surface_actions=session._files_stay_on_surface_actions(),
                inspect_focused_file_actions=[f"/files show {index}"],
                inspect_explicit_context_actions=session._file_context_context_actions(item),
                ordered_keys=(
                    "inspect_focused_file",
                    "inspect_change",
                    "inspect_task",
                    "inspect_active_plan",
                    "inspect_explicit_context",
                    "stay_on_surface",
                ),
            )
        )
    return "\n".join(lines)


def describe_files_inventory(session: Any) -> str:
    return session._render_files_inventory_text(
        session._working_set_files_payload(),
        filter_label="all",
    )


def describe_files_changes(session: Any) -> str:
    payload = session._working_set_files_payload()
    files = [item for item in payload.get("file_context_files", []) if isinstance(item, dict)]
    filtered = [
        item
        for item in files
        if bool(item.get("has_related_change")) or bool(item.get("has_diff_hunks"))
    ]
    filtered_payload = session._reorder_payload_to_current_focus(
        session._file_context_payload_from_files(filtered, scope="session"),
        required_reason="recent change",
    )
    return session._render_files_inventory_text(
        filtered_payload,
        filter_label="changes",
    )


def describe_files_filtered(session: Any, *, reason: str, label: str) -> str:
    payload = session._reorder_payload_to_current_focus(
        session._filtered_working_set_payload(reason=reason),
        required_reason=reason,
    )
    summary_lines: list[str] | None = None
    if reason == "explicit context path":
        full_payload = session._working_set_files_payload()
        full_files = [item for item in full_payload.get("file_context_files", []) if isinstance(item, dict)]
        summary_lines = session._explicit_context_compare_summary_lines(
            files=full_files,
            total_file_count=len(full_files),
        )
    return session._render_files_inventory_text(
        payload,
        filter_label=label,
        summary_lines=summary_lines,
    )


def describe_files_auto(session: Any) -> str:
    payload = session._working_set_files_payload()
    files = [item for item in payload.get("file_context_files", []) if isinstance(item, dict)]
    filtered = [
        item
        for item in files
        if "explicit context path" not in session._file_context_scope_reasons(item)
    ]
    filtered_payload = session._reorder_payload_to_current_focus(
        session._file_context_payload_from_files(filtered, scope="session"),
    )
    return session._render_files_inventory_text(
        filtered_payload,
        filter_label="auto",
        summary_lines=session._explicit_context_compare_summary_lines(
            files=files,
            total_file_count=len(files),
        ),
    )


def describe_files_focused(session: Any) -> str:
    payload = session._current_context_focus_payload()
    files, _index, focused_item = session._file_context_items_and_index(payload)
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
    lines = session.render_focused_file_context_lines(
        payload,
        title="focused file",
        include_next_actions=False,
    )
    lines.extend(
        session._render_file_surface_action_group_lines(
            focused_item,
            stay_on_surface_actions=session._files_stay_on_surface_actions(),
            inspect_focused_diff_actions=["/diff focused"],
            inspect_explicit_context_actions=session._file_context_context_actions(focused_item),
            ordered_keys=(
                "inspect_focused_diff",
                "inspect_change",
                "inspect_task",
                "inspect_active_plan",
                "inspect_explicit_context",
                "stay_on_surface",
            ),
        )
    )
    return "\n".join(lines) if lines else "No focused file context."


def describe_files_show(session: Any, *, selected_index: int) -> str:
    payload = session._working_set_files_payload()
    files, bounded_index, focused_item = session._file_context_items_and_index(
        payload,
        selected_index=selected_index,
    )
    usage = "Usage: /files [context|working-set|focused|changes|tasks|plan|explicit|auto|show <n>]"
    if not files or focused_item is None or selected_index >= len(files):
        return usage
    reordered = session._reordered_file_context_payload(payload, selected_index=selected_index)
    lines = session.render_focused_file_context_lines(
        reordered,
        title="focused file",
        include_next_actions=False,
    )
    lines.extend(
        session._render_file_surface_action_group_lines(
            focused_item,
            stay_on_surface_actions=[
                f"/files show {bounded_index + 1}",
                *session._files_stay_on_surface_actions(),
            ],
            inspect_focused_diff_actions=["/diff focused"],
            inspect_explicit_context_actions=session._file_context_context_actions(focused_item),
            ordered_keys=(
                "inspect_focused_diff",
                "inspect_change",
                "inspect_task",
                "inspect_active_plan",
                "inspect_explicit_context",
                "stay_on_surface",
            ),
        )
    )
    return "\n".join(lines) if lines else usage


def describe_diff_summary(session: Any) -> str:
    payload = session._working_set_files_payload()
    files = [item for item in payload.get("file_context_files", []) if isinstance(item, dict)]
    diff_backed_count = sum(
        1 for item in files if bool(item.get("has_diff_hunks")) or session._file_context_diff_hunk_count(item) > 0
    )
    focused_payload = session._current_context_focus_payload()
    _focused_files, _focused_index, focused_item = session._file_context_items_and_index(focused_payload)
    focused_path = str(focused_item.get("path") or "none") if focused_item is not None else "none"
    focused_diff_hunks = session._file_context_diff_hunk_count(focused_item) if focused_item is not None else 0
    action_groups = {
        "inspect_focused_diff": ["/diff focused"],
        "inspect_focused_file": ["/files focused"],
        "inspect_change": ["/changes working-set"],
        "stay_on_surface": ["/diff working-set"],
    }
    lines = [
        "diff summary:",
        f"recorded undo-stack changes: {len(session.state.recent_change_sets)}",
        f"recorded redo-stack changes: {len(session.state.undone_change_sets)}",
        f"working set diff-backed files: {diff_backed_count}",
        session._render_file_context_mix_line(files),
        f"focused file: {focused_path}",
        f"focused diff hunks: {focused_diff_hunks}",
    ]
    lines.extend(
        session.render_workflow_action_sections(
            action_groups,
            ordered_keys=("inspect_focused_diff", "inspect_focused_file", "inspect_change", "stay_on_surface"),
        )
    )
    return "\n".join(lines)


def describe_diff_focused(session: Any) -> str:
    payload = session._current_context_focus_payload()
    files, _index, focused_item = session._file_context_items_and_index(payload)
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
    lines = session.render_focused_file_context_lines(
        payload,
        title="focused file",
        include_next_actions=False,
    )
    lines.extend(
        session._render_file_surface_action_group_lines(
            focused_item,
            stay_on_surface_actions=session._diff_stay_on_surface_actions(),
            inspect_focused_file_actions=["/files focused"],
            ordered_keys=(
                "inspect_focused_file",
                "inspect_change",
                "inspect_task",
                "inspect_active_plan",
                "stay_on_surface",
            ),
        )
    )
    if session._file_context_diff_hunk_count(focused_item) <= 0:
        lines.append("- diff status: no diff hunks on focused file")
        return "\n".join(lines)
    return "\n".join(lines)


def describe_diff_working_set(session: Any) -> str:
    payload = session._reorder_payload_to_current_focus(
        session._diff_working_set_payload(),
        required_reason="recent change",
    )
    working_set_payload = session._working_set_files_payload()
    files = [item for item in payload.get("file_context_files", []) if isinstance(item, dict)]
    lines = [
        "working set diff:",
        f"file_count: {len(files)}",
    ]
    if not files:
        return "\n".join(lines + ["No diff-backed working-set files."])
    lines.append(session._render_file_context_mix_line(files))
    for index, item in enumerate(files, start=1):
        related_change = str(item.get("change_id") or "").strip() or "none"
        working_set_index = session._find_matching_file_context_index(
            working_set_payload,
            path=str(item.get("path") or ""),
        )
        inspect_focused_file_actions = (
            [f"/files show {working_set_index + 1}"]
            if working_set_index is not None
            else []
        )
        lines.append(f"{index}. {item['path']}")
        lines.append(f"- related change: {related_change}")
        lines.append(f"- diff hunks: {session._file_context_diff_hunk_count(item)}")
        lines.extend(
            session._render_file_surface_action_group_lines(
                item,
                stay_on_surface_actions=session._diff_stay_on_surface_actions(),
                inspect_focused_file_actions=inspect_focused_file_actions,
                line_prefix="- ",
                ordered_keys=(
                    "inspect_focused_file",
                    "inspect_change",
                    "inspect_task",
                    "inspect_active_plan",
                    "stay_on_surface",
                ),
            )
        )
        if index < len(files):
            lines.append("")
    return "\n".join(lines)
