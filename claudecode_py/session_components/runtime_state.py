from __future__ import annotations

from typing import Any


def _make_runtime_state_delegate(method_name: str):
    def delegated(self: "RuntimeStateSessionComponent", *args: Any, **kwargs: Any):
        return getattr(self._session, f"_original_{method_name}")(*args, **kwargs)

    delegated.__name__ = method_name
    delegated.__qualname__ = f"RuntimeStateSessionComponent.{method_name}"
    delegated.__doc__ = (
        f"Delegates RuntimeStateSessionComponent.{method_name} to the preserved "
        "Session implementation."
    )
    return delegated


class RuntimeStateSessionComponent:
    _DELEGATED_METHODS = (
        "tool_result_replacement_surface_payload",
        "tool_result_artifact_surface_payload",
        "tool_schema_surface_payload",
        "system_prompt_surface_payload",
        "build_provider_prompt_assembly",
        "build_provider_prompt_cache_plan",
        "record_prompt_prefix_assembly",
        "prompt_prefix_surface_payload",
        "refresh_runtime_budget_state",
        "runtime_budget_state_payload",
        "runtime_budget_surface_payload",
        "_runtime_narrative_payload",
        "_runtime_budget_narrative_lines",
        "_runtime_compact_lifecycle_narrative_lines",
        "_runtime_progress_narrative_lines",
        "_prompt_prefix_narrative_lines",
        "_runtime_progress_defaults",
        "_runtime_waiting_tool_summary_for_event",
        "_runtime_last_tool_summary_for_event",
        "_runtime_progress_snapshot",
        "_apply_runtime_progress_event_to_snapshot",
        "_record_runtime_progress_event",
        "runtime_progress_surface_payload",
        "_record_runtime_usage_event",
        "should_emit_budget_pressure_event",
        "compaction_policy_payload",
    )

    def __init__(self, session: Any) -> None:
        self._session = session


for _method_name in RuntimeStateSessionComponent._DELEGATED_METHODS:
    setattr(
        RuntimeStateSessionComponent,
        _method_name,
        _make_runtime_state_delegate(_method_name),
    )
