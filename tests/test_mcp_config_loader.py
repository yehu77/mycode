from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Queue
import json
import os
import shutil
import sys
from threading import Event, Thread
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.mcp import default_mcp_config_path, load_mcp_registry


class _HttpLoaderHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        msg_id = payload["id"]
        method = payload["method"]

        if self.headers.get("Authorization") != "Bearer test-token":
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
                        "serverInfo": {"version": "0.0.3"},
                        "capabilities": {"tools": {}, "resources": {}},
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
                                "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
                            }
                        ]
                    },
                },
            )
            return
        if method == "resources/list":
            self._write_json(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "resources": [
                            {
                                "uri": "docs://guide",
                                "name": "Guide",
                                "mimeType": "text/plain",
                            }
                        ]
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


class _SseLoaderHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    events = Queue()
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
            except Exception:  # noqa: BLE001
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
                    "capabilities": {"tools": {}, "resources": {}},
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
                            "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
                        }
                    ]
                },
            }
        elif method == "resources/list":
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "resources": [
                        {
                            "uri": "docs://guide",
                            "name": "Guide",
                            "mimeType": "text/plain",
                        }
                    ]
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


class _FakeWebSocketTransport:
    def __init__(self, config) -> None:
        self.config = config
        self._request_id = 0

    def request(self, method: str, params: dict | None = None) -> dict:
        self._request_id += 1
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"version": "0.0.6"},
                    "capabilities": {"tools": {}, "resources": {}},
                },
            }
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "result": {
                    "tools": [
                        {
                            "name": "echo_text",
                            "description": "Return the provided text.",
                            "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
                        }
                    ]
                },
            }
        if method == "resources/list":
            return {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "result": {
                    "resources": [
                        {
                            "uri": "docs://guide",
                            "name": "Guide",
                            "mimeType": "text/plain",
                        }
                    ]
                },
            }
        raise AssertionError(f"Unexpected method: {method}")

    def close(self) -> None:
        return None


class McpConfigLoaderTests(unittest.TestCase):
    def test_loads_registry_from_workspace_config(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_mcp_loader"
        if cwd.exists():
            shutil.rmtree(cwd)
        (cwd / ".pyclaude").mkdir(parents=True)
        server_script = Path(__file__).resolve().parent / "fixtures" / "fake_mcp_server.py"
        config_path = default_mcp_config_path(cwd)
        config_path.write_text(
            json.dumps(
                {
                    "servers": [
                        {
                            "name": "fake",
                            "transport": "stdio",
                            "command": sys.executable,
                            "args": [str(server_script)],
                        }
                    ]
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )

        try:
            registry = load_mcp_registry(cwd)
            assert registry is not None
            self.assertEqual(registry.list_servers(), ["fake"])
            refs = registry.list_tool_references()
            self.assertEqual(refs[0].qualified_name, "fake.echo_text")
        finally:
            if "registry" in locals() and registry is not None:
                for server_name in registry.list_servers():
                    registry.get_server(server_name).client.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_loads_http_registry_from_workspace_config(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_mcp_loader_http"
        if cwd.exists():
            shutil.rmtree(cwd)
        (cwd / ".pyclaude").mkdir(parents=True)
        server = ThreadingHTTPServer(("127.0.0.1", 0), _HttpLoaderHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        config_path = default_mcp_config_path(cwd)
        config_path.write_text(
            json.dumps(
                {
                    "servers": [
                        {
                            "name": "http-fake",
                            "transport": "http",
                            "url": f"http://127.0.0.1:{server.server_address[1]}/mcp",
                            "headers": {"Authorization": "Bearer test-token"},
                            "timeout_sec": 3,
                        }
                    ]
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )

        try:
            registry = load_mcp_registry(cwd)
            assert registry is not None
            self.assertEqual(registry.list_servers(), ["http-fake"])
            refs = registry.list_tool_references()
            self.assertEqual(refs[0].qualified_name, "http-fake.echo_text")
            self.assertEqual(registry.get_server("http-fake").client.config.timeout_sec, 3.0)
        finally:
            if "registry" in locals() and registry is not None:
                registry.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=1.0)
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_expands_env_vars_in_http_config(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_mcp_loader_http_env"
        if cwd.exists():
            shutil.rmtree(cwd)
        (cwd / ".pyclaude").mkdir(parents=True)
        server = ThreadingHTTPServer(("127.0.0.1", 0), _HttpLoaderHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        config_path = default_mcp_config_path(cwd)
        with patch.dict(
            os.environ,
            {
                "MCP_HTTP_URL": f"http://127.0.0.1:{server.server_address[1]}/mcp",
                "MCP_HTTP_TOKEN": "test-token",
            },
            clear=False,
        ):
            config_path.write_text(
                json.dumps(
                    {
                        "servers": [
                            {
                                "name": "http-env",
                                "transport": "http",
                                "url": "${MCP_HTTP_URL}",
                                "headers": {
                                    "Authorization": "Bearer ${MCP_HTTP_TOKEN}",
                                    "X-Mode": "${MCP_MODE:-fallback}",
                                },
                            }
                        ]
                    },
                    ensure_ascii=True,
                    indent=2,
                ),
                encoding="utf-8",
            )

            try:
                registry = load_mcp_registry(cwd)
                assert registry is not None
                server_config = registry.get_server("http-env").client.config
                self.assertEqual(server_config.url, os.environ["MCP_HTTP_URL"])
                self.assertEqual(server_config.headers["Authorization"], "Bearer test-token")
                self.assertEqual(server_config.headers["X-Mode"], "fallback")
            finally:
                if "registry" in locals() and registry is not None:
                    registry.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=1.0)
                if cwd.exists():
                    shutil.rmtree(cwd)

    def test_loads_sse_registry_from_workspace_config(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_mcp_loader_sse"
        if cwd.exists():
            shutil.rmtree(cwd)
        (cwd / ".pyclaude").mkdir(parents=True)
        _SseLoaderHandler.stop_event.clear()
        while True:
            try:
                _SseLoaderHandler.events.get_nowait()
            except Exception:  # noqa: BLE001
                break
        server = ThreadingHTTPServer(("127.0.0.1", 0), _SseLoaderHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        config_path = default_mcp_config_path(cwd)
        config_path.write_text(
            json.dumps(
                {
                    "servers": [
                        {
                            "name": "sse-fake",
                            "transport": "sse",
                            "url": f"http://127.0.0.1:{server.server_address[1]}/mcp",
                            "timeout_sec": 3,
                        }
                    ]
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )

        try:
            registry = load_mcp_registry(cwd)
            assert registry is not None
            self.assertEqual(registry.list_servers(), ["sse-fake"])
            refs = registry.list_tool_references()
            self.assertEqual(refs[0].qualified_name, "sse-fake.echo_text")
        finally:
            if "registry" in locals() and registry is not None:
                registry.close()
            _SseLoaderHandler.stop_event.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=1.0)
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_builds_bearer_auth_headers_from_auth_config(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_mcp_loader_http_auth"
        if cwd.exists():
            shutil.rmtree(cwd)
        (cwd / ".pyclaude").mkdir(parents=True)
        server = ThreadingHTTPServer(("127.0.0.1", 0), _HttpLoaderHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        config_path = default_mcp_config_path(cwd)
        with patch.dict(
            os.environ,
            {
                "MCP_HTTP_URL": f"http://127.0.0.1:{server.server_address[1]}/mcp",
                "MCP_HTTP_TOKEN": "test-token",
            },
            clear=False,
        ):
            config_path.write_text(
                json.dumps(
                    {
                        "servers": [
                            {
                                "name": "http-auth",
                                "transport": "http",
                                "url": "${MCP_HTTP_URL}",
                                "auth": {
                                    "type": "bearer",
                                    "token": "${MCP_HTTP_TOKEN}",
                                },
                            }
                        ]
                    },
                    ensure_ascii=True,
                    indent=2,
                ),
                encoding="utf-8",
            )

            try:
                registry = load_mcp_registry(cwd)
                assert registry is not None
                server_config = registry.get_server("http-auth").client.config
                self.assertEqual(server_config.headers["Authorization"], "Bearer test-token")
                self.assertEqual(server_config.auth_mode, "bearer")
            finally:
                if "registry" in locals() and registry is not None:
                    registry.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=1.0)
                if cwd.exists():
                    shutil.rmtree(cwd)

    def test_auth_header_conflict_raises_clear_error(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_mcp_loader_auth_conflict"
        if cwd.exists():
            shutil.rmtree(cwd)
        (cwd / ".pyclaude").mkdir(parents=True)
        config_path = default_mcp_config_path(cwd)
        config_path.write_text(
            json.dumps(
                {
                    "servers": [
                        {
                            "name": "conflict-auth",
                            "transport": "http",
                            "url": "http://example.test/mcp",
                            "auth": {"type": "bearer", "token": "demo"},
                            "headers": {"Authorization": "Bearer something-else"},
                        }
                    ]
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )

        try:
            with self.assertRaises(ValueError) as exc:
                load_mcp_registry(cwd)
            self.assertIn("conflicts with explicit headers", str(exc.exception))
            self.assertIn("Authorization", str(exc.exception))
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_missing_env_var_in_config_raises_clear_error(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_mcp_loader_missing_env"
        if cwd.exists():
            shutil.rmtree(cwd)
        (cwd / ".pyclaude").mkdir(parents=True)
        config_path = default_mcp_config_path(cwd)
        config_path.write_text(
            json.dumps(
                {
                    "servers": [
                        {
                            "name": "broken-env",
                            "transport": "http",
                            "url": "${MISSING_MCP_URL}",
                        }
                    ]
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )

        try:
            with patch.dict(os.environ, {}, clear=False):
                with self.assertRaises(ValueError) as exc:
                    load_mcp_registry(cwd)
            self.assertIn('MCP server "broken-env" field "url"', str(exc.exception))
            self.assertIn("MISSING_MCP_URL", str(exc.exception))
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_loads_websocket_registry_from_workspace_config(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_mcp_loader_websocket"
        if cwd.exists():
            shutil.rmtree(cwd)
        (cwd / ".pyclaude").mkdir(parents=True)
        config_path = default_mcp_config_path(cwd)
        config_path.write_text(
            json.dumps(
                {
                    "servers": [
                        {
                            "name": "ws-fake",
                            "transport": "websocket",
                            "url": "ws://example.test/mcp",
                            "headers": {"Authorization": "Bearer demo-token"},
                            "timeout_sec": 4,
                        }
                    ]
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )

        try:
            with patch("claudecode_py.mcp.config_loader.McpWebSocketTransport", _FakeWebSocketTransport):
                registry = load_mcp_registry(cwd)
            assert registry is not None
            self.assertEqual(registry.list_servers(), ["ws-fake"])
            refs = registry.list_tool_references()
            self.assertEqual(refs[0].qualified_name, "ws-fake.echo_text")
            self.assertEqual(registry.get_server("ws-fake").client.config.timeout_sec, 4.0)
        finally:
            if "registry" in locals() and registry is not None:
                registry.close()
            if cwd.exists():
                shutil.rmtree(cwd)


if __name__ == "__main__":
    unittest.main()
