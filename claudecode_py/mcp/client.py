from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .models import (
    McpCallToolResult,
    McpInitializeResult,
    McpServerConfig,
    McpTool,
    parse_call_tool_result,
    parse_initialize_result,
    parse_mcp_tool,
)


class McpClientError(RuntimeError):
    pass


class McpProtocolError(McpClientError):
    pass


class McpTransport(Protocol):
    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


@dataclass(slots=True)
class McpClient:
    config: McpServerConfig
    transport: McpTransport
    initialized: bool = False

    def initialize(self) -> McpInitializeResult:
        result = self.transport.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "pyclaudecode",
                    "version": "0.1.0",
                },
            },
        )
        self.initialized = True
        return parse_initialize_result(self.config.name, _extract_result("initialize", result))

    def list_tools(self) -> list[McpTool]:
        self._require_initialized()
        payload = _extract_result("tools/list", self.transport.request("tools/list", {}))
        return [parse_mcp_tool(item) for item in payload.get("tools", [])]

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> McpCallToolResult:
        self._require_initialized()
        payload = _extract_result(
            "tools/call",
            self.transport.request(
                "tools/call",
                {
                    "name": name,
                    "arguments": arguments or {},
                },
            ),
        )
        return parse_call_tool_result(payload)

    def _require_initialized(self) -> None:
        if not self.initialized:
            raise McpClientError(
                f'MCP client for "{self.config.name}" must be initialized before use.'
            )

    def close(self) -> None:
        self.transport.close()


def _extract_result(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    if "error" in payload:
        error = payload["error"]
        code = error.get("code", "unknown")
        message = error.get("message", "Unknown MCP error")
        raise McpProtocolError(f"{method} failed ({code}): {message}")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise McpProtocolError(f"{method} returned invalid result payload.")
    return result
