from __future__ import annotations

from typing import Any


def _make_project_context_delegate(method_name: str):
    def delegated(self: "ProjectContextSessionComponent", *args: Any, **kwargs: Any):
        return getattr(self._session, f"_original_{method_name}")(*args, **kwargs)

    delegated.__name__ = method_name
    delegated.__qualname__ = f"ProjectContextSessionComponent.{method_name}"
    delegated.__doc__ = (
        f"Delegates ProjectContextSessionComponent.{method_name} to the preserved "
        "Session implementation."
    )
    return delegated


class ProjectContextSessionComponent:
    _DELEGATED_METHODS = (
        "describe_project_context",
        "_describe_project_context_summary",
        "describe_project_memory",
        "_loaded_skill_status_parts",
        "_loaded_skill_source_payload",
        "_loaded_skill_manual_override_state",
        "_loaded_skill_payload",
        "_loaded_skill_registry_payload",
        "_skill_registry_summary_lines",
        "_skill_diagnostic_lines",
        "_skill_registry_next_actions",
        "skills_surface_payload",
        "_skill_toggle_command",
        "_loaded_skill_groups",
        "_render_loaded_skill_lines",
        "describe_loaded_skills",
        "_plugin_contribution_labels",
        "_plugin_source_label",
        "_plugin_reload_state_payload",
        "plugin_surface_payload",
        "_plugin_toggle_command",
        "describe_plugins",
        "describe_plugin",
        "_project_context_reload_snapshot",
        "_record_project_context_reload_result",
        "_last_project_context_reload_summary_line",
        "_describe_project_context_reload_status",
        "_skill_reload_state_payload",
        "_yes_no",
        "reload_project_context",
    )

    def __init__(self, session: Any) -> None:
        self._session = session


for _method_name in ProjectContextSessionComponent._DELEGATED_METHODS:
    setattr(
        ProjectContextSessionComponent,
        _method_name,
        _make_project_context_delegate(_method_name),
    )
