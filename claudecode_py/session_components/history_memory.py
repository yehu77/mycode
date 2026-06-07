from __future__ import annotations

from typing import Any


def _make_history_memory_delegate(method_name: str):
    def delegated(self: "HistoryMemorySessionComponent", *args: Any, **kwargs: Any):
        return getattr(self._session, f"_original_{method_name}")(*args, **kwargs)

    delegated.__name__ = method_name
    delegated.__qualname__ = f"HistoryMemorySessionComponent.{method_name}"
    delegated.__doc__ = (
        f"Delegates HistoryMemorySessionComponent.{method_name} to the preserved "
        "Session implementation."
    )
    return delegated


class HistoryMemorySessionComponent:
    _DELEGATED_METHODS = (
        "describe_history",
        "describe_compact",
        "_history_state_payload",
        "_history_state_lines",
        "_record_history_boundary",
        "_latest_history_boundary",
        "_history_boundary_is_rewindable",
        "_latest_rewindable_boundary_for",
        "_render_history_boundary_summary",
        "_history_boundary_kind_label",
        "_history_boundary_snapshot_available",
        "_history_lifecycle_lines",
        "_history_boundary_preview_lines",
        "_find_history_boundary_by_id",
        "_history_boundary_lineage_summary",
        "_history_boundary_compare_payload",
        "_history_boundary_compare_lines",
        "_history_boundary_restore_effect_lines",
        "_history_boundary_lines_for",
        "_history_boundary_lines",
        "_normalize_memory_operation",
        "_memory_operation_semantics",
        "_memory_operation_payload",
        "_remember_memory_operation",
        "_current_memory_operation_payload",
        "memory_surface_payload",
        "_memory_operation_surface_policy",
        "_apply_memory_operation_surface_policy",
        "_memory_operation_surface_policy_lines",
        "_rewindable_boundaries",
        "_resolve_rewind_boundary",
        "rewind_boundary_preview_payload",
        "describe_rewind",
        "rewind_to_boundary",
        "_history_compaction_event_payload",
        "compact_history_into_context_summary",
        "_history_compaction_keep_last",
        "_history_compaction_preview_payload",
        "_merge_context_summary",
        "apply_history_compaction",
        "_describe_compact_status",
        "_describe_compact_preview",
        "_compact_instruction_lines",
        "_history_section_lines",
        "_history_focus_summary_lines",
    )

    def __init__(self, session: Any) -> None:
        self._session = session


for _method_name in HistoryMemorySessionComponent._DELEGATED_METHODS:
    setattr(
        HistoryMemorySessionComponent,
        _method_name,
        _make_history_memory_delegate(_method_name),
    )
