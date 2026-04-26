from __future__ import annotations

from dataclasses import dataclass
from socketserver import StreamRequestHandler, ThreadingTCPServer
from typing import Any, TextIO
import json
from threading import Lock
from uuid import uuid4

from .stdio import ServiceDispatcher, ServiceError


BRIDGE_PROTOCOL = "pyclaude-bridge"
BRIDGE_VERSION = "0.1"
BRIDGE_SCHEMA_VERSION = 1
BRIDGE_METHODS = (
    "bridge.hello",
    "bridge.subscribe",
    "bridge.unsubscribe",
    "ping",
    "service.hello",
    "session.create",
    "session.resume",
    "session.close",
    "session.list_open",
    "session.list_saved",
    "session.describe",
    "session.events",
    "session.ask",
    "session.command",
    "session.view",
    "session.change_view",
    "session.action",
    "session.approval_status",
    "session.approval_respond",
    "symbol.locate",
    "symbol.references",
    "symbol.actions",
)


@dataclass(slots=True)
class BridgeNotification:
    connection_id: str
    session_id: str
    seq: int
    event: dict[str, Any]
    source: str = "live"
    notification: str = "session.event"

    def to_dict(self) -> dict[str, Any]:
        return {
            "bridge": BRIDGE_PROTOCOL,
            "version": BRIDGE_VERSION,
            "schema_version": BRIDGE_SCHEMA_VERSION,
            "type": "notification",
            "notification": self.notification,
            "connection_id": self.connection_id,
            "source": self.source,
            "session_id": self.session_id,
            "seq": self.seq,
            "event_kind": self.event.get("kind"),
            "event": self.event,
        }


class BridgeConnection:
    def __init__(self, dispatcher: ServiceDispatcher, writer: TextIO) -> None:
        self.dispatcher = dispatcher
        self.writer = writer
        self.connection_id = uuid4().hex
        self._subscriptions: dict[str, Any] = {}
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        for session_id, callback in list(self._subscriptions.items()):
            try:
                record = self.dispatcher._get_record(session_id)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                continue
            record.remove_subscriber(callback)
        self._subscriptions.clear()
        self._closed = True

    def handle_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = payload.get("id")
        try:
            method = payload.get("method")
            if not isinstance(method, str) or not method:
                raise ServiceError(-32600, "Invalid bridge request: missing method.")
            params = payload.get("params") or {}
            if not isinstance(params, dict):
                raise ServiceError(-32602, "Invalid bridge params: expected object.")
            if method == "bridge.hello":
                result = {
                    "protocol": BRIDGE_PROTOCOL,
                    "version": BRIDGE_VERSION,
                    "schema_version": BRIDGE_SCHEMA_VERSION,
                    "connection_id": self.connection_id,
                    "methods": list(BRIDGE_METHODS),
                    "capabilities": {
                        "notifications": True,
                        "subscriptions": True,
                        "events_polling": True,
                        "event_replay": True,
                        "notification_kinds": [
                            "session.event",
                            "session.closed",
                            "session.approval_required",
                            "session.approval_resolved",
                        ],
                    },
                }
            elif method == "bridge.subscribe":
                result = self._subscribe(params)
            elif method == "bridge.unsubscribe":
                result = self._unsubscribe(params)
            else:
                result = self.dispatcher._dispatch(method, params)  # type: ignore[attr-defined]
            return {
                "bridge": BRIDGE_PROTOCOL,
                "version": BRIDGE_VERSION,
                "schema_version": BRIDGE_SCHEMA_VERSION,
                "type": "response",
                "id": request_id,
                "result": result,
            }
        except ServiceError as exc:
            return {
                "bridge": BRIDGE_PROTOCOL,
                "version": BRIDGE_VERSION,
                "schema_version": BRIDGE_SCHEMA_VERSION,
                "type": "response",
                "id": request_id,
                "error": {"code": exc.code, "message": exc.message, "data": exc.data},
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "bridge": BRIDGE_PROTOCOL,
                "version": BRIDGE_VERSION,
                "schema_version": BRIDGE_SCHEMA_VERSION,
                "type": "response",
                "id": request_id,
                "error": {
                    "code": -32000,
                    "message": f"{type(exc).__name__}: {exc}",
                    "data": {"type": type(exc).__name__},
                },
            }

    def _subscribe(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = params.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise ServiceError(-32602, "session_id must be a non-empty string.")
        record = self.dispatcher._get_record(session_id)  # type: ignore[attr-defined]
        after_seq = params.get("after_seq", 0)
        limit = params.get("limit", 100)
        try:
            after_seq = int(after_seq)
            limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise ServiceError(
                -32602,
                "after_seq and limit must be integers.",
                data={"type": "invalid_params", "field": "after_seq|limit"},
            ) from exc
        replay = record.get_events(after_seq=after_seq, limit=limit)
        replay_notifications = [
            self._build_notification(session_id, event_payload, source="replay").to_dict()
            for event_payload in replay["events"]
        ]
        if session_id in self._subscriptions:
            return {
                "session_id": session_id,
                "subscribed": True,
                "replay": replay_notifications,
                "replayed": len(replay_notifications),
                "next_seq": replay["next_seq"],
                "last_seq": record._event_cursor,
                "subscriber_count": record.subscriber_count,
            }

        def callback(event_payload: dict[str, Any]) -> None:
            notification = self._build_notification(session_id, event_payload, source="live")
            self.writer.write(json.dumps(notification.to_dict(), ensure_ascii=False) + "\n")
            self.writer.flush()

        record.add_subscriber(callback)
        self._subscriptions[session_id] = callback
        return {
            "session_id": session_id,
            "subscribed": True,
            "replay": replay_notifications,
            "replayed": len(replay_notifications),
            "next_seq": replay["next_seq"],
            "last_seq": record._event_cursor,
            "subscriber_count": record.subscriber_count,
        }

    def _unsubscribe(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = params.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise ServiceError(-32602, "session_id must be a non-empty string.")
        callback = self._subscriptions.pop(session_id, None)
        if callback is None:
            return {"session_id": session_id, "subscribed": False}
        record = self.dispatcher._get_record(session_id)  # type: ignore[attr-defined]
        record.remove_subscriber(callback)
        return {
            "session_id": session_id,
            "subscribed": False,
            "subscriber_count": record.subscriber_count,
        }

    def _build_notification(
        self,
        session_id: str,
        event_payload: dict[str, Any],
        *,
        source: str,
    ) -> BridgeNotification:
        return BridgeNotification(
            connection_id=self.connection_id,
            session_id=session_id,
            seq=int(event_payload["seq"]),
            event=event_payload,
            source=source,
            notification=_notification_name_for_event(event_payload),
        )


def _notification_name_for_event(event_payload: dict[str, Any]) -> str:
    if event_payload.get("kind") == "session_closed":
        return "session.closed"
    if event_payload.get("kind") == "approval_required":
        return "session.approval_required"
    if event_payload.get("kind") == "approval_resolved":
        return "session.approval_resolved"
    return "session.event"


class _BridgeRequestHandler(StreamRequestHandler):
    def handle(self) -> None:
        writer = _BridgeStreamWriter(self.wfile)
        server = self.server  # type: ignore[attr-defined]
        server.track_connection_open()
        connection = BridgeConnection(server.dispatcher, writer)
        try:
            for raw_line in self.rfile:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    response = {
                        "bridge": BRIDGE_PROTOCOL,
                        "version": BRIDGE_VERSION,
                        "schema_version": BRIDGE_SCHEMA_VERSION,
                        "type": "response",
                        "id": None,
                        "error": {
                            "code": -32700,
                            "message": f"Parse error: {exc.msg}",
                            "data": {"type": "parse_error"},
                        },
                    }
                else:
                    response = connection.handle_message(payload)
                writer.write(json.dumps(response, ensure_ascii=False) + "\n")
                writer.flush()
        finally:
            connection.close()
            server.track_connection_close()


class BridgeTcpServer(ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, host: str, port: int, dispatcher: ServiceDispatcher) -> None:
        super().__init__((host, port), _BridgeRequestHandler)
        self.dispatcher = dispatcher
        self._active_connections = 0
        self._connection_lock = Lock()

    def track_connection_open(self) -> None:
        with self._connection_lock:
            self._active_connections += 1

    def track_connection_close(self) -> None:
        with self._connection_lock:
            self._active_connections = max(0, self._active_connections - 1)

    @property
    def active_connections(self) -> int:
        with self._connection_lock:
            return self._active_connections

    def close(self) -> None:
        try:
            self.shutdown()
        except Exception:  # noqa: BLE001
            pass
        self.server_close()
        self.dispatcher.close()


class _BridgeStreamWriter:
    def __init__(self, stream) -> None:
        self.stream = stream

    def write(self, text: str) -> None:
        self.stream.write(text.encode("utf-8"))

    def flush(self) -> None:
        self.stream.flush()
