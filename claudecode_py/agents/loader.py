from __future__ import annotations

import json
from pathlib import Path

from .registry import AgentDefinition, AgentDefinitionDiagnostic, AgentDefinitionRegistry

_AGENT_EXTENSION = ".json"
_ALLOWED_EXECUTION = {"foreground", "background-session", "background-session+snapshot", "child-session"}
_ALLOWED_PROJECT_MEMORY = {"inherit", "disable"}
_ALLOWED_SKILLS_MODE = {"inherit", "explicit"}


def default_external_agents_dir(cwd: Path) -> Path:
    return cwd / ".pyclaude" / "agents"


def load_project_local_agent_registry(cwd: Path) -> AgentDefinitionRegistry:
    registry = AgentDefinitionRegistry()
    agents_dir = default_external_agents_dir(cwd)
    registry.set_source_root("project-local", agents_dir.resolve() if agents_dir.exists() else agents_dir)
    if not agents_dir.exists() or not agents_dir.is_dir():
        return registry
    for path in sorted(agents_dir.glob(f"*{_AGENT_EXTENSION}")):
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            definition = _parse_agent_definition(path.resolve(), payload)
        except Exception as exc:  # noqa: BLE001
            registry.add_diagnostic(
                AgentDefinitionDiagnostic(
                    name=path.stem or "(unnamed)",
                    source="project-local",
                    path=path.resolve(),
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        if registry.has_definition(definition.name):
            registry.add_diagnostic(
                AgentDefinitionDiagnostic(
                    name=definition.name,
                    source="project-local",
                    path=path.resolve(),
                    error="Duplicate project-local agent definition name.",
                )
            )
            continue
        registry.add_definition(definition)
    return registry


def _parse_agent_definition(path: Path, payload: object) -> AgentDefinition:
    if not isinstance(payload, dict):
        raise ValueError("Agent definition manifest must be a JSON object.")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError('Agent definition field "name" cannot be empty.')
    description = _optional_string(payload.get("description")) or ""
    based_on = _optional_string(payload.get("based_on"))
    execution = _optional_string(payload.get("execution"))
    if execution is not None and execution not in _ALLOWED_EXECUTION:
        raise ValueError(
            f'Agent definition field "execution" must be one of: {", ".join(sorted(_ALLOWED_EXECUTION))}.'
        )
    project_memory = _optional_string(payload.get("project_memory")) or "inherit"
    if project_memory not in _ALLOWED_PROJECT_MEMORY:
        raise ValueError(
            f'Agent definition field "project_memory" must be one of: {", ".join(sorted(_ALLOWED_PROJECT_MEMORY))}.'
        )
    skills_mode = _optional_string(payload.get("skills_mode")) or "inherit"
    if skills_mode not in _ALLOWED_SKILLS_MODE:
        raise ValueError(
            f'Agent definition field "skills_mode" must be one of: {", ".join(sorted(_ALLOWED_SKILLS_MODE))}.'
        )
    enabled_skills = _parse_string_list(payload.get("enabled_skills"), field="enabled_skills")
    if enabled_skills and skills_mode != "explicit":
        raise ValueError('Agent definition field "enabled_skills" requires skills_mode="explicit".')
    return AgentDefinition(
        name=name,
        description=description,
        based_on=based_on,
        execution=execution,
        model_override=_optional_string(payload.get("model_override")),
        tool_policy=_optional_string(payload.get("tool_policy")),
        project_memory=project_memory,
        skills_mode=skills_mode,
        enabled_skills=enabled_skills,
        notes=_optional_string(payload.get("notes")) or "",
        source="project-local",
        path=path,
    )


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_string_list(value: object, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f'Agent definition field "{field}" must be a list of strings.')
    items: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            items.append(text)
    return tuple(items)
