from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.mcp import McpClient, McpServerConfig, McpSseTransport


class _FakeMcpSseHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    events: Queue[dict] = Queue()
    stop_event = Event()

    def do_GET(self) -> None:  # noqa: N802
        if self.headers.get("Accept") != "text/event-stream":
            self.send_error(406)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        while not self.stop_event.is_set():
            try:
                payload = self.events.get(timeout=0.1)
            except Empty:
                continue
            body = json.dumps(payload, ensure_ascii=True)
            self.wfile.write(f"data: {body}\n\n".encode("utf-8"))
            self.wfile.flush()

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        msg_id = payload["id"]
        method = payload["method"]

        if method == "initialize":
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"version": "0.0.4"},
                    "capabilities": {"tools": {}},
                },
            }
        elif method == "tools/list":
            response = {
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
                            },
                        }
                    ]
                },
            }
        elif method == "tools/call":
            text = payload.get("params", {}).get("arguments", {}).get("text", "")
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"sse-echo:{text}"}],
                    "isError": False,
                },
            }
        else:
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Unknown method: {method}"},
            }

        self.events.put(response)
        self.send_response(202)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return None


class McpSseTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeMcpSseHandler.stop_event.clear()
        while True:
            try:
                _FakeMcpSseHandler.events.get_nowait()
            except Empty:
                break
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeMcpSseHandler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        _FakeMcpSseHandler.stop_event.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1.0)

    def test_sse_transport_talks_to_fake_server(self) -> None:
        url = f"http://127.0.0.1:{self.server.server_address[1]}/mcp"
        config = McpServerConfig(
            name="sse-fake",
            transport="sse",
            url=url,
            timeout_sec=3.0,
        )
        client = McpClient(config=config, transport=McpSseTransport(config))

        try:
            initialized = client.initialize()
            tools = client.list_tools()
            result = client.call_tool("echo_text", {"text": "hello"})
        finally:
            client.close()

        self.assertEqual(initialized.server_version, "0.0.4")
        self.assertEqual(tools[0].name, "echo_text")
        self.assertEqual(result.content[0]["text"], "sse-echo:hello")


if __name__ == "__main__":
    unittest.main()
