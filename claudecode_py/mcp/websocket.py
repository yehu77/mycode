from __future__ import annotations

from collections import deque
from time import monotonic
from typing import Any, Callable, Protocol
import json

from .client import McpClientError, McpTransport
from .models import McpServerConfig


class _WebSocketConnection(Protocol):
    def send(self, data: str) -> None:
        raise NotImplementedError

    def recv(self, timeout: float | None = None):
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class McpWebSocketTransport(McpTransport):
    def __init__(
        self,
        config: McpServerConfig,
        *,
        timeout_sec: float = 10.0,
        connector: Callable[[str, dict[str, str], float], _WebSocketConnection] | None = None,
    ) -> None:
        if config.transport != "websocket":
            raise ValueError("McpWebSocketTransport requires a websocket server config.")
        if not config.url:
            raise ValueError("websocket MCP config requires a url.")
        self.config = config
        self.timeout_sec = config.timeout_sec or timeout_sec
        self._request_id = 0
        self._connection: _WebSocketConnection | None = None
        self._backlog: deque[dict[str, Any]] = deque()
        self._connector = connector or _connect_websocket

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        connection = self._ensure_connection()
        self._request_id += 1
        request_id = self._request_id
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        try:
            connection.send(json.dumps(payload, ensure_ascii=True))
        except Exception as exc:  # noqa: BLE001
            self.close()
            raise McpClientError(f'WebSocket MCP send failed for "{method}": {exc}') from exc
        return self._await_response(method, request_id)

    def close(self) -> None:
        connection = self._connection
        self._connection = None
        self._backlog.clear()
        if connection is None:
            return
        try:
            connection.close()
        except Exception:  # noqa: BLE001
            return None

    def _ensure_connection(self) -> _WebSocketConnection:
        if self._connection is not None:
            return self._connection
        try:
            self._connection = self._connector(
                self.config.url or "",
                dict(self.config.headers),
                self.timeout_sec,
            )
        except McpClientError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise McpClientError(f"WebSocket MCP connection failed: {exc}") from exc
        return self._connection

    def _await_response(self, method: str, request_id: int) -> dict[str, Any]:
        cached = self._pop_backlog_response(request_id)
        if cached is not None:
            return cached

        deadline = monotonic() + self.timeout_sec
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise McpClientError(
                    f'WebSocket MCP server did not respond to "{method}" within {self.timeout_sec:.1f}s.'
                )
            payload = self._receive_payload(method, timeout=remaining)
            payload_id = payload.get("id")
            if payload_id == request_id:
                return payload
            self._backlog.append(payload)

    def _pop_backlog_response(self, request_id: int) -> dict[str, Any] | None:
        if not self._backlog:
            return None
        for index, payload in enumerate(self._backlog):
            if payload.get("id") == request_id:
                matched = payload
                del self._backlog[index]
                return matched
        return None

    def _receive_payload(self, method: str, *, timeout: float) -> dict[str, Any]:
        connection = self._connection
        if connection is None:
            raise McpClientError("WebSocket MCP connection is not established.")
        try:
            raw = connection.recv(timeout=timeout)
        except TypeError:
            raw = connection.recv()
        except TimeoutError as exc:
            raise McpClientError(
                f'WebSocket MCP server did not respond to "{method}" within {self.timeout_sec:.1f}s.'
            ) from exc
        except Exception as exc:  # noqa: BLE001
            self.close()
            raise McpClientError(f'WebSocket MCP receive failed for "{method}": {exc}') from exc
        return _parse_payload(raw)


def _connect_websocket(url: str, headers: dict[str, str], timeout_sec: float) -> _WebSocketConnection:
    try:
        from websockets.sync.client import connect
    except ImportError as exc:
        raise McpClientError(
            'Missing dependency "websockets". Install with: pip install -e .[mcp-remote]'
        ) from exc
    return connect(
        url,
        additional_headers=headers or None,
        open_timeout=timeout_sec,
        close_timeout=timeout_sec,
    )


def _parse_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, bytes):
        raw_text = raw.decode("utf-8")
    elif isinstance(raw, str):
        raw_text = raw
    else:
        raise McpClientError("Invalid WebSocket MCP payload type.")
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise McpClientError("Invalid JSON payload from WebSocket MCP server.") from exc
    if not isinstance(payload, dict):
        raise McpClientError("Invalid WebSocket MCP response payload.")
    return payload
