from __future__ import annotations

from typing import Any
import re

from ..mcp import McpClient, McpClientError, McpToolReference
from .base import BaseTool


def make_mcp_tool_name(server_name: str, tool_name: str) -> str:
    normalized_server = re.sub(r"[^a-zA-Z0-9_]", "_", server_name)
    normalized_tool = re.sub(r"[^a-zA-Z0-9_]", "_", tool_name)
    return f"mcp__{normalized_server}__{normalized_tool}"


class McpToolAdapter(BaseTool):
    read_only = False
    concurrency_safe = False
    risk_level = "mcp"

    def __init__(self, *, client: McpClient, reference: McpToolReference) -> None:
        self.client = client
        self.reference = reference
        self.name = make_mcp_tool_name(reference.server_name, reference.tool.name)
        self.description = (
            f'MCP tool from server "{reference.server_name}": {reference.tool.description}'
        )
        self.input_schema = reference.tool.input_schema

    def schema_source(self) -> str:
        return "mcp"

    def execute(self, tool_input: dict[str, Any], ctx):
        server = ctx.session.ensure_mcp_server_connected(self.reference.server_name)
        if server is None:
            raise RuntimeError(f'MCP server "{self.reference.server_name}" is not configured.')
        if server.status != "connected":
            registry = ctx.session.mcp_registry
            retry_in = (
                registry.retry_wait_seconds(self.reference.server_name)
                if registry is not None
                else 0
            )
            if retry_in:
                raise RuntimeError(
                    f'MCP server "{self.reference.server_name}" is unavailable; retry in {retry_in}s. '
                    f"Last error: {server.last_error or 'unknown'}"
                )
            raise RuntimeError(
                f'MCP server "{self.reference.server_name}" is unavailable. '
                f"Last error: {server.last_error or 'unknown'}"
            )
        try:
            result = server.client.call_tool(self.reference.tool.name, tool_input)
        except (McpClientError, OSError, TimeoutError) as exc:
            ctx.session.handle_mcp_server_failure(
                self.reference.server_name,
                f"{type(exc).__name__}: {exc}",
            )
            raise
        return _render_mcp_result(result.content, is_error=result.is_error)


def _render_mcp_result(content: list[dict[str, Any]], *, is_error: bool) -> str:
    parts: list[str] = []
    for item in content:
        item_type = item.get("type")
        if item_type == "text":
            parts.append(str(item.get("text", "")))
        else:
            parts.append(str(item))
    rendered = "\n".join(part for part in parts if part).strip()
    if not rendered:
        rendered = "(empty MCP result)"
    if is_error:
        return f"MCP tool returned error:\n{rendered}"
    return rendered
