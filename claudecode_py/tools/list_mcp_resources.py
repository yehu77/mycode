from __future__ import annotations

from ..mcp import McpResource
from .base import BaseTool


class ListMcpResourcesTool(BaseTool):
    name = "list_mcp_resources"
    description = "List resources exposed by connected MCP servers."
    read_only = True
    concurrency_safe = True
    deferred = True
    search_terms = ("mcp resources", "resource list", "mcp list")
    input_schema = {
        "type": "object",
        "properties": {
            "server": {
                "type": "string",
                "description": "Optional MCP server name to filter by.",
            },
        },
    }

    def execute(self, tool_input: dict, ctx):
        registry = ctx.session.mcp_registry
        if registry is None:
            return []
        server_filter = str(tool_input.get("server", "") or "").strip()
        server_names = [server_filter] if server_filter else registry.list_servers()
        resources: list[dict[str, str]] = []
        for server_name in server_names:
            server = ctx.session.ensure_mcp_server_connected(server_name)
            if server is None:
                if server_filter:
                    raise RuntimeError(f'MCP server "{server_name}" is not configured.')
                continue
            if server.status != "connected":
                if server_filter:
                    retry_in = registry.retry_wait_seconds(server_name)
                    retry_text = f" Retry in {retry_in}s." if retry_in else ""
                    raise RuntimeError(
                        f'MCP server "{server_name}" is {server.status}. '
                        f"Last error: {server.last_error or 'unknown'}."
                        f"{retry_text}"
                    )
                continue
            for resource in registry.list_resources(server_name):
                assert isinstance(resource, McpResource)
                resources.append(
                    {
                        "uri": resource.uri,
                        "name": resource.name,
                        "mime_type": resource.mime_type,
                        "description": resource.description,
                        "server": resource.server_name,
                    }
                )
        return resources
