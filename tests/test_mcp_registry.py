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


class ToggleTransport:
    def __init__(self, state: dict[str, bool]) -> None:
        self.state = state

    def request(self, method: str, params: dict | None = None) -> dict:
        if method == "initialize":
            if self.state["fail"]:
                return {"error": {"code": -1, "message": "offline"}}
            return {
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"version": "1.0.1"},
                    "capabilities": {"tools": {}, "resources": {}},
                }
            }
        if method == "tools/list":
            return {
                "result": {
                    "tools": [
                        {
                            "name": "search_docs",
                            "description": "Search docs",
                            "inputSchema": {"type": "object", "properties": {}},
                        }
                    ]
                }
            }
        if method == "resources/list":
            return {
                "result": {
                    "resources": [
                        {
                            "uri": "docs://guide",
                            "name": "Guide",
                            "mimeType": "text/plain",
                            "description": "Guide text",
                        }
                    ]
                }
            }
        raise AssertionError(f"unexpected method: {method}")


class McpRegistryTests(unittest.TestCase):
    def test_registry_tracks_servers_tools_and_resources(self) -> None:
        client = McpClient(
            config=McpServerConfig(name="docs", transport="stdio", command="demo"),
            transport=FakeTransport(
                {
                    "initialize": {
                        "result": {
                            "protocolVersion": "2024-11-05",
                            "serverInfo": {"version": "1.0.0"},
                            "capabilities": {"tools": {}, "resources": {}},
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
                    "resources/list": {
                        "result": {
                            "resources": [
                                {
                                    "uri": "docs://guide",
                                    "name": "Guide",
                                    "mimeType": "text/plain",
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
        resource_refs = registry.list_resources("docs")

        self.assertEqual(registry.list_servers(), ["docs"])
        self.assertEqual(initialized.server_version, "1.0.0")
        self.assertEqual(tool_refs[0].qualified_name, "docs.search_docs")
        self.assertEqual(registry.list_tool_references()[0].qualified_name, "docs.search_docs")
        self.assertEqual(resource_refs[0].uri, "docs://guide")
        self.assertEqual(registry.find_resource("docs", "docs://guide"), resource_refs[0])

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
        self.assertEqual(registry.list_resources("broken"), [])

    def test_failed_server_has_zero_initial_retry_backoff(self) -> None:
        client = McpClient(
            config=McpServerConfig(name="broken", transport="stdio", command="demo"),
            transport=FakeTransport({"initialize": {"error": {"code": -1, "message": "boom"}}}),
        )
        registry = McpRegistry()
        registry.register_client(client)

        registry.connect_server("broken")

        self.assertEqual(registry.retry_wait_seconds("broken"), 0)

    def test_mark_server_failed_enters_retrying_after_repeated_failure(self) -> None:
        client = McpClient(
            config=McpServerConfig(name="broken", transport="stdio", command="demo"),
            transport=FakeTransport({"initialize": {"error": {"code": -1, "message": "boom"}}}),
        )
        registry = McpRegistry()
        registry.register_client(client)

        registry.connect_server("broken")
        server = registry.mark_server_failed("broken", "still offline")

        self.assertEqual(server.status, "retrying")
        self.assertGreater(registry.retry_wait_seconds("broken"), 0)

    def test_reconnect_server_refreshes_tools_and_resources(self) -> None:
        state = {"fail": True}

        def build_client() -> McpClient:
            return McpClient(
                config=McpServerConfig(name="docs", transport="stdio", command="demo"),
                transport=ToggleTransport(state),
            )

        registry = McpRegistry()
        registry.register_client(build_client(), client_factory=build_client)

        failed = registry.connect_server("docs")
        self.assertEqual(failed.status, "failed")

        state["fail"] = False
        server = registry.reconnect_server("docs")

        self.assertEqual(server.status, "connected")
        self.assertEqual([tool.name for tool in server.tools], ["search_docs"])
        self.assertEqual([resource.uri for resource in server.resources], ["docs://guide"])


if __name__ == "__main__":
    unittest.main()
