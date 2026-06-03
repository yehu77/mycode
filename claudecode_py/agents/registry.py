from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True, frozen=True)
class AgentDefinition:
    name: str
    description: str = ""
    based_on: str | None = None
    execution: str | None = None
    model_override: str | None = None
    tool_policy: str | None = None
    project_memory: str = "inherit"
    skills_mode: str = "inherit"
    enabled_skills: tuple[str, ...] = ()
    notes: str = ""
    source: str = "builtin"
    path: Path | None = None

    @property
    def definition_id(self) -> str:
        return f"{self.name}@{self.source}"


@dataclass(slots=True, frozen=True)
class AgentDefinitionDiagnostic:
    name: str
    source: str
    path: Path
    error: str


@dataclass(slots=True, frozen=True)
class ShadowedAgentDefinition:
    name: str
    source: str
    path: Path | None
    shadowed_by_name: str
    shadowed_by_source: str


class AgentDefinitionRegistry:
    def __init__(
        self,
        definitions: list[AgentDefinition] | None = None,
        *,
        diagnostics: list[AgentDefinitionDiagnostic] | None = None,
        shadowed: list[ShadowedAgentDefinition] | None = None,
        source_roots: dict[str, Path | None] | None = None,
    ) -> None:
        self._definitions: dict[str, AgentDefinition] = {}
        self._diagnostics: list[AgentDefinitionDiagnostic] = []
        self._shadowed: list[ShadowedAgentDefinition] = []
        self._source_roots: dict[str, Path | None] = {}
        for source, root in (source_roots or {}).items():
            self.set_source_root(source, root)
        for definition in definitions or []:
            self.add_definition(definition)
        for diagnostic in diagnostics or []:
            self.add_diagnostic(diagnostic)
        for item in shadowed or []:
            self.add_shadowed(item)

    def add_definition(self, definition: AgentDefinition) -> None:
        self._definitions[definition.name] = definition
        self._source_roots.setdefault(definition.source, None)

    def has_definition(self, name: str) -> bool:
        return self.get_definition(name) is not None

    def get_definition(self, name: str) -> AgentDefinition | None:
        resolved = self._resolve_name(name)
        if resolved is None:
            return None
        return self._definitions.get(resolved)

    def list_effective_definitions(self) -> list[AgentDefinition]:
        return [self._definitions[name] for name in sorted(self._definitions)]

    def add_diagnostic(self, diagnostic: AgentDefinitionDiagnostic) -> None:
        self._diagnostics.append(diagnostic)
        self._source_roots.setdefault(diagnostic.source, None)

    def list_diagnostics(self) -> list[AgentDefinitionDiagnostic]:
        return sorted(self._diagnostics, key=lambda item: (item.source, item.name, str(item.path)))

    def add_shadowed(self, definition: ShadowedAgentDefinition) -> None:
        self._shadowed.append(definition)
        self._source_roots.setdefault(definition.source, None)

    def list_shadowed_definitions(self) -> list[ShadowedAgentDefinition]:
        return sorted(self._shadowed, key=lambda item: (item.source, item.name))

    def set_source_root(self, source: str, root: Path | None) -> None:
        self._source_roots[source] = root.resolve() if isinstance(root, Path) else None

    def get_source_root(self, source: str) -> Path | None:
        return self._source_roots.get(source)

    def source_names(self) -> list[str]:
        names = set(self._source_roots)
        names.update(item.source for item in self._definitions.values())
        names.update(item.source for item in self._diagnostics)
        names.update(item.source for item in self._shadowed)
        return sorted(names)

    def known_definition_names(self) -> set[str]:
        return set(self._definitions)

    def _resolve_name(self, value: str) -> str | None:
        raw = value.strip()
        if not raw:
            return None
        if raw in self._definitions:
            return raw
        if raw.endswith("@builtin"):
            candidate = raw.removesuffix("@builtin")
            if candidate in self._definitions:
                return candidate
        if raw.endswith("@project-local"):
            candidate = raw.removesuffix("@project-local")
            if candidate in self._definitions:
                return candidate
        return raw


def merge_agent_registries(*registries: AgentDefinitionRegistry) -> AgentDefinitionRegistry:
    merged = AgentDefinitionRegistry()
    builtin_names: set[str] = set()
    for registry in registries:
        for source in registry.source_names():
            if source not in merged.source_names():
                merged.set_source_root(source, registry.get_source_root(source))
        for definition in registry.list_effective_definitions():
            if definition.source == "builtin":
                builtin_names.add(definition.name)
    for registry in registries:
        for definition in registry.list_effective_definitions():
            if definition.source == "project-local" and definition.based_on and definition.based_on not in builtin_names:
                merged.add_diagnostic(
                    AgentDefinitionDiagnostic(
                        name=definition.name,
                        source=definition.source,
                        path=definition.path or Path(definition.name),
                        error=f'Agent definition field "based_on" references unknown builtin definition "{definition.based_on}".',
                    )
                )
                continue
            existing = merged.get_definition(definition.name)
            if existing is None:
                merged.add_definition(definition)
                continue
            if definition.source == "project-local" and existing.source == "builtin":
                merged.add_shadowed(
                    ShadowedAgentDefinition(
                        name=existing.name,
                        source=existing.source,
                        path=existing.path,
                        shadowed_by_name=definition.name,
                        shadowed_by_source=definition.source,
                    )
                )
                merged.add_definition(definition)
                continue
            merged.add_diagnostic(
                AgentDefinitionDiagnostic(
                    name=definition.name,
                    source=definition.source,
                    path=definition.path or Path(definition.name),
                    error=(
                        f'Agent definition conflicts with already-registered definition '
                        f'"{existing.name}" from source={existing.source}.'
                    ),
                )
            )
        for diagnostic in registry.list_diagnostics():
            merged.add_diagnostic(diagnostic)
    return merged
