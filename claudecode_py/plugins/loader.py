from __future__ import annotations

from pathlib import Path
import json

from .registry import (
    PluginDefinition,
    PluginLoadDiagnostic,
    PluginMcpServerDefinition,
    PluginRegistry,
    PluginSkillDefinition,
)

_PLUGIN_MANIFEST = "plugin.json"


def default_external_plugins_dir(cwd: Path) -> Path:
    return cwd / ".pyclaude" / "plugins"


def load_project_local_plugin_registry(cwd: Path) -> PluginRegistry:
    registry = PluginRegistry()
    plugins_dir = default_external_plugins_dir(cwd)
    if not plugins_dir.exists() or not plugins_dir.is_dir():
        return registry
    for plugin_dir in sorted(plugins_dir.iterdir()):
        if not plugin_dir.is_dir():
            continue
        plugin_name = plugin_dir.name.strip() or "(unnamed)"
        manifest_path = plugin_dir / _PLUGIN_MANIFEST
        if not manifest_path.exists() or not manifest_path.is_file():
            registry.add_diagnostic(
                PluginLoadDiagnostic(
                    name=plugin_name,
                    source="external",
                    path=plugin_dir.resolve(),
                    error=f'Missing required manifest "{_PLUGIN_MANIFEST}".',
                )
            )
            continue
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            plugin = _parse_plugin_definition(plugin_dir.resolve(), manifest_path.resolve(), payload)
        except Exception as exc:  # noqa: BLE001
            registry.add_diagnostic(
                PluginLoadDiagnostic(
                    name=plugin_name,
                    source="external",
                    path=plugin_dir.resolve(),
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        registry.add_plugin(plugin)
    return registry


def _parse_plugin_definition(
    plugin_dir: Path,
    manifest_path: Path,
    payload: object,
) -> PluginDefinition:
    if not isinstance(payload, dict):
        raise ValueError("Plugin manifest must be a JSON object.")
    name = str(payload.get("name") or plugin_dir.name).strip()
    if not name:
        raise ValueError('Plugin manifest field "name" cannot be empty.')
    description = str(payload.get("description") or "").strip()
    version = str(payload.get("version") or "0.1.0").strip() or "0.1.0"
    skills = tuple(_parse_skill_definitions(manifest_path, payload.get("skills")))
    mcp_servers = tuple(_parse_mcp_servers(plugin_dir, payload.get("mcp_servers")))
    hooks = _parse_hooks(payload.get("hooks"))
    return PluginDefinition(
        name=name,
        description=description,
        version=version,
        source="external",
        path=plugin_dir,
        skills=skills,
        mcp_servers=mcp_servers,
        hooks=hooks,
    )


def _parse_skill_definitions(manifest_path: Path, payload: object) -> list[PluginSkillDefinition]:
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise ValueError('Plugin manifest field "skills" must be a list.')
    skills: list[PluginSkillDefinition] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise ValueError(f'Plugin skill #{index} must be an object.')
        name = str(item.get("name") or "").strip()
        if not name:
            raise ValueError(f'Plugin skill #{index} is missing "name".')
        content = str(item.get("content") or "").strip()
        if not content:
            raise ValueError(f'Plugin skill "{name}" is missing "content".')
        description = str(item.get("description") or "").strip()
        auto_enable = _parse_bool(item.get("auto_enable", False), field=f"skills[{index}].auto_enable")
        tags = _parse_tags(item.get("tags"), field=f"skills[{index}].tags")
        skills.append(
            PluginSkillDefinition(
                name=name,
                content=content,
                description=description,
                auto_enable=auto_enable,
                tags=tags,
                path=manifest_path,
            )
        )
    return skills


def _parse_mcp_servers(plugin_dir: Path, payload: object) -> list[PluginMcpServerDefinition]:
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise ValueError('Plugin manifest field "mcp_servers" must be a list.')
    servers: list[PluginMcpServerDefinition] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise ValueError(f'MCP server #{index} must be an object.')
        name = str(item.get("name") or "").strip()
        transport = str(item.get("transport") or "").strip()
        if not name:
            raise ValueError(f'MCP server #{index} is missing "name".')
        if not transport:
            raise ValueError(f'MCP server "{name}" is missing "transport".')
        cwd_value = item.get("cwd")
        resolved_cwd: str | None = None
        if cwd_value is None:
            if transport == "stdio":
                resolved_cwd = str(plugin_dir)
        else:
            raw_cwd = str(cwd_value).strip()
            if raw_cwd:
                resolved_cwd = str((plugin_dir / raw_cwd).resolve())
        servers.append(
            PluginMcpServerDefinition(
                name=name,
                transport=transport,
                command=_optional_string(item.get("command")),
                args=_parse_string_list(item.get("args"), field=f"mcp_servers[{index}].args"),
                env=_parse_string_map(item.get("env"), field=f"mcp_servers[{index}].env"),
                headers=_parse_string_map(item.get("headers"), field=f"mcp_servers[{index}].headers"),
                auth=_parse_string_map(item.get("auth"), field=f"mcp_servers[{index}].auth"),
                cwd=resolved_cwd,
                url=_optional_string(item.get("url")),
                timeout_sec=_optional_float(item.get("timeout_sec"), field=f"mcp_servers[{index}].timeout_sec"),
            )
        )
    return servers


def _parse_hooks(payload: object) -> tuple[str, ...]:
    if payload is None:
        return ()
    if not isinstance(payload, list):
        raise ValueError('Plugin manifest field "hooks" must be a list.')
    hooks: list[str] = []
    for item in payload:
        hook = str(item or "").strip()
        if hook:
            hooks.append(hook)
    return tuple(hooks)


def _parse_bool(value: object, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f'Plugin manifest field "{field}" must be a boolean.')


def _parse_tags(value: object, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f'Plugin manifest field "{field}" must be a list of strings.')
    tags: list[str] = []
    for item in value:
        tag = str(item or "").strip()
        if tag:
            tags.append(tag)
    return tuple(tags)


def _parse_string_list(value: object, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f'Plugin manifest field "{field}" must be a list of strings.')
    return tuple(str(item) for item in value)


def _parse_string_map(value: object, *, field: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f'Plugin manifest field "{field}" must be an object.')
    return {str(key): str(item) for key, item in value.items()}


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: object, *, field: str) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'Plugin manifest field "{field}" must be numeric.') from exc
