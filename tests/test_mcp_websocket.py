from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.mcp import McpClient, McpServerConfig
from claudecode_py.mcp.websocket import McpWebSocketTransport


class _FakeWebSocketConnection:
    def __init__(self) -> None:
        self.sent_messages: list[str] = []
        self.responses = [
            '{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05","serverInfo":{"version":"0.0.5"},"capabilities":{"tools":{}}}}',
            '{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"echo_text","description":"Return the provided text.","inputSchema":{"type":"object","properties":{"text":{"type":"string"}}}}]}}',
            '{"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"ws-echo:hello"}],"isError":false}}',
        ]
        self.closed = False

    def send(self, data: str) -> None:
        self.sent_messages.append(data)

    def recv(self, timeout: float | None = None) -> str:
        if not self.responses:
            raise TimeoutError(f"timed out after {timeout}")
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


class McpWebSocketTransportTests(unittest.TestCase):
    def test_websocket_transport_talks_to_fake_connection(self) -> None:
        captured: dict[str, object] = {}
        fake_connection = _FakeWebSocketConnection()

        def connector(url: str, headers: dict[str, str], timeout_sec: float):
            captured["url"] = url
            captured["headers"] = headers
            captured["timeout_sec"] = timeout_sec
            return fake_connection

        config = McpServerConfig(
            name="ws-fake",
            transport="websocket",
            url="ws://example.test/mcp",
            headers={"Authorization": "Bearer demo-token"},
            timeout_sec=2.5,
        )
        client = McpClient(
            config=config,
            transport=McpWebSocketTransport(config, connector=connector),
        )

        initialized = client.initialize()
        tools = client.list_tools()
        result = client.call_tool("echo_text", {"text": "hello"})
        client.close()

        self.assertEqual(initialized.server_version, "0.0.5")
        self.assertEqual(tools[0].name, "echo_text")
        self.assertEqual(result.content[0]["text"], "ws-echo:hello")
        self.assertEqual(captured["url"], "ws://example.test/mcp")
        self.assertEqual(captured["headers"], {"Authorization": "Bearer demo-token"})
        self.assertEqual(captured["timeout_sec"], 2.5)
        self.assertTrue(fake_connection.closed)

