from pathlib import Path
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.config import SessionConfig
from claudecode_py.plugins import (
    PluginDefinition,
    PluginMcpServerDefinition,
    PluginRegistry,
    PluginSkillDefinition,
    build_builtin_plugin_registry,
)
from claudecode_py.session import Session
from claudecode_py.session_factory import SessionFactory
from claudecode_py.storage.transcript import load_latest_transcript


class PluginRegistryTests(unittest.TestCase):
    def test_builtin_plugin_registry_exposes_default_plugins(self) -> None:
        registry = build_builtin_plugin_registry()

        names = [plugin.name for plugin in registry.list_plugins()]

        self.assertEqual(
            names,
            ["advisor", "commit", "init", "insights", "install", "review", "security-review", "ultraplan"],
        )

    def test_session_can_enable_and_disable_builtin_plugin_commands(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))

        try:
            handled, output = session.handle_repl_command("/review 9")
            self.assertTrue(handled)
            self.assertIsNotNone(output)

            message = session.disable_plugin("review")
            self.assertEqual(message, 'Disabled plugin "review".')

            handled_after_disable, output_after_disable = session.handle_repl_command("/review 9")
            self.assertFalse(handled_after_disable)
            self.assertIsNone(output_after_disable)

            message = session.enable_plugin("review")
            self.assertEqual(message, 'Enabled plugin "review".')

            handled_after_enable, output_after_enable = session.handle_repl_command("/review 9")
            self.assertTrue(handled_after_enable)
            self.assertIsNotNone(output_after_enable)
        finally:
            session.close()

    def test_plugin_state_persists_to_transcript(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_plugin_persist"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))

        try:
            session.disable_plugin("review")
            restored_state, _ = load_latest_transcript(cwd)

            assert restored_state is not None
            self.assertEqual(restored_state.disabled_plugin_names, ["review"])
            self.assertEqual(restored_state.enabled_plugin_names, [])
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_plugin_skills_follow_plugin_enable_disable_state(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_plugin_skills"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        factory = SessionFactory(
            plugin_registry=PluginRegistry(
                [
                    PluginDefinition(
                        name="docs",
                        description="Docs helper plugin.",
                        skills=(
                            PluginSkillDefinition(
                                name="docs-style",
                                description="Write docs with stable terminology.",
                                content="Prefer stable user-facing terminology.",
                            ),
                        ),
                    )
                ]
            )
        )
        session = factory.create_session(SessionConfig(cwd=cwd, interactive=False))

        try:
            self.assertIn("docs-style", session.describe_loaded_skills())

            message = session.disable_plugin("docs")
            self.assertEqual(message, 'Disabled plugin "docs".')
            self.assertNotIn("docs-style", session.describe_loaded_skills())

            message = session.enable_plugin("docs")
            self.assertEqual(message, 'Enabled plugin "docs".')
            self.assertIn("docs-style", session.describe_loaded_skills())
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_plugin_mcp_servers_load_into_config_backed_sessions(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_plugin_mcp"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        server_script = Path(__file__).resolve().parent / "fixtures" / "fake_mcp_server.py"
        factory = SessionFactory(
            load_mcp_from_config=True,
            plugin_registry=PluginRegistry(
                [
                    PluginDefinition(
                        name="docs",
                        description="Docs helper plugin.",
                        mcp_servers=(
                            PluginMcpServerDefinition(
                                name="plugin-fake",
                                transport="stdio",
                                command=sys.executable,
                                args=(str(server_script),),
                            ),
                        ),
                    )
                ]
            ),
        )
        session = factory.create_session(SessionConfig(cwd=cwd, interactive=False))

        try:
            self.assertIn("plugin-fake.echo_text", session.describe_mcp_tools())

            message = session.disable_plugin("docs")
            self.assertEqual(message, 'Disabled plugin "docs".')
            self.assertEqual(session.describe_mcp_tools(), "No MCP servers configured.")
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)


if __name__ == "__main__":
    unittest.main()
