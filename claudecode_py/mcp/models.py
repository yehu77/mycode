from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


McpServerStatus = Literal["registered", "connected", "failed"]


@dataclass(slots=True, frozen=True)
class McpServerConfig:
    name: str
    transport: str
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    auth_mode: str | None = None
    cwd: str | None = None
    url: str | None = None
    timeout_sec: float | None = None


@dataclass(slots=True, frozen=True)
class McpTool:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(slots=True, frozen=True)
class McpToolReference:
    server_name: str
    tool: McpTool

    @property
    def qualified_name(self) -> str:
        return f"{self.server_name}.{self.tool.name}"


@dataclass(slots=True, frozen=True)
class McpInitializeResult:
    server_name: str
    server_version: str = ""
    protocol_version: str = ""
    instructions: str = ""
    capabilities: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class McpCallToolResult:
    content: list[dict[str, Any]]
    is_error: bool = False


@dataclass(slots=True, frozen=True)
class McpDiagnosticResult:
    server_name: str
    tool_name: str
    ok: bool
    source: str
    transport: str = ""
    server_status: str = ""
    retry_in: int = 0
    failure_count: int = 0
    result_text: str = ""
    error_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_name": self.server_name,
            "tool_name": self.tool_name,
            "ok": self.ok,
            "source": self.source,
            "transport": self.transport,
            "server_status": self.server_status,
            "retry_in": self.retry_in,
            "failure_count": self.failure_count,
            "result_text": self.result_text,
            "error_text": self.error_text,
        }


@dataclass(slots=True, frozen=True)
class McpVerificationResult:
    server_name: str
    tool_name: str
    mapped_tool_name: str
    ok: bool
    source: str
    output_text: str = ""
    error_text: str = ""
    tool_called: bool = False
    preflight: McpDiagnosticResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_name": self.server_name,
            "tool_name": self.tool_name,
            "mapped_tool_name": self.mapped_tool_name,
            "ok": self.ok,
            "source": self.source,
            "output_text": self.output_text,
            "error_text": self.error_text,
            "tool_called": self.tool_called,
            "preflight": self.preflight.to_dict() if self.preflight is not None else None,
        }


def parse_mcp_tool(payload: dict[str, Any]) -> McpTool:
    return McpTool(
        name=str(payload["name"]),
        description=str(payload.get("description", "")),
        input_schema=dict(payload.get("inputSchema", {}) or {}),
    )


def parse_initialize_result(server_name: str, payload: dict[str, Any]) -> McpInitializeResult:
    return McpInitializeResult(
        server_name=server_name,
        server_version=str(payload.get("serverInfo", {}).get("version", "")),
        protocol_version=str(payload.get("protocolVersion", "")),
        instructions=str(payload.get("instructions", "")),
        capabilities=dict(payload.get("capabilities", {}) or {}),
    )


def parse_call_tool_result(payload: dict[str, Any]) -> McpCallToolResult:
    return McpCallToolResult(
        content=list(payload.get("content", []) or []),
        is_error=bool(payload.get("isError", False)),
    )
