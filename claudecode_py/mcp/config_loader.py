from __future__ import annotations

from pathlib import Path
import base64
import json
import os
import re

from .client import McpClient
from .http import McpHttpTransport
from .models import McpServerConfig
from .registry import McpRegistry
from .sse import McpSseTransport
from .stdio import McpStdioTransport
from .websocket import McpWebSocketTransport


def default_mcp_config_path(cwd: Path) -> Path:
    return cwd / ".pyclaude" / "mcp_servers.json"


def load_mcp_registry(cwd: Path, config_path: Path | None = None) -> McpRegistry | None:
    path = config_path or default_mcp_config_path(cwd)
    if not path.exists():
        return None
    return load_mcp_registry_from_payloads(cwd, load_mcp_server_payloads(path))


def load_mcp_registry_from_payloads(
    cwd: Path,
    server_payloads: list[dict[str, object]],
) -> McpRegistry | None:
    if not server_payloads:
        return None

    registry = McpRegistry()
    for item in server_payloads:
        server_config = _parse_server_config(cwd, item)
        registry.register_client(
            _build_client(server_config),
            client_factory=lambda config=server_config: _build_client(config),
        )
        registry.connect_server(server_config.name)
    return registry


def load_mcp_server_payloads(path: Path) -> list[dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload.get("servers", []) or [])


def _parse_server_config(cwd: Path, payload: dict) -> McpServerConfig:
    transport = str(payload["transport"])
    server_name = str(payload["name"])
    if transport == "stdio":
        command = _expand_required(str(payload["command"]), field="command", server_name=server_name)
        args = tuple(
            _expand_required(str(arg), field="args", server_name=server_name)
            for arg in payload.get("args", [])
        )
        server_cwd = (
            _expand_required(str(payload.get("cwd")), field="cwd", server_name=server_name)
            if payload.get("cwd") is not None
            else None
        )
        resolved_cwd = str((cwd / server_cwd).resolve()) if server_cwd else None
        return McpServerConfig(
            name=server_name,
            transport=transport,
            command=command,
            args=args,
            env={
                str(k): _expand_required(str(v), field=f"env.{k}", server_name=server_name)
                for k, v in dict(payload.get("env", {}) or {}).items()
            },
            cwd=resolved_cwd,
            timeout_sec=_parse_timeout(payload.get("timeout_sec")),
        )
    if transport == "http":
        headers, auth_mode = _build_remote_headers(payload, server_name=server_name)
        return McpServerConfig(
            name=server_name,
            transport=transport,
            url=_expand_required(str(payload["url"]), field="url", server_name=server_name),
            headers=headers,
            auth_mode=auth_mode,
            timeout_sec=_parse_timeout(payload.get("timeout_sec")),
        )
    if transport == "sse":
        headers, auth_mode = _build_remote_headers(payload, server_name=server_name)
        return McpServerConfig(
            name=server_name,
            transport=transport,
            url=_expand_required(str(payload["url"]), field="url", server_name=server_name),
            headers=headers,
            auth_mode=auth_mode,
            timeout_sec=_parse_timeout(payload.get("timeout_sec")),
        )
    if transport == "websocket":
        headers, auth_mode = _build_remote_headers(payload, server_name=server_name)
        return McpServerConfig(
            name=server_name,
            transport=transport,
            url=_expand_required(str(payload["url"]), field="url", server_name=server_name),
            headers=headers,
            auth_mode=auth_mode,
            timeout_sec=_parse_timeout(payload.get("timeout_sec")),
        )
    raise ValueError(f"Unsupported MCP transport: {transport}")


def _build_client(config: McpServerConfig) -> McpClient:
    if config.transport == "stdio":
        return McpClient(config=config, transport=McpStdioTransport(config))
    if config.transport == "http":
        return McpClient(config=config, transport=McpHttpTransport(config))
    if config.transport == "sse":
        return McpClient(config=config, transport=McpSseTransport(config))
    if config.transport == "websocket":
        return McpClient(config=config, transport=McpWebSocketTransport(config))
    raise ValueError(f"Unsupported MCP transport: {config.transport}")


def _parse_timeout(value) -> float | None:
    if value is None:
        return None
    return float(value)


def _expand_required(value: str, *, field: str, server_name: str) -> str:
    expanded, missing = _expand_env_vars_in_string(value)
    if missing:
        missing_names = ", ".join(sorted(set(missing)))
        raise ValueError(
            f'MCP server "{server_name}" field "{field}" references missing environment variable(s): {missing_names}'
        )
    return expanded


def _expand_env_vars_in_string(value: str) -> tuple[str, list[str]]:
    missing_vars: list[str] = []

    def replace(match: re.Match[str]) -> str:
        content = match.group(1)
        var_name, default_value = (content.split(":-", 1) + [None])[:2]
        env_value = os.getenv(var_name)
        if env_value is not None:
            return env_value
        if default_value is not None:
            return default_value
        missing_vars.append(var_name)
        return match.group(0)

    expanded = re.sub(r"\$\{([^}]+)\}", replace, value)
    return expanded, missing_vars


def _build_remote_headers(payload: dict, *, server_name: str) -> tuple[dict[str, str], str | None]:
    raw_headers = {
        str(k): _expand_required(str(v), field=f"headers.{k}", server_name=server_name)
        for k, v in dict(payload.get("headers", {}) or {}).items()
    }
    auth_headers, auth_mode = _build_auth_headers(payload.get("auth"), server_name=server_name)
    conflicting = set(raw_headers).intersection(auth_headers)
    if conflicting:
        conflict_names = ", ".join(sorted(conflicting))
        raise ValueError(
            f'MCP server "{server_name}" auth configuration conflicts with explicit headers: {conflict_names}'
        )
    return ({**auth_headers, **raw_headers}, auth_mode)


def _build_auth_headers(auth_payload, *, server_name: str) -> tuple[dict[str, str], str | None]:
    auth = dict(auth_payload or {})
    if not auth:
        return {}, None
    auth_type = str(auth.get("type", "")).strip().lower()
    if not auth_type:
        raise ValueError(f'MCP server "{server_name}" auth.type is required when auth is configured.')
    if auth_type == "bearer":
        token = _expand_required(str(auth["token"]), field="auth.token", server_name=server_name)
        return {"Authorization": f"Bearer {token}"}, "bearer"
    if auth_type == "basic":
        username = _expand_required(
            str(auth["username"]),
            field="auth.username",
            server_name=server_name,
        )
        password = _expand_required(
            str(auth["password"]),
            field="auth.password",
            server_name=server_name,
        )
        encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}, "basic"
    if auth_type == "api_key":
        header_name = _expand_required(
            str(auth.get("header", "X-API-Key")),
            field="auth.header",
            server_name=server_name,
        )
        value = _expand_required(
            str(auth["value"]),
            field="auth.value",
            server_name=server_name,
        )
        prefix = _expand_required(
            str(auth.get("prefix", "")),
            field="auth.prefix",
            server_name=server_name,
        )
        return {header_name: f"{prefix}{value}"}, "api_key"
    raise ValueError(
        f'MCP server "{server_name}" has unsupported auth.type "{auth_type}". '
        'Supported values: bearer, basic, api_key'
    )
