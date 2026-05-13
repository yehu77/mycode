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
from claudecode_py.permissions import ApprovalRequest
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
            self.assertEqual(response["result"]["surface_kind"], "symbol_actions")
            self.assertEqual(response["result"]["definition_count"], 1)
            self.assertEqual(response["result"]["reference_count"], 1)
            self.assertEqual(response["result"]["selected_definition"]["action"], "open_symbol")
            self.assertEqual(response["result"]["selected_reference"]["action"], "open_reference")
            self.assertEqual(response["result"]["navigation_target"]["action"], "open_symbol")
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

            dispatcher._sessions[session_id].append_event(
                RuntimeEvent(kind="task_progress", message="repair planned | target=missing-agent", task_id="task-123")
            )
            lines = [line for line in writer.getvalue().splitlines() if line.strip()]
            task_payload = json.loads(lines[-1])
            self.assertEqual(task_payload["notification"], "session.event")
            self.assertEqual(task_payload["event_kind"], "task_progress")
            self.assertEqual(task_payload["event"]["kind"], "task_progress")
            self.assertEqual(task_payload["event"]["task_id"], "task-123")

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

    def test_bridge_connection_pushes_enriched_approval_notifications(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_bridge_service_approval"
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
            record = dispatcher._sessions[session_id]

            def require() -> None:
                record.request_approval(
                    ApprovalRequest(
                        tool_name="bash",
                        reason="Need approval",
                        risk_level="shell_dangerous",
                        approval_key="shell_dangerous",
                        command="Remove-Item demo.txt",
                        target_paths=("demo.txt",),
                        permission_rules=("ask:path:demo",),
                        decision_reason="Matched ask rules: ask:path:demo",
                    )
                )

            thread = threading.Thread(target=require, daemon=True)
            thread.start()
            time.sleep(0.1)
            lines = [line for line in writer.getvalue().splitlines() if line.strip()]
            payload = json.loads(lines[-1])
            self.assertEqual(payload["notification"], "session.approval_required")
            self.assertEqual(payload["event"]["approval"]["command"], "Remove-Item demo.txt")
            self.assertEqual(payload["event"]["approval"]["target_paths"], ["demo.txt"])
            self.assertEqual(payload["event"]["approval"]["permission_rules"], ["ask:path:demo"])
            self.assertEqual(
                payload["event"]["approval"]["decision_reason"],
                "Matched ask rules: ask:path:demo",
            )
            self.assertEqual(payload["event"]["approval"]["display_lines"][0], "risk: shell_dangerous")
            self.assertEqual(payload["event"]["approval"]["display_lines"][1], "tool: bash")
            self.assertIn("policy: Matched ask rules: ask:path:demo", payload["event"]["approval"]["display_lines"])
            self.assertTrue(payload["event"]["approval"]["display_compact"].startswith("risk=shell_dangerous | tool=bash"))
            connection.handle_message(
                {
                    "id": 3,
                    "method": "session.approval_respond",
                    "params": {
                        "session_id": session_id,
                        "approval_id": payload["event"]["approval_id"],
                        "decision": "allow",
                        "scope": "once",
                    },
                }
            )
            thread.join(timeout=2)
        finally:
            connection.close()
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_bridge_connection_pushes_workspace_cleanup_approval_with_formatter_output(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_bridge_service_workspace_cleanup_approval"
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
            record = dispatcher._sessions[session_id]

            def require() -> None:
                record.request_approval(
                    ApprovalRequest(
                        tool_name="workspace_cleanup",
                        reason="Delete orphaned isolated workspaces from the local .pyclaude directory.",
                        risk_level="delete",
                        approval_key="workspace_cleanup_delete:orphan-agent",
                        details=(
                            "Delete orphaned isolated workspaces.\n"
                            "selector: orphan-agent\n"
                            "planned_deletions: 1\n"
                            "planned_targets:\n"
                        "- workspace=snapshot health=orphaned label=orphan-agent origin=C:/tmp cwd=C:/tmp/orphan-agent cleanup=none session_refs=0 background_refs=0"
                        ),
                        target_paths=(".pyclaude/workspaces/orphan-agent",),
                    )
                )

            thread = threading.Thread(target=require, daemon=True)
            thread.start()
            time.sleep(0.1)
            lines = [line for line in writer.getvalue().splitlines() if line.strip()]
            payload = json.loads(lines[-1])
            approval = payload["event"]["approval"]
            self.assertEqual(payload["notification"], "session.approval_required")
            self.assertEqual(approval["display_lines"][0], "risk: delete")
            self.assertEqual(approval["display_lines"][1], "tool: workspace_cleanup")
            self.assertIn("paths:", approval["display_lines"])
            self.assertIn("- .pyclaude/workspaces/orphan-agent", approval["display_lines"])
            self.assertIn("details:", approval["display_lines"])
            self.assertIn("- selector: orphan-agent", approval["display_lines"])
            self.assertTrue(approval["display_compact"].startswith("risk=delete | tool=workspace_cleanup"))
            connection.handle_message(
                {
                    "id": 3,
                    "method": "session.approval_respond",
                    "params": {
                        "session_id": session_id,
                        "approval_id": payload["event"]["approval_id"],
                        "decision": "allow",
                        "scope": "once",
                    },
                }
            )
            thread.join(timeout=2)
        finally:
            connection.close()
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_bridge_session_describe_includes_workspace_action_metadata(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_bridge_service_workspace_describe"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        writer = StringIO()
        connection = BridgeConnection(dispatcher, writer)
        try:
            created = connection.handle_message({"id": 1, "method": "session.create", "params": {}})
            session_id = created["result"]["session_id"]
            session = dispatcher._sessions[session_id].session
            session.state.workspace_mode = "snapshot"
            session.state.workspace_label = "missing-agent"
            session.state.workspace_cleanup_status = "pending"
            session.state.workspace_unavailable = True
            session.state.workspace_unavailable_reason = (
                "Isolated workspace is unavailable: expected missing snapshot."
            )
            session.state.workspace_fallback_cwd = str(cwd.resolve())
            session.state.workspace_health = "unavailable"
            session.state.original_cwd = str(cwd.resolve())
            session.state.effective_cwd = str((cwd / ".pyclaude" / "workspaces" / "missing-agent").resolve())

            described = connection.handle_message(
                {
                    "id": 2,
                    "method": "session.describe",
                    "params": {"session_id": session_id},
                }
            )

            self.assertEqual(described["result"]["workspace_primary_action"], f"workspace_repair {session_id}")
            self.assertEqual(described["result"]["workspace_secondary_action"], "workspace_cleanup_preview")
            self.assertEqual(described["result"]["workspace_tertiary_action"], "/workspaces list")
            self.assertEqual(described["result"]["workspace_action_target"], session_id)
        finally:
            connection.close()
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_bridge_session_view_task_detail_includes_workspace_detail_metadata(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_bridge_service_workspace_task_detail"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        writer = StringIO()
        connection = BridgeConnection(dispatcher, writer)
        try:
            created = connection.handle_message({"id": 1, "method": "session.create", "params": {}})
            session_id = created["result"]["session_id"]
            session = dispatcher._sessions[session_id].session
            task = session.task_manager.create(
                "workspace",
                "Clean orphaned isolated workspaces",
                workspace_action="cleanup",
                workspace_target="orphan-agent",
                workspace_health_before="orphaned",
                workspace_health_after="healthy",
                workspace_planned_paths=["C:/tmp/orphan-agent"],
                workspace_applied_paths=["C:/tmp/orphan-agent"],
            )

            detail = connection.handle_message(
                {
                    "id": 2,
                    "method": "session.view",
                    "params": {
                        "session_id": session_id,
                        "view": "task_detail",
                        "task_id": task.id,
                    },
                }
            )

            self.assertEqual(detail["result"]["workspace_action"], "cleanup")
            self.assertEqual(detail["result"]["workspace_target"], "orphan-agent")
            self.assertEqual(detail["result"]["workspace_health_before"], "orphaned")
            self.assertEqual(detail["result"]["workspace_health_after"], "healthy")
            self.assertEqual(detail["result"]["workspace_planned_paths"], ["C:/tmp/orphan-agent"])
            self.assertEqual(detail["result"]["workspace_applied_paths"], ["C:/tmp/orphan-agent"])
            self.assertEqual(detail["result"]["workspace_primary_action"], "workspace_cleanup_preview")
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

    def test_bridge_connection_describe_includes_workspace_unavailable_metadata(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_bridge_workspace_describe"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        writer = StringIO()
        connection = BridgeConnection(dispatcher, writer)
        try:
            created = connection.handle_message({"id": 1, "method": "session.create", "params": {}})
            session_id = created["result"]["session_id"]
            session = dispatcher._sessions[session_id].session
            session.state.original_cwd = str(cwd.resolve())
            session.state.effective_cwd = str((cwd / ".pyclaude" / "worktrees" / "missing-agent").resolve())
            session.state.workspace_mode = "worktree"
            session.state.workspace_label = "missing-agent"
            session.state.workspace_cleanup_status = "failed"
            session.state.workspace_unavailable = True
            session.state.workspace_unavailable_reason = "Isolated workspace is unavailable: expected missing worktree."
            session.state.workspace_fallback_cwd = str(cwd.resolve())

            described = connection.handle_message(
                {"id": 2, "method": "session.describe", "params": {"session_id": session_id}}
            )
            self.assertEqual(described["result"]["workspace_mode"], "worktree")
            self.assertTrue(described["result"]["workspace_unavailable"])
            self.assertEqual(described["result"]["workspace_fallback_cwd"], str(cwd.resolve()))
        finally:
            connection.close()
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)
