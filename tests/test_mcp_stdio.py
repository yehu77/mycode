from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.mcp import McpClient, McpServerConfig, McpStdioTransport


class McpStdioTransportTests(unittest.TestCase):
    def test_stdio_transport_talks_to_fake_server(self) -> None:
        server_script = Path(__file__).resolve().parent / "fixtures" / "fake_mcp_server.py"
        transport = McpStdioTransport(
            McpServerConfig(
                name="fake",
                transport="stdio",
                command=sys.executable,
                args=(str(server_script),),
            )
        )
        client = McpClient(
            config=McpServerConfig(
                name="fake",
                transport="stdio",
                command=sys.executable,
                args=(str(server_script),),
            ),
            transport=transport,
        )

        try:
            initialized = client.initialize()
            tools = client.list_tools()
            result = client.call_tool("echo_text", {"text": "hello"})
        finally:
            client.close()

        self.assertEqual(initialized.server_version, "0.0.1")
        self.assertEqual(tools[0].name, "echo_text")
        self.assertEqual(result.content[0]["text"], "echo:hello")


if __name__ == "__main__":
    unittest.main()
