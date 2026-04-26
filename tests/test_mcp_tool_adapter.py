from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.config import SessionConfig
from claudecode_py.mcp import McpClient, McpRegistry, McpServerConfig
from claudecode_py.mcp.client import McpProtocolError
from claudecode_py.permissions import PermissionManager
from claudecode_py.session import Session
from claudecode_py.tasks import TaskManager
from claudecode_py.tools.base import ToolContext
from claudecode_py.tools.mcp import McpToolAdapter, make_mcp_tool_name


class FakeTransport:
    def request(self, method: str, params: dict | None = None) -> dict:
        if method == "initialize":
            return {
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"version": "1.0.0"},
                    "capabilities": {"tools": {}},
                }
            }
        if method == "tools/list":
            return {
                "result": {
                    "tools": [
                        {
                            "name": "echo_text",
                            "description": "Return text",
                            "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
                        }
                    ]
                }
            }
        if method == "tools/call":
            text = (params or {}).get("arguments", {}).get("text", "")
            return {"result": {"content": [{"type": "text", "text": f"echo:{text}"}]}}
        raise AssertionError(f"unexpected method: {method}")

    def close(self) -> None:
        return None


class McpToolAdapterTests(unittest.TestCase):
    def test_adapter_executes_remote_tool(self) -> None:
        client = McpClient(
            config=McpServerConfig(name="docs", transport="stdio", command="demo"),
            transport=FakeTransport(),
        )
        registry = McpRegistry()
        registry.register_client(client)
        registry.initialize_server("docs")
        reference = registry.refresh_tools("docs")[0]

        session = Session(
            SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False),
            mcp_registry=registry,
        )
        ctx = ToolContext(
            cwd=session.config.cwd,
            permission_manager=PermissionManager(mode="bypass", interactive=False),
            task_manager=TaskManager(),
            session=session,
        )
        tool = next(tool for tool in session.tools if tool.name == make_mcp_tool_name("docs", "echo_text"))
        assert isinstance(tool, McpToolAdapter)

        result = tool.execute({"text": "hello"}, ctx)

        self.assertEqual(result, "echo:hello")
        self.assertEqual(tool.declared_risk_level(), "mcp")

    def test_adapter_marks_server_failed_on_connection_level_error(self) -> None:
        transport_state = {"fail": True}

        class BrokenCallTransport(FakeTransport):
            def request(self, method: str, params: dict | None = None) -> dict:
                if method == "tools/call" and transport_state["fail"]:
                    return {"error": {"code": -1, "message": "connection lost"}}
                return super().request(method, params)

        registry = McpRegistry()

        def build_client():
            return McpClient(
                config=McpServerConfig(name="docs", transport="stdio", command="demo"),
                transport=BrokenCallTransport(),
            )

        registry.register_client(build_client(), client_factory=build_client)
        registry.initialize_server("docs")
        registry.refresh_tools("docs")

        session = Session(
            SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False),
            mcp_registry=registry,
        )
        ctx = ToolContext(
            cwd=session.config.cwd,
            permission_manager=PermissionManager(mode="bypass", interactive=False),
            task_manager=TaskManager(),
            session=session,
        )
        tool_name = make_mcp_tool_name("docs", "echo_text")
        tool = next(tool for tool in session.tools if tool.name == tool_name)
        assert isinstance(tool, McpToolAdapter)

        with self.assertRaises(McpProtocolError):
            tool.execute({"text": "hello"}, ctx)

        self.assertEqual(registry.get_server("docs").status, "failed")
        self.assertIn(tool_name, [tool.name for tool in session.tools])
        self.assertIn("status=failed", session.describe_mcp_servers())

        transport_state["fail"] = False
        recovered = tool.execute({"text": "hello"}, ctx)

        self.assertEqual(recovered, "echo:hello")
        self.assertEqual(registry.get_server("docs").status, "connected")


if __name__ == "__main__":
    unittest.main()
