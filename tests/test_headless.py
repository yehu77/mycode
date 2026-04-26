from pathlib import Path
import json
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.config import SessionConfig
from claudecode_py.runtime.events import RuntimeEvent
from claudecode_py.runtime.headless import (
    HeadlessRunner,
    create_headless_session,
    locate_symbol_headless,
    run_headless,
)
from claudecode_py.session import Session
from claudecode_py.state import SessionState
from claudecode_py.storage.transcript import save_transcript


class HeadlessTests(unittest.TestCase):
    def test_headless_runner_returns_structured_result(self) -> None:
        cwd = Path(__file__).resolve().parent
        session = Session(SessionConfig(cwd=cwd, interactive=False))

        class FakeSession:
            pass

        fake_session = FakeSession()
        fake_session.ask = lambda prompt, sink=None: sink and sink(RuntimeEvent(kind="assistant_text", message="done")) or "done"
        fake_session.state = session.state
        fake_session.config = session.config
        fake_session.persist_transcript = False
        fake_session.close = lambda: None

        runner = HeadlessRunner(fake_session)  # type: ignore[arg-type]
        result = runner.run("hello")

        self.assertEqual(result.output, "done")
        self.assertEqual(result.session_id, fake_session.state.session_id)
        self.assertEqual(result.cwd, str(fake_session.config.cwd))
        self.assertEqual(len(result.events), 1)
        self.assertIsNone(result.transcript_path)
        self.assertEqual(result.to_dict()["kind"], "run_result")

    def test_create_headless_session_can_restore_latest(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_headless_restore"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        config = SessionConfig(cwd=cwd, interactive=False)
        state = SessionState(messages=[{"role": "user", "content": [{"type": "text", "text": "saved"}]}])
        save_transcript(config, state)

        try:
            session, restored_from = create_headless_session(config, restore_latest=True)
            self.assertIsNotNone(restored_from)
            self.assertEqual(len(session.state.messages), 1)
            self.assertEqual(session.state.messages[0]["content"][0]["text"], "saved")
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_run_headless_includes_restored_from_path(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_headless_run"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        config = SessionConfig(cwd=cwd, interactive=False)
        state = SessionState(messages=[{"role": "user", "content": [{"type": "text", "text": "saved"}]}])
        transcript_path = save_transcript(config, state)

        try:
            class FakeSession(Session):
                def ask(self, prompt: str, sink=None) -> str:
                    if sink is not None:
                        sink(RuntimeEvent(kind="assistant_text", message="done"))
                    return "done"

            original_factory = create_headless_session

            def fake_factory(config_arg, *, restore_latest=False, resume_session_id=None):
                restored = transcript_path if restore_latest else None
                return FakeSession(config_arg, state=state), restored

            import claudecode_py.runtime.headless as headless_module

            saved_factory = headless_module.create_headless_session
            headless_module.create_headless_session = fake_factory  # type: ignore[assignment]
            try:
                result = run_headless("hello", config=config, restore_latest=True)
            finally:
                headless_module.create_headless_session = saved_factory  # type: ignore[assignment]

            self.assertEqual(result.output, "done")
            self.assertEqual(result.restored_from, transcript_path)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_create_headless_session_can_resume_specific_session_id(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_headless_resume_id"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        config = SessionConfig(cwd=cwd, interactive=False)
        save_transcript(
            config,
            SessionState(
                session_id="resume-id",
                messages=[{"role": "user", "content": [{"type": "text", "text": "saved"}]}],
            ),
        )

        try:
            session, restored_from = create_headless_session(
                config,
                resume_session_id="resume-id",
            )
            self.assertIsNotNone(restored_from)
            self.assertEqual(session.state.session_id, "resume-id")
            self.assertEqual(session.state.messages[0]["content"][0]["text"], "saved")
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_headless_symbol_lookup_returns_js_ts_records(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_headless_symbol_ts"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        (cwd / "ui.ts").write_text("export const deploy = () => 1\n", encoding="utf-8")

        try:
            result = locate_symbol_headless(
                "deploy",
                config=SessionConfig(cwd=cwd, interactive=False),
            )
            self.assertEqual(result.lookup.matches[0].path, "ui.ts")
            self.assertEqual(result.lookup.matches[0].line, 1)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_create_headless_session_loads_mcp_tools_from_config(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_headless_mcp"
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
        session = None

        try:
            session, _ = create_headless_session(
                SessionConfig(cwd=cwd, interactive=False, mcp_config_path=config_path)
            )
            self.assertIn("fake.echo_text", session.describe_mcp_tools())
        finally:
            if session is not None:
                session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_headless_child_session_loads_mcp_tools_from_config(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_headless_child_mcp"
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
        session = None
        child = None

        try:
            session, _ = create_headless_session(
                SessionConfig(cwd=cwd, interactive=False, mcp_config_path=config_path)
            )
            child = session.create_child_session(interactive=False)
            self.assertIn("fake.echo_text", child.describe_mcp_tools())
        finally:
            if child is not None:
                child.close()
            if session is not None:
                session.close()
            if cwd.exists():
                shutil.rmtree(cwd)


if __name__ == "__main__":
    unittest.main()
