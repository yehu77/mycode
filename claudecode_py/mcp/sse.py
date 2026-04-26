from __future__ import annotations

from collections import deque
from queue import Empty, Queue
from threading import Event, Lock, Thread
from typing import Any
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .client import McpClientError, McpTransport
from .http import _parse_payload
from .models import McpServerConfig


class McpSseTransport(McpTransport):
    def __init__(self, config: McpServerConfig, *, timeout_sec: float = 10.0) -> None:
        if config.transport != "sse":
            raise ValueError("McpSseTransport requires an sse server config.")
        if not config.url:
            raise ValueError("sse MCP config requires a url.")
        self.config = config
        self.timeout_sec = config.timeout_sec or timeout_sec
        self._request_id = 0
        self._stream_response = None
        self._reader_thread: Thread | None = None
        self._closed = Event()
        self._pending: dict[int, Queue[dict[str, Any]]] = {}
        self._backlog: deque[dict[str, Any]] = deque()
        self._lock = Lock()

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._ensure_stream()
        self._request_id += 1
        request_id = self._request_id
        response_queue: Queue[dict[str, Any]] = Queue(maxsize=1)
        with self._lock:
            self._pending[request_id] = response_queue

        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            **self.config.headers,
        }
        request = Request(self.config.url, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.timeout_sec) as response:
                response.read()
        except HTTPError as exc:
            with self._lock:
                self._pending.pop(request_id, None)
            raw = exc.read()
            try:
                return _parse_payload(raw)
            except McpClientError as parse_exc:
                raise McpClientError(
                    f'SSE MCP POST failed with status {exc.code}: {exc.reason}'
                ) from parse_exc
        except URLError as exc:
            with self._lock:
                self._pending.pop(request_id, None)
            raise McpClientError(f"SSE MCP POST failed: {exc.reason}") from exc

        try:
            return response_queue.get(timeout=self.timeout_sec)
        except Empty as exc:
            with self._lock:
                self._pending.pop(request_id, None)
            raise McpClientError(
                f'MCP SSE server did not respond to "{method}" within {self.timeout_sec:.1f}s.'
            ) from exc

    def close(self) -> None:
        self._closed.set()
        response = self._stream_response
        self._stream_response = None
        if response is not None:
            try:
                response.close()
            except Exception:  # noqa: BLE001
                pass
        thread = self._reader_thread
        self._reader_thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def _ensure_stream(self) -> None:
        if self._reader_thread is not None and self._reader_thread.is_alive():
            return
        headers = {
            "Accept": "text/event-stream",
            **self.config.headers,
        }
        request = Request(self.config.url, headers=headers, method="GET")
        try:
            self._stream_response = urlopen(request, timeout=self.timeout_sec)
        except HTTPError as exc:
            raise McpClientError(f'SSE MCP stream failed with status {exc.code}: {exc.reason}') from exc
        except URLError as exc:
            raise McpClientError(f"SSE MCP stream failed: {exc.reason}") from exc

        self._closed.clear()
        self._reader_thread = Thread(target=self._read_stream, daemon=True)
        self._reader_thread.start()

    def _read_stream(self) -> None:
        response = self._stream_response
        if response is None:
            return
        data_lines: list[str] = []
        try:
            while not self._closed.is_set():
                try:
                    raw_line = response.readline()
                except TimeoutError:
                    if self._closed.is_set():
                        break
                    continue
                if raw_line == b"":
                    break
                line = raw_line.decode("utf-8").rstrip("\r\n")
                if not line:
                    self._dispatch_event_data(data_lines)
                    data_lines = []
                    continue
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
        finally:
            self._closed.set()

    def _dispatch_event_data(self, data_lines: list[str]) -> None:
        if not data_lines:
            return
        payload = _parse_payload("\n".join(data_lines).encode("utf-8"))
        payload_id = payload.get("id")
        if not isinstance(payload_id, int):
            self._backlog.append(payload)
            return
        with self._lock:
            queue = self._pending.pop(payload_id, None)
        if queue is None:
            self._backlog.append(payload)
            return
        queue.put(payload)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # noqa: BLE001
            return None
