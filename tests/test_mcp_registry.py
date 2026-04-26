from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.mcp import McpClient, McpRegistry, McpServerConfig


class FakeTransport:
    def __init__(self, responses: dict[str, dict]) -> None:
        self.responses = responses

    def request(self, method: str, params: dict | None = None) -> dict:
        return self.responses[method]


class McpRegistryTests(unittest.TestCase):
    def test_registry_tracks_servers_and_tools(self) -> None:
        client = McpClient(
            config=McpServerConfig(name="docs", transport="stdio", command="demo"),
            transport=FakeTransport(
                {
                    "initialize": {
                        "result": {
                            "protocolVersion": "2024-11-05",
                            "serverInfo": {"version": "1.0.0"},
                            "capabilities": {"tools": {}},
                        }
                    },
                    "tools/list": {
                        "result": {
                            "tools": [
                                {
                                    "name": "search_docs",
                                    "description": "Search docs",
                                    "inputSchema": {"type": "object", "properties": {}},
                                }
                            ]
                        }
                    },
                }
            ),
        )
        registry = McpRegistry()

        registry.register_client(client)
        initialized = registry.initialize_server("docs")
        tool_refs = registry.refresh_tools("docs")

        self.assertEqual(registry.list_servers(), ["docs"])
        self.assertEqual(initialized.server_version, "1.0.0")
        self.assertEqual(tool_refs[0].qualified_name, "docs.search_docs")
        self.assertEqual(registry.list_tool_references()[0].qualified_name, "docs.search_docs")

    def test_connect_server_records_failure_without_crashing_registry(self) -> None:
        client = McpClient(
            config=McpServerConfig(name="broken", transport="stdio", command="demo"),
            transport=FakeTransport({"initialize": {"error": {"code": -1, "message": "boom"}}}),
        )
        registry = McpRegistry()
        registry.register_client(client)

        server = registry.connect_server("broken")

        self.assertEqual(server.status, "failed")
        self.assertIn("boom", server.last_error or "")
        self.assertEqual(registry.list_tool_references(), [])

    def test_failed_server_has_zero_initial_retry_backoff(self) -> None:
        client = McpClient(
            config=McpServerConfig(name="broken", transport="stdio", command="demo"),
            transport=FakeTransport({"initialize": {"error": {"code": -1, "message": "boom"}}}),
        )
        registry = McpRegistry()
        registry.register_client(client)

        registry.connect_server("broken")

        self.assertEqual(registry.retry_wait_seconds("broken"), 0)


if __name__ == "__main__":
    unittest.main()
