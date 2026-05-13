from pathlib import Path
import json
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
    load_project_local_plugin_registry,
    merge_plugin_registries,
)
from claudecode_py.session import Session
from claudecode_py.session_factory import SessionFactory
from claudecode_py.storage.transcript import load_latest_transcript


class PluginRegistryTests(unittest.TestCase):
    def _write_external_plugin(self, cwd: Path, plugin_dir_name: str, payload: dict) -> Path:
        plugin_dir = cwd / ".pyclaude" / "plugins" / plugin_dir_name
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "plugin.json").write_text(json.dumps(payload), encoding="utf-8")
        return plugin_dir

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

    def test_load_project_local_external_plugin_manifest_with_skill(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_external_plugin_skill"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        plugin_dir = self._write_external_plugin(
            cwd,
            "docs",
            {
                "name": "docs",
                "description": "Docs helper plugin.",
                "skills": [
                    {
                        "name": "docs-style",
                        "description": "Stable docs terminology.",
                        "content": "Prefer stable user-facing terminology.",
                        "auto_enable": True,
                        "tags": ["docs", "style"],
                    }
                ],
            },
        )

        try:
            registry = load_project_local_plugin_registry(cwd)

            plugin = registry.get_plugin("docs")
            assert plugin is not None
            self.assertEqual(plugin.source, "external")
            self.assertEqual(plugin.path, plugin_dir.resolve())
            self.assertEqual(plugin.skills[0].name, "docs-style")
            self.assertEqual(plugin.skills[0].path, (plugin_dir / "plugin.json").resolve())
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_load_project_local_external_plugin_manifest_with_mcp_server(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_external_plugin_mcp_manifest"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        plugin_dir = self._write_external_plugin(
            cwd,
            "docs",
            {
                "name": "docs",
                "description": "Docs helper plugin.",
                "mcp_servers": [
                    {
                        "name": "plugin-fake",
                        "transport": "stdio",
                        "command": sys.executable,
                        "args": ["server.py"],
                    }
                ],
            },
        )

        try:
            registry = load_project_local_plugin_registry(cwd)

            plugin = registry.get_plugin("docs")
            assert plugin is not None
            self.assertEqual(plugin.source, "external")
            self.assertEqual(plugin.mcp_servers[0].name, "plugin-fake")
            self.assertEqual(plugin.mcp_servers[0].cwd, str(plugin_dir.resolve()))
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_merge_builtin_and_external_plugin_registries_preserves_sources(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_external_plugin_merge"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        self._write_external_plugin(
            cwd,
            "docs",
            {
                "name": "docs",
                "description": "Docs helper plugin.",
            },
        )

        try:
            merged = merge_plugin_registries(
                build_builtin_plugin_registry(),
                load_project_local_plugin_registry(cwd),
            )

            self.assertEqual(merged.get_plugin("review").source, "builtin")
            self.assertEqual(merged.get_plugin("docs").source, "external")
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_malformed_external_plugin_surfaces_diagnostic_in_views(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_external_plugin_bad"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        plugin_dir = cwd / ".pyclaude" / "plugins" / "broken"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text('{"name": "broken", "skills": "bad"}', encoding="utf-8")

        session = Session(SessionConfig(cwd=cwd, interactive=False))
        try:
            rendered = session.describe_plugins()
            self.assertIn("broken: status=invalid source=external", rendered)
            self.assertIn("path=", rendered)
            detail = session.describe_plugin("broken")
            self.assertIn("status: invalid", detail)
            self.assertIn("source: external", detail)
            self.assertIn('error: ValueError: Plugin manifest field "skills" must be a list.', detail)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_external_plugin_skills_follow_enable_disable_state_and_persist(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_external_plugin_session"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        self._write_external_plugin(
            cwd,
            "docs",
            {
                "name": "docs",
                "description": "Docs helper plugin.",
                "skills": [
                    {
                        "name": "docs-style",
                        "description": "Stable docs terminology.",
                        "content": "Prefer stable user-facing terminology.",
                    }
                ],
            },
        )

        session = Session(SessionConfig(cwd=cwd, interactive=False))
        try:
            self.assertIn("docs-style", session.describe_loaded_skills())

            message = session.disable_plugin("docs")
            self.assertEqual(message, 'Disabled plugin "docs".')
            self.assertNotIn("docs-style", session.describe_loaded_skills())

            message = session.enable_plugin("docs")
            self.assertEqual(message, 'Enabled plugin "docs".')
            self.assertIn("docs-style", session.describe_loaded_skills())

            restored_state, _ = load_latest_transcript(cwd)
            assert restored_state is not None
            self.assertIn("docs", restored_state.enabled_plugin_names)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_external_plugin_mcp_servers_load_into_sessions(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_external_plugin_mcp"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        server_script = Path(__file__).resolve().parent / "fixtures" / "fake_mcp_server.py"
        self._write_external_plugin(
            cwd,
            "docs",
            {
                "name": "docs",
                "description": "Docs helper plugin.",
                "mcp_servers": [
                    {
                        "name": "plugin-fake",
                        "transport": "stdio",
                        "command": sys.executable,
                        "args": [str(server_script)],
                    }
                ],
            },
        )
        factory = SessionFactory(load_mcp_from_config=True)
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

    def test_reload_project_context_discovers_new_external_plugin(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_external_plugin_reload"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))

        try:
            self.assertNotIn("docs-style", session.describe_loaded_skills())
            self._write_external_plugin(
                cwd,
                "docs",
                {
                    "name": "docs",
                    "description": "Docs helper plugin.",
                    "skills": [
                        {
                            "name": "docs-style",
                            "description": "Stable docs terminology.",
                            "content": "Prefer stable user-facing terminology.",
                        }
                    ],
                },
            )

            message = session.reload_project_context()

            self.assertIn("Reloaded project context.", message)
            self.assertIn("docs-style", session.describe_loaded_skills())
            self.assertIn("docs: status=enabled source=external", session.describe_plugins())
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_reload_project_context_removes_deleted_external_plugin_and_reconciles_state(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_external_plugin_remove"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        plugin_dir = self._write_external_plugin(
            cwd,
            "docs",
            {
                "name": "docs",
                "description": "Docs helper plugin.",
                "skills": [
                    {
                        "name": "docs-style",
                        "description": "Stable docs terminology.",
                        "content": "Prefer stable user-facing terminology.",
                    }
                ],
            },
        )
        session = Session(SessionConfig(cwd=cwd, interactive=False))

        try:
            self.assertIn("docs-style", session.describe_loaded_skills())
            self.assertIn("docs", session.describe_plugins())
            self.assertEqual(session.disable_plugin("docs"), 'Disabled plugin "docs".')
            shutil.rmtree(plugin_dir)

            message = session.reload_project_context()

            self.assertIn("Reloaded project context.", message)
            self.assertNotIn("docs-style", session.describe_loaded_skills())
            self.assertNotIn("docs: status=", session.describe_plugins())
            self.assertEqual(session.state.enabled_plugin_names, [])
            self.assertEqual(session.state.disabled_plugin_names, [])
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_external_plugin_name_conflict_surfaces_diagnostic_and_keeps_builtin(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_external_plugin_conflict"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        self._write_external_plugin(
            cwd,
            "review",
            {
                "name": "review",
                "description": "Conflicting external review plugin.",
            },
        )
        session = Session(SessionConfig(cwd=cwd, interactive=False))

        try:
            rendered = session.describe_plugins()
            self.assertIn("review: status=enabled source=builtin", rendered)
            self.assertIn("review: status=invalid source=external", rendered)
            detail = session.describe_plugin("review")
            self.assertIn("source: builtin", detail)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_missing_manifest_directory_surfaces_invalid_plugin_diagnostic(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_external_plugin_missing_manifest"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        plugin_dir = cwd / ".pyclaude" / "plugins" / "broken"
        plugin_dir.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))

        try:
            rendered = session.describe_plugins()
            self.assertIn("broken: status=invalid source=external", rendered)
            detail = session.describe_plugin("broken")
            self.assertIn("status: invalid", detail)
            self.assertIn('error: Missing required manifest "plugin.json".', detail)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)


if __name__ == "__main__":
    unittest.main()
