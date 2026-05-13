from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable

from .client import McpClient
from .models import (
    McpInitializeResult,
    McpResource,
    McpServerStatus,
    McpTool,
    McpToolReference,
)


@dataclass(slots=True)
class RegisteredMcpServer:
    client: McpClient
    client_factory: Callable[[], McpClient] | None = None
    initialize_result: McpInitializeResult | None = None
    tools: list[McpTool] = field(default_factory=list)
    resources: list[McpResource] = field(default_factory=list)
    status: McpServerStatus = "registered"
    last_error: str | None = None
    last_connected_at: str | None = None
    last_failed_at: str | None = None
    failure_count: int = 0


class McpRegistry:
    def __init__(self) -> None:
        self._servers: dict[str, RegisteredMcpServer] = {}

    def register_client(
        self,
        client: McpClient,
        *,
        client_factory: Callable[[], McpClient] | None = None,
    ) -> None:
        self._servers[client.config.name] = RegisteredMcpServer(
            client=client,
            client_factory=client_factory,
        )

    def initialize_server(self, server_name: str) -> McpInitializeResult:
        server = self._servers[server_name]
        server.initialize_result = server.client.initialize()
        self._mark_connected(server, preserve_initialize=True)
        return server.initialize_result

    def get_server(self, server_name: str) -> RegisteredMcpServer:
        return self._servers[server_name]

    def refresh_tools(self, server_name: str) -> list[McpToolReference]:
        server = self._servers[server_name]
        if server.initialize_result is None:
            server.initialize_result = server.client.initialize()
        self._refresh_server_discovery(server_name)
        self._mark_connected(server, preserve_initialize=True)
        return [McpToolReference(server_name=server_name, tool=tool) for tool in server.tools]

    def connect_server(self, server_name: str) -> RegisteredMcpServer:
        server = self._servers[server_name]
        try:
            server.initialize_result = server.client.initialize()
            self._refresh_server_discovery(server_name)
            self._mark_connected(server, preserve_initialize=True)
        except Exception as exc:  # noqa: BLE001
            self._mark_failed(server, f"{type(exc).__name__}: {exc}")
        return server

    def reconnect_server(self, server_name: str) -> RegisteredMcpServer:
        server = self._servers[server_name]
        if server.client_factory is None:
            return self.connect_server(server_name)
        try:
            server.client.close()
        except Exception:  # noqa: BLE001
            pass
        server.client = server.client_factory()
        server.initialize_result = None
        server.tools = []
        server.resources = []
        server.status = "registered"
        server.last_error = None
        return self.connect_server(server_name)

    def mark_server_failed(self, server_name: str, error_text: str) -> RegisteredMcpServer:
        server = self._servers[server_name]
        self._mark_failed(server, error_text)
        return server

    def ensure_server_connected(self, server_name: str) -> RegisteredMcpServer:
        server = self._servers[server_name]
        if server.status == "connected":
            return server
        if server.status == "registered":
            return self.connect_server(server_name)
        retry_wait = self.retry_wait_seconds(server_name)
        if retry_wait > 0:
            server.status = "retrying"
            return server
        return self.reconnect_server(server_name)

    def retry_wait_seconds(self, server_name: str) -> int:
        server = self._servers[server_name]
        if server.status not in {"failed", "retrying"} or server.last_failed_at is None:
            return 0
        failed_at = _parse_iso_timestamp(server.last_failed_at)
        if failed_at is None:
            return 0
        delay_seconds = _retry_backoff_seconds(server.failure_count)
        retry_at = failed_at.timestamp() + delay_seconds
        remaining = retry_at - datetime.now(UTC).timestamp()
        return max(0, int(remaining + 0.999))

    def refresh_all_tools(self) -> list[McpToolReference]:
        refs: list[McpToolReference] = []
        for server_name in self.list_servers():
            try:
                server = self.ensure_server_connected(server_name)
                if server.status != "connected":
                    continue
                refs.extend(self.refresh_tools(server_name))
            except Exception as exc:  # noqa: BLE001
                server = self._servers[server_name]
                self._mark_failed(server, f"{type(exc).__name__}: {exc}")
        return refs

    def list_servers(self) -> list[str]:
        return sorted(self._servers.keys())

    def list_tool_references(self) -> list[McpToolReference]:
        refs: list[McpToolReference] = []
        for server_name in self.list_servers():
            server = self._servers[server_name]
            refs.extend(McpToolReference(server_name=server_name, tool=tool) for tool in server.tools)
        return refs

    def list_resources(self, server_name: str | None = None) -> list[McpResource]:
        if server_name is not None:
            server = self._servers[server_name]
            return list(server.resources)
        resources: list[McpResource] = []
        for name in self.list_servers():
            resources.extend(self._servers[name].resources)
        return resources

    def find_resource(self, server_name: str, uri: str) -> McpResource | None:
        server = self._servers.get(server_name)
        if server is None:
            return None
        for resource in server.resources:
            if resource.uri == uri:
                return resource
        return None

    def find_tool_reference(self, server_name: str, tool_name: str) -> McpToolReference | None:
        server = self._servers.get(server_name)
        if server is None:
            return None
        for tool in server.tools:
            if tool.name == tool_name:
                return McpToolReference(server_name=server_name, tool=tool)
        return None

    def close(self) -> None:
        for server_name in self.list_servers():
            try:
                self._servers[server_name].client.close()
            except Exception:  # noqa: BLE001
                pass

    def _refresh_server_discovery(self, server_name: str) -> None:
        server = self._servers[server_name]
        server.tools = server.client.list_tools()
        try:
            server.resources = server.client.list_resources()
        except Exception:  # noqa: BLE001
            server.resources = []

    def _mark_connected(
        self,
        server: RegisteredMcpServer,
        *,
        preserve_initialize: bool = False,
    ) -> None:
        if not preserve_initialize and server.initialize_result is None:
            server.initialize_result = server.client.initialize()
        server.status = "connected"
        server.last_error = None
        server.last_connected_at = _utc_now_iso()

    def _mark_failed(self, server: RegisteredMcpServer, error_text: str) -> None:
        server.initialize_result = None
        server.tools = []
        server.resources = []
        server.last_error = error_text
        server.last_failed_at = _utc_now_iso()
        server.failure_count += 1
        server.status = "retrying" if _retry_backoff_seconds(server.failure_count) > 0 else "failed"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _retry_backoff_seconds(failure_count: int) -> int:
    if failure_count <= 1:
        return 0
    return min(30, 2 ** (failure_count - 2))
