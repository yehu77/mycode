from __future__ import annotations

from subprocess import PIPE, Popen, TimeoutExpired
from typing import Any
import json

from .client import McpClientError, McpTransport
from .models import McpServerConfig


class McpStdioTransport(McpTransport):
    def __init__(self, config: McpServerConfig, *, timeout_sec: float = 10.0) -> None:
        if config.transport != "stdio":
            raise ValueError("McpStdioTransport requires a stdio server config.")
        if not config.command:
            raise ValueError("stdio MCP config requires a command.")
        self.config = config
        self.timeout_sec = timeout_sec
        self._process: Popen[bytes] | None = None
        self._request_id = 0

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        process = self._ensure_process()
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params or {},
        }
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        message = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
        assert process.stdin is not None
        process.stdin.write(message)
        process.stdin.flush()
        response = self._read_message(process)
        if response.get("id") != self._request_id:
            raise McpClientError(
                f'Unexpected MCP response id for method "{method}": {response.get("id")}'
            )
        return response

    def close(self) -> None:
        process = self._process
        if process is None:
            return
        self._process = None
        if process.poll() is None:
            if process.stdin is not None:
                process.stdin.close()
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()

    def _ensure_process(self) -> Popen[bytes]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        argv = [self.config.command, *self.config.args]
        self._process = Popen(
            argv,
            stdin=PIPE,
            stdout=PIPE,
            stderr=PIPE,
            cwd=self.config.cwd,
            env={**self.config.env} if self.config.env else None,
        )
        return self._process

    def _read_message(self, process: Popen[bytes]) -> dict[str, Any]:
        assert process.stdout is not None
        headers: dict[str, str] = {}
        while True:
            line = process.stdout.readline()
            if line == b"":
                stderr_text = ""
                if process.stderr is not None:
                    try:
                        stderr_text = process.stderr.read().decode("utf-8", errors="replace")
                    except Exception:  # noqa: BLE001
                        stderr_text = ""
                raise McpClientError(
                    "MCP stdio server closed unexpectedly."
                    + (f" stderr:\n{stderr_text.strip()}" if stderr_text.strip() else "")
                )
            stripped = line.decode("utf-8").strip()
            if not stripped:
                break
            if ":" not in stripped:
                raise McpClientError(f"Invalid MCP header line: {stripped}")
            key, value = stripped.split(":", 1)
            headers[key.strip().lower()] = value.strip()

        content_length = headers.get("content-length")
        if content_length is None:
            raise McpClientError("Missing Content-Length header from MCP response.")
        try:
            expected = int(content_length)
        except ValueError as exc:
            raise McpClientError(f"Invalid Content-Length value: {content_length}") from exc
        body = process.stdout.read(expected)
        if len(body) < expected:
            raise McpClientError("Incomplete MCP response body.")
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise McpClientError("Invalid JSON payload from MCP server.") from exc

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # noqa: BLE001
            return None
