from __future__ import annotations

from typing import Any
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .client import McpClientError, McpTransport
from .models import McpServerConfig


class McpHttpTransport(McpTransport):
    def __init__(self, config: McpServerConfig, *, timeout_sec: float = 10.0) -> None:
        if config.transport != "http":
            raise ValueError("McpHttpTransport requires an http server config.")
        if not config.url:
            raise ValueError("http MCP config requires a url.")
        self.config = config
        self.timeout_sec = config.timeout_sec or timeout_sec
        self._request_id = 0

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params or {},
        }
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            **self.config.headers,
        }
        request = Request(
            self.config.url,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_sec) as response:
                raw = response.read()
        except HTTPError as exc:
            raw = exc.read()
            try:
                return _parse_payload(raw)
            except McpClientError as parse_exc:
                raise McpClientError(
                    f'HTTP MCP request failed with status {exc.code}: {exc.reason}'
                ) from parse_exc
        except URLError as exc:
            raise McpClientError(f"HTTP MCP request failed: {exc.reason}") from exc

        response_payload = _parse_payload(raw)
        if response_payload.get("id") != self._request_id:
            raise McpClientError(
                f'Unexpected MCP response id for method "{method}": {response_payload.get("id")}'
            )
        return response_payload

    def close(self) -> None:
        return None


def _parse_payload(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise McpClientError("Invalid JSON payload from HTTP MCP server.") from exc
    if not isinstance(payload, dict):
        raise McpClientError("Invalid HTTP MCP response payload.")
    return payload
