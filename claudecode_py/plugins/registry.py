from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..commands.registry import CommandRegistry, ReplCommand
from ..skills.loader import LoadedSkill, ProjectContext

if TYPE_CHECKING:
    from ..state import SessionState


@dataclass(slots=True, frozen=True)
class PluginSkillDefinition:
    name: str
    content: str
    description: str = ""
    auto_enable: bool = False
    tags: tuple[str, ...] = ()
    path: Path | None = None

    def to_loaded_skill(self, *, cwd: Path, plugin_name: str) -> LoadedSkill:
        return LoadedSkill(
            name=self.name,
            path=(
                self.path
                or (cwd / ".pyclaude" / "plugins" / plugin_name / f"{self.name}.md").resolve()
            ),
            content=self.content,
            description=self.description,
            auto_enable=self.auto_enable,
            tags=self.tags,
        )


@dataclass(slots=True, frozen=True)
class PluginMcpServerDefinition:
    name: str
    transport: str
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    auth: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    timeout_sec: float | None = None

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": self.name,
            "transport": self.transport,
        }
        if self.command is not None:
            payload["command"] = self.command
        if self.args:
            payload["args"] = list(self.args)
        if self.env:
            payload["env"] = dict(self.env)
        if self.headers:
            payload["headers"] = dict(self.headers)
        if self.auth:
            payload["auth"] = dict(self.auth)
        if self.cwd is not None:
            payload["cwd"] = self.cwd
        if self.url is not None:
            payload["url"] = self.url
        if self.timeout_sec is not None:
            payload["timeout_sec"] = self.timeout_sec
        return payload


@dataclass(slots=True, frozen=True)
class PluginDefinition:
    name: str
    description: str
    version: str = "0.1.0"
    default_enabled: bool = True
    source: str = "builtin"
    path: Path | None = None
    commands: tuple[ReplCommand, ...] = field(default_factory=tuple)
    skills: tuple[PluginSkillDefinition, ...] = field(default_factory=tuple)
    mcp_servers: tuple[PluginMcpServerDefinition, ...] = field(default_factory=tuple)
    hooks: tuple[str, ...] = field(default_factory=tuple)

    @property
    def plugin_id(self) -> str:
        return f"{self.name}@{self.source}"


@dataclass(slots=True, frozen=True)
class PluginLoadDiagnostic:
    name: str
    source: str
    path: Path
    error: str


class PluginRegistry:
    def __init__(
        self,
        plugins: list[PluginDefinition] | None = None,
        diagnostics: list[PluginLoadDiagnostic] | None = None,
    ) -> None:
        self._plugins: dict[str, PluginDefinition] = {}
        self._diagnostics: dict[str, PluginLoadDiagnostic] = {}
        for plugin in plugins or []:
            self.add_plugin(plugin)
        for diagnostic in diagnostics or []:
            self.add_diagnostic(diagnostic)

    def add_plugin(self, plugin: PluginDefinition) -> None:
        self._plugins[plugin.name] = plugin

    def add_diagnostic(self, diagnostic: PluginLoadDiagnostic) -> None:
        self._diagnostics[diagnostic.name] = diagnostic

    def get_plugin(self, name: str) -> PluginDefinition | None:
        resolved = self._resolve_name(name)
        if resolved is None:
            return None
        return self._plugins.get(resolved)

    def has_plugin(self, name: str) -> bool:
        return self.get_plugin(name) is not None

    def list_plugins(self) -> list[PluginDefinition]:
        return [self._plugins[name] for name in sorted(self._plugins)]

    def list_diagnostics(self) -> list[PluginLoadDiagnostic]:
        return [self._diagnostics[name] for name in sorted(self._diagnostics)]

    def get_diagnostic(self, name: str) -> PluginLoadDiagnostic | None:
        resolved = self._resolve_name(name)
        if resolved is None:
            return None
        return self._diagnostics.get(resolved)

    def enabled_plugins(self, state: "SessionState") -> list[PluginDefinition]:
        return [plugin for plugin in self.list_plugins() if self.is_enabled(plugin.name, state)]

    def disabled_plugins(self, state: "SessionState") -> list[PluginDefinition]:
        return [plugin for plugin in self.list_plugins() if not self.is_enabled(plugin.name, state)]

    def is_enabled(self, name: str, state: "SessionState") -> bool:
        plugin = self.get_plugin(name)
        if plugin is None:
            return False
        if plugin.name in state.disabled_plugin_names:
            return False
        if plugin.name in state.enabled_plugin_names:
            return True
        return plugin.default_enabled

    def build_command_registry(
        self,
        state: "SessionState",
        *,
        base_commands: list[ReplCommand],
    ) -> CommandRegistry:
        registry = CommandRegistry(list(base_commands))
        for plugin in self.enabled_plugins(state):
            for command in plugin.commands:
                registry.add_command(command)
        return registry

    def build_project_context(
        self,
        base_context: ProjectContext,
        state: "SessionState",
        *,
        cwd: Path,
    ) -> ProjectContext:
        skills = list(base_context.skills)
        for plugin in self.enabled_plugins(state):
            for skill in plugin.skills:
                skills.append(skill.to_loaded_skill(cwd=cwd, plugin_name=plugin.name))
        return ProjectContext(
            memory_path=base_context.memory_path,
            memory_content=base_context.memory_content,
            skills=skills,
        )

    def enabled_mcp_server_payloads(self, state: "SessionState") -> list[dict[str, object]]:
        payloads: list[dict[str, object]] = []
        for plugin in self.enabled_plugins(state):
            for server in plugin.mcp_servers:
                payloads.append(server.to_payload())
        return payloads

    def enabled_hook_plugin_names(self, state: "SessionState", hook_name: str) -> list[str]:
        return [
            plugin.name
            for plugin in self.enabled_plugins(state)
            if hook_name in plugin.hooks
        ]

    def describe_plugins(self, state: "SessionState") -> str:
        plugins = self.list_plugins()
        diagnostics = self.list_diagnostics()
        if not plugins and not diagnostics:
            return "No plugins registered."
        lines = []
        for plugin in plugins:
            status = "enabled" if self.is_enabled(plugin.name, state) else "disabled"
            default = "default=enabled" if plugin.default_enabled else "default=disabled"
            path_text = f" path={plugin.path}" if plugin.path is not None else ""
            lines.append(
                f"{plugin.name}: status={status} source={plugin.source} "
                f"version={plugin.version} {default} commands={len(plugin.commands)} "
                f"skills={len(plugin.skills)} mcp_servers={len(plugin.mcp_servers)} hooks={len(plugin.hooks)} "
                f"description={plugin.description}{path_text}"
            )
        for diagnostic in diagnostics:
            lines.append(
                f"{diagnostic.name}: status=invalid source={diagnostic.source} "
                f"path={diagnostic.path} error={diagnostic.error}"
            )
        return "\n".join(lines)

    def describe_plugin(self, name: str, state: "SessionState") -> str:
        plugin = self.get_plugin(name)
        if plugin is None:
            diagnostic = self.get_diagnostic(name)
            if diagnostic is not None:
                return "\n".join(
                    [
                        f"name: {diagnostic.name}",
                        "status: invalid",
                        f"source: {diagnostic.source}",
                        f"path: {diagnostic.path}",
                        f"error: {diagnostic.error}",
                    ]
                )
            return f'Unknown plugin "{name.strip()}".'
        status = "enabled" if self.is_enabled(plugin.name, state) else "disabled"
        default = "enabled" if plugin.default_enabled else "disabled"
        lines = [
            f"name: {plugin.name}",
            f"plugin_id: {plugin.plugin_id}",
            f"status: {status}",
            f"default_enabled: {default}",
            f"version: {plugin.version}",
            f"source: {plugin.source}",
            f"description: {plugin.description}",
            "path: " + (str(plugin.path) if plugin.path is not None else "none"),
            f"commands: {len(plugin.commands)}",
            f"skills: {len(plugin.skills)}",
            f"mcp_servers: {len(plugin.mcp_servers)}",
            f"hooks: {len(plugin.hooks)}",
        ]
        if plugin.commands:
            lines.append("command_names: " + ", ".join(command.name for command in plugin.commands))
        if plugin.skills:
            lines.append("skill_names: " + ", ".join(skill.name for skill in plugin.skills))
        if plugin.mcp_servers:
            lines.append("mcp_server_names: " + ", ".join(server.name for server in plugin.mcp_servers))
        if plugin.hooks:
            lines.append("hook_names: " + ", ".join(plugin.hooks))
        return "\n".join(lines)

    def known_plugin_names(self) -> set[str]:
        return set(self._plugins)

    def _resolve_name(self, value: str) -> str | None:
        raw = value.strip()
        if not raw:
            return None
        if raw in self._plugins:
            return raw
        if raw.endswith("@builtin"):
            candidate = raw.removesuffix("@builtin")
            if candidate in self._plugins:
                return candidate
        return raw


def merge_plugin_registries(*registries: PluginRegistry) -> PluginRegistry:
    merged = PluginRegistry()
    for registry in registries:
        for plugin in registry.list_plugins():
            existing = merged.get_plugin(plugin.name)
            if existing is None:
                merged.add_plugin(plugin)
                continue
            if existing.name == plugin.name:
                merged.add_diagnostic(
                    PluginLoadDiagnostic(
                        name=plugin.name,
                        source=plugin.source,
                        path=plugin.path or Path(plugin.name),
                        error=(
                            f'Plugin name conflicts with already-registered plugin '
                            f'"{existing.name}" from source={existing.source}.'
                        ),
                    )
                )
        for diagnostic in registry.list_diagnostics():
            merged.add_diagnostic(diagnostic)
    return merged
