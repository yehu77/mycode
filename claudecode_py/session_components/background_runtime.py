from __future__ import annotations

from typing import Any


def _make_background_runtime_delegate(method_name: str):
    def delegated(self: "BackgroundRuntimeSessionComponent", *args: Any, **kwargs: Any):
        return getattr(self._session, f"_original_{method_name}")(*args, **kwargs)

    delegated.__name__ = method_name
    delegated.__qualname__ = f"BackgroundRuntimeSessionComponent.{method_name}"
    delegated.__doc__ = (
        f"Delegates BackgroundRuntimeSessionComponent.{method_name} to the preserved "
        "Session implementation."
    )
    return delegated


class BackgroundRuntimeSessionComponent:
    _DELEGATED_METHODS = (
        "background_surface_payload",
        "background_registry_payload",
        "background_handoff_payload",
        "_background_runtime_progress_metadata",
        "_background_recent_activity_from_metadata",
        "_background_last_tool_summary_for_event",
        "_background_waiting_tool_summary_for_event",
        "_compact_runtime_progress_text",
        "_estimate_runtime_progress_tokens",
    )

    def __init__(self, session: Any) -> None:
        self._session = session


for _method_name in BackgroundRuntimeSessionComponent._DELEGATED_METHODS:
    setattr(
        BackgroundRuntimeSessionComponent,
        _method_name,
        _make_background_runtime_delegate(_method_name),
    )
