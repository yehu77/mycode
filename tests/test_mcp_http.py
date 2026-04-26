from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.mcp import McpClient, McpHttpTransport, McpServerConfig


class _FakeMcpHttpHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        payload = json.loads(body.decode("utf-8"))
        method = payload["method"]
        msg_id = payload["id"]

        if self.headers.get("Authorization") != "Bearer demo-token":
            self._write_json(
                401,
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32001, "message": "unauthorized"},
                },
            )
            return

        if method == "initialize":
            self._write_json(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {"version": "0.0.2"},
                        "capabilities": {"tools": {}},
                    },
                },
            )
            return
        if method == "tools/list":
            self._write_json(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "tools": [
                            {
                                "name": "echo_text",
                                "description": "Return the provided text.",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"text": {"type": "string"}},
                                    "required": ["text"],
                                },
                            }
                        ]
                    },
                },
            )
            return
        if method == "tools/call":
            text = payload.get("params", {}).get("arguments", {}).get("text", "")
            self._write_json(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": f"http-echo:{text}"}],
                        "isError": False,
                    },
                },
            )
            return

        self._write_json(
            404,
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Unknown method: {method}"},
            },
        )

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return None

    def _write_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class McpHttpTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeMcpHttpHandler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1.0)

    def test_http_transport_talks_to_fake_server_with_headers(self) -> None:
        url = f"http://127.0.0.1:{self.server.server_address[1]}/mcp"
        config = McpServerConfig(
            name="http-fake",
            transport="http",
            url=url,
            headers={"Authorization": "Bearer demo-token"},
            timeout_sec=3.0,
        )
        client = McpClient(config=config, transport=McpHttpTransport(config))

        initialized = client.initialize()
        tools = client.list_tools()
        result = client.call_tool("echo_text", {"text": "hello"})

        self.assertEqual(initialized.server_version, "0.0.2")
        self.assertEqual(tools[0].name, "echo_text")
        self.assertEqual(result.content[0]["text"], "http-echo:hello")


if __name__ == "__main__":
    unittest.main()
