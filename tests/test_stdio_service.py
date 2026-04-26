from io import StringIO
from pathlib import Path
import json
import shutil
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.config import SessionConfig
from claudecode_py.permissions import ApprovalRequest
from claudecode_py.runtime.events import RuntimeEvent
from claudecode_py.state import PlanningArtifact, SessionState
from claudecode_py.service import JsonRpcStdioService, ServiceDispatcher
from claudecode_py.storage.transcript import save_transcript


class StdioServiceTests(unittest.TestCase):
    def test_dispatcher_reports_service_protocol_metadata(self) -> None:
        dispatcher = ServiceDispatcher(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        try:
            response = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "service.hello",
                    "params": {},
                }
            )
            self.assertEqual(response["result"]["protocol"], "pyclaude-stdio-service")
            self.assertEqual(response["result"]["version"], "0.1")
            self.assertEqual(response["result"]["schema_version"], 1)
            self.assertIn("session.ask", response["result"]["methods"])
            self.assertTrue(response["result"]["capabilities"]["events_polling"])
            self.assertEqual(response["meta"]["schema_version"], 1)
        finally:
            dispatcher.close()

    def test_dispatcher_can_create_ask_and_close_session(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_session"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "demo.py").write_text("def deploy():\n    return 1\n", encoding="utf-8")

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            dispatcher._sessions[session_id].session.ask = (  # type: ignore[method-assign]
                lambda prompt, sink=None: (sink and sink(RuntimeEvent(kind="assistant_text", message="hello"))) or "hello"
            )

            asked = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.ask",
                    "params": {"session_id": session_id, "prompt": "hello"},
                }
            )
            self.assertEqual(asked["result"]["kind"], "run_result")
            self.assertEqual(asked["result"]["session_id"], session_id)
            described = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 22,
                    "method": "session.describe",
                    "params": {"session_id": session_id},
                }
            )
            self.assertEqual(described["result"]["subscriber_count"], 0)

            closed = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "session.close",
                    "params": {"session_id": session_id},
                }
            )
            self.assertTrue(closed["result"]["closed"])
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_dispatcher_supports_remote_command_and_action_methods(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_command"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]

            help_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.command",
                    "params": {"session_id": session_id, "prompt": "/help"},
                }
            )
            self.assertTrue(help_result["result"]["handled"])
            self.assertEqual(help_result["result"]["output_kind"], "text")
            self.assertIn("/review", help_result["result"]["output"])

            action_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "session.action",
                    "params": {
                        "session_id": session_id,
                        "action": "reload_project_context",
                    },
                }
            )
            self.assertIn("Reloaded project context.", action_result["result"]["text"])

            view_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "session.view",
                    "params": {"session_id": session_id, "view": "config"},
                }
            )
            self.assertIn("session_id:", view_result["result"]["text"])

            session = dispatcher._sessions[session_id].session
            task = session.task_manager.create(
                "ultraplan_scout",
                "Scout architecture",
                planner_kind="ultraplan",
                scout_category="architecture-boundaries",
            )
            session.task_manager.complete(task.id, "Inspect session.py.")
            session.record_planning_artifact(
                PlanningArtifact(
                    kind="ultraplan",
                    goal="map runtime",
                    summary="summary",
                    derived_from_drift=True,
                    derivation_reason="Need a safer runtime-only revision.",
                    used_read_only_subagents=True,
                    task_ids=[task.id],
                    advisor_status="approve",
                    advisor_reason="Solid plan.",
                )
            )
            session.state.advisor_model = "advisor-model"
            session.state.advisor_mode = "interactive-review"
            active_plan_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "session.view",
                    "params": {"session_id": session_id, "view": "active_plan"},
                }
            )
            self.assertIn("artifact_id:", active_plan_result["result"]["text"])
            self.assertIn("scout_outputs:", active_plan_result["result"]["text"])
            self.assertIn("derived_from_drift: yes", active_plan_result["result"]["text"])
            advisor_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "session.view",
                    "params": {"session_id": session_id, "view": "advisor_status"},
                }
            )
            self.assertIn("Advisor: advisor-model", advisor_result["result"]["text"])
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_dispatcher_serializes_and_executes_command_metadata(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_run_command"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            session = dispatcher._sessions[session_id].session

            command_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.command",
                    "params": {"session_id": session_id, "prompt": "/ultraplan map the runtime"},
                }
            )
            execution = command_result["result"]["execution"]
            self.assertTrue(execution["require_read_only_subagents"])
            self.assertEqual(execution["metadata"]["command_kind"], "ultraplan")

            session.run_command = lambda execution, sink=None: (  # type: ignore[method-assign]
                sink and sink(RuntimeEvent(kind="advisor_review_started", message="checkpoint=ultraplan"))
            ) or f"ran:{execution.metadata['command_kind']}"

            run_result = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "session.run_command",
                    "params": {"session_id": session_id, "execution": execution},
                }
            )
            self.assertEqual(run_result["result"]["payload"]["output"], "ran:ultraplan")
            self.assertTrue(
                any(
                    event["kind"] == "advisor_review_started"
                    for event in dispatcher._sessions[session_id].get_events()["events"]
                )
            )
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_dispatcher_can_report_and_resolve_pending_approval(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_approval"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            record = dispatcher._sessions[session_id]
            result_holder: dict[str, object] = {}

            def require() -> None:
                result_holder["result"] = record.request_approval(
                    ApprovalRequest(
                        tool_name="bash",
                        reason="Need to write files",
                        risk_level="write",
                        approval_key="write",
                        details="preview",
                    )
                )

            thread = threading.Thread(target=require)
            thread.start()
            try:
                status = dispatcher.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "session.approval_status",
                        "params": {"session_id": session_id},
                    }
                )
                self.assertTrue(status["result"]["pending"])
                self.assertEqual(status["result"]["approval"]["tool_name"], "bash")

                response = dispatcher.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "session.approval_respond",
                        "params": {
                            "session_id": session_id,
                            "approval_id": status["result"]["approval_id"],
                            "decision": "allow",
                            "scope": "session",
                        },
                    }
                )
                self.assertTrue(response["result"]["resolved"])
            finally:
                thread.join(timeout=2)
            self.assertEqual(getattr(result_holder["result"], "decision", None), "allow")
            self.assertEqual(record.pending_approval_status()["pending"], False)
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_dispatcher_returns_symbol_actions(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_symbol"
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
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            response = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "symbol.actions",
                    "params": {"session_id": session_id, "symbol": "build"},
                }
            )
            self.assertEqual(response["result"]["symbol"], "build")
            self.assertEqual(response["result"]["definitions"][0]["action"], "open_symbol")
            self.assertEqual(response["result"]["references"][0]["action"], "open_reference")
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_dispatcher_returns_js_ts_symbol_actions(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_symbol_ts"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "ui.ts").write_text(
            "export const build = () => 1\n"
            "const value = build()\n",
            encoding="utf-8",
        )

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]
            response = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "symbol.actions",
                    "params": {"session_id": session_id, "symbol": "build"},
                }
            )
            self.assertEqual(response["result"]["definitions"][0]["path"], "ui.ts")
            self.assertEqual(response["result"]["references"][0]["path"], "ui.ts")
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_dispatcher_session_create_loads_mcp_tools_from_config(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_mcp"
        if cwd.exists():
            shutil.rmtree(cwd)
        (cwd / ".pyclaude").mkdir(parents=True)
        server_script = Path(__file__).resolve().parent / "fixtures" / "fake_mcp_server.py"
        config_path = cwd / ".pyclaude" / "mcp_servers.json"
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

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {"mcp_config_path": str(config_path)},
                }
            )
            session_id = created["result"]["session_id"]
            self.assertIn(
                "fake.echo_text",
                dispatcher._sessions[session_id].session.describe_mcp_tools(),
            )
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_dispatcher_lists_and_resumes_saved_sessions(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_saved"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        save_transcript(
            SessionConfig(cwd=cwd, interactive=False),
            SessionState(
                session_id="saved-session",
                messages=[{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
            ),
        )

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            listed = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.list_saved",
                    "params": {},
                }
            )
            self.assertEqual(listed["result"]["sessions"][0]["session_id"], "saved-session")

            resumed = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.resume",
                    "params": {"resume_session_id": "saved-session"},
                }
            )
            self.assertEqual(resumed["result"]["session_id"], "saved-session")
            self.assertIsNotNone(resumed["result"]["restored_from"])
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_dispatcher_tracks_open_sessions_and_event_cursor(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_stdio_service_events"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        dispatcher = ServiceDispatcher(SessionConfig(cwd=cwd, interactive=False))
        try:
            created = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.create",
                    "params": {},
                }
            )
            session_id = created["result"]["session_id"]

            def fake_ask(prompt, sink=None):
                if sink is not None:
                    sink(RuntimeEvent(kind="assistant_text", message="chunk-1"))
                    sink(RuntimeEvent(kind="tool_started", message='{"path":"demo.py"}', tool_name="read_file"))
                return "done"

            dispatcher._sessions[session_id].session.ask = fake_ask  # type: ignore[method-assign]

            dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.ask",
                    "params": {"session_id": session_id, "prompt": "hello"},
                }
            )
            events = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "session.events",
                    "params": {"session_id": session_id, "after_seq": 0},
                }
            )
            self.assertEqual(events["result"]["last_seq"], 2)
            self.assertEqual(len(events["result"]["events"]), 2)
            self.assertEqual(events["result"]["events"][0]["kind"], "assistant_text")

            listed = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "session.list_open",
                    "params": {},
                }
            )
            self.assertEqual(listed["result"]["sessions"][0]["session_id"], session_id)
            self.assertEqual(listed["result"]["sessions"][0]["event_cursor"], 2)
            self.assertEqual(listed["result"]["sessions"][0]["subscriber_count"], 0)
        finally:
            dispatcher.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_stdio_service_handles_parse_error(self) -> None:
        dispatcher = ServiceDispatcher(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        stdin = StringIO('not-json\n')
        stdout = StringIO()
        service = JsonRpcStdioService(dispatcher, stdin=stdin, stdout=stdout)

        service.serve_forever()

        payload = json.loads(stdout.getvalue().strip())
        self.assertEqual(payload["error"]["code"], -32700)
        self.assertEqual(payload["error"]["data"]["type"], "parse_error")
        self.assertEqual(payload["meta"]["schema_version"], 1)

    def test_dispatcher_returns_method_not_found(self) -> None:
        dispatcher = ServiceDispatcher(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        try:
            response = dispatcher.handle(
                {"jsonrpc": "2.0", "id": 1, "method": "nope", "params": {}}
            )
            self.assertEqual(response["error"]["code"], -32601)
            self.assertEqual(response["meta"]["schema_version"], 1)
        finally:
            dispatcher.close()

    def test_dispatcher_returns_structured_error_data_for_unknown_session(self) -> None:
        dispatcher = ServiceDispatcher(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        try:
            response = dispatcher.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.describe",
                    "params": {"session_id": "missing"},
                }
            )
            self.assertEqual(response["error"]["code"], -32004)
            self.assertEqual(response["error"]["data"]["type"], "session_not_found")
            self.assertEqual(response["error"]["data"]["session_id"], "missing")
            self.assertEqual(response["meta"]["schema_version"], 1)
        finally:
            dispatcher.close()
