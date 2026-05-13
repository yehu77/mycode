from base64 import b64encode
from pathlib import Path
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.config import SessionConfig
from claudecode_py.interactions import UserQuestionResponse
from claudecode_py.mcp import McpClient, McpRegistry, McpServerConfig
from claudecode_py.permissions import PermissionManager
from claudecode_py.session import Session
from claudecode_py.storage.transcript import load_transcript_by_session_id
from claudecode_py.tasks import TaskManager
from claudecode_py.tools.base import ToolContext


class _ResourceTransport:
    def request(self, method: str, params: dict | None = None) -> dict:
        if method == "initialize":
            return {
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"version": "1.0.0"},
                    "capabilities": {"tools": {}, "resources": {}},
                }
            }
        if method == "tools/list":
            return {"result": {"tools": []}}
        if method == "resources/list":
            return {
                "result": {
                    "resources": [
                        {
                            "uri": "docs://guide",
                            "name": "Guide",
                            "mimeType": "text/plain",
                            "description": "Text guide",
                        },
                        {
                            "uri": "blob://image",
                            "name": "Image",
                            "mimeType": "image/png",
                            "description": "Binary image",
                        },
                    ]
                }
            }
        if method == "resources/read":
            uri = (params or {}).get("uri")
            if uri == "docs://guide":
                return {
                    "result": {
                        "contents": [
                            {
                                "uri": uri,
                                "mimeType": "text/plain",
                                "text": "hello from resource",
                            }
                        ]
                    }
                }
            if uri == "blob://image":
                return {
                    "result": {
                        "contents": [
                            {
                                "uri": uri,
                                "mimeType": "image/png",
                                "blob": b64encode(b"png-bytes").decode("ascii"),
                            }
                        ]
                    }
                }
        raise AssertionError(f"unexpected method: {method}")

    def close(self) -> None:
        return None


class DeferredToolTests(unittest.TestCase):
    def test_deferred_tools_are_hidden_until_activated(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))

        names = [str(spec["name"]) for spec in session.tool_specs()]

        self.assertIn("tool_search", names)
        self.assertNotIn("ask_user_question", names)
        self.assertNotIn("list_mcp_resources", names)
        matches = session.search_deferred_tools("mcp resource", max_results=10)["matches"]
        self.assertIn("list_mcp_resources", matches)
        self.assertIn("read_mcp_resource", matches)

        selected = session.search_deferred_tools("select:ask_user_question")

        self.assertEqual(selected["activated"], "ask_user_question")
        self.assertIn("ask_user_question", [str(spec["name"]) for spec in session.tool_specs()])

    def test_deferred_tool_activation_persists_in_transcript(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_deferred_tools"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))

            self.assertTrue(session.activate_deferred_tool("ask_user_question"))
            loaded_state, loaded_path = load_transcript_by_session_id(cwd, session.state.session_id)

            self.assertIsNotNone(loaded_path)
            assert loaded_state is not None
            self.assertIn("ask_user_question", loaded_state.activated_deferred_tool_names)

            restored = Session(SessionConfig(cwd=cwd, interactive=False), state=loaded_state)

            self.assertIn("ask_user_question", [str(spec["name"]) for spec in restored.tool_specs()])
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_mcp_resource_tools_list_and_read_resources(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_mcp_resources"
        if cwd.exists():
            shutil.rmtree(cwd)
        registry = McpRegistry()
        registry.register_client(
            McpClient(
                config=McpServerConfig(name="docs", transport="stdio", command="demo"),
                transport=_ResourceTransport(),
            )
        )
        try:
            session = Session(
                SessionConfig(cwd=cwd, interactive=False),
                mcp_registry=registry,
            )
            ctx = ToolContext(
                cwd=session.config.cwd,
                permission_manager=PermissionManager(mode="bypass", interactive=False),
                task_manager=TaskManager(),
                session=session,
            )
            list_tool = next(tool for tool in session.tools if tool.name == "list_mcp_resources")
            read_tool = next(tool for tool in session.tools if tool.name == "read_mcp_resource")

            resources = list_tool.execute({"server": "docs"}, ctx)
            text_result = read_tool.execute({"server": "docs", "uri": "docs://guide"}, ctx)
            blob_result = read_tool.execute({"server": "docs", "uri": "blob://image"}, ctx)

            self.assertEqual([item["uri"] for item in resources], ["docs://guide", "blob://image"])
            self.assertEqual(text_result["contents"][0]["text"], "hello from resource")
            saved_path = Path(blob_result["contents"][0]["blob_saved_to"])
            self.assertTrue(saved_path.exists())
            self.assertEqual(saved_path.read_bytes(), b"png-bytes")
        finally:
            registry.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_read_mcp_resource_rejects_unknown_cached_uri(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_mcp_unknown_resource"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        registry = McpRegistry()
        registry.register_client(
            McpClient(
                config=McpServerConfig(name="docs", transport="stdio", command="demo"),
                transport=_ResourceTransport(),
            )
        )
        try:
            session = Session(
                SessionConfig(cwd=cwd, interactive=False),
                mcp_registry=registry,
            )
            ctx = ToolContext(
                cwd=session.config.cwd,
                permission_manager=PermissionManager(mode="bypass", interactive=False),
                task_manager=TaskManager(),
                session=session,
            )
            read_tool = next(tool for tool in session.tools if tool.name == "read_mcp_resource")

            with self.assertRaisesRegex(RuntimeError, "not currently discovered"):
                read_tool.execute({"server": "docs", "uri": "docs://missing"}, ctx)
        finally:
            registry.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_ask_user_question_tool_uses_question_text_keys(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        session.set_question_handler(
            lambda request: UserQuestionResponse(
                answers={request.questions[0].question: "tcp"},
                canceled=False,
            )
        )
        ctx = ToolContext(
            cwd=session.config.cwd,
            permission_manager=PermissionManager(mode="bypass", interactive=False),
            task_manager=TaskManager(),
            session=session,
        )
        tool = next(tool for tool in session.tools if tool.name == "ask_user_question")

        result = tool.execute(
            {
                "questions": [
                    {
                        "header": "Backend",
                        "question": "Which backend should be used?",
                        "options": [
                            {"label": "stdio", "description": "Use stdio"},
                            {"label": "tcp", "description": "Use bridge"},
                        ],
                    }
                ]
            },
            ctx,
        )

        self.assertIn("Which backend should be used?: tcp", result)


if __name__ == "__main__":
    unittest.main()
