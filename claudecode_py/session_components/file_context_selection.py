from __future__ import annotations

from typing import Any


def file_context_item_matches_path(
    session: Any,
    item: dict[str, Any],
    *,
    path: str,
    required_reason: str | None = None,
) -> bool:
    normalized_path = str(path or "").strip()
    if not normalized_path or str(item.get("path") or "").strip() != normalized_path:
        return False
    if required_reason is None:
        return True
    reasons = session._file_context_scope_reasons(item)
    return required_reason in reasons


def find_matching_file_context_index(
    session: Any,
    payload: dict[str, Any] | None,
    *,
    path: str,
    required_reason: str | None = None,
) -> int | None:
    if not isinstance(payload, dict):
        return None
    files = [item for item in payload.get("file_context_files", []) if isinstance(item, dict)]
    for index, item in enumerate(files):
        if file_context_item_matches_path(
            session,
            item,
            path=path,
            required_reason=required_reason,
        ):
            return index
    return None


def focused_path_from_payload(session: Any, payload: dict[str, Any] | None) -> str | None:
    _files, _bounded_index, focused_item = session._file_context_items_and_index(payload)
    if focused_item is None:
        return None
    path = str(focused_item.get("path") or "").strip()
    return path or None


def preferred_file_context_index(
    session: Any,
    payload: dict[str, Any] | None,
    *,
    fallback: int = 0,
    preferred_payloads: tuple[dict[str, Any] | None, ...] | None = None,
    required_reason: str | None = None,
) -> int:
    files = [item for item in (payload or {}).get("file_context_files", []) if isinstance(item, dict)]
    if not files:
        return 0
    candidates = preferred_payloads or (
        session._current_change_focus_payload,
        session._current_task_focus_payload,
        session._current_plan_focus_payload,
        session._current_context_focus_payload(),
    )
    for preferred_payload in candidates:
        focused_path = focused_path_from_payload(session, preferred_payload)
        if not focused_path:
            continue
        match_index = find_matching_file_context_index(
            session,
            payload,
            path=focused_path,
            required_reason=required_reason,
        )
        if match_index is None:
            match_index = find_matching_file_context_index(
                session,
                payload,
                path=focused_path,
            )
        if match_index is not None:
            return match_index
    return max(0, min(len(files) - 1, fallback))


def resolve_file_context_selection(
    session: Any,
    payload: dict[str, Any] | None,
    *,
    file_index: int = 0,
    preserve_current_focus: bool = False,
    preferred_payloads: tuple[dict[str, Any] | None, ...] | None = None,
    required_reason: str | None = None,
) -> dict[str, Any]:
    resolved_index = (
        preferred_file_context_index(
            session,
            payload,
            fallback=file_index,
            preferred_payloads=preferred_payloads,
            required_reason=required_reason,
        )
        if preserve_current_focus
        else file_index
    )
    files, bounded_index, focused_item = session._file_context_items_and_index(
        payload,
        selected_index=resolved_index,
    )
    fallback_payload = payload if isinstance(payload, dict) else session._build_file_context_payload([], scope="session")
    reordered_payload = session._reordered_file_context_payload(
        payload,
        selected_index=bounded_index,
    ) or fallback_payload
    return {
        "payload": fallback_payload,
        "reordered_payload": reordered_payload,
        "selected_index": bounded_index,
        "files": files,
        "focused_item": focused_item,
        "file_count": len(files),
    }


def resolve_task_file_context(
    session: Any,
    identifier: str,
    *,
    file_index: int = 0,
    preserve_current_focus: bool = True,
    limit: int = 5,
) -> dict[str, Any]:
    payload = session.task_file_context_payload(identifier, limit=limit)
    return resolve_file_context_selection(
        session,
        payload,
        file_index=file_index,
        preserve_current_focus=preserve_current_focus,
    )


def resolve_selected_change_file_context(
    session: Any,
    *,
    index: int = 0,
    file_index: int = 0,
    limit: int = 10,
    redo: bool = False,
    preserve_current_focus: bool = False,
) -> dict[str, Any]:
    payload = session._selected_change_file_context_payload(index=index, limit=limit, redo=redo)
    return resolve_file_context_selection(
        session,
        payload,
        file_index=file_index,
        preserve_current_focus=preserve_current_focus,
        required_reason="recent change",
    )


def resolve_active_plan_file_context(
    session: Any,
    *,
    identifier: str | None = None,
    file_index: int = 0,
    preserve_current_focus: bool = True,
) -> dict[str, Any]:
    payload = session.active_plan_file_context_payload(identifier)
    return resolve_file_context_selection(
        session,
        payload,
        file_index=file_index,
        preserve_current_focus=preserve_current_focus,
        required_reason="active plan",
    )


def resolve_active_plan_scout_file_context(
    session: Any,
    *,
    selected_index: int = 0,
    file_index: int = 0,
    preserve_current_focus: bool = True,
) -> dict[str, Any]:
    payload = session.active_plan_scout_file_context_payload(selected_index=selected_index)
    return resolve_file_context_selection(
        session,
        payload,
        file_index=file_index,
        preserve_current_focus=preserve_current_focus,
    )


def resolve_active_plan_execution_file_context(
    session: Any,
    *,
    selected_index: int = 0,
    file_index: int = 0,
    preserve_current_focus: bool = True,
) -> dict[str, Any]:
    payload = session.active_plan_execution_file_context_payload(selected_index=selected_index)
    return resolve_file_context_selection(
        session,
        payload,
        file_index=file_index,
        preserve_current_focus=preserve_current_focus,
    )


def preferred_task_file_index(session: Any, identifier: str, *, fallback: int = 0) -> int:
    return int(
        resolve_task_file_context(
            session,
            identifier,
            file_index=fallback,
            preserve_current_focus=True,
            limit=20,
        )["selected_index"]
    )


def preferred_selected_change_file_index(
    session: Any,
    *,
    index: int = 0,
    redo: bool = False,
    limit: int = 10,
    fallback: int = 0,
) -> int:
    return int(
        resolve_selected_change_file_context(
            session,
            index=index,
            file_index=fallback,
            limit=limit,
            redo=redo,
            preserve_current_focus=True,
        )["selected_index"]
    )


def preferred_active_plan_file_index(
    session: Any,
    *,
    identifier: str | None = None,
    fallback: int = 0,
) -> int:
    return int(
        resolve_active_plan_file_context(
            session,
            identifier=identifier,
            file_index=fallback,
            preserve_current_focus=True,
        )["selected_index"]
    )


def preferred_active_plan_scout_file_index(
    session: Any,
    *,
    selected_index: int = 0,
    fallback: int = 0,
) -> int:
    return int(
        resolve_active_plan_scout_file_context(
            session,
            selected_index=selected_index,
            file_index=fallback,
            preserve_current_focus=True,
        )["selected_index"]
    )


def preferred_active_plan_execution_file_index(
    session: Any,
    *,
    selected_index: int = 0,
    fallback: int = 0,
) -> int:
    return int(
        resolve_active_plan_execution_file_context(
            session,
            selected_index=selected_index,
            file_index=fallback,
            preserve_current_focus=True,
        )["selected_index"]
    )
