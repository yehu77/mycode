from io import StringIO
from pathlib import Path
import json
import shutil
import socket
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.config import SessionConfig
from claudecode_py.runtime.events import RuntimeEvent
from claudecode_py.service import BridgeConnection, BridgeTcpServer, ServiceDispatcher


class BridgeServiceTests(unittest.TestCase):
    def test_bridge_connection_handles_hello_and_symbol_action(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_bridge_service_basic"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.py").write_text(
            "def build():\n"
            "    return 1\n\n"
            "value = build()\n",
            encoding="utf-8",
        )
        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        writer = StringIO()
        connection = BridgeConnection(dispatcher, writer)
        try:
            hello = connection.handle_message({"id": 1, "method": "bridge.hello", "params": {}})
            self.assertEqual(hello["result"]["protocol"], "pyclaude-bridge")
            self.assertEqual(hello["result"]["version"], "0.1")
            self.assertEqual(hello["result"]["schema_version"], 1)
            self.assertTrue(hello["result"]["connection_id"])
            self.assertIn("service.hello", hello["result"]["methods"])
            self.assertTrue(hello["result"]["capabilities"]["notifications"])

            created = connection.handle_message({"id": 2, "method": "session.create", "params": {}})
            session_id = created["result"]["session_id"]
            response = connection.handle_message(
                {
                    "id": 3,
                    "method": "symbol.actions",
                    "params": {"session_id": session_id, "symbol": "build"},
                }
            )
            self.assertEqual(response["result"]["symbol"], "build")
        finally:
            connection.close()
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_bridge_connection_pushes_notifications(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_bridge_service_events"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        writer = StringIO()
        connection = BridgeConnection(dispatcher, writer)
        try:
            created = connection.handle_message({"id": 1, "method": "session.create", "params": {}})
            session_id = created["result"]["session_id"]
            subscribed = connection.handle_message(
                {"id": 2, "method": "bridge.subscribe", "params": {"session_id": session_id}}
            )
            self.assertTrue(subscribed["result"]["subscribed"])
            self.assertEqual(subscribed["result"]["subscriber_count"], 1)

            dispatcher._sessions[session_id].append_event(RuntimeEvent(kind="assistant_text", message="hello"))
            lines = [line for line in writer.getvalue().splitlines() if line.strip()]
            payload = json.loads(lines[-1])
            self.assertEqual(payload["type"], "notification")
            self.assertEqual(payload["notification"], "session.event")
            self.assertEqual(payload["session_id"], session_id)
            self.assertEqual(payload["connection_id"], connection.connection_id)
            self.assertEqual(payload["source"], "live")
            self.assertEqual(payload["event_kind"], "assistant_text")
            self.assertEqual(payload["event"]["kind"], "assistant_text")

            closed = connection.handle_message(
                {"id": 99, "method": "session.close", "params": {"session_id": session_id}}
            )
            self.assertTrue(closed["result"]["closed"])
            lines = [line for line in writer.getvalue().splitlines() if line.strip()]
            closed_payload = json.loads(lines[-1])
            self.assertEqual(closed_payload["type"], "notification")
            self.assertEqual(closed_payload["notification"], "session.closed")
            self.assertEqual(closed_payload["event_kind"], "session_closed")
            self.assertEqual(closed_payload["event"]["message"], "Session closed.")

        finally:
            connection.close()
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_bridge_subscribe_replays_previous_events(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_bridge_service_replay"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        writer = StringIO()
        connection = BridgeConnection(dispatcher, writer)
        try:
            created = connection.handle_message({"id": 1, "method": "session.create", "params": {}})
            session_id = created["result"]["session_id"]
            record = dispatcher._sessions[session_id]
            record.append_event(RuntimeEvent(kind="assistant_text", message="one"))
            record.append_event(RuntimeEvent(kind="assistant_text", message="two"))

            subscribed = connection.handle_message(
                {
                    "id": 2,
                    "method": "bridge.subscribe",
                    "params": {"session_id": session_id, "after_seq": 0, "limit": 10},
                }
            )
            self.assertTrue(subscribed["result"]["subscribed"])
            self.assertEqual(subscribed["result"]["replayed"], 2)
            self.assertEqual(len(subscribed["result"]["replay"]), 2)
            self.assertEqual(subscribed["result"]["replay"][0]["type"], "notification")
            self.assertEqual(subscribed["result"]["replay"][0]["notification"], "session.event")
            self.assertEqual(subscribed["result"]["replay"][0]["source"], "replay")
            self.assertEqual(subscribed["result"]["replay"][0]["event"]["message"], "one")
            self.assertEqual(subscribed["result"]["replay"][1]["event"]["message"], "two")
            self.assertEqual(subscribed["result"]["next_seq"], 2)
            self.assertEqual(subscribed["result"]["last_seq"], 2)
        finally:
            connection.close()
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_bridge_subscribe_validates_replay_params(self) -> None:
        dispatcher = ServiceDispatcher(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        writer = StringIO()
        connection = BridgeConnection(dispatcher, writer)
        try:
            created = connection.handle_message({"id": 1, "method": "session.create", "params": {}})
            session_id = created["result"]["session_id"]
            response = connection.handle_message(
                {
                    "id": 2,
                    "method": "bridge.subscribe",
                    "params": {"session_id": session_id, "after_seq": "bad"},
                }
            )
            self.assertEqual(response["error"]["code"], -32602)
            self.assertEqual(response["error"]["data"]["type"], "invalid_params")
        finally:
            connection.close()
            dispatcher.close()

    def test_bridge_tcp_server_serves_json_lines(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_bridge_tcp_server"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        server = BridgeTcpServer("127.0.0.1", 0, dispatcher)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            self.assertEqual(server.active_connections, 0)
            with socket.create_connection((host, port), timeout=2) as sock:
                sock.sendall(b'{"id":1,"method":"bridge.hello","params":{}}\n')
                data = sock.recv(4096).decode("utf-8")
                time.sleep(0.05)
                self.assertEqual(server.active_connections, 1)
            payload = json.loads(data.strip())
            self.assertEqual(payload["result"]["protocol"], "pyclaude-bridge")
            self.assertEqual(payload["schema_version"], 1)
            time.sleep(0.05)
            self.assertEqual(server.active_connections, 0)
        finally:
            server.close()
            thread.join(timeout=2)
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_bridge_connection_returns_structured_error_data(self) -> None:
        dispatcher = ServiceDispatcher(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        writer = StringIO()
        connection = BridgeConnection(dispatcher, writer)
        try:
            response = connection.handle_message(
                {"id": 1, "method": "session.describe", "params": {"session_id": "missing"}}
            )
            self.assertEqual(response["error"]["code"], -32004)
            self.assertEqual(response["error"]["data"]["type"], "session_not_found")
            self.assertEqual(response["schema_version"], 1)
        finally:
            connection.close()
            dispatcher.close()
