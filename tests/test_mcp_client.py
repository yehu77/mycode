from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.mcp import (
    McpClient,
    McpClientError,
    McpProtocolError,
    McpServerConfig,
)


class FakeTransport:
    def __init__(self, responses: dict[str, dict]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def request(self, method: str, params: dict | None = None) -> dict:
        self.calls.append((method, params or {}))
        return self.responses[method]


class McpClientTests(unittest.TestCase):
    def test_initialize_list_tools_resources_and_call_tool(self) -> None:
        transport = FakeTransport(
            {
                "initialize": {
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {"version": "1.2.3"},
                        "capabilities": {"tools": {}, "resources": {}},
                        "instructions": "Use carefully",
                    }
                },
                "tools/list": {
                    "result": {
                        "tools": [
                            {
                                "name": "search_docs",
                                "description": "Search documents",
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
                "resources/read": {
                    "result": {
                        "contents": [
                            {
                                "uri": "docs://guide",
                                "mimeType": "text/plain",
                                "text": "hello",
                            }
                        ]
                    }
                },
                "tools/call": {
                    "result": {
                        "content": [{"type": "text", "text": "found"}],
                        "isError": False,
                    }
                },
            }
        )
        client = McpClient(
            config=McpServerConfig(name="docs", transport="stdio", command="demo"),
            transport=transport,
        )

        initialized = client.initialize()
        tools = client.list_tools()
        resources = client.list_resources()
        resource_result = client.read_resource("docs://guide")
        result = client.call_tool("search_docs", {"query": "mcp"})

        self.assertEqual(initialized.server_name, "docs")
        self.assertEqual(initialized.server_version, "1.2.3")
        self.assertEqual(tools[0].name, "search_docs")
        self.assertEqual(resources[0].uri, "docs://guide")
        self.assertEqual(resource_result.contents[0]["text"], "hello")
        self.assertEqual(result.content[0]["text"], "found")
        self.assertEqual(
            [call[0] for call in transport.calls],
            ["initialize", "tools/list", "resources/list", "resources/read", "tools/call"],
        )

    def test_client_requires_initialize_before_listing_tools(self) -> None:
        client = McpClient(
            config=McpServerConfig(name="docs", transport="stdio", command="demo"),
            transport=FakeTransport({}),
        )

        with self.assertRaises(McpClientError):
            client.list_tools()

    def test_client_requires_initialize_before_listing_resources(self) -> None:
        client = McpClient(
            config=McpServerConfig(name="docs", transport="stdio", command="demo"),
            transport=FakeTransport({}),
        )

        with self.assertRaises(McpClientError):
            client.list_resources()

    def test_protocol_error_is_raised_for_error_payload(self) -> None:
        client = McpClient(
            config=McpServerConfig(name="docs", transport="stdio", command="demo"),
            transport=FakeTransport(
                {
                    "initialize": {
                        "error": {
                            "code": -32601,
                            "message": "Method not found",
                        }
                    }
                }
            ),
        )

        with self.assertRaises(McpProtocolError):
            client.initialize()


if __name__ == "__main__":
    unittest.main()
